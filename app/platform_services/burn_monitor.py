import asyncio, json, logging
from datetime import datetime, timezone
from sqlalchemy import select
from app.infra.db import SessionLocal
from app.infra.redis_client import redis_client
from app.domain.models import TenantPolicy
from app.platform_services.cost_timeseries import CostTimeseries

log = logging.getLogger("burn-monitor")
EWMA_ALPHA, MIN_RATE, DEDUP_TTL = 0.15, 1e-6, 1800
LEVELS = [("emergency", 15 * 60), ("critical", 60 * 60), ("warning", 4 * 3600)]

class BurnRateMonitor:
    @staticmethod
    def ewma_rate(series):
        """EWMA 烧钱速率 $/min（忽略前导零）"""
        started, rate = False, 0.0
        for v in series:
            if not started and v == 0:
                continue
            if not started:
                rate, started = v, True
                continue
            rate = EWMA_ALPHA * v + (1 - EWMA_ALPHA) * rate
        return max(rate, 0.0)

    @classmethod
    async def analyze_tenant(cls, tenant_id, policy):
        day = datetime.now(timezone.utc).strftime("%Y%m%d")
        used = float(await redis_client.get(
            f"budget:tenant:{tenant_id}:{day}") or 0)
        limit = policy.max_cost_per_day_usd
        remaining = max(0.0, limit - used)
        series = await CostTimeseries.recent_minutes(tenant_id, 30)
        rate = cls.ewma_rate(series)
        if rate < MIN_RATE:
            return None
        seconds_to_exhaustion = remaining / rate * 60
        now = datetime.now(timezone.utc)
        minutes_left = (24 * 60) - (now.hour * 60 + now.minute)
        projected_eod = used + rate * minutes_left
        level = None
        for name, threshold in LEVELS:
            if seconds_to_exhaustion <= threshold:
                level = name
                break
        if level is None and projected_eod > limit:
            level = "warning"
        if level is None:
            return None
        return {"tenant_id": tenant_id, "level": level,
                "used_usd": round(used, 4), "limit_usd": limit,
                "burn_rate_usd_per_min": round(rate, 6),
                "seconds_to_exhaustion": int(seconds_to_exhaustion),
                "projected_eod_usd": round(projected_eod, 4)}

    @classmethod
    async def alert(cls, report):
        tenant_id, level = report["tenant_id"], report["level"]
        rank = {"warning": 0, "critical": 1, "emergency": 2}
        prev = await redis_client.get(f"burn:alerted:{tenant_id}")
        if prev and rank[level] <= rank.get(prev, -1):
            return
        await redis_client.setex(f"burn:alerted:{tenant_id}", DEDUP_TTL, level)
        await redis_client.publish("budget:alerts", json.dumps(report))
        log.warning("BURN ALERT %s tenant=%s exhaust_in=%ss",
                    level, tenant_id, report["seconds_to_exhaustion"])
        if level == "emergency":
            await redis_client.setex(f"risk:cost_limited:{tenant_id}",
                                     1800, "1")

async def main():
    logging.basicConfig(level=logging.INFO)
    while True:
        try:
            async with SessionLocal() as db:
                policies = (await db.execute(
                    select(TenantPolicy))).scalars().all()
            for p in policies:
                try:
                    report = await BurnRateMonitor.analyze_tenant(p.tenant_id, p)
                    if report:
                        await BurnRateMonitor.alert(report)
                except Exception:
                    log.exception("analyze failed tenant=%s", p.tenant_id)
        except Exception:
            log.exception("monitor tick failed")
        await asyncio.sleep(60)

if __name__ == "__main__":
    asyncio.run(main())