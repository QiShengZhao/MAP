"""注册/登录/刷新/登出/建租户。"""
import logging
import re
import uuid

import jwt as pyjwt
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr, field_validator
from sqlalchemy import select

from app.api.deps import get_auth
from app.config import settings
from app.domain.models import Tenant, TenantMember, TenantPolicy, User
from app.infra import db as db_mod
from app.infra.db import get_db
from app.infra.redis_client import get_redis
from app.security.hardening import rate_limit
from app.security.jwt_keys import issue_access, issue_refresh, revoke, verify
from app.security.passwords import hash_password, verify_password
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

log = logging.getLogger("api.auth")
router = APIRouter(prefix="/v1/auth", tags=["auth"])
_bearer = HTTPBearer(auto_error=False)

LOCKOUT_THRESHOLD = 5
LOCKOUT_SECONDS = 900


async def rl_login(request: Request):
    await rate_limit(request, rule=settings.RATE_LIMIT_AUTH, key_suffix="login")


async def rl_register(request: Request):
    await rate_limit(request, rule="5/minute", key_suffix="register")


async def rl_refresh(request: Request):
    await rate_limit(request, rule="30/minute", key_suffix="refresh")


class RegisterIn(BaseModel):
    email: EmailStr
    password: str
    tenant_name: str | None = None
    display_name: str = ""

    @field_validator("password")
    @classmethod
    def strong(cls, v: str) -> str:
        if len(v) < 10 or v.isalnum() or not any(c.isupper() for c in v) \
                or not any(c.isdigit() for c in v):
            raise ValueError("password must be >=10 chars with upper/digit/symbol")
        return v

    @field_validator("tenant_name")
    @classmethod
    def name_ok(cls, v: str | None) -> str | None:
        if v is None:
            return v
        v = v.strip()
        if not (2 <= len(v) <= 64):
            raise ValueError("tenant_name length 2~64")
        return v


class LoginIn(BaseModel):
    email: EmailStr
    password: str
    tenant_id: str | None = None


class RefreshIn(BaseModel):
    refresh_token: str


class CreateTenantReq(BaseModel):
    name: str
    slug: str


def _slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:48]
    return s or f"org-{uuid.uuid4().hex[:8]}"


@router.post("/register", status_code=201, dependencies=[Depends(rl_register)])
async def register(body: RegisterIn):
    tenant_name = body.tenant_name or body.email.split("@")[0]
    async with db_mod.session_factory() as db:
        if await db.scalar(select(User).where(User.email == body.email)):
            raise HTTPException(409, "email already registered")
        user = User(email=body.email, password_hash=hash_password(body.password),
                    display_name=body.display_name)
        slug = _slugify(tenant_name)
        tenant = Tenant(name=tenant_name, slug=slug, plan="free")
        db.add_all([user, tenant])
        await db.flush()
        db.add(TenantMember(tenant_id=tenant.id, user_id=user.id, role="owner"))
        db.add(TenantPolicy(tenant_id=tenant.id,
                            approval_required_tools=["deploy_to_production"]))
        await db.commit()
        log.info("tenant registered tenant=%s", tenant.id)
        return {"user_id": str(user.id), "tenant_id": str(tenant.id)}


@router.post("/login", dependencies=[Depends(rl_login)])
async def login(body: LoginIn, request: Request):
    r = await get_redis()
    lock_key = f"auth:lockout:{body.email}"

    fails = int(await r.get(lock_key) or 0)
    if fails >= LOCKOUT_THRESHOLD:
        raise HTTPException(429, "account temporarily locked, try later",
                            headers={"Retry-After": str(LOCKOUT_SECONDS)})

    async with db_mod.session_factory() as db:
        user = await db.scalar(select(User).where(User.email == body.email))
        if not user or not verify_password(body.password, user.password_hash):
            pipe = r.pipeline()
            pipe.incr(lock_key)
            pipe.expire(lock_key, LOCKOUT_SECONDS)
            await pipe.execute()
            log.warning("login failed email=%s ip=%s", body.email,
                        request.client.host if request.client else "?")
            raise HTTPException(401, "invalid credentials")

        members = (await db.execute(select(TenantMember, Tenant)
                    .join(Tenant, Tenant.id == TenantMember.tenant_id)
                    .where(TenantMember.user_id == user.id))).all()
        if not members:
            raise HTTPException(403, "no active tenant membership")
        if body.tenant_id:
            picked = next((m for m in members if str(m[1].id) == body.tenant_id), None)
            if not picked:
                raise HTTPException(403, "not a member of tenant")
            member, tenant = picked[0], picked[1]
        else:
            member, tenant = members[0][0], members[0][1]

    await r.delete(lock_key)
    access = issue_access(str(user.id), str(member.tenant_id), member.role)
    refresh = issue_refresh(str(user.id), str(member.tenant_id))
    return {
        "access_token": access,
        "refresh_token": refresh,
        "token": access,
        "token_type": "bearer",
        "expires_in": settings.JWT_ACCESS_TTL_SECONDS,
        "tenants": [{"id": t[1].id, "name": t[1].name, "role": t[0].role} for t in members],
    }


@router.post("/refresh", dependencies=[Depends(rl_refresh)])
async def refresh(body: RefreshIn):
    try:
        claims = verify(body.refresh_token)
    except pyjwt.InvalidTokenError:
        raise HTTPException(401, "invalid refresh token")
    if claims.get("typ") != "refresh":
        raise HTTPException(401, "not a refresh token")

    r = await get_redis()
    if await r.exists(f"jwt:revoked:{claims['jti']}"):
        raise HTTPException(401, "refresh token revoked")

    await revoke(claims["jti"], claims["exp"])

    async with db_mod.session_factory() as db:
        member = await db.scalar(select(TenantMember).where(
            TenantMember.user_id == claims["sub"],
            TenantMember.tenant_id == claims["tid"]))
        if not member:
            raise HTTPException(403, "membership no longer active")

    return {
        "access_token": issue_access(claims["sub"], claims["tid"], member.role),
        "refresh_token": issue_refresh(claims["sub"], claims["tid"]),
        "token_type": "bearer",
        "expires_in": settings.JWT_ACCESS_TTL_SECONDS,
    }


@router.post("/logout", status_code=204)
async def logout(cred: HTTPAuthorizationCredentials = Depends(_bearer)):
    if cred:
        try:
            claims = verify(cred.credentials)
            await revoke(claims["jti"], claims["exp"])
        except Exception:
            pass


@router.post("/tenants")
async def create_tenant(req: CreateTenantReq, auth=Depends(get_auth)):
    async with db_mod.session_factory() as db:
        tenant = Tenant(name=req.name, slug=req.slug)
        db.add(tenant)
        await db.flush()
        db.add(TenantMember(
            tenant_id=tenant.id, user_id=auth.user_id, role="owner"))
        db.add(TenantPolicy(
            tenant_id=tenant.id,
            approval_required_tools=["deploy_to_production"]))
        await db.commit()
        return {"tenant_id": tenant.id}
