from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.api.deps import check_workspace, get_auth
from app.infra.db import get_db
from app.memory.service import MemoryService

router = APIRouter(prefix="/v1/memories", tags=["memories"])


class MemoryReq(BaseModel):
    workspace_id: str | None = None
    session_id: str | None = None
    run_id: str | None = None
    scope: str = "workspace"
    kind: str = "note"
    title: str = Field(default="", max_length=255)
    content: str = Field(min_length=1)
    confidence: float = 0.6
    pinned: bool = False
    expires_at: datetime | None = None


class MemoryPatchReq(BaseModel):
    workspace_id: str | None = None
    session_id: str | None = None
    run_id: str | None = None
    title: str | None = None
    content: str | None = None
    confidence: float | None = None
    pinned: bool | None = None
    expires_at: datetime | None = None


def _out(item):
    return {
        "id": item.id,
        "scope": item.scope,
        "kind": item.kind,
        "title": item.title,
        "content": item.content,
        "workspace_id": item.workspace_id,
        "session_id": item.session_id,
        "run_id": item.run_id,
        "user_id": item.user_id,
        "confidence": item.confidence,
        "pinned": item.pinned,
        "source_type": item.source_type,
        "source_id": item.source_id,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }


@router.get("")
async def list_memories(
    workspace_id: str | None = None,
    session_id: str | None = None,
    run_id: str | None = None,
    query: str = "",
    scope: str | None = None,
    limit: int = Query(8, ge=1, le=20),
    auth=Depends(get_auth),
    db=Depends(get_db),
):
    if workspace_id:
        await check_workspace(workspace_id, auth, db)
    rows = await MemoryService.search(
        db,
        tenant_id=auth.tenant_id,
        workspace_id=workspace_id,
        session_id=session_id,
        run_id=run_id,
        user_id=auth.user_id,
        query=query,
        scope=scope,
        limit=limit,
    )
    return [_out(row) for row in rows]


@router.post("", status_code=201)
async def create_memory(req: MemoryReq, auth=Depends(get_auth), db=Depends(get_db)):
    if req.workspace_id:
        await check_workspace(req.workspace_id, auth, db)
    item = await MemoryService.write(
        db,
        tenant_id=auth.tenant_id,
        workspace_id=req.workspace_id,
        session_id=req.session_id,
        run_id=req.run_id,
        user_id=auth.user_id,
        scope=req.scope,
        kind=req.kind,
        title=req.title,
        content=req.content,
        source_type="user",
        source_id=auth.user_id,
        confidence=req.confidence,
        pinned=req.pinned,
        expires_at=req.expires_at,
    )
    await db.commit()
    return _out(item)


@router.patch("/{memory_id}")
async def update_memory(memory_id: str, req: MemoryPatchReq,
                        auth=Depends(get_auth), db=Depends(get_db)):
    if req.workspace_id:
        await check_workspace(req.workspace_id, auth, db)
    item = await MemoryService.get_visible(
        db,
        tenant_id=auth.tenant_id,
        memory_id=memory_id,
        workspace_id=req.workspace_id,
        session_id=req.session_id,
        run_id=req.run_id,
        user_id=auth.user_id,
    )
    if not item:
        raise HTTPException(404, "memory not found")
    await MemoryService.update(
        db, item, title=req.title, content=req.content,
        confidence=req.confidence, pinned=req.pinned,
        expires_at=req.expires_at)
    await db.commit()
    return _out(item)


@router.delete("/{memory_id}")
async def delete_memory(memory_id: str, workspace_id: str | None = None,
                        session_id: str | None = None,
                        run_id: str | None = None,
                        auth=Depends(get_auth), db=Depends(get_db)):
    if workspace_id:
        await check_workspace(workspace_id, auth, db)
    ok = await MemoryService.forget(
        db,
        tenant_id=auth.tenant_id,
        memory_id=memory_id,
        workspace_id=workspace_id,
        session_id=session_id,
        run_id=run_id,
        user_id=auth.user_id,
    )
    if not ok:
        raise HTTPException(404, "memory not found")
    await db.commit()
    return {"ok": True}
