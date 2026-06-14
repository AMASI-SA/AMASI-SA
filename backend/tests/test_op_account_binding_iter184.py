"""Iter-184 — Operation→Accounts Binding Enforcement.

Two behaviours under test:
  1. With NO binding configured (the default) every account remains
     allowed → backwards compatible.
  2. Once the merchant sets a binding for an op (e.g. allow ONLY bank A
     for supplier_pay), the backend rejects supplier_pay attempts that
     reference any OTHER account, EVEN IF the request is well-formed
     otherwise. Same enforcement layer for both UI and API callers.
"""
import os
import uuid

import pytest
from dotenv import load_dotenv
from httpx import ASGITransport, AsyncClient

load_dotenv("/app/backend/.env")
import sys
sys.path.insert(0, "/app/backend")
from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402
from server import app  # noqa: E402


@pytest.mark.asyncio
async def test_operation_account_binding_enforcement():
    async with AsyncClient(transport=ASGITransport(app=app),
                           base_url="http://test") as client:
        email = f"bind-{os.urandom(3).hex()}@test.com"
        r = await client.post("/api/auth/register",
                              json={"name": "B", "email": email,
                                    "password": "pass1234"})
        token = r.json()["access_token"]
        uid = r.json()["id"]
        h = {"Authorization": f"Bearer {token}"}

        c = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = c[os.environ["DB_NAME"]]

        # Seed: 1 employee, 1 supplier, 2 banks (A allowed, B disallowed).
        emp = str(uuid.uuid4())
        sup = str(uuid.uuid4())
        bank_a = str(uuid.uuid4())
        bank_b = str(uuid.uuid4())
        await db.operating_salaries.insert_one({
            "id": emp, "user_id": uid, "name": "أحمد",
            "category": "employee", "monthly_amount": 0, "status": "active",
        })
        await db.counterparties.insert_one({
            "id": sup, "user_id": uid, "kind": "supplier", "name": "Sup",
            "name_lower": "sup",
        })
        await db.accounts.insert_many([
            {"id": bank_a, "user_id": uid, "account_type": "bank",
             "name": "الراجحي", "balance": 0},
            {"id": bank_b, "user_id": uid, "account_type": "bank",
             "name": "الأهلي", "balance": 0},
        ])

        # ── 1) GET /settings returns the new fields with sensible defaults
        r = await client.get("/api/settings", headers=h)
        body = r.json()
        assert body["operation_account_bindings"] == {}
        assert body["hidden_transaction_types"] == []

        # ── 2) BEFORE configuring binding → both banks allowed
        # We need a supplier invoice first so there's payable to settle.
        r = await client.post(f"/api/accounting/suppliers/{sup}/invoice",
                              headers=h,
                              json={"amount": 1000,
                                    "expense_category": "inventory"})
        assert r.status_code == 200, r.text

        # Pay from bank_b — should work (no binding configured).
        r = await client.post(f"/api/accounting/suppliers/{sup}/pay",
                              headers=h,
                              json={"amount": 100,
                                    "paid_from_account_id": bank_b})
        assert r.status_code == 200, r.text

        # ── 3) Configure binding: supplier_pay restricted to bank_a only.
        # First read the existing settings so PUT keeps required fields.
        cur = await client.get("/api/settings", headers=h)
        payload = cur.json()
        payload["operation_account_bindings"] = {
            "supplier_pay": [bank_a],
            "advance_grant": [bank_a],
        }
        r = await client.put("/api/settings", headers=h, json=payload)
        assert r.status_code == 200, r.text

        # ── 4) AFTER binding: paying from bank_b is rejected (400)
        r = await client.post(f"/api/accounting/suppliers/{sup}/pay",
                              headers=h,
                              json={"amount": 100,
                                    "paid_from_account_id": bank_b})
        assert r.status_code == 400, r.text
        assert "غير مسموح" in r.json()["detail"]

        # ── 5) AFTER binding: paying from bank_a is still allowed
        r = await client.post(f"/api/accounting/suppliers/{sup}/pay",
                              headers=h,
                              json={"amount": 100,
                                    "paid_from_account_id": bank_a})
        assert r.status_code == 200, r.text

        # ── 6) Binding for ANOTHER op should not affect supplier_pay
        # (regression: ensure bindings are per-op, not global).
        # advance_grant is bound to bank_a too; granting from bank_b
        # must be rejected — and supplier_pay from bank_a must still work.
        r = await client.post(f"/api/accounting/employees/{emp}/advances",
                              headers=h,
                              json={"amount": 50,
                                    "paid_from_account_id": bank_b})
        assert r.status_code == 400
        r = await client.post(f"/api/accounting/employees/{emp}/advances",
                              headers=h,
                              json={"amount": 50,
                                    "paid_from_account_id": bank_a})
        assert r.status_code == 200, r.text

        # ── 7) GET /settings reflects the newly saved bindings.
        r = await client.get("/api/settings", headers=h)
        body = r.json()
        assert body["operation_account_bindings"] == {
            "supplier_pay": [bank_a],
            "advance_grant": [bank_a],
        }

        # ── 8) Clearing the binding (empty list) restores allow-all.
        payload["operation_account_bindings"] = {
            "supplier_pay": [],
            "advance_grant": [bank_a],
        }
        r = await client.put("/api/settings", headers=h, json=payload)
        assert r.status_code == 200
        # supplier_pay from bank_b allowed again
        r = await client.post(f"/api/accounting/suppliers/{sup}/pay",
                              headers=h,
                              json={"amount": 10,
                                    "paid_from_account_id": bank_b})
        assert r.status_code == 200, r.text
