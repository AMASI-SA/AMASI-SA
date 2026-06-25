"""Shipping cost SSOT — base + tax + total unification tests.

Validates the new mandate:
  • Every report that needs shipping cost goes through
    shipping_cost_ssot.shipping_breakdown() and uses TOTAL (= base + tax).
  • Per-shipment fields exposed: base, tax, total, vat_rate, source.
  • Historical data preservation — NO retroactive recompute if the
    cfg lookup misses (no default VAT applied).
"""
from __future__ import annotations

import sys
sys.path.insert(0, "/app/backend")

from shipping_cost_ssot import (
    shipping_breakdown,
    aggregate_breakdown,
)


# ─────────────────────────────────────────────────────────────────────
# 1. shipping_breakdown — pure logic
# ─────────────────────────────────────────────────────────────────────
def test_breakdown_uses_order_shipping_cost_as_base():
    order = {"shipping_company": "Aramex", "shipping_cost": 20}
    cfgs = {"Aramex": {"vat_rate": 0.15, "cost_per_order": 999}}
    bd = shipping_breakdown(order, cfgs)
    assert bd["base"] == 20.0
    assert bd["tax"] == 3.0
    assert bd["total"] == 23.0
    assert bd["source"] == "order"
    assert bd["vat_rate"] == 0.15


def test_breakdown_falls_back_to_company_cost_when_order_has_no_value():
    order = {"shipping_company": "SMSA", "shipping_cost": 0}
    cfgs = {"SMSA": {"cost_per_order": 18, "vat_rate": 0.15}}
    bd = shipping_breakdown(order, cfgs)
    assert bd["base"] == 18.0
    assert bd["tax"] == 2.7
    assert bd["total"] == 20.7
    assert bd["source"] == "company_config"


def test_breakdown_no_vat_means_zero_tax_no_default_applied():
    """CRITICAL — no default VAT% is fabricated."""
    order = {"shipping_company": "X", "shipping_cost": 25}
    cfgs = {"X": {"cost_per_order": 25}}      # no vat_rate set
    bd = shipping_breakdown(order, cfgs)
    assert bd["base"] == 25.0
    assert bd["tax"] == 0.0
    assert bd["total"] == 25.0
    assert bd["vat_rate"] == 0.0


def test_breakdown_unknown_company_returns_none_source():
    order = {"shipping_company": "Unknown", "shipping_cost": 0}
    bd = shipping_breakdown(order, {})
    assert bd["base"] == 0.0
    assert bd["tax"] == 0.0
    assert bd["total"] == 0.0
    assert bd["source"] == "none"


def test_breakdown_order_cost_overrides_company_cost():
    """The merchant may negotiate a per-shipment cost (sent from Salla
    on the order); this should beat the static company config."""
    order = {"shipping_company": "Aramex", "shipping_cost": 30}
    cfgs = {"Aramex": {"cost_per_order": 25, "vat_rate": 0.15}}
    bd = shipping_breakdown(order, cfgs)
    assert bd["base"] == 30.0
    assert bd["total"] == 34.5   # 30 + (30 * 0.15)
    assert bd["source"] == "order"


def test_breakdown_handles_string_shipping_cost():
    order = {"shipping_company": "Aramex", "shipping_cost": "20.50"}
    cfgs = {"Aramex": {"vat_rate": 0.15}}
    bd = shipping_breakdown(order, cfgs)
    assert bd["base"] == 20.5
    assert bd["tax"] in (3.07, 3.08)  # banker's-round can yield 3.07
    assert abs(bd["total"] - (20.5 + bd["tax"])) < 0.01


def test_breakdown_handles_malformed_vat_rate():
    order = {"shipping_company": "Aramex", "shipping_cost": 20}
    cfgs = {"Aramex": {"vat_rate": "not_a_number"}}
    bd = shipping_breakdown(order, cfgs)
    assert bd["base"] == 20.0
    assert bd["tax"] == 0.0
    assert bd["total"] == 20.0


def test_breakdown_accepts_vat_percent_field():
    """The /shipping/settings UI saves the rate as vat_percent (0-100
    scale). SSOT must convert it to a decimal automatically."""
    order = {"shipping_company": "Aramex", "shipping_cost": 20}
    cfgs = {"Aramex": {"vat_percent": 15.0}}
    bd = shipping_breakdown(order, cfgs)
    assert bd["base"] == 20.0
    assert bd["tax"] == 3.0
    assert bd["total"] == 23.0
    assert bd["vat_rate"] == 0.15


