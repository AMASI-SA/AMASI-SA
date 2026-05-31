"""Meta (Facebook + Instagram) Ads webhook + dashboard summary tests.

Verify:
- POST /api/webhook/meta/{token} accepts a single row, a list, and rejects
  bad payloads.
- Re-posting the same (date, campaign_id) updates (not duplicates).
- GET /api/dashboard/meta-summary returns today/month/30d aggregates +
  per-campaign breakdown + 30-day sparkline.
"""
import os
import uuid
from datetime import datetime, timezone

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


def _webhook_token(token):
    h = {"Authorization": f"Bearer {token}"}
    return requests.get(f"{API}/webhook/settings", headers=h).json()["token"]


def test_meta_webhook_invalid_token_returns_401():
    r = requests.post(
        f"{API}/webhook/meta/not-a-real-token",
        json={"platform": "meta", "date": "2026-05-31", "spend": 100},
    )
    assert r.status_code == 401


def test_meta_webhook_accepts_full_payload():
    token = _register()
    wt = _webhook_token(token)
    r = requests.post(
        f"{API}/webhook/meta/{wt}",
        json={
            "platform": "meta",
            "date": "2026-05-31",
            "account_id": "act_123",
            "campaign_id": "cmp_summer",
            "campaign_name": "حملة الصيف",
            "adset_id": "as_women",
            "adset_name": "النساء 25-35",
            "ad_id": "ad_video",
            "ad_name": "إعلان فيديو",
            "spend": 350.75,
            "impressions": 12000,
            "clicks": 250,
            "cpc": 1.40,
            "cpm": 29.20,
            "ctr": 2.08,
            "purchases": 8,
            "purchase_value": 1200.50,
        },
    )
    assert r.status_code == 200
    assert r.json()["accepted"] == 1


def test_meta_webhook_upserts_on_same_key():
    """Same (date, campaign_id) → update, not duplicate."""
    token = _register()
    wt = _webhook_token(token)
    p = {"platform": "meta", "date": "2026-05-31",
         "campaign_id": "cmp_x", "spend": 100, "purchases": 2}
    requests.post(f"{API}/webhook/meta/{wt}", json=p).raise_for_status()
    p["spend"] = 200
    p["purchases"] = 5
    requests.post(f"{API}/webhook/meta/{wt}", json=p).raise_for_status()

    h = {"Authorization": f"Bearer {token}"}
    items = requests.get(f"{API}/webhook/meta/recent?days=60", headers=h).json()["items"]
    same_key = [i for i in items if i["campaign_id"] == "cmp_x" and i["date"] == "2026-05-31"]
    assert len(same_key) == 1  # only ONE row
    assert same_key[0]["spend"] == 200.0
    assert same_key[0]["purchases"] == 5


def test_meta_webhook_rejects_bad_date():
    token = _register()
    wt = _webhook_token(token)
    r = requests.post(
        f"{API}/webhook/meta/{wt}",
        json={"platform": "meta", "date": "31/05/2026", "spend": 50},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["accepted"] == 0
    assert len(body["errors"]) == 1


def test_meta_webhook_accepts_batch_list():
    token = _register()
    wt = _webhook_token(token)
    r = requests.post(
        f"{API}/webhook/meta/{wt}",
        json=[
            {"platform": "meta", "date": "2026-05-30", "campaign_id": "a", "spend": 10},
            {"platform": "meta", "date": "2026-05-30", "campaign_id": "b", "spend": 20},
            {"platform": "meta", "date": "2026-05-31", "campaign_id": "a", "spend": 30},
        ],
    )
    assert r.status_code == 200
    assert r.json()["accepted"] == 3


def test_meta_summary_aggregates_correctly():
    """Push 2 campaigns for today, verify summary buckets."""
    token = _register()
    wt = _webhook_token(token)
    today = datetime.now(timezone.utc).date().isoformat()
    requests.post(
        f"{API}/webhook/meta/{wt}",
        json=[
            {"platform": "meta", "date": today, "campaign_id": "c1",
             "campaign_name": "حملة 1", "spend": 100, "purchases": 3,
             "purchase_value": 500, "impressions": 5000, "clicks": 100},
            {"platform": "meta", "date": today, "campaign_id": "c2",
             "campaign_name": "حملة 2", "spend": 50, "purchases": 1,
             "purchase_value": 250, "impressions": 2000, "clicks": 40},
        ],
    ).raise_for_status()

    h = {"Authorization": f"Bearer {token}"}
    r = requests.get(f"{API}/dashboard/meta-summary", headers=h).json()

    # Today aggregates
    assert r["today"]["spend"] == 150.0
    assert r["today"]["orders"] == 4
    assert r["today"]["revenue"] == 750.0
    assert r["today"]["impressions"] == 7000
    assert r["today"]["clicks"] == 140
    assert r["today"]["roas"] == 5.0    # 750/150
    assert r["today"]["cpa"] == 37.5    # 150/4

    # Month aggregates (same as today since only today's data)
    assert r["month"]["spend"] == 150.0
    assert r["month"]["orders"] == 4

    # Campaigns breakdown sorted by spend
    assert len(r["campaigns"]) == 2
    assert r["campaigns"][0]["campaign_name"] == "حملة 1"
    assert r["campaigns"][0]["spend"] == 100.0
    assert r["campaigns"][1]["campaign_name"] == "حملة 2"

    # last_sync_at is set
    assert r["last_sync_at"] is not None

    # 30-day history
    assert len(r["history"]) == 30


def test_meta_summary_empty_returns_zeros():
    token = _register()
    h = {"Authorization": f"Bearer {token}"}
    r = requests.get(f"{API}/dashboard/meta-summary", headers=h).json()
    assert r["today"]["spend"] == 0.0
    assert r["today"]["orders"] == 0
    assert r["today"]["revenue"] == 0.0
    assert r["last_sync_at"] is None
    assert r["campaigns"] == []
