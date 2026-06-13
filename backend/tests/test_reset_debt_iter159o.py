"""Iter-159p — Reset debt: SAFE behaviour (debts + spend only,
keeps balance + topups)."""
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
async def test_reset_debt_keeps_balance_and_topups():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        email = f"reset2-{os.urandom(3).hex()}@test.com"
        r = await client.post("/api/auth/register",
                              json={"name": "T", "email": email,
                                    "password": "pass1234"})
        token = r.json()["access_token"]
        uid = r.json()["id"]
        h = {"Authorization": f"Bearer {token}"}

        c = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = c[os.environ["DB_NAME"]]

        cp_id = str(uuid.uuid4())
        await db.counterparties.insert_one({
            "id": cp_id, "user_id": uid, "kind": "ad_account",
            "name": "Safe", "name_lower": "safe",
            "ad_provider": "meta", "balance": 1500.0,
        })
        # 2 spend rows + 1 topup row
        for amt in (100, 200):
            await db.ad_account_ledger.insert_one({
                "id": str(uuid.uuid4()), "user_id": uid,
                "counterparty_id": cp_id, "type": "spend",
                "amount": amt, "date": "2026-06-12",
                "created_at": datetime.now(timezone.utc).isoformat(),
            })
        topup_id = str(uuid.uuid4())
        await db.ad_account_ledger.insert_one({
            "id": topup_id, "user_id": uid,
            "counterparty_id": cp_id, "type": "topup",
            "amount": 2000, "date": "2026-06-10",
            "created_at": datetime.now(timezone.utc).isoformat(),
        })
        # 1 liability
        await db.liabilities.insert_one({
            "id": str(uuid.uuid4()), "user_id": uid,
            "kind": "ad_account", "counterparty_id": cp_id,
            "expected_amount": 500, "paid_amount": 0,
            "status": "unpaid", "description": "x",
            "auto_generated": True, "due_date": "2026-06-12",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })

        r = await client.post(f"/api/ad-accounts/{cp_id}/reset-debt",
                              headers=h)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["liabilities_deleted"] == 1
        assert body["ledger_rows_deleted"] == 2   # only spends
        assert body["balance_preserved"] == 1500.0

        # Verify state
        liabs = await db.liabilities.count_documents(
            {"user_id": uid, "counterparty_id": cp_id})
        spend_rows = await db.ad_account_ledger.count_documents(
            {"user_id": uid, "counterparty_id": cp_id, "type": "spend"})
        topup_rows = await db.ad_account_ledger.count_documents(
            {"user_id": uid, "counterparty_id": cp_id, "type": "topup"})
        cp = await db.counterparties.find_one({"id": cp_id})

        assert liabs == 0          # debts wiped
        assert spend_rows == 0     # spend wiped
        assert topup_rows == 1     # topup PRESERVED ✓
        assert cp["balance"] == 1500.0  # balance PRESERVED ✓

        # Cleanup
        await db.counterparties.delete_many({"user_id": uid})
        await db.ad_account_ledger.delete_many({"user_id": uid})

