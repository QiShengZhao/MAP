"""按配置选择沙箱后端。生产强制 k8s（见 config 校验）。"""
from functools import lru_cache

from app.config import settings
from app.runtime.sandbox_base import SandboxBackend


@lru_cache(maxsize=1)
def get_sandbox_backend() -> SandboxBackend | None:
    if settings.SANDBOX_BACKEND == "local":
        from app.runtime.sandbox_local import LocalSandboxBackend
        return LocalSandboxBackend()
    if settings.SANDBOX_BACKEND == "k8s":
        return None
    from app.runtime.sandbox_local import LocalSandboxBackend
    return LocalSandboxBackend()
