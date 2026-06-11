import time
from datetime import datetime, timezone
from app.infra.redis_client import redis_client

class CostTimeseries:
    """分钟粒度成本时序（Redis Hash 按天分桶）"""
    @staticmethod
    def _bucket(ts=None):
        dt = datetime.fromtimestamp(ts or time.time(), tz=timezone.utc)
        return dt.strftime("%Y%m%d"), dt.hour * 60 + dt.minute

    @classmethod
    async def record(cls, tenant_id, cost_usd):
        if cost_usd <= 0:
            return
        day, minute = cls._bucket()
        key = f"cost:ts:{tenant_id}:{day}"
        await redis_client.hincrbyfloat(key, str(minute), cost_usd)
        await redis_client.expire(key, 86400 * 3)

    @classmethod
    async def recent_minutes(cls, tenant_id, n=30):
        now = time.time()
        out = []
        for i in range(n):
            day, minute = cls._bucket(now - i * 60)
            v = await redis_client.hget(f"cost:ts:{tenant_id}:{day}",
                                        str(minute))
            out.append(float(v or 0))
        return list(reversed(out))