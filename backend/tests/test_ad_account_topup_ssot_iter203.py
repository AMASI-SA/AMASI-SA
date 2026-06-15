"""Iter-203 — Ad Account Top-up SSOT (Asset-to-Asset Transfer)

P0 bug fix: Top-up MUST debit the bank/cash and credit the ad account
in the universal ledger (general_ledger) so the financial position,
bank statement, and ad-account screens all reflect the deduction.

Guarantees enforced here:
    1. A balanced 2-leg txn_group lands in general_ledger:
       DEBIT  ad_account.balance + CREDIT bank.main
    2. Bank live balance (Iter-198 SSOT) decreases by the topup amount.
    3. Ad-account legacy balance increases by the same amount.
    4. Insufficient bank balance → 400, NOTHING written.
    5. NO expense entry is created at top-up time.
    6. Returned response includes ledger_txn_group_id.
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


async def _bank_live(db, uid, bank_id):
    """Read live balance from the same resolver the UI uses."""
    from balance_resolver import resolve_live_balance_by_id  # noqa
    res = await resolve_live_balance_by_id(
        db, user_id=uid, account_id=bank_id,
    )
    return float((res or {}).get("balance") or 0)


async def _ledger_legs(db, uid, txn_group_id):
    rows = await db.general_ledger.find(
        {"user_id": uid, "txn_group_id": txn_group_id},
        {"_id": 0},
    ).to_list(20)
    return rows


@pytest.mark.asyncio
async def test_ad_topup_ssot_asset_transfer():
    async with AsyncClient(transport=ASGITransport(app=app),
                            base_url="http://test") as client:
        email = f"adt203-{os.urandom(3).hex()}@test.com"
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
            # Seed a bank with 10,000 SAR opening balance.
            bank_id = str(uuid.uuid4())
            await db.accounts.insert_one({
                "id": bank_id, "user_id": uid, "account_type": "bank",
                "name": "بنك الإنماء", "current_balance": 10_000.0,
                "balance": 10_000.0, "status": "active",
            })
            # Seed an ad-account counterparty (Snap).
            ad_id = str(uuid.uuid4())
            await db.counterparties.insert_one({
                "id": ad_id, "user_id": uid, "kind": "ad_account",
                "name": "Snap Account 1", "ad_provider": "snapchat",
                "balance": 0.0, "debt_mode": "auto",
                "external_account_id": "acc_test",
            })

            # ─── Scenario 1: Insufficient funds → 400 ─────────────
            r = await client.post(
                f"/api/ad-accounts/{ad_id}/topup",
                headers=h,
                json={"amount": 50_000,
                       "paid_from_account_id": bank_id,
                       "transaction_date": "2026-02-15"},
            )
            assert r.status_code == 400, r.text
            assert "غير كافٍ" in r.json()["detail"]
            # Nothing was written.
            assert await db.general_ledger.count_documents(
                {"user_id": uid, "entity_type": "ad_account"}
            ) == 0
            assert await db.account_transactions.count_documents(
                {"user_id": uid, "transaction_type": "ad_account_topup"}
            ) == 0

            # ─── Scenario 2: Successful topup (asset transfer) ────
            r = await client.post(
                f"/api/ad-accounts/{ad_id}/topup",
                headers=h,
                json={"amount": 3_000,
                       "paid_from_account_id": bank_id,
                       "transaction_date": "2026-02-15",
                       "notes": "تعبئة من الإنماء"},
            )
            assert r.status_code == 200, r.text
            resp = r.json()
            assert resp["ok"] is True
            assert resp["amount"] == 3000.0
            assert resp["applied_to_balance"] == 3000.0
            txn_group_id = resp["ledger_txn_group_id"]
            assert txn_group_id

            # 1) Bank live balance dropped by 3,000.
            bank_live = await _bank_live(db, uid, bank_id)
            assert abs(bank_live - 7_000.0) < 0.01, (
                f"Expected bank=7000; got {bank_live}"
            )

            # 2) Ad-account balance increased by 3,000.
            assert resp["ad_account"]["balance"] == 3_000.0

            # 3) Ledger has TWO legs, balanced, double-entry.
            legs = await _ledger_legs(db, uid, txn_group_id)
            assert len(legs) == 2, f"Expected 2 legs; got {len(legs)}"
            by_side = {x["side"]: x for x in legs}
            assert "debit" in by_side and "credit" in by_side
            assert by_side["debit"]["entity_type"] == "ad_account"
            assert by_side["debit"]["entity_id"] == ad_id
            assert by_side["debit"]["sub_account"] == "balance"
            assert by_side["debit"]["entry_type"] == "topup"
            assert by_side["debit"]["amount"] == 3_000.0
            assert by_side["credit"]["entity_type"] == "bank"
            assert by_side["credit"]["entity_id"] == bank_id
            assert by_side["credit"]["sub_account"] == "main"
            assert by_side["credit"]["entry_type"] == "topup"
            assert by_side["credit"]["amount"] == 3_000.0
            # Status posted.
            assert all(l["status"] == "posted" for l in legs)
            # Double-entry invariant
            d_total = sum(l["amount"] for l in legs if l["side"] == "debit")
            c_total = sum(l["amount"] for l in legs if l["side"] == "credit")
            assert abs(d_total - c_total) < 0.01

            # 4) No expense entry — neither in general_ledger nor in
            #    legacy expenses collection.
            exp_count = await db.general_ledger.count_documents(
                {"user_id": uid,
                 "entry_type": {"$in": ["expense_record", "expense"]},
                 "txn_group_id": txn_group_id},
            )
            assert exp_count == 0, "Top-up must NOT create an expense"
            legacy_exp = await db.expenses.count_documents(
                {"user_id": uid},
            )
            assert legacy_exp == 0

            # 5) Bank statement endpoint shows the topup row.
            r = await client.get(
                f"/api/accounts/{bank_id}/transactions", headers=h,
            )
            assert r.status_code == 200, r.text
            data = r.json()
            items = data if isinstance(data, list) else (
                data.get("transactions") or data.get("items") or []
            )
            assert any(
                str(item.get("amount")) == "3000.0"
                or float(item.get("amount") or 0) == 3000.0
                for item in items
            ), f"Bank statement missing topup row: {items}"

            # 6) Financial-position accounts endpoint reflects new bank
            #    balance and ad account is reachable via ad-accounts list.
            r = await client.get(
                "/api/ad-accounts", headers=h,
            )
            assert r.status_code == 200, r.text
            ads = r.json()["items"]
            this_ad = next(a for a in ads if a["id"] == ad_id)
            assert this_ad["balance"] == 3_000.0

            # ─── Scenario 3: Second topup — verify cumulative deduct ──
            r = await client.post(
                f"/api/ad-accounts/{ad_id}/topup",
                headers=h,
                json={"amount": 1_500,
                       "paid_from_account_id": bank_id,
                       "transaction_date": "2026-02-15"},
            )
            assert r.status_code == 200, r.text
            bank_live2 = await _bank_live(db, uid, bank_id)
            assert abs(bank_live2 - 5_500.0) < 0.01, (
                f"After two topups expected bank=5500; got {bank_live2}"
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
