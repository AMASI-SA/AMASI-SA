"""Tests for TikTok Ads daily-stats webhook (Make.com → /webhook/tiktok/{token}).

Coverage:
- Invalid token → 401
- Bad date format → 400 (item-level error, not request-wide)
- Happy path: single object, list of objects, idempotent overwrite
- Dashboard exposes tiktok_spend / tiktok_purchases / tiktok_revenue / tiktok_roas
"""
import os
import uuid

import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
API = f"{BASE_URL}/api"


def _register():
    email = f"u{uuid.uuid4().hex[:8]}@test.app"
    r = requests.post(f"{API}/auth/register",
                      json={"name": "T", "email": email, "password": "test12345"})
    r.raise_for_status()
    return r.json()["access_token"]


def _webhook_token(h):
    r = requests.get(f"{API}/webhook/settings", headers=h)
    r.raise_for_status()
    return r.json()


def test_settings_exposes_tiktok_webhook_url():
    token = _register()
    h = {"Authorization": f"Bearer {token}"}
    s = _webhook_token(h)
    assert "/webhook/tiktok/" in s["tiktok_webhook_url"]
    assert "/webhook/make/" in s["webhook_url"]
    assert s["tiktok_webhook_url"] == s["webhook_url"].replace("/make/", "/tiktok/")


def test_invalid_token_returns_401():
    r = requests.post(f"{API}/webhook/tiktok/INVALID",
                      json={"date": "2026-05-30", "spend": 1, "purchases": 0, "revenue": 0})
    assert r.status_code == 401


def test_bad_date_format_returned_in_errors():
    token = _register()
    h = {"Authorization": f"Bearer {token}"}
    wh = _webhook_token(h)["token"]
    r = requests.post(f"{API}/webhook/tiktok/{wh}",
                      json={"date": "30/05/2026", "spend": 100, "purchases": 5, "revenue": 600})
    assert r.status_code == 200
    body = r.json()
    assert body["accepted"] == 0
    assert len(body["errors"]) == 1
    assert "YYYY-MM-DD" in body["errors"][0]["error"]


def test_ingest_and_aggregate_on_dashboard():
    token = _register()
    h = {"Authorization": f"Bearer {token}"}
    wh = _webhook_token(h)["token"]

    # Push three days
    payload = [
        {"source": "tiktok", "date": "2026-05-28", "spend": 100, "purchases": 4, "revenue": 600},
        {"source": "tiktok", "date": "2026-05-29", "spend": 200, "purchases": 8, "revenue": 1200},
        {"source": "tiktok", "date": "2026-05-30", "spend": 350.75, "purchases": 12, "revenue": 2400},
    ]
    r = requests.post(f"{API}/webhook/tiktok/{wh}", json=payload)
    assert r.status_code == 200, r.text
    assert r.json()["accepted"] == 3

    # Dashboard filtered to the three-day window: totals match exactly.
    r = requests.get(f"{API}/dashboard",
                     params={"from_date": "2026-05-28", "to_date": "2026-05-30"},
                     headers=h)
    t = r.json()["totals"]
    assert t["tiktok_spend"] == 650.75
    assert t["tiktok_purchases"] == 24
    assert t["tiktok_revenue"] == 4200.0
    # ROAS = 4200 / 650.75 = 6.453...
    assert t["tiktok_roas"] == round(4200 / 650.75, 2)

    # Single-day filter narrows correctly
    r = requests.get(f"{API}/dashboard",
                     params={"from_date": "2026-05-30", "to_date": "2026-05-30"},
                     headers=h)
    t = r.json()["totals"]
    assert t["tiktok_spend"] == 350.75
    assert t["tiktok_purchases"] == 12
    assert t["tiktok_revenue"] == 2400.0
    assert t["tiktok_roas"] == 6.84  # 2400/350.75


def test_reposting_same_date_overwrites_not_appends():
    token = _register()
    h = {"Authorization": f"Bearer {token}"}
    wh = _webhook_token(h)["token"]
    # First push: 100 spend
    requests.post(f"{API}/webhook/tiktok/{wh}",
                  json={"date": "2026-05-30", "spend": 100, "purchases": 1, "revenue": 200})
    # Second push: 999 spend → must REPLACE not add
    requests.post(f"{API}/webhook/tiktok/{wh}",
                  json={"date": "2026-05-30", "spend": 999, "purchases": 5, "revenue": 1500})
    r = requests.get(f"{API}/dashboard",
                     params={"from_date": "2026-05-30", "to_date": "2026-05-30"},
                     headers=h)
    t = r.json()["totals"]
    assert t["tiktok_spend"] == 999.0
    assert t["tiktok_purchases"] == 5
    assert t["tiktok_revenue"] == 1500.0


def test_roas_zero_when_no_spend():
    token = _register()
    h = {"Authorization": f"Bearer {token}"}
    r = requests.get(f"{API}/dashboard", headers=h)
    t = r.json()["totals"]
    assert t["tiktok_spend"] == 0.0
    assert t["tiktok_roas"] == 0.0


def test_recent_endpoint_returns_descending():
    token = _register()
    h = {"Authorization": f"Bearer {token}"}
    wh = _webhook_token(h)["token"]
    for d in ["2026-05-01", "2026-05-15", "2026-05-30"]:
        requests.post(f"{API}/webhook/tiktok/{wh}",
                      json={"date": d, "spend": 50, "purchases": 1, "revenue": 100})
    r = requests.get(f"{API}/webhook/tiktok/recent?days=365", headers=h)
    items = r.json()["items"]
    assert len(items) == 3
    # descending order
    assert items[0]["date"] >= items[1]["date"] >= items[2]["date"]
