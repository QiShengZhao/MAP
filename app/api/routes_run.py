from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, func
from app.infra.db import get_db
from app.infra.redis_client import redis_client
from app.api.deps import get_auth, check_workspace
from app.domain.models import Session, Message, Run, RunStatus, RunEvent
from app.scheduling.queue import RunQueue
from app.platform_services.policy import PolicyService

router = APIRouter(prefix="/v1", tags=["runs"])

class CreateMessageReq(BaseModel):
    workspace_id: str
    session_id: str | None = None
    content: str
    agent_config: dict = {}

@router.post("/messages")
async def create_message(req: CreateMessageReq, auth=Depends(get_auth),
                         db=Depends(get_db)):
    await check_workspace(req.workspace_id, auth, db)
    # 风控暂停检查
    if await redis_client.get(f"risk:paused:{auth.tenant_id}"):
        raise HTTPException(423, "tenant temporarily paused by risk control")
    # 并发配额
    policy = await PolicyService.get(db, auth.tenant_id)
    active = (await db.execute(select(func.count()).select_from(Run).where(
        Run.tenant_id == auth.tenant_id,
        Run.status.in_([RunStatus.queued, RunStatus.running,
                        RunStatus.awaiting_approval])))).scalar()
    if active >= policy.max_concurrent_runs:
        raise HTTPException(429, "concurrent run quota exceeded")

    if req.session_id:
        session = await db.get(Session, req.session_id)
        if not session or session.tenant_id != auth.tenant_id:
            raise HTTPException(404, "session not found")
    else:
        session = Session(tenant_id=auth.tenant_id, workspace_id=req.workspace_id,
                          user_id=auth.user_id, title=req.content[:60])
        db.add(session)
        await db.flush()

    msg = Message(tenant_id=auth.tenant_id, session_id=session.id,
                  role="user", content={"text": req.content})
    run = Run(tenant_id=auth.tenant_id, session_id=session.id,
              user_id=auth.user_id, agent_config=req.agent_config)
    db.add_all([msg, run])
    await db.commit()
    await RunQueue.enqueue(auth.tenant_id, run.id, req.workspace_id)
    return {"session_id": session.id, "message_id": msg.id, "run_id": run.id}

@router.get("/runs/{run_id}")
async def get_run(run_id: str, auth=Depends(get_auth), db=Depends(get_db)):
    run = await db.get(Run, run_id)
    if not run or run.tenant_id != auth.tenant_id:
        raise HTTPException(404, "run not found")
    return {"id": run.id, "status": run.status, "usage": run.usage,
            "error": run.error, "created_at": run.created_at}

@router.post("/runs/{run_id}/cancel")
async def cancel_run(run_id: str, auth=Depends(get_auth), db=Depends(get_db)):
    run = await db.get(Run, run_id)
    if not run or run.tenant_id != auth.tenant_id:
        raise HTTPException(404, "run not found")
    await redis_client.set(f"cancel:run:{run_id}", "1", ex=3600)
    return {"ok": True}

@router.get("/runs/{run_id}/events")
async def list_run_events(run_id: str, after_seq: int = 0,
                          auth=Depends(get_auth), db=Depends(get_db)):
    run = await db.get(Run, run_id)
    if not run or run.tenant_id != auth.tenant_id:
        raise HTTPException(404, "run not found")
    rows = (await db.execute(select(RunEvent)
        .where(RunEvent.run_id == run_id, RunEvent.seq > after_seq)
        .order_by(RunEvent.seq))).scalars().all()
    return [{"seq": e.seq, "type": e.type, "payload": e.payload,
             "ts": e.created_at.isoformat()} for e in rows]

@router.get("/sessions/{session_id}/messages")
async def list_messages(session_id: str, auth=Depends(get_auth),
                        db=Depends(get_db)):
    s = await db.get(Session, session_id)
    if not s or s.tenant_id != auth.tenant_id:
        raise HTTPException(404, "session not found")
    rows = (await db.execute(select(Message)
        .where(Message.session_id == session_id)
        .order_by(Message.created_at))).scalars().all()
    return [{"id": m.id, "role": m.role, "content": m.content} for m in rows]