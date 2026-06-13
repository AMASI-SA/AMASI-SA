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


def _seed_spend(uid, col, date, amount, ad_account_id=None):
    load_dotenv("/app/backend/.env")
    mdb = MongoClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
    doc = {
        "id": str(uuid.uuid4()), "user_id": uid,
        "date": date, "spend": amount,
        "campaign_id": "C1", "purchases": 0, "revenue": 0,
        "created_at": date, "updated_at": date,
    }
    # Iter-163 — when seeding a per-account source (e.g.
    # snapchat_account_daily, meta_ads_daily) we must include the scope
    # field so the new strict sync logic picks the row up.
    if ad_account_id:
        if col == "snapchat_account_daily":
            doc["ad_account_id"] = ad_account_id
        elif col == "meta_ads_daily":
            doc["account_id"] = ad_account_id
    mdb[col].insert_one(doc)


def test_sync_all_processes_all_supported_accounts():
    """User has 3 ad accounts (snap, tiktok, meta). Each has daily
    spend seeded. /sync-all should process all 3 in one call."""
    c = _ctx()
    cps = {}
    for prov, col, name, ext_id in [
        ("snapchat", "snapchat_account_daily", "Snap Main", "snap-ext-1"),
        ("tiktok",   "tiktok_ads_daily",       "TT Main",   None),
        ("meta",     "meta_ads_daily",         "FB Main",   "meta-ext-1"),
    ]:
        body = {"name": name, "ad_provider": prov}
        if ext_id:
            body["external_account_id"] = ext_id
        cp = requests.post(f"{BASE_URL}/api/ad-accounts",
                            json=body,
                            headers=c["headers"], timeout=10).json()
        cps[prov] = cp
        _seed_spend(c["uid"], col, "2026-06-10", 200, ad_account_id=ext_id)

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
                        json={"name": "Snap I", "ad_provider": "snapchat",
                              "external_account_id": "snap-idem"},
                        headers=c["headers"], timeout=10).json()
    _seed_spend(c["uid"], "snapchat_account_daily", "2026-06-11", 100,
                ad_account_id="snap-idem")

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
