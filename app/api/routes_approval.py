from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from app.infra.db import get_db
from app.api.deps import get_auth, require_admin
from app.domain.models import ApprovalRequest, ApprovalStatus
from app.runtime.approval import ApprovalService

router = APIRouter(prefix="/v1/approvals", tags=["approvals"])

@router.get("")
async def list_pending(auth=Depends(get_auth), db=Depends(get_db)):
    rows = (await db.execute(select(ApprovalRequest).where(
        ApprovalRequest.tenant_id == auth.tenant_id,
        ApprovalRequest.status == ApprovalStatus.pending)
        .order_by(ApprovalRequest.created_at))).scalars().all()
    return [{"id": a.id, "run_id": a.run_id, "tool": a.tool_name,
             "args": a.tool_args, "requested_by": a.requested_by,
             "created_at": a.created_at.isoformat()} for a in rows]

class DecideReq(BaseModel):
    approved: bool
    reason: str = ""

@router.post("/{approval_id}/decide")
async def decide(approval_id: str, req: DecideReq,
                 auth=Depends(require_admin), db=Depends(get_db)):
    a = await db.get(ApprovalRequest, approval_id)
    if not a or a.tenant_id != auth.tenant_id:
        raise HTTPException(404, "approval not found")
    if a.status != ApprovalStatus.pending:
        raise HTTPException(409, f"already {a.status}")
    a.status = ApprovalStatus.approved if req.approved else ApprovalStatus.rejected
    a.decided_by, a.reason, a.decided_at = auth.user_id, req.reason, datetime.utcnow()
    await db.commit()
    await ApprovalService.notify(auth.tenant_id, a.run_id, a.tool_call_id,
                                 req.approved, auth.user_id)
    return {"ok": True, "status": a.status}