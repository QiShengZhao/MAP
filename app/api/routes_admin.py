from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, func
from app.infra.db import get_db
from app.api.deps import require_admin
from app.domain.models import (TenantMember, User, Run, RunStatus, RunEvent,
                               UsageRecord)

router = APIRouter(prefix="/v1/admin", tags=["admin"])

@router.get("/members")
async def list_members(auth=Depends(require_admin), db=Depends(get_db)):
    rows = (await db.execute(select(TenantMember, User)
        .join(User, User.id == TenantMember.user_id)
        .where(TenantMember.tenant_id == auth.tenant_id))).all()
    return [{"user_id": r.User.id, "email": r.User.email,
             "role": r.TenantMember.role} for r in rows]

class RoleReq(BaseModel):
    role: str

@router.put("/members/{user_id}/role")
async def set_role(user_id: str, req: RoleReq, auth=Depends(require_admin),
                   db=Depends(get_db)):
    m = (await db.execute(select(TenantMember).where(
        TenantMember.tenant_id == auth.tenant_id,
        TenantMember.user_id == user_id))).scalar_one_or_none()
    if not m:
        raise HTTPException(404)
    if m.role == "owner":
        raise HTTPException(400, "cannot change owner role")
    m.role = req.role
    await db.commit()
    return {"ok": True}

@router.get("/runs")
async def list_runs(status: str | None = None, limit: int = 50,
                    auth=Depends(require_admin), db=Depends(get_db)):
    q = select(Run).where(Run.tenant_id == auth.tenant_id)
    if status:
        q = q.where(Run.status == RunStatus(status))
    rows = (await db.execute(
        q.order_by(Run.created_at.desc()).limit(limit))).scalars().all()
    return [{"id": r.id, "status": r.status, "usage": r.usage,
             "error": r.error, "created_at": r.created_at.isoformat()}
            for r in rows]

@router.get("/runs/{run_id}/audit")
async def audit_run(run_id: str, auth=Depends(require_admin), db=Depends(get_db)):
    run = await db.get(Run, run_id)
    if not run or run.tenant_id != auth.tenant_id:
        raise HTTPException(404)
    events = (await db.execute(select(RunEvent)
        .where(RunEvent.run_id == run_id).order_by(RunEvent.seq))).scalars().all()
    return {"run": {"id": run.id, "status": run.status,
                    "trace_id": run.trace_id, "usage": run.usage},
            "events": [{"seq": e.seq, "type": e.type, "payload": e.payload,
                        "ts": e.created_at.isoformat()} for e in events]}

@router.get("/model-routing")
async def model_routing(model: str = "gpt-4o", auth=Depends(require_admin)):
    from app.runtime.model_router import ModelRouter
    return await ModelRouter.get().routing_table(model)