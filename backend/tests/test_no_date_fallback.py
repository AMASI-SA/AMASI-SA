"""Regression tests — Make.com webhook date handling.

Behavior:
- Order WITH created_at → order_date = parsed date, order_date_inferred = False.
- Order WITHOUT created_at → order_date = received_at[:10], order_date_inferred = True.
  This makes the order appear in dashboard immediately, with an "approximate
  date" badge. If the same order arrives later with a real created_at, the
  inferred date is REPLACED by the authoritative one.
- Stats expose `orders_inferred_date` so the merchant can fix Make.com mapping.
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


def test_webhook_without_created_at_uses_today_as_inferred():
    """Order without created_at → order_date = today (inferred), shows in dashboard."""
    token = _register()
    h = {"Authorization": f"Bearer {token}"}
    wt = requests.get(f"{API}/webhook/settings", headers=h).json()
    resp = requests.post(
        f"{API}/webhook/make/{wt['token']}",
        json={
            "order_number": "INFER-1",
            "total": 100,
            "payment_method": "مدى",
            "order_status": "تم التوصيل",
        },
    )
    resp.raise_for_status()
    body = resp.json()
    assert body["accepted"] == 1
    assert body.get("inferred_date") == 1

    orders = requests.get(f"{API}/webhook/orders?limit=10", headers=h).json()["orders"]
    target = next((o for o in orders if o["order_number"] == "INFER-1"), None)
    assert target is not None
    assert target.get("order_date") is not None  # has a date now
    assert target.get("order_date_inferred") is True


def test_inferred_order_appears_in_dashboard_for_current_month():
    """Inferred-date order MUST appear in dashboard date filter for today."""
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).date()
    first = today.replace(day=1).isoformat()
    last = today.isoformat()

    token = _register()
    h = {"Authorization": f"Bearer {token}"}
    wt = requests.get(f"{API}/webhook/settings", headers=h).json()
    requests.post(
        f"{API}/webhook/make/{wt['token']}",
        json={
            "order_number": "INFER-DASH-1",
            "total": 500,
            "payment_method": "مدى",
            "order_status": "تم التوصيل",
        },
    ).raise_for_status()

    r = requests.get(
        f"{API}/dashboard?from_date={first}&to_date={last}",
        headers=h,
    ).json()
    assert r["totals"]["total_orders"] == 1


def test_authoritative_date_overrides_inferred():
    """Same order, later, arrives WITH created_at → inferred date replaced."""
    token = _register()
    h = {"Authorization": f"Bearer {token}"}
    wt = requests.get(f"{API}/webhook/settings", headers=h).json()

    # First webhook: no date → inferred
    requests.post(
        f"{API}/webhook/make/{wt['token']}",
        json={"order_number": "FIX-LATER-1", "total": 200, "payment_method": "مدى"},
    ).raise_for_status()

    # Second webhook: same order, now with created_at
    requests.post(
        f"{API}/webhook/make/{wt['token']}",
        json={
            "order_number": "FIX-LATER-1",
            "created_at": "2026-02-10T10:00:00+03:00",
            "total": 200,
            "payment_method": "مدى",
        },
    ).raise_for_status()

    orders = requests.get(f"{API}/webhook/orders?limit=10", headers=h).json()["orders"]
    target = next((o for o in orders if o["order_number"] == "FIX-LATER-1"), None)
    assert target is not None
    assert target.get("order_date") == "2026-02-10"
    assert target.get("order_date_inferred") is False


def test_stats_exposes_inferred_count():
    token = _register()
    h = {"Authorization": f"Bearer {token}"}
    wt = requests.get(f"{API}/webhook/settings", headers=h).json()
    # 2 inferred + 1 authoritative
    requests.post(
        f"{API}/webhook/make/{wt['token']}",
        json={"order_number": "INF-A", "total": 50, "payment_method": "مدى"},
    ).raise_for_status()
    requests.post(
        f"{API}/webhook/make/{wt['token']}",
        json={"order_number": "INF-B", "total": 60, "payment_method": "مدى"},
    ).raise_for_status()
    requests.post(
        f"{API}/webhook/make/{wt['token']}",
        json={
            "order_number": "GOOD-DATE",
            "created_at": "2026-04-10T00:00:00+03:00",
            "total": 70,
            "payment_method": "مدى",
        },
    ).raise_for_status()

    stats = requests.get(f"{API}/webhook/stats", headers=h).json()
    assert stats["orders_inferred_date"] == 2
    assert stats["orders_missing_date"] == 0  # no truly missing
    assert stats["by_source"]["make"] == 3


def test_order_with_correct_created_at_lands_in_right_month():
    """Authoritative date → exact month, NOT current month."""
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
