"""死信队列：原始字节 + 错误上下文 headers 原样转存。"""
import time
import traceback

from aiokafka import ConsumerRecord

from app.config import settings
from app.eventbus.kafka_client import KafkaProducerHolder


async def send_to_dlq(msg: ConsumerRecord, error: Exception, consumer_group: str) -> None:
    producer = await KafkaProducerHolder.get()
    headers = list(msg.headers or [])
    headers += [
        ("dlq-original-topic", msg.topic.encode()),
        ("dlq-original-partition", str(msg.partition).encode()),
        ("dlq-original-offset", str(msg.offset).encode()),
        ("dlq-error", str(error)[:512].encode()),
        ("dlq-traceback", traceback.format_exc()[-2000:].encode()),
        ("dlq-consumer-group", consumer_group.encode()),
        ("dlq-ts", str(int(time.time() * 1000)).encode()),
        ("dlq-replay-count", dict(msg.headers or {}).get("dlq-replay-count", b"0")),
    ]
    await producer.send_and_wait(
        settings.KAFKA_TOPIC_DLQ,
        value=msg.value, key=msg.key, headers=headers)
