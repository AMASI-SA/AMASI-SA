"""Iter-160 — Dashboard SSOT (Single Source of Truth).

Per user Message #737 directive: ALL ad-spend figures on the dashboard
(Snapchat / Meta / TikTok summaries) must be aggregated STRICTLY from
`ad_account_ledger.type=spend`. Old paths reading `daily_costs`,
`snapchat_ads_daily`, `meta_ads_daily`, or `tiktok_ads_daily` for the
spend figure are forbidden — they cause double-counting between
manual entries and API syncs.

This test verifies:
  • Seeding `daily_costs.snapchat_ads = 99999` but no ledger rows
    yields 0.0 spend on the snapchat-summary endpoint.
  • Seeding `ad_account_ledger.type=spend` rows DOES surface as spend.
  • Same for meta_ads_daily.spend vs ledger.
"""
import os
import uuid
from datetime import datetime, timezone

import pytest
from dotenv import load_dotenv
from httpx import ASGITransport, AsyncClient

load_dotenv("/app/backend/.env")
import sys
sys.path.insert(0, "/app/backend")
from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402
from server import app  # noqa: E402


@pytest.mark.asyncio
async def test_dashboard_spend_only_from_ledger():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport,
                            base_url="http://test") as client:
        email = f"ssot-{os.urandom(3).hex()}@test.com"
        r = await client.post("/api/auth/register",
                              json={"name": "T", "email": email,
                                    "password": "pass1234"})
        token = r.json()["access_token"]
        uid = r.json()["id"]
        h = {"Authorization": f"Bearer {token}"}

        c = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = c[os.environ["DB_NAME"]]
        now = datetime.now(timezone.utc).isoformat()
        today = now[:10]

        # Seed BIG numbers in legacy paths — these should be IGNORED.
        await db.daily_costs.insert_one({
            "id": str(uuid.uuid4()), "user_id": uid, "date": today,
            "snapchat_ads": 99999.0, "snapchat_ads_2": 88888.0,
            "tiktok_ads": 77777.0,
            "created_at": now, "updated_at": now,
        })
        await db.meta_ads_daily.insert_one({
            "id": str(uuid.uuid4()), "user_id": uid, "date": today,
            "spend": 55555.0, "purchases": 5, "purchase_value": 1000,
        })

        # 1) BEFORE any ledger rows: dashboard spend must be 0.0
        r1 = await client.get("/api/dashboard/snapchat-summary", headers=h)
        assert r1.status_code == 200
        snap1 = r1.json()
        assert snap1["today"]["spend"] == 0.0, (
            f"Legacy daily_costs leaked: got {snap1['today']['spend']}"
        )

        r2 = await client.get("/api/dashboard/meta-summary", headers=h)
        assert r2.status_code == 200
        meta1 = r2.json()
        assert meta1["today"]["spend"] == 0.0, (
            f"Legacy meta_ads_daily leaked: got {meta1['today']['spend']}"
        )

        r3 = await client.get("/api/dashboard/tiktok-summary", headers=h)
        assert r3.status_code == 200
        tt1 = r3.json()
        assert tt1["today"]["spend"] == 0.0, (
            f"Legacy daily_costs.tiktok_ads leaked: got {tt1['today']['spend']}"
        )

        # 2) Seed counterparties + ledger rows. Spend MUST appear now.
        for provider, ledger_amt in [("snapchat", 250.0),
                                       ("meta", 100.0),
                                       ("tiktok", 75.0)]:
            cp_id = str(uuid.uuid4())
            await db.counterparties.insert_one({
                "id": cp_id, "user_id": uid, "kind": "ad_account",
                "name": f"Test {provider}", "name_lower": f"test {provider}",
                "ad_provider": provider, "balance": 0,
            })
            await db.ad_account_ledger.insert_one({
                "id": str(uuid.uuid4()), "user_id": uid,
                "counterparty_id": cp_id, "type": "spend",
                "amount": ledger_amt, "date": today,
                "created_at": now,
            })

        r4 = await client.get("/api/dashboard/snapchat-summary", headers=h)
        assert r4.json()["today"]["spend"] == 250.0

        r5 = await client.get("/api/dashboard/meta-summary", headers=h)
        assert r5.json()["today"]["spend"] == 100.0

        r6 = await client.get("/api/dashboard/tiktok-summary", headers=h)
        assert r6.json()["today"]["spend"] == 75.0

        # cleanup
        await db.counterparties.delete_many({"user_id": uid})
        await db.ad_account_ledger.delete_many({"user_id": uid})
        await db.daily_costs.delete_many({"user_id": uid})
        await db.meta_ads_daily.delete_many({"user_id": uid})
