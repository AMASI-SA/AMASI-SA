"""Iter-98 — Shipping company unification + Net-COD method.

Covers:
  1. GET /api/shipping-accounts/companies returns deduped canonical list.
  2. Aliases SMSA / سمسا / smsa collapse on save (transfers + shipping_payments).
  3. Net-COD transfer (gross/fee/net) creates 3 correct movements:
     - OUT from COD = gross
     - IN to bank = net
     - shipping_payable settlement = fee (default)
     - OR operating expense = fee (alternative)
"""
import os
import uuid

import pytest
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


def _new_user_with_bank():
    suffix = uuid.uuid4().hex[:8]
    email = f"iter98-{suffix}@example.com"
    requests.post(
        f"{BASE_URL}/api/auth/register",
        json={"email": email, "password": "T#98test", "name": "COD Net"},
        timeout=10,
    )
    r = requests.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": email, "password": "T#98test"},
        timeout=10,
    )
    token = r.json()["access_token"]
    h = {"Authorization": f"Bearer {token}"}
    r = requests.post(
        f"{BASE_URL}/api/accounts",
        json={
            "name": "بنك Iter-98", "account_type": "bank",
            "currency": "SAR", "opening_balance": 0,
            "opening_balance_date": "2026-01-01",
        },
        headers=h, timeout=10,
    )
    bank_id = r.json()["id"]
    me = requests.get(f"{BASE_URL}/api/auth/me", headers=h, timeout=10).json()
    return {"headers": h, "bank_id": bank_id, "uid": me["id"]}


def _seed_cod(uid, mongo, balance=20000.0):
    from datetime import datetime, timezone
    acc_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    mongo.accounts.insert_one({
        "id": acc_id, "user_id": uid,
        "name": "الدفع عند الاستلام",
        "account_type": "payment_platform",
        "currency": "SAR",
        "opening_balance": balance,
        "current_balance": balance,
        "expected_orders_balance": balance,
        "status": "active",
        "normalized_payment_method": "cash_on_delivery",
        "created_at": now, "updated_at": now,
    })
    return acc_id


@pytest.fixture
def mongo_db():
    from pymongo import MongoClient
    return MongoClient(_mongo_url())["test_database"]


def test_aliases_collapse_on_save(mongo_db):
    ctx = _new_user_with_bank()
    h = ctx["headers"]
    cod_id = _seed_cod(ctx["uid"], mongo_db)

    # Three transfers with different spellings of SMSA
    for amt, raw in [(100, "SMSA"), (200, "سمسا"), (300, "smsa")]:
        requests.post(
            f"{BASE_URL}/api/transfers",
            json={
                "from_account_id": cod_id, "to_account_id": ctx["bank_id"],
                "amount": amt, "transfer_date": "2026-06-10",
                "shipping_company": raw,
            },
            headers=h, timeout=10,
        )

    rows = requests.get(f"{BASE_URL}/api/transfers", headers=h, timeout=10).json()
    cos = {r["shipping_company"] for r in rows if r.get("shipping_company")}
    # All three should collapse to the canonical "سمسا"
    assert cos == {"سمسا"}, f"Expected only canonical name, got {cos}"


def test_companies_endpoint_dedupes(mongo_db):
    ctx = _new_user_with_bank()
    h = ctx["headers"]
    cod_id = _seed_cod(ctx["uid"], mongo_db)

    for amt, raw in [(100, "SMSA"), (200, "سمسا")]:
        requests.post(
            f"{BASE_URL}/api/transfers",
            json={
                "from_account_id": cod_id, "to_account_id": ctx["bank_id"],
                "amount": amt, "transfer_date": "2026-06-10",
                "shipping_company": raw,
            },
            headers=h, timeout=10,
        )

    r = requests.get(
        f"{BASE_URL}/api/shipping-accounts/companies", headers=h, timeout=10
    )
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    smsa_rows = [x for x in items if x["canonical"] == "smsa"]
    assert len(smsa_rows) == 1, f"Expected single SMSA row, got {smsa_rows}"
    assert smsa_rows[0]["usage_count"] >= 2
    assert smsa_rows[0]["display"] == "سمسا"


