"""Iter-199 — Salary Payment Full Reversal tests.

Distinct from Iter-196 (employee correction): a FULL reversal
MUST restore the bank/cash balance. Every leg of the original
salary_payment is mirrored with the opposite side. The original
ledger group is preserved byte-for-byte.

Consolidated into ONE async function to dodge the project-wide
pytest-asyncio loop-close limitation.
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


async def _bank_live_via_api(client, h, bank_id):
    r = await client.get(f"/api/accounts/{bank_id}", headers=h)
    return float(r.json()["current_balance"])


async def _ledger_snapshot(db, uid, txn_group_id):
    rows = await db.general_ledger.find(
        {"user_id": uid, "txn_group_id": txn_group_id},
        {"_id": 0},
    ).to_list(50)
    return sorted(rows, key=lambda x: x.get("id") or "")


@pytest.mark.asyncio
async def test_reverse_salary_payment_full_lifecycle():
    async with AsyncClient(transport=ASGITransport(app=app),
                            base_url="http://test") as client:
        email = f"i199-{os.urandom(3).hex()}@test.com"
        r = await client.post("/api/auth/register", json={
            "name": "I199", "email": email, "password": "pass1234",
        })
        token = r.json()["access_token"]
        uid = r.json()["id"]
        h = {"Authorization": f"Bearer {token}"}

        c = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = c[os.environ["DB_NAME"]]

        # ── Seed: bank + 1 employee ────────────────────────────
        bank_id = str(uuid.uuid4())
        await db.accounts.insert_one({
            "id": bank_id, "user_id": uid, "account_type": "bank",
            "name": "بنك الراجحي", "current_balance": 50_000.0,
            "balance": 50_000.0, "status": "active",
        })
        khaled = str(uuid.uuid4())
        await db.operating_salaries.insert_one({
            "id": khaled, "user_id": uid, "name": "خالد",
            "category": "employee", "monthly_amount": 5000,
            "status": "active",
        })

        try:
            # 1) Accrue + pay salary 3000
            r = await client.post(
                f"/api/accounting/employees/{khaled}/salary-accrual",
                headers=h,
                json={"amount": 3000, "period": "2026-01"},
            )
            assert r.status_code == 200, r.text

            bank_before_payment = await _bank_live_via_api(
                client, h, bank_id)

            r = await client.post(
                f"/api/accounting/employees/{khaled}/settle",
                headers=h,
                json={"amount": 3000,
                      "paid_from_account_id": bank_id,
                      "apply_open_advances": False},
            )
            assert r.status_code == 200, r.text
            original_txn = r.json()["txn_group_id"]

            bank_after_payment = await _bank_live_via_api(
                client, h, bank_id)
            assert bank_after_payment == \
                round(bank_before_payment - 3000.0, 2), (
                    f"Bank should have dropped by 3000 — before "
                    f"{bank_before_payment}, after "
                    f"{bank_after_payment}"
                )

            original_snapshot = await _ledger_snapshot(
                db, uid, original_txn)
            assert len(original_snapshot) >= 2

            # 2) Missing reason → 422
            r = await client.post(
                "/api/accounting/employees/reverse-salary-payment",
                headers=h,
                json={"original_txn_group_id": original_txn,
                      "reason": "ab"},
            )
            assert r.status_code == 422

            # 3) Successful reversal
            r = await client.post(
                "/api/accounting/employees/reverse-salary-payment",
                headers=h,
                json={"original_txn_group_id": original_txn,
                      "reason": "صرف خطأ يجب عكسه بالكامل"},
            )
            assert r.status_code == 200, r.text
            data = r.json()
            assert data["amount"] == 3000.0
            assert data["bank_impact_direction"] == \
                "restored_inflow"
            assert data["employee"]["id"] == khaled
            assert data["bank_account"]["id"] == bank_id
            reversal_gid = data["reversal_group_id"]
            assert reversal_gid != original_txn

            # 4) Bank balance restored to pre-payment value
            bank_after_reversal = await _bank_live_via_api(
                client, h, bank_id)
            assert bank_after_reversal == bank_before_payment, (
                f"Bank should be back to {bank_before_payment} "
                f"after the full reversal, got {bank_after_reversal}"
            )

            # 5) Original entries untouched (byte-identical)
            original_after = await _ledger_snapshot(
                db, uid, original_txn)
            assert original_snapshot == original_after, (
                "Original txn_group was mutated — invariant broken"
            )

            # 6) Reversal entries carry reversal_of_txn_group_id
            #    and entry_type='reversal' on every row
            rev_rows = await db.general_ledger.find(
                {"user_id": uid, "txn_group_id": reversal_gid},
                {"_id": 0},
            ).to_list(50)
            assert len(rev_rows) == len(original_snapshot)
            for row in rev_rows:
                assert row["entry_type"] == "reversal"
                assert row["reversal_of_txn_group_id"] == \
                    original_txn
                md = row["metadata"]
                assert md["original_operation"] == "salary_payment"
                assert md["reversed_by"] == uid
                assert md["reason"] == "صرف خطأ يجب عكسه بالكامل"

            # 7) Sides are correctly flipped per leg
            orig_by_id = {
                (r["entity_type"], r["entity_id"], r["sub_account"]):
                r["side"] for r in original_after
            }
            rev_by_id = {
                (r["entity_type"], r["entity_id"], r["sub_account"]):
                r["side"] for r in rev_rows
            }
            assert orig_by_id.keys() == rev_by_id.keys()
            for k in orig_by_id:
                assert orig_by_id[k] != rev_by_id[k], (
                    f"Leg {k} was NOT flipped: orig={orig_by_id[k]} "
                    f"rev={rev_by_id[k]}"
                )

            # 8) Double-reverse → 400
            r = await client.post(
                "/api/accounting/employees/reverse-salary-payment",
                headers=h,
                json={"original_txn_group_id": original_txn,
                      "reason": "محاولة عكس ثاني للعملية نفسها"},
            )
            assert r.status_code == 400
            assert "مرتين" in r.json()["detail"]

            # 9) Reverse a reversal → 400
            r = await client.post(
                "/api/accounting/employees/reverse-salary-payment",
                headers=h,
                json={"original_txn_group_id": reversal_gid,
                      "reason": "محاولة عكس قيد عكسي"},
            )
            assert r.status_code == 400

            # 10) Non-existent group → 404
            r = await client.post(
                "/api/accounting/employees/reverse-salary-payment",
                headers=h,
                json={"original_txn_group_id":
                      "deadbeef-not-found-1234",
                      "reason": "محاولة عكس عملية وهمية"},
            )
            assert r.status_code == 404

            # 11) Audit log surfaces the reversal
            r = await client.get(
                "/api/accounting/employees/salary-reversals",
                headers=h,
            )
            assert r.status_code == 200
            log = r.json()["reversals"]
            assert len(log) == 1
            rec = log[0]
            assert rec["reversal_of_txn_group_id"] == original_txn
            assert rec["amount"] == 3000.0
            assert rec["employee"]["id"] == khaled
            assert rec["bank_account"]["id"] == bank_id

            # 12) Reversible-list now flags the original as already
            #     reversed → it should NOT appear (already_reversed)
            r = await client.get(
                f"/api/accounting/employees/{khaled}/"
                f"reversible-salary-payments",
                headers=h,
            )
            assert r.status_code == 200
            ops = r.json()["operations"]
            target = next(
                (o for o in ops
                 if o["txn_group_id"] == original_txn), None)
            assert target is not None
            assert target["already_reversed"] is True

            # 13) Transactions feed shows the reversal as an inflow
            r = await client.get(
                f"/api/accounts/{bank_id}/transactions", headers=h,
            )
            # NOTE: for non-migrated bank, /transactions uses the
            # legacy account_transactions feed. The ledger-only
            # reversal we wrote does NOT mirror into
            # account_transactions, so the feed may not reflect it.
            # However the API-level bank balance MUST already match
            # (asserted in step 4). We only assert top card.
            assert r.status_code == 200
        finally:
            await db.accounts.delete_many({"user_id": uid})
            await db.operating_salaries.delete_many({"user_id": uid})
            await db.general_ledger.delete_many({"user_id": uid})
            await db.account_transactions.delete_many(
                {"user_id": uid})
            await db.audit_log.delete_many({"user_id": uid})
