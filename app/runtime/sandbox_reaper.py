import asyncio, time, logging
from kubernetes_asyncio import client
from app.runtime.sandbox import load_kube
from app.config import settings

log = logging.getLogger("reaper")

async def reap_once():
    await load_kube()
    api = client.CoreV1Api()
    pods = await api.list_pod_for_all_namespaces(
        label_selector="app=agent-sandbox")
    now = int(time.time())
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
            except Exception:
                pass

async def main():
    logging.basicConfig(level=logging.INFO)
    while True:
        try:
            await reap_once()
        except Exception:
            log.exception("reap failed")
        await asyncio.sleep(120)

if __name__ == "__main__":
    asyncio.run(main())