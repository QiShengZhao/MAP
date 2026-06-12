"""消费 risk-metrics → 攒齐同窗口指标 → 调用规则引擎评估 → 执行动作。"""
import asyncio
import json
import logging
import time

from aiokafka import AIOKafkaConsumer

from app.config import settings
from app.eventbus.dlq import send_to_dlq
from app.eventbus.kafka_client import _security_kwargs
from app.risk.engine import RiskEngine

log = logging.getLogger("eventbus.risk")

GROUP = "risk-consumer"
EXPECTED_METRICS = {"tool_call_rate", "error_rate", "cost_per_min", "distinct_tools",
                    "sandbox_exec_rate", "approval_denied", "token_rate"}
BUCKET_TIMEOUT = 5.0
BUCKET_MAX = 10_000


class WindowBuckets:
    def __init__(self):
        self._buckets: dict[tuple[str, int], dict] = {}

    def add(self, m: dict) -> dict | None:
        key = (m["tenant_id"], m["window_start"])
        b = self._buckets.setdefault(key, {"_created": time.monotonic(), "_metrics": {}})
        b["_metrics"][m["metric"]] = m["value"]
        if set(b["_metrics"]) >= EXPECTED_METRICS:
            self._buckets.pop(key)
            return self._finalize(key, b)
        if len(self._buckets) > BUCKET_MAX:
            oldest = min(self._buckets, key=lambda k: self._buckets[k]["_created"])
            ob = self._buckets.pop(oldest)
            log.warning("bucket overflow, force-flush %s", oldest)
            return self._finalize(oldest, ob)
        return None

    def expire(self) -> list[dict]:
        now = time.monotonic()
        expired = [k for k, b in self._buckets.items()
                   if now - b["_created"] > BUCKET_TIMEOUT]
        return [self._finalize(k, self._buckets.pop(k)) for k in expired]

    @staticmethod
    def _finalize(key: tuple[str, int], bucket: dict) -> dict:
        tenant_id, window_start = key
        ctx = {m: bucket["_metrics"].get(m, 0.0) for m in EXPECTED_METRICS}
        ctx["tenant_id"] = tenant_id
        ctx["window_start"] = window_start
        return ctx


async def run_risk_consumer() -> None:
    engine = RiskEngine()
    await engine.start()

    consumer = AIOKafkaConsumer(
        "risk-metrics",
        bootstrap_servers=settings.KAFKA_BOOTSTRAP,
        group_id=GROUP,
        enable_auto_commit=False,
        auto_offset_reset="latest",
        max_poll_records=500,
        **_security_kwargs(),
    )
    await consumer.start()
    log.info("risk consumer started")
    buckets = WindowBuckets()

    try:
        while True:
            batch = await consumer.getmany(timeout_ms=1000)
            ready: list[dict] = []
            for _tp, msgs in batch.items():
                for m in msgs:
                    try:
                        metric = json.loads(m.value)
                        if done := buckets.add(metric):
                            ready.append(done)
                    except Exception as e:
                        await send_to_dlq(m, e, GROUP)
            ready.extend(buckets.expire())

            for ctx in ready:
                try:
                    incidents = await engine.evaluate(ctx)
                    for inc in incidents:
                        if inc.executed:
                            log.warning("risk incident tenant=%s rule=%s action=%s",
                                        ctx["tenant_id"], inc.rule_name, inc.action)
                except Exception:
                    log.exception("rule evaluation failed tenant=%s", ctx.get("tenant_id"))

            if batch:
                await consumer.commit()
    finally:
        await consumer.stop()
        await engine.stop()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_risk_consumer())
