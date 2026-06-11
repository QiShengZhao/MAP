import asyncio, logging, os, socket
from app.scheduling.queue import RunQueue
from app.scheduling.lock import DistributedLock
from app.execution.runner import Runner
from app.config import settings
from app.observability.tracing import setup_tracing

log = logging.getLogger("worker")
CONCURRENCY = int(os.getenv("WORKER_CONCURRENCY", "4"))

async def handle(entry_id, task, sem):
    async with sem:
        lock = DistributedLock(f"run:{task['run_id']}", ttl=settings.RUN_LOCK_TTL)
        if not await lock.acquire():
            await RunQueue.ack(entry_id)
            return
        keepalive = asyncio.create_task(_keepalive(lock))
        try:
            await Runner(task["tenant_id"], task["run_id"],
                         task.get("workspace_id", "")).execute()
        except Exception:
            log.exception("run %s crashed", task["run_id"])
        finally:
            keepalive.cancel()
            await lock.release()
            await RunQueue.ack(entry_id)

async def _keepalive(lock):
    while True:
        await asyncio.sleep(60)
        await lock.extend()

async def main():
    logging.basicConfig(level=logging.INFO)
    setup_tracing("agent-worker")
    consumer = f"worker-{socket.gethostname()}-{os.getpid()}"
    sem = asyncio.Semaphore(CONCURRENCY)
    log.info("worker %s started", consumer)
    for entry_id, task in await RunQueue.claim_stale(consumer):
        asyncio.create_task(handle(entry_id, task, sem))
    async for entry_id, task in RunQueue.consume(consumer):
        asyncio.create_task(handle(entry_id, task, sem))

if __name__ == "__main__":
    asyncio.run(main())