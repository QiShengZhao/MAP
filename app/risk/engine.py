import json, logging, time
import aiohttp
from sqlalchemy import select, update
from app.infra.db import SessionLocal
from app.infra.redis_client import redis_client
from app.domain.models import RiskRule, RiskIncident, TenantPolicy
from app.risk.expression import SafeExpression, ExpressionError

log = logging.getLogger("risk-engine")
RELOAD_CHANNEL = "risk:rules:reload"

class CompiledRule:
    def __init__(self, row):
        self.name = row.name
        self.tenant_id = row.tenant_id
        self.priority = row.priority
        self.cooldown = row.cooldown_seconds
        self.actions = row.actions
        self.expr = SafeExpression(row.condition)

class RuleEngine:
    def __init__(self):
        self.global_rules = []
        self.tenant_rules = {}

    async def load(self):
        async with SessionLocal() as db:
            rows = (await db.execute(select(RiskRule).where(
                RiskRule.enabled == True))).scalars().all()
        global_, tenant_ = [], {}
        for r in rows:
            try:
                cr = CompiledRule(r)
            except ExpressionError as e:
                log.error("rule %s skipped: %s", r.name, e)
                continue
            if r.tenant_id:
                tenant_.setdefault(r.tenant_id, []).append(cr)
            else:
                global_.append(cr)
        global_.sort(key=lambda r: r.priority)
        for lst in tenant_.values():
            lst.sort(key=lambda r: r.priority)
        self.global_rules, self.tenant_rules = global_, tenant_
        log.info("rules loaded: %d global, %d tenant",
                 len(global_), sum(len(v) for v in tenant_.values()))

    async def watch_reload(self):
        pubsub = redis_client.pubsub()
        await pubsub.subscribe(RELOAD_CHANNEL)
        last_full = time.monotonic()
        while True:
            m = await pubsub.get_message(ignore_subscribe_messages=True,
                                         timeout=10)
            if m or time.monotonic() - last_full > 300:
                await self.load()
                last_full = time.monotonic()

    @staticmethod
    async def signal_reload():
        await redis_client.publish(RELOAD_CHANNEL, "1")

    def rules_for(self, tenant_id):
        overrides = {r.name for r in self.tenant_rules.get(tenant_id, [])}
        merged = [r for r in self.global_rules if r.name not in overrides]
        merged += self.tenant_rules.get(tenant_id, [])
        return sorted(merged, key=lambda r: r.priority)

    async def evaluate(self, tenant_id, metrics):
        for rule in self.rules_for(tenant_id):
            try:
                if not rule.expr.evaluate(metrics):
                    continue
            except Exception as e:
                log.warning("rule %s eval error: %s", rule.name, e)
                continue
            cd_key = f"risk:cooldown:{tenant_id}:{rule.name}"
            if not await redis_client.set(cd_key, "1", nx=True,
                                          ex=rule.cooldown):
                continue
            await self._fire(tenant_id, rule, metrics)

    async def _fire(self, tenant_id, rule, metrics):
        taken = []
        for action in rule.actions:
            try:
                await self._execute_action(tenant_id, action)
                taken.append(action)
            except Exception:
                log.exception("action failed: %s", action)
        async with SessionLocal() as db:
            db.add(RiskIncident(tenant_id=tenant_id, rule_name=rule.name,
                                metrics=metrics, actions_taken=taken))
            await db.commit()
        log.warning("RISK FIRED tenant=%s rule=%s", tenant_id, rule.name)

    async def _execute_action(self, tenant_id, action):
        t, p = action.get("type"), action.get("params", {})
        if t == "throttle":
            async with SessionLocal() as db:
                await db.execute(update(TenantPolicy)
                    .where(TenantPolicy.tenant_id == tenant_id)
                    .values(max_concurrent_runs=p.get(
                        "max_concurrent_runs", 1)))
                await db.commit()
        elif t == "flag":
            await redis_client.setex(
                f"risk:{p.get('key', 'flagged')}:{tenant_id}",
                p.get("ttl", 600), "1")
        elif t == "pause_tenant":
            await redis_client.setex(f"risk:paused:{tenant_id}",
                                     p.get("ttl", 1800), "1")
        elif t == "notify":
            payload = {"tenant_id": tenant_id,
                       "severity": p.get("severity", "medium"),
                       "ts": int(time.time())}
            await redis_client.publish("risk:notifications",
                                       json.dumps(payload))
            if p.get("webhook"):
                async with aiohttp.ClientSession() as s:
                    await s.post(p["webhook"], json=payload,
                                 timeout=aiohttp.ClientTimeout(total=5))
        else:
            raise ValueError(f"unknown action type: {t}")

engine = RuleEngine()