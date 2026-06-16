"""平台管理员与租户切换。"""
import jwt
from sqlalchemy import select

from app.config import settings
from app.domain.models import User
from app.security.jwt_keys import verify


async def test_platform_admin_can_create_platform_rule(client, db_session):
    cred = {"email": "plat@x.com", "password": "Str0ng!Passw0rd"}
    await client.post("/v1/auth/register", json={**cred, "tenant_name": "plat-admin"})
    user = await db_session.scalar(select(User).where(User.email == cred["email"]))
    user.is_platform_admin = True
    await db_session.commit()

    tok = (await client.post("/v1/auth/login", json=cred)).json()["access_token"]
    claims = verify(tok)
    assert claims.get("padm") is True

    h = {"Authorization": f"Bearer {tok}"}
    r = await client.post("/v1/risk/rules", headers=h, json={
        "name": "plat-rule", "expression": "error_rate > 0.5",
        "action": "flag", "platform_scope": True,
    })
    assert r.status_code == 201
    assert r.json()["platform_scope"] is True


async def test_switch_tenant_issues_new_token(client):
    cred = {"email": "switch@x.com", "password": "Str0ng!Passw0rd"}
    await client.post("/v1/auth/register", json={**cred, "tenant_name": "Tenant A"})
    login = await client.post("/v1/auth/login", json=cred)
    tok_a = login.json()["access_token"]

    tenant_b = (await client.post("/v1/auth/tenants", headers={
        "Authorization": f"Bearer {tok_a}",
    }, json={"name": "Tenant B", "slug": "tenant-b"})).json()["tenant_id"]

    switched = (await client.post("/v1/auth/switch-tenant", headers={
        "Authorization": f"Bearer {tok_a}",
    }, json={"tenant_id": tenant_b})).json()

    claims = jwt.decode(
        switched["access_token"],
        settings.JWT_KEYS.get(settings.JWT_ACTIVE_KID, settings.JWT_SECRET),
        algorithms=["HS256"],
    )
    assert claims["tid"] == tenant_b
