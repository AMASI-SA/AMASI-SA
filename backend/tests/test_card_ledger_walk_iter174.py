"""Iter-174 — PERMANENT FIX: ad-account cards derive from ledger walk.

This is the definitive fix for the recurring «cached balance/debt
drifts away from the audit log» class of bugs (Iter-163, 169, 171b).
Going forward, the listings endpoint NEVER reads `counterparties.balance`
or any cached value — it always walks `ad_account_ledger` row by row
and replays the sync engine's logic.

This means:
  • No matter how badly cached fields drift on production, the card
    displays the truth.
  • Manual «🔄 إعادة احتساب من السجل» is no longer required.
  • Any new sync bug can no longer produce inflated card numbers
    (they auto-correct on every read).
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
async def test_card_ignores_corrupt_cached_balance():
    """Set counterparties.balance to a wildly wrong value. The card
    must STILL display the value derived from ledger walk, not the
    corrupt cache."""
    async with AsyncClient(transport=ASGITransport(app=app),
                            base_url="http://test") as client:
        email = f"perm174-{os.urandom(3).hex()}@test.com"
        r = await client.post("/api/auth/register",
                              json={"name": "P", "email": email,
                                    "password": "pass1234"})
        token = r.json()["access_token"]
        uid = r.json()["id"]
        h = {"Authorization": f"Bearer {token}"}

        c = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = c[os.environ["DB_NAME"]]
        cp_id = str(uuid.uuid4())

        try:
            # Counterparty with CORRUPT cached balance (25,893.59) and
            # CORRUPT liability (26,122.36) — mirrors the production
            # bug the merchant reported.
            await db.counterparties.insert_one({
                "id": cp_id, "user_id": uid, "kind": "ad_account",
                "ad_provider": "meta", "name": "Meta Test",
                "external_account_id": "act_799549215909312",
                "balance": 25893.59,
                "debt_mode": "auto",
            })
            await db.liabilities.insert_one({
                "id": str(uuid.uuid4()), "user_id": uid,
                "counterparty_id": cp_id, "kind": "ad_account",
                "expected_amount": 26122.36, "paid_amount": 0,
                "status": "unpaid",
                "auto_generated": True, "source": "ad_account_cron",
            })
            # Ledger truth: topup 5000, spend 3000 → balance=2000, debt=0,
            # spend total=3000. (Nothing close to 25K.)
            await db.ad_account_ledger.insert_many([
                {"id": str(uuid.uuid4()), "user_id": uid,
                 "counterparty_id": cp_id, "type": "topup",
                 "amount": 5000, "date": "2026-02-10"},
                {"id": str(uuid.uuid4()), "user_id": uid,
                 "counterparty_id": cp_id, "type": "spend",
                 "amount": 3000, "date": "2026-02-11"},
            ])

            r = await client.get("/api/ad-accounts", headers=h)
            cards = r.json()["items"]
            card = next(x for x in cards if x["id"] == cp_id)
            # Truth from ledger walk (NOT the corrupt cache):
            assert card["balance"] == 2000.0
            assert card["open_debt"] == 0.0
            assert card["total_spend"] == 3000.0
            # The corrupt cache is exposed for diagnostics only:
            assert card["_cached_balance"] == 25893.59
        finally:
            await db.counterparties.delete_many({"user_id": uid})
            await db.liabilities.delete_many({"user_id": uid})
            await db.ad_account_ledger.delete_many({"user_id": uid})


@pytest.mark.asyncio
async def test_card_handles_correction_after_inflated_sync():
    """Re-run the user's production scenario: 116K topup + 200K spend +
    -84K correction → card MUST show balance=0, debt=0, spend=116K net.
    """
    async with AsyncClient(transport=ASGITransport(app=app),
                            base_url="http://test") as client:
        email = f"perm174b-{os.urandom(3).hex()}@test.com"
        r = await client.post("/api/auth/register",
                              json={"name": "P", "email": email,
                                    "password": "pass1234"})
        token = r.json()["access_token"]
        uid = r.json()["id"]
        h = {"Authorization": f"Bearer {token}"}

        c = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = c[os.environ["DB_NAME"]]
        cp_id = str(uuid.uuid4())

        try:
            await db.counterparties.insert_one({
                "id": cp_id, "user_id": uid, "kind": "ad_account",
                "ad_provider": "snapchat", "name": "Snap Inflated",
                "external_account_id": "snap-xyz",
                "balance": 116000,  # stale inflated cache
                "debt_mode": "auto",
            })
            await db.liabilities.insert_one({
                "id": str(uuid.uuid4()), "user_id": uid,
                "counterparty_id": cp_id, "kind": "ad_account",
                "expected_amount": 200000, "paid_amount": 0,
                "status": "unpaid",
                "auto_generated": True, "source": "ad_account_cron",
            })
            await db.ad_account_ledger.insert_many([
                {"id": str(uuid.uuid4()), "user_id": uid,
                 "counterparty_id": cp_id, "type": "topup",
                 "amount": 116000, "date": "2026-02-10"},
                {"id": str(uuid.uuid4()), "user_id": uid,
                 "counterparty_id": cp_id, "type": "spend",
                 "amount": 200000, "date": "2026-02-12"},
                {"id": str(uuid.uuid4()), "user_id": uid,
                 "counterparty_id": cp_id, "type": "spend",
                 "amount": -84000, "date": "2026-02-13"},
            ])

            r = await client.get("/api/ad-accounts", headers=h)
            card = next(x for x in r.json()["items"] if x["id"] == cp_id)
            # Walk: 116 topup→bal=116; 200 spend→cover 116, uncov 84→debt=84,bal=0;
            # -84 correction→unwind debt 84→debt=0,refund=0,bal=0.
            assert card["balance"] == 0.0
            assert card["open_debt"] == 0.0
            # Total spend (net) = 200 - 84 = 116K
            assert card["total_spend"] == 116000.0
        finally:
            await db.counterparties.delete_many({"user_id": uid})
            await db.liabilities.delete_many({"user_id": uid})
            await db.ad_account_ledger.delete_many({"user_id": uid})
