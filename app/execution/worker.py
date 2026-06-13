import asyncio
import logging
import os
import socket

from app.config import settings
from app.execution.runner import RunOutcome, Runner
from app.observability.tracing import setup_tracing
from app.scheduling.lock import DistributedLock
from app.scheduling.queue import RunQueue

log = logging.getLogger("worker")
CONCURRENCY = int(os.getenv("WORKER_CONCURRENCY", "4"))


async def handle(entry_id, task, sem):
    async with sem:
        lock = DistributedLock(f"run:{task['run_id']}", ttl=settings.RUN_LOCK_TTL)
        if not await lock.acquire():
            await RunQueue.ack(entry_id)
            return
        keepalive = asyncio.create_task(_keepalive(lock))
        handled = False
        try:
            outcome = await Runner(
                task["tenant_id"], task["run_id"],
                task.get("workspace_id", "")).execute(resume=bool(task.get("resume")))
            handled = outcome in {
                RunOutcome.COMPLETED, RunOutcome.FAILED,
                RunOutcome.PAUSED, RunOutcome.CANCELLED,
            }
            if outcome == RunOutcome.PAUSED:
                log.info("run %s paused (normal exit)", task["run_id"])
        except Exception:
            log.exception("run %s crashed", task["run_id"])
        finally:
            keepalive.cancel()
            try:
                await keepalive
            except asyncio.CancelledError:
                pass
            await lock.release()
            if handled:
                await RunQueue.ack(entry_id)
                await RunQueue.mark_consumed(task["run_id"])


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
