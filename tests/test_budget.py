import pytest

from app.runtime.budget import BudgetExceeded, BudgetGuard


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
