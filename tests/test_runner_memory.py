async def _seed_run(client):
    from app.domain.models import Message, Run, RunStatus
    from app.infra import db as db_mod

    credentials = {
        "email": "runner-memory@example.com",
        "password": "Str0ng!Passw0rd",
    }
    reg = (await client.post("/v1/auth/register", json={
        **credentials,
        "tenant_name": "runner-memory",
    })).json()
    tok = (await client.post("/v1/auth/login", json=credentials)).json()["access_token"]
    h = {"Authorization": f"Bearer {tok}"}
    ws = (await client.post("/v1/workspaces", headers=h,
                            json={"name": "Runner Memory"})).json()["workspace_id"]
    async with db_mod.tenant_session(reg["tenant_id"]) as db:
        run = Run(tenant_id=reg["tenant_id"], session_id="session-1",
                  user_id=reg["user_id"], status=RunStatus.running)
        db.add(run)
        await db.flush()
        user = Message(tenant_id=reg["tenant_id"], session_id="session-1",
                       run_id=run.id, role="user",
                       content={"text": "记住：以后都用中文回复。"})
        db.add(user)
        await db.commit()
        return reg, ws, run.id, user.id


async def test_build_history_injects_summary_and_relevant_memory(client):
    from app.execution.runner import Runner
    from app.infra import db as db_mod
    from app.memory.service import MemoryService

    reg, workspace_id, run_id, _ = await _seed_run(client)
    async with db_mod.tenant_session(reg["tenant_id"]) as db:
        await MemoryService.update_session_summary(
            db,
            tenant_id=reg["tenant_id"],
            workspace_id=workspace_id,
            session_id="session-1",
            messages=[{"id": "m1", "role": "user",
                       "content": {"text": "用户喜欢中文回复。"}}],
        )
        await MemoryService.write(
            db,
            tenant_id=reg["tenant_id"],
            workspace_id=workspace_id,
            user_id=reg["user_id"],
            scope="workspace",
            kind="preference",
            title="Language",
            content="用户偏好中文回复。",
        )
        await db.commit()
        run = await db.get(__import__("app.domain.models", fromlist=["Run"]).Run, run_id)
        runner = Runner(reg["tenant_id"], run_id, workspace_id)
        agent = type("Agent", (), {
            "instructions": "Be helpful.",
            "name": "default",
        })()

        history = await runner._build_history(db, run, agent)

        system = history[0]["content"]
        assert "Session Summary" in system
        assert "用户喜欢中文回复" in system
        assert "Relevant Long-Term Memory" in system
        assert "用户偏好中文回复" in system


async def test_finish_updates_summary_and_captures_memory(client, monkeypatch):
    from app.domain.models import Run
    from app.execution.runner import Runner
    from app.infra import db as db_mod
    from app.memory.service import MemoryService

    reg, workspace_id, run_id, _ = await _seed_run(client)
    async with db_mod.tenant_session(reg["tenant_id"]) as db:
        run = await db.get(Run, run_id)
        runner = Runner(reg["tenant_id"], run_id, workspace_id)

        async def no_emit(*args, **kwargs):
            return None

        monkeypatch.setattr(runner, "emit", no_emit)
        usage = type("Usage", (), {"snapshot": lambda self: {}})()
        await runner._finish(db, run, "好的，我会记住。", usage)

        summary = await MemoryService.get_session_summary(
            db, tenant_id=reg["tenant_id"], session_id="session-1")
        assert summary is not None
        assert "以后都用中文回复" in summary.summary

        memories = await MemoryService.search(
            db,
            tenant_id=reg["tenant_id"],
            workspace_id=workspace_id,
            session_id="session-1",
            run_id=run_id,
            user_id=reg["user_id"],
            query="中文回复",
        )
        assert memories
