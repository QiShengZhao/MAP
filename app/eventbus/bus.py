"""统一发布入口。灰度：EVENT_SERIALIZATION=avro|json；EVENT_BUS=kafka|redis。"""
import json
import logging
import time
import uuid
from typing import Any

from app.config import settings
from app.eventbus.kafka_client import KafkaProducerHolder
from app.eventbus.avro_serde import avro_encode
from app.infra.redis_client import get_redis

log = logging.getLogger("eventbus.bus")


def build_run_event(*, run_id: str, tenant_id: str, seq: int, event_type: str,
                    payload: dict[str, Any], workspace_id: str | None = None,
                    agent_name: str | None = None, cost_usd: float | None = None,
                    trace_id: str | None = None) -> dict:
    return {
        "event_id": str(uuid.uuid4()),
        "run_id": run_id, "tenant_id": tenant_id, "seq": seq,
        "event_type": event_type, "ts_ms": int(time.time() * 1000),
        "payload": json.dumps(payload, ensure_ascii=False, default=str),
        "workspace_id": workspace_id, "agent_name": agent_name,
        "cost_usd": cost_usd, "trace_id": trace_id,
    }


def _legacy_event_dict(event: dict) -> dict:
    """与现有 SSE/WS 通道兼容的格式。"""
    payload = event.get("payload")
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            payload = {"raw": payload}
    return {"seq": event["seq"], "type": event["event_type"], "payload": payload}


async def _publish_redis(event: dict) -> None:
    r = await get_redis()
    legacy = _legacy_event_dict(event)
    data = json.dumps(legacy, ensure_ascii=False)
    await r.xadd(f"run:{event['run_id']}:events",
                 {"data": json.dumps(event, ensure_ascii=False)},
                 maxlen=10000, approximate=True)
    channel = f"tenant:{event['tenant_id']}:run:{event['run_id']}:events"
    await r.publish(channel, data)
    await r.publish(f"run:{event['run_id']}:notify", str(event["seq"]))


async def publish_run_event(event: dict) -> None:
    if settings.EVENT_BUS == "redis":
        await _publish_redis(event)
        return

    producer = await KafkaProducerHolder.get()
    if settings.EVENT_SERIALIZATION == "avro":
        value = await avro_encode("run-events-value", event)
        content_type = b"avro"
    else:
        value = json.dumps(event, ensure_ascii=False).encode()
        content_type = b"json"
    await producer.send_and_wait(
        settings.KAFKA_TOPIC_RUN_EVENTS,
        value=value,
        key=event["tenant_id"].encode(),
        headers=[("content-type", content_type),
                 ("event-id", event["event_id"].encode())],
    )


async def decode_value(value: bytes, headers: list[tuple[str, bytes]] | None) -> dict:
    hdr = dict(headers or {})
    ct = hdr.get("content-type", b"json")
    if ct == b"avro":
        from app.eventbus.avro_serde import avro_decode
        return await avro_decode(value)
    return json.loads(value)
