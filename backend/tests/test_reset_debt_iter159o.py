"""Iter-159o — Reset debt endpoint: nuke liabilities + ledger + balance."""
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
async def test_reset_debt_wipes_everything_for_account():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        email = f"reset-{os.urandom(3).hex()}@test.com"
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
            "name": "ToWipe", "name_lower": "towipe",
            "ad_provider": "meta", "balance": 999.0,
        })
        # seed 3 ledger rows + 2 liabilities
        for amt in (100, 200, 300):
            await db.ad_account_ledger.insert_one({
                "id": str(uuid.uuid4()), "user_id": uid,
                "counterparty_id": cp_id, "type": "spend",
                "amount": amt, "date": "2026-06-12",
                "created_at": datetime.now(timezone.utc).isoformat(),
            })
        for amt in (500, 800):
            await db.liabilities.insert_one({
                "id": str(uuid.uuid4()), "user_id": uid,
                "kind": "ad_account", "counterparty_id": cp_id,
                "expected_amount": amt, "paid_amount": 0,
                "status": "unpaid", "description": "x",
                "auto_generated": True, "due_date": "2026-06-12",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            })

        r = await client.post(f"/api/ad-accounts/{cp_id}/reset-debt",
                              headers=h)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["liabilities_deleted"] == 2
        assert body["ledger_rows_deleted"] == 3

        # Confirm wiped
        liabs = await db.liabilities.count_documents(
            {"user_id": uid, "counterparty_id": cp_id})
        ledger = await db.ad_account_ledger.count_documents(
            {"user_id": uid, "counterparty_id": cp_id})
        cp = await db.counterparties.find_one({"id": cp_id})
        assert liabs == 0
        assert ledger == 0
        assert cp["balance"] == 0.0

        # Cleanup
        await db.counterparties.delete_many({"user_id": uid})
