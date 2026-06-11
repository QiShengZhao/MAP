import jwt
from dataclasses import dataclass
from datetime import datetime, timedelta
from fastapi import Depends, Header, HTTPException, Query
from passlib.context import CryptContext
from sqlalchemy import select
from app.config import settings
from app.infra.db import get_db
from app.domain.models import TenantMember, Workspace, WorkspaceMember

pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")

def hash_password(p): return pwd_ctx.hash(p)
def verify_password(p, h): return pwd_ctx.verify(p, h)

def create_token(user_id: str) -> str:
    return jwt.encode(
        {"sub": user_id, "exp": datetime.utcnow() +
         timedelta(minutes=settings.JWT_EXPIRE_MINUTES)},
        settings.JWT_SECRET, algorithm="HS256")

def decode_token(token: str) -> str:
    try:
        return jwt.decode(token, settings.JWT_SECRET, algorithms=["HS256"])["sub"]
    except jwt.PyJWTError:
        raise HTTPException(401, "invalid or expired token")

@dataclass
class AuthContext:
    user_id: str
    tenant_id: str
    tenant_role: str
    @property
    def is_admin(self): return self.tenant_role in ("owner", "admin")

async def get_current_user(authorization: str = Header(...)) -> str:
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "missing bearer token")
    return decode_token(authorization[7:])

async def get_auth(user_id: str = Depends(get_current_user),
                   x_tenant_id: str = Header(...),
                   db=Depends(get_db)) -> AuthContext:
    member = (await db.execute(select(TenantMember).where(
        TenantMember.tenant_id == x_tenant_id,
        TenantMember.user_id == user_id))).scalar_one_or_none()
    if not member:
        raise HTTPException(403, "not a member of this tenant")
    return AuthContext(user_id, x_tenant_id, member.role)

async def get_auth_sse(token: str = Query(...), tenant_id: str = Query(...),
                       db=Depends(get_db)) -> AuthContext:
    user_id = decode_token(token)
    member = (await db.execute(select(TenantMember).where(
        TenantMember.tenant_id == tenant_id,
        TenantMember.user_id == user_id))).scalar_one_or_none()
    if not member:
        raise HTTPException(403, "not a member of this tenant")
    return AuthContext(user_id, tenant_id, member.role)

async def check_workspace(workspace_id, auth, db) -> Workspace:
    ws = (await db.execute(select(Workspace).where(
        Workspace.id == workspace_id,
        Workspace.tenant_id == auth.tenant_id))).scalar_one_or_none()
    if not ws:
        raise HTTPException(404, "workspace not found")
    if auth.is_admin:
        return ws
    wm = (await db.execute(select(WorkspaceMember).where(
        WorkspaceMember.workspace_id == workspace_id,
        WorkspaceMember.user_id == auth.user_id))).scalar_one_or_none()
    if not wm:
        raise HTTPException(403, "no workspace access")
    return ws

def require_admin(auth: AuthContext = Depends(get_auth)) -> AuthContext:
    if not auth.is_admin:
        raise HTTPException(403, "admin role required")
    return auth