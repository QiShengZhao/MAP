import asyncio, json
from app.infra.redis_client import redis_client

class ApprovalService:
    @staticmethod
    def _key(tenant_id, run_id, call_id):
        return f"approval:{tenant_id}:{run_id}:{call_id}"

    @staticmethod
    async def notify(tenant_id, run_id, call_id, approved, approver):
        key = ApprovalService._key(tenant_id, run_id, call_id)
        val = json.dumps({"approved": approved, "approver": approver})
        await redis_client.set(key, val, ex=86400)
        await redis_client.publish(key, val)

    @staticmethod
    async def wait(tenant_id, run_id, call_id, timeout=3600):
        key = ApprovalService._key(tenant_id, run_id, call_id)
        val = await redis_client.get(key)
        if val:
            return json.loads(val)["approved"]
        pubsub = redis_client.pubsub()
        await pubsub.subscribe(key)
        try:
            deadline = asyncio.get_event_loop().time() + timeout
            while asyncio.get_event_loop().time() < deadline:
                m = await pubsub.get_message(ignore_subscribe_messages=True,
                                             timeout=5)
                if m:
                    return json.loads(m["data"])["approved"]
                val = await redis_client.get(key)
                if val:
                    return json.loads(val)["approved"]
            return False
        finally:
            await pubsub.unsubscribe(key)
            await pubsub.close()