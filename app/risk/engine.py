"""风控规则引擎：加载/热更新/评估/cooldown 去重/动作执行。"""
import asyncio
import hashlib
import hmac
import json
import logging
import time
import uuid
from dataclasses import dataclass, field

import httpx
from sqlalchemy import select

from app.config import settings
from app.domain.models import RiskIncident, RiskRule
from app.infra import db as db_mod
from app.infra.redis_client import get_redis
from app.risk.expression import compile_expr, evaluate_compiled, ExpressionError

log = logging.getLogger("risk.engine")

RELOAD_CHANNEL = "risk:rules:reload"
VALID_ACTIONS = {"throttle", "flag", "pause", "notify"}


@dataclass
class CompiledRule:
    rule_id: str
    name: str
    tenant_id: str | None
    expression: str
    compiled: object
    action: str
    action_params: dict
    cooldown_seconds: int
    severity: str
    enabled: bool


@dataclass
class Incident:
    rule_id: str
    rule_name: str
    tenant_id: str
    action: str
    severity: str
    context: dict
    executed: bool
    suppressed_by_cooldown: bool = False
    incident_id: str = field(default_factory=lambda: str(uuid.uuid4()))


def _normalize_action(action: str) -> str:
    if action == "pause_tenant":
        return "pause"
    return action if action in VALID_ACTIONS else "flag"


def _rule_from_row(r: RiskRule) -> CompiledRule:
    action = _normalize_action(r.action or "flag")
    params = dict(r.action_params or {})
    if not r.action and r.actions:
        first = r.actions[0] if isinstance(r.actions, list) else {}
        action = _normalize_action(first.get("type", "flag"))
        params = dict(first.get("params") or {})
    return CompiledRule(
        rule_id=str(r.id), name=r.name,
        tenant_id=str(r.tenant_id) if r.tenant_id else None,
        expression=r.condition,
        compiled=compile_expr(r.condition),
        action=action,
        action_params=params,
        cooldown_seconds=r.cooldown_seconds or 300,
        severity=r.severity or "warning",
        enabled=True,
    )


