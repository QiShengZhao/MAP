"""幂等 Kafka Producer + Topic 管理（aiokafka）"""
import asyncio
import logging
import ssl

from aiokafka import AIOKafkaProducer
from aiokafka.admin import AIOKafkaAdminClient, NewTopic
from aiokafka.errors import TopicAlreadyExistsError

from app.config import settings

log = logging.getLogger("eventbus.kafka")

TOPICS = {
    "run-events": dict(partitions=12, rf=settings.KAFKA_RF, config={
        "retention.ms": str(7 * 24 * 3600 * 1000),
        "cleanup.policy": "delete",
        "min.insync.replicas": str(min(2, settings.KAFKA_RF)),
    }),
    "run-events.dlq": dict(partitions=3, rf=settings.KAFKA_RF, config={
        "retention.ms": str(30 * 24 * 3600 * 1000),
    }),
    "risk-metrics": dict(partitions=6, rf=settings.KAFKA_RF, config={
        "retention.ms": str(24 * 3600 * 1000),
    }),
    "run-events-json": dict(partitions=6, rf=settings.KAFKA_RF, config={
        "retention.ms": str(3600 * 1000),
    }),
}


def _security_kwargs() -> dict:
    if settings.KAFKA_SECURITY_PROTOCOL == "PLAINTEXT":
        return {}
    ctx = ssl.create_default_context()
    if settings.KAFKA_SSL_CAFILE:
        ctx.load_verify_locations(settings.KAFKA_SSL_CAFILE)
    kw: dict = {"security_protocol": settings.KAFKA_SECURITY_PROTOCOL, "ssl_context": ctx}
    if settings.KAFKA_SECURITY_PROTOCOL.startswith("SASL"):
        kw.update(
            sasl_mechanism=settings.KAFKA_SASL_MECHANISM,
            sasl_plain_username=settings.KAFKA_SASL_USERNAME,
            sasl_plain_password=settings.KAFKA_SASL_PASSWORD,
        )
    return kw


async def ensure_topics() -> None:
    admin = AIOKafkaAdminClient(bootstrap_servers=settings.KAFKA_BOOTSTRAP, **_security_kwargs())
    await admin.start()
    try:
        for name, c in TOPICS.items():
            topic = NewTopic(name, num_partitions=c["partitions"], replication_factor=c["rf"],
                             topic_configs=c.get("config", {}))
            try:
                await admin.create_topics([topic])
                log.info("topic created: %s", name)
            except TopicAlreadyExistsError:
                pass
    finally:
        await admin.close()


class KafkaProducerHolder:
    _producer: AIOKafkaProducer | None = None
    _lock = asyncio.Lock()

    @classmethod
    async def get(cls) -> AIOKafkaProducer:
        if cls._producer is None:
            async with cls._lock:
                if cls._producer is None:
                    p = AIOKafkaProducer(
                        bootstrap_servers=settings.KAFKA_BOOTSTRAP,
                        enable_idempotence=True,
                        acks="all",
                        linger_ms=5,
                        compression_type="zstd",
                        max_request_size=1024 * 1024,
                        request_timeout_ms=15000,
                        **_security_kwargs(),
                    )
                    await p.start()
                    cls._producer = p
        return cls._producer

    @classmethod
    async def close(cls) -> None:
        if cls._producer:
            await cls._producer.stop()
            cls._producer = None
