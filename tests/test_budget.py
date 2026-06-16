import pytest

from app.runtime.budget import BudgetExceeded, BudgetGuard
from app.platform_services.usage import UsageMeter


@pytest.fixture
async def budget_redis(redis, monkeypatch):
    from app.runtime import budget as budget_mod
    monkeypatch.setattr(budget_mod, "redis_client", redis)
    return redis


async def test_reserve_and_settle(budget_redis):
    class Policy:
        max_cost_per_run_usd = 1.0
        max_cost_per_day_usd = 10.0

    policy = Policy()
    await BudgetGuard.reserve("t1", "r1", 0.4, policy)
    await BudgetGuard.settle("t1", "r1", 0.4, 0.25)
    snap = await BudgetGuard.status("t1", policy)
    assert snap["used_usd"] == pytest.approx(0.25)


async def test_run_limit_hard_stop(budget_redis):
    class Policy:
        max_cost_per_run_usd = 0.5
        max_cost_per_day_usd = 10.0

    policy = Policy()
    await BudgetGuard.reserve("t1", "r1", 0.4, policy)
    with pytest.raises(BudgetExceeded) as e:
        await BudgetGuard.reserve("t1", "r1", 0.2, policy)
    assert e.value.scope == "run"


async def test_token_quota_reservation_is_atomic(redis, monkeypatch):
    from app.platform_services import usage as usage_mod

    monkeypatch.setattr(usage_mod, "redis_client", redis)
    policy = type("Policy", (), {"max_tokens_per_day": 100})()
    first = UsageMeter("tenant", "workspace", "run-1")
    second = UsageMeter("tenant", "workspace", "run-2")

    await first.reserve_tokens(60, policy)
    with pytest.raises(Exception, match="daily token quota exceeded"):
        await second.reserve_tokens(60, policy)


async def test_token_reservation_survives_usage_snapshot(redis, monkeypatch):
    from app.platform_services import usage as usage_mod

    monkeypatch.setattr(usage_mod, "redis_client", redis)
    policy = type("Policy", (), {"max_tokens_per_day": 100})()
    original = UsageMeter("tenant", "workspace", "run-1")
    original.add_tokens({"prompt": 60, "completion": 0})
    await original.check_token_quota(policy)

    resumed = UsageMeter("tenant", "workspace", "run-1")
    resumed.restore(original.snapshot())
    await resumed.check_token_quota(policy)

    key = f"quota:tokens:tenant:{resumed._day()}"
    assert int(await redis.get(key)) == 60
