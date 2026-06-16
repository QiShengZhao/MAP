"""Run 状态机：唯一合法变更入口（CAS 乐观锁）。"""
import logging
from datetime import datetime

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models import Run, RunStatus

log = logging.getLogger("execution.statemachine")

TERMINAL = {"completed", "failed", "cancelled"}

ALLOWED: dict[str, set[str]] = {
    "queued":            {"running", "cancelled"},
    "running":           {"awaiting_approval", "paused", "completed", "failed", "cancelled"},
    "awaiting_approval": {"running", "paused", "failed", "cancelled"},
    "paused":            {"queued", "cancelled", "failed"},
    "completed": set(), "failed": set(), "cancelled": set(),
}


class InvalidTransition(Exception):
    def __init__(self, run_id: str, from_s: str, to_s: str):
        self.run_id, self.from_s, self.to_s = run_id, from_s, to_s
        super().__init__(f"run {run_id}: {from_s} -> {to_s} not allowed")


class StaleTransition(Exception):
    """乐观锁失败：状态已被并发方改走。"""


def utcnow_naive() -> datetime:
    return datetime.utcnow()


def _status(s: str | RunStatus) -> RunStatus:
    return s if isinstance(s, RunStatus) else RunStatus(s)


async def transition(db: AsyncSession, run_id: str, from_status: str,
                     to_status: str, *, reason: str | None = None,
                     extra: dict | None = None, commit: bool = True) -> None:
    if to_status not in ALLOWED.get(from_status, set()):
        raise InvalidTransition(run_id, from_status, to_status)

    values: dict = {
        "status": _status(to_status),
        "status_reason": reason or "",
        "updated_at": utcnow_naive(),
    }
    if to_status == "running":
        values["started_at"] = utcnow_naive()
    if to_status in TERMINAL:
        values["finished_at"] = utcnow_naive()
    if to_status == "paused":
        values["paused_at"] = utcnow_naive()
    if extra:
        values.update(extra)

    result = await db.execute(
        update(Run).where(Run.id == run_id, Run.status == _status(from_status)).values(**values))
    if result.rowcount == 0:
        raise StaleTransition(f"run {run_id}: expected {from_status}, concurrently changed")
    if commit:
        await db.commit()
    log.info("run %s: %s -> %s (%s)", run_id, from_status, to_status, reason or "-")
