"""Iter-112 — Edit existing topup (amount + date)."""
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
    email = f"top112-{suffix}@example.com"
    pwd = "T#aaa1"
    requests.post(f"{BASE_URL}/api/auth/register",
                  json={"email": email, "password": pwd, "name": "E"}, timeout=10)
    r = requests.post(f"{BASE_URL}/api/auth/login",
                      json={"email": email, "password": pwd}, timeout=10)
    token = r.json()["access_token"]
    hdr = {"Authorization": f"Bearer {token}"}
    me = requests.get(f"{BASE_URL}/api/auth/me", headers=hdr, timeout=10).json()
    # Setup bank + ad account
    bank = requests.post(f"{BASE_URL}/api/accounts",
                        json={"name": "Test Bank", "account_type": "bank",
                              "opening_balance": 5000},
                        headers=hdr, timeout=10).json()
    cp = requests.post(f"{BASE_URL}/api/ad-accounts",
                      json={"name": "TT", "ad_provider": "tiktok", "force": True},
                      headers=hdr, timeout=10).json()
    yield {"hdr": hdr, "uid": me["id"], "db": _mdb(),
           "bank": bank["id"], "cp": cp["id"]}


def _topup(ctx, amount, date):
    r = requests.post(f"{BASE_URL}/api/ad-accounts/{ctx['cp']}/topup",
                      json={"amount": amount, "paid_from_account_id": ctx["bank"],
                            "transaction_date": date, "description": "first"},
                      headers=ctx["hdr"], timeout=10)
    assert r.status_code == 200, r.text
    return r.json()


def test_edit_topup_increases_amount(ctx):
    _topup(ctx, 1000, "2026-06-01")
    led = list(ctx["db"].ad_account_ledger.find({"user_id": ctx["uid"], "type": "topup"}))
    assert len(led) == 1
    ledger_id = led[0]["id"]

    r = requests.put(f"{BASE_URL}/api/ad-accounts/{ctx['cp']}/topup/{ledger_id}",
                     json={"amount": 1500},
                     headers=ctx["hdr"], timeout=10)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["amount"] == 1500.0
    assert d["previous_amount"] == 1000.0
    assert d["ad_account"]["balance"] == 1500.0

    # Bank balance went DOWN by additional 500 (was -1000, now -1500 → 5000-1500=3500)
    bank = requests.get(f"{BASE_URL}/api/accounts/{ctx['bank']}",
                       headers=ctx["hdr"], timeout=10).json()
    assert bank["current_balance"] == 3500.0


def test_edit_topup_decreases_amount(ctx):
    _topup(ctx, 1000, "2026-06-01")
    ledger_id = list(ctx["db"].ad_account_ledger.find(
        {"user_id": ctx["uid"], "type": "topup"}))[0]["id"]

    r = requests.put(f"{BASE_URL}/api/ad-accounts/{ctx['cp']}/topup/{ledger_id}",
                     json={"amount": 400},
                     headers=ctx["hdr"], timeout=10)
    assert r.status_code == 200
    assert r.json()["amount"] == 400.0
    assert r.json()["ad_account"]["balance"] == 400.0
    bank = requests.get(f"{BASE_URL}/api/accounts/{ctx['bank']}",
                       headers=ctx["hdr"], timeout=10).json()
    assert bank["current_balance"] == 4600.0  # 5000 - 400


def test_edit_topup_changes_date_only(ctx):
    _topup(ctx, 1000, "2026-06-01")
    ledger_id = list(ctx["db"].ad_account_ledger.find(
        {"user_id": ctx["uid"], "type": "topup"}))[0]["id"]

    r = requests.put(f"{BASE_URL}/api/ad-accounts/{ctx['cp']}/topup/{ledger_id}",
                     json={"transaction_date": "2026-07-15"},
                     headers=ctx["hdr"], timeout=10)
    assert r.status_code == 200
    # Verify ledger has the new date and bank tx has the new date
    led = ctx["db"].ad_account_ledger.find_one({"id": ledger_id})
    assert led["date"] == "2026-07-15"
    bank_tx = ctx["db"].account_transactions.find_one(
        {"user_id": ctx["uid"], "id": led["related_transaction_id"]})
    assert bank_tx["transaction_date"] == "2026-07-15"


def test_edit_rejects_non_topup_entries(ctx):
    # Create a spend (not topup)
    _topup(ctx, 1000, "2026-06-01")
    sp = requests.post(f"{BASE_URL}/api/ad-accounts/{ctx['cp']}/spend",
                       json={"amount": 200, "spend_date": "2026-06-02"},
                       headers=ctx["hdr"], timeout=10)
    assert sp.status_code == 200
    spend_row = ctx["db"].ad_account_ledger.find_one(
        {"user_id": ctx["uid"], "type": "spend"})
    r = requests.put(f"{BASE_URL}/api/ad-accounts/{ctx['cp']}/topup/{spend_row['id']}",
                     json={"amount": 100},
                     headers=ctx["hdr"], timeout=10)
    assert r.status_code == 400
    assert "تعبئة" in r.json()["detail"]


def test_edit_rejects_missing_ledger(ctx):
    r = requests.put(f"{BASE_URL}/api/ad-accounts/{ctx['cp']}/topup/nonexistent-id",
                     json={"amount": 100},
                     headers=ctx["hdr"], timeout=10)
    assert r.status_code == 404
