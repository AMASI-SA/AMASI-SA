"""Iter-108 — Daily cron + sync-all endpoint."""
import os
import uuid
import requests
from dotenv import load_dotenv
from pymongo import MongoClient


BASE_URL = (
    os.environ.get("REACT_APP_BACKEND_URL")
    or open("/app/frontend/.env").read()
    .split("REACT_APP_BACKEND_URL=")[1].split("\n")[0].strip()
)


def _ctx():
    suffix = uuid.uuid4().hex[:8]
    email = f"iter108-{suffix}@example.com"
    requests.post(f"{BASE_URL}/api/auth/register",
                   json={"email": email, "password": "T#108t", "name": "Cron"},
                   timeout=10)
    r = requests.post(f"{BASE_URL}/api/auth/login",
                       json={"email": email, "password": "T#108t"}, timeout=10)
    h = {"Authorization": f"Bearer {r.json()['access_token']}"}
    me = requests.get(f"{BASE_URL}/api/auth/me", headers=h, timeout=10).json()
    return {"headers": h, "uid": me["id"]}


def _seed_spend(uid, col, date, amount):
    load_dotenv("/app/backend/.env")
    mdb = MongoClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
    mdb[col].insert_one({
        "id": str(uuid.uuid4()), "user_id": uid,
        "date": date, "spend": amount,
        "campaign_id": "C1", "purchases": 0, "revenue": 0,
        "created_at": date, "updated_at": date,
    })


def test_sync_all_processes_all_supported_accounts():
    """User has 3 ad accounts (snap, tiktok, meta). Each has daily
    spend seeded. /sync-all should process all 3 in one call."""
    c = _ctx()
    cps = {}
    for prov, col, name in [
        ("snapchat", "snapchat_ads_daily", "Snap Main"),
        ("tiktok",   "tiktok_ads_daily",   "TT Main"),
        ("meta",     "meta_ads_daily",     "FB Main"),
    ]:
        cp = requests.post(f"{BASE_URL}/api/ad-accounts",
                            json={"name": name, "ad_provider": prov},
                            headers=c["headers"], timeout=10).json()
        cps[prov] = cp
        _seed_spend(c["uid"], col, "2026-06-10", 200)

    # Run sync-all
    r = requests.post(f"{BASE_URL}/api/ad-accounts/sync-all",
                       json={"from_date": "2026-06-10", "to_date": "2026-06-10"},
                       headers=c["headers"], timeout=10)
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["results"]) == 3
    for row in body["results"]:
        assert row["spend"] == 200.0
        assert row.get("debt_created", 0) == 200.0   # all balance=0 → debt


def test_sync_all_is_idempotent_per_to_date():
    """Re-running sync-all for the same `to_date` must NOT double-debit."""
    c = _ctx()
    cp = requests.post(f"{BASE_URL}/api/ad-accounts",
                        json={"name": "Snap I", "ad_provider": "snapchat"},
                        headers=c["headers"], timeout=10).json()
    _seed_spend(c["uid"], "snapchat_ads_daily", "2026-06-11", 100)

    # First run
    r1 = requests.post(f"{BASE_URL}/api/ad-accounts/sync-all",
                        json={"from_date": "2026-06-11", "to_date": "2026-06-11"},
                        headers=c["headers"], timeout=10)
    assert r1.status_code == 200
    # Second run with same to_date
    r2 = requests.post(f"{BASE_URL}/api/ad-accounts/sync-all",
                        json={"from_date": "2026-06-11", "to_date": "2026-06-11"},
                        headers=c["headers"], timeout=10)
    assert r2.status_code == 200
    # The second run must report `skipped: true` for the account
    skipped = any(r.get("skipped") for r in r2.json()["results"])
    assert skipped is True

    # Open debt is still 100 (not 200)
    s = requests.get(f"{BASE_URL}/api/ad-accounts/{cp['id']}",
                      headers=c["headers"], timeout=10).json()
    assert s["open_debt"] == 100.0
