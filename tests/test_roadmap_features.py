"""检查点大 history S3 溢出与 stale paused 取消。"""
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import settings
from app.domain.models import Run, RunStatus
from app.runtime.state import Checkpoint, load_checkpoint, save_checkpoint
from app.scheduling.scheduler import cancel_stale_paused_runs


async def test_checkpoint_offloads_and_hydrates_large_history(
        db_session, monkeypatch):
    from app.infra.object_storage import object_storage

    stored = {}

    async def fake_put(key, data, mime="application/octet-stream"):
        stored[key] = data

    async def fake_get(key):
        return stored[key]

    monkeypatch.setattr(object_storage, "put", fake_put)
    monkeypatch.setattr(object_storage, "get", fake_get)
    monkeypatch.setattr(settings, "CHECKPOINT_S3_THRESHOLD_MESSAGES", 5)
    monkeypatch.setattr(settings, "CHECKPOINT_INLINE_TAIL_MESSAGES", 2)

    history = [{"role": "user", "content": f"m{i}"} for i in range(8)]
    cp = Checkpoint("run-chk-1", "t1", {
        "messages": list(history), "history": list(history),
        "iteration": 0, "current_agent": "default", "seq": 0, "usage_partial": {},
    })
    await save_checkpoint(db_session, cp, commit=True)
    assert cp.data.get("messages_ref")

    loaded = await load_checkpoint(db_session, "run-chk-1")
    assert len(loaded.data["messages"]) == 8
    assert loaded.data["messages"][0]["content"] == "m0"


async def test_cancel_stale_paused_runs(db_session, db_engine, monkeypatch):
    from app.infra import db as db_mod

    monkeypatch.setattr(settings, "PAUSED_RUN_MAX_DAYS", 7)
    notified = []

    async def fake_notify(payload, url=None):
        notified.append(payload)
        return True

    monkeypatch.setattr("app.scheduling.scheduler.send_webhook", fake_notify)

    factory = async_sessionmaker(db_engine, expire_on_commit=False)

    @asynccontextmanager
    async def _factory():
        async with factory() as s:
            yield s

    monkeypatch.setattr(db_mod, "session_factory", _factory)

    old = Run(id="run-stale", tenant_id="t1", session_id="s1", user_id="u1",
              status=RunStatus.paused,
              paused_at=datetime.utcnow() - timedelta(days=8))
    fresh = Run(id="run-fresh", tenant_id="t1", session_id="s1", user_id="u1",
                status=RunStatus.paused,
                paused_at=datetime.utcnow() - timedelta(days=1))
    db_session.add_all([old, fresh])
    await db_session.commit()

    count = await cancel_stale_paused_runs()
    assert count == 1

    async with factory() as s:
        refreshed = await s.get(Run, "run-stale")
        assert refreshed.status == RunStatus.cancelled
    assert notified[0]["event"] == "run.paused_stale_cancelled"
