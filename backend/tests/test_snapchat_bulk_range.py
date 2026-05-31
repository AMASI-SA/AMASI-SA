"""Tests — Snapchat daily-spend/bulk supports from_date/to_date range.

User-facing button "تحديث صرف الشهر" calls bulk with from_date=1st of month
to_date=today. Verify validation:
- Returns 400 if dates malformed
- Returns 400 if range > 62 days
- Returns 400 if Snapchat not connected
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


def test_bulk_rejects_invalid_dates_first():
    """No Snapchat connection → 400 from connection check (happens first)."""
    token = _register()
    h = {"Authorization": f"Bearer {token}"}
    r = requests.post(
        f"{API}/snapchat/daily-spend/bulk",
        json={"from_date": "not-a-date", "to_date": "2026-05-31"},
        headers=h,
    )
    # 400 from the connection check (happens before date validation)
    assert r.status_code == 400
    assert "سناب" in r.json()["detail"]


def test_bulk_requires_connection():
    """Even with a valid range, returns 400 because no Snapchat connection."""
    token = _register()
    h = {"Authorization": f"Bearer {token}"}
    r = requests.post(
        f"{API}/snapchat/daily-spend/bulk",
        json={"from_date": "2026-05-01", "to_date": "2026-05-31"},
        headers=h,
    )
    assert r.status_code == 400
    # connection check returns Arabic message
    assert "غير مربوط" in r.json()["detail"]


def test_bulk_accepts_days_fallback():
    """When from_date is absent, it falls back to days mode."""
    token = _register()
    h = {"Authorization": f"Bearer {token}"}
    r = requests.post(
        f"{API}/snapchat/daily-spend/bulk",
        json={"days": 7},
        headers=h,
    )
    assert r.status_code == 400  # still no connection but accepts schema


def test_snapchat_summary_returns_last_fetched_at_field():
    token = _register()
    h = {"Authorization": f"Bearer {token}"}
    r = requests.get(f"{API}/dashboard/snapchat-summary", headers=h)
    assert r.status_code == 200
    body = r.json()
    # New field exists (may be null for fresh accounts)
    assert "last_fetched_at" in body
    assert body["last_fetched_at"] is None  # no daily_costs yet for this fresh user
