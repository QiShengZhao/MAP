import asyncio, logging
from datetime import datetime, timedelta
from sqlalchemy import select, update
from app.infra.db import SessionLocal
from app.infra.redis_client import redis_client
from app.domain.models import Run, RunStatus, ApprovalRequest, ApprovalStatus
from app.scheduling.queue import RunQueue

log = logging.getLogger("scheduler")
STUCK_RUN_MINUTES, APPROVAL_EXPIRE_HOURS = 30, 24

async def recover_stuck_runs():
    async with SessionLocal() as db:
        cutoff = datetime.utcnow() - timedelta(minutes=STUCK_RUN_MINUTES)
        rows = (await db.execute(select(Run).where(
            Run.status == RunStatus.running,
            Run.started_at < cutoff))).scalars().all()
        for run in rows:
            if await redis_client.exists(f"lock:run:{run.id}"):
                continue
            run.status, run.error = RunStatus.failed, "worker lost (recovered)"
            log.warning("recovered stuck run %s", run.id)
        await db.commit()

async def expire_stale_approvals():
    async with SessionLocal() as db:
        cutoff = datetime.utcnow() - timedelta(hours=APPROVAL_EXPIRE_HOURS)
        await db.execute(update(ApprovalRequest)
            .where(ApprovalRequest.status == ApprovalStatus.pending,
                   ApprovalRequest.created_at < cutoff)
            .values(status=ApprovalStatus.expired))
        await db.commit()

async def main():
    logging.basicConfig(level=logging.INFO)
    await RunQueue.ensure_group()
    while True:
        try:
            await recover_stuck_runs()
            await expire_stale_approvals()
        except Exception:
            log.exception("scheduler tick failed")
        await asyncio.sleep(60)

if __name__ == "__main__":
    asyncio.run(main())