import pytest


async def test_login_lockout_by_email(client):
    await client.post("/v1/auth/register", json={
        "email": "lock@x.com", "password": "Str0ng!Passw0rd", "tenant_name": "test"})
    for _ in range(5):
        await client.post("/v1/auth/login",
                          json={"email": "lock@x.com", "password": "wrong-Pass1!"})
    r = await client.post("/v1/auth/login",
                          json={"email": "lock@x.com", "password": "Str0ng!Passw0rd"})
    assert r.status_code == 429


async def test_refresh_rotation_old_token_dead(client):
    await client.post("/v1/auth/register", json={
        "email": "r@x.com", "password": "Str0ng!Passw0rd", "tenant_name": "test"})
    tokens = (await client.post("/v1/auth/login", json={
        "email": "r@x.com", "password": "Str0ng!Passw0rd"})).json()
    r1 = await client.post("/v1/auth/refresh",
                           json={"refresh_token": tokens["refresh_token"]})
    assert r1.status_code == 200
    r2 = await client.post("/v1/auth/refresh",
                           json={"refresh_token": tokens["refresh_token"]})
    assert r2.status_code == 401


async def test_refresh_token_cannot_access_api(client):
    await client.post("/v1/auth/register", json={
        "email": "t@x.com", "password": "Str0ng!Passw0rd", "tenant_name": "test"})
    tokens = (await client.post("/v1/auth/login", json={
        "email": "t@x.com", "password": "Str0ng!Passw0rd"})).json()
    r = await client.get("/v1/workspaces",
                         headers={"Authorization": f"Bearer {tokens['refresh_token']}"})
    assert r.status_code == 401


async def test_logout_revokes_access(client):
    await client.post("/v1/auth/register", json={
        "email": "o@x.com", "password": "Str0ng!Passw0rd", "tenant_name": "test"})
    tok = (await client.post("/v1/auth/login", json={
        "email": "o@x.com", "password": "Str0ng!Passw0rd"})).json()["access_token"]
    h = {"Authorization": f"Bearer {tok}"}
    assert (await client.get("/v1/workspaces", headers=h)).status_code == 200
    await client.post("/v1/auth/logout", headers=h)
    assert (await client.get("/v1/workspaces", headers=h)).status_code == 401
