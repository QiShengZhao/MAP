"""Kafka → PG 审计落库：批量 + 幂等(event_id 唯一) + 先落库后提交位点。"""
import asyncio
import json
import logging
from datetime import datetime, timezone

from aiokafka import AIOKafkaConsumer
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.config import settings
from app.domain.models import RunEvent
from app.eventbus.bus import decode_value
from app.eventbus.dlq import send_to_dlq
from app.eventbus.kafka_client import _security_kwargs
from app.infra.db import SessionLocal

log = logging.getLogger("eventbus.audit")
GROUP = "audit-writer"
BATCH_SIZE = 500
FLUSH_INTERVAL = 2.0


async def run_audit_writer() -> None:
    consumer = AIOKafkaConsumer(
        settings.KAFKA_TOPIC_RUN_EVENTS,
        bootstrap_servers=settings.KAFKA_BOOTSTRAP,
        group_id=GROUP,
        enable_auto_commit=False,
        auto_offset_reset="earliest",
        max_poll_records=BATCH_SIZE,
        **_security_kwargs(),
    )
    await consumer.start()
    log.info("audit writer started")
    buffer: list[dict] = []
    last_flush = asyncio.get_event_loop().time()
    try:
        while True:
            batch = await consumer.getmany(timeout_ms=500, max_records=BATCH_SIZE)
            for _tp, msgs in batch.items():
                for m in msgs:
                    try:
                        ev = await decode_value(m.value, m.headers)
                        buffer.append(ev)
                    except Exception as e:
                        await send_to_dlq(m, e, GROUP)

            now = asyncio.get_event_loop().time()
            if buffer and (len(buffer) >= BATCH_SIZE or now - last_flush > FLUSH_INTERVAL):
                await _flush(buffer)
                await consumer.commit()
                buffer.clear()
                last_flush = now
    finally:
        if buffer:
            await _flush(buffer)
            await consumer.commit()
        await consumer.stop()


async def _flush(events: list[dict]) -> None:
    rows = []
    for ev in events:
        payload = ev.get("payload")
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError:
                payload = {"raw": payload}
        if ev.get("agent_name") or ev.get("cost_usd") or ev.get("trace_id"):
            payload = {**payload, "_meta": {
                k: ev.get(k) for k in ("agent_name", "cost_usd", "trace_id", "workspace_id")
                if ev.get(k) is not None}}
        rows.append({
            "id": ev["event_id"],
            "tenant_id": ev["tenant_id"],
            "run_id": ev["run_id"],
            "seq": ev["seq"],
            "type": ev["event_type"],
            "payload": payload,
            "created_at": datetime.fromtimestamp(ev["ts_ms"] / 1000, tz=timezone.utc),
        })

    async with SessionLocal() as db:
        stmt = pg_insert(RunEvent).values(rows).on_conflict_do_nothing(index_elements=["id"])
        await db.execute(stmt)
        await db.commit()
    log.info("flushed %s audit events", len(rows))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_audit_writer())
