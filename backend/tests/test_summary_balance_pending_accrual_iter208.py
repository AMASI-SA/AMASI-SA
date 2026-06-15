"""Iter-208 — Fix for /accounting/employees/{id}/summary-balance.

The "Add Transaction → صرف راتب موظف" UI was showing balance=0 for
employees with post-cutoff salary accrual because the
`summary-balance` endpoint read only from the frozen ledger snapshot,
missing the dynamic delta added in Iter-203.

This test asserts the endpoint now adds `pending_accrual` to the
`salary_payable` returned to the UI, exactly like
/employees/list and /employees/{id}/financial-summary already do.
"""
import os
import sys
import uuid
from datetime import date, timedelta

import pytest
from dotenv import load_dotenv
from httpx import ASGITransport, AsyncClient

load_dotenv("/app/backend/.env")
sys.path.insert(0, "/app/backend")
from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402
from server import app  # noqa: E402


@pytest.mark.asyncio
async def test_summary_balance_includes_pending_accrual():
    async with AsyncClient(transport=ASGITransport(app=app),
                            base_url="http://test") as client:
        email = f"sb208-{os.urandom(3).hex()}@test.com"
        r = await client.post("/api/auth/register", json={
            "name": "A", "email": email, "password": "pass1234",
        })
        assert r.status_code == 200
        token = r.json()["access_token"]
        uid = r.json()["id"]
        h = {"Authorization": f"Bearer {token}"}

        c = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = c[os.environ["DB_NAME"]]
        try:
            today = date.today()
            emp_id = str(uuid.uuid4())
            # Employee with 3000 SAR monthly, started 10 days ago.
            await db.operating_salaries.insert_one({
                "id": emp_id, "user_id": uid, "name": "Test Emp",
                "category": "employee", "monthly_amount": 3000,
                "status": "active",
                "start_date": (today - timedelta(days=10)).isoformat(),
            })

            r = await client.get(
                f"/api/accounting/employees/{emp_id}/summary-balance",
                headers=h)
            assert r.status_code == 200, r.text
            data = r.json()

            # Before Iter-208 fix: salary_payable=0 (only ledger).
            # After Iter-208 fix: salary_payable ≈ 10 days × 3000/30 = 1000
            assert data["salary_payable_ledger"] == 0.0, data
            assert data["pending_accrual"] > 0, data
            assert abs(data["salary_payable"]
                       - data["pending_accrual"]) < 0.05
            # net_due ≈ salary_payable (no advances/custody seeded).
            assert data["net_due_to_employee"] == data["salary_payable"]
            # 10 days × (3000/30) = 1000 SAR — accept generous tolerance
            # (calendar month length varies, plus the day boundary).
            assert 800 < data["salary_payable"] < 1300, data
        finally:
            await db.operating_salaries.delete_many({"user_id": uid})
            await db.general_ledger.delete_many({"user_id": uid})
            await db.users.delete_many({"id": uid})
            c.close()
