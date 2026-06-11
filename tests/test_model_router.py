import pytest

from app.runtime.model_router import CircuitBreaker, ModelRouter


def test_estimate_cost():
    router = ModelRouter()
    cost = router.estimate_cost_usd("openai-default", "gpt-4o", 1000, 400)
    assert cost > 0


def test_circuit_breaker_opens_after_failures():
    cb = CircuitBreaker(fail_threshold=2, cooldown=60.0)
    cb.record_failure()
    cb.record_failure()
    assert cb.state == "open"
    assert not cb.allow()
