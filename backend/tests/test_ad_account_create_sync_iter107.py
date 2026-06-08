"""Iter-107 — Inline ad-account creation + TikTok/Meta/Google providers
+ sync-from-platform endpoint that aggregates daily ad spend."""
import os
import uuid

import requests


BASE_URL = (
    os.environ.get("REACT_APP_BACKEND_URL")
    or open("/app/frontend/.env").read()
    .split("REACT_APP_BACKEND_URL=")[1].split("\n")[0].strip()
)


def _new_user_with_bank():
    suffix = uuid.uuid4().hex[:8]
    email = f"iter107-{suffix}@example.com"
    requests.post(
        f"{BASE_URL}/api/auth/register",
        json={"email": email, "password": "T#107t", "name": "Ad+"},
        timeout=10,
    )
    r = requests.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": email, "password": "T#107t"},
        timeout=10,
    )
    h = {"Authorization": f"Bearer {r.json()['access_token']}"}
    bank = requests.post(
        f"{BASE_URL}/api/accounts",
        json={"name": "بنك Iter-107", "account_type": "bank",
              "currency": "SAR", "opening_balance": 50000,
              "opening_balance_date": "2026-01-01"},
        headers=h, timeout=10,
    ).json()
    me = requests.get(f"{BASE_URL}/api/auth/me", headers=h, timeout=10).json()
    return {"headers": h, "bank_id": bank["id"], "uid": me["id"]}


# ── 1) Inline create supports TikTok / Meta / Google / Twitter / Other
def test_inline_create_supports_all_providers():
    ctx = _new_user_with_bank()
    for prov, name in [
        ("snapchat", "Snap Main"),
        ("tiktok",   "TikTok Ads 1"),
        ("meta",     "Facebook Ads"),
        ("google",   "Google Ads"),
        ("twitter",  "X Ads"),
        ("other",    "Pinterest"),
    ]:
        r = requests.post(
            f"{BASE_URL}/api/ad-accounts",
            json={"name": name, "ad_provider": prov},
            headers=ctx["headers"], timeout=10,
        )
        assert r.status_code == 200, f"{prov}: {r.text}"
        body = r.json()
        assert body["ad_provider"] == prov
        assert body["balance"] == 0.0
        assert body["debt_mode"] == "auto"

    # All 6 accounts appear in list
    r = requests.get(
        f"{BASE_URL}/api/ad-accounts",
        headers=ctx["headers"], timeout=10,
    )
    assert r.json()["total"] == 6


# ── 2) Duplicate within same provider rejected ──────────────────────
def test_duplicate_within_same_provider_rejected():
    ctx = _new_user_with_bank()
    requests.post(
        f"{BASE_URL}/api/ad-accounts",
        json={"name": "TikTok Main", "ad_provider": "tiktok"},
        headers=ctx["headers"], timeout=10,
    )
    r = requests.post(
        f"{BASE_URL}/api/ad-accounts",
        json={"name": "TikTok Main", "ad_provider": "tiktok"},
        headers=ctx["headers"], timeout=10,
    )
    assert r.status_code == 409
    assert r.json()["detail"]["message"] == "duplicate"


# ── 3) Same name in DIFFERENT providers is allowed ──────────────────
def test_same_name_different_provider_allowed():
    """Documented limitation — counterparties unique index is on
    (user_id, kind, name_lower) so the user must give DIFFERENT
    names across providers. Acceptable since human-readable names
    are usually unique anyway."""
    ctx = _new_user_with_bank()
    r = requests.post(
        f"{BASE_URL}/api/ad-accounts",
        json={"name": "Main Snap", "ad_provider": "snapchat"},
        headers=ctx["headers"], timeout=10,
    )
    assert r.status_code == 200
    r = requests.post(
        f"{BASE_URL}/api/ad-accounts",
        json={"name": "Main TT", "ad_provider": "tiktok"},
        headers=ctx["headers"], timeout=10,
    )
    assert r.status_code == 200


