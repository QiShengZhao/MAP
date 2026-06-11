from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from app.infra.db import get_db
from app.api.deps import get_auth, require_admin, check_workspace
from app.domain.models import Workspace, WorkspaceMember, TenantMember
from app.platform_services.seats import SeatService, SeatLimitExceeded

router = APIRouter(prefix="/v1/workspaces", tags=["workspaces"])

class CreateWorkspaceReq(BaseModel):
    name: str

@router.post("")
async def create_workspace(req: CreateWorkspaceReq, auth=Depends(get_auth),
                           db=Depends(get_db)):
    ws = Workspace(tenant_id=auth.tenant_id, name=req.name)
    db.add(ws)
    await db.flush()
    db.add(WorkspaceMember(tenant_id=auth.tenant_id, workspace_id=ws.id,
                           user_id=auth.user_id, role="owner"))
    await db.commit()
    return {"workspace_id": ws.id}

@router.get("")
async def list_workspaces(auth=Depends(get_auth), db=Depends(get_db)):
    rows = (await db.execute(select(Workspace)
        .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
        .where(Workspace.tenant_id == auth.tenant_id,
               WorkspaceMember.user_id == auth.user_id))).scalars().all()
    return [{"id": w.id, "name": w.name} for w in rows]

class AddMemberReq(BaseModel):
    user_id: str
    role: str = "member"

@router.post("/{workspace_id}/members")
async def add_member(workspace_id: str, req: AddMemberReq,
                     auth=Depends(require_admin), db=Depends(get_db)):
    await check_workspace(workspace_id, auth, db)
    tm = (await db.execute(select(TenantMember).where(
        TenantMember.tenant_id == auth.tenant_id,
        TenantMember.user_id == req.user_id))).scalar_one_or_none()
    if not tm:
        raise HTTPException(400, "user not in tenant")
    try:
        await SeatService.check_can_add_member(db, auth.tenant_id)
    except SeatLimitExceeded as e:
        raise HTTPException(402, str(e))
    db.add(WorkspaceMember(tenant_id=auth.tenant_id, workspace_id=workspace_id,
                           user_id=req.user_id, role=req.role))
    await db.commit()
    try:
        await SeatService.sync_seats(db, auth.tenant_id)
        await db.commit()
    except SeatLimitExceeded:
        pass
    return {"ok": True}