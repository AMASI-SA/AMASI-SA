"""Iter-159n — Recompute debt endpoint test."""
import os, uuid
from datetime import datetime, timezone
import pytest
from httpx import AsyncClient, ASGITransport
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")
import sys
sys.path.insert(0, "/app/backend")
from server import app  # noqa: E402
from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402


@pytest.mark.asyncio
async def test_recompute_collapses_bloated_liability_to_truth():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        email = f"recompute-{os.urandom(3).hex()}@test.com"
        r = await client.post("/api/auth/register",
                              json={"name": "T", "email": email,
                                    "password": "pass1234"})
        token = r.json()["access_token"]
        uid = r.json()["id"]
        h = {"Authorization": f"Bearer {token}"}

        c = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = c[os.environ["DB_NAME"]]

        # Setup: ad account with ledger showing 3000 uncovered spend,
        # 1000 already paid via partial liability, but a SECOND inflated
        # liability of 6000 (bug residue) still open. Real outstanding
        # should be 3000 - 1000 = 2000.
        cp_id = str(uuid.uuid4())
        await db.counterparties.insert_one({
            "id": cp_id, "user_id": uid, "kind": "ad_account",
            "name": "Bloated", "name_lower": "bloated",
            "ad_provider": "meta", "balance": 0.0,
        })
        # Three ledger rows totalling 3000 uncovered
        for amt in [1000.0, 1000.0, 1000.0]:
            await db.ad_account_ledger.insert_one({
                "id": str(uuid.uuid4()), "user_id": uid,
                "counterparty_id": cp_id, "type": "spend",
                "amount": amt, "balance_after": 0,
                "debt_after": amt,
                "breakdown": {"uncovered": amt, "from_balance": 0,
                              "auto_cron": True},
                "date": "2026-06-12",
                "created_at": datetime.now(timezone.utc).isoformat(),
            })
        # First liability — partially paid (1000 of 1500)
        await db.liabilities.insert_one({
            "id": str(uuid.uuid4()), "user_id": uid, "kind": "ad_account",
            "counterparty_id": cp_id, "expected_amount": 1500.0,
            "paid_amount": 1000.0, "status": "partial",
            "description": "old debt", "source": "ad_account_cron",
            "auto_generated": True, "due_date": "2026-06-12",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })
        # Bug-residue inflated liability — open, never paid
        await db.liabilities.insert_one({
            "id": str(uuid.uuid4()), "user_id": uid, "kind": "ad_account",
            "counterparty_id": cp_id, "expected_amount": 6000.0,
            "paid_amount": 0.0, "status": "unpaid",
            "description": "bug residue", "source": "ad_account_migration",
            "auto_generated": True, "due_date": "2026-06-12",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })

        # Before recompute: open debt = 500 (partial) + 6000 (bug) = 6500
        r = await client.post(f"/api/ad-accounts/{cp_id}/recompute-debt",
                              headers=h)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["total_ledger_uncovered"] == 3000.0
        assert body["total_paid"] == 1000.0
        assert body["correct_outstanding"] == 2000.0  # 3000 - 1000
        assert body["previous_open_debt"] == 6500.0   # 500 + 6000

        # State after: exactly ONE open liability with 2000.
        open_liabs = await db.liabilities.find(
            {"user_id": uid, "counterparty_id": cp_id,
             "kind": "ad_account",
             "status": {"$in": ["unpaid", "partial"]}}, {"_id": 0}
        ).to_list(20)
        assert len(open_liabs) == 1
        assert open_liabs[0]["expected_amount"] == 2000.0
        assert open_liabs[0]["source"] == "ad_account_recompute"

        # Cleanup
        await db.counterparties.delete_many({"user_id": uid})
        await db.liabilities.delete_many({"user_id": uid})
        await db.ad_account_ledger.delete_many({"user_id": uid})
