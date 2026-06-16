import asyncio
import logging
from datetime import datetime, timedelta

from sqlalchemy import select, update

from app.config import settings
from app.domain.models import ApprovalRequest, ApprovalStatus, Run, RunStatus
from app.execution.run_statemachine import InvalidTransition, StaleTransition, transition
from app.infra import db as db_mod
from app.infra.redis_client import get_redis, redis_client
from app.platform_services.notifications import send_webhook
from app.scheduling.queue import RunQueue

log = logging.getLogger("scheduler")
STUCK_RUN_MINUTES, APPROVAL_EXPIRE_HOURS = 30, 24


async def recover_stuck_runs():
    async with db_mod.session_factory() as db:
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
    async with db_mod.session_factory() as db:
        cutoff = datetime.utcnow() - timedelta(hours=APPROVAL_EXPIRE_HOURS)
        await db.execute(update(ApprovalRequest)
            .where(ApprovalRequest.status == ApprovalStatus.pending,
                   ApprovalRequest.created_at < cutoff)
            .values(status=ApprovalStatus.expired))
        await db.commit()


async def recover_unqueued_runs(db=None) -> int:
    """Re-dispatch queued runs that have no atomic Redis dispatch marker."""
    owns_session = db is None
    if owns_session:
        ctx = db_mod.session_factory()
        db = await ctx.__aenter__()
    try:
        rows = (await db.scalars(
            select(Run).where(Run.status == RunStatus.queued)
            .order_by(Run.created_at).limit(100))).all()
        recovered = 0
        for run in rows:
            if await redis_client.exists(RunQueue.dispatch_key(str(run.id))):
                continue
            if await RunQueue.enqueue(str(run.tenant_id), str(run.id)):
                recovered += 1
        return recovered
    finally:
        if owns_session:
            await ctx.__aexit__(None, None, None)


async def _requeue_paused_run(run: Run) -> None:
    async with db_mod.session_factory() as db:
        try:
            await transition(db, str(run.id), "paused", "queued", reason="auto-resume")
        except (StaleTransition, InvalidTransition):
            return
    await RunQueue.enqueue(str(run.tenant_id), str(run.id), resume=True)
    log.info("run %s requeued for resume", run.id)


async def cancel_stale_paused_runs() -> int:
    """Paused 超过 PAUSED_RUN_MAX_DAYS 的 Run 自动 cancel 并通知。"""
    days = settings.PAUSED_RUN_MAX_DAYS
    if days <= 0:
        return 0
    cutoff = datetime.utcnow() - timedelta(days=days)
    cancelled = 0
    async with db_mod.session_factory() as db:
        rows = (await db.scalars(
            select(Run).where(
                Run.status == RunStatus.paused,
                Run.paused_at.is_not(None),
                Run.paused_at < cutoff,
            ).limit(100))).all()
        for run in rows:
            try:
                await transition(db, str(run.id), "paused", "cancelled",
                                 reason=f"auto-cancel: paused>{days}d", commit=False)
            except (StaleTransition, InvalidTransition):
                continue
            run.finished_at = datetime.utcnow()
            run.error = f"paused run auto-cancelled after {days} days"
            cancelled += 1
            await send_webhook({
                "event": "run.paused_stale_cancelled",
                "tenant_id": str(run.tenant_id),
                "run_id": str(run.id),
                "paused_at": run.paused_at.isoformat() if run.paused_at else None,
                "max_days": days,
            })
        if cancelled:
            await db.commit()
            log.info("auto-cancelled %d stale paused runs", cancelled)
    return cancelled


async def stale_paused_scanner_loop():
    while True:
        try:
            await cancel_stale_paused_runs()
        except Exception:
            log.exception("stale paused scanner error")
        await asyncio.sleep(3600)


async def resume_scanner_loop():
    """每 30s：paused 且租户/Run 未再被暂停 → 重新入队。"""
    while True:
        try:
            async with db_mod.session_factory() as db:
                rows = (await db.scalars(
                    select(Run).where(Run.status == RunStatus.paused)
                    .order_by(Run.paused_at).limit(100))).all()
            r = await get_redis()
            for run in rows:
                if await r.exists(f"risk:paused:{run.tenant_id}"):
                    continue
                if await r.exists(f"run:{run.id}:pause"):
                    continue
                await _requeue_paused_run(run)
        except Exception:
            log.exception("resume scanner error")
        await asyncio.sleep(30)


async def main():
    logging.basicConfig(level=logging.INFO)
    await RunQueue.ensure_group()
    asyncio.create_task(resume_scanner_loop())
    asyncio.create_task(stale_paused_scanner_loop())
    while True:
        try:
            await recover_stuck_runs()
            await expire_stale_approvals()
            recovered = await recover_unqueued_runs()
            if recovered:
                log.warning("re-dispatched %d queued runs", recovered)
        except Exception:
            log.exception("scheduler tick failed")
        await asyncio.sleep(60)


if __name__ == "__main__":
    asyncio.run(main())
