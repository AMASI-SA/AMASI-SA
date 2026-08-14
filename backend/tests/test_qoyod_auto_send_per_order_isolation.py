"""Regression coverage for Plan-B per-order failure isolation."""
from qoyod_auto_per_order_isolation import ALL_FAILURES_ARE_ORDER_LOCAL


def test_every_runtime_failure_is_order_local():
    assert ALL_FAILURES_ARE_ORDER_LOCAL is True