def test_breakdown_vat_rate_beats_vat_percent_when_both_present():
    """vat_rate is the legacy decimal field. If a config has both,
    vat_rate wins (it was set deliberately by the merchant)."""
    order = {"shipping_company": "Aramex", "shipping_cost": 20}
    cfgs = {"Aramex": {"vat_rate": 0.10, "vat_percent": 15.0}}
    bd = shipping_breakdown(order, cfgs)
    assert bd["vat_rate"] == 0.10
    assert bd["tax"] == 2.0
    assert bd["total"] == 22.0


# ─────────────────────────────────────────────────────────────────────
# 2. aggregate_breakdown — list aggregation
# ─────────────────────────────────────────────────────────────────────
def test_aggregate_breakdown_sums_correctly():
    orders = [
        {"shipping_company": "Aramex", "shipping_cost": 20},
        {"shipping_company": "Aramex", "shipping_cost": 20},
        {"shipping_company": "SMSA",   "shipping_cost": 18},
    ]
    cfgs = {
        "Aramex": {"vat_rate": 0.15},
        "SMSA":   {"vat_rate": 0.15},
    }
    agg = aggregate_breakdown(orders, cfgs)
    assert agg["orders_count"] == 3
    assert agg["total_base"] == 58.0     # 20+20+18
    assert agg["total_tax"]  == 8.7      # 3+3+2.7
    assert agg["total_with_tax"] == 66.7
    # Per company numbers
    assert agg["per_company"]["Aramex"]["orders_count"] == 2
    assert agg["per_company"]["Aramex"]["base"] == 40.0
    assert agg["per_company"]["Aramex"]["tax"] == 6.0
    assert agg["per_company"]["Aramex"]["total"] == 46.0
    assert agg["per_company"]["Aramex"]["cost_per_unit"] == 20.0
    assert agg["per_company"]["Aramex"]["tax_per_unit"] == 3.0
    assert agg["per_company"]["Aramex"]["total_per_unit"] == 23.0
    assert agg["per_company"]["SMSA"]["orders_count"] == 1


def test_aggregate_with_mixed_vat_rates():
    """One company VAT-able (15%), the other not (0%)."""
    orders = [
        {"shipping_company": "Aramex",  "shipping_cost": 20},
        {"shipping_company": "Foreign", "shipping_cost": 50},
    ]
    cfgs = {
        "Aramex":  {"vat_rate": 0.15},
        "Foreign": {"vat_rate": 0.0},
    }
    agg = aggregate_breakdown(orders, cfgs)
    assert agg["total_base"]     == 70.0
    assert agg["total_tax"]      == 3.0
    assert agg["total_with_tax"] == 73.0


# ─────────────────────────────────────────────────────────────────────
# 3. balances.compute_balances now uses SSOT (base+tax)
# ─────────────────────────────────────────────────────────────────────
def test_compute_balances_uses_total_with_tax():
    from balances import compute_balances

    orders = [
        {"order_status": "delivered", "shipping_company": "Aramex",
         "shipping_cost": 20, "total_amount": 100,
         "payment_method": "cod"},
        {"order_status": "delivered", "shipping_company": "Aramex",
         "shipping_cost": 20, "total_amount": 100,
         "payment_method": "cod"},
    ]
    cfgs = {"Aramex": {"vat_rate": 0.15}}
    b = compute_balances(
        orders, shipping_approved=["delivered"], cod_approved=["delivered"],
        company_cfgs=cfgs,
    )
    # 2 orders × 23 SAR (base+tax) = 46
    assert b["shipping"]["total_approved"] == 46.0


def test_compute_balances_without_cfgs_falls_back_to_base():
    """Legacy callers that don't pass cfgs get base-only (no fake VAT)."""
    from balances import compute_balances

    orders = [{"order_status": "delivered", "shipping_company": "Aramex",
                "shipping_cost": 20, "total_amount": 100,
                "payment_method": "cod"}]
    b = compute_balances(
        orders, shipping_approved=["delivered"], cod_approved=["delivered"],
    )
    # With no cfgs → vat_rate=0 → total=20 (no fake default VAT)
    assert b["shipping"]["total_approved"] == 20.0
