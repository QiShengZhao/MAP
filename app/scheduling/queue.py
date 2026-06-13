import json
from app.infra.redis_client import redis_client

STREAM, GROUP = "agent:run:queue", "workers"
PENDING_IDLE_MS = 10 * 60 * 1000
DISPATCH_TTL_SECONDS = 7 * 24 * 60 * 60

ENQUEUE_LUA = """
if redis.call('exists', KEYS[2]) == 1 then
  return 0
end
local entry_id = redis.call(
  'xadd', KEYS[1], 'MAXLEN', '~', 100000, '*', 'data', ARGV[1])
redis.call('set', KEYS[2], entry_id, 'EX', ARGV[2])
return entry_id
"""

class RunQueue:
    @staticmethod
    def dispatch_key(run_id):
        return f"run:dispatch:{run_id}"

    @staticmethod
    async def ensure_group():
        try:
            await redis_client.xgroup_create(STREAM, GROUP, id="0", mkstream=True)
        except Exception:
            pass

    @staticmethod
    async def enqueue(tenant_id, run_id, workspace_id="", resume=False):
        payload = json.dumps({
            "tenant_id": tenant_id, "run_id": run_id,
            "workspace_id": workspace_id, "resume": resume,
        })
        result = await redis_client.eval(
            ENQUEUE_LUA, 2, STREAM, RunQueue.dispatch_key(run_id),
            payload, DISPATCH_TTL_SECONDS)
        return result not in (0, b"0", "0")

    @staticmethod
    async def consume(consumer):
        await RunQueue.ensure_group()
        while True:
            resp = await redis_client.xreadgroup(
                GROUP, consumer, {STREAM: ">"}, count=1, block=5000)
            if not resp:
                continue
            for _stream, entries in resp:
                for entry_id, fields in entries:
                    yield entry_id, json.loads(fields["data"])

    @staticmethod
    async def claim_stale(consumer, count=10):
        await RunQueue.ensure_group()
        try:
            _, entries, _ = await redis_client.xautoclaim(
                STREAM, GROUP, consumer, min_idle_time=PENDING_IDLE_MS,
                start_id="0-0", count=count)
            return [(eid, json.loads(f["data"])) for eid, f in entries if f]
        except Exception:
            return []

    @staticmethod
    async def ack(entry_id):
        await redis_client.xack(STREAM, GROUP, entry_id)

    @staticmethod
    async def mark_consumed(run_id):
        await redis_client.delete(RunQueue.dispatch_key(run_id))
