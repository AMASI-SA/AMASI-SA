"""Meta Ads integration tests.

Covers:
- GET /api/meta/config returns connected:false for new accounts.
- PUT /api/meta/config saves all 4 fields, normalizes act_ prefix.
- Subsequent PUT with empty secret/token keeps existing values.
- GET masks secret + token (only last 4 chars shown).
- POST /api/meta/sync requires connection, validates date range, returns
  Meta's error verbatim when API call fails (e.g. bad token).
- DELETE /api/meta/config disconnects.
- /auto-sync-if-stale: skips when last_sync_at is fresh.
- Dashboard meta-summary now exposes CPC/CPM/CTR.
"""
import os
import uuid
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")

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


def test_get_config_returns_not_connected_for_new_user():
    token = _register()
    h = {"Authorization": f"Bearer {token}"}
    r = requests.get(f"{API}/meta/config", headers=h)
    assert r.status_code == 200
    assert r.json() == {"connected": False}


def test_put_config_saves_and_normalizes_account_id():
    """ad_account_id without 'act_' prefix → backend adds it."""
    token = _register()
    h = {"Authorization": f"Bearer {token}"}
    r = requests.put(f"{API}/meta/config", headers=h, json={
        "app_id": "111222333",
        "app_secret": "supersecret",
        "access_token": "EAAxxx1234567890",
        "ad_account_id": "9876543210",  # no act_ prefix
    })
    assert r.status_code == 200

    cfg = requests.get(f"{API}/meta/config", headers=h).json()
    assert cfg["connected"] is True
    assert cfg["ad_account_id"] == "act_9876543210"
    assert cfg["app_id"] == "111222333"
    # Secrets masked (last 4 chars visible)
    assert cfg["app_secret_masked"].endswith("cret")
    assert cfg["access_token_masked"].endswith("7890")
    # Full values NOT returned
    assert "app_secret" not in cfg
    assert "access_token" not in cfg


def test_put_config_with_act_prefix_stays_same():
    token = _register()
    h = {"Authorization": f"Bearer {token}"}
    requests.put(f"{API}/meta/config", headers=h, json={
        "app_id": "1", "app_secret": "x", "access_token": "y",
        "ad_account_id": "act_123456",
    }).raise_for_status()
    assert requests.get(f"{API}/meta/config", headers=h).json()["ad_account_id"] == "act_123456"


def test_put_config_rejects_non_numeric_account_id():
    token = _register()
    h = {"Authorization": f"Bearer {token}"}
    r = requests.put(f"{API}/meta/config", headers=h, json={
        "app_id": "1", "app_secret": "x", "access_token": "y",
        "ad_account_id": "act_abc",
    })
    assert r.status_code == 400


def test_put_config_keeps_existing_secret_when_blank():
    """After initial save, sending empty secret/token preserves them."""
    token = _register()
    h = {"Authorization": f"Bearer {token}"}
    # Initial save
    requests.put(f"{API}/meta/config", headers=h, json={
        "app_id": "100",
        "app_secret": "ORIGINAL_SECRET",
        "access_token": "ORIGINAL_TOKEN_1234",
        "ad_account_id": "999",
    }).raise_for_status()

    # Update without re-sending secret/token
    r = requests.put(f"{API}/meta/config", headers=h, json={
        "app_id": "100",
        "app_secret": "",
        "access_token": "",
        "ad_account_id": "999",
    })
    assert r.status_code == 200

    cfg = requests.get(f"{API}/meta/config", headers=h).json()
    # Still masked the original values
    assert cfg["app_secret_masked"].endswith("CRET")
    assert cfg["access_token_masked"].endswith("1234")


def test_put_config_initial_requires_secret_and_token():
    """Brand-new user MUST provide secret + token."""
    token = _register()
    h = {"Authorization": f"Bearer {token}"}
    r = requests.put(f"{API}/meta/config", headers=h, json={
        "app_id": "1",
        "app_secret": "",
        "access_token": "",
        "ad_account_id": "1",
    })
    assert r.status_code == 400


