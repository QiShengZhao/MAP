from datetime import datetime, timedelta

import pytest


async def _tenant_workspace_user(client):
    credentials = {
        "email": "memory@example.com",
        "password": "Str0ng!Passw0rd",
    }
    reg = (await client.post("/v1/auth/register", json={
        **credentials,
        "tenant_name": "memory",
    })).json()
    tok = (await client.post("/v1/auth/login", json=credentials)).json()["access_token"]
    headers = {"Authorization": f"Bearer {tok}"}
    ws = (await client.post("/v1/workspaces", headers=headers,
                            json={"name": "Memory"})).json()["workspace_id"]
    return reg["tenant_id"], ws, reg["user_id"]


async def test_memory_search_respects_scope_and_soft_delete(client):
    from app.infra import db as db_mod
    from app.memory.service import MemoryService

    tenant_id, workspace_id, user_id = await _tenant_workspace_user(client)
    async with db_mod.tenant_session(tenant_id) as db:
        visible = await MemoryService.write(
            db,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            user_id=user_id,
            scope="workspace",
            kind="decision",
            title="Use DeepSeek",
            content="Use DeepSeek for default chat completions.",
        )
        await MemoryService.write(
            db,
            tenant_id=tenant_id,
            workspace_id="other-workspace",
            user_id=user_id,
            scope="workspace",
            kind="decision",
            title="Hidden",
            content="This belongs to another workspace.",
        )
        await db.commit()

        results = await MemoryService.search(
            db,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            user_id=user_id,
            query="DeepSeek chat",
        )
        assert [item.id for item in results] == [visible.id]

        await MemoryService.forget(
            db, tenant_id=tenant_id, memory_id=visible.id,
            workspace_id=workspace_id, user_id=user_id)
        await db.commit()
        results = await MemoryService.search(
            db,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            user_id=user_id,
            query="DeepSeek",
        )
        assert results == []


async def test_session_summary_rolls_forward(client):
    from app.infra import db as db_mod
    from app.memory.service import MemoryService

    tenant_id, workspace_id, user_id = await _tenant_workspace_user(client)
    async with db_mod.tenant_session(tenant_id) as db:
        summary = await MemoryService.update_session_summary(
            db,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            session_id="session-1",
            messages=[
                {"id": "m1", "role": "user", "content": {"text": "用户喜欢中文回复。"}},
                {"id": "m2", "role": "assistant", "content": {"text": "我会用中文。"}},
            ],
        )
        await db.commit()

        assert summary.session_id == "session-1"
        assert summary.last_message_id == "m2"
        assert "用户喜欢中文回复" in summary.summary


async def test_expired_memories_are_hidden(client):
    from app.infra import db as db_mod
    from app.memory.service import MemoryService

    tenant_id, workspace_id, user_id = await _tenant_workspace_user(client)
    async with db_mod.tenant_session(tenant_id) as db:
        await MemoryService.write(
            db,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            user_id=user_id,
            scope="workspace",
            kind="fact",
            title="Expired",
            content="old fact",
            expires_at=datetime.utcnow() - timedelta(days=1),
        )
        await db.commit()

        results = await MemoryService.search(
            db,
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            user_id=user_id,
            query="old fact",
        )
        assert results == []
