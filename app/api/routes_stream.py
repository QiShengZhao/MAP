import json
from fastapi import APIRouter, Depends, Header, HTTPException
from sse_starlette.sse import EventSourceResponse
from sqlalchemy import select
from app.infra.db import SessionLocal
from app.infra.redis_client import redis_client
from app.api.deps import get_auth_sse
from app.domain.models import Run, RunEvent, RunStatus

router = APIRouter(prefix="/v1", tags=["stream"])
TERMINAL = ("run.completed", "run.failed", "run.cancelled")

@router.get("/runs/{run_id}/stream")
async def stream_run(run_id: str, auth=Depends(get_auth_sse),
                     last_event_id: str | None = Header(None)):
    async with SessionLocal() as db:
        run = await db.get(Run, run_id)
        if not run or run.tenant_id != auth.tenant_id:
            raise HTTPException(404, "run not found")
    channel = f"tenant:{auth.tenant_id}:run:{run_id}:events"
    start_seq = int(last_event_id) if (last_event_id or "").isdigit() else 0

    async def gen():
        last_seq = start_seq
        # 历史回放（断点续传）
        async with SessionLocal() as db:
            rows = (await db.execute(select(RunEvent)
                .where(RunEvent.run_id == run_id, RunEvent.seq > last_seq)
                .order_by(RunEvent.seq))).scalars().all()
            for e in rows:
                last_seq = e.seq
                yield {"id": str(e.seq), "event": e.type,
                       "data": json.dumps(e.payload, ensure_ascii=False)}
                if e.type in TERMINAL:
                    return
            run2 = await db.get(Run, run_id)
            if run2.status in (RunStatus.completed, RunStatus.failed,
                               RunStatus.cancelled):
                return
        # 实时订阅
        pubsub = redis_client.pubsub()
        await pubsub.subscribe(channel)
        try:
            while True:
                m = await pubsub.get_message(ignore_subscribe_messages=True,
                                             timeout=30)
                if m is None:
                    yield {"event": "ping", "data": "{}"}
                    continue
                event = json.loads(m["data"])
                if event["seq"] <= last_seq:
                    continue
                last_seq = event["seq"]
                yield {"id": str(event["seq"]), "event": event["type"],
                       "data": json.dumps(event["payload"], ensure_ascii=False)}
                if event["type"] in TERMINAL:
                    return
        finally:
            await pubsub.unsubscribe(channel)
            await pubsub.close()
    return EventSourceResponse(gen())