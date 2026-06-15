"""Iter-221 — E2E HTTP test for BNPL settlements registration page.

Exercises the full HTTP flow that the new /bnpl-settlements/register
page depends on:
    GET  /api/bnpl/settlements/registration-overview
    GET  /api/bnpl/settlements/registered
    POST /api/bnpl/settlements/register
    GET  /api/bnpl/settlements/registered/{txn_group_id}
    POST again (same reference) → idempotent skip
    POST after receivable closed → over-settlement reject

Seeds receivable directly via post_bnpl_sale_to_ledger so the merchant
account has a positive balance to settle against. Cleanup at end
removes all PYTEST-tagged seed data — leaves the merchant's real data
untouched.
"""
from __future__ import annotations

import os
import sys
import uuid
import asyncio
import pytest
import requests
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")
sys.path.insert(0, "/app/backend")

from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402

BASE_URL = os.environ["REACT_APP_BACKEND_URL"].rstrip("/") if os.environ.get(
    "REACT_APP_BACKEND_URL"
) else "https://salla-analytics.preview.emergentagent.com"
EMAIL = "amasi.jewelery@gmail.com"
PWD = "10201917"
REF_OK = "PYTEST-E2E-1"
REF_OVER = "PYTEST-E2E-OVER"
SEED_SID = "PYTEST_SEED_1"


# ---------------- Helpers ----------------
def _login():
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login", json={"email": EMAIL, "password": PWD}, timeout=20)
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
    body = r.json()
    tok = body.get("access_token")
    s.headers.update({"Authorization": f"Bearer {tok}", "Content-Type": "application/json"})
    uid = body.get("id") or body.get("user", {}).get("id") or body.get("user_id")
    if not uid:
        me = s.get(f"{BASE_URL}/api/auth/me", timeout=20).json()
        uid = me.get("id")
    assert uid, f"could not resolve user id: {body}"
    return s, uid


def _conn():
    cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
    return cli, cli[os.environ["DB_NAME"]]


async def _seed_receivable_and_bank(uid: str):
    """Seed: insert a bnpl_sale general_ledger entry for tabby = 500."""
    from bnpl.ledger_bridge import post_bnpl_sale_to_ledger
    cli, db = _conn()
    try:
        # Ensure a usable bank account exists; create a PYTEST one if missing
        bank = await db.accounts.find_one(
            {"user_id": uid, "account_type": {"$in": ["bank", "cash"]}},
            {"_id": 0, "id": 1, "name": 1},
        )
        if not bank:
            bid = str(uuid.uuid4())
            await db.accounts.insert_one({
                "id": bid, "user_id": uid, "name": "PYTEST Bank",
                "account_type": "bank", "status": "active",
                "current_balance": 0.0,
            })
            bank_id = bid
        else:
            bank_id = bank["id"]
        # Wipe any prior PYTEST seed leftovers
        await db.general_ledger.delete_many(
            {"user_id": uid,
             "$or": [
                 {"metadata.idempotency_key": {"$regex": "^bnpl_sale:tabby:PYTEST"}},
                 {"metadata.idempotency_key": {"$regex": "^bnpl_settlement:tabby:PYTEST"}},
             ]}
        )
        await db.account_transactions.delete_many(
            {"user_id": uid,
             "metadata.idempotency_key": {"$regex": "^bnpl_settlement:tabby:PYTEST"}}
        )
        await post_bnpl_sale_to_ledger(
            db, user_id=uid, txn={
                "id": str(uuid.uuid4()),
                "provider": "tabby",
                "provider_id": SEED_SID,
                "amount": 500.0,
                "status": "closed",
                "order_reference_id": f"ORD-{SEED_SID}",
                "order_number": f"#{SEED_SID}",
                "created_at_provider": "2026-03-01T10:00:00Z",
            },
        )
        return bank_id
    finally:
        cli.close()


async def _cleanup(uid: str):
    cli, db = _conn()
    try:
        # Only remove PYTEST-tagged data; never touch real merchant data
        await db.general_ledger.delete_many(
            {"user_id": uid,
             "$or": [
                 {"metadata.idempotency_key": {"$regex": "^bnpl_sale:tabby:PYTEST"}},
                 {"metadata.idempotency_key": {"$regex": "^bnpl_settlement:tabby:PYTEST"}},
             ]}
        )
        await db.account_transactions.delete_many(
            {"user_id": uid,
             "metadata.idempotency_key": {"$regex": "^bnpl_settlement:tabby:PYTEST"}}
        )
        await db.accounts.delete_many({"user_id": uid, "name": "PYTEST Bank"})
    finally:
        cli.close()


