"""Phase 3 — Net Sales configuration tests.

Verify:
- Default net_sales_config is exposed by GET /settings.
- PUT /settings persists per-flag overrides.
- GET /dashboard returns net_sales = total_sales − sum(deducted line items),
  and the recomputation is correct for both default and custom flags.
"""
import os
import uuid

import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
API = f"{BASE_URL}/api"


def _register():
    email = f"u{uuid.uuid4().hex[:8]}@test.app"
    r = requests.post(
        f"{API}/auth/register",
        json={"name": "T", "email": email, "password": "test12345"},
    )
    r.raise_for_status()
    return r.json()["access_token"]


def test_default_net_sales_config_is_exposed():
    token = _register()
    h = {"Authorization": f"Bearer {token}"}
    r = requests.get(f"{API}/settings", headers=h)
    assert r.status_code == 200
    cfg = r.json().get("net_sales_config")
    assert cfg is not None, "net_sales_config missing from /settings"
    # Defaults
    assert cfg["deduct_payment_fees"] is True
    assert cfg["deduct_shipping"] is True
    assert cfg["deduct_deferred_shipping"] is False
    assert cfg["deduct_ads"] is True
    assert cfg["deduct_product_costs"] is True
    assert cfg["deduct_vat"] is False
    assert cfg["deduct_daily_expenses"] is False


def test_net_sales_config_persists_and_reads_back():
    token = _register()
    h = {"Authorization": f"Bearer {token}"}
    s = requests.get(f"{API}/settings", headers=h).json()
    payload = {
        "payment_methods": s["payment_methods"],
        "shipping_companies": s["shipping_companies"],
        "net_sales_config": {
            "deduct_payment_fees": False,
            "deduct_shipping": False,
            "deduct_deferred_shipping": True,
            "deduct_ads": False,
            "deduct_product_costs": False,
            "deduct_vat": True,
            "deduct_daily_expenses": True,
        },
    }
    r = requests.put(f"{API}/settings", json=payload, headers=h)
    assert r.status_code == 200

    cfg = requests.get(f"{API}/settings", headers=h).json()["net_sales_config"]
    assert cfg["deduct_payment_fees"] is False
    assert cfg["deduct_shipping"] is False
    assert cfg["deduct_deferred_shipping"] is True
    assert cfg["deduct_vat"] is True


def test_dashboard_returns_net_sales():
    token = _register()
    h = {"Authorization": f"Bearer {token}"}
    r = requests.get(f"{API}/dashboard", headers=h)
    assert r.status_code == 200
    body = r.json()
    assert "net_sales" in body["totals"], "totals.net_sales not in dashboard"
    assert "net_sales_config" in body, "net_sales_config not in dashboard"
    # Empty account: total_sales=0 → net_sales=0
    assert body["totals"]["total_sales"] == 0.0
    assert body["totals"]["net_sales"] == 0.0


def test_dashboard_net_sales_reflects_custom_config():
    """Push a webhook order, push daily-cost, then verify the math changes
    when we flip the deduct flags."""
    token = _register()
    h = {"Authorization": f"Bearer {token}"}

    # Get user's webhook token & enable a single payment method with no fees
    s = requests.get(f"{API}/settings", headers=h).json()
    requests.put(
        f"{API}/settings",
        json={
            "payment_methods": [
                {"name": "مدى", "commission_percent": 2, "fixed_fee": 1, "vat_percent": 15},
            ],
            "shipping_companies": [
                {"name": "سمسا", "cost_per_order": 25, "vat_percent": 15, "is_deferred": False},
            ],
        },
        headers=h,
    ).raise_for_status()

    wt = requests.get(f"{API}/webhook/settings", headers=h).json()
    requests.post(
        f"{API}/webhook/make/{wt['token']}",
        json={
            "order_number": "NS-1",
            "created_at": "2026-05-15T10:00:00+03:00",
            "total": 1000,
            "payment_method": "مدى",
            "shipping_company": "سمسا",
            "shipping_cost": 25,
            "order_status": "تم التوصيل",
        },
    ).raise_for_status()

    # Add an ads cost
    requests.post(
        f"{API}/daily-costs",
        json={"date": "2026-05-15", "snapchat_ads": 100, "product_costs": 200},
        headers=h,
    ).raise_for_status()

    # Default config: net_sales = total_sales - fees - shipping - ads - products
    d1 = requests.get(f"{API}/dashboard", headers=h).json()["totals"]
    expected_default = (
        d1["total_sales"]
        - d1["total_payment_fees"]
        - d1["regular_shipping_cost"]
        - d1["daily_ads_total"]
        - d1["daily_products_total"]
    )
    assert abs(d1["net_sales"] - round(expected_default, 2)) < 0.01

    # Flip: deduct nothing
    requests.put(
        f"{API}/settings",
        json={
            "payment_methods": s["payment_methods"],
            "shipping_companies": s["shipping_companies"],
            "net_sales_config": {
                "deduct_payment_fees": False,
                "deduct_shipping": False,
                "deduct_deferred_shipping": False,
                "deduct_ads": False,
                "deduct_product_costs": False,
                "deduct_vat": False,
                "deduct_daily_expenses": False,
            },
        },
        headers=h,
    ).raise_for_status()
    d2 = requests.get(f"{API}/dashboard", headers=h).json()["totals"]
    # With no deductions, net_sales should equal total_sales exactly.
    assert d2["net_sales"] == d2["total_sales"]
