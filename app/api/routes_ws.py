import asyncio, json
from datetime import datetime
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from app.infra.db import SessionLocal
from app.infra.redis_client import redis_client
from app.api.deps import decode_token
from app.domain.models import (TenantMember, Session, Message, Run,
                               ApprovalRequest, ApprovalStatus)
from app.scheduling.queue import RunQueue
from app.runtime.approval import ApprovalService

router = APIRouter()

@router.websocket("/v1/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    try:
        frame = json.loads(await asyncio.wait_for(ws.receive_text(), timeout=10))
        assert frame.get("type") == "auth"
        user_id = decode_token(frame["token"])
        tenant_id = frame["tenant_id"]
        async with SessionLocal() as db:
            member = (await db.execute(select(TenantMember).where(
                TenantMember.tenant_id == tenant_id,
                TenantMember.user_id == user_id))).scalar_one_or_none()
        if not member:
            await ws.close(code=4403); return
    except Exception:
        await ws.close(code=4401); return
    await ws.send_json({"type": "auth.ok"})

    subscriptions = {}

    async def forward_events(run_id):
        channel = f"tenant:{tenant_id}:run:{run_id}:events"
        pubsub = redis_client.pubsub()
        await pubsub.subscribe(channel)
        try:
            while True:
                m = await pubsub.get_message(ignore_subscribe_messages=True,
                                             timeout=30)
                if m is None: continue
                event = json.loads(m["data"])
                await ws.send_json({"type": "run.event", "run_id": run_id, **event})
                if event["type"] in ("run.completed", "run.failed", "run.cancelled"):
                    return
        finally:
            await pubsub.unsubscribe(channel)
            await pubsub.close()

    try:
        while True:
            frame = json.loads(await ws.receive_text())
            t = frame.get("type")
            if t == "message.create":
                async with SessionLocal() as db:
                    sid = frame.get("session_id")
                    if sid:
                        s = await db.get(Session, sid)
                        if not s or s.tenant_id != tenant_id:
                            await ws.send_json({"type": "error",
                                                "error": "session not found"})
                            continue
                    else:
                        s = Session(tenant_id=tenant_id,
                                    workspace_id=frame["workspace_id"],
                                    user_id=user_id, title=frame["content"][:60])
                        db.add(s); await db.flush()
                    msg = Message(tenant_id=tenant_id, session_id=s.id,
                                  role="user", content={"text": frame["content"]})
                    run = Run(tenant_id=tenant_id, session_id=s.id,
                              user_id=user_id,
                              agent_config=frame.get("agent_config", {}))
                    db.add_all([msg, run])
                    await db.commit()
                await RunQueue.enqueue(tenant_id, run.id,
                                       frame.get("workspace_id", ""))
                await ws.send_json({"type": "run.created", "run_id": run.id,
                                    "session_id": s.id})
                subscriptions[run.id] = asyncio.create_task(forward_events(run.id))
            elif t == "run.subscribe":
                rid = frame["run_id"]
                if rid not in subscriptions:
                    subscriptions[rid] = asyncio.create_task(forward_events(rid))
            elif t == "approval.decide" and member.role in ("owner", "admin"):
                async with SessionLocal() as db:
                    a = await db.get(ApprovalRequest, frame["approval_id"])
                    if a and a.tenant_id == tenant_id and \
                            a.status == ApprovalStatus.pending:
                        a.status = (ApprovalStatus.approved if frame["approved"]
                                    else ApprovalStatus.rejected)
                        a.decided_by, a.decided_at = user_id, datetime.utcnow()
                        await db.commit()
                        await ApprovalService.notify(
                            tenant_id, a.run_id, a.tool_call_id,
                            frame["approved"], user_id)
                        await ws.send_json({"type": "approval.ok",
                                            "approval_id": a.id})
            elif t == "ping":
                await ws.send_json({"type": "pong"})
    except WebSocketDisconnect:
        pass
    finally:
        for task in subscriptions.values():
            task.cancel()