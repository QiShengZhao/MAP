import pytest


async def test_register_login_flow(client):
    r = await client.post("/v1/auth/register", json={
        "email": "a@x.com", "password": "Str0ng!Passw0rd", "tenant_name": "acme"})
    assert r.status_code == 201
    r = await client.post("/v1/auth/login", json={
        "email": "a@x.com", "password": "Str0ng!Passw0rd"})
    assert r.status_code == 200
    body = r.json()
    assert "access_token" in body or "token" in body


async def test_weak_password_rejected(client):
    r = await client.post("/v1/auth/register", json={
        "email": "b@x.com", "password": "123456", "tenant_name": "test"})
    assert r.status_code == 422


async def test_no_token_401(client):
    assert (await client.get("/v1/workspaces")).status_code == 401


async def test_workspace_sessions_list(client):
    from app.domain.models import Session
    from app.infra import db as db_mod

    reg = (await client.post("/v1/auth/register", json={
        "email": "sessions2@x.com", "password": "Str0ng!Passw0rd",
        "tenant_name": "sessions2"})).json()
    tok = (await client.post("/v1/auth/login", json={
        "email": "sessions2@x.com", "password": "Str0ng!Passw0rd"})).json()["access_token"]
    h = {"Authorization": f"Bearer {tok}"}
    ws = (await client.post("/v1/workspaces", headers=h,
                            json={"name": "Default"})).json()["workspace_id"]
    async with db_mod.SessionLocal() as db:
        db.add(Session(tenant_id=reg["tenant_id"], workspace_id=ws,
                       user_id=reg["user_id"], title="hello"))
        await db.commit()

    r = await client.get(f"/v1/workspaces/{ws}/sessions", headers=h)

    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1
    assert body[0]["title"] == "hello"


async def test_run_stream_uses_tenant_context(client):
    from app.domain.models import Run, RunEvent, Session
    from app.infra import db as db_mod

    reg = (await client.post("/v1/auth/register", json={
        "email": "stream@x.com", "password": "Str0ng!Passw0rd",
        "tenant_name": "stream"})).json()
    tok = (await client.post("/v1/auth/login", json={
        "email": "stream@x.com", "password": "Str0ng!Passw0rd"})).json()["access_token"]
    h = {"Authorization": f"Bearer {tok}"}
    ws = (await client.post("/v1/workspaces", headers=h,
                            json={"name": "Default"})).json()["workspace_id"]

    async with db_mod.tenant_session(reg["tenant_id"]) as db:
        session = Session(tenant_id=reg["tenant_id"], workspace_id=ws,
                          user_id=reg["user_id"], title="stream")
        db.add(session)
        await db.flush()
        run = Run(tenant_id=reg["tenant_id"], session_id=session.id,
                  user_id=reg["user_id"])
        db.add(run)
        await db.flush()
        db.add(RunEvent(tenant_id=reg["tenant_id"], run_id=run.id, seq=1,
                        type="run.completed", payload={"content": "ok"}))
        await db.commit()
        run_id = run.id

    r = await client.get(f"/v1/runs/{run_id}/stream?token={tok}")

    assert r.status_code == 200
    assert "event: run.completed" in r.text