def test_sync_requires_connection():
    token = _register()
    h = {"Authorization": f"Bearer {token}"}
    r = requests.post(f"{API}/meta/sync", headers=h, json={"days": 7})
    assert r.status_code == 400
    assert "غير مربوط" in r.json()["detail"]


def test_sync_calls_meta_and_returns_error_for_bad_token():
    """With a fake token, Meta returns 400 'Malformed access token'.
    Verify our wrapper surfaces that as `errors` (not 500)."""
    token = _register()
    h = {"Authorization": f"Bearer {token}"}
    requests.put(f"{API}/meta/config", headers=h, json={
        "app_id": "1", "app_secret": "x",
        "access_token": "totally-fake-token",
        "ad_account_id": "123",
    }).raise_for_status()

    r = requests.post(f"{API}/meta/sync", headers=h, json={"days": 3})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["upserted"] == 0
    assert len(body["errors"]) >= 1
    assert "Malformed" in body["errors"][0] or "OAuth" in body["errors"][0]


def test_sync_rejects_range_over_90_days():
    token = _register()
    h = {"Authorization": f"Bearer {token}"}
    requests.put(f"{API}/meta/config", headers=h, json={
        "app_id": "1", "app_secret": "x", "access_token": "y",
        "ad_account_id": "1",
    }).raise_for_status()
    r = requests.post(f"{API}/meta/sync", headers=h, json={
        "from_date": "2025-01-01", "to_date": "2026-05-01"
    })
    assert r.status_code == 400


def test_auto_sync_if_stale_skips_when_recent():
    """Set last_sync_at to now → auto-sync returns synced:false."""
    token = _register()
    h = {"Authorization": f"Bearer {token}"}
    requests.put(f"{API}/meta/config", headers=h, json={
        "app_id": "1", "app_secret": "x", "access_token": "y",
        "ad_account_id": "1",
    }).raise_for_status()

    # Manually mark as just-synced via the public sync endpoint (fake token,
    # but it WILL set last_sync_at)
    requests.post(f"{API}/meta/sync", headers=h, json={"days": 1}).raise_for_status()

    r = requests.post(f"{API}/meta/auto-sync-if-stale", headers=h)
    assert r.status_code == 200
    body = r.json()
    assert body["connected"] is True
    assert body["synced"] is False  # skipped


def test_auto_sync_if_stale_returns_not_connected_when_disconnected():
    token = _register()
    h = {"Authorization": f"Bearer {token}"}
    r = requests.post(f"{API}/meta/auto-sync-if-stale", headers=h)
    assert r.status_code == 200
    assert r.json() == {"connected": False, "synced": False}


def test_delete_config():
    token = _register()
    h = {"Authorization": f"Bearer {token}"}
    requests.put(f"{API}/meta/config", headers=h, json={
        "app_id": "1", "app_secret": "x", "access_token": "y",
        "ad_account_id": "1",
    }).raise_for_status()
    r = requests.delete(f"{API}/meta/config", headers=h)
    assert r.status_code == 200
    assert requests.get(f"{API}/meta/config", headers=h).json() == {"connected": False}


def test_meta_summary_exposes_cpc_cpm_ctr():
    """Push some data via webhook, verify summary now returns CPC/CPM/CTR."""
    token = _register()
    h = {"Authorization": f"Bearer {token}"}
    wt = requests.get(f"{API}/webhook/settings", headers=h).json()["token"]
    today = datetime.now(timezone.utc).date().isoformat()
    requests.post(f"{API}/webhook/meta/{wt}", json={
        "platform": "meta", "date": today, "campaign_id": "c1",
        "spend": 100, "impressions": 10000, "clicks": 200,
        "purchases": 5, "purchase_value": 500,
    }).raise_for_status()

    r = requests.get(f"{API}/dashboard/meta-summary", headers=h).json()
    # CPC = 100/200 = 0.50
    assert r["today"]["cpc"] == 0.50
    # CPM = (100 / 10000) * 1000 = 10.00
    assert r["today"]["cpm"] == 10.00
    # CTR = (200 / 10000) * 100 = 2.00 (%)
    assert r["today"]["ctr"] == 2.00
