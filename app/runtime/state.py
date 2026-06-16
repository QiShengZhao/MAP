"""RunState 检查点（崩溃续跑 + pause/resume + 大 history S3 溢出）。"""
import json
import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.domain.models import RunState
from app.infra.object_storage import object_storage

log = logging.getLogger("runtime.state")

CHECKPOINT_SCHEMA_VERSION = 3


def utcnow_naive() -> datetime:
    return datetime.utcnow()


class Checkpoint:
    def __init__(self, run_id: str, tenant_id: str, data: dict, version: int = 0):
        self.run_id, self.tenant_id, self.data, self.version = run_id, tenant_id, data, version


async def _hydrate_messages(data: dict) -> list:
    history = data.get("messages") or data.get("history") or []
    ref = data.get("messages_ref")
    if not ref:
        return history
    try:
        raw = await object_storage.get(ref)
        payload = json.loads(raw)
        full = payload.get("messages") or payload.get("history") or []
        tail = history
        if tail and full and len(full) > len(tail):
            return full[: len(full) - len(tail)] + tail
        return full or history
    except Exception:
        log.exception("failed to load checkpoint messages from %s", ref)
        return history


async def _maybe_offload_messages(cp: Checkpoint) -> None:
    history = cp.data.get("messages") or cp.data.get("history") or []
    if len(history) <= settings.CHECKPOINT_S3_THRESHOLD_MESSAGES:
        cp.data.pop("messages_ref", None)
        return
    key = object_storage.checkpoint_key(cp.tenant_id, cp.run_id, cp.version + 1)
    payload = json.dumps({"messages": history}, ensure_ascii=False).encode()
    await object_storage.put(key, payload, mime="application/json")
    tail = history[-settings.CHECKPOINT_INLINE_TAIL_MESSAGES:]
    cp.data["messages_ref"] = key
    cp.data["messages"] = tail
    cp.data["history"] = tail


async def save_checkpoint(db: AsyncSession, cp: Checkpoint, *, commit: bool = True) -> None:
    await _maybe_offload_messages(cp)
    cp.data["_schema"] = CHECKPOINT_SCHEMA_VERSION
    cp.data["_saved_at"] = datetime.now(timezone.utc).isoformat()
    row = await db.scalar(select(RunState).where(RunState.run_id == cp.run_id))
    if row is None:
        row = RunState(run_id=cp.run_id, tenant_id=cp.tenant_id)
        db.add(row)
    row.tenant_id = cp.tenant_id
    row.state = cp.data
    row.version = cp.version + 1
    row.current_agent = cp.data.get("current_agent", row.current_agent or "default")
    row.turn = cp.data.get("iteration", cp.data.get("turn", 0))
    row.history = cp.data.get("messages", cp.data.get("history", []))
    row.pending_tool_call = cp.data.get("pending_approval")
    row.updated_at = utcnow_naive()
    await db.flush()
    if commit:
        await db.commit()


async def load_checkpoint(db: AsyncSession, run_id: str) -> Checkpoint | None:
    row = await db.scalar(select(RunState).where(RunState.run_id == run_id))
    if not row:
        return None
    data = row.state if row.state else {
        "messages": row.history,
        "iteration": row.turn,
        "current_agent": row.current_agent,
        "pending_approval": row.pending_tool_call,
        "seq": 0,
        "usage_partial": {},
        "tool_results": {},
    }
    if data.get("_schema", 1) > CHECKPOINT_SCHEMA_VERSION:
        raise ValueError(f"checkpoint schema too new: {data.get('_schema')}")
    full_history = await _hydrate_messages(data)
    data["messages"] = full_history
    data["history"] = full_history
    return Checkpoint(run_id=run_id, tenant_id=str(row.tenant_id),
                      data=data, version=row.version or 0)


class StateService:
    @staticmethod
    async def load(db, tenant_id, run_id):
        return await db.scalar(select(RunState).where(
            RunState.run_id == run_id, RunState.tenant_id == tenant_id))

    @staticmethod
    async def checkpoint(db, tenant_id, run_id, *, current_agent, turn,
                         history, pending_tool_call=None, seq=0,
                         usage_partial=None, paused_from=None,
                         tool_results=None, commit=True):
        cp = Checkpoint(run_id=run_id, tenant_id=tenant_id, data={
            "messages": history,
            "history": history,
            "current_agent": current_agent,
            "iteration": turn,
            "turn": turn,
            "seq": seq,
            "usage_partial": usage_partial or {},
            "pending_approval": pending_tool_call,
            "paused_from": paused_from,
            "tool_results": tool_results or {},
        }, version=0)
        existing = await load_checkpoint(db, run_id)
        if existing:
            cp.version = existing.version
        await save_checkpoint(db, cp, commit=commit)

    @staticmethod
    async def clear(db, tenant_id, run_id):
        state = await StateService.load(db, tenant_id, run_id)
        if state:
            await db.delete(state)
