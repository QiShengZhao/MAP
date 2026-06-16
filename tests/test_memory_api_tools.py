async def _login_workspace(client):
    credentials = {
        "email": "memory-api@example.com",
        "password": "Str0ng!Passw0rd",
    }
    reg = (await client.post("/v1/auth/register", json={
        **credentials,
        "tenant_name": "memory-api",
    })).json()
    tok = (await client.post("/v1/auth/login", json=credentials)).json()["access_token"]
    headers = {"Authorization": f"Bearer {tok}"}
    ws = (await client.post("/v1/workspaces", headers=headers,
                            json={"name": "Memory API"})).json()["workspace_id"]
    return reg, headers, ws


async def test_memory_api_create_search_and_forget(client):
    reg, headers, workspace_id = await _login_workspace(client)

    created = await client.post("/v1/memories", headers=headers, json={
        "workspace_id": workspace_id,
        "scope": "workspace",
        "kind": "decision",
        "title": "Default model",
        "content": "Use DeepSeek for chat by default.",
    })
    assert created.status_code == 201

    found = await client.get(
        f"/v1/memories?workspace_id={workspace_id}&query=DeepSeek",
        headers=headers,
    )
    assert found.status_code == 200
    assert found.json()[0]["title"] == "Default model"

    memory_id = created.json()["id"]
    deleted = await client.delete(
        f"/v1/memories/{memory_id}?workspace_id={workspace_id}",
        headers=headers,
    )
    assert deleted.status_code == 200

    found = await client.get(
        f"/v1/memories?workspace_id={workspace_id}&query=DeepSeek",
        headers=headers,
    )
    assert found.json() == []


async def test_memory_tools_share_workspace_memory(client):
    from app.infra import db as db_mod
    from app.runtime.model_provider import ToolCall
    from app.runtime.tools import ToolContext, ToolRegistry

    reg, _, workspace_id = await _login_workspace(client)
    async with db_mod.tenant_session(reg["tenant_id"]) as db:
        ctx = ToolContext(
            tenant_id=reg["tenant_id"],
            workspace_id=workspace_id,
            user_id=reg["user_id"],
            run_id="run-1",
            session_id="session-1",
            db=db,
            emit=lambda *args, **kwargs: None,
            usage=None,
        )
        tools = ToolRegistry.for_tenant(type("Policy", (), {"allowed_tools": []})())
        written = await tools.execute(ctx, ToolCall("1", "memory_write", {
            "scope": "workspace",
            "kind": "fact",
            "title": "Language",
            "content": "The team prefers Chinese responses.",
        }))
        assert "Language" in written

        found = await tools.execute(ctx, ToolCall("2", "memory_search", {
            "query": "Chinese responses",
        }))
        assert "Chinese responses" in found
