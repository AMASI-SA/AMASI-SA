"""Iter-196 — Employee Misposting Correction tests.

Validates the new `POST /api/accounting/employees/correct-misposting`
endpoint and its read-only guarantee on the bank/cash side.

Decisions enforced (per merchant approval):
    1a  partial corrections allowed
    2b  opening_balance is NOT correctable here
    3b  a correction itself is NOT correctable
    4a  original entry stays visible & untouched
    5b  MVP: salary_payment / advance_grant / custody_grant only

Consolidated into a SINGLE test to dodge the project-wide
pytest-asyncio loop-close bug that affects every HTTP-based test
file with more than one async function.
"""
import os
import sys
import uuid

import pytest
from dotenv import load_dotenv
from httpx import ASGITransport, AsyncClient

load_dotenv("/app/backend/.env")
sys.path.insert(0, "/app/backend")
from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402
from server import app  # noqa: E402


async def _bank_balance(db, uid, bank_id):
    a = await db.accounts.find_one(
        {"id": bank_id, "user_id": uid}, {"_id": 0, "current_balance": 1},
    )
    return float(a.get("current_balance") or 0)


async def _ledger_snapshot(db, uid, txn_group_id):
    """Capture a stable comparable representation of the original
    transaction group so we can prove it wasn't mutated."""
    rows = await db.general_ledger.find(
        {"user_id": uid, "txn_group_id": txn_group_id},
        {"_id": 0},
    ).to_list(50)
    # Drop volatile fields (none expected — but be defensive).
    return [
        {k: v for k, v in r.items() if k not in {"updated_at"}}
        for r in sorted(rows, key=lambda x: x.get("id") or "")
    ]


async def _employee_payable_outstanding(db, uid, emp_id):
    """Σ credit − Σ debit on salary_payable (positive → we owe)."""
    pipeline = [
        {"$match": {"user_id": uid, "entity_type": "employee",
                    "entity_id": emp_id,
                    "sub_account": "salary_payable",
                    "status": "posted"}},
        {"$group": {
            "_id": "$side",
            "total": {"$sum": "$amount"},
        }},
    ]
    debit_total = 0.0
    credit_total = 0.0
    async for r in db.general_ledger.aggregate(pipeline):
        if r["_id"] == "debit":
            debit_total = float(r["total"])
        else:
            credit_total = float(r["total"])
    return round(credit_total - debit_total, 2)


