"""依赖链：①JWT(kid 轮换) → ②TenantMember 校验 → ③Workspace 校验（RLS 兜底）。"""
import logging
from dataclasses import dataclass

import jwt as pyjwt
from fastapi import Depends, HTTPException, Query, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.models import TenantMember, Workspace, WorkspaceMember
from app.infra.db import get_db, set_tenant_context, tenant_session
from app.infra.redis_client import get_redis
from app.security.jwt_keys import verify
from app.security.passwords import hash_password, verify_password

log = logging.getLogger("api.deps")
_bearer = HTTPBearer(auto_error=False)


@dataclass
class AuthContext:
    user_id: str
    tenant_id: str
    role: str
    jti: str = ""
    is_platform_admin: bool = False

    @property
    def tenant_role(self) -> str:
        return self.role

    @property
    def is_admin(self) -> bool:
        return self.role in ("owner", "admin")


async def get_auth(request: Request,
                   cred: HTTPAuthorizationCredentials = Depends(_bearer),
                   db: AsyncSession = Depends(get_db)) -> AuthContext:
    if cred is None:
        raise HTTPException(401, "missing bearer token",
                            headers={"WWW-Authenticate": "Bearer"})
    try:
        claims = verify(cred.credentials)
    except pyjwt.ExpiredSignatureError:
        raise HTTPException(401, "token expired")
    except pyjwt.InvalidTokenError as e:
        raise HTTPException(401, f"invalid token: {e}")

    if claims.get("typ") != "access":
        raise HTTPException(401, "wrong token type")

    await set_tenant_context(db, claims["tid"])
    r = await get_redis()
    if await r.exists(f"jwt:revoked:{claims['jti']}"):
        raise HTTPException(401, "token revoked")

    member = await db.scalar(select(TenantMember).where(
        TenantMember.tenant_id == claims["tid"],
        TenantMember.user_id == claims["sub"]))
    if not member:
        raise HTTPException(403, "not a member of this tenant")

    ctx = AuthContext(user_id=claims["sub"], tenant_id=claims["tid"],
                      role=member.role, jti=claims["jti"],
                      is_platform_admin=bool(claims.get("padm")))
    request.state.tenant_id = ctx.tenant_id
    request.state.user_id = ctx.user_id
    return ctx


async def get_auth_sse(token: str = Query(...), db: AsyncSession = Depends(get_db)) -> AuthContext:
    try:
        claims = verify(token)
    except pyjwt.InvalidTokenError:
        raise HTTPException(401, "invalid token")
    if claims.get("typ") != "access":
        raise HTTPException(401, "wrong token type")
    await set_tenant_context(db, claims["tid"])
    r = await get_redis()
    if await r.exists(f"jwt:revoked:{claims['jti']}"):
        raise HTTPException(401, "token revoked")
    member = await db.scalar(select(TenantMember).where(
        TenantMember.tenant_id == claims["tid"],
        TenantMember.user_id == claims["sub"]))
    if not member:
        raise HTTPException(403, "not a member of this tenant")
    return AuthContext(user_id=claims["sub"], tenant_id=claims["tid"],
                      role=member.role, jti=claims["jti"],
                      is_platform_admin=bool(claims.get("padm")))


async def get_tenant_db(auth: AuthContext = Depends(get_auth)):
    async with tenant_session(auth.tenant_id) as db:
        yield db


def require_role(*roles: str):
    async def _check(auth: AuthContext = Depends(get_auth)) -> AuthContext:
        if auth.role not in roles:
            raise HTTPException(403, f"requires role: {roles}")
        return auth
    return _check


def require_admin(auth: AuthContext = Depends(get_auth)) -> AuthContext:
    if not auth.is_admin:
        raise HTTPException(403, "admin role required")
    return auth


async def check_workspace(workspace_id: str, auth: AuthContext,
                          db: AsyncSession) -> Workspace:
    ws = await db.scalar(select(Workspace).where(
        Workspace.id == workspace_id,
        Workspace.tenant_id == auth.tenant_id))
    if not ws:
        raise HTTPException(404, "workspace not found")
    if auth.role != "owner" and not auth.is_admin:
        wm = await db.scalar(select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.user_id == auth.user_id))
        if not wm:
            raise HTTPException(403, "no access to this workspace")
    return ws


# 兼容旧代码
def create_token(user_id: str) -> str:
    from app.security.jwt_keys import issue_access
    return issue_access(user_id, "", "")


def decode_token(token: str) -> str:
    return verify(token)["sub"]
