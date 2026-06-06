"""Iter-77 — Refund-monitor alert with date period filters."""
import os
import sys
from datetime import date, datetime, timedelta

import pytest
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from refunds_alert_routes import _resolve_period  # noqa: E402


BASE_URL = os.environ.get(
    "REACT_APP_BACKEND_URL",
    "https://salla-analytics.preview.emergentagent.com",
).rstrip("/")
EMAIL = "amasi.jewelery@gmail.com"
PASSWORD = "10201917"
SAMPLES = "/tmp/settlements_samples"


@pytest.fixture(scope="module")
def auth():
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login", json={"email": EMAIL, "password": PASSWORD}, timeout=15)
    assert r.status_code == 200
    s.headers.update({"Authorization": f"Bearer {r.json()['access_token']}"})
    return s


# ── 1. Period resolution (pure helper) ────────────────────────────────
def test_today_period():
    f, t, lbl = _resolve_period("today")
    today = date.today().isoformat()
    assert f == today and t == today
    assert lbl == "اليوم"


def test_yesterday_period():
    f, t, lbl = _resolve_period("yesterday")
    y = (date.today() - timedelta(days=1)).isoformat()
    assert f == y and t == y
    assert lbl == "بالأمس"


def test_this_month_period():
    f, t, lbl = _resolve_period("this_month")
    first = date.today().replace(day=1).isoformat()
    assert f == first
    assert lbl == "هذا الشهر"


def test_last_month_period():
    f, t, lbl = _resolve_period("last_month")
    today = date.today()
    first_of_this = today.replace(day=1)
    last_of_prev = first_of_this - timedelta(days=1)
    first_of_prev = last_of_prev.replace(day=1)
    assert f == first_of_prev.isoformat()
    assert t == last_of_prev.isoformat()
    assert lbl == "الشهر الماضي"


def test_last_30d_period():
    f, t, lbl = _resolve_period("last_30d")
    assert lbl == "آخر 30 يوم"
    # Window length = 30 days inclusive (29 day step + today)
    f_d = datetime.strptime(f, "%Y-%m-%d").date()
    t_d = datetime.strptime(t, "%Y-%m-%d").date()
    assert (t_d - f_d).days == 29


def test_this_year_period():
    f, t, lbl = _resolve_period("this_year")
    assert lbl == "السنة الحالية"
    assert f == date(date.today().year, 1, 1).isoformat()


def test_custom_period_with_dates():
    f, t, lbl = _resolve_period("custom", "2025-01-01", "2025-12-31")
    assert f == "2025-01-01" and t == "2025-12-31"
    assert "2025-01-01" in lbl and "2025-12-31" in lbl


def test_custom_period_missing_dates_raises():
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        _resolve_period("custom", None, None)
    assert exc.value.status_code == 400


def test_invalid_period_raises():
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        _resolve_period("never_heard_of_this", None, None)
    assert exc.value.status_code == 400


# ── 2. Live endpoint shape & filtering ────────────────────────────────
def test_endpoint_default_last_30d(auth):
    r = auth.get(f"{BASE_URL}/api/reports/refunds-alert", timeout=15)
    assert r.status_code == 200
    body = r.json()
    assert body["period"] == "last_30d"
    assert body["label"] == "آخر 30 يوم"
    for k in ("from_date", "to_date", "summary", "orders", "by_payment_method"):
        assert k in body
    s = body["summary"]
    for k in ("refund_orders_count", "total_orders_in_period", "refund_rate_pct",
              "total_refund_full", "total_refund_partial", "total_refund_amount"):
        assert k in s


def test_endpoint_today(auth):
    r = auth.get(f"{BASE_URL}/api/reports/refunds-alert?period=today", timeout=15)
    assert r.status_code == 200
    body = r.json()
    assert body["period"] == "today"
    today = date.today().isoformat()
    assert body["from_date"] == today
    assert body["to_date"] == today


def test_endpoint_custom_requires_dates(auth):
    r = auth.get(f"{BASE_URL}/api/reports/refunds-alert?period=custom", timeout=15)
    assert r.status_code == 400


def test_endpoint_custom_with_dates(auth):
    r = auth.get(
        f"{BASE_URL}/api/reports/refunds-alert",
        params={"period": "custom", "from_date": "2025-06-01", "to_date": "2025-06-30"},
        timeout=15,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["from_date"] == "2025-06-01"
    assert body["to_date"] == "2025-06-30"


# ── 3. After uploading the refund file, alert picks up the refunds ───
def test_alert_picks_up_uploaded_refunds(auth):
    # Cleanup any existing uploads
    files = auth.get(f"{BASE_URL}/api/payment-settlements", timeout=10).json().get("files", [])
    for f in files:
        auth.delete(f"{BASE_URL}/api/payment-settlements/{f['id']}", timeout=10)

    try:
        with open(f"{SAMPLES}/salla_refund.xlsx", "rb") as fh:
            up = auth.post(
                f"{BASE_URL}/api/payment-settlements/upload",
                files={"file": ("salla_refund.xlsx", fh, "application/vnd.ms-excel")},
                timeout=30,
            )
        assert up.status_code == 200, up.text

        # Query alert for this year — should see at least 3 refund orders
        r = auth.get(f"{BASE_URL}/api/reports/refunds-alert?period=this_year", timeout=15)
        assert r.status_code == 200
        body = r.json()
        s = body["summary"]
        assert s["refund_orders_count"] >= 3
        assert s["total_refund_partial"] >= 610.0  # 312.20 + 208.59 + 89.43
        # Order 263864673 (user's example) should be in the list
        order_numbers = [o["order_number"] for o in body["orders"]]
        assert "263864673" in order_numbers
        # by_payment_method: mada + credit_card present
        methods = [r["payment_method"] for r in body["by_payment_method"]]
        assert "mada" in methods
        assert "credit_card" in methods
    finally:
        files = auth.get(f"{BASE_URL}/api/payment-settlements", timeout=10).json().get("files", [])
        for f in files:
            auth.delete(f"{BASE_URL}/api/payment-settlements/{f['id']}", timeout=10)
