from datetime import datetime
from app.infra.redis_client import redis_client
from app.config import settings

class BudgetExceeded(Exception):
    def __init__(self, scope, used, limit):
        self.scope, self.used, self.limit = scope, used, limit
        super().__init__(f"{scope} budget exceeded: ${used:.4f}/${limit:.2f}")

# 检查+预占原子完成
RESERVE_LUA = """
local used = tonumber(redis.call('get', KEYS[1]) or '0')
local limit = tonumber(ARGV[1])
local amount = tonumber(ARGV[2])
if used + amount > limit then return -1 end
redis.call('incrbyfloat', KEYS[1], amount)
redis.call('expire', KEYS[1], tonumber(ARGV[3]))
return 0
"""

class BudgetGuard:
    @staticmethod
    def _day():
        return datetime.utcnow().strftime("%Y%m%d")

    @classmethod
    async def reserve(cls, tenant_id, run_id, est_cost, policy):
        """三级预算：run / tenant_daily / platform_daily"""
        day = cls._day()
        checks = [
            (f"budget:run:{run_id}", policy.max_cost_per_run_usd, 7200, "run"),
            (f"budget:tenant:{tenant_id}:{day}",
             policy.max_cost_per_day_usd, 86400 * 2, "tenant_daily"),
            (f"budget:platform:{day}",
             settings.PLATFORM_DAILY_BUDGET_USD, 86400 * 2, "platform_daily"),
        ]
        reserved = []
        for key, limit, ttl, scope in checks:
            ok = await redis_client.eval(RESERVE_LUA, 1, key,
                                         str(limit), str(est_cost), str(ttl))
            if int(ok) == -1:
                for rkey in reserved:
                    await redis_client.incrbyfloat(rkey, -est_cost)
                used = float(await redis_client.get(key) or 0)
                raise BudgetExceeded(scope, used, limit)
            reserved.append(key)

    @classmethod
    async def settle(cls, tenant_id, run_id, est_cost, actual_cost):
        diff = actual_cost - est_cost
        if abs(diff) < 1e-9:
            return
        day = cls._day()
        for key in (f"budget:run:{run_id}",
                    f"budget:tenant:{tenant_id}:{day}",
                    f"budget:platform:{day}"):
            await redis_client.incrbyfloat(key, diff)

    @classmethod
    async def status(cls, tenant_id, policy):
        day = cls._day()
        used = float(await redis_client.get(
            f"budget:tenant:{tenant_id}:{day}") or 0)
        return {"used_usd": round(used, 4),
                "limit_usd": policy.max_cost_per_day_usd,
                "remaining_usd": round(
                    max(0, policy.max_cost_per_day_usd - used), 4)}