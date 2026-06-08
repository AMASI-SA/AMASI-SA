"""Iter-110-fix — Regression for the sync-all bug.

Bug reported by user (8 Feb 2026): clicking "مزامنة الكل الآن" returned
"تمت المزامنة" but no debt was created for Snapchat accounts that have
`external_account_id` set. Root cause was `_run_sync_for_all` querying
the wrong collection (`snapchat_ads_daily` instead of
`snapchat_account_daily`) and the wrong scope field for Meta
(`ad_account_id` instead of `account_id`).

These tests would FAIL before the Iter-110 fix.
"""
import os
import uuid

import pytest
import requests
from dotenv import load_dotenv
from pymongo import MongoClient


BASE_URL = (
    os.environ.get("REACT_APP_BACKEND_URL")
    or open("/app/frontend/.env").read()
    .split("REACT_APP_BACKEND_URL=")[1].split("\n")[0].strip()
)


def _mdb():
    load_dotenv("/app/backend/.env")
    return MongoClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]


@pytest.fixture
def ctx():
    suffix = uuid.uuid4().hex[:8]
    email = f"fix110-{suffix}@example.com"
    pwd = "T#110a"
    requests.post(f"{BASE_URL}/api/auth/register",
                  json={"email": email, "password": pwd, "name": "Fix"}, timeout=10)
    r = requests.post(f"{BASE_URL}/api/auth/login",
                      json={"email": email, "password": pwd}, timeout=10)
    token = r.json()["access_token"]
    hdr = {"Authorization": f"Bearer {token}"}
    me = requests.get(f"{BASE_URL}/api/auth/me", headers=hdr, timeout=10).json()
    yield {"hdr": hdr, "uid": me["id"], "db": _mdb()}


def _make_account(ctx, name, provider, external_id):
    r = requests.post(f"{BASE_URL}/api/ad-accounts",
                      json={"name": name, "ad_provider": provider,
                            "external_account_id": external_id, "force": True},
                      headers=ctx["hdr"], timeout=10)
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_sync_all_picks_up_snapchat_account_daily(ctx):
    """Bug fix: sync-all must read from snapchat_account_daily and
    filter by ad_account_id — NOT from snapchat_ads_daily."""
    cp = _make_account(ctx, "Snap real", "snapchat", "acc_REAL")
    # Insert into the CORRECT collection (snapchat_account_daily). The
    # buggy code would query snapchat_ads_daily and see 0 rows → debt=0.
    ctx["db"].snapchat_account_daily.insert_many([
        {"user_id": ctx["uid"], "ad_account_id": "acc_REAL",
         "date": "2026-06-01", "spend": 100.0},
        {"user_id": ctx["uid"], "ad_account_id": "acc_REAL",
         "date": "2026-06-02", "spend": 200.0},
        # Different account — must NOT bleed into our debt
        {"user_id": ctx["uid"], "ad_account_id": "acc_OTHER",
         "date": "2026-06-01", "spend": 999.0},
    ])
    r = requests.post(f"{BASE_URL}/api/ad-accounts/sync-all",
                      json={"from_date": "2026-06-01", "to_date": "2026-06-02"},
                      headers=ctx["hdr"], timeout=15)
    assert r.status_code == 200, r.text
    res = r.json()["results"]
    assert len(res) == 1, res
    row = res[0]
    # Pre-fix this would have been spend=0 / debt_created=0.
    assert row["spend"] == 300.0
    assert row["debt_created"] == 300.0
    # Source collection should be snapchat_account_daily
    assert row["source_collection"] == "snapchat_account_daily"

    # Liability persisted
    liab = ctx["db"].liabilities.find_one(
        {"user_id": ctx["uid"], "counterparty_id": cp,
         "kind": "ad_account", "status": "unpaid"})
    assert liab["expected_amount"] == 300.0


def test_sync_all_picks_up_meta_account_id_field(ctx):
    """Bug fix: Meta uses `account_id` (NOT `ad_account_id`) — buggy
    code filtered the wrong field → 0 rows → no debt."""
    cp = _make_account(ctx, "Meta real", "meta", "act_FACEBOOK_123")
    ctx["db"].meta_ads_daily.insert_many([
        {"user_id": ctx["uid"], "account_id": "act_FACEBOOK_123",
         "date": "2026-06-01", "spend": 75.50},
        {"user_id": ctx["uid"], "account_id": "act_FACEBOOK_123",
         "date": "2026-06-02", "spend": 24.50},
        # Different account
        {"user_id": ctx["uid"], "account_id": "act_OTHER",
         "date": "2026-06-01", "spend": 50.0},
    ])
    r = requests.post(f"{BASE_URL}/api/ad-accounts/sync-all",
                      json={"from_date": "2026-06-01", "to_date": "2026-06-02"},
                      headers=ctx["hdr"], timeout=15)
    res = r.json()["results"][0]
    assert res["spend"] == 100.0
    assert res["debt_created"] == 100.0
    assert res["source_collection"] == "meta_ads_daily"


def test_sync_from_platform_endpoint_also_fixed(ctx):
    """The per-account sync-from-platform endpoint had the same bug."""
    cp = _make_account(ctx, "Snap single", "snapchat", "acc_SF")
    ctx["db"].snapchat_account_daily.insert_one({
        "user_id": ctx["uid"], "ad_account_id": "acc_SF",
        "date": "2026-06-01", "spend": 150.0,
    })
    r = requests.post(f"{BASE_URL}/api/ad-accounts/{cp}/sync-from-platform",
                      json={"from_date": "2026-06-01", "to_date": "2026-06-30"},
                      headers=ctx["hdr"], timeout=15)
    assert r.status_code == 200, r.text
    data = r.json()
    # record_spend wrapper returns the summary structure with open_debt
    assert data["ad_account"]["open_debt"] == 150.0


def test_force_resync_bypasses_idempotency_after_buggy_run(ctx):
    """Regression: previously buggy sync stamped `last_auto_sync_date`
    even though it created no debt. Calling sync-all again normally
    must SKIP (idempotency), but with `force=true` it must process
    the account and create the actual debt this time."""
    cp = _make_account(ctx, "Snap Forced", "snapchat", "acc_FORCED")
    # 1) Pre-stamp last_auto_sync_date to today as the buggy code would
    today = __import__("datetime").date.today().isoformat()
    ctx["db"].counterparties.update_one(
        {"id": cp, "user_id": ctx["uid"]},
        {"$set": {"last_auto_sync_date": today}},
    )
    ctx["db"].snapchat_account_daily.insert_one({
        "user_id": ctx["uid"], "ad_account_id": "acc_FORCED",
        "date": today, "spend": 250.0,
    })

    # 2) Normal call must skip
    r1 = requests.post(f"{BASE_URL}/api/ad-accounts/sync-all",
                       json={"from_date": today, "to_date": today},
                       headers=ctx["hdr"], timeout=15)
    res1 = r1.json()["results"][0]
    assert res1.get("skipped") is True
    assert res1["reason"] == "already_synced"

    # 3) Force call must process and create debt
    r2 = requests.post(f"{BASE_URL}/api/ad-accounts/sync-all",
                       json={"from_date": today, "to_date": today, "force": True},
                       headers=ctx["hdr"], timeout=15)
    res2 = r2.json()["results"][0]
    assert "skipped" not in res2 or res2.get("skipped") is None
    assert res2["spend"] == 250.0
    assert res2["debt_created"] == 250.0
