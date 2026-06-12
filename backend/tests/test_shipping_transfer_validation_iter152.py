"""Iter-152 — Shipping courier transfers validation tests.

Three guardrails are added to POST /api/shipping-accounts/transfers:

1. **courier_to_bank**: amount cannot exceed the courier's open balance
   (net_balance > 0 means they owe us).  If amount > net_balance the
   endpoint rejects with HTTP 400.

2. **bank_to_courier**: bank balance must be >= amount when a bank_id
   is supplied.  Insufficient balance → HTTP 400.

3. **bank_to_courier**: if amount > what we owe the courier the
   endpoint allows the transfer (the excess flips the net balance into
   "courier owes us") and surfaces a friendly `overpayment_note` so
   the merchant is informed.
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
    email = f"i152-{suffix}@example.com"
    pwd = "T#152a"
    requests.post(f"{BASE_URL}/api/auth/register",
                  json={"email": email, "password": pwd, "name": "I152"}, timeout=10)
    r = requests.post(f"{BASE_URL}/api/auth/login",
                      json={"email": email, "password": pwd}, timeout=10)
    token = r.json()["access_token"]
    hdr = {"Authorization": f"Bearer {token}"}
    me = requests.get(f"{BASE_URL}/api/auth/me", headers=hdr, timeout=10).json()
    # Bank with 1000 SAR
    bank = requests.post(f"{BASE_URL}/api/accounts",
                         json={"name": "بنك التجربة", "account_type": "bank",
                               "opening_balance": 1000.0},
                         headers=hdr, timeout=10).json()
    yield {"hdr": hdr, "uid": me["id"], "db": _mdb(), "bank_id": bank["id"]}


def _seed_courier_balance(ctx, company: str, owed_to_us: float = 0.0,
                          owed_to_courier: float = 0.0):
    """Seed unified_orders + courier_transfers to produce a target
    net_balance for `company`.

    Convention from `shipping_ledger`:
      net = cod_approved − shipping_cost − cod_fee − courier_to_bank + bank_to_courier
    So:
      - owed_to_us  → cod_approved=X, no other deductions
      - owed_to_courier → shipping_cost or bank_to_courier already paid
    """
    db = ctx["db"]
    uid = ctx["uid"]
    # Ensure the company is registered as a DEFERRED shipping company
    # in settings.shipping_companies (collection: `settings`, NOT
    # user_settings) — that's what `shipping_ledger` reads from.
    settings = db.settings.find_one({"user_id": uid}) or {}
    companies_list = list(settings.get("shipping_companies", []))
    names = {(s.get("name") or "").lower() for s in companies_list}
    if company.lower() in names:
        db.settings.update_one(
            {"user_id": uid, "shipping_companies.name": company},
            {"$set": {"shipping_companies.$.is_deferred": True}},
        )
    else:
        companies_list.append({
            "name": company, "cost_per_order": 0.0,
            "vat_percent": 15.0, "is_deferred": True,
        })
        db.settings.update_one(
            {"user_id": uid},
            {"$set": {"shipping_companies": companies_list}},
            upsert=True,
        )
    if owed_to_us > 0:
        # Inject one delivered COD order with total = owed_to_us.
        db.unified_orders.insert_one({
            "id": str(uuid.uuid4()), "user_id": uid,
            "order_status": "delivered",
            "shipping_company": company,
            "shipping_cost": 0.0,
            "total_amount": owed_to_us,
            "payment_method": "cash_on_delivery",
            "received_at": "2026-05-01T00:00:00",
            "is_pre_accounting": False,
        })
    if owed_to_courier > 0:
        # Inject delivered orders with shipping_cost.
        db.unified_orders.insert_one({
            "id": str(uuid.uuid4()), "user_id": uid,
            "order_status": "delivered",
            "shipping_company": company,
            "shipping_cost": owed_to_courier,
            "total_amount": 0.0,
            "payment_method": "online",  # NOT COD
            "received_at": "2026-05-01T00:00:00",
            "is_pre_accounting": False,
        })


# ── Tests ──────────────────────────────────────────────────────────────


def test_courier_to_bank_rejects_amount_exceeding_owed(ctx):
    _seed_courier_balance(ctx, "SMSA", owed_to_us=500.0)
    r = requests.post(
        f"{BASE_URL}/api/shipping-accounts/transfers",
        json={"company_name": "SMSA", "direction": "courier_to_bank",
              "amount": 750.0, "transfer_date": "2026-06-01",
              "bank_account_id": ctx["bank_id"]},
        headers=ctx["hdr"], timeout=10,
    )
    assert r.status_code == 400, r.text
    assert "أكبر من المستحق" in r.json()["detail"]


def test_courier_to_bank_rejects_when_no_balance(ctx):
    _seed_courier_balance(ctx, "iMile", owed_to_us=0.0)
    r = requests.post(
        f"{BASE_URL}/api/shipping-accounts/transfers",
        json={"company_name": "iMile", "direction": "courier_to_bank",
              "amount": 100.0, "transfer_date": "2026-06-01",
              "bank_account_id": ctx["bank_id"]},
        headers=ctx["hdr"], timeout=10,
    )
    assert r.status_code == 400


def test_courier_to_bank_succeeds_when_amount_within_owed(ctx):
    _seed_courier_balance(ctx, "SMSA", owed_to_us=500.0)
    r = requests.post(
        f"{BASE_URL}/api/shipping-accounts/transfers",
        json={"company_name": "SMSA", "direction": "courier_to_bank",
              "amount": 300.0, "transfer_date": "2026-06-01",
              "bank_account_id": ctx["bank_id"]},
        headers=ctx["hdr"], timeout=10,
    )
    assert r.status_code == 200, r.text


def test_bank_to_courier_rejects_insufficient_bank_balance(ctx):
    _seed_courier_balance(ctx, "Aramex", owed_to_courier=10000.0)
    r = requests.post(
        f"{BASE_URL}/api/shipping-accounts/transfers",
        json={"company_name": "Aramex", "direction": "bank_to_courier",
              "amount": 5000.0,  # bank only has 1000
              "transfer_date": "2026-06-01",
              "bank_account_id": ctx["bank_id"]},
        headers=ctx["hdr"], timeout=10,
    )
    assert r.status_code == 400, r.text
    assert "غير كافٍ" in r.json()["detail"]


def test_bank_to_courier_allows_overpayment_with_note(ctx):
    """We owe Aramex 200. Paying them 500 must succeed (bank has 1000)
    AND the response must include `overpayment_note`."""
    _seed_courier_balance(ctx, "Aramex", owed_to_courier=200.0)
    r = requests.post(
        f"{BASE_URL}/api/shipping-accounts/transfers",
        json={"company_name": "Aramex", "direction": "bank_to_courier",
              "amount": 500.0, "transfer_date": "2026-06-01",
              "bank_account_id": ctx["bank_id"]},
        headers=ctx["hdr"], timeout=10,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("overpayment") == 300.0
    assert "يزيد عن المستحق" in (body.get("overpayment_note") or "")


def test_bank_to_courier_normal_payment_no_overpayment_note(ctx):
    """We owe Aramex 200. Paying them 150 → no over-payment note."""
    _seed_courier_balance(ctx, "Aramex", owed_to_courier=200.0)
    r = requests.post(
        f"{BASE_URL}/api/shipping-accounts/transfers",
        json={"company_name": "Aramex", "direction": "bank_to_courier",
              "amount": 150.0, "transfer_date": "2026-06-01",
              "bank_account_id": ctx["bank_id"]},
        headers=ctx["hdr"], timeout=10,
    )
    assert r.status_code == 200
    assert "overpayment_note" not in r.json()


def test_unknown_company_skips_validation(ctx):
    """A non-deferred / unknown company is not in the ledger, so the
    new validation should NOT block (existing behavior preserved)."""
    r = requests.post(
        f"{BASE_URL}/api/shipping-accounts/transfers",
        json={"company_name": "بريد جوي", "direction": "bank_to_courier",
              "amount": 50.0, "transfer_date": "2026-06-01",
              "bank_account_id": ctx["bank_id"]},
        headers=ctx["hdr"], timeout=10,
    )
    assert r.status_code == 200, r.text


def test_transfer_without_bank_still_validates_courier_balance(ctx):
    """When no bank_id is supplied, the bank-balance check is skipped
    but the courier-balance check still applies."""
    _seed_courier_balance(ctx, "SMSA", owed_to_us=300.0)
    r = requests.post(
        f"{BASE_URL}/api/shipping-accounts/transfers",
        json={"company_name": "SMSA", "direction": "courier_to_bank",
              "amount": 500.0, "transfer_date": "2026-06-01"},
        headers=ctx["hdr"], timeout=10,
    )
    assert r.status_code == 400
    assert "أكبر من المستحق" in r.json()["detail"]
