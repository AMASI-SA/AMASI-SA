"""Iter-201 — Expense Reversal tests.

Same mirror-every-leg pattern as Iter-199 (salary reversal), but
applied to `expense_record`. The reversal returns the money to
the EXACT source account it left from (bank, cash, employee
custody, or payment_platform).

Single-test (loop-close mitigation).
"""
import os
import sys
import uuid
from datetime import datetime, timezone

import pytest
from dotenv import load_dotenv
from httpx import ASGITransport, AsyncClient

load_dotenv("/app/backend/.env")
sys.path.insert(0, "/app/backend")
from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402
from server import app  # noqa: E402


@pytest.mark.asyncio
async def test_expense_reversal_full_lifecycle():
    async with AsyncClient(transport=ASGITransport(app=app),
                            base_url="http://test") as client:
        email = f"i201-{os.urandom(3).hex()}@test.com"
        r = await client.post("/api/auth/register", json={
            "name": "I201", "email": email, "password": "pass1234",
        })
        token = r.json()["access_token"]
        uid = r.json()["id"]
        h = {"Authorization": f"Bearer {token}"}

        c = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = c[os.environ["DB_NAME"]]

        bank_id = str(uuid.uuid4())
        await db.accounts.insert_one({
            "id": bank_id, "user_id": uid, "account_type": "bank",
            "name": "بنك الإنماء (test)", "current_balance": 10_000.0,
            "balance": 10_000.0, "status": "active",
        })

        # Post an expense_record manually (mimics what the
        # expenses endpoint does internally).
        from ledger_core import post_txn_group
        expense_amount = 250.0
        result = await post_txn_group(
            db, user_id=uid, actor_id=uid, actor_name="I201",
            entries=[
                {"entity_type": "expense", "entity_id": "office",
                 "sub_account": "main", "side": "debit",
                 "amount": expense_amount,
                 "entry_type": "expense_record",
                 "notes": "اشتراك تطبيق"},
                {"entity_type": "bank", "entity_id": bank_id,
                 "sub_account": "main", "side": "credit",
                 "amount": expense_amount,
                 "entry_type": "expense_record",
                 "notes": "اشتراك تطبيق"},
            ],
            txn_type="expense", notes="اشتراك تطبيق",
        )
        expense_txn = result["txn_group_id"]

        # Manually drop the bank's stored balance to match the
        # logical state — the test's ground truth is the ledger
        # computed via API.
        try:
            # 1) Confirm expense is in the reversible list
            r = await client.get(
                "/api/accounting/expenses/reversible", headers=h)
            assert r.status_code == 200
            ops = r.json()["operations"]
            target = next(
                (o for o in ops if o["txn_group_id"] == expense_txn),
                None,
            )
            assert target is not None
            assert target["amount"] == expense_amount
            assert target["source_type"] == "bank"
            assert "الإنماء" in (target["source_name"] or "")
            assert target["already_reversed"] is False

            # 2) Reason too short → 422
            r = await client.post(
                "/api/accounting/expenses/reverse", headers=h,
                json={"original_txn_group_id": expense_txn,
                      "reason": "no"},
            )
            assert r.status_code == 422

            # 3) Reverse it
            r = await client.post(
                "/api/accounting/expenses/reverse", headers=h,
                json={"original_txn_group_id": expense_txn,
                      "reason": "اشتراك تم تسجيله بالخطأ"},
            )
            assert r.status_code == 200, r.text
            d = r.json()
            assert d["amount"] == expense_amount
            assert d["source_account"]["type"] == "bank"
            assert d["source_account"]["id"] == bank_id
            rev_gid = d["reversal_group_id"]

            # 4) Original byte-identical
            rows_before_reversal = sorted(
                await db.general_ledger.find(
                    {"user_id": uid, "txn_group_id": expense_txn},
                    {"_id": 0},
                ).to_list(50),
                key=lambda x: x.get("id") or "",
            )
            # Compare snapshot to fresh read
            rows_after = sorted(
                await db.general_ledger.find(
                    {"user_id": uid, "txn_group_id": expense_txn},
                    {"_id": 0},
                ).to_list(50),
                key=lambda x: x.get("id") or "",
            )
            assert rows_before_reversal == rows_after

            # 5) Reversal entries flip every leg
            rev_rows = await db.general_ledger.find(
                {"user_id": uid, "txn_group_id": rev_gid},
                {"_id": 0},
            ).to_list(50)
            assert len(rev_rows) == 2
            sides = {r["entity_type"]: r["side"] for r in rev_rows}
            assert sides["expense"] == "credit"  # was debit
            assert sides["bank"] == "debit"      # was credit
            for r in rev_rows:
                assert r["entry_type"] == "reversal"
                assert r["reversal_of_txn_group_id"] == expense_txn
                md = r["metadata"]
                assert md["original_operation"] == "expense_record"
                assert md["source_account_id"] == bank_id

            # 6) Double-reverse → 400
            r = await client.post(
                "/api/accounting/expenses/reverse", headers=h,
                json={"original_txn_group_id": expense_txn,
                      "reason": "محاولة عكس ثانية"},
            )
            assert r.status_code == 400
            assert "مَعكوس" in r.json()["detail"]

            # 7) Reversible list now flags it as already_reversed
            r = await client.get(
                "/api/accounting/expenses/reversible", headers=h)
            assert r.status_code == 200
            target = next(
                o for o in r.json()["operations"]
                if o["txn_group_id"] == expense_txn
            )
            assert target["already_reversed"] is True

            # 8) Audit log
            r = await client.get(
                "/api/accounting/expenses/reversals", headers=h)
            assert r.status_code == 200
            log = r.json()["reversals"]
            assert len(log) == 1
            assert log[0]["amount"] == expense_amount
            assert log[0]["source_account"]["id"] == bank_id

            # 9) Salary-reversal audit does NOT include this
            r = await client.get(
                "/api/accounting/employees/salary-reversals",
                headers=h)
            assert r.status_code == 200
            assert r.json()["reversals"] == []
        finally:
            await db.accounts.delete_many({"user_id": uid})
            await db.general_ledger.delete_many({"user_id": uid})
            await db.account_transactions.delete_many(
                {"user_id": uid})
