"""pause/resume 端到端：检查点往返 + API 恢复入队。"""
import json
from datetime import datetime
from types import SimpleNamespace

import pytest

from app.domain.models import Run, RunStatus
from app.execution.runner import Runner
from app.scheduling import queue as queue_mod


@pytest.fixture
async def pause_resume_ctx(client, redis, monkeypatch):
    monkeypatch.setattr(queue_mod, "redis_client", redis)
    cred = {"email": "pause@x.com", "password": "Str0ng!Passw0rd"}
    reg = (await client.post("/v1/auth/register", json={
        **cred, "tenant_name": "pause-resume",
    })).json()
    tok = (await client.post("/v1/auth/login", json=cred)).json()["access_token"]
    h = {"Authorization": f"Bearer {tok}"}
    ws = (await client.post("/v1/workspaces", headers=h,
                            json={"name": "Pause WS"})).json()["workspace_id"]
    return reg, tok, h, ws, redis


async def test_pause_checkpoint_and_restore_roundtrip(pause_resume_ctx, db_session):
    from app.infra import db as db_mod

    reg, _, _, ws, _ = pause_resume_ctx
    run = Run(id="run-pause-1", tenant_id=reg["tenant_id"], session_id="s1",
              user_id=reg["user_id"], status=RunStatus.running)
    db_session.add(run)
    await db_session.commit()

    runner = Runner(reg["tenant_id"], "run-pause-1", ws)
    history = [{"role": "user", "content": "hello"}]
    current = SimpleNamespace(name="default", model="gpt-4o", tool_names=[])
    usage = SimpleNamespace(snapshot=lambda: {"tokens": 0})

    async def noop_emit(*args, **kwargs):
        return None

    runner.emit = noop_emit
    runner.seq = 1
    runner._tool_results = {"tc-1": "cached"}

    async with db_mod.tenant_session(reg["tenant_id"]) as db:
        await runner._do_pause(
            db, run, current, turn=2, history=history,
            paused_from="running", reason="manual:test", usage=usage,
        )
        refreshed = await db.get(Run, "run-pause-1")
        assert refreshed.status == RunStatus.paused

    async with db_mod.tenant_session(reg["tenant_id"]) as db:
        from app.runtime.agents import AgentRegistry

        agents = await AgentRegistry.load(db, reg["tenant_id"])
        usage2 = SimpleNamespace(snapshot=lambda: {}, restore=lambda x: None)
        restored = await runner._restore_from_checkpoint(db, run, agents, usage2)
        assert restored is not None
        agent, turn, hist = restored
        assert turn == 2
        assert hist == history
        assert runner._tool_results.get("tc-1") == "cached"


async def test_resume_api_requeues_paused_run(client, pause_resume_ctx, monkeypatch):
    from app.infra import db as db_mod

    reg, _, h, ws, redis = pause_resume_ctx
    async with db_mod.tenant_session(reg["tenant_id"]) as db:
        db.add(Run(id="run-pause-2", tenant_id=reg["tenant_id"], session_id="s2",
                 user_id=reg["user_id"], status=RunStatus.paused,
                 paused_at=datetime.utcnow()))
        await db.commit()

    monkeypatch.setattr(queue_mod, "redis_client", redis)
    resp = await client.post("/v1/risk/runs/run-pause-2/resume", headers=h)
    assert resp.status_code == 202
    entries = await redis.xrange(queue_mod.STREAM)
    assert len(entries) == 1
    payload = json.loads(entries[0][1]["data"])
    assert payload["run_id"] == "run-pause-2"
    assert payload.get("resume") is True

