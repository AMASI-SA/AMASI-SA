"""Iter-161 Phase 4 — Ledger-only list endpoints + financial position."""
import os
import uuid
from datetime import datetime, timezone

import pytest
from dotenv import load_dotenv
from httpx import ASGITransport, AsyncClient

load_dotenv("/app/backend/.env")
import sys
sys.path.insert(0, "/app/backend")
from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402
from server import app  # noqa: E402


@pytest.mark.asyncio
async def test_phase4_listings_and_financial_position():
    async with AsyncClient(transport=ASGITransport(app=app),
                            base_url="http://test") as client:
        email = f"p4-{os.urandom(3).hex()}@test.com"
        r = await client.post("/api/auth/register",
                              json={"name": "T4", "email": email,
                                    "password": "pass1234"})
        token = r.json()["access_token"]
        uid = r.json()["id"]
        h = {"Authorization": f"Bearer {token}"}

        c = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = c[os.environ["DB_NAME"]]

        # Seed: 2 employees, 2 suppliers, 1 external, 1 bank, 1 courier
        emp1, emp2 = str(uuid.uuid4()), str(uuid.uuid4())
        sup1, sup2 = str(uuid.uuid4()), str(uuid.uuid4())
        ext1 = str(uuid.uuid4())
        bank = str(uuid.uuid4())
        cour = str(uuid.uuid4())
        await db.operating_salaries.insert_many([
            {"id": emp1, "user_id": uid, "name": "أحمد", "category": "employee",
             "monthly_amount": 3000, "status": "active"},
            {"id": emp2, "user_id": uid, "name": "خالد", "category": "employee",
             "monthly_amount": 4000, "status": "active"},
        ])
        await db.counterparties.insert_many([
            {"id": sup1, "user_id": uid, "kind": "supplier", "name": "Sup1", "name_lower": "sup1"},
            {"id": sup2, "user_id": uid, "kind": "supplier", "name": "Sup2", "name_lower": "sup2"},
            {"id": ext1, "user_id": uid, "kind": "external", "name": "علي", "name_lower": "علي"},
            {"id": cour, "user_id": uid, "kind": "courier", "name": "SMSA", "name_lower": "smsa"},
        ])
        await db.accounts.insert_one({
            "id": bank, "user_id": uid, "account_type": "bank",
            "name": "الراجحي", "balance": 0,
        })

        # Post a bunch of ledger entries
        await client.post(f"/api/accounting/employees/{emp1}/advances", headers=h,
            json={"amount": 500, "paid_from_account_id": bank})
        await client.post(f"/api/accounting/employees/{emp1}/salary-accrual", headers=h,
            json={"amount": 3000, "period": "2026-06"})
        await client.post(f"/api/accounting/employees/{emp2}/custody", headers=h,
            json={"amount": 2000, "paid_from_account_id": bank})
        await client.post(f"/api/accounting/suppliers/{sup1}/invoice", headers=h,
            json={"amount": 10000, "expense_category": "inventory"})
        await client.post(f"/api/accounting/suppliers/{sup1}/pay", headers=h,
            json={"amount": 4000, "paid_from_account_id": bank})
        await client.post(f"/api/accounting/suppliers/{sup2}/invoice", headers=h,
            json={"amount": 5000, "expense_category": "shipping"})
        await client.post(f"/api/accounting/external-persons/{ext1}/grant", headers=h,
            json={"amount": 1500, "paid_from_account_id": bank})
        await client.post(f"/api/accounting/couriers/{cour}/charge", headers=h,
            json={"amount": 800, "expense_category": "shipping"})

        # ── /employees/list ─────────────────────────────────────
        r = await client.get("/api/accounting/employees/list", headers=h)
        data = r.json()
        assert len(data["employees"]) == 2
        e1 = next(x for x in data["employees"] if x["id"] == emp1)
        assert e1["salary_payable"] == 3000.0
        assert e1["advance"] == 500.0
        e2 = next(x for x in data["employees"] if x["id"] == emp2)
        assert e2["custody"] == 2000.0
        assert data["totals"]["salary_payable"] == 3000.0
        assert data["totals"]["advance"] == 500.0
        assert data["totals"]["custody"] == 2000.0

        # ── /suppliers/list ─────────────────────────────────────
        r = await client.get("/api/accounting/suppliers/list", headers=h)
        data = r.json()
        assert len(data["suppliers"]) == 2
        s1 = next(x for x in data["suppliers"] if x["id"] == sup1)
        assert s1["outstanding_debt"] == 6000.0  # 10000 - 4000
        assert data["totals"]["outstanding_debt"] == 11000.0

        # ── /externals/list ─────────────────────────────────────
        r = await client.get("/api/accounting/externals/list", headers=h)
        data = r.json()
        assert len(data["externals"]) == 1
        assert data["totals"]["receivable"] == 1500.0

        # ── /couriers/list ──────────────────────────────────────
        r = await client.get("/api/accounting/couriers/list", headers=h)
        data = r.json()
        assert data["totals"]["payable"] == 800.0

        # ── /financial-position ─────────────────────────────────
        r = await client.get("/api/accounting/financial-position", headers=h)
        fp = r.json()
        # bank: started 0, spent on advances(500)+custody(2000)+pay(4000)+grant(1500) = 8000 out
        assert fp["assets"]["bank"] == -8000.0
        assert fp["assets"]["employee_advance"] == 500.0
        assert fp["assets"]["employee_custody"] == 2000.0
        assert fp["assets"]["external_receivable"] == 1500.0
        assert fp["liabilities"]["employee_salary_payable"] == 3000.0
        assert fp["liabilities"]["supplier_payable"] == 11000.0
        assert fp["liabilities"]["courier_payable"] == 800.0
        # Net position = -8000 + 500 + 2000 + 1500 - 3000 - 11000 - 800 = -18800
        # (This is intentionally negative because the test only posts
        # obligations + grants, no actual cash inflow. The math is what matters.)
        expected_net = (-8000 + 500 + 2000 + 1500) - (3000 + 11000 + 800)
        assert fp["totals"]["net_position"] == round(expected_net, 2)
        assert fp["source"].startswith("general_ledger")

        # Cleanup
        await db.operating_salaries.delete_many({"user_id": uid})
        await db.counterparties.delete_many({"user_id": uid})
        await db.accounts.delete_many({"user_id": uid})
        await db.general_ledger.delete_many({"user_id": uid})
        await db.accounting_audit_log.delete_many({"user_id": uid})
        await db.expense_categories.delete_many({"user_id": uid})