# ---------------- Fixtures ----------------
@pytest.fixture(scope="module")
def env():
    s, uid = _login()
    bank_id = asyncio.get_event_loop().run_until_complete(_seed_receivable_and_bank(uid))
    yield {"session": s, "uid": uid, "bank_id": bank_id}
    asyncio.get_event_loop().run_until_complete(_cleanup(uid))


# ---------------- Tests ----------------
def test_overview_returns_providers(env):
    r = env["session"].get(f"{BASE_URL}/api/bnpl/settlements/registration-overview", timeout=30)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data.get("success") is True
    provs = {p["provider"]: p for p in data.get("providers", [])}
    assert "tabby" in provs and "tamara" in provs
    # Seed gave tabby a 500 receivable
    assert provs["tabby"]["current_receivable"] >= 500.0
    for k in ("expected_total", "received_total", "difference", "match_status"):
        assert k in provs["tabby"]


def test_registered_list_endpoint(env):
    r = env["session"].get(f"{BASE_URL}/api/bnpl/settlements/registered?limit=10", timeout=20)
    assert r.status_code == 200, r.text
    data = r.json()
    assert "items" in data and isinstance(data["items"], list)


def test_register_settlement_creates_ledger(env):
    payload = {
        "provider": "tabby",
        "bank_account_id": env["bank_id"],
        "transferred_amount": 400.0,
        "commission": 80.0,
        "commission_vat": 12.0,
        "settlement_fee": 8.0,
        "settlement_reference": REF_OK,
        "settlement_date": "2026-03-05",
        "notes": "PYTEST settlement",
    }
    r = env["session"].post(f"{BASE_URL}/api/bnpl/settlements/register", json=payload, timeout=30)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data.get("success") is True
    assert data.get("skipped") in (False, None)
    txn_group_id = data.get("txn_group_id")
    assert txn_group_id
    assert data.get("total_closed") == 500.0
    # Fetch entries
    r2 = env["session"].get(
        f"{BASE_URL}/api/bnpl/settlements/registered/{txn_group_id}", timeout=20)
    assert r2.status_code == 200
    d2 = r2.json()
    assert d2["balanced"] is True
    assert d2["debit_total"] == d2["credit_total"] == 500.0
    assert len(d2["entries"]) == 5  # bank + 3 expenses + 1 credit
    env["_txn"] = txn_group_id


def test_idempotent_re_register_skips(env):
    payload = {
        "provider": "tabby",
        "bank_account_id": env["bank_id"],
        "transferred_amount": 400.0,
        "commission": 80.0,
        "commission_vat": 12.0,
        "settlement_fee": 8.0,
        "settlement_reference": REF_OK,
        "settlement_date": "2026-03-05",
        "notes": "PYTEST settlement re-try",
    }
    r = env["session"].post(f"{BASE_URL}/api/bnpl/settlements/register", json=payload, timeout=20)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data.get("skipped") is True
    assert data.get("reason") == "idempotent_duplicate"


def test_over_settlement_rejected_after_close(env):
    # Receivable is now 0 → next settlement should be rejected
    payload = {
        "provider": "tabby",
        "bank_account_id": env["bank_id"],
        "transferred_amount": 100.0,
        "commission": 0.0,
        "commission_vat": 0.0,
        "settlement_fee": 0.0,
        "settlement_reference": REF_OVER,
        "settlement_date": "2026-03-06",
        "notes": "PYTEST over",
    }
    r = env["session"].post(f"{BASE_URL}/api/bnpl/settlements/register", json=payload, timeout=20)
    assert r.status_code == 400, r.text
    detail = (r.json().get("detail") or "").lower()
    # Arabic error or general English keyword
    assert "رصيد" in r.json().get("detail", "") or "receivable" in detail or "يتجاوز" in r.json().get("detail", "")


def test_recent_list_shows_new_entry(env):
    r = env["session"].get(f"{BASE_URL}/api/bnpl/settlements/registered?limit=20", timeout=20)
    assert r.status_code == 200
    refs = [it.get("settlement_reference") for it in r.json().get("items", [])]
    assert REF_OK in refs
