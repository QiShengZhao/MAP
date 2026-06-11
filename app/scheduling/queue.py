import json
from app.infra.redis_client import redis_client

STREAM, GROUP = "agent:run:queue", "workers"
PENDING_IDLE_MS = 10 * 60 * 1000

class RunQueue:
    @staticmethod
    async def ensure_group():
        try:
            await redis_client.xgroup_create(STREAM, GROUP, id="0", mkstream=True)
        except Exception:
            pass

    @staticmethod
    async def enqueue(tenant_id, run_id, workspace_id=""):
        await redis_client.xadd(STREAM, {"data": json.dumps(
            {"tenant_id": tenant_id, "run_id": run_id,
             "workspace_id": workspace_id})}, maxlen=100_000)

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