from datetime import datetime

from app.platform_services.billing_reporter import (
    billable_quantity,
    usage_batch_key,
)


def test_billable_quantity_rounds_up_without_losing_usage():
    assert billable_quantity("tokens", 1) == 1
    assert billable_quantity("tokens", 1000) == 1
    assert billable_quantity("tokens", 1001) == 2
    assert billable_quantity("sandbox_seconds", 61) == 2


def test_usage_batch_key_is_stable_for_same_window():
    start = datetime(2026, 1, 1, 0, 0, 0)
    end = datetime(2026, 1, 1, 1, 0, 0)

    first = usage_batch_key("tenant-1", "tokens", start, end)
    second = usage_batch_key("tenant-1", "tokens", start, end)

    assert first == second
    assert len(first) <= 255

