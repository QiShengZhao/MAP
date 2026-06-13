from datetime import datetime
from app.infra.redis_client import redis_client
from app.domain.models import UsageRecord
from app.runtime.guardrails import GuardrailBlocked

TOKEN_RESERVE_LUA = """
local used = tonumber(redis.call('get', KEYS[1]) or '0')
local amount = tonumber(ARGV[1])
local limit = tonumber(ARGV[2])
if used + amount > limit then return -1 end
redis.call('incrby', KEYS[1], amount)
redis.call('expire', KEYS[1], tonumber(ARGV[3]))
return used + amount
"""


class UsageMeter:
    def __init__(self, tenant_id, workspace_id, run_id):
        self.tenant_id, self.workspace_id, self.run_id = \
            tenant_id, workspace_id, run_id
        self.tokens = {"prompt": 0, "completion": 0}
        self.tool_calls = {}
        self.sandbox_seconds = 0
        self.by_model = {}
        self.cost_usd = 0.0
        self._reserved_tokens = 0

    @staticmethod
    def _day():
        return datetime.utcnow().strftime("%Y%m%d")

    def add_tokens(self, usage, model="", provider="", cost_usd=0.0):
        self.tokens["prompt"] += usage.get("prompt", 0)
        self.tokens["completion"] += usage.get("completion", 0)
        self.cost_usd += cost_usd
        if model:
            key = f"{provider or 'unknown'}/{model}"
            self.by_model[key] = self.by_model.get(key, 0) + \
                usage.get("prompt", 0) + usage.get("completion", 0)

    def add_tool_call(self, name):
        self.tool_calls[name] = self.tool_calls.get(name, 0) + 1

    def add_sandbox_seconds(self, seconds):
        self.sandbox_seconds += seconds

    def snapshot(self):
        return {"tokens": self.tokens, "tool_calls": self.tool_calls,
                "sandbox_seconds": self.sandbox_seconds,
                "by_model": self.by_model,
                "cost_usd": round(self.cost_usd, 6),
                "reserved_tokens": self._reserved_tokens}

    def restore(self, data: dict) -> None:
        if not data:
            return
        self.tokens = dict(data.get("tokens", self.tokens))
        self.tool_calls = dict(data.get("tool_calls", self.tool_calls))
        self.sandbox_seconds = data.get("sandbox_seconds", 0)
        self.by_model = dict(data.get("by_model", self.by_model))
        self.cost_usd = float(data.get("cost_usd", 0))
        self._reserved_tokens = int(data.get("reserved_tokens", 0))

    async def check_token_quota(self, policy):
        total = self.tokens["prompt"] + self.tokens["completion"]
        await self.reserve_tokens(total - self._reserved_tokens, policy)

    async def reserve_tokens(self, amount, policy):
        if amount <= 0:
            return
        day = self._day()
        key = f"quota:tokens:{self.tenant_id}:{day}"
        result = await redis_client.eval(
            TOKEN_RESERVE_LUA, 1, key, amount,
            policy.max_tokens_per_day, 86400 * 2)
        if int(result) == -1:
            raise GuardrailBlocked("daily token quota exceeded")
        self._reserved_tokens += amount

    async def flush(self, db):
        total_tokens = self.tokens["prompt"] + self.tokens["completion"]
        if total_tokens:
            db.add(UsageRecord(tenant_id=self.tenant_id,
                               workspace_id=self.workspace_id,
                               run_id=self.run_id, kind="tokens",
                               detail={**self.tokens,
                                       "by_model": self.by_model,
                                       "cost_usd": round(self.cost_usd, 6)},
                               quantity=total_tokens))
        if self.tool_calls:
            db.add(UsageRecord(tenant_id=self.tenant_id,
                               workspace_id=self.workspace_id,
                               run_id=self.run_id, kind="tool_call",
                               detail=self.tool_calls,
                               quantity=sum(self.tool_calls.values())))
        if self.sandbox_seconds:
            db.add(UsageRecord(tenant_id=self.tenant_id,
                               workspace_id=self.workspace_id,
                               run_id=self.run_id, kind="sandbox_seconds",
                               detail={}, quantity=self.sandbox_seconds))
