"""Iter-188 — Golden Rule guard: block advance_grant when employee has
open salary_payable, suggest salary_settle instead.

Verified scenarios
==================
1. Employee with ZERO salary_payable → advance_grant succeeds (no
   regression for normal advances).
2. Employee with OPEN salary_payable → advance_grant returns 409 with
   a structured payload the UI uses to offer the conversion:
     { code: "PENDING_SALARY_BLOCK", salary_payable, employee_id,
       employee_name, message }
3. Re-submitting with acknowledge_pending_salary=true bypasses the
   guard for the rare case where the advance is genuinely separate
   from the pending salary (e.g. a personal loan from the company).
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
async def test_golden_rule_blocks_advance_when_salary_is_pending():
    async with AsyncClient(transport=ASGITransport(app=app),
                           base_url="http://test") as client:
        email = f"golden-{os.urandom(3).hex()}@test.com"
        r = await client.post("/api/auth/register",
                              json={"name": "G", "email": email,
                                    "password": "pass1234"})
        token = r.json()["access_token"]
        uid = r.json()["id"]
        h = {"Authorization": f"Bearer {token}"}

        c = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = c[os.environ["DB_NAME"]]

        emp_clean = str(uuid.uuid4())
        emp_with_salary = str(uuid.uuid4())
        bank = str(uuid.uuid4())
        await db.operating_salaries.insert_many([
            {"id": emp_clean, "user_id": uid, "name": "أحمد",
             "category": "employee", "monthly_amount": 5000,
             "status": "active"},
            {"id": emp_with_salary, "user_id": uid, "name": "خالد",
             "category": "employee", "monthly_amount": 5000,
             "status": "active"},
        ])
        await db.accounts.insert_one({
            "id": bank, "user_id": uid, "account_type": "bank",
            "name": "الراجحي", "current_balance": 50_000,
            "balance": 50_000,
        })

        # ── 1) Clean employee → advance is allowed ──
        r = await client.post(
            f"/api/accounting/employees/{emp_clean}/advances", headers=h,
            json={"amount": 1000, "paid_from_account_id": bank},
        )
        assert r.status_code == 200, r.text

        # Salary accrual on emp_with_salary so they have payable.
        r = await client.post(
            f"/api/accounting/employees/{emp_with_salary}/salary-accrual",
            headers=h,
            json={"amount": 8800, "period": "2026-06"},
        )
        assert r.status_code == 200, r.text

        # ── 2) Employee with pending salary → 409 with suggestion ──
        r = await client.post(
            f"/api/accounting/employees/{emp_with_salary}/advances",
            headers=h,
            json={"amount": 5000, "paid_from_account_id": bank},
        )
        assert r.status_code == 409, r.text
        detail = r.json()["detail"]
        assert detail["code"] == "PENDING_SALARY_BLOCK"
        assert detail["salary_payable"] == 8800
        assert detail["employee_id"] == emp_with_salary
        assert detail["employee_name"] == "خالد"
        assert "صرف راتب" in detail["message"]
        assert "سلفة موظف" in detail["message"]

        # ── 3) Override with acknowledge_pending_salary=true ──
        r = await client.post(
            f"/api/accounting/employees/{emp_with_salary}/advances",
            headers=h,
            json={"amount": 5000, "paid_from_account_id": bank,
                  "acknowledge_pending_salary": True,
                  "notes": "قرض شخصي خارج الراتب"},
        )
        assert r.status_code == 200, r.text

        # ── 4) Conversion path: pay via salary_settle works as before ──
        r = await client.post(
            f"/api/accounting/employees/{emp_with_salary}/settle",
            headers=h,
            json={"amount": 8800, "paid_from_account_id": bank,
                  "apply_open_advances": False},
        )
        assert r.status_code == 200, r.text

        # ── 5) Now salary_payable=0 → advance is allowed again w/o flag ──
        r = await client.post(
            f"/api/accounting/employees/{emp_with_salary}/advances",
            headers=h,
            json={"amount": 200, "paid_from_account_id": bank},
        )
        assert r.status_code == 200, r.text