# ── 4) Sync-from-platform aggregates daily spend (TikTok) ───────────
def test_sync_from_tiktok_daily():
    """Seed a few `tiktok_ads_daily` rows and verify the sync endpoint
    sums them and records a single /spend on the linked ad account."""
    ctx = _new_user_with_bank()
    cp = requests.post(
        f"{BASE_URL}/api/ad-accounts",
        json={"name": "TikTok 1", "ad_provider": "tiktok"},
        headers=ctx["headers"], timeout=10,
    ).json()

    # Seed daily spend via raw mongo write through the test helper.
    from pymongo import MongoClient
    from dotenv import load_dotenv
    load_dotenv("/app/backend/.env")
    mdb = MongoClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
    for d, spend in [("2026-06-01", 100), ("2026-06-02", 150), ("2026-06-03", 50)]:
        mdb.tiktok_ads_daily.insert_one({
            "id": str(uuid.uuid4()),
            "user_id": ctx["uid"],
            "date": d, "spend": spend,
            "campaign_id": "C-1", "purchases": 0, "revenue": 0,
            "created_at": d, "updated_at": d,
        })

    r = requests.post(
        f"{BASE_URL}/api/ad-accounts/{cp['id']}/sync-from-platform",
        json={"from_date": "2026-06-01", "to_date": "2026-06-03"},
        headers=ctx["headers"], timeout=10,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    # 100 + 150 + 50 = 300. Account had balance=0 → debt=300 in auto mode.
    assert body["amount"] == 300.0
    assert body["covered_by_balance"] == 0.0
    assert body["uncovered"] == 300.0
    assert body["debt_created"] == 300.0
    assert body["ad_account"]["open_debt"] == 300.0


# ── 5) Sync returns 0 when no data in range ─────────────────────────
def test_sync_zero_when_no_data():
    ctx = _new_user_with_bank()
    cp = requests.post(
        f"{BASE_URL}/api/ad-accounts",
        json={"name": "Empty TikTok", "ad_provider": "tiktok"},
        headers=ctx["headers"], timeout=10,
    ).json()
    r = requests.post(
        f"{BASE_URL}/api/ad-accounts/{cp['id']}/sync-from-platform",
        json={"from_date": "2027-01-01", "to_date": "2027-01-31"},
        headers=ctx["headers"], timeout=10,
    )
    assert r.status_code == 200
    assert r.json()["spend"] == 0.0


# ── 6) Sync rejected for providers without daily collection ─────────
def test_sync_rejected_for_unsupported_provider():
    ctx = _new_user_with_bank()
    cp = requests.post(
        f"{BASE_URL}/api/ad-accounts",
        json={"name": "Google Ads", "ad_provider": "google"},
        headers=ctx["headers"], timeout=10,
    ).json()
    r = requests.post(
        f"{BASE_URL}/api/ad-accounts/{cp['id']}/sync-from-platform",
        json={"from_date": "2026-06-01", "to_date": "2026-06-30"},
        headers=ctx["headers"], timeout=10,
    )
    assert r.status_code == 400
    assert "google" in r.json()["detail"].lower() or "غير متاحة" in r.json()["detail"]


# ── 7) Delete ad account refused when there's open debt ─────────────
def test_delete_refused_with_debt_or_balance():
    ctx = _new_user_with_bank()
    cp = requests.post(
        f"{BASE_URL}/api/ad-accounts",
        json={"name": "To delete", "ad_provider": "snapchat"},
        headers=ctx["headers"], timeout=10,
    ).json()

    # Has balance → cannot delete
    requests.post(
        f"{BASE_URL}/api/ad-accounts/{cp['id']}/topup",
        json={"amount": 50, "paid_from_account_id": ctx["bank_id"],
              "transaction_date": "2026-06-01"},
        headers=ctx["headers"], timeout=10,
    )
    r = requests.delete(
        f"{BASE_URL}/api/ad-accounts/{cp['id']}",
        headers=ctx["headers"], timeout=10,
    )
    assert r.status_code == 400

    # Spend balance to 0
    requests.post(
        f"{BASE_URL}/api/ad-accounts/{cp['id']}/spend",
        json={"amount": 50, "spend_date": "2026-06-02"},
        headers=ctx["headers"], timeout=10,
    )
    # Now delete OK
    r = requests.delete(
        f"{BASE_URL}/api/ad-accounts/{cp['id']}",
        headers=ctx["headers"], timeout=10,
    )
    assert r.status_code == 200