def test_net_cod_with_shipping_payable(mongo_db):
    """10,000 collected − 2,000 fee = 8,000 to bank, default settle."""
    ctx = _new_user_with_bank()
    h = ctx["headers"]
    cod_id = _seed_cod(ctx["uid"], mongo_db, balance=20000.0)

    r = requests.post(
        f"{BASE_URL}/api/transfers",
        json={
            "from_account_id": cod_id, "to_account_id": ctx["bank_id"],
            "amount": 8000,                           # net to bank
            "transfer_date": "2026-06-10",
            "shipping_company": "smsa",               # → سمسا
            "cod_gross_collected": 10000,
            "shipping_fee_deducted": 2000,
            # shipping_fee_settles_against omitted → default shipping_payable
        },
        headers=h, timeout=10,
    )
    assert r.status_code in (200, 201), r.text
    body = r.json()
    assert body["amount"] == 8000
    assert body["cod_gross_collected"] == 10000
    assert body["shipping_fee_deducted"] == 2000
    assert body["shipping_company"] == "سمسا"

    # COD account dropped by GROSS (10,000)
    cod = requests.get(
        f"{BASE_URL}/api/accounts/{cod_id}", headers=h, timeout=10
    ).json()
    assert cod["current_balance"] == 10000.0   # 20000 − 10000

    # Bank gained the NET (8,000)
    bank = requests.get(
        f"{BASE_URL}/api/accounts/{ctx['bank_id']}", headers=h, timeout=10
    ).json()
    assert bank["current_balance"] == 8000.0

    # A shipping_payments row for سمسا = 2,000 was written (withheld settlement)
    payments = requests.get(
        f"{BASE_URL}/api/shipping-accounts/سمسا/payments",
        headers=h, timeout=10,
    ).json()
    fee_rows = [p for p in payments["payments"] if p["amount"] == 2000.0]
    assert len(fee_rows) == 1
    assert fee_rows[0].get("settled_via_cod_withholding") is True
    assert fee_rows[0].get("paid_from_account_id") is None


def test_net_cod_with_expense_mode(mongo_db):
    """Same scenario but choose `expense` instead of payable settlement."""
    ctx = _new_user_with_bank()
    h = ctx["headers"]
    cod_id = _seed_cod(ctx["uid"], mongo_db, balance=15000.0)

    r = requests.post(
        f"{BASE_URL}/api/transfers",
        json={
            "from_account_id": cod_id, "to_account_id": ctx["bank_id"],
            "amount": 4500,
            "transfer_date": "2026-06-11",
            "shipping_company": "Aramex",
            "cod_gross_collected": 5000,
            "shipping_fee_deducted": 500,
            "shipping_fee_settles_against": "expense",
        },
        headers=h, timeout=10,
    )
    assert r.status_code in (200, 201), r.text

    # No shipping_payments row should exist
    payments = requests.get(
        f"{BASE_URL}/api/shipping-accounts/أرامكس/payments",
        headers=h, timeout=10,
    ).json()
    assert payments["payments"] == []

    # operating_daily_expenses should carry a 500 SAR row
    daily = requests.get(
        f"{BASE_URL}/api/operating-expenses/daily",
        headers=h, timeout=10,
    ).json()
    fee_rows = [d for d in daily["items"]
                if d.get("amount") == 500 and "أرامكس" in (d.get("description") or "")]
    assert len(fee_rows) == 1


def test_net_cod_validates_math(mongo_db):
    """Reject when gross − fee != amount."""
    ctx = _new_user_with_bank()
    h = ctx["headers"]
    cod_id = _seed_cod(ctx["uid"], mongo_db)

    r = requests.post(
        f"{BASE_URL}/api/transfers",
        json={
            "from_account_id": cod_id, "to_account_id": ctx["bank_id"],
            "amount": 8000,
            "transfer_date": "2026-06-10",
            "shipping_company": "سمسا",
            "cod_gross_collected": 10000,
            "shipping_fee_deducted": 5000,    # 10000 − 5000 = 5000 ≠ 8000
        },
        headers=h, timeout=10,
    )
    assert r.status_code == 400
    assert "لا يطابق" in r.text or "expected" in r.text.lower() or "لا يطابق" in r.text


def test_plain_cod_transfer_still_works(mongo_db):
    """No gross/fee fields → behaves exactly as Iter-96 (single amount)."""
    ctx = _new_user_with_bank()
    h = ctx["headers"]
    cod_id = _seed_cod(ctx["uid"], mongo_db, balance=10000.0)

    r = requests.post(
        f"{BASE_URL}/api/transfers",
        json={
            "from_account_id": cod_id, "to_account_id": ctx["bank_id"],
            "amount": 3000, "transfer_date": "2026-06-10",
            "shipping_company": "i-mile",   # alias → iMile للتوصيل
        },
        headers=h, timeout=10,
    )
    assert r.status_code in (200, 201), r.text
    body = r.json()
    assert body["shipping_company"] == "iMile للتوصيل"
    assert body.get("cod_gross_collected") is None

    cod = requests.get(f"{BASE_URL}/api/accounts/{cod_id}", headers=h, timeout=10).json()
    assert cod["current_balance"] == 7000.0
    bank = requests.get(f"{BASE_URL}/api/accounts/{ctx['bank_id']}", headers=h, timeout=10).json()
    assert bank["current_balance"] == 3000.0