@pytest.mark.asyncio
async def test_employee_correction_full_lifecycle():
    """One consolidated test covering every guarantee of Iter-196."""
    async with AsyncClient(transport=ASGITransport(app=app),
                            base_url="http://test") as client:
        email = f"corr196-{os.urandom(3).hex()}@test.com"
        r = await client.post("/api/auth/register", json={
            "name": "C", "email": email, "password": "pass1234",
        })
        token = r.json()["access_token"]
        uid = r.json()["id"]
        h = {"Authorization": f"Bearer {token}"}

        c = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = c[os.environ["DB_NAME"]]

        # ── Seed: bank + 2 employees ────────────────────────────
        bank_id = str(uuid.uuid4())
        await db.accounts.insert_one({
            "id": bank_id, "user_id": uid, "account_type": "bank",
            "name": "الراجحي", "current_balance": 50_000.0,
            "balance": 50_000.0, "status": "active",
        })
        khaled = str(uuid.uuid4())  # WRONG employee
        mohammed = str(uuid.uuid4())  # CORRECT employee
        await db.operating_salaries.insert_many([
            {"id": khaled, "user_id": uid, "name": "خالد",
             "category": "employee", "monthly_amount": 5000,
             "status": "active"},
            {"id": mohammed, "user_id": uid, "name": "محمد",
             "category": "employee", "monthly_amount": 5000,
             "status": "active"},
        ])

        try:
            # ─── Step 1) Accrue salary 3000 to khaled then pay it
            r = await client.post(
                f"/api/accounting/employees/{khaled}/salary-accrual",
                headers=h,
                json={"amount": 3000, "period": "2026-01"},
            )
            assert r.status_code == 200, r.text

            r = await client.post(
                f"/api/accounting/employees/{khaled}/settle",
                headers=h,
                json={"amount": 3000,
                      "paid_from_account_id": bank_id,
                      "apply_open_advances": False},
            )
            assert r.status_code == 200, r.text
            settle_result = r.json()
            original_txn_group_id = settle_result["txn_group_id"]

            # Snapshot bank + original entries.
            bank_before = await _bank_balance(db, uid, bank_id)
            original_snapshot = await _ledger_snapshot(
                db, uid, original_txn_group_id)
            assert len(original_snapshot) >= 2

            khaled_payable_before = await _employee_payable_outstanding(
                db, uid, khaled)
            mohammed_payable_before = await _employee_payable_outstanding(
                db, uid, mohammed)
            # khaled paid in full → balance is now 0
            assert khaled_payable_before == 0.0
            # mohammed never accrued → balance is 0
            assert mohammed_payable_before == 0.0

            # ─── Step 2) Same employee in from & to → 400 ──────
            r = await client.post(
                "/api/accounting/employees/correct-misposting",
                headers=h,
                json={
                    "original_txn_group_id": original_txn_group_id,
                    "from_employee_id": khaled,
                    "to_employee_id":   khaled,
                    "amount": 3000,
                    "reason": "حاول يصحح لنفس الموظف",
                },
            )
            assert r.status_code == 400
            assert "نفس الشخص" in r.json()["detail"]

            # ─── Step 3) Over-amount → 400 ─────────────────────
            r = await client.post(
                "/api/accounting/employees/correct-misposting",
                headers=h,
                json={
                    "original_txn_group_id": original_txn_group_id,
                    "from_employee_id": khaled,
                    "to_employee_id":   mohammed,
                    "amount": 5000,  # > 3000 original
                    "reason": "محاولة تجاوز المبلغ الأصلي",
                },
            )
            assert r.status_code == 400
            assert "المتبقي" in r.json()["detail"]

            # ─── Step 4) Reason too short → 422 ─────────────────
            r = await client.post(
                "/api/accounting/employees/correct-misposting",
                headers=h,
                json={
                    "original_txn_group_id": original_txn_group_id,
                    "from_employee_id": khaled,
                    "to_employee_id":   mohammed,
                    "amount": 3000,
                    "reason": "اب",  # < 5 chars
                },
            )
            assert r.status_code == 422

            # ─── Step 5) PARTIAL correction 1500 of 3000 ───────
            r = await client.post(
                "/api/accounting/employees/correct-misposting",
                headers=h,
                json={
                    "original_txn_group_id": original_txn_group_id,
                    "from_employee_id": khaled,
                    "to_employee_id":   mohammed,
                    "amount": 1500,
                    "reason": "نقل نصف المبلغ لمحمد",
                },
            )
            assert r.status_code == 200, r.text
            d = r.json()
            assert d["bank_impact"] == 0.0
            assert d["is_partial"] is True
            assert d["remaining_after_this"] == 1500.0
            corr1_group = d["correction_group_id"]

            # ─── Step 6) Bank UNCHANGED ────────────────────────
            bank_after_partial = await _bank_balance(db, uid, bank_id)
            assert bank_after_partial == bank_before, (
                f"Bank moved! before={bank_before} "
                f"after={bank_after_partial} — correction must NEVER "
                f"touch the bank"
            )

            # ─── Step 7) Original entries BYTE-IDENTICAL ───────
            original_snapshot_after = await _ledger_snapshot(
                db, uid, original_txn_group_id,
            )
            assert original_snapshot == original_snapshot_after, (
                "Original txn_group rows were mutated by the "
                "correction — violation of Iter-196 invariant"
            )

            # ─── Step 8) Employee balances move correctly ──────
            khaled_payable_after_partial = \
                await _employee_payable_outstanding(db, uid, khaled)
            mohammed_payable_after_partial = \
                await _employee_payable_outstanding(db, uid, mohammed)
            # khaled: a CREDIT 1500 was added → we owe khaled 1500 again
            assert khaled_payable_after_partial == 1500.0, (
                f"khaled payable should be 1500 (the un-corrected half "
                f"is still paid off, the corrected half restores debt), "
                f"got {khaled_payable_after_partial}"
            )
            # mohammed: a DEBIT 1500 was added → -1500
            assert mohammed_payable_after_partial == -1500.0

            # ─── Step 9) Verify ledger metadata is rich ────────
            corr_rows = await db.general_ledger.find(
                {"user_id": uid, "txn_group_id": corr1_group},
                {"_id": 0},
            ).to_list(10)
            assert len(corr_rows) == 2
            for row in corr_rows:
                assert row["entry_type"] == "correction"
                assert row["sub_account"] == "salary_payable"
                assert row["corrects_txn_group_id"] == \
                    original_txn_group_id
                md = row["metadata"]
                assert md["correction_type"] == "wrong_employee"
                assert md["original_operation"] == "salary_payment"
                assert md["original_employee_id"] == khaled
                assert md["corrected_to_employee_id"] == mohammed
                assert md["reason"] == "نقل نصف المبلغ لمحمد"
                assert md["partial"] is True
                assert md["corrected_by"] == uid

            # ─── Step 10) Cannot correct a correction (3b) ─────
            r = await client.post(
                "/api/accounting/employees/correct-misposting",
                headers=h,
                json={
                    "original_txn_group_id": corr1_group,
                    "from_employee_id": khaled,
                    "to_employee_id":   mohammed,
                    "amount": 1500,
                    "reason": "محاولة تصحيح التصحيح",
                },
            )
            # Either the rows are not of a supported entry_type or the
            # endpoint rejects the entire group — both are acceptable.
            assert r.status_code == 400, r.text

            # ─── Step 11) Complete the partial — correct remaining 1500
            r = await client.post(
                "/api/accounting/employees/correct-misposting",
                headers=h,
                json={
                    "original_txn_group_id": original_txn_group_id,
                    "from_employee_id": khaled,
                    "to_employee_id":   mohammed,
                    "amount": 1500,
                    "reason": "إكمال التصحيح للنصف الثاني",
                },
            )
            assert r.status_code == 200, r.text
            d2 = r.json()
            assert d2["remaining_after_this"] == 0.0
            assert d2["is_partial"] is False  # exactly the rest

            # ─── Step 12) Try to over-correct now → 400 ────────
            r = await client.post(
                "/api/accounting/employees/correct-misposting",
                headers=h,
                json={
                    "original_txn_group_id": original_txn_group_id,
                    "from_employee_id": khaled,
                    "to_employee_id":   mohammed,
                    "amount": 100,
                    "reason": "محاولة تجاوز بعد إكمال التصحيح",
                },
            )
            assert r.status_code == 400

            # ─── Step 13) Final accounting invariants ──────────
            assert (await _bank_balance(db, uid, bank_id)) == \
                bank_before, "Bank moved across the entire flow"

            khaled_final = await _employee_payable_outstanding(
                db, uid, khaled)
            mohammed_final = await _employee_payable_outstanding(
                db, uid, mohammed)
            # Full 3000 transferred → khaled owed 3000 again,
            # mohammed received 3000 we don't owe → he owes us 3000
            assert khaled_final == 3000.0, khaled_final
            assert mohammed_final == -3000.0, mohammed_final

            # ─── Step 14) Audit log endpoint surfaces corrections
            r = await client.get(
                "/api/accounting/employees/corrections", headers=h,
            )
            assert r.status_code == 200
            log = r.json()["corrections"]
            assert len(log) == 2  # we made 2 corrections
            assert all(
                c["corrects_txn_group_id"] == original_txn_group_id
                for c in log
            )
            assert all(c["amount"] == 1500.0 for c in log)
            assert all(
                c["original_operation"] == "salary_payment"
                for c in log
            )

            # ─── Step 15) Listing correctable ops for khaled ───
            r = await client.get(
                f"/api/accounting/employees/{khaled}/"
                f"correctable-operations",
                headers=h,
            )
            assert r.status_code == 200
            ops = r.json()["operations"]
            # The original 3000 settlement should appear with 0
            # remaining (fully corrected now).
            target = next(
                (o for o in ops
                 if o["txn_group_id"] == original_txn_group_id),
                None,
            )
            assert target is not None
            assert target["amount"] == 3000.0
            assert target["already_corrected"] == 3000.0
            assert target["remaining_correctable"] == 0.0

            # ─── Step 16) ADVANCE_GRANT correction smoke test ──
            r = await client.post(
                f"/api/accounting/employees/{mohammed}/advances",
                headers=h,
                json={"amount": 700, "paid_from_account_id": bank_id},
            )
            assert r.status_code == 200, r.text
            adv_txn = r.json()["txn_group_id"]
            bank_after_advance = await _bank_balance(db, uid, bank_id)

            r = await client.post(
                "/api/accounting/employees/correct-misposting",
                headers=h,
                json={
                    "original_txn_group_id": adv_txn,
                    "from_employee_id": mohammed,
                    "to_employee_id":   khaled,
                    "amount": 700,
                    "reason": "السلفة كانت لخالد فعلاً",
                },
            )
            assert r.status_code == 200, r.text
            adv_corr = r.json()
            assert adv_corr["sub_account"] == "advance"
            assert adv_corr["original_operation"] == "advance_grant"
            assert adv_corr["bank_impact"] == 0.0
            assert (await _bank_balance(db, uid, bank_id)) == \
                bank_after_advance
        finally:
            await db.accounts.delete_many({"user_id": uid})
            await db.operating_salaries.delete_many({"user_id": uid})
            await db.general_ledger.delete_many({"user_id": uid})
            await db.audit_log.delete_many({"user_id": uid})
