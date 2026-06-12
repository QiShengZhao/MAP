import json
import pytest
from unittest.mock import AsyncMock

from app.risk.engine import RiskEngine, CompiledRule
from app.risk.expression import compile_expr

CTX = {"tenant_id": "t1", "window_start": 0, "tool_call_rate": 100.0,
       "error_rate": 0.5, "cost_per_min": 9.0, "distinct_tools": 3.0,
       "sandbox_exec_rate": 0.0, "approval_denied": 0.0, "token_rate": 0.0}


def make_rule(action="flag", cooldown=60, expr="error_rate > 0.3", tenant=None, params=None):
    return CompiledRule(rule_id="r1", name="high-error", tenant_id=tenant,
                        expression=expr, compiled=compile_expr(expr),
                        action=action, action_params=params or {},
                        cooldown_seconds=cooldown, severity="warning", enabled=True)


@pytest.fixture
async def engine(redis, monkeypatch):
    e = RiskEngine()
    monkeypatch.setattr("app.risk.engine.get_redis", AsyncMock(return_value=redis))
    e._persist_incident = AsyncMock()
    e._emit_event = AsyncMock()
    yield e
    await e._http.aclose()


async def test_hit_executes_action(engine, redis):
    engine._rules = [make_rule(action="throttle",
                               params={"max_concurrent_runs": 2, "duration_seconds": 60})]
    incidents = await engine.evaluate(CTX)
    assert incidents[0].executed is True
    t = json.loads(await redis.get("risk:throttle:t1"))
    assert t["max_concurrent_runs"] == 2


async def test_cooldown_suppresses_second_hit(engine):
    engine._rules = [make_rule(cooldown=300)]
    first = await engine.evaluate(CTX)
    second = await engine.evaluate(CTX)
    assert first[0].executed and not second[0].executed
    assert second[0].suppressed_by_cooldown


async def test_dry_run_no_side_effects(engine, redis):
    engine._rules = [make_rule(action="pause")]
    incidents = await engine.evaluate(CTX, dry_run=True)
    assert incidents and not incidents[0].executed
    assert not await redis.exists("risk:paused:t1")
    assert not await redis.exists("risk:cooldown:r1:t1")


async def test_tenant_scoped_rule_skips_other_tenants(engine):
    engine._rules = [make_rule(tenant="other-tenant")]
    assert await engine.evaluate(CTX) == []


async def test_pause_action_sets_key_and_publishes(engine, redis):
    engine._rules = [make_rule(action="pause", params={"duration_seconds": 60})]
    await engine.evaluate(CTX)
    assert await redis.exists("risk:paused:t1")


async def test_no_hit_no_incident(engine):
    engine._rules = [make_rule(expr="error_rate > 0.9")]
    assert await engine.evaluate(CTX) == []
