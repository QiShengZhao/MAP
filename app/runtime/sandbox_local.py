"""本地 mock 沙箱（开发/CI，SANDBOX_BACKEND=local）。非 gVisor 安全边界，禁止生产。"""
import asyncio
import os
import resource
import shutil
import signal
import sys
import time
import uuid
from pathlib import Path

from app.config import settings
from app.runtime.sandbox_base import ExecResult, SandboxBackend, SandboxHandle

BASE_DIR = Path(settings.SANDBOX_LOCAL_BASE_DIR or "/tmp/agent-sandbox")
SAFE_ENV = {"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8",
            "HOME": "/tmp", "PYTHONUNBUFFERED": "1"}
MAX_OUTPUT = 256 * 1024


def _limits():
    resource.setrlimit(resource.RLIMIT_CPU, (settings.SANDBOX_CPU_SECONDS,) * 2)
    mem = settings.SANDBOX_MEM_MB * 1024 * 1024
    if sys.platform == "linux":
        resource.setrlimit(resource.RLIMIT_AS, (mem, mem))
    resource.setrlimit(resource.RLIMIT_FSIZE,
                       (settings.SANDBOX_MAX_FILE_MB * 1024 * 1024,) * 2)
    resource.setrlimit(resource.RLIMIT_NPROC, (128, 128))
    os.setsid()


class LocalSandboxAdapter:
    """适配现有 Sandbox 接口（exec 返回 str）。"""
    def __init__(self, handle: SandboxHandle, backend: "LocalSandboxBackend"):
        self._handle = handle
        self._backend = backend
        self.started_at = time.monotonic()

    async def exec(self, command, timeout=120, container="main"):
        r = await self._backend.exec(self._handle, command, timeout=timeout)
        out = r.stdout
        if r.stderr:
            out = (out + "\n" + r.stderr).strip() if out else r.stderr
        if r.exit_code == 124:
            return r.stderr or f"(command timed out after {timeout}s)"
        return out[-16000:] if out else "(no output)"

    async def write_file(self, path, content):
        data = content.encode() if isinstance(content, str) else content
        await self._backend.put_file(self._handle, path, data)

    async def read_file(self, path):
        data = await self._backend.get_file(self._handle, path)
        return data.decode(errors="replace")

    async def read_file_bytes(self, path):
        return await self._backend.get_file(self._handle, path)

    def elapsed_seconds(self):
        return int(time.monotonic() - self.started_at)


class LocalSandboxBackend(SandboxBackend):
    def __init__(self):
        self._handles: dict[str, SandboxHandle] = {}

    def _workdir(self, tenant_id: str, session_id: str) -> Path:
        d = BASE_DIR / tenant_id / session_id
        d.mkdir(parents=True, exist_ok=True)
        (d / "artifacts").mkdir(exist_ok=True)
        return d

    def _resolve(self, handle: SandboxHandle, path: str) -> Path:
        root = Path(handle.workdir).resolve()
        p = (root / path.lstrip("/")).resolve()
        if not str(p).startswith(str(root)):
            raise PermissionError(f"path escape blocked: {path}")
        return p

    async def ensure(self, tenant_id: str, session_id: str) -> SandboxHandle:
        key = f"{tenant_id}:{session_id}"
        if key in self._handles:
            return self._handles[key]
        wd = self._workdir(tenant_id, session_id)
        h = SandboxHandle(sandbox_id=f"local-{uuid.uuid4().hex[:12]}",
                          tenant_id=tenant_id, session_id=session_id,
                          backend="local", workdir=str(wd))
        self._handles[key] = h
        return h

    async def exec(self, handle: SandboxHandle, command: str,
                   timeout: int = 60, workdir: str | None = None) -> ExecResult:
        cwd = self._resolve(handle, workdir) if workdir else Path(handle.workdir)
        argv = ["bash", "-c", command]
        if settings.SANDBOX_LOCAL_NO_NET and sys.platform == "linux" and shutil.which("unshare"):
            argv = ["unshare", "--net", "--map-root-user"] + argv

        start = time.monotonic()
        proc = await asyncio.create_subprocess_exec(
            *argv, cwd=str(cwd), env=dict(SAFE_ENV),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            preexec_fn=_limits,
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except ProcessLookupError:
                pass
            await proc.wait()
            return ExecResult(exit_code=124, stdout="", duration_ms=timeout * 1000,
                              stderr=f"timeout after {timeout}s (killed)")

        dur = int((time.monotonic() - start) * 1000)
        truncated = len(out) > MAX_OUTPUT or len(err) > MAX_OUTPUT
        return ExecResult(
            exit_code=proc.returncode or 0,
            stdout=out[:MAX_OUTPUT].decode(errors="replace"),
            stderr=err[:MAX_OUTPUT].decode(errors="replace"),
            duration_ms=dur, truncated=truncated,
        )

    async def put_file(self, handle: SandboxHandle, path: str, content: bytes) -> None:
        p = self._resolve(handle, path)
        p.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(p.write_bytes, content)

    async def get_file(self, handle: SandboxHandle, path: str, max_bytes: int = 10 << 20) -> bytes:
        p = self._resolve(handle, path)
        if not p.exists():
            raise FileNotFoundError(path)
        if p.stat().st_size > max_bytes:
            raise ValueError(f"file too large: {p.stat().st_size} > {max_bytes}")
        return await asyncio.to_thread(p.read_bytes)

    async def list_artifacts(self, handle: SandboxHandle) -> list[dict]:
        art = Path(handle.workdir) / "artifacts"
        return [{"path": str(f.relative_to(handle.workdir)), "size": f.stat().st_size,
                 "mtime": f.stat().st_mtime}
                for f in art.rglob("*") if f.is_file()]

    async def terminate(self, handle: SandboxHandle) -> None:
        self._handles.pop(f"{handle.tenant_id}:{handle.session_id}", None)
        wd = Path(handle.workdir)
        if wd.exists() and str(wd).startswith(str(BASE_DIR.resolve())):
            await asyncio.to_thread(shutil.rmtree, wd, ignore_errors=True)
