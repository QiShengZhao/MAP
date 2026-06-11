from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from app.infra.db import get_db
from app.api.deps import get_auth, require_admin
from app.domain.models import SandboxSession, Session
from app.runtime.sandbox import SandboxManager, Sandbox

router = APIRouter(prefix="/v1/sandboxes", tags=["sandbox"])

@router.get("")
async def list_sandboxes(auth=Depends(get_auth), db=Depends(get_db)):
    rows = (await db.execute(select(SandboxSession).where(
        SandboxSession.tenant_id == auth.tenant_id))).scalars().all()
    return [{"id": s.id, "session_id": s.session_id, "pod": s.pod_name,
             "status": s.status, "created_at": s.created_at.isoformat()}
            for s in rows]

@router.delete("/{session_id}")
async def terminate_sandbox(session_id: str, auth=Depends(get_auth),
                            db=Depends(get_db)):
    s = await db.get(Session, session_id)
    if not s or s.tenant_id != auth.tenant_id:
        raise HTTPException(404)
    await SandboxManager.terminate(auth.tenant_id, session_id)
    return {"ok": True}

class ExecReq(BaseModel):
    command: str
    timeout: int = 60

@router.post("/{session_id}/exec")
async def debug_exec(session_id: str, req: ExecReq,
                     auth=Depends(require_admin), db=Depends(get_db)):
    row = (await db.execute(select(SandboxSession).where(
        SandboxSession.session_id == session_id,
        SandboxSession.tenant_id == auth.tenant_id,
        SandboxSession.status == "running"))).scalar_one_or_none()
    if not row:
        raise HTTPException(404, "sandbox not running")
    sbx = Sandbox(row.namespace, row.pod_name)
    return {"output": await sbx.exec(req.command, timeout=req.timeout)}