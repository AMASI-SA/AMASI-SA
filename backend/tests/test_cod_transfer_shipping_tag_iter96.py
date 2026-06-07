"""Iter-96 — Tag COD → Bank transfers with the shipping company.

Captures which courier physically remitted COD cash to the bank so the
merchant can later answer "how much did SMSA / Aramex / مندوب الرياض
actually transfer in this period". No new collection — the field is
persisted on the existing `transfers` envelope and on both linked
`account_transactions` rows.
"""
import os
import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
import requests
from motor.motor_asyncio import AsyncIOMotorClient


BASE_URL = (
    os.environ.get("REACT_APP_BACKEND_URL")
    or open("/app/frontend/.env").read()
    .split("REACT_APP_BACKEND_URL=")[1].split("\n")[0].strip()
)


def _mongo_url() -> str:
    return (
        open("/app/backend/.env").read()
        .split("MONGO_URL=")[1].split("\n")[0].strip('"')
    )


def _seed_cod_account(db_sync_client, uid, opening_balance=5000.0):
    """Direct DB insert because `normalized_payment_method` is set by the
    sync flow, not by POST /accounts. Production has this field set
    correctly for the COD bucket — we replicate the same shape here.
    Returns the account id."""
    acc_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    db_sync_client.accounts.insert_one({
        "id": acc_id, "user_id": uid,
        "name": "الدفع عند الاستلام",
        "account_type": "payment_platform",
        "currency": "SAR",
        "opening_balance": opening_balance,
        "current_balance": opening_balance,
        "expected_orders_balance": opening_balance,
        "status": "active",
        "normalized_payment_method": "cash_on_delivery",
        "created_at": now, "updated_at": now,
    })
    return acc_id


def _new_user_with_bank():
    suffix = uuid.uuid4().hex[:8]
    email = f"iter96-{suffix}@example.com"
    password = "Test#96"
    r = requests.post(
        f"{BASE_URL}/api/auth/register",
        json={"email": email, "password": password, "name": "COD Tag Test"},
        timeout=10,
    )
    assert r.status_code in (200, 201), r.text
    r = requests.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": email, "password": password},
        timeout=10,
    )
    body = r.json()
    token = body.get("access_token") or body.get("token")
    h = {"Authorization": f"Bearer {token}"}

    # Bank
    r = requests.post(
        f"{BASE_URL}/api/accounts",
        json={
            "name": "بنك F3", "account_type": "bank",
            "currency": "SAR", "opening_balance": 0,
            "opening_balance_date": "2026-01-01",
        },
        headers=h, timeout=10,
    )
    bank_id = r.json()["id"]
    # Resolve user id via /api/auth/me
    me = requests.get(f"{BASE_URL}/api/auth/me", headers=h, timeout=10).json()
    uid = me.get("id") or me.get("user_id")
    return {"headers": h, "bank_id": bank_id, "uid": uid, "email": email}


@pytest.fixture
def mongo_db():
    from pymongo import MongoClient
    return MongoClient(_mongo_url())["test_database"]


def _create_cod_account(headers, uid, mongo_db, opening_balance=5000.0):
    return _seed_cod_account(mongo_db, uid, opening_balance)


def test_cod_transfer_persists_shipping_company(mongo_db):
    ctx = _new_user_with_bank()
    h = ctx["headers"]
    cod_id = _create_cod_account(h, ctx["uid"], mongo_db, opening_balance=5000.0)

    r = requests.post(
        f"{BASE_URL}/api/transfers",
        json={
            "from_account_id": cod_id,
            "to_account_id": ctx["bank_id"],
            "amount": 1500,
            "transfer_date": "2026-06-07",
            "reference": "SMSA-W23",
            "shipping_company": "سمسا",
        },
        headers=h, timeout=10,
    )
    assert r.status_code in (200, 201), r.text
    body = r.json()
    assert body["shipping_company"] == "سمسا"

    # Both linked tx rows must carry the tag too
    txs = requests.get(
        f"{BASE_URL}/api/accounts/{cod_id}/transactions",
        headers=h, timeout=10,
    ).json()
    items = txs if isinstance(txs, list) else txs.get("transactions") or txs.get("items", [])
    out_tx = [t for t in items if t.get("transfer_id") == body["id"]][0]
    assert out_tx["shipping_company"] == "سمسا"
    assert "سمسا" in out_tx["description"]


def test_non_cod_transfer_ignores_shipping_company():
    """If the source is not the COD bucket, the shipping_company hint
    should be ignored (stored as None) — protects ledger semantics."""
    ctx = _new_user_with_bank()
    h = ctx["headers"]
    # Create a second bank to use as source
    r = requests.post(
        f"{BASE_URL}/api/accounts",
        json={
            "name": "بنك ثاني", "account_type": "bank",
            "currency": "SAR", "opening_balance": 3000.0,
            "opening_balance_date": "2026-01-01",
        },
        headers=h, timeout=10,
    )
    other_bank = r.json()["id"]

    r = requests.post(
        f"{BASE_URL}/api/transfers",
        json={
            "from_account_id": other_bank,
            "to_account_id": ctx["bank_id"],
            "amount": 500,
            "transfer_date": "2026-06-07",
            "shipping_company": "سمسا",   # should be ignored
        },
        headers=h, timeout=10,
    )
    assert r.status_code in (200, 201), r.text
    body = r.json()
    assert body.get("shipping_company") in (None, "")


def test_cod_transfer_without_shipping_company_still_accepted_by_backend(mongo_db):
    """Backend keeps the field optional; the requirement is enforced on
    the UI side. The backend should not break legacy clients."""
    ctx = _new_user_with_bank()
    h = ctx["headers"]
    cod_id = _create_cod_account(h, ctx["uid"], mongo_db, opening_balance=2000.0)

    r = requests.post(
        f"{BASE_URL}/api/transfers",
        json={
            "from_account_id": cod_id,
            "to_account_id": ctx["bank_id"],
            "amount": 300,
            "transfer_date": "2026-06-08",
        },
        headers=h, timeout=10,
    )
    assert r.status_code in (200, 201), r.text
    body = r.json()
    assert body.get("shipping_company") in (None, "")


def test_list_transfers_returns_shipping_company(mongo_db):
    ctx = _new_user_with_bank()
    h = ctx["headers"]
    cod_id = _create_cod_account(h, ctx["uid"], mongo_db, opening_balance=8000.0)

    for amt, company in [(1000, "سمسا"), (2000, "أيميل"), (500, "مندوب الرياض")]:
        requests.post(
            f"{BASE_URL}/api/transfers",
            json={
                "from_account_id": cod_id,
                "to_account_id": ctx["bank_id"],
                "amount": amt, "transfer_date": "2026-06-07",
                "shipping_company": company,
            },
            headers=h, timeout=10,
        )

    r = requests.get(f"{BASE_URL}/api/transfers", headers=h, timeout=10)
    rows = r.json()
    by_company = {row["shipping_company"]: row["amount"] for row in rows
                  if row.get("shipping_company")}
    assert by_company == {"سمسا": 1000, "أيميل": 2000, "مندوب الرياض": 500}
