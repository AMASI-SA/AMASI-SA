"""Iter-169 — Ad-account card debt mirrors the audit log after corrections.

Production bug: User clicked sync→ daily total was correctly reduced via
the «negative delta» branch which inserted a «تصحيح مزامنة» row into
`ad_account_ledger` showing the right debt_after. But the card kept
displaying the inflated debt (201,753.81 SAR) because the open
`liabilities` row was never reduced.

Fix:
  1. Negative-delta branch in `_run_sync_for_all` now reduces the
     existing auto-cron liability by the same refund amount.
  2. New endpoint `/api/ad-accounts/{cp_id}/recover/recompute-debt-from-ledger`
     repairs accounts already in the bad state by walking
     `ad_account_ledger` and recomputing the true open debt.
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
async def test_recompute_debt_from_ledger_fixes_stale_card_debt():
    """Seed a counterparty + stale liability (100K) + ledger rows showing
    real net spend of only 5K. After recompute the open_debt drops to 5K.
    """
    async with AsyncClient(transport=ASGITransport(app=app),
                            base_url="http://test") as client:
        email = f"recomp169-{os.urandom(3).hex()}@test.com"
        r = await client.post("/api/auth/register",
                              json={"name": "R", "email": email,
                                    "password": "pass1234"})
        token = r.json()["access_token"]
        uid = r.json()["id"]
        h = {"Authorization": f"Bearer {token}"}

        c = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = c[os.environ["DB_NAME"]]
        cp_id = str(uuid.uuid4())
        liab_id = str(uuid.uuid4())

        try:
            await db.counterparties.insert_one({
                "id": cp_id, "user_id": uid, "kind": "ad_account",
                "ad_provider": "snapchat", "name": "Snap A",
                "external_account_id": "snap-ext", "balance": 0.0,
                "debt_mode": "auto",
            })
            # Stale inflated open liability
            await db.liabilities.insert_one({
                "id": liab_id, "user_id": uid,
                "counterparty_id": cp_id, "kind": "ad_account",
                "expected_amount": 100000, "paid_amount": 0,
                "status": "unpaid",
                "auto_generated": True, "source": "ad_account_cron",
            })
            # Ledger: original sync 100K, then correction -95K → net 5K
            await db.ad_account_ledger.insert_many([
                {"id": str(uuid.uuid4()), "user_id": uid,
                 "counterparty_id": cp_id, "type": "spend",
                 "amount": 100000, "date": "2026-02-12",
                 "breakdown": {"from_balance": 0, "uncovered": 100000,
                               "auto_cron": True}},
                {"id": str(uuid.uuid4()), "user_id": uid,
                 "counterparty_id": cp_id, "type": "spend",
                 "amount": -95000, "date": "2026-02-12",
                 "breakdown": {"correction": True, "auto_cron": True}},
            ])

            # Verify the card initially shows stale 100K
            r = await client.get("/api/ad-accounts", headers=h)
            cards = r.json()["items"]
            card = next(x for x in cards if x["id"] == cp_id)
            assert card["open_debt"] == 100000.0

            # Run recompute
            r = await client.post(
                f"/api/ad-accounts/{cp_id}/recover/recompute-debt-from-ledger",
                headers=h)
            assert r.status_code == 200
            d = r.json()
            assert d["previous_open_debt"] == 100000.0
            assert d["new_open_debt"] == 5000.0
            assert d["delta"] == -95000.0

            # Card now reflects 5K
            r = await client.get("/api/ad-accounts", headers=h)
            card = next(x for x in r.json()["items"] if x["id"] == cp_id)
            assert card["open_debt"] == 5000.0

            # Idempotent
            r = await client.post(
                f"/api/ad-accounts/{cp_id}/recover/recompute-debt-from-ledger",
                headers=h)
            assert r.json()["new_open_debt"] == 5000.0
            assert r.json()["delta"] == 0.0
        finally:
            await db.counterparties.delete_many({"user_id": uid})
            await db.liabilities.delete_many({"user_id": uid})
            await db.ad_account_ledger.delete_many({"user_id": uid})


@pytest.mark.asyncio
async def test_recompute_zeroes_debt_when_topups_exceed_spend():
    """If the user topped up more than they spent, open_debt → 0 and
    the liability is marked paid."""
    async with AsyncClient(transport=ASGITransport(app=app),
                            base_url="http://test") as client:
        email = f"recomp169b-{os.urandom(3).hex()}@test.com"
        r = await client.post("/api/auth/register",
                              json={"name": "R", "email": email,
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
                "ad_provider": "snapchat", "name": "Snap B",
                "external_account_id": "snap-ext-b", "balance": 0.0,
                "debt_mode": "auto",
            })
            await db.liabilities.insert_one({
                "id": str(uuid.uuid4()), "user_id": uid,
                "counterparty_id": cp_id, "kind": "ad_account",
                "expected_amount": 50000, "paid_amount": 0,
                "status": "unpaid",
                "auto_generated": True, "source": "ad_account_cron",
            })
            await db.ad_account_ledger.insert_many([
                {"id": str(uuid.uuid4()), "user_id": uid,
                 "counterparty_id": cp_id, "type": "topup",
                 "amount": 10000, "date": "2026-02-12"},
                {"id": str(uuid.uuid4()), "user_id": uid,
                 "counterparty_id": cp_id, "type": "spend",
                 "amount": 8000, "date": "2026-02-12",
                 "breakdown": {"from_balance": 8000, "uncovered": 0}},
            ])

            r = await client.post(
                f"/api/ad-accounts/{cp_id}/recover/recompute-debt-from-ledger",
                headers=h)
            assert r.json()["new_open_debt"] == 0.0

            # Card open_debt is now 0
            r = await client.get("/api/ad-accounts", headers=h)
            card = next(x for x in r.json()["items"] if x["id"] == cp_id)
            assert card["open_debt"] == 0.0
        finally:
            await db.counterparties.delete_many({"user_id": uid})
            await db.liabilities.delete_many({"user_id": uid})
            await db.ad_account_ledger.delete_many({"user_id": uid})
