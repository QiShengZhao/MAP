import time
from app.infra.redis_client import redis_client

STATS_KEY = "router:stats:{name}"
LOCAL_TTL = 5.0

EWMA_LUA = """
local key = KEYS[1]
local alpha = tonumber(ARGV[1])
local success = tonumber(ARGV[2])
local latency = tonumber(ARGV[3])
local fail = tonumber(redis.call('hget', key, 'fail_rate') or '0')
fail = alpha * (1 - success) + (1 - alpha) * fail
redis.call('hset', key, 'fail_rate', fail)
if success == 1 and latency > 0 then
  local lat = tonumber(redis.call('hget', key, 'latency_ms') or '0')
  if lat == 0 then lat = latency
  else lat = alpha * latency + (1 - alpha) * lat end
  redis.call('hset', key, 'latency_ms', lat)
end
redis.call('hincrby', key, 'calls', 1)
redis.call('expire', key, 86400)
return 1
"""

class SharedProviderStats:
    """跨 Worker 共享的 Provider 统计（Redis EWMA + 本地短缓存）"""
    def __init__(self, name, alpha=0.2):
        self.name, self.alpha = name, alpha
        self._local = {"latency_ms": 0.0, "fail_rate": 0.0, "calls": 0}
        self._fetched_at = 0.0

    async def record(self, success, first_token_ms=0):
        await redis_client.eval(
            EWMA_LUA, 1, STATS_KEY.format(name=self.name),
            str(self.alpha), "1" if success else "0", str(first_token_ms))
        self._fetched_at = 0

    async def get(self):
        if time.monotonic() - self._fetched_at < LOCAL_TTL:
            return self._local
        raw = await redis_client.hgetall(STATS_KEY.format(name=self.name))
        self._local = {"latency_ms": float(raw.get("latency_ms", 0)),
                       "fail_rate": float(raw.get("fail_rate", 0)),
                       "calls": int(raw.get("calls", 0))}
        self._fetched_at = time.monotonic()
        return self._local