class RiskEngine:
    def __init__(self):
        self._rules: list[CompiledRule] = []
        self._rules_version: str = ""
        self._reload_task: asyncio.Task | None = None
        self._http = httpx.AsyncClient(timeout=10)

    async def start(self) -> None:
        await self.reload_rules()
        self._reload_task = asyncio.create_task(self._listen_reload())
        log.info("risk engine started, %d rules loaded", len(self._rules))

    async def stop(self) -> None:
        if self._reload_task:
            self._reload_task.cancel()
            try:
                await self._reload_task
            except asyncio.CancelledError:
                pass
        await self._http.aclose()

    async def reload_rules(self) -> None:
        async with db_mod.session_factory() as db:
            rows = (await db.scalars(
                select(RiskRule).where(RiskRule.enabled == True)  # noqa: E712
            )).all()

        compiled: list[CompiledRule] = []
        for r in rows:
            try:
                compiled.append(_rule_from_row(r))
            except ExpressionError as e:
                log.error("rule %s compile failed, skipped: %s", r.name, e)

        self._rules = compiled
        self._rules_version = hashlib.md5(
            "|".join(f"{c.rule_id}:{c.expression}" for c in compiled).encode()
        ).hexdigest()[:8]
        log.info("rules reloaded version=%s count=%d", self._rules_version, len(compiled))

    async def _listen_reload(self) -> None:
        backoff = 1
        while True:
            try:
                r = await get_redis()
                pubsub = r.pubsub()
                await pubsub.subscribe(RELOAD_CHANNEL)
                backoff = 1
                async for msg in pubsub.listen():
                    if msg["type"] == "message":
                        log.info("reload signal received")
                        await self.reload_rules()
            except asyncio.CancelledError:
                return
            except Exception:
                log.exception("reload listener error, retry in %ss", backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)

    @staticmethod
    async def publish_reload() -> None:
        r = await get_redis()
        await r.publish(RELOAD_CHANNEL, str(int(time.time())))

    async def evaluate(self, ctx: dict, dry_run: bool = False) -> list[Incident]:
        tenant_id = ctx["tenant_id"]
        incidents: list[Incident] = []

        for rule in self._rules:
            if rule.tenant_id is not None and rule.tenant_id != tenant_id:
                continue
            try:
                hit = evaluate_compiled(rule.compiled, ctx)
            except ExpressionError as e:
                log.warning("rule %s eval error (ctx missing var?): %s", rule.name, e)
                continue
            if not hit:
                continue

            inc = Incident(rule_id=rule.rule_id, rule_name=rule.name,
                           tenant_id=tenant_id, action=rule.action,
                           severity=rule.severity, context=dict(ctx), executed=False)

            if dry_run:
                incidents.append(inc)
                continue

            if not await self._acquire_cooldown(rule, tenant_id):
                inc.suppressed_by_cooldown = True
                incidents.append(inc)
                continue

            try:
                await self._execute_action(rule, inc)
                inc.executed = True
            except Exception:
                log.exception("action %s failed rule=%s tenant=%s",
                              rule.action, rule.name, tenant_id)
            await self._persist_incident(rule, inc)
            await self._emit_event(inc)
            incidents.append(inc)

        return incidents

    async def _acquire_cooldown(self, rule: CompiledRule, tenant_id: str) -> bool:
        r = await get_redis()
        key = f"risk:cooldown:{rule.rule_id}:{tenant_id}"
        return bool(await r.set(key, str(int(time.time())),
                                nx=True, ex=rule.cooldown_seconds))

    async def _execute_action(self, rule: CompiledRule, inc: Incident) -> None:
        handler = {
            "throttle": self._act_throttle,
            "pause": self._act_pause,
            "flag": self._act_flag,
            "notify": self._act_notify,
        }[rule.action]
        await handler(rule, inc)
        log.warning("ACTION executed action=%s rule=%s tenant=%s severity=%s",
                    rule.action, rule.name, inc.tenant_id, rule.severity)

    async def _act_throttle(self, rule: CompiledRule, inc: Incident) -> None:
        p = rule.action_params
        duration = int(p.get("duration_seconds", 600))
        r = await get_redis()
        await r.set(
            f"risk:throttle:{inc.tenant_id}",
            json.dumps({
                "max_concurrent_runs": int(p.get("max_concurrent_runs", 1)),
                "rule": rule.name,
                "until": int(time.time()) + duration,
            }),
            ex=duration,
        )

    async def _act_pause(self, rule: CompiledRule, inc: Incident) -> None:
        p = rule.action_params
        duration = int(p.get("duration_seconds", 1800))
        r = await get_redis()
        payload = json.dumps({
            "rule": rule.name,
            "reason": p.get("reason", f"risk rule triggered: {rule.name}"),
            "incident_id": inc.incident_id,
        })
        if duration > 0:
            await r.set(f"risk:paused:{inc.tenant_id}", payload, ex=duration)
        else:
            await r.set(f"risk:paused:{inc.tenant_id}", payload)
        await r.publish(f"tenant:{inc.tenant_id}:control",
                        json.dumps({"op": "pause", "incident_id": inc.incident_id}))

    async def _act_flag(self, rule: CompiledRule, inc: Incident) -> None:
        r = await get_redis()
        await r.zadd("risk:flagged_tenants", {inc.tenant_id: time.time()})

    async def _act_notify(self, rule: CompiledRule, inc: Incident) -> None:
        p = rule.action_params
        url = p.get("webhook_url") or settings.RISK_DEFAULT_WEBHOOK
        if not url:
            log.warning("notify action but no webhook configured rule=%s", rule.name)
            return

        body = {
            "incident_id": inc.incident_id,
            "rule": rule.name,
            "severity": rule.severity,
            "tenant_id": inc.tenant_id,
            "triggered_at": int(time.time()),
            "context": inc.context if p.get("include_context", True) else {},
        }
        raw = json.dumps(body, sort_keys=True).encode()
        sig = hmac.new(settings.RISK_WEBHOOK_SECRET.encode(), raw,
                       hashlib.sha256).hexdigest()
        for attempt in range(3):
            try:
                resp = await self._http.post(url, content=raw, headers={
                    "Content-Type": "application/json",
                    "X-Risk-Signature": f"sha256={sig}",
                })
                if resp.status_code < 500:
                    return
            except httpx.HTTPError:
                pass
            await asyncio.sleep(2 ** attempt)
        log.error("webhook notify failed after retries rule=%s url=%s", rule.name, url)

    async def _persist_incident(self, rule: CompiledRule, inc: Incident) -> None:
        async with db_mod.session_factory() as db:
            db.add(RiskIncident(
                id=inc.incident_id,
                tenant_id=inc.tenant_id,
                rule_id=rule.rule_id,
                rule_name=rule.name,
                severity=rule.severity,
                action=rule.action,
                action_executed=inc.executed,
                context=inc.context,
                metrics=inc.context,
                actions_taken=[rule.action] if inc.executed else [],
            ))
            await db.commit()

    async def _emit_event(self, inc: Incident) -> None:
        from app.eventbus.bus import build_run_event, publish_run_event
        ev = build_run_event(
            run_id=f"risk-{inc.incident_id}",
            tenant_id=inc.tenant_id, seq=0,
            event_type="risk.incident",
            payload={"rule": inc.rule_name, "severity": inc.severity,
                     "action": inc.action, "executed": inc.executed,
                     "suppressed": inc.suppressed_by_cooldown},
        )
        try:
            await publish_run_event(ev)
        except Exception:
            log.exception("emit risk event failed (non-fatal)")


# 兼容旧引用
RuleEngine = RiskEngine
engine = RiskEngine()
