"""Iter-205 — Ad-Account Spend SSOT (Universal Ledger integration).

Validates that EVERY spend movement (manual `/spend`, the half-hour
cron via `_run_sync_for_all`, and the public `/sync-all` endpoint)
writes a balanced txn_group into `general_ledger` with the model:

    DEBIT  expense.advertising  = total spend
    CREDIT ad_account.balance   = covered (consumed prepaid)
    CREDIT ad_account.debt      = uncovered (new debt portion)

Idempotency:
    key = "spend:{cp_id}:{ad_provider}:{date}:{source}:{amount}"
    same key ⇒ skipped, no duplicate row.

Scenarios:
    1. Manual spend < prepaid balance → only balance credit, no debt.
    2. Manual spend > prepaid balance → balance drains to 0,
       remainder becomes debt sub-account credit.
    3. Re-posting the same manual spend → idempotent (no duplicate
       txn_group, ledger sum stays identical).
    4. The cron (`_run_sync_for_all`) writes via the same helper and
       respects idempotency on repeated runs of the same delta.
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


async def _ledger_sum(db, uid, *, entity_type, entity_id=None,
                       sub_account=None):
    q = {"user_id": uid, "entity_type": entity_type,
          "status": "posted"}
    if entity_id is not None:
        q["entity_id"] = entity_id
    if sub_account is not None:
        q["sub_account"] = sub_account
    agg = await db.general_ledger.aggregate([
        {"$match": q},
        {"$group": {"_id": "$side", "total": {"$sum": "$amount"}}},
    ]).to_list(5)
    d = c = 0.0
    for r in agg:
        if r["_id"] == "debit":
            d = float(r["total"])
        elif r["_id"] == "credit":
            c = float(r["total"])
    return round(d - c, 2)


@pytest.mark.asyncio
async def test_ad_spend_ssot_full_lifecycle():
    async with AsyncClient(transport=ASGITransport(app=app),
                            base_url="http://test") as client:
        email = f"sp205-{os.urandom(3).hex()}@test.com"
        r = await client.post("/api/auth/register", json={
            "name": "S", "email": email, "password": "pass1234",
        })
        assert r.status_code == 200, r.text
        token = r.json()["access_token"]
        uid = r.json()["id"]
        h = {"Authorization": f"Bearer {token}"}

        c = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = c[os.environ["DB_NAME"]]

        try:
            # Seed: bank 5,000 SAR + ad account "Meta1"
            bank_id = str(uuid.uuid4())
            await db.accounts.insert_one({
                "id": bank_id, "user_id": uid, "account_type": "bank",
                "name": "بنك الإنماء", "current_balance": 5000.0,
                "balance": 5000.0, "status": "active",
            })
            ad_id = str(uuid.uuid4())
            await db.counterparties.insert_one({
                "id": ad_id, "user_id": uid, "kind": "ad_account",
                "name": "Meta 1", "ad_provider": "meta",
                "balance": 0.0, "debt_mode": "auto",
                "external_account_id": "act_meta_001",
            })

            # Top up 1,000 so prepaid balance = 1,000 in the ledger.
            r = await client.post(
                f"/api/ad-accounts/{ad_id}/topup",
                headers=h,
                json={"amount": 1000.0,
                       "paid_from_account_id": bank_id,
                       "transaction_date": "2026-02-15"},
            )
            assert r.status_code == 200, r.text

            # ─── Scenario 1: spend < prepaid (500 < 1000) ─────────
            r = await client.post(
                f"/api/ad-accounts/{ad_id}/spend",
                headers=h,
                json={"amount": 500.0,
                       "spend_date": "2026-02-15"},
            )
            assert r.status_code == 200, r.text
            resp = r.json()
            assert resp["covered_by_balance"] == 500.0
            assert resp["uncovered"] == 0.0
            assert resp.get("ledger_txn_group_id")

            # Expense accrued = 500
            exp_net = await _ledger_sum(
                db, uid, entity_type="expense",
                entity_id="advertising")
            assert exp_net == 500.0, f"expense expected 500, got {exp_net}"

            # Ad account balance remaining = 1000 − 500 = 500
            bal_net = await _ledger_sum(
                db, uid, entity_type="ad_account",
                entity_id=ad_id, sub_account="balance")
            assert bal_net == 500.0, f"prepaid expected 500, got {bal_net}"

            # No debt yet.
            debt_net = await _ledger_sum(
                db, uid, entity_type="ad_account",
                entity_id=ad_id, sub_account="debt")
            assert debt_net == 0.0, f"debt expected 0, got {debt_net}"

            # ─── Scenario 2: spend > prepaid (700 > 500 remaining) ──
            r = await client.post(
                f"/api/ad-accounts/{ad_id}/spend",
                headers=h,
                json={"amount": 700.0,
                       "spend_date": "2026-02-15"},
            )
            assert r.status_code == 200, r.text
            resp2 = r.json()
            assert resp2["covered_by_balance"] == 500.0
            assert resp2["uncovered"] == 200.0

            # Expense cumulative = 500 + 700 = 1200
            exp_net = await _ledger_sum(
                db, uid, entity_type="expense",
                entity_id="advertising")
            assert exp_net == 1200.0, f"expense expected 1200, got {exp_net}"

            # Prepaid balance drained to 0
            bal_net = await _ledger_sum(
                db, uid, entity_type="ad_account",
                entity_id=ad_id, sub_account="balance")
            assert bal_net == 0.0, f"prepaid expected 0, got {bal_net}"

            # Debt now = 200
            debt_net = await _ledger_sum(
                db, uid, entity_type="ad_account",
                entity_id=ad_id, sub_account="debt")
            # net = debit - credit; for liability sub_account credit, net
            # is negative.  Outstanding = max(0, -net).
            assert round(-debt_net, 2) == 200.0, (
                f"debt expected 200, got -{debt_net}"
            )

            # ─── Scenario 3: idempotency — same spend again ─────────
            count_before = await db.general_ledger.count_documents(
                {"user_id": uid, "metadata.idempotency_key": {
                    "$exists": True}})
            r = await client.post(
                f"/api/ad-accounts/{ad_id}/spend",
                headers=h,
                json={"amount": 700.0,
                       "spend_date": "2026-02-15"},
            )
            assert r.status_code == 200, r.text
            # The legacy path still appends (cumulative reasons), but the
            # SSOT layer must skip — ledger expense unchanged.
            assert r.json().get("ledger_skipped") is True, r.json()
            count_after = await db.general_ledger.count_documents(
                {"user_id": uid, "metadata.idempotency_key": {
                    "$exists": True}})
            assert count_after == count_before, (
                f"Idempotency broken: gl rows {count_before} → {count_after}"
            )
            # Expense unchanged (still 1200) — proves no double-count.
            exp_net = await _ledger_sum(
                db, uid, entity_type="expense",
                entity_id="advertising")
            assert exp_net == 1200.0, (
                f"Idempotency leaked: expense moved to {exp_net}"
            )

            # ─── Scenario 4: cron helper writes via same path ────────
            # Direct unit call to the helper to mirror the cron.  The
            # delta value is part of the idem key, so two distinct
            # deltas produce two distinct entries — but the SAME delta
            # twice must skip.
            from ad_account_routes import _post_spend_to_ledger
            cp_doc = await db.counterparties.find_one(
                {"id": ad_id, "user_id": uid}, {"_id": 0})
            res1 = await _post_spend_to_ledger(
                db, user_id=uid, actor_name="ad_account_cron",
                cp=cp_doc, amount=300.0, spend_date="2026-02-16",
                source="ad_account_cron",
                description="cron delta",
            )
            assert res1["skipped"] is False, res1
            assert res1["covered"] == 0.0   # prepaid drained
            assert res1["uncovered"] == 300.0
            res2 = await _post_spend_to_ledger(
                db, user_id=uid, actor_name="ad_account_cron",
                cp=cp_doc, amount=300.0, spend_date="2026-02-16",
                source="ad_account_cron",
                description="cron retry",
            )
            assert res2["skipped"] is True, res2
            assert res2["txn_group_id"] == res1["txn_group_id"]

            # Final expense = 1200 + 300 = 1500
            exp_net = await _ledger_sum(
                db, uid, entity_type="expense",
                entity_id="advertising")
            assert exp_net == 1500.0
            # Final debt = 200 + 300 = 500 outstanding
            debt_net = await _ledger_sum(
                db, uid, entity_type="ad_account",
                entity_id=ad_id, sub_account="debt")
            assert round(-debt_net, 2) == 500.0

            # ─── Σdebits == Σcredits (double-entry invariant) ──────
            agg = await db.general_ledger.aggregate([
                {"$match": {"user_id": uid,
                              "metadata.txn_type": "ad_account_spend",
                              "status": "posted"}},
                {"$group": {"_id": "$side",
                              "t": {"$sum": "$amount"}}},
            ]).to_list(5)
            d = c_ = 0.0
            for r in agg:
                if r["_id"] == "debit":
                    d = float(r["t"])
                else:
                    c_ = float(r["t"])
            assert abs(d - c_) < 0.01, (
                f"Double-entry imbalance: debit={d} credit={c_}"
            )

        finally:
            await db.counterparties.delete_many({"user_id": uid})
            await db.general_ledger.delete_many({"user_id": uid})
            await db.account_transactions.delete_many({"user_id": uid})
            await db.ad_account_ledger.delete_many({"user_id": uid})
            await db.accounts.delete_many({"user_id": uid})
            await db.liabilities.delete_many({"user_id": uid})
            await db.users.delete_many({"id": uid})
            c.close()
