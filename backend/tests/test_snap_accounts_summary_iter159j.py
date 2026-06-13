"""Iter-159j — Per-Snapchat-account dashboard summary."""
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
async def test_snapchat_accounts_summary_splits_by_account():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        email = f"snap-it-{os.urandom(3).hex()}@test.com"
        r = await client.post("/api/auth/register",
                              json={"name": "T", "email": email,
                                    "password": "pass1234"})
        token = r.json()["access_token"]
        uid = r.json()["id"]
        h = {"Authorization": f"Bearer {token}"}

        # Empty user: no accounts → empty rows
        r = await client.get("/api/dashboard/snapchat-accounts-summary",
                              headers=h)
        assert r.status_code == 200
        assert r.json()["accounts"] == []

        # Seed: 2 accounts, spend 1500 + 500 = 2000, pixel 20 orders 4000 SAR
        c = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = c[os.environ["DB_NAME"]]
        today_d = datetime.now(timezone.utc).date()
        today = today_d.isoformat()
        for name, spend in [("Snap A", 1500), ("Snap B", 500)]:
            cp_id = str(uuid.uuid4())
            await db.counterparties.insert_one({
                "id": cp_id, "user_id": uid, "kind": "ad_account",
                "name": name, "name_lower": name.lower(),
                "ad_provider": "snapchat",
            })
            await db.ad_account_ledger.insert_one({
                "id": str(uuid.uuid4()), "user_id": uid,
                "counterparty_id": cp_id, "type": "spend",
                "amount": spend, "date": today,
                "created_at": datetime.now(timezone.utc).isoformat(),
            })
        await db.snapchat_daily_stats.insert_one({
            "user_id": uid, "date": today,
            "purchases": 20, "revenue": 4000.0,
        })

        # Call the endpoint
        r = await client.get("/api/dashboard/snapchat-accounts-summary",
                              headers=h)
        assert r.status_code == 200
        body = r.json()
        assert len(body["accounts"]) == 2
        by_name = {a["name"]: a for a in body["accounts"]}

        a, b = by_name["Snap A"], by_name["Snap B"]
        # Spend split is accurate
        assert a["spend"] == 1500.0
        assert b["spend"] == 500.0
        assert a["spend_share_pct"] == 75.0
        assert b["spend_share_pct"] == 25.0
        # Orders prorated
        assert a["orders"] == 15  # 75% of 20
        assert b["orders"] == 5   # 25% of 20
        assert a["revenue"] == 3000.0
        assert b["revenue"] == 1000.0
        # CPO = spend / orders
        assert a["cost_per_order"] == 100.0
        assert b["cost_per_order"] == 100.0

        # Totals match
        assert body["totals"]["spend"] == 2000.0
        assert body["totals"]["orders"] == 20
        assert body["totals"]["revenue"] == 4000.0

        # Cleanup
        await db.counterparties.delete_many({"user_id": uid})
        await db.ad_account_ledger.delete_many({"user_id": uid})
        await db.snapchat_daily_stats.delete_many({"user_id": uid})
