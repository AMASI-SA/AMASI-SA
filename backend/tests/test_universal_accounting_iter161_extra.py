"""Iter-161 extra coverage — negative cases and uncovered endpoints.

Single end-to-end test in ONE function (motor's module-level cache
breaks across multiple AsyncClient tests in one file).

Covers:
  • expense-categories: duplicate code rejection, PATCH rename,
    DELETE refuses system, DELETE refuses in-use, DELETE deletes
    user category cleanly
  • custody/return rejects amount > custody balance
  • custody/settle-with-receipts rejects unknown category + over-balance
  • bank-transfer rejects same source/destination
  • /expenses rejects unknown category, happy path posts balanced txn
  • /trial-balance is_balanced True after the test transactions
  • /statement returns entries + balance + audit log
  • /migration/snapshot, /status, /run dry_run twice (idempotent),
    /run dry_run=false applies + second non-dry-run returns 400
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
async def test_iter161_extra_coverage():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport,
                            base_url="http://test") as client:
        email = f"p2x-{os.urandom(3).hex()}@test.com"
        r = await client.post("/api/auth/register",
                              json={"name": "Tx", "email": email,
                                    "password": "pass1234"})
        assert r.status_code == 200, r.text
        token = r.json()["access_token"]
        uid = r.json()["id"]
        h = {"Authorization": f"Bearer {token}"}

        c = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = c[os.environ["DB_NAME"]]

        # Seed: 1 employee + 1 bank
        emp_id = str(uuid.uuid4())
        bank_id = str(uuid.uuid4())
        bank2_id = str(uuid.uuid4())
        await db.operating_salaries.insert_one({
            "id": emp_id, "user_id": uid, "name": "T-Emp",
            "monthly_amount": 1000, "category": "employee",
        })
        await db.accounts.insert_many([
            {"id": bank_id, "user_id": uid, "account_type": "bank",
             "name": "Bank-A", "balance": 50000},
            {"id": bank2_id, "user_id": uid, "account_type": "bank",
             "name": "Bank-B", "balance": 10000},
        ])

        # ── Expense categories: seed defaults, duplicate, patch, delete
        r = await client.get("/api/accounting/expense-categories", headers=h)
        assert r.status_code == 200
        cats = r.json()
        assert len(cats) >= 16
        system_cat = next(c for c in cats if c["code"] == "fuel")
        assert system_cat.get("system") is True

        # Create a user category
        r = await client.post("/api/accounting/expense-categories", headers=h,
                              json={"code": "fines", "name": "غرامات"})
        assert r.status_code == 200
        new_cat = r.json()
        cat_id = new_cat["id"]
        assert new_cat["system"] is False

        # Duplicate code rejected
        r = await client.post("/api/accounting/expense-categories", headers=h,
                              json={"code": "fines", "name": "X"})
        assert r.status_code == 400

        # PATCH rename
        r = await client.patch(
            f"/api/accounting/expense-categories/{cat_id}", headers=h,
            json={"code": "penalties", "name": "غرامات معدلة"})
        assert r.status_code == 200

        # DELETE non-system, not-in-use → ok
        r = await client.delete(
            f"/api/accounting/expense-categories/{cat_id}", headers=h)
        assert r.status_code == 200

        # DELETE system category rejected
        r = await client.delete(
            f"/api/accounting/expense-categories/{system_cat['id']}",
            headers=h)
        assert r.status_code == 400

        # ── Custody grant 1000
        r = await client.post(
            f"/api/accounting/employees/{emp_id}/custody", headers=h,
            json={"amount": 1000, "paid_from_account_id": bank_id})
        assert r.status_code == 200

        # custody/return > balance rejected
        r = await client.post(
            f"/api/accounting/employees/{emp_id}/custody/return", headers=h,
            json={"amount": 5000, "deposited_to_account_id": bank_id})
        assert r.status_code == 400

        # custody/settle-with-receipts unknown category rejected
        r = await client.post(
            f"/api/accounting/employees/{emp_id}/custody/settle-with-receipts",
            headers=h, json={"items": [
                {"expense_category": "nonexistent_xyz", "amount": 100}]})
        assert r.status_code == 400

        # custody/settle-with-receipts over-balance rejected
        r = await client.post(
            f"/api/accounting/employees/{emp_id}/custody/settle-with-receipts",
            headers=h, json={"items": [
                {"expense_category": "fuel", "amount": 5000}]})
        assert r.status_code == 400

        # Happy: settle 600 in receipts
        r = await client.post(
            f"/api/accounting/employees/{emp_id}/custody/settle-with-receipts",
            headers=h, json={"items": [
                {"expense_category": "fuel", "amount": 400},
                {"expense_category": "office", "amount": 200}]})
        assert r.status_code == 200

        # Now the 'fuel' category is in-use → DELETE must be blocked
        # First re-fetch categories to find fuel id
        cats = (await client.get(
            "/api/accounting/expense-categories", headers=h)).json()
        # Fuel is system; deleting it is still blocked by system rule. So
        # test in-use by creating a user category, using it, then
        # trying to delete it.
        r = await client.post(
            "/api/accounting/expense-categories", headers=h,
            json={"code": "fines", "name": "غرامات"})
        assert r.status_code == 200
        in_use_id = r.json()["id"]
        # Record an expense against it (and verify happy path of /expenses)
        r = await client.post(
            "/api/accounting/expenses", headers=h,
            json={"amount": 50, "expense_category": "fines",
                   "paid_from_account_id": bank_id})
        assert r.status_code == 200
        # Now delete should be blocked because it's in use
        r = await client.delete(
            f"/api/accounting/expense-categories/{in_use_id}", headers=h)
        assert r.status_code == 400

        # ── /expenses rejects unknown category
        r = await client.post(
            "/api/accounting/expenses", headers=h,
            json={"amount": 99, "expense_category": "no_such",
                   "paid_from_account_id": bank_id})
        assert r.status_code == 400

        # ── bank-transfer rejects same source/dest
        r = await client.post("/api/accounting/bank-transfer", headers=h,
            json={"amount": 100, "from_account_id": bank_id,
                   "to_account_id": bank_id})
        assert r.status_code == 400

        # Happy bank transfer
        r = await client.post("/api/accounting/bank-transfer", headers=h,
            json={"amount": 200, "from_account_id": bank_id,
                   "to_account_id": bank2_id})
        assert r.status_code == 200

        # ── /trial-balance balanced
        r = await client.get("/api/accounting/trial-balance", headers=h)
        assert r.status_code == 200
        tb = r.json()
        assert tb["is_balanced"] is True
        assert abs(tb["total_debits"] - tb["total_credits"]) < 0.01
        assert len(tb["rows"]) > 0

        # ── /statement for employee custody
        r = await client.get(
            f"/api/accounting/statement?entity_type=employee"
            f"&entity_id={emp_id}&sub_account=custody",
            headers=h)
        assert r.status_code == 200
        st = r.json()
        assert "entries" in st and "balance" in st and "audit_log" in st
        assert len(st["entries"]) >= 1

        # ── /api/ledger/balance?sub_account=… filter works
        r = await client.get(
            f"/api/ledger/balance?entity_type=employee"
            f"&entity_id={emp_id}&sub_account=custody",
            headers=h)
        assert r.status_code == 200
        # Custody should be 1000 - 600 = 400 (return rejected earlier)
        assert r.json()["net_balance"] == 400.0

        # ── Migration: snapshot, status, dry-run twice, then apply
        r = await client.get("/api/accounting/migration/snapshot", headers=h)
        assert r.status_code == 200
        snap = r.json()
        assert set(snap.keys()) >= {"employees", "suppliers",
                                     "externals", "banks"}

        r = await client.get("/api/accounting/migration/status", headers=h)
        assert r.status_code == 200
        assert r.json()["completed"] is False

        # Dry-run #1
        r = await client.post("/api/accounting/migration/run", headers=h,
            json={"cutoff_date": "2026-06-13", "dry_run": True})
        assert r.status_code == 200
        m1 = r.json()
        assert m1["dry_run"] is True

        # Dry-run #2 — still allowed (idempotent dry-run)
        r = await client.post("/api/accounting/migration/run", headers=h,
            json={"cutoff_date": "2026-06-13", "dry_run": True})
        assert r.status_code == 200
        assert r.json()["dry_run"] is True

        # Apply
        r = await client.post("/api/accounting/migration/run", headers=h,
            json={"cutoff_date": "2026-06-13", "dry_run": False})
        assert r.status_code == 200
        applied = r.json()
        assert applied["dry_run"] is False

        # Status now completed
        r = await client.get("/api/accounting/migration/status", headers=h)
        assert r.json()["completed"] is True

        # Second apply → 400
        r = await client.post("/api/accounting/migration/run", headers=h,
            json={"cutoff_date": "2026-06-13", "dry_run": False})
        assert r.status_code == 400

        # ── Final double-entry invariant check
        groups = await db.general_ledger.aggregate([
            {"$match": {"user_id": uid,
                          "txn_group_id": {"$ne": None}}},
            {"$group": {
                "_id": "$txn_group_id",
                "debit": {"$sum": {"$cond": [
                    {"$eq": ["$side", "debit"]}, "$amount", 0]}},
                "credit": {"$sum": {"$cond": [
                    {"$eq": ["$side", "credit"]}, "$amount", 0]}}}}
        ]).to_list(500)
        unbalanced = [g for g in groups
                       if abs(g["debit"] - g["credit"]) > 0.01]
        assert not unbalanced, f"Unbalanced txn_groups: {unbalanced}"

        # Cleanup
        await db.operating_salaries.delete_many({"user_id": uid})
        await db.counterparties.delete_many({"user_id": uid})
        await db.accounts.delete_many({"user_id": uid})
        await db.general_ledger.delete_many({"user_id": uid})
        await db.accounting_audit_log.delete_many({"user_id": uid})
        await db.expense_categories.delete_many({"user_id": uid})
        await db.migration_cutoffs.delete_many({"user_id": uid})
