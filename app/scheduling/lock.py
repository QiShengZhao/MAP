import uuid
from app.infra.redis_client import redis_client

RELEASE_LUA = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('del', KEYS[1])
else return 0 end
"""

EXTEND_LUA = """
if redis.call('get', KEYS[1]) == ARGV[1] then
  return redis.call('expire', KEYS[1], tonumber(ARGV[2]))
else return 0 end
"""

class LockNotAcquired(Exception): pass

class DistributedLock:
    def __init__(self, key, ttl=900):
        self.key, self.ttl, self.token = f"lock:{key}", ttl, str(uuid.uuid4())

    async def acquire(self):
        return bool(await redis_client.set(self.key, self.token, nx=True,
                                           ex=self.ttl))

    async def extend(self):
        return bool(await redis_client.eval(
            EXTEND_LUA, 1, self.key, self.token, self.ttl))

    async def release(self):
        await redis_client.eval(RELEASE_LUA, 1, self.key, self.token)
