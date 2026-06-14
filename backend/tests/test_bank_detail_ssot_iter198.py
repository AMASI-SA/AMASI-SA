"""Iter-198 — Bank account-detail SSOT unification test.

Production bug (reported by merchant):
    • Top card on /account/{id} showed 166,449.30 SAR (from iter-192
      ledger SSOT — correct).
    • Last `balance_after` in the transactions log showed 254,208.67
      SAR (stale, sourced from frozen `account_transactions` —
      operations posted to `general_ledger` after migration never
      mirrored back into `account_transactions`).
    • Drift: 87,759.37 SAR — kashf el-7esab unreliable.

This Iter-198 fix routes the transactions endpoint through
`_ledger_based_tx_feed()` for any bank/cash account that has an
`opening_balance` entry in `general_ledger`. The test below
PROVES that after a typical operational mix (settle, expense,
internal transfer recorded in the ledger):

    accounts.live_balance (top card)
      == last balance_after in /transactions
      == ledger compute_balance().net_balance
      == accounts/summary.by_type.bank entry

Consolidated into ONE async function (per the project's known
pytest-asyncio loop-close limitation in HTTP-based tests).
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
async def test_bank_detail_ssot_unification():
    async with AsyncClient(transport=ASGITransport(app=app),
                            base_url="http://test") as client:
        email = f"i198-{os.urandom(3).hex()}@test.com"
        r = await client.post("/api/auth/register", json={
            "name": "I198", "email": email, "password": "pass1234",
        })
        token = r.json()["access_token"]
        uid = r.json()["id"]
        h = {"Authorization": f"Bearer {token}"}

        c = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = c[os.environ["DB_NAME"]]

        # ── Seed: migrated bank ────────────────────────────────
        # The opening_balance ledger row simulates a Phase-4 migration
        # snapshot that anchors the SSOT.
        bank_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        # Intentional stale field: matches the production bug shape —
        # accounts.current_balance is OUT OF SYNC with the ledger.
        await db.accounts.insert_one({
            "id": bank_id, "user_id": uid, "account_type": "bank",
            "name": "بنك الإنماء (test)", "currency": "SAR",
            "current_balance": 254_208.67,
            "expected_orders_balance": 100_000.0,
            "opening_balance": 100_000.0,
            "status": "active",
            "created_at": now, "updated_at": now,
        })

        # ── Ledger seed via post_txn_group so the feed sees real
        #    posted entries (not raw inserts). 1) opening, 2) sale,
        #    3) expense, 4) settle-back. Mix of debit/credit.
        sys.path.insert(0, "/app/backend")
        from ledger_core import post_txn_group, compute_balance

        async def _post(entries, txn_type="adjustment", notes=""):
            return await post_txn_group(
                db, user_id=uid, actor_id=uid, actor_name="I198",
                entries=entries, txn_type=txn_type, notes=notes,
            )

        # opening 100,000 (debit bank / credit owner_equity)
        await _post(
            [
                {"entity_type": "bank", "entity_id": bank_id,
                 "sub_account": "main", "side": "debit",
                 "amount": 100_000.0, "entry_type": "opening_balance",
                 "notes": "Opening — Phase 4"},
                {"entity_type": "equity", "entity_id": "owner",
                 "sub_account": "main", "side": "credit",
                 "amount": 100_000.0, "entry_type": "opening_balance",
                 "notes": "Opening contra"},
            ],
            txn_type="opening_balance",
            notes="Opening — Phase 4",
        )
        # +50,000 inflow
        await _post(
            [
                {"entity_type": "bank", "entity_id": bank_id,
                 "sub_account": "main", "side": "debit",
                 "amount": 50_000.0, "entry_type": "payment",
                 "notes": "Salla settlement"},
                {"entity_type": "revenue", "entity_id": "sales",
                 "sub_account": "main", "side": "credit",
                 "amount": 50_000.0, "entry_type": "payment"},
            ],
            txn_type="sale", notes="Salla settlement",
        )
        # −3,000 expense
        await _post(
            [
                {"entity_type": "bank", "entity_id": bank_id,
                 "sub_account": "main", "side": "credit",
                 "amount": 3_000.0, "entry_type": "expense_record",
                 "notes": "اشتراك تابي"},
                {"entity_type": "expense", "entity_id": "subs",
                 "sub_account": "main", "side": "debit",
                 "amount": 3_000.0, "entry_type": "expense_record"},
            ],
            txn_type="expense", notes="اشتراك تابي",
        )
        # +17,449.30 inflow
        await _post(
            [
                {"entity_type": "bank", "entity_id": bank_id,
                 "sub_account": "main", "side": "debit",
                 "amount": 17_449.30, "entry_type": "payment",
                 "notes": "Tabby payout"},
                {"entity_type": "revenue", "entity_id": "sales",
                 "sub_account": "main", "side": "credit",
                 "amount": 17_449.30, "entry_type": "payment"},
            ],
            txn_type="sale", notes="Tabby payout",
        )
        # −48,000 salary payment
        await _post(
            [
                {"entity_type": "bank", "entity_id": bank_id,
                 "sub_account": "main", "side": "credit",
                 "amount": 48_000.0, "entry_type": "salary_payment",
                 "notes": "صرف رواتب"},
                {"entity_type": "expense", "entity_id": "payroll",
                 "sub_account": "main", "side": "debit",
                 "amount": 48_000.0, "entry_type": "salary_payment"},
            ],
            txn_type="salary_payment", notes="صرف رواتب",
        )

        try:
            # 1) Ledger ground-truth balance
            bal = await compute_balance(
                db, user_id=uid, entity_type="bank",
                entity_id=bank_id, sub_account="main",
            )
            expected = round(float(bal["net_balance"]), 2)
            # 100,000 + 50,000 - 3,000 + 17,449.30 - 48,000 = 116,449.30
            assert expected == 116_449.30, expected

            # 2) Top card (GET /accounts/{id}) — must reflect ledger
            r = await client.get(f"/api/accounts/{bank_id}", headers=h)
            assert r.status_code == 200, r.text
            top = r.json()
            assert top["balance_source"] == "ledger", top.get(
                "balance_source")
            assert top["current_balance"] == expected, (
                f"top card={top['current_balance']} ledger={expected} "
                f"current_balance_ledger={top.get('current_balance_ledger')}"
            )

            # 3) Transactions log — running balance must terminate
            #    at the SAME number (last txn in chrono order).
            r = await client.get(
                f"/api/accounts/{bank_id}/transactions", headers=h,
            )
            assert r.status_code == 200, r.text
            txs = r.json()
            assert len(txs) == 5, len(txs)
            # The endpoint returns newest-first. The first item is the
            # LATEST event, i.e. its balance_after is the live balance.
            assert all(t["source"] == "ledger" for t in txs)
            last_running = txs[0]["balance_after"]
            assert last_running == expected, (
                f"last running balance ({last_running}) does NOT "
                f"match top card ({expected}) — SSOT bug"
            )

            # 4) The oldest entry is the opening, with balance_after
            #    equal to the opening amount itself.
            oldest = txs[-1]
            assert oldest["transaction_type"] == "opening_balance"
            assert oldest["balance_after"] == 100_000.0
            assert oldest["direction"] == "in"

            # 5) Sanity: every middle row's balance_after walks
            #    correctly when read in chronological order.
            chrono = list(reversed(txs))
            running = 0.0
            for t in chrono:
                amt = float(t["amount"])
                if t["direction"] == "in":
                    running += amt
                else:
                    running -= amt
                assert round(running, 2) == t["balance_after"], (
                    f"running mismatch at txn {t['id']}: "
                    f"computed={round(running, 2)} "
                    f"stored={t['balance_after']}"
                )

            # 6) GET /accounts/summary — by_type.bank must agree
            r = await client.get("/api/accounts/summary", headers=h)
            assert r.status_code == 200
            summary = r.json()
            assert summary["by_type"]["bank"] == expected, (
                f"summary bank total ({summary['by_type']['bank']}) "
                f"diverges from ledger ({expected})"
            )

            # 7) Adding ANOTHER ledger entry must move BOTH the top
            #    card and the last running balance in lock-step.
            await _post(
                [
                    {"entity_type": "bank", "entity_id": bank_id,
                     "sub_account": "main", "side": "debit",
                     "amount": 500.0, "entry_type": "payment",
                     "notes": "اختبار بعد التحديث"},
                    {"entity_type": "revenue", "entity_id": "sales",
                     "sub_account": "main", "side": "credit",
                     "amount": 500.0, "entry_type": "payment"},
                ],
                txn_type="sale", notes="اختبار بعد التحديث",
            )
            new_expected = round(expected + 500.0, 2)

            r1 = await client.get(f"/api/accounts/{bank_id}",
                                   headers=h)
            r2 = await client.get(
                f"/api/accounts/{bank_id}/transactions", headers=h,
            )
            assert r1.json()["current_balance"] == new_expected
            assert r2.json()[0]["balance_after"] == new_expected
            assert r1.json()["current_balance"] == \
                r2.json()[0]["balance_after"]

            # 8) accounts.current_balance raw field MAY still be the
            #    stale 254,208.67 — that's expected (legacy field is
            #    untouched). What MATTERS is that the API never
            #    returns it for migrated accounts.
            raw = await db.accounts.find_one(
                {"id": bank_id, "user_id": uid}, {"_id": 0},
            )
            assert raw["current_balance"] == 254_208.67, (
                "Raw legacy field must remain frozen until a true "
                "migration writes a fresh value."
            )
            # But the API returns the ledger value.
            assert r1.json()["current_balance_legacy"] == 254_208.67
            assert r1.json()["current_balance"] == new_expected

            # ── 9) Regression: a fresh bank with NO ledger anchor
            #    must keep the legacy account_transactions feed.
            bank2 = str(uuid.uuid4())
            await db.accounts.insert_one({
                "id": bank2, "user_id": uid, "account_type": "bank",
                "name": "بنك جديد بدون migration",
                "currency": "SAR",
                "current_balance": 5_000.0,
                "expected_orders_balance": 0.0,
                "opening_balance": 5_000.0,
                "status": "active",
            })
            await db.account_transactions.insert_one({
                "id": str(uuid.uuid4()), "user_id": uid,
                "account_id": bank2,
                "transaction_type": "opening_balance",
                "amount": 5_000.0, "direction": "in",
                "balance_after": 5_000.0, "status": "posted",
                "transaction_date": "2026-01-01",
            })
            r = await client.get(
                f"/api/accounts/{bank2}/transactions", headers=h,
            )
            assert r.status_code == 200
            legacy_feed = r.json()
            assert len(legacy_feed) == 1
            assert legacy_feed[0]["source"] == "account_tx", (
                "Non-migrated bank must remain on the legacy feed"
            )
            assert legacy_feed[0]["balance_after"] == 5_000.0
        finally:
            await db.accounts.delete_many({"user_id": uid})
            await db.general_ledger.delete_many({"user_id": uid})
            await db.account_transactions.delete_many(
                {"user_id": uid})


@pytest.mark.asyncio
async def _OBSOLETE_separate_non_migrated_test():
    """Removed — consolidated into the main test above to dodge the
    project-wide pytest-asyncio loop-close limitation. Kept as a
    no-op so file-level imports remain stable."""
    return
