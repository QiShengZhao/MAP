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

    @classmethod
    async def recent_metric_windows(cls, tenant_id: str, n: int = 10) -> list[dict]:
        """回放最近 N 分钟成本窗口，供 risk dry-run 使用（简化指标）。"""
        costs = await cls.recent_minutes(tenant_id, n)
        now = int(time.time())
        return [{
            "tenant_id": tenant_id,
            "window_start": (now - (n - i) * 60) * 1000,
            "cost_per_min": c,
            "tool_call_rate": 0.0,
            "error_rate": 0.0,
            "distinct_tools": 0.0,
            "sandbox_exec_rate": 0.0,
            "approval_denied": 0.0,
            "token_rate": 0.0,
        } for i, c in enumerate(costs)]