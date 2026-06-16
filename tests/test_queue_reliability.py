import json
from types import SimpleNamespace

import pytest

from app.domain.models import Run, RunStatus
from app.execution import worker
from app.scheduling import queue as queue_mod
from app.scheduling import scheduler
from app.scheduling.lock import DistributedLock


async def test_enqueue_is_idempotent(redis, monkeypatch):
    monkeypatch.setattr(queue_mod, "redis_client", redis)

    first = await queue_mod.RunQueue.enqueue("t1", "r1", "w1")
    second = await queue_mod.RunQueue.enqueue("t1", "r1", "w1")

    assert first is True
    assert second is False
    entries = await redis.xrange(queue_mod.STREAM)
    assert len(entries) == 1
    assert json.loads(entries[0][1]["data"])["run_id"] == "r1"


async def test_lock_extend_does_not_refresh_replaced_owner(redis, monkeypatch):
    from app.scheduling import lock as lock_mod

    monkeypatch.setattr(lock_mod, "redis_client", redis)
    lock = DistributedLock("run-1", ttl=30)
    assert await lock.acquire()
    await redis.set(lock.key, "new-owner", ex=5)

    assert await lock.extend() is False
    assert await redis.ttl(lock.key) <= 5


async def test_recover_unqueued_runs_dispatches_missing_marker(
        db_session, redis, monkeypatch):
    run = Run(id="r-missing", tenant_id="t1", session_id="s1",
              user_id="u1", status=RunStatus.queued)
    db_session.add(run)
    await db_session.commit()

    monkeypatch.setattr(queue_mod, "redis_client", redis)
    monkeypatch.setattr(scheduler, "redis_client", redis)

    recovered = await scheduler.recover_unqueued_runs(db_session)

    assert recovered == 1
    assert await redis.exists(queue_mod.RunQueue.dispatch_key("r-missing"))


class _FakeLock:
    async def acquire(self):
        return True

    async def release(self):
        return None

    async def extend(self):
        return None


class _CrashingRunner:
    def __init__(self, *args, **kwargs):
        pass

    async def execute(self, **kwargs):
        raise RuntimeError("worker process failure")


class _CompletedRunner:
    def __init__(self, *args, **kwargs):
        pass

    async def execute(self, **kwargs):
        return worker.RunOutcome.COMPLETED


@pytest.mark.parametrize("runner_cls,expected_acks", [
    (_CrashingRunner, []),
    (_CompletedRunner, ["1-0"]),
])
async def test_worker_only_acks_handled_runs(
        runner_cls, expected_acks, monkeypatch):
    acks = []

    monkeypatch.setattr(worker, "Runner", runner_cls)
    monkeypatch.setattr(worker, "DistributedLock",
                        lambda *args, **kwargs: _FakeLock())

    async def ack(entry_id):
        acks.append(entry_id)

    async def mark_consumed(run_id):
        return None

    monkeypatch.setattr(worker.RunQueue, "ack", ack)
    monkeypatch.setattr(worker.RunQueue, "mark_consumed", mark_consumed)
    sem = __import__("asyncio").Semaphore(1)

    await worker.handle("1-0", {
        "tenant_id": "t1", "run_id": "r1", "workspace_id": "w1",
    }, sem)

    assert acks == expected_acks
