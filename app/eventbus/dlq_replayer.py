"""DLQ 重放工具：python -m app.eventbus.dlq_replayer --topic run-events --max 100"""
import argparse
import asyncio
import logging

from aiokafka import AIOKafkaConsumer

from app.config import settings
from app.eventbus.kafka_client import KafkaProducerHolder, _security_kwargs

log = logging.getLogger("eventbus.dlq_replayer")
MAX_REPLAYS = 3


async def replay(topic: str, max_messages: int, dry_run: bool) -> None:
    consumer = AIOKafkaConsumer(
        f"{topic}.dlq",
        bootstrap_servers=settings.KAFKA_BOOTSTRAP,
        group_id=f"dlq-replayer-{topic}",
        enable_auto_commit=False,
        auto_offset_reset="earliest",
        **_security_kwargs(),
    )
    await consumer.start()
    producer = await KafkaProducerHolder.get()
    replayed = skipped = 0
    try:
        while replayed + skipped < max_messages:
            batch = await consumer.getmany(timeout_ms=3000, max_records=50)
            if not batch:
                break
            for _tp, msgs in batch.items():
                for m in msgs:
                    h = dict(m.headers or {})
                    count = int(h.get("dlq-replay-count", b"0"))
                    if count >= MAX_REPLAYS:
                        log.warning("skip poison message offset=%s replays=%s", m.offset, count)
                        skipped += 1
                        continue
                    if dry_run:
                        log.info("[dry-run] would replay offset=%s err=%s",
                                 m.offset, h.get("dlq-error"))
                    else:
                        headers = [(k, v) for k, v in (m.headers or [])
                                   if not k.startswith("dlq-")]
                        headers.append(("dlq-replay-count", str(count + 1).encode()))
                        await producer.send_and_wait(topic, value=m.value,
                                                     key=m.key, headers=headers)
                    replayed += 1
            if not dry_run:
                await consumer.commit()
        log.info("done: replayed=%s skipped=%s", replayed, skipped)
    finally:
        await consumer.stop()
        await KafkaProducerHolder.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    ap = argparse.ArgumentParser()
    ap.add_argument("--topic", default="run-events")
    ap.add_argument("--max", type=int, default=100)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    asyncio.run(replay(a.topic, a.max, a.dry_run))
