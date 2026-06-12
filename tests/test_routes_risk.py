import pytest

RULE = {"name": "high-err", "expression": "error_rate > 0.3", "action": "flag",
        "severity": "warning"}


@pytest.fixture
async def admin_client(client):
    await client.post("/v1/auth/register", json={
        "email": "adm@x.com", "password": "Str0ng!Passw0rd", "tenant_name": "test"})
    tok = (await client.post("/v1/auth/login", json={
        "email": "adm@x.com", "password": "Str0ng!Passw0rd"})).json()["access_token"]
    client.headers["Authorization"] = f"Bearer {tok}"
    return client


async def test_crud_lifecycle(admin_client):
    r = await admin_client.post("/v1/risk/rules", json=RULE)
    assert r.status_code == 201
    rid = r.json()["id"]
    assert any(x["id"] == rid for x in (await admin_client.get("/v1/risk/rules")).json())
    r = await admin_client.patch(f"/v1/risk/rules/{rid}/toggle?enabled=false")
    assert r.json()["enabled"] is False
    assert (await admin_client.delete(f"/v1/risk/rules/{rid}")).status_code == 204


async def test_injection_expression_rejected_at_create(admin_client):
    bad = {**RULE, "name": "evil", "expression": "__import__('os').system('id')"}
    assert (await admin_client.post("/v1/risk/rules", json=bad)).status_code == 422


async def test_pause_cooldown_constraint(admin_client):
    bad = {**RULE, "name": "p1", "action": "pause", "cooldown_seconds": 3600,
           "action_params": {"duration_seconds": 600}}
    r = await admin_client.post("/v1/risk/rules", json=bad)
    assert r.status_code == 422 and "cooldown" in r.text


async def test_duplicate_name_409(admin_client):
    await admin_client.post("/v1/risk/rules", json=RULE)
    assert (await admin_client.post("/v1/risk/rules", json=RULE)).status_code == 409


async def test_dry_run_no_side_effects(admin_client, redis):
    r = await admin_client.post("/v1/risk/rules/dry-run", json={
        "expression": "error_rate > 0.3",
        "contexts": [{"error_rate": 0.5}, {"error_rate": 0.1}]})
    body = r.json()
    assert (body["hits"], body["total"]) == (1, 2)
    keys = [k async for k in redis.scan_iter("risk:cooldown:*")]
    assert not keys


async def test_platform_scope_requires_platform_admin(admin_client):
    r = await admin_client.post("/v1/risk/rules",
                                json={**RULE, "name": "plat", "platform_scope": True})
    assert r.status_code == 403
