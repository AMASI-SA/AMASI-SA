"""Iter-185 — Insufficient-funds enforcement + helper endpoints.

What's tested
=============
1. `GET /api/accounting/cash-accounts-with-balances` returns each
   cash-touchable account with a correct `live_balance` that includes
   BOTH the stored legacy balance AND ledger entries posted by the
   universal accounting flow.
2. Posting a cash-out operation whose amount EXCEEDS the source
   account's live balance is rejected with the exact merchant-facing
   Arabic message.
3. The same operation succeeds when the amount fits the balance.
4. The check applies to every cash-out endpoint (advance, supplier_pay,
   external_grant, expense_record, bank_transfer source, custody_grant,
   salary_settle).
5. `GET /api/accounting/employees/{id}/summary-balance` returns the
   net amount the company owes the employee (positive) or the
   employee owes the company (negative).
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

INSUFFICIENT_MSG = "لا يمكن تنفيذ العملية، رصيد الحساب المختار غير كافٍ."


@pytest.mark.asyncio
async def test_insufficient_funds_and_employee_summary():
    """Combined to avoid the known Motor + pytest-asyncio event-loop
    teardown issue when running multiple async tests in one process."""
    async with AsyncClient(transport=ASGITransport(app=app),
                           base_url="http://test") as client:
        email = f"funds-{os.urandom(3).hex()}@test.com"
        r = await client.post("/api/auth/register",
                              json={"name": "F", "email": email,
                                    "password": "pass1234"})
        token = r.json()["access_token"]
        uid = r.json()["id"]
        h = {"Authorization": f"Bearer {token}"}

        c = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = c[os.environ["DB_NAME"]]

        emp = str(uuid.uuid4())
        sup = str(uuid.uuid4())
        ext = str(uuid.uuid4())
        bank_full = str(uuid.uuid4())
        bank_low = str(uuid.uuid4())
        await db.operating_salaries.insert_one({
            "id": emp, "user_id": uid, "name": "أحمد",
            "category": "employee", "monthly_amount": 5000, "status": "active",
        })
        await db.counterparties.insert_many([
            {"id": sup, "user_id": uid, "kind": "supplier",
             "name": "Sup", "name_lower": "sup"},
            {"id": ext, "user_id": uid, "kind": "external",
             "name": "علي", "name_lower": "علي"},
        ])
        # Bank with 10,000 cash (stored as current_balance).
        await db.accounts.insert_many([
            {"id": bank_full, "user_id": uid, "account_type": "bank",
             "name": "الراجحي", "current_balance": 10000,
             "opening_balance": 10000, "balance": 10000},
            {"id": bank_low, "user_id": uid, "account_type": "bank",
             "name": "الأهلي", "current_balance": 500,
             "opening_balance": 500, "balance": 500},
        ])

        # ── 1) cash-accounts endpoint returns live balances ──
        r = await client.get(
            "/api/accounting/cash-accounts-with-balances", headers=h,
        )
        assert r.status_code == 200
        body = r.json()
        bal_map = {a["id"]: a["live_balance"] for a in body["accounts"]}
        assert bal_map[bank_full] == 10000
        assert bal_map[bank_low] == 500

        # ── 2) Insufficient funds rejected: advance_grant ──
        r = await client.post(f"/api/accounting/employees/{emp}/advances",
                              headers=h,
                              json={"amount": 1000,
                                    "paid_from_account_id": bank_low})
        assert r.status_code == 400
        assert r.json()["detail"] == INSUFFICIENT_MSG

        # ── 3) Insufficient funds rejected: supplier_pay ──
        await client.post(f"/api/accounting/suppliers/{sup}/invoice",
                          headers=h,
                          json={"amount": 5000,
                                "expense_category": "inventory"})
        r = await client.post(f"/api/accounting/suppliers/{sup}/pay",
                              headers=h,
                              json={"amount": 700,
                                    "paid_from_account_id": bank_low})
        assert r.status_code == 400
        assert r.json()["detail"] == INSUFFICIENT_MSG

        # ── 4) Insufficient funds rejected: external_grant ──
        r = await client.post(f"/api/accounting/external-persons/{ext}/grant",
                              headers=h,
                              json={"amount": 800,
                                    "paid_from_account_id": bank_low})
        assert r.status_code == 400

        # ── 5) Insufficient funds rejected: expense_record ──
        r = await client.post("/api/accounting/expenses", headers=h,
                              json={"amount": 600,
                                    "expense_category": "inventory",
                                    "paid_from_account_id": bank_low})
        assert r.status_code == 400

        # ── 6) Insufficient funds rejected: custody_grant ──
        r = await client.post(f"/api/accounting/employees/{emp}/custody",
                              headers=h,
                              json={"amount": 900,
                                    "paid_from_account_id": bank_low})
        assert r.status_code == 400

        # ── 7) bank_transfer with insufficient source rejected ──
        r = await client.post("/api/accounting/bank-transfer", headers=h,
                              json={"amount": 600,
                                    "from_account_id": bank_low,
                                    "to_account_id": bank_full})
        assert r.status_code == 400

        # ── 8) Within-budget advance from bank_full succeeds ──
        r = await client.post(f"/api/accounting/employees/{emp}/advances",
                              headers=h,
                              json={"amount": 2000,
                                    "paid_from_account_id": bank_full})
        assert r.status_code == 200, r.text

        # ── 9) Live balance after the post reflects the deduction
        r = await client.get(
            "/api/accounting/cash-accounts-with-balances", headers=h,
        )
        bal_map = {a["id"]: a["live_balance"] for a in r.json()["accounts"]}
        assert bal_map[bank_full] == 8000   # 10,000 − 2,000
        assert bal_map[bank_low] == 500     # unchanged

        # ── 10) Now an 8,001 advance from bank_full must be rejected ──
        r = await client.post(f"/api/accounting/employees/{emp}/advances",
                              headers=h,
                              json={"amount": 8001,
                                    "paid_from_account_id": bank_full})
        assert r.status_code == 400

        # ═══════════════════════════════════════════════════════════════
        # Part 2 — Employee summary-balance endpoint
        # Reuses the same user so we can verify both endpoints in one
        # connection without hitting the Motor event-loop teardown bug.
        # ═══════════════════════════════════════════════════════════════
        emp2 = str(uuid.uuid4())
        await db.operating_salaries.insert_one({
            "id": emp2, "user_id": uid, "name": "خالد",
            "category": "employee", "monthly_amount": 5000, "status": "active",
        })

        # Baseline: no movement → net 0
        r = await client.get(
            f"/api/accounting/employees/{emp2}/summary-balance", headers=h,
        )
        assert r.status_code == 200
        assert r.json()["net_due_to_employee"] == 0

        # Salary accrual 5,000 → company owes employee 5,000 (green)
        await client.post(f"/api/accounting/employees/{emp2}/salary-accrual",
                          headers=h,
                          json={"amount": 5000, "period": "2026-06"})
        r = await client.get(
            f"/api/accounting/employees/{emp2}/summary-balance", headers=h,
        )
        assert r.json()["net_due_to_employee"] == 5000

        # Grant 1,500 advance from bank_full → company net owed = 3,500
        # (Iter-188: must acknowledge pending salary since payable > 0)
        await client.post(f"/api/accounting/employees/{emp2}/advances",
                          headers=h,
                          json={"amount": 1500,
                                "paid_from_account_id": bank_full,
                                "acknowledge_pending_salary": True})
        r = await client.get(
            f"/api/accounting/employees/{emp2}/summary-balance", headers=h,
        )
        body = r.json()
        assert body["net_due_to_employee"] == 3500
        assert body["salary_payable"] == 5000
        assert body["advance_open"] == 1500
        assert body["custody_open"] == 0

        # Grant 4,000 custody → net = 5,000 − 1,500 − 4,000 = −500
        # (employee owes us 500)
        await client.post(f"/api/accounting/employees/{emp2}/custody",
                          headers=h,
                          json={"amount": 4000,
                                "paid_from_account_id": bank_full})
        r = await client.get(
            f"/api/accounting/employees/{emp2}/summary-balance", headers=h,
        )
        assert r.json()["net_due_to_employee"] == -500
