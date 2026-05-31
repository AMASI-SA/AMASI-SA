"""Backend tests for the Unified Ads Report endpoint (/api/reports/ads).

Covers:
- Empty response shape for a brand-new account (all zeros, 3 platforms, series matches range).
- Snapchat-only data (from daily_costs).
- TikTok-only data (via webhook).
- Multi-platform combined totals (Snap + TikTok + Meta) with correct metric math.
- Date-range filtering (from_date / to_date).
- Derived metrics: ROAS, CPC, CPM, CTR, CPA correctness.
"""
import os
import uuid
from datetime import datetime, timezone, timedelta

import pytest
import requests


BASE_URL = os.environ.get(
    "REACT_APP_BACKEND_URL",
    "https://salla-analytics.preview.emergentagent.com",
).rstrip("/")
API = f"{BASE_URL}/api"


def _register():
    email = f"TEST_ads_{uuid.uuid4().hex[:8]}@hesab.app"
    r = requests.post(
        f"{API}/auth/register",
        json={"name": "AdsUser", "email": email, "password": "test12345"},
    )
    assert r.status_code == 200, r.text
    return r.json()["access_token"], email


def _hdr(tok):
    return {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}


@pytest.fixture(scope="function")
def clean_user_token():
    tok, _ = _register()
    return tok


def _get_webhook_token(jwt):
    r = requests.get(f"{API}/webhook/settings", headers=_hdr(jwt))
    assert r.status_code == 200, r.text
    return r.json()["token"]


# ── 1. Empty-state shape ──────────────────────────────────────────────────
def test_unified_ads_empty_shape(clean_user_token):
    r = requests.get(f"{API}/reports/ads", headers=_hdr(clean_user_token))
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body.keys()) == {"range", "platforms", "combined", "series"}
    assert len(body["platforms"]) == 3
    plat_names = {p["platform"] for p in body["platforms"]}
    assert plat_names == {"snapchat", "tiktok", "meta"}
    # All zeros for a fresh user
    for p in body["platforms"]:
        assert p["spend"] == 0
        assert p["revenue"] == 0
        assert p["purchases"] == 0
        assert p["roas"] == 0
    assert body["combined"]["spend"] == 0
    # Series spans the default month-to-date range
    assert len(body["series"]) >= 1
    sample = body["series"][0]
    assert set(sample.keys()) == {"date", "snapchat", "tiktok", "meta"}


# ── 2. Snapchat spend appears via daily_costs ─────────────────────────────
def test_unified_ads_picks_up_snapchat_daily_costs(clean_user_token):
    tok = clean_user_token
    today = datetime.now(timezone.utc).date().isoformat()
    # Add Snapchat daily cost (split across two accounts)
    r = requests.post(
        f"{API}/daily-costs", headers=_hdr(tok),
        json={"date": today, "snapchat_ads": 100, "snapchat_ads_2": 50},
    )
    assert r.status_code == 200

    body = requests.get(
        f"{API}/reports/ads", headers=_hdr(tok),
        params={"from_date": today, "to_date": today},
    ).json()

    snap = next(p for p in body["platforms"] if p["platform"] == "snapchat")
    assert snap["spend"] == 150.0  # 100 + 50

    # Combined reflects it too
    assert body["combined"]["spend"] == 150.0

    # Series row contains the spend on the correct date
    assert any(s["date"] == today and s["snapchat"] == 150 for s in body["series"])


