"""Iter-171 — Economic net (display-only) for employees.

Validates the formula:
  economic_net = salary_payable − advance − custody
  verdict     = owed_to_employee  if > +0.01
                owed_by_employee  if < −0.01
                balanced          otherwise

The underlying ledger keeps the 3 sub_accounts separate — this is an
aggregate VIEW used by the UI to answer «does the employee owe us or
do we owe him?».
"""
import os
import sys
import uuid
from datetime import datetime, timezone, timedelta

import pytest
from dotenv import load_dotenv
from httpx import ASGITransport, AsyncClient

load_dotenv("/app/backend/.env")
sys.path.insert(0, "/app/backend")
from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402
from server import app  # noqa: E402


@pytest.mark.asyncio
async def test_economic_net_in_reconciliation_report():
    """Recreate شهاب's production scenario:
       salary_payable=100, advance=2895, custody=0 → net=−2795
       verdict='owed_by_employee'."""
    async with AsyncClient(transport=ASGITransport(app=app),
                            base_url="http://test") as client:
        email = f"econ171-{os.urandom(3).hex()}@test.com"
        r = await client.post("/api/auth/register",
                              json={"name": "E", "email": email,
                                    "password": "pass1234"})
        token = r.json()["access_token"]
        uid = r.json()["id"]
        h = {"Authorization": f"Bearer {token}"}

        c = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = c[os.environ["DB_NAME"]]
        emp_id = str(uuid.uuid4())

        try:
            # Seed employee whose accrual ≈ 4400 (30 days × 4400/30) with
            # 4300 already paid → salary_payable = 100.
            start = (datetime.now(timezone.utc).date()
                     - timedelta(days=30))
            await db.operating_salaries.insert_one({
                "id": emp_id, "user_id": uid, "name": "شهاب",
                "category": "employee", "status": "active",
                "monthly_amount": 4400, "start_date": start.isoformat(),
            })
            # Cash paid 4300
            await db.liabilities.insert_one({
                "id": str(uuid.uuid4()), "user_id": uid,
                "kind": "salary", "employee_salary_id": emp_id,
                "expected_amount": 4400, "paid_amount": 4300,
                "advance_deducted": 0, "status": "partial",
            })
            # Open advance: expected 2895, consumed 0 → remaining 2895
            await db.liabilities.insert_one({
                "id": str(uuid.uuid4()), "user_id": uid,
                "kind": "salary_advance", "employee_salary_id": emp_id,
                "expected_amount": 2895, "paid_amount": 2895,
                "consumed_amount": 0,
                "status": "paid", "advance_status": "open",
            })

            r = await client.get(
                "/api/accounting/migration/reconciliation", headers=h)
            d = r.json()
            shahab = next(e for e in d["employees"]
                          if e["id"] == emp_id)
            # Verify the 3 components
            assert shahab["salary_payable"]["legacy"] >= 100  # close to 100
            assert shahab["advance"]["legacy"] == 2895.0
            assert shahab["custody"]["legacy"] == 0.0
            # Verify the economic_net field
            en = shahab["economic_net"]
            expected_net = round(
                shahab["salary_payable"]["legacy"] - 2895.0 - 0, 2)
            assert en["legacy"] == expected_net
            # Since salary_payable < advance → net is negative
            assert en["legacy"] < 0
            assert en["verdict"] == "owed_by_employee"
            # owed_by_employee = absolute value of negative net
            assert en["owed_by_employee"] == abs(expected_net)
            assert en["owed_to_employee"] == 0.0
        finally:
            await db.operating_salaries.delete_many({"user_id": uid})
            await db.liabilities.delete_many({"user_id": uid})


@pytest.mark.asyncio
async def test_economic_net_balanced_when_zero():
    async with AsyncClient(transport=ASGITransport(app=app),
                            base_url="http://test") as client:
        email = f"econ171b-{os.urandom(3).hex()}@test.com"
        r = await client.post("/api/auth/register",
                              json={"name": "B", "email": email,
                                    "password": "pass1234"})
        token = r.json()["access_token"]
        uid = r.json()["id"]
        h = {"Authorization": f"Bearer {token}"}

        c = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = c[os.environ["DB_NAME"]]
        emp_id = str(uuid.uuid4())

        try:
            # Employee with no accrual (no start_date) + no liabilities.
            await db.operating_salaries.insert_one({
                "id": emp_id, "user_id": uid, "name": "موظف جديد",
                "category": "employee", "status": "active",
                "monthly_amount": 3000,
            })
            r = await client.get(
                "/api/accounting/migration/reconciliation", headers=h)
            d = r.json()
            emp = next(e for e in d["employees"]
                       if e["id"] == emp_id)
            assert emp["economic_net"]["legacy"] == 0.0
            assert emp["economic_net"]["verdict"] == "balanced"
        finally:
            await db.operating_salaries.delete_many({"user_id": uid})
