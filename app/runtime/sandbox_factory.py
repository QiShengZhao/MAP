"""按配置选择沙箱后端。生产强制 k8s/docker（见 config 校验）。"""
from functools import lru_cache

from app.config import settings
from app.runtime.sandbox_base import SandboxBackend


@lru_cache(maxsize=1)
def get_sandbox_backend() -> SandboxBackend | None:
    if settings.SANDBOX_BACKEND == "docker":
        from app.runtime.sandbox_docker import DockerSandboxBackend
        return DockerSandboxBackend()
    if settings.SANDBOX_BACKEND == "local":
        from app.runtime.sandbox_local import LocalSandboxBackend
        return LocalSandboxBackend()
    return None
