"""Tests — Snapchat summary dashboard endpoint.

GET /api/dashboard/snapchat-summary returns:
- today / month / last_30d blocks each with {spend, orders, revenue, roas}
- 30-day history array for sparkline
"""
import os
import uuid
from datetime import datetime, timezone, timedelta

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


def test_summary_empty_account_returns_zeros():
    token = _register()
    h = {"Authorization": f"Bearer {token}"}
    r = requests.get(f"{API}/dashboard/snapchat-summary", headers=h)
    assert r.status_code == 200
    body = r.json()
    assert body["today"]["spend"] == 0.0
    assert body["today"]["orders"] == 0
    assert body["today"]["revenue"] == 0.0
    assert body["today"]["roas"] == 0.0
    assert body["month"]["spend"] == 0.0
    assert body["last_30d"]["spend"] == 0.0
    assert len(body["history"]) == 30


def test_summary_includes_snapchat_spend_and_orders():
    """Log a daily_cost with snapchat_ads, post a Make order today,
    expect summary to reflect both."""
    today = datetime.now(timezone.utc).date().isoformat()
    token = _register()
    h = {"Authorization": f"Bearer {token}"}

    # 1) Log a snapchat spend for today
    requests.post(
        f"{API}/daily-costs",
        json={"date": today, "snapchat_ads": 50, "snapchat_ads_2": 25},
        headers=h,
    ).raise_for_status()

    # 2) Push a Make order with today's date
    wt = requests.get(f"{API}/webhook/settings", headers=h).json()
    requests.post(
        f"{API}/webhook/make/{wt['token']}",
        json={
            "order_number": "SNAP-DAY-1",
            "created_at": f"{today}T10:00:00+03:00",
            "total": 300,
            "payment_method": "مدى",
            "order_status": "تم التوصيل",
        },
    ).raise_for_status()

    r = requests.get(f"{API}/dashboard/snapchat-summary", headers=h).json()
    assert r["today"]["spend"] == 75.0  # 50 + 25
    assert r["today"]["spend_usd"] == round(75.0 / 3.752, 2)
    assert r["usd_rate"] == 3.752
    assert r["today"]["orders"] == 1
    assert r["today"]["revenue"] == 300.0
    assert r["today"]["roas"] == 4.0  # 300/75

    # Month aggregates the same (single day)
    assert r["month"]["spend"] == 75.0
    assert r["month"]["revenue"] == 300.0
    assert r["last_30d"]["spend"] == 75.0


def test_summary_history_has_30_days():
    token = _register()
    h = {"Authorization": f"Bearer {token}"}
    r = requests.get(f"{API}/dashboard/snapchat-summary", headers=h).json()
    history = r["history"]
    assert len(history) == 30
    assert history[-1]["date"] == datetime.now(timezone.utc).date().isoformat()
    expected_first = (datetime.now(timezone.utc).date() - timedelta(days=29)).isoformat()
    assert history[0]["date"] == expected_first
    # All entries have a `spend` numeric field
    assert all(isinstance(h["spend"], (int, float)) for h in history)


def test_summary_respects_hide_inferred_toggle():
    """When user enables hide_inferred_date_orders, an order without
    created_at should NOT count toward today's orders/revenue."""
    token = _register()
    h = {"Authorization": f"Bearer {token}"}

    # Push an inferred-date order (no created_at)
    wt = requests.get(f"{API}/webhook/settings", headers=h).json()
    requests.post(
        f"{API}/webhook/make/{wt['token']}",
        json={
            "order_number": "INF-SNAP",
            "total": 500,
            "payment_method": "مدى",
            "order_status": "تم التوصيل",
        },
    ).raise_for_status()

    # Toggle hide_inferred ON
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

    r = requests.get(f"{API}/dashboard/snapchat-summary", headers=h).json()
    assert r["today"]["orders"] == 0
    assert r["today"]["revenue"] == 0.0
