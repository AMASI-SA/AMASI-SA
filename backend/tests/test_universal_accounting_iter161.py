"""Iter-161 — Universal Accounting Phase 2 end-to-end.

Verifies: post_txn_group balanced invariant, all employee flows
(advance/custody/salary), supplier flows, external persons, bank
transfer, expense categories CRUD, and the migration dry-run
comparison.
"""
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
async def test_phase2_universal_accounting_end_to_end():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport,
                            base_url="http://test") as client:
        # Register a fresh user
        email = f"p2-{os.urandom(3).hex()}@test.com"
        r = await client.post("/api/auth/register",
                              json={"name": "T", "email": email,
                                    "password": "pass1234"})
        token = r.json()["access_token"]
        uid = r.json()["id"]
        h = {"Authorization": f"Bearer {token}"}

        c = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = c[os.environ["DB_NAME"]]

        # ── Seed: 1 employee + 1 supplier + 1 external + 1 bank ───
        emp_id = str(uuid.uuid4())
        sup_id = str(uuid.uuid4())
        ext_id = str(uuid.uuid4())
        bank_id = str(uuid.uuid4())
        await db.operating_salaries.insert_one({
            "id": emp_id, "user_id": uid, "name": "أحمد",
            "monthly_amount": 3000, "category": "employee",
            "accrual_mode": "monthly",
            "accrual_start_date": "2026-06-01",
        })
        await db.counterparties.insert_many([
            {"id": sup_id, "user_id": uid, "kind": "supplier",
             "name": "Supplier X", "name_lower": "supplier x"},
            {"id": ext_id, "user_id": uid, "kind": "external",
             "name": "علي", "name_lower": "علي"},
        ])
        await db.accounts.insert_one({
            "id": bank_id, "user_id": uid, "account_type": "bank",
            "name": "الراجحي", "balance": 100000,
        })

        # ── Expense categories CRUD ───────────────────────────────
        r = await client.get("/api/accounting/expense-categories", headers=h)
        assert r.status_code == 200
        cats = r.json()
        assert any(c["code"] == "salary" for c in cats)
        assert any(c["code"] == "fuel" for c in cats)
        r = await client.post("/api/accounting/expense-categories", headers=h,
                              json={"code": "fines", "name": "غرامات"})
        assert r.status_code == 200

        # ── EMPLOYEE: grant 500 advance ───────────────────────────
        r = await client.post(
            f"/api/accounting/employees/{emp_id}/advances", headers=h,
            json={"amount": 500, "paid_from_account_id": bank_id,
                   "payment_date": "2026-06-13"})
        assert r.status_code == 200
        assert r.json()["debit_total"] == 500
        assert r.json()["credit_total"] == 500
        # advance balance = 500 (employee owes us)
        b = (await client.get(
            f"/api/ledger/balance?entity_type=employee&entity_id={emp_id}&sub_account=advance",
            headers=h)).json()
        assert b["net_balance"] == 500.0

        # ── EMPLOYEE: post salary accrual 3000 ────────────────────
        r = await client.post(
            f"/api/accounting/employees/{emp_id}/salary-accrual", headers=h,
            json={"amount": 3000, "period": "2026-06"})
        assert r.status_code == 200
        # idempotent: same period rejected
        r2 = await client.post(
            f"/api/accounting/employees/{emp_id}/salary-accrual", headers=h,
            json={"amount": 3000, "period": "2026-06"})
        assert r2.status_code == 400

        # payable = 3000
        b = (await client.get(
            f"/api/ledger/balance?entity_type=employee&entity_id={emp_id}&sub_account=salary_payable",
            headers=h)).json()
        assert b["outstanding_debt"] == 3000.0

        # ── EMPLOYEE: settle salary 2500 cash + 500 advance ──────
        r = await client.post(
            f"/api/accounting/employees/{emp_id}/settle", headers=h,
            json={"amount": 2500, "paid_from_account_id": bank_id,
                   "apply_open_advances": True})
        assert r.status_code == 200
        body = r.json()
        # salary_part = 2500, advance_offset = 500 (entire advance), no excess
        assert body["salary_part"] == 2500.0
        assert body["advance_offset"] == 500.0
        assert body["new_advance_excess"] == 0.0
        # Now: payable=0, advance=0
        assert body["salary_payable_after"] == 0.0
        assert body["advance_after"] == 0.0

        # ── EMPLOYEE: grant 5000 custody ─────────────────────────
        r = await client.post(
            f"/api/accounting/employees/{emp_id}/custody", headers=h,
            json={"amount": 5000, "paid_from_account_id": bank_id})
        assert r.status_code == 200
        b = (await client.get(
            f"/api/ledger/balance?entity_type=employee&entity_id={emp_id}&sub_account=custody",
            headers=h)).json()
        assert b["net_balance"] == 5000.0

        # ── EMPLOYEE: return 2000 from custody ───────────────────
        r = await client.post(
            f"/api/accounting/employees/{emp_id}/custody/return", headers=h,
            json={"amount": 2000, "deposited_to_account_id": bank_id})
        assert r.status_code == 200
        # custody = 3000
        b = (await client.get(
            f"/api/ledger/balance?entity_type=employee&entity_id={emp_id}&sub_account=custody",
            headers=h)).json()
        assert b["net_balance"] == 3000.0

        # ── EMPLOYEE: settle 1500 in receipts ────────────────────
        r = await client.post(
            f"/api/accounting/employees/{emp_id}/custody/settle-with-receipts",
            headers=h, json={"items": [
                {"expense_category": "office", "amount": 1000},
                {"expense_category": "fuel", "amount": 500},
            ]})
        assert r.status_code == 200
        # custody = 1500
        b = (await client.get(
            f"/api/ledger/balance?entity_type=employee&entity_id={emp_id}&sub_account=custody",
            headers=h)).json()
        assert b["net_balance"] == 1500.0
        # expense totals
        be = (await client.get(
            "/api/ledger/balance?entity_type=expense&entity_id=office",
            headers=h)).json()
        assert be["debits"] == 1000.0
        bf = (await client.get(
            "/api/ledger/balance?entity_type=expense&entity_id=fuel",
            headers=h)).json()
        assert bf["debits"] == 500.0

        # ── EMPLOYEE: financial-summary ──────────────────────────
        r = await client.get(
            f"/api/accounting/employees/{emp_id}/financial-summary",
            headers=h)
        s = r.json()
        assert s["salary_payable"]["outstanding_debt"] == 0.0
        assert s["advance"]["net_balance"] == 0.0
        assert s["custody"]["net_balance"] == 1500.0
        # net_position: we owe him 0, he holds 1500 of our custody → -1500
        assert s["net_position"] == -1500.0

        # ── SUPPLIER: invoice 10000 then pay 4000 + 6000 ─────────
        r = await client.post(
            f"/api/accounting/suppliers/{sup_id}/invoice", headers=h,
            json={"amount": 10000, "expense_category": "inventory",
                   "invoice_no": "INV-001"})
        assert r.status_code == 200
        b = (await client.get(
            f"/api/ledger/balance?entity_type=supplier&entity_id={sup_id}&sub_account=payable",
            headers=h)).json()
        assert b["outstanding_debt"] == 10000.0

        await client.post(
            f"/api/accounting/suppliers/{sup_id}/pay", headers=h,
            json={"amount": 4000, "paid_from_account_id": bank_id})
        await client.post(
            f"/api/accounting/suppliers/{sup_id}/pay", headers=h,
            json={"amount": 6000, "paid_from_account_id": bank_id})
        b = (await client.get(
            f"/api/ledger/balance?entity_type=supplier&entity_id={sup_id}&sub_account=payable",
            headers=h)).json()
        assert b["outstanding_debt"] == 0.0

        # ── EXTERNAL: grant 3000 → collect 1000 ──────────────────
        r = await client.post(
            f"/api/accounting/external-persons/{ext_id}/grant", headers=h,
            json={"amount": 3000, "paid_from_account_id": bank_id})
        assert r.status_code == 200
        r = await client.post(
            f"/api/accounting/external-persons/{ext_id}/collect", headers=h,
            json={"amount": 1000, "deposited_to_account_id": bank_id})
        assert r.status_code == 200
        b = (await client.get(
            f"/api/ledger/balance?entity_type=external_person&entity_id={ext_id}&sub_account=receivable",
            headers=h)).json()
        assert b["net_balance"] == 2000.0

        # ── BANK TRANSFER: requires 2 banks ─────────────────────
        bank2_id = str(uuid.uuid4())
        await db.accounts.insert_one({
            "id": bank2_id, "user_id": uid, "account_type": "bank",
            "name": "الأهلي", "balance": 0,
        })
        r = await client.post("/api/accounting/bank-transfer", headers=h,
            json={"amount": 5000, "from_account_id": bank_id,
                   "to_account_id": bank2_id})
        assert r.status_code == 200
        b1 = (await client.get(
            f"/api/ledger/balance?entity_type=bank&entity_id={bank_id}&sub_account=main",
            headers=h)).json()
        b2 = (await client.get(
            f"/api/ledger/balance?entity_type=bank&entity_id={bank2_id}&sub_account=main",
            headers=h)).json()
        # bank A net: lots of credits accumulated; just verify b2 is +5000
        assert b2["net_balance"] == 5000.0

        # ── DOUBLE-ENTRY INVARIANT: all txn_groups must balance ──
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

        # ── MIGRATION: dry-run on a fresh user works (no errors) ─
        r = await client.post(
            "/api/accounting/migration/run", headers=h,
            json={"cutoff_date": "2026-06-13", "dry_run": True})
        assert r.status_code == 200
        mig = r.json()
        assert mig["dry_run"] is True
        # before snapshot includes the seeded bank with 100000 balance
        assert "employees" in mig["before"]
        # bank balance is non-zero so at least 1 planned op
        assert mig["planned_operations"] >= 1
        # since opening_balance entries DIDN'T mutate the existing ledger
        # rows we already posted via the universal endpoints, the
        # mismatch is the sum of all the test transactions we did.

        # ── Cleanup ──────────────────────────────────────────────
        await db.operating_salaries.delete_many({"user_id": uid})
        await db.counterparties.delete_many({"user_id": uid})
        await db.accounts.delete_many({"user_id": uid})
        await db.general_ledger.delete_many({"user_id": uid})
        await db.accounting_audit_log.delete_many({"user_id": uid})
        await db.expense_categories.delete_many({"user_id": uid})
