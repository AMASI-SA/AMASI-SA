"""Iter-246g — Supplier_pay endpoint accepts Iter-244 suppliers.

The merchant hit «المورد غير موجود» because the supplier was created
via /api/suppliers (Iter-244) and the legacy `/suppliers/{id}/pay`
endpoint only searched `db.counterparties`.  This test asserts the
lazy fallback + auto-bridge works.
"""
from __future__ import annotations

import os
import uuid

import pytest
import requests
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(_BACKEND_DIR, ".env"))
load_dotenv(os.path.join(_BACKEND_DIR, "..", "frontend", ".env"))
BASE_URL = os.environ["REACT_APP_BACKEND_URL"].rstrip("/")
MONGO_URL = os.environ["MONGO_URL"]
DB_NAME = os.environ["DB_NAME"]


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="module")
def ctx():
    suf = uuid.uuid4().hex[:8]
    r = requests.post(f"{BASE_URL}/api/auth/register",
                      json={"name": "t", "email": f"iter246g-{suf}@x.com",
                            "password": "pw1234567"})
    h = _h(r.json()["access_token"])
    uid = r.json()["id"]

    requests.post(
        f"{BASE_URL}/api/expense-category-tree/seed-template", headers=h)
    rows = requests.get(
        f"{BASE_URL}/api/expense-category-tree",
        params={"movement_type": "supplier_invoice"}, headers=h).json()["items"]
    leaf = next(x for x in rows if x.get("parent_id"))

    r = requests.post(
        f"{BASE_URL}/api/accounts", headers=h,
        json={"name": "بنك", "account_type": "bank",
              "opening_balance": 10000.0, "currency": "SAR"})
    bank_id = r.json()["id"]

    return {"h": h, "uid": uid, "cat_id": leaf["id"],
            "bank_id": bank_id}


@pytest.mark.asyncio
async def test_supplier_pay_finds_unbridged_iter244_supplier(ctx):
    """Simulate a pre-bridge data state: write a row to db.suppliers
    WITHOUT a matching counterparties row, then ensure /supplier_pay
    still works AND that the bridge is created on-the-fly."""
    h = ctx["h"]
    uid = ctx["uid"]
    sup_id = str(uuid.uuid4())

    client = AsyncIOMotorClient(MONGO_URL)
    try:
        db = client[DB_NAME]
        # Insert raw — bypassing the API so the bridge code in
        # /api/suppliers is NOT triggered.
        from datetime import datetime, timezone
        await db.suppliers.insert_one({
            "id": sup_id,
            "user_id": uid,
            "company_name": "مورد قبل-الجسر",
            "contact_person": "أبو زكريا",
            "phone": "0500000099",
            "status": "active",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })
        # Sanity check: NO counterparties row yet.
        existed = await db.counterparties.find_one(
            {"id": sup_id, "user_id": uid})
        assert existed is None
    finally:
        client.close()

    # Give the supplier a credit invoice so a debt exists; we use the
    # NEW financial-movements endpoint (also touches counterparties via
    # the journal post).  In real prod this would have failed BEFORE
    # iter246g because supplier_pay couldn't find the counterparty.
    r = requests.post(
        f"{BASE_URL}/api/financial-movements", headers=h, json={
            "movement_type": "supplier_invoice",
            "doc_date": "2026-02-06",
            "supplier_id": sup_id, "category_id": ctx["cat_id"],
            "payment_terms": "credit", "total_amount": 0,
            "line_items": [{"description": "x", "quantity": 1,
                            "unit_price": 200}],
        })
    assert r.status_code == 200, r.text

    # Now hit the legacy /supplier_pay endpoint.
    r = requests.post(
        f"{BASE_URL}/api/accounting/suppliers/{sup_id}/pay",
        headers=h, json={
            "amount": 80,
            "paid_from_account_id": ctx["bank_id"],
            "payment_date": "2026-02-06",
            "notes": "test",
        })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True

    # Bridge created lazily.
    client = AsyncIOMotorClient(MONGO_URL)
    try:
        db = client[DB_NAME]
        cp = await db.counterparties.find_one(
            {"id": sup_id, "user_id": uid})
        assert cp is not None
        assert cp["kind"] == "supplier"
        assert cp["name"] == "مورد قبل-الجسر"
    finally:
        client.close()

    # Balance reduced by the payment (200 invoice − 80 paid = 120 left)
    r = requests.get(
        f"{BASE_URL}/api/ledger/balance",
        params={"entity_type": "supplier", "entity_id": sup_id},
        headers=h)
    assert r.status_code == 200
    assert round(r.json().get("outstanding_debt"), 2) == 120.0
