"""Iter-183 — Custody Transfer between Employees + Open Balances Report.

Covers the new feature:
  • POST /api/accounting/employees/custody/transfer
  • GET  /api/accounting/employees/custody/open-balances

Business rules verified
=======================
1. Transfer creates a balanced 2-entry txn_group:
     debit:  to_employee.custody  +amount
     credit: from_employee.custody -amount
2. Same-employee transfer is rejected.
3. Transferring more than the open custody balance is rejected.
4. Bank/cash accounts are NOT touched by the transfer.
5. open-balances breaks down per employee:
     granted / settled_receipts / returned_cash /
     transferred_in / transferred_out / opening / open_balance
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
async def test_custody_transfer_and_open_balances_report():
    async with AsyncClient(transport=ASGITransport(app=app),
                           base_url="http://test") as client:
        email = f"custody-{os.urandom(3).hex()}@test.com"
        r = await client.post("/api/auth/register",
                              json={"name": "C", "email": email,
                                    "password": "pass1234"})
        token = r.json()["access_token"]
        uid = r.json()["id"]
        h = {"Authorization": f"Bearer {token}"}

        c = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = c[os.environ["DB_NAME"]]

        emp_a = str(uuid.uuid4())
        emp_b = str(uuid.uuid4())
        bank = str(uuid.uuid4())
        await db.operating_salaries.insert_many([
            {"id": emp_a, "user_id": uid, "name": "أحمد",
             "category": "employee", "monthly_amount": 0, "status": "active"},
            {"id": emp_b, "user_id": uid, "name": "خالد",
             "category": "employee", "monthly_amount": 0, "status": "active"},
        ])
        await db.accounts.insert_one({
            "id": bank, "user_id": uid, "account_type": "bank",
            "name": "الراجحي", "balance": 0,
        })

        # Grant 2,000 custody to A.
        r = await client.post(
            f"/api/accounting/employees/{emp_a}/custody", headers=h,
            json={"amount": 2000, "paid_from_account_id": bank},
        )
        assert r.status_code == 200, r.text

        # ── Negative cases ──────────────────────────────────────
        # Same employee on both sides → rejected.
        r = await client.post(
            "/api/accounting/employees/custody/transfer", headers=h,
            json={"from_employee_id": emp_a, "to_employee_id": emp_a,
                  "amount": 100},
        )
        assert r.status_code == 400
        assert "نفس الموظف" in r.json()["detail"]

        # Amount > balance → rejected.
        r = await client.post(
            "/api/accounting/employees/custody/transfer", headers=h,
            json={"from_employee_id": emp_a, "to_employee_id": emp_b,
                  "amount": 9_999},
        )
        assert r.status_code == 400
        assert "أقل" in r.json()["detail"]

        # Unknown employee → 404.
        r = await client.post(
            "/api/accounting/employees/custody/transfer", headers=h,
            json={"from_employee_id": str(uuid.uuid4()),
                  "to_employee_id": emp_b, "amount": 100},
        )
        assert r.status_code == 404

        # ── Happy path ─────────────────────────────────────────
        r = await client.post(
            "/api/accounting/employees/custody/transfer", headers=h,
            json={"from_employee_id": emp_a, "to_employee_id": emp_b,
                  "amount": 700, "notes": "نقل عهدة لخالد"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ok"] is True
        assert body["debit_total"] == 700
        assert body["credit_total"] == 700
        assert len(body["entries"]) == 2
        # A had 2,000 → now 1,300.
        assert body["from_balance"]["net_balance"] == 1300.0
        # B was 0 → now 700.
        assert body["to_balance"]["net_balance"] == 700.0

        # Verify both entries share txn_group_id, sub_account=custody.
        gid = body["txn_group_id"]
        rows = await db.general_ledger.find(
            {"txn_group_id": gid}, {"_id": 0}).to_list(10)
        assert len(rows) == 2
        for row in rows:
            assert row["entity_type"] == "employee"
            assert row["sub_account"] == "custody"
            assert row["entry_type"] == "custody_transfer"
            assert row["amount"] == 700
        debit_row  = next(x for x in rows if x["side"] == "debit")
        credit_row = next(x for x in rows if x["side"] == "credit")
        assert debit_row["entity_id"] == emp_b
        assert credit_row["entity_id"] == emp_a
        # Bank/cash must NOT be touched.
        bank_rows = await db.general_ledger.find(
            {"txn_group_id": gid, "entity_type": "bank"}).to_list(5)
        assert bank_rows == []

        # ── Open balances report ───────────────────────────────
        r = await client.get(
            "/api/accounting/employees/custody/open-balances", headers=h,
        )
        assert r.status_code == 200
        rep = r.json()
        rows = {x["employee_id"]: x for x in rep["rows"]}
        assert emp_a in rows and emp_b in rows
        a = rows[emp_a]
        b = rows[emp_b]
        assert a["granted"] == 2000
        assert a["transferred_out"] == 700
        assert a["transferred_in"] == 0
        assert a["open_balance"] == 1300
        assert b["granted"] == 0
        assert b["transferred_in"] == 700
        assert b["transferred_out"] == 0
        assert b["open_balance"] == 700
        # Total must equal sum of net opens (= 2,000 → A still owes 1300, B 700).
        assert abs(rep["total_open_balance"] - 2000) < 0.01
