import pytest

from app.risk.expression import ExpressionError, evaluate

CTX = {"tool_call_rate": 42.0, "error_rate": 0.31, "cost_per_min": 1.8,
       "distinct_tools": 7}


class TestEval:
    def test_comparison_and_logic(self):
        assert evaluate("tool_call_rate > 30 and error_rate > 0.2", CTX) is True
        assert evaluate("cost_per_min > 5 or distinct_tools >= 10", CTX) is False

    def test_arithmetic(self):
        assert evaluate("cost_per_min * 60 > 100", CTX) is True


class TestInjectionRejection:
    @pytest.mark.parametrize("expr", [
        "__import__('os').system('id')",
        "exec('1')",
        "tool_call_rate.__class__",
    ])
    def test_blocked(self, expr):
        with pytest.raises(ExpressionError):
            evaluate(expr, CTX)
