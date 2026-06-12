"""Kafka → Redis 桥：供 SSE/WS 实时推送 + Last-Event-ID 断点续传。"""
import asyncio
import json
import logging

from aiokafka import AIOKafkaConsumer

from app.config import settings
from app.eventbus.bus import _legacy_event_dict, decode_value
from app.eventbus.dlq import send_to_dlq
from app.eventbus.kafka_client import _security_kwargs
from app.infra.redis_client import get_redis

log = logging.getLogger("eventbus.relay")
GROUP = "event-relay"


async def run_relay() -> None:
    consumer = AIOKafkaConsumer(
        settings.KAFKA_TOPIC_RUN_EVENTS,
        bootstrap_servers=settings.KAFKA_BOOTSTRAP,
        group_id=GROUP,
        enable_auto_commit=False,
        auto_offset_reset="latest",
        max_poll_records=200,
        **_security_kwargs(),
    )
    await consumer.start()
    r = await get_redis()
    log.info("relay consumer started")
    try:
        while True:
            batch = await consumer.getmany(timeout_ms=500)
            if not batch:
                continue
            pipe = r.pipeline(transaction=False)
            for _tp, msgs in batch.items():
                for m in msgs:
                    try:
                        ev = await decode_value(m.value, m.headers)
                        data = json.dumps(ev, ensure_ascii=False)
                        legacy = json.dumps(_legacy_event_dict(ev), ensure_ascii=False)
                        pipe.xadd(f"run:{ev['run_id']}:events", {"data": data},
                                  maxlen=10000, approximate=True)
                        pipe.publish(
                            f"tenant:{ev['tenant_id']}:run:{ev['run_id']}:events",
                            legacy)
                        pipe.publish(f"run:{ev['run_id']}:notify", data)
                        if settings.RELAY_MIRROR_JSON:
                            from app.eventbus.kafka_client import KafkaProducerHolder
                            producer = await KafkaProducerHolder.get()
                            await producer.send(
                                "run-events-json",
                                value=data.encode(),
                                key=ev["tenant_id"].encode())
                    except Exception as e:
                        log.exception("relay decode failed")
                        await send_to_dlq(m, e, GROUP)
            await pipe.execute()
            await consumer.commit()
    finally:
        await consumer.stop()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_relay())
