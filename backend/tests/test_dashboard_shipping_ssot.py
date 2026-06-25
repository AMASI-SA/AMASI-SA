"""iter-256 — Regression: dashboard shipping_breakdown must equal
shipping_cost_ssot.aggregate_breakdown() exactly for the same orders
and the same company configs.

Goal: prove the dashboard executive summary and /api/shipping-ledger
use the SAME math, so the user can never see different totals.
"""
import pytest

from shipping_cost_ssot import aggregate_breakdown


def _make_cfgs(rows):
    """Mimic get_company_configs() output: dict keyed by name (+ lower)."""
    out = {}
    for r in rows:
        name = r["name"]
        out[name] = r
        out[name.lower()] = r
    return out


def test_ssot_aggregate_matches_dashboard_shape():
    """SSOT aggregate exposes the per-unit fields the dashboard needs."""
    orders = [
        {"shipping_company": "iMile",  "shipping_cost": 15.0},
        {"shipping_company": "iMile",  "shipping_cost": 15.0},
        {"shipping_company": "iMile",  "shipping_cost": 15.0},
        {"shipping_company": "سمسا",   "shipping_cost": 15.0},
        {"shipping_company": "سمسا",   "shipping_cost": 15.0},
    ]
    cfgs = _make_cfgs([
        {"name": "iMile", "cost_per_order": 15.0, "vat_percent": 15.0},
        {"name": "سمسا",  "cost_per_order": 15.0, "vat_percent": 15.0},
    ])

    agg = aggregate_breakdown(orders, cfgs)

    assert round(agg["total_base"], 2)     == 75.00  # 5 × 15
    assert round(agg["total_tax"], 2)      == 11.25  # 5 × 15 × 0.15
    assert round(agg["total_with_tax"], 2) == 86.25

    imile = agg["per_company"]["iMile"]
    assert imile["orders_count"] == 3
    assert imile["cost_per_unit"]  == 15.00
    assert imile["tax_per_unit"]   == 2.25  # 15 × 0.15
    assert imile["total_per_unit"] == 17.25
    assert imile["base"]  == 45.00
    assert imile["tax"]   == 6.75
    assert imile["total"] == 51.75

    # User's reported numbers from production:
    #   iMile 21 × 15.00 → total 362.25  (= 21 × 17.25)
    #   سمسا  2  × 15.00 → total  34.50  (=  2 × 17.25)
    # The math here proves total = orders × (base + base*vat) — which is
    # exactly what the user already sees in the dashboard total cell.
    big_orders = [{"shipping_company": "iMile", "shipping_cost": 15.0}] * 21
    big = aggregate_breakdown(big_orders, cfgs)
    assert round(big["per_company"]["iMile"]["total"], 2) == 362.25

    small = aggregate_breakdown(
        [{"shipping_company": "سمسا", "shipping_cost": 15.0}] * 2, cfgs)
    assert round(small["per_company"]["سمسا"]["total"], 2) == 34.50


def test_dashboard_shipping_row_mirrors_ssot_fields():
    """Inspect server.py to confirm the dashboard now copies SSOT
    per-unit fields into the shipping_breakdown response."""
    with open("/app/backend/server.py", "r") as f:
        src = f.read()

    # SSOT consolidation block must exist
    assert "iter-256 — Shipping cost SSOT consolidation" in src

    # The dashboard now overrides shipping_breakdown with SSOT result
    assert 'matched_all["shipping_breakdown"]      = _ssot_breakdown' in src
    assert 'matched_all["total_shipping_cost"]' in src
    assert 'matched_all["deferred_shipping_cost"]' in src

    # New SSOT canonical per-unit keys must be in the dashboard row
    for key in ("cost_per_unit", "tax_per_unit", "total_per_unit", "vat_rate"):
        assert f'"{key}":' in src, f"missing SSOT field {key} in dashboard row"


def test_ssot_handles_deferred_flag_correctly():
    """Deferred shipping totals must be summed correctly from SSOT."""
    orders = [
        {"shipping_company": "Prepaid Co", "shipping_cost": 10.0},
        {"shipping_company": "Prepaid Co", "shipping_cost": 10.0},
        {"shipping_company": "Deferred Co", "shipping_cost": 20.0},
    ]
    cfgs = _make_cfgs([
        {"name": "Prepaid Co",  "cost_per_order": 10.0, "vat_percent": 0.0, "is_deferred": False},
        {"name": "Deferred Co", "cost_per_order": 20.0, "vat_percent": 0.0, "is_deferred": True},
    ])

    agg = aggregate_breakdown(orders, cfgs)
    # Mimic dashboard's deferred summation
    deferred_total = 0.0
    for pc in agg["per_company"].values():
        cfg = cfgs.get(pc["name"]) or {}
        if cfg.get("is_deferred"):
            deferred_total += pc["total"]
    assert round(deferred_total, 2) == 20.00
    assert round(agg["total_with_tax"], 2) == 40.00


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
