"""Docker 沙箱后端（SANDBOX_BACKEND=docker）。

隔离强度：local < docker < k8s+gVisor
- 每 session 一个长驻容器（复用，TTL 由 sandbox_reaper 统一回收）
- 安全基线：非 root + 只读 rootfs + cap_drop ALL + no-new-privileges
  + pids/cpu/mem cgroup 限额 + 默认断网（network_mode=none）
- 文件交换走 docker exec + tar 流，不挂载宿主目录（避免逃逸面）
- 标签 tenant_id/session_id/expires_at 供 reaper 扫描回收
"""
import asyncio
import hashlib
import io
import logging
import tarfile
import time
from pathlib import PurePosixPath

import aiodocker
from aiodocker.exceptions import DockerError

from app.config import settings
from app.runtime.sandbox_base import ExecResult, SandboxBackend, SandboxHandle

log = logging.getLogger("sandbox.docker")

WORKDIR = "/workspace"
MAX_OUTPUT = 256 * 1024
LABEL_PREFIX = "agent-platform"


class DockerSandboxAdapter:
    """适配 SandboxManager 现有 exec/write_file 接口。"""

    def __init__(self, handle: SandboxHandle, backend: "DockerSandboxBackend"):
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


class DockerSandboxBackend(SandboxBackend):
    def __init__(self):
        self._docker: aiodocker.Docker | None = None
        self._lock = asyncio.Lock()

    async def _client(self) -> aiodocker.Docker:
        if self._docker is None:
            async with self._lock:
                if self._docker is None:
                    self._docker = aiodocker.Docker(url=settings.DOCKER_HOST or None)
        return self._docker

    @staticmethod
    def _container_name(tenant_id: str, session_id: str) -> str:
        h = hashlib.sha256(f"{tenant_id}:{session_id}".encode()).hexdigest()[:12]
        return f"sbx-{tenant_id[:12]}-{h}"

    async def ensure(self, tenant_id: str, session_id: str) -> SandboxHandle:
        docker = await self._client()
        name = self._container_name(tenant_id, session_id)

        try:
            c = await docker.containers.get(name)
            info = await c.show()
            if info["State"]["Running"]:
                return SandboxHandle(
                    sandbox_id=info["Id"][:12], tenant_id=tenant_id,
                    session_id=session_id, backend="docker", workdir=WORKDIR,
                    meta={"container": name})
            await c.delete(force=True)
        except DockerError as e:
            if e.status != 404:
                raise

        expires_at = int(time.time()) + settings.SANDBOX_TTL_SECONDS
        config = {
            "Image": settings.SANDBOX_IMAGE,
            "Cmd": ["sleep", "infinity"],
            "User": "65534:65534",
            "WorkingDir": WORKDIR,
            "Env": ["PATH=/usr/local/bin:/usr/bin:/bin", "HOME=/tmp",
                    "LANG=C.UTF-8", "PYTHONUNBUFFERED=1"],
            "Labels": {
                f"{LABEL_PREFIX}/managed": "true",
                f"{LABEL_PREFIX}/tenant-id": tenant_id,
                f"{LABEL_PREFIX}/session-id": session_id,
                f"{LABEL_PREFIX}/expires-at": str(expires_at),
            },
            "NetworkDisabled": not settings.SANDBOX_DOCKER_ALLOW_NET,
            "HostConfig": {
                "NetworkMode": "bridge" if settings.SANDBOX_DOCKER_ALLOW_NET else "none",
                "ReadonlyRootfs": True,
                "Tmpfs": {
                    WORKDIR: f"rw,nosuid,nodev,size={settings.SANDBOX_DISK_MB}m",
                    "/tmp": "rw,nosuid,nodev,size=256m",
                },
                "CapDrop": ["ALL"],
                "SecurityOpt": ["no-new-privileges:true"],
                "Memory": settings.SANDBOX_MEM_MB * 1024 * 1024,
                "MemorySwap": settings.SANDBOX_MEM_MB * 1024 * 1024,
                "NanoCpus": int(settings.SANDBOX_CPU_CORES * 1e9),
                "PidsLimit": 128,
                "AutoRemove": False,
            },
        }
        c = await docker.containers.create(config=config, name=name)
        await c.start()
        await self._raw_exec(c, ["chown", "65534:65534", WORKDIR], user="root", timeout=10)
        await self._raw_exec(c, ["mkdir", "-p", f"{WORKDIR}/artifacts"], timeout=10)
        info = await c.show()
        log.info("sandbox container created name=%s tenant=%s", name, tenant_id)
        return SandboxHandle(sandbox_id=info["Id"][:12], tenant_id=tenant_id,
                             session_id=session_id, backend="docker",
                             workdir=WORKDIR, meta={"container": name})

    async def terminate(self, handle: SandboxHandle) -> None:
        docker = await self._client()
        try:
            c = await docker.containers.get(handle.meta["container"])
            await c.delete(force=True)
        except DockerError as e:
            if e.status != 404:
                raise

    async def exec(self, handle: SandboxHandle, command: str,
                   timeout: int = 60, workdir: str | None = None) -> ExecResult:
        docker = await self._client()
        c = await docker.containers.get(handle.meta["container"])
        cwd = self._resolve(workdir) if workdir else WORKDIR
        argv = ["timeout", "-k", "5", str(timeout), "bash", "-c", command]
        start = time.monotonic()
        try:
            code, out, err = await asyncio.wait_for(
                self._raw_exec(c, argv, workdir=cwd, timeout=timeout + 10),
                timeout=timeout + 15)
        except asyncio.TimeoutError:
            await c.restart(timeout=5)
            return ExecResult(exit_code=124, stdout="", duration_ms=timeout * 1000,
                              stderr=f"timeout after {timeout}s (container restarted)")
        dur = int((time.monotonic() - start) * 1000)
        if code == 124:
            err = (err or b"") + f"\n[timeout after {timeout}s]".encode()
        truncated = len(out) > MAX_OUTPUT or len(err) > MAX_OUTPUT
        return ExecResult(exit_code=code,
                          stdout=out[:MAX_OUTPUT].decode(errors="replace"),
                          stderr=err[:MAX_OUTPUT].decode(errors="replace"),
                          duration_ms=dur, truncated=truncated)

    async def _raw_exec(self, container, argv: list[str], *, workdir: str = WORKDIR,
                        user: str = "", timeout: int = 60) -> tuple[int, bytes, bytes]:
        ex = await container.exec(argv, workdir=workdir, user=user,
                                  stdout=True, stderr=True)
        out, err = bytearray(), bytearray()
        async with ex.start(detach=False) as stream:
            while True:
                msg = await asyncio.wait_for(stream.read_out(), timeout=timeout)
                if msg is None:
                    break
                (out if msg.stream == 1 else err).extend(msg.data)
        inspect = await ex.inspect()
        return inspect.get("ExitCode") or 0, bytes(out), bytes(err)

    @staticmethod
    def _resolve(path: str) -> str:
        p = PurePosixPath(WORKDIR) / path.lstrip("/")
        parts, stack = p.parts, []
        for seg in parts:
            if seg == "..":
                if len(stack) <= len(PurePosixPath(WORKDIR).parts):
                    raise PermissionError(f"path escape blocked: {path}")
                stack.pop()
            else:
                stack.append(seg)
        resolved = str(PurePosixPath(*stack))
        if not resolved.startswith(WORKDIR):
            raise PermissionError(f"path escape blocked: {path}")
        return resolved

    async def put_file(self, handle: SandboxHandle, path: str, content: bytes) -> None:
        docker = await self._client()
        c = await docker.containers.get(handle.meta["container"])
        dst = self._resolve(path)
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tar:
            ti = tarfile.TarInfo(name=PurePosixPath(dst).name)
            ti.size, ti.mode, ti.uid, ti.gid = len(content), 0o644, 65534, 65534
            tar.addfile(ti, io.BytesIO(content))
        parent = str(PurePosixPath(dst).parent)
        await self._raw_exec(c, ["mkdir", "-p", parent], timeout=10)
        await c.put_archive(parent, buf.getvalue())

    async def get_file(self, handle: SandboxHandle, path: str,
                       max_bytes: int = 10 << 20) -> bytes:
        docker = await self._client()
        c = await docker.containers.get(handle.meta["container"])
        src = self._resolve(path)
        tar_stream = await c.get_archive(src)
        for member in tar_stream.getmembers():
            if member.isfile():
                if member.size > max_bytes:
                    raise ValueError(f"file too large: {member.size} > {max_bytes}")
                f = tar_stream.extractfile(member)
                return f.read() if f else b""
        raise FileNotFoundError(path)

    async def list_artifacts(self, handle: SandboxHandle) -> list[dict]:
        docker = await self._client()
        c = await docker.containers.get(handle.meta["container"])
        code, out, _ = await self._raw_exec(
            c, ["find", f"{WORKDIR}/artifacts", "-type", "f",
                "-printf", "%P\\t%s\\t%T@\\n"], timeout=15)
        if code != 0:
            return []
        result = []
        for line in out.decode(errors="replace").splitlines():
            try:
                rel, size, mtime = line.split("\t")
                result.append({"path": f"artifacts/{rel}", "size": int(size),
                               "mtime": float(mtime)})
            except ValueError:
                continue
        return result

    async def reap_expired(self) -> int:
        docker = await self._client()
        containers = await docker.containers.list(
            all=True, filters={"label": [f"{LABEL_PREFIX}/managed=true"]})
        now, reaped = int(time.time()), 0
        for c in containers:
            info = await c.show()
            labels = info["Config"]["Labels"]
            if int(labels.get(f"{LABEL_PREFIX}/expires-at", "0")) < now:
                await c.delete(force=True)
                reaped += 1
                log.info("reaped expired sandbox tenant=%s",
                         labels.get(f"{LABEL_PREFIX}/tenant-id"))
        return reaped
