"""Iter-91 Phase 1 — effective_product_cost helper tests.

Covers:
  - cancelled orders → 0 COGS
  - refunded (status) orders → 0 COGS
  - full refund via actual_refund_amount → 0 COGS
  - partial refund → proportional COGS reduction
  - confirmed orders → unchanged COGS
  - missing/zero total_product_cost → 0
"""
from order_status_policy import effective_product_cost


EMPTY: dict[str, str] = {}


def test_cancelled_order_zero_cogs():
    o = {
        "order_status": "ملغي",
        "total_product_cost": 300.0,
        "total_amount": 1000.0,
    }
    assert effective_product_cost(o, EMPTY) == 0.0


def test_refunded_status_zero_cogs():
    o = {
        "order_status": "مسترجع",
        "total_product_cost": 250.0,
        "total_amount": 800.0,
    }
    assert effective_product_cost(o, EMPTY) == 0.0


def test_full_refund_via_actual_field():
    o = {
        "order_status": "تم التوصيل",   # status didn't flip yet
        "total_product_cost": 200.0,
        "total_amount": 500.0,
        "actual_refund_amount": 500.0,
    }
    assert effective_product_cost(o, EMPTY) == 0.0


def test_partial_refund_proportional():
    # 200 SAR refunded out of 1000 → 20% share refunded
    # COGS 300 → effective 240
    o = {
        "order_status": "تم التوصيل",
        "total_product_cost": 300.0,
        "total_amount": 1000.0,
        "actual_partial_refund_amount": 200.0,
    }
    assert effective_product_cost(o, EMPTY) == 240.0


def test_partial_refund_half_order():
    o = {
        "order_status": "تم التوصيل",
        "total_product_cost": 100.0,
        "total_amount": 500.0,
        "actual_partial_refund_amount": 250.0,   # 50%
    }
    assert effective_product_cost(o, EMPTY) == 50.0


def test_partial_refund_caps_at_full():
    # Edge case: refund > gross (data error) should never go negative
    o = {
        "order_status": "تم التوصيل",
        "total_product_cost": 100.0,
        "total_amount": 200.0,
        "actual_partial_refund_amount": 9999.0,
    }
    assert effective_product_cost(o, EMPTY) == 0.0


def test_confirmed_order_unchanged():
    o = {
        "order_status": "تم التوصيل",
        "total_product_cost": 150.0,
        "total_amount": 500.0,
    }
    assert effective_product_cost(o, EMPTY) == 150.0


def test_pending_order_unchanged():
    # Pending orders aren't refunds — full COGS still applies for the
    # purpose of profit forecast (they may settle later).
    o = {
        "order_status": "قيد التنفيذ",
        "total_product_cost": 75.0,
        "total_amount": 200.0,
    }
    assert effective_product_cost(o, EMPTY) == 75.0


def test_zero_cost_returns_zero():
    o = {
        "order_status": "تم التوصيل",
        "total_product_cost": 0,
        "total_amount": 1000.0,
    }
    assert effective_product_cost(o, EMPTY) == 0.0


def test_missing_cost_returns_zero():
    o = {
        "order_status": "تم التوصيل",
        "total_amount": 1000.0,
    }
    assert effective_product_cost(o, EMPTY) == 0.0


def test_overrides_can_force_refund_category():
    # User mapped a custom status to "refunded" → COGS must zero out.
    o = {
        "order_status": "Returned by customer",
        "total_product_cost": 80.0,
        "total_amount": 200.0,
    }
    overrides = {"Returned by customer": "refunded"}
    assert effective_product_cost(o, overrides) == 0.0


def test_overrides_can_force_cancellation():
    o = {
        "order_status": "Dropped",
        "total_product_cost": 40.0,
        "total_amount": 120.0,
    }
    overrides = {"Dropped": "cancelled"}
    assert effective_product_cost(o, overrides) == 0.0
