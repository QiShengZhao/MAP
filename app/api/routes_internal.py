import json
from fastapi import APIRouter, Header, HTTPException, Depends
from pydantic import BaseModel
from sqlalchemy import select
from app.config import settings
from app.infra.db import get_db, set_tenant_context
from app.infra.redis_client import redis_client
from app.domain.models import Artifact, Run, RunStatus

router = APIRouter(prefix="/internal", tags=["internal"])

def verify_internal(x_internal_token: str = Header(...)):
    if x_internal_token != settings.INTERNAL_TOKEN:
        raise HTTPException(403, "forbidden")

class SidecarArtifactReq(BaseModel):
    tenant_id: str
    session_id: str
    name: str
    storage_key: str
    size: int
    mime: str

@router.post("/artifacts", dependencies=[Depends(verify_internal)])
async def sidecar_artifact(req: SidecarArtifactReq, db=Depends(get_db)):
    await set_tenant_context(db, req.tenant_id)
    run = (await db.execute(select(Run).where(
        Run.tenant_id == req.tenant_id,
        Run.session_id == req.session_id, Run.status == RunStatus.running)
        .order_by(Run.created_at.desc()).limit(1))).scalar_one_or_none()
    a = Artifact(tenant_id=req.tenant_id, run_id=run.id if run else "",
                 session_id=req.session_id, name=req.name,
                 storage_key=req.storage_key, mime_type=req.mime,
                 size_bytes=req.size)
    db.add(a)
    await db.commit()
    if run:
        await redis_client.publish(
            f"tenant:{req.tenant_id}:run:{run.id}:events",
            json.dumps({"seq": 0, "type": "artifact.created",
                        "payload": {"artifact_id": a.id, "name": a.name}}))
    return {"ok": True, "artifact_id": a.id}
