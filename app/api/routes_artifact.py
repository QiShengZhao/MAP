from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from app.infra.db import get_db
from app.infra.object_storage import object_storage
from app.api.deps import get_auth
from app.domain.models import Artifact

router = APIRouter(prefix="/v1/artifacts", tags=["artifacts"])

@router.get("")
async def list_artifacts(run_id: str | None = None, session_id: str | None = None,
                         auth=Depends(get_auth), db=Depends(get_db)):
    q = select(Artifact).where(Artifact.tenant_id == auth.tenant_id)
    if run_id: q = q.where(Artifact.run_id == run_id)
    if session_id: q = q.where(Artifact.session_id == session_id)
    rows = (await db.execute(q.order_by(Artifact.created_at))).scalars().all()
    return [{"id": a.id, "name": a.name, "mime": a.mime_type,
             "size": a.size_bytes} for a in rows]

@router.get("/{artifact_id}/download")
async def download(artifact_id: str, auth=Depends(get_auth), db=Depends(get_db)):
    a = await db.get(Artifact, artifact_id)
    if not a or a.tenant_id != auth.tenant_id:
        raise HTTPException(404, "artifact not found")
    return {"url": await object_storage.presigned_url(a.storage_key),
            "expires_in": 3600}