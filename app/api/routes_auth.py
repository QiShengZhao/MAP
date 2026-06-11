from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from app.infra.db import get_db
from app.domain.models import User, Tenant, TenantMember, TenantPolicy
from app.api.deps import hash_password, verify_password, create_token, get_current_user

router = APIRouter(prefix="/v1/auth", tags=["auth"])

class RegisterReq(BaseModel):
    email: EmailStr
    password: str
    display_name: str = ""

@router.post("/register")
async def register(req: RegisterReq, db=Depends(get_db)):
    if (await db.execute(select(User).where(
            User.email == req.email))).scalar_one_or_none():
        raise HTTPException(409, "email already registered")
    user = User(email=req.email, password_hash=hash_password(req.password),
                display_name=req.display_name)
    db.add(user)
    await db.commit()
    return {"user_id": user.id, "token": create_token(user.id)}

class LoginReq(BaseModel):
    email: EmailStr
    password: str

@router.post("/login")
async def login(req: LoginReq, db=Depends(get_db)):
    user = (await db.execute(select(User).where(
        User.email == req.email))).scalar_one_or_none()
    if not user or not verify_password(req.password, user.password_hash):
        raise HTTPException(401, "invalid credentials")
    tenants = (await db.execute(select(TenantMember, Tenant)
        .join(Tenant, Tenant.id == TenantMember.tenant_id)
        .where(TenantMember.user_id == user.id))).all()
    return {"token": create_token(user.id),
            "tenants": [{"id": t.Tenant.id, "name": t.Tenant.name,
                         "role": t.TenantMember.role} for t in tenants]}

class CreateTenantReq(BaseModel):
    name: str
    slug: str

@router.post("/tenants")
async def create_tenant(req: CreateTenantReq,
                        user_id: str = Depends(get_current_user),
                        db=Depends(get_db)):
    tenant = Tenant(name=req.name, slug=req.slug)
    db.add(tenant)
    await db.flush()
    db.add(TenantMember(tenant_id=tenant.id, user_id=user_id, role="owner"))
    db.add(TenantPolicy(tenant_id=tenant.id,
                        approval_required_tools=["deploy_to_production"]))
    await db.commit()
    return {"tenant_id": tenant.id}