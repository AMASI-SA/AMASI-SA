"""Tests — hide_inferred_date_orders toggle.

User-facing toggle in Settings: when enabled, dashboard and balances
endpoints exclude orders whose date was inferred from received_at
(Make.com webhook without created_at). Useful when the merchant wants
"accurate-date orders only" until they fix their Make.com mapping or
re-upload a Salla Excel export.
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


def test_settings_default_hide_inferred_is_false():
    token = _register()
    h = {"Authorization": f"Bearer {token}"}
    r = requests.get(f"{API}/settings", headers=h).json()
    assert r.get("hide_inferred_date_orders") is False


def test_toggle_persists():
    token = _register()
    h = {"Authorization": f"Bearer {token}"}
    s = requests.get(f"{API}/settings", headers=h).json()
    requests.put(
        f"{API}/settings",
        json={
            "payment_methods": s["payment_methods"],
            "shipping_companies": s["shipping_companies"],
            "hide_inferred_date_orders": True,
        },
        headers=h,
    ).raise_for_status()
    assert requests.get(f"{API}/settings", headers=h).json()["hide_inferred_date_orders"] is True


def test_dashboard_hides_inferred_orders_when_toggle_on():
    """One inferred order + one authoritative order. Toggle ON → only authoritative counted."""
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).date()
    first = today.replace(day=1).isoformat()
    last = today.isoformat()

    token = _register()
    h = {"Authorization": f"Bearer {token}"}
    wt = requests.get(f"{API}/webhook/settings", headers=h).json()

    # Order A: inferred (no created_at)
    requests.post(
        f"{API}/webhook/make/{wt['token']}",
        json={"order_number": "INF-A", "total": 100, "payment_method": "مدى",
              "order_status": "تم التوصيل"},
    ).raise_for_status()
    # Order B: authoritative (with created_at = today)
    requests.post(
        f"{API}/webhook/make/{wt['token']}",
        json={"order_number": "AUTH-B",
              "created_at": f"{today.isoformat()}T10:00:00+03:00",
              "total": 300, "payment_method": "مدى",
              "order_status": "تم التوصيل"},
    ).raise_for_status()

    # Toggle OFF (default) → both counted
    r1 = requests.get(
        f"{API}/dashboard?from_date={first}&to_date={last}",
        headers=h,
    ).json()
    assert r1["totals"]["total_orders"] == 2
    assert r1["hide_inferred_date_orders"] is False

    # Turn toggle ON
    s = requests.get(f"{API}/settings", headers=h).json()
    requests.put(
        f"{API}/settings",
        json={
            "payment_methods": s["payment_methods"],
            "shipping_companies": s["shipping_companies"],
            "hide_inferred_date_orders": True,
        },
        headers=h,
    ).raise_for_status()

    r2 = requests.get(
        f"{API}/dashboard?from_date={first}&to_date={last}",
        headers=h,
    ).json()
    assert r2["totals"]["total_orders"] == 1  # only AUTH-B
    assert r2["totals"]["total_sales"] == 300.0
    assert r2["hide_inferred_date_orders"] is True


def test_balances_endpoint_also_respects_toggle():
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).date().isoformat()

    token = _register()
    h = {"Authorization": f"Bearer {token}"}
    wt = requests.get(f"{API}/webhook/settings", headers=h).json()
    requests.post(
        f"{API}/webhook/make/{wt['token']}",
        json={"order_number": "BAL-INF", "total": 100, "payment_method": "مدى",
              "shipping_company": "سمسا", "shipping_cost": 25,
              "order_status": "تم التوصيل"},
    ).raise_for_status()
    requests.post(
        f"{API}/webhook/make/{wt['token']}",
        json={"order_number": "BAL-AUTH",
              "created_at": f"{today}T10:00:00+03:00",
              "total": 200, "payment_method": "مدى",
              "shipping_company": "سمسا", "shipping_cost": 25,
              "order_status": "تم التوصيل"},
    ).raise_for_status()

    # Turn toggle ON
    s = requests.get(f"{API}/settings", headers=h).json()
    requests.put(
        f"{API}/settings",
        json={
            "payment_methods": s["payment_methods"],
            "shipping_companies": s["shipping_companies"],
            "hide_inferred_date_orders": True,
        },
        headers=h,
    ).raise_for_status()

    r = requests.get(f"{API}/balances", headers=h).json()
    # Only BAL-AUTH should be counted in balances (1 order through shipping)
    assert r["shipping"]["approved_orders"] + r["shipping"]["unapproved_orders"] == 1
