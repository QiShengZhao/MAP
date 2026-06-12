import asyncio
import logging
import time

from app.config import settings

log = logging.getLogger("reaper")


async def reap_once() -> int:
    if settings.SANDBOX_BACKEND == "docker":
        from app.runtime.sandbox_docker import DockerSandboxBackend
        backend = DockerSandboxBackend()
        return await backend.reap_expired()

    if settings.SANDBOX_BACKEND == "local":
        from pathlib import Path
        from app.runtime.sandbox_local import BASE_DIR
        now = time.time()
        reaped = 0
        if not BASE_DIR.exists():
            return 0
        for tenant_dir in BASE_DIR.iterdir():
            if not tenant_dir.is_dir():
                continue
            for sess_dir in tenant_dir.iterdir():
                if not sess_dir.is_dir():
                    continue
                age = now - sess_dir.stat().st_mtime
                if age > settings.SANDBOX_TTL_SECONDS:
                    import shutil
                    shutil.rmtree(sess_dir, ignore_errors=True)
                    reaped += 1
        return reaped

    from kubernetes_asyncio import client
    from app.runtime.sandbox import load_kube
    await load_kube()
    api = client.CoreV1Api()
    pods = await api.list_pod_for_all_namespaces(
        label_selector="app=agent-sandbox")
    now = int(time.time())
    reaped = 0
    for pod in pods.items:
        created = int((pod.metadata.annotations or {})
                      .get("sandbox/created-at", now))
        if (now - created > settings.SANDBOX_TTL_SECONDS or
                pod.status.phase in ("Succeeded", "Failed")):
            log.info("reaping %s/%s", pod.metadata.namespace, pod.metadata.name)
            try:
                await api.delete_namespaced_pod(
                    pod.metadata.name, pod.metadata.namespace,
                    grace_period_seconds=0)
                reaped += 1
            except Exception:
                pass
    return reaped


async def main():
    logging.basicConfig(level=logging.INFO)
    while True:
        try:
            n = await reap_once()
            if n:
                log.info("reaped %s sandboxes", n)
        except Exception:
            log.exception("reap failed")
        await asyncio.sleep(120)


if __name__ == "__main__":
    asyncio.run(main())
