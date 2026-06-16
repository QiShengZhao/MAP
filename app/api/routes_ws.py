import asyncio, json
from datetime import datetime
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from app.infra.db import SessionLocal, set_tenant_context
from app.infra.redis_client import redis_client
from app.api.deps import AuthContext
from app.security.jwt_keys import verify
from app.domain.models import (TenantMember, Workspace, WorkspaceMember, Session,
                               Message, Run, ApprovalRequest, ApprovalStatus)
from app.scheduling.queue import RunQueue
from app.runtime.approval import ApprovalService

router = APIRouter()


async def authenticate_websocket(token, requested_tenant_id, db):
    claims = verify(token)
    if claims.get("typ") != "access":
        raise ValueError("websocket requires an access token")
    if claims.get("tid") != requested_tenant_id:
        raise ValueError("tenant mismatch")
    if await redis_client.exists(f"jwt:revoked:{claims['jti']}"):
        raise ValueError("token revoked")
    await set_tenant_context(db, claims["tid"])
    member = (await db.execute(select(TenantMember).where(
        TenantMember.tenant_id == claims["tid"],
        TenantMember.user_id == claims["sub"]))).scalar_one_or_none()
    if not member:
        raise ValueError("tenant membership inactive")
    return AuthContext(
        user_id=claims["sub"], tenant_id=claims["tid"],
        role=member.role, jti=claims["jti"],
        is_platform_admin=bool(claims.get("padm")),
    )


async def validate_workspace_access(workspace_id, auth, db):
    workspace = await db.scalar(select(Workspace).where(
        Workspace.id == workspace_id,
        Workspace.tenant_id == auth.tenant_id))
    if not workspace:
        raise ValueError("workspace not found")
    if not auth.is_admin:
        membership = await db.scalar(select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.user_id == auth.user_id))
        if not membership:
            raise ValueError("workspace access denied")
    return workspace


@router.websocket("/v1/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    try:
        frame = json.loads(await asyncio.wait_for(ws.receive_text(), timeout=10))
        assert frame.get("type") == "auth"
        tenant_id = frame["tenant_id"]
        async with SessionLocal() as db:
            auth = await authenticate_websocket(frame["token"], tenant_id, db)
        user_id = auth.user_id
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
                    await set_tenant_context(db, tenant_id)
                    sid = frame.get("session_id")
                    if sid:
                        s = await db.get(Session, sid)
                        if not s or s.tenant_id != tenant_id:
                            await ws.send_json({"type": "error",
                                                "error": "session not found"})
                            continue
                    else:
                        workspace_id = frame["workspace_id"]
                        try:
                            await validate_workspace_access(
                                workspace_id, auth, db)
                        except ValueError as exc:
                            await ws.send_json(
                                {"type": "error", "error": str(exc)})
                            continue
                        s = Session(tenant_id=tenant_id,
                                    workspace_id=workspace_id,
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
            elif t == "approval.decide" and auth.is_admin:
                async with SessionLocal() as db:
                    await set_tenant_context(db, tenant_id)
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
