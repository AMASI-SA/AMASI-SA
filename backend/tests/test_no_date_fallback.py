"""Regression test — webhook MUST NOT fall back to "today" for orders
without a created_at field. Previously this silently labeled
March/April orders that Make.com forwarded today (without created_at)
as "May orders", inflating the current month's KPIs.

Expected new behavior:
- Order without created_at → order_date=None (or absent).
- Order does NOT appear in date-filtered dashboard queries.
- Stats expose `orders_missing_date` counter so the merchant can fix
  their Make.com mapping.
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


def test_webhook_no_date_fallback_to_today():
    """Send an order without created_at. order_date MUST be None, not today."""
    token = _register()
    h = {"Authorization": f"Bearer {token}"}
    wt = requests.get(f"{API}/webhook/settings", headers=h).json()
    resp = requests.post(
        f"{API}/webhook/make/{wt['token']}",
        json={
            "order_number": "NO-DATE-ORDER-1",
            "total": 100,
            "payment_method": "مدى",
            "order_status": "بإنتظار المراجعة",
        },
    )
    resp.raise_for_status()
    body = resp.json()
    assert body["accepted"] == 1
    assert body.get("without_date") == 1  # response exposes the counter

    # Verify the stored order has order_date=None
    orders = requests.get(f"{API}/webhook/orders?limit=10", headers=h).json()["orders"]
    target = next((o for o in orders if o["order_number"] == "NO-DATE-ORDER-1"), None)
    assert target is not None
    assert target.get("order_date") in (None, "")


def test_no_date_order_excluded_from_date_filter():
    """An order without created_at must NOT appear in date-filtered dashboard."""
    token = _register()
    h = {"Authorization": f"Bearer {token}"}
    wt = requests.get(f"{API}/webhook/settings", headers=h).json()
    requests.post(
        f"{API}/webhook/make/{wt['token']}",
        json={
            "order_number": "NO-DATE-2",
            "total": 999,
            "payment_method": "مدى",
            "order_status": "تم التوصيل",
        },
    ).raise_for_status()

    # Dashboard with any date filter must NOT include this order
    r = requests.get(
        f"{API}/dashboard?from_date=2026-05-01&to_date=2026-05-31",
        headers=h,
    )
    r.raise_for_status()
    t = r.json()["totals"]
    assert t["total_orders"] == 0
    assert t["total_sales"] == 0.0


def test_stats_exposes_orders_missing_date():
    token = _register()
    h = {"Authorization": f"Bearer {token}"}
    wt = requests.get(f"{API}/webhook/settings", headers=h).json()
    requests.post(
        f"{API}/webhook/make/{wt['token']}",
        json={
            "order_number": "ND-A",
            "total": 50,
            "payment_method": "مدى",
        },
    ).raise_for_status()
    requests.post(
        f"{API}/webhook/make/{wt['token']}",
        json={
            "order_number": "WITH-DATE-X",
            "created_at": "2026-04-10T00:00:00+03:00",
            "total": 60,
            "payment_method": "مدى",
        },
    ).raise_for_status()

    stats = requests.get(f"{API}/webhook/stats", headers=h).json()
    assert stats["orders_missing_date"] == 1
    assert stats["by_source"]["make"] == 2


def test_orders_missing_date_endpoint():
    token = _register()
    h = {"Authorization": f"Bearer {token}"}
    wt = requests.get(f"{API}/webhook/settings", headers=h).json()
    requests.post(
        f"{API}/webhook/make/{wt['token']}",
        json={"order_number": "MISS-1", "total": 10, "payment_method": "مدى"},
    ).raise_for_status()
    requests.post(
        f"{API}/webhook/make/{wt['token']}",
        json={"order_number": "MISS-2", "total": 20, "payment_method": "مدى"},
    ).raise_for_status()
    requests.post(
        f"{API}/webhook/make/{wt['token']}",
        json={
            "order_number": "HAS-DATE",
            "created_at": "2026-03-15T00:00:00+03:00",
            "total": 30,
            "payment_method": "مدى",
        },
    ).raise_for_status()

    r = requests.get(f"{API}/webhook/orders-missing-date?limit=50", headers=h)
    r.raise_for_status()
    body = r.json()
    assert body["total"] == 2
    order_numbers = sorted(o["order_number"] for o in body["orders"])
    assert order_numbers == ["MISS-1", "MISS-2"]


def test_order_with_correct_created_at_lands_in_right_month():
    """Send a March order. It must appear in March, NOT in the current month."""
    token = _register()
    h = {"Authorization": f"Bearer {token}"}
    wt = requests.get(f"{API}/webhook/settings", headers=h).json()
    requests.post(
        f"{API}/webhook/make/{wt['token']}",
        json={
            "order_number": "MAR-1",
            "created_at": "2026-03-12T10:00:00+03:00",
            "total": 250,
            "payment_method": "مدى",
            "order_status": "تم التوصيل",
        },
    ).raise_for_status()

    # March filter → 1 order
    r_mar = requests.get(
        f"{API}/dashboard?from_date=2026-03-01&to_date=2026-03-31",
        headers=h,
    ).json()
    assert r_mar["totals"]["total_orders"] == 1

    # May filter → 0 orders (this was the bug we just fixed)
    r_may = requests.get(
        f"{API}/dashboard?from_date=2026-05-01&to_date=2026-05-31",
        headers=h,
    ).json()
    assert r_may["totals"]["total_orders"] == 0
