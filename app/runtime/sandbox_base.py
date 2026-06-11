"""沙箱后端抽象：k8s(gVisor) / local(进程级 mock) 同接口。"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class ExecResult:
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int
    truncated: bool = False


@dataclass
class SandboxHandle:
    sandbox_id: str
    tenant_id: str
    session_id: str
    backend: str
    workdir: str = ""
    meta: dict = field(default_factory=dict)


class SandboxBackend(ABC):
    @abstractmethod
    async def ensure(self, tenant_id: str, session_id: str) -> SandboxHandle: ...

    @abstractmethod
    async def exec(self, handle: SandboxHandle, command: str,
                   timeout: int = 60, workdir: str | None = None) -> ExecResult: ...

    @abstractmethod
    async def put_file(self, handle: SandboxHandle, path: str, content: bytes) -> None: ...

    @abstractmethod
    async def get_file(self, handle: SandboxHandle, path: str,
                       max_bytes: int = 10 << 20) -> bytes: ...

    @abstractmethod
    async def list_artifacts(self, handle: SandboxHandle) -> list[dict]: ...

    @abstractmethod
    async def terminate(self, handle: SandboxHandle) -> None: ...
