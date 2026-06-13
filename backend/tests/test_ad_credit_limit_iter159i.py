"""Iter-159i — Per-account credit limit + alert threshold."""
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
async def test_credit_limit_drives_per_account_alert():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Register
        email = f"credit-{os.urandom(3).hex()}@test.com"
        r = await client.post("/api/auth/register",
                              json={"name": "T", "email": email,
                                    "password": "pass1234"})
        token = r.json()["access_token"]
        uid = r.json()["id"]
        h = {"Authorization": f"Bearer {token}"}

        # Create ad account
        r = await client.post("/api/ad-accounts",
                              json={"name": "Snap", "ad_provider": "snapchat"},
                              headers=h)
        assert r.status_code == 200, r.text
        cp_id = r.json()["id"]
        assert r.json()["credit_limit"] is None

        # Set limit = 1000, threshold = 60%
        r = await client.put(f"/api/ad-accounts/{cp_id}/credit-limit",
                              json={"credit_limit": 1000,
                                    "alert_threshold_pct": 60},
                              headers=h)
        assert r.status_code == 200, r.text
        assert r.json()["credit_limit"] == 1000.0
        assert r.json()["alert_threshold_pct"] == 60.0

        # Seed 500 SAR debt → 50% usage → BELOW threshold → no alert
        c = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = c[os.environ["DB_NAME"]]
        liab_id = str(uuid.uuid4())
        await db.liabilities.insert_one({
            "id": liab_id, "user_id": uid, "kind": "ad_account",
            "counterparty_id": cp_id, "expected_amount": 500.0,
            "paid_amount": 0.0, "status": "unpaid",
            "description": "test", "auto_generated": True,
            "source": "ad_account_cron",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })
        await client.post("/api/alerts/refresh", headers=h)
        r = await client.get("/api/alerts", headers=h)
        debt_alerts = [a for a in r.json()["alerts"]
                       if a["alert_type"] == "high_ad_debt"]
        assert len(debt_alerts) == 0, "alert raised below threshold"

        # Bump debt to 700 → 70% → triggers warning
        await db.liabilities.update_one(
            {"id": liab_id}, {"$set": {"expected_amount": 700.0}})
        await client.post("/api/alerts/refresh", headers=h)
        r = await client.get("/api/alerts", headers=h)
        debt_alerts = [a for a in r.json()["alerts"]
                       if a["alert_type"] == "high_ad_debt"]
        assert len(debt_alerts) == 1
        assert debt_alerts[0]["severity"] == "warning"
        assert debt_alerts[0]["metadata"]["mode"] == "per_account_limit"
        assert debt_alerts[0]["metadata"]["usage_pct"] == 70.0

        # Bump debt to 980 → 98% → critical
        await db.liabilities.update_one(
            {"id": liab_id}, {"$set": {"expected_amount": 980.0}})
        await client.post("/api/alerts/refresh", headers=h)
        r = await client.get("/api/alerts", headers=h)
        debt_alerts = [a for a in r.json()["alerts"]
                       if a["alert_type"] == "high_ad_debt"]
        assert debt_alerts[0]["severity"] == "critical"
        assert "النفاذ" in debt_alerts[0]["title"]

        # Validation: negative limit / pct > 100 / empty body all rejected
        r = await client.put(f"/api/ad-accounts/{cp_id}/credit-limit",
                              json={"credit_limit": -100}, headers=h)
        assert r.status_code == 422
        r = await client.put(f"/api/ad-accounts/{cp_id}/credit-limit",
                              json={"alert_threshold_pct": 150}, headers=h)
        assert r.status_code == 422
        r = await client.put(f"/api/ad-accounts/{cp_id}/credit-limit",
                              json={}, headers=h)
        assert r.status_code == 400

        # Cleanup
        await db.liabilities.delete_many({"user_id": uid})
        await db.counterparties.delete_many({"user_id": uid})
        await db.settlement_alerts.delete_many({"user_id": uid})


@pytest.mark.skip(reason="merged into the main test above to avoid "
                          "pytest-asyncio event-loop teardown collision")
@pytest.mark.asyncio
async def test_credit_limit_validation():
    pass
