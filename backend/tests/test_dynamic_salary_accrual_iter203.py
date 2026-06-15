"""Iter-203 — Dynamic Post-Cutoff Salary Accrual tests.

After Phase-4 migration (Iter-161), salary_payable in `general_ledger`
is FROZEN at the cutoff snapshot.  This iteration adds a transparent
display-time delta that surfaces the daily accrual since the cutoff so
the Employees screen reflects today's reality even when the ledger
hasn't been explicitly accrued.

Guarantees enforced here:
    1. New user (no migration) → today's accrual still appears on the
       Employees screen even with ZERO ledger entries.
    2. Migrated user (cutoff in the past) → ledger payable + post-cutoff
       days delta shown on the screen.
    3. Explicit salary_accrual ledger posts after cutoff are NOT
       double-counted.
    4. financial-summary endpoint returns the same combined value.
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
async def test_post_cutoff_dynamic_accrual_full_lifecycle():
    async with AsyncClient(transport=ASGITransport(app=app),
                            base_url="http://test") as client:
        email = f"acc203-{os.urandom(3).hex()}@test.com"
        r = await client.post("/api/auth/register", json={
            "name": "A", "email": email, "password": "pass1234",
        })
        assert r.status_code == 200, r.text
        token = r.json()["access_token"]
        uid = r.json()["id"]
        h = {"Authorization": f"Bearer {token}"}

        c = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = c[os.environ["DB_NAME"]]

        try:
            today = date.today()
            # ─── Scenario 1: NEW user (no migration cutoff) ─────
            # An active employee with monthly 3000 SAR started 10 days
            # ago should accrue ~3000 * (10 / days_in_month).
            emp1 = str(uuid.uuid4())
            start_10d_ago = (today - timedelta(days=10)).isoformat()
            await db.operating_salaries.insert_one({
                "id": emp1, "user_id": uid, "name": "موظف 1",
                "category": "employee", "monthly_amount": 3000,
                "status": "active", "start_date": start_10d_ago,
            })

            r = await client.get(
                "/api/accounting/employees/list", headers=h)
            assert r.status_code == 200, r.text
            data = r.json()
            assert data["cutoff_date"] is None
            row1 = next(e for e in data["employees"] if e["id"] == emp1)
            assert row1["salary_payable"] > 0, (
                "No-migration user should still see accrual"
            )
            assert row1["pending_accrual"] > 0
            # ledger had no entries → pending_accrual ≈ salary_payable
            assert abs(row1["pending_accrual"]
                       - row1["salary_payable"]) < 0.05
            assert row1["salary_payable_ledger"] == 0.0

            # ─── Scenario 2: MIGRATED user with cutoff in past ──
            # Insert a migration cutoff 5 days ago and check that the
            # delta corresponds to the LAST 5 days only.
            cutoff_iso = (today - timedelta(days=5)).isoformat()
            await db.migration_cutoffs.update_one(
                {"user_id": uid},
                {"$set": {"user_id": uid, "cutoff_date": cutoff_iso,
                          "status": "completed"}},
                upsert=True,
            )

            r = await client.get(
                "/api/accounting/employees/list", headers=h)
            data = r.json()
            assert data["cutoff_date"] == cutoff_iso
            row1b = next(e for e in data["employees"] if e["id"] == emp1)
            # Now pending_accrual should be roughly half of scenario 1
            # (5 of the 10 days are pre-cutoff and would be in ledger if
            # we had migrated; since no ledger entries exist, ledger=0,
            # pending = last 5 days only, total visible ≈ 5 days worth)
            assert 400 < row1b["pending_accrual"] < 600, (
                f"Expected ~5 days of 3000/month (~500); "
                f"got {row1b['pending_accrual']}"
            )

            # ─── Scenario 3: posted salary_accrual after cutoff ──
            # Simulate that the admin already posted 200 SAR worth of
            # daily accrual after the cutoff. The pending delta must
            # decrease by 200 (no double counting).
            posted_amount = 200.0
            r = await client.post(
                f"/api/accounting/employees/{emp1}/salary-accrual",
                headers=h,
                json={"amount": posted_amount, "period": "2026-02"},
            )
            assert r.status_code == 200, r.text

            r = await client.get(
                "/api/accounting/employees/list", headers=h)
            data = r.json()
            row1c = next(e for e in data["employees"] if e["id"] == emp1)
            # Total visible payable should be (raw 5-day accrued) — same
            # as before; just decomposed differently.
            assert row1c["salary_payable_ledger"] >= posted_amount - 0.05
            # pending decreased by ~posted_amount
            assert abs(
                (row1b["pending_accrual"] - row1c["pending_accrual"])
                - posted_amount
            ) < 1.0, (
                f"Expected pending to drop by {posted_amount}, got "
                f"{row1b['pending_accrual'] - row1c['pending_accrual']}"
            )
            # Combined total ≈ unchanged
            assert abs(row1c["salary_payable"]
                       - row1b["salary_payable"]) < 1.0

            # ─── Scenario 4: financial-summary endpoint matches ──
            r = await client.get(
                f"/api/accounting/employees/{emp1}/financial-summary",
                headers=h)
            assert r.status_code == 200, r.text
            summary = r.json()
            sp = summary["salary_payable"]
            assert "pending_accrual" in sp
            assert "outstanding_debt_ledger" in sp
            assert abs(sp["outstanding_debt"]
                       - row1c["salary_payable"]) < 0.05
            assert abs(sp["pending_accrual"]
                       - row1c["pending_accrual"]) < 0.05

            # ─── Scenario 5: future-start employee → 0 accrual ──
            emp_future = str(uuid.uuid4())
            start_future = (today + timedelta(days=5)).isoformat()
            await db.operating_salaries.insert_one({
                "id": emp_future, "user_id": uid, "name": "موظف مستقبلي",
                "category": "employee", "monthly_amount": 4000,
                "status": "active", "start_date": start_future,
            })
            r = await client.get(
                "/api/accounting/employees/list", headers=h)
            data = r.json()
            row_f = next(e for e in data["employees"]
                         if e["id"] == emp_future)
            assert row_f["pending_accrual"] == 0.0
            assert row_f["salary_payable"] == 0.0

            # ─── Scenario 6: stopped employee → no accrual after stop
            emp_stop = str(uuid.uuid4())
            stop_iso = (today - timedelta(days=3)).isoformat()
            await db.operating_salaries.insert_one({
                "id": emp_stop, "user_id": uid, "name": "موظف متوقف",
                "category": "employee", "monthly_amount": 3000,
                "status": "stopped",
                "start_date": (today - timedelta(days=10)).isoformat(),
                "stopped_at": stop_iso,
            })
            r = await client.get(
                "/api/accounting/employees/list", headers=h)
            data = r.json()
            row_s = next(e for e in data["employees"]
                         if e["id"] == emp_stop)
            # Stopped 3 days ago; cutoff was 5 days ago.
            # Accrual from cutoff to stop = 2 days.  Expected ≈
            # 3000/30 * 2 ≈ 200 SAR (approx).
            assert 100 < row_s["pending_accrual"] < 300, (
                f"Stopped employee — expected ~2 days; got "
                f"{row_s['pending_accrual']}"
            )

        finally:
            # Cleanup
            await db.operating_salaries.delete_many({"user_id": uid})
            await db.migration_cutoffs.delete_many({"user_id": uid})
            await db.general_ledger.delete_many({"user_id": uid})
            await db.accounts.delete_many({"user_id": uid})
            await db.users.delete_many({"id": uid})
            c.close()
