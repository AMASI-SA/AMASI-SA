"""Iter-171b — Recompute endpoint also recomputes the cached balance.

Production bug: After running the Iter-169 recompute the card showed:
  الرصيد = 116,351.99 (wrong — should be 0 after correction)
  المديونية = 90,364.08 (correct — fixed by Iter-169)

Root cause: Iter-169 only updated the `liabilities` row but not the
`counterparties.balance` cached field. The negative-delta correction
branch had refunded the «fake» 116K to balance — but that money never
existed; it was the offset of a buggy inflated sync.

Fix: the recompute endpoint now walks the ledger chronologically,
applying the SAME logic as the sync engine (topup adds to balance,
positive spend consumes balance first then creates debt, negative spend
unwinds debt first then refunds balance, settlements/writeoffs reduce
debt). The resulting `balance_walk` is written to
`counterparties.balance`.
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


@pytest.mark.asyncio
async def test_recompute_fixes_stale_balance_after_correction():
    """Reproduce production scenario:
       1. User had 0 real balance.
       2. Buggy sync added inflated 200K spend → debt 84K + balance 0.
          (We model this as: 1 topup 116K then 1 spend 200K.)
       3. Correction inserted -84K spend → balance refunded to 116K
          but liability was NOT reduced (legacy bug).
       4. User clicks recompute → BOTH balance AND debt should match
          the ledger-walked reality.
    """
    async with AsyncClient(transport=ASGITransport(app=app),
                            base_url="http://test") as client:
        email = f"bal171b-{os.urandom(3).hex()}@test.com"
        r = await client.post("/api/auth/register",
                              json={"name": "B", "email": email,
                                    "password": "pass1234"})
        token = r.json()["access_token"]
        uid = r.json()["id"]
        h = {"Authorization": f"Bearer {token}"}

        c = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = c[os.environ["DB_NAME"]]
        cp_id = str(uuid.uuid4())

        try:
            # Counterparty with the WRONG cached balance (116K) and a
            # stale 200K liability — exactly what the production bug
            # created.
            await db.counterparties.insert_one({
                "id": cp_id, "user_id": uid, "kind": "ad_account",
                "ad_provider": "snapchat", "name": "Snap A",
                "external_account_id": "snap-ext", "balance": 116000,
                "debt_mode": "auto",
            })
            await db.liabilities.insert_one({
                "id": str(uuid.uuid4()), "user_id": uid,
                "counterparty_id": cp_id, "kind": "ad_account",
                "expected_amount": 200000, "paid_amount": 0,
                "status": "unpaid",
                "auto_generated": True, "source": "ad_account_cron",
            })
            # Ledger truth: 116K topup, 200K spend, -84K correction
            # Net spend = 116K. Topups = 116K. → True debt = 0.
            # Walking: balance 0 → +116 (topup=116K) → 0 covered/116
            # spend after 116K consumed → 200-116=84 uncovered, debt=84
            # → -84 correction: unwind debt 84 → debt=0, no residual
            # refund. So balance=0, debt=0.
            await db.ad_account_ledger.insert_many([
                {"id": str(uuid.uuid4()), "user_id": uid,
                 "counterparty_id": cp_id, "type": "topup",
                 "amount": 116000, "date": "2026-02-10"},
                {"id": str(uuid.uuid4()), "user_id": uid,
                 "counterparty_id": cp_id, "type": "spend",
                 "amount": 200000, "date": "2026-02-12",
                 "breakdown": {"from_balance": 116000,
                                "uncovered": 84000}},
                {"id": str(uuid.uuid4()), "user_id": uid,
                 "counterparty_id": cp_id, "type": "spend",
                 "amount": -84000, "date": "2026-02-13",
                 "breakdown": {"correction": True}},
            ])

            # Pre-recompute: card shows stale 116K balance + 200K debt
            r = await client.get("/api/ad-accounts", headers=h)
            card = next(x for x in r.json()["items"] if x["id"] == cp_id)
            assert card["balance"] == 116000.0
            assert card["open_debt"] == 200000.0

            # Run recompute
            r = await client.post(
                f"/api/ad-accounts/{cp_id}/recover/recompute-debt-from-ledger",
                headers=h)
            d = r.json()
            assert d["previous_balance"] == 116000.0
            assert d["new_balance"] == 0.0, (
                f"Balance must reflect ledger walk; got {d['new_balance']}")
            assert d["new_open_debt"] == 0.0

            # Card now reflects 0 / 0
            r = await client.get("/api/ad-accounts", headers=h)
            card = next(x for x in r.json()["items"] if x["id"] == cp_id)
            assert card["balance"] == 0.0
            assert card["open_debt"] == 0.0
        finally:
            await db.counterparties.delete_many({"user_id": uid})
            await db.liabilities.delete_many({"user_id": uid})
            await db.ad_account_ledger.delete_many({"user_id": uid})
