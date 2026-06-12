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