# ── 3. TikTok via webhook ─────────────────────────────────────────────────
def test_unified_ads_picks_up_tiktok_webhook(clean_user_token):
    tok = clean_user_token
    wh_token = _get_webhook_token(tok)
    today = datetime.now(timezone.utc).date().isoformat()

    payload = {
        "platform": "tiktok",
        "date": today,
        "spend": 300,
        "impressions": 10000,
        "clicks": 200,
        "purchases": 10,
        "revenue": 1500,
        "campaign_id": "camp1",
    }
    r = requests.post(f"{API}/webhook/tiktok/{wh_token}", json=payload)
    assert r.status_code == 200, r.text

    body = requests.get(
        f"{API}/reports/ads", headers=_hdr(tok),
        params={"from_date": today, "to_date": today},
    ).json()

    tt = next(p for p in body["platforms"] if p["platform"] == "tiktok")
    assert tt["spend"] == 300
    assert tt["impressions"] == 10000
    assert tt["clicks"] == 200
    assert tt["purchases"] == 10
    assert tt["revenue"] == 1500
    # Derived metrics
    assert tt["roas"] == 5.0  # 1500/300
    assert tt["cpc"] == 1.5   # 300/200
    assert tt["cpm"] == 30.0  # (300/10000)*1000
    assert tt["ctr"] == 2.0   # (200/10000)*100
    assert tt["cpa"] == 30.0  # 300/10


# ── 4. Date-range filter excludes out-of-range data ───────────────────────
def test_unified_ads_date_range_filtering(clean_user_token):
    tok = clean_user_token
    today_d = datetime.now(timezone.utc).date()
    yesterday = (today_d - timedelta(days=1)).isoformat()
    long_ago = (today_d - timedelta(days=60)).isoformat()

    # Add Snap cost for yesterday and long_ago
    requests.post(
        f"{API}/daily-costs", headers=_hdr(tok),
        json={"date": yesterday, "snapchat_ads": 99},
    )
    requests.post(
        f"{API}/daily-costs", headers=_hdr(tok),
        json={"date": long_ago, "snapchat_ads": 555},
    )

    # Query only yesterday — long_ago is excluded
    body = requests.get(
        f"{API}/reports/ads", headers=_hdr(tok),
        params={"from_date": yesterday, "to_date": yesterday},
    ).json()
    snap = next(p for p in body["platforms"] if p["platform"] == "snapchat")
    assert snap["spend"] == 99.0
    assert body["combined"]["spend"] == 99.0


# ── 5. Combined math correctness across all 3 platforms ───────────────────
def test_unified_ads_combined_math(clean_user_token):
    tok = clean_user_token
    wh_token = _get_webhook_token(tok)
    today = datetime.now(timezone.utc).date().isoformat()

    # Snap: 100 spend
    requests.post(
        f"{API}/daily-costs", headers=_hdr(tok),
        json={"date": today, "snapchat_ads": 100},
    )

    # TikTok: 200 spend, 5 purchases, 1000 revenue
    requests.post(f"{API}/webhook/tiktok/{wh_token}", json={
        "platform": "tiktok", "date": today,
        "spend": 200, "purchases": 5, "revenue": 1000,
        "impressions": 5000, "clicks": 100, "campaign_id": "x",
    })

    # Meta: push via meta webhook
    requests.post(f"{API}/webhook/meta/{wh_token}", json={
        "date": today, "spend": 400, "purchases": 8,
        "purchase_value": 2400, "impressions": 8000, "clicks": 120,
        "campaign_id": "m1", "campaign_name": "Test",
    })

    body = requests.get(
        f"{API}/reports/ads", headers=_hdr(tok),
        params={"from_date": today, "to_date": today},
    ).json()

    combined = body["combined"]
    # Spend: 100 + 200 + 400 = 700
    assert combined["spend"] == 700.0
    # Revenue: 0 (snap pixel empty) + 1000 + 2400 = 3400
    assert combined["revenue"] == 3400.0
    # Purchases: 0 + 5 + 8 = 13
    assert combined["purchases"] == 13
    # ROAS = 3400 / 700 = 4.857... rounded to 4.86
    assert combined["roas"] == 4.86
    # Clicks: snap not avail + 100 + 120 = 220
    assert combined["clicks"] == 220
    # Impressions: snap not avail + 5000 + 8000 = 13000
    assert combined["impressions"] == 13000
    # CTR = 220/13000*100 = 1.6923... → 1.69
    assert combined["ctr"] == 1.69
