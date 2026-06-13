"""Iter-159h — Smart Settlement Alerts integration test."""
import os
import uuid
from datetime import datetime, timezone, timedelta

import pytest
from httpx import AsyncClient, ASGITransport
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")
import sys
sys.path.insert(0, "/app/backend")
from server import app  # noqa: E402
from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402


@pytest.mark.asyncio
async def test_alerts_full_lifecycle():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Register
        email = f"alerts-it-{os.urandom(3).hex()}@test.com"
        r = await client.post("/api/auth/register",
                              json={"name": "T", "email": email,
                                    "password": "pass1234"})
        token = r.json()["access_token"]
        uid = r.json()["id"]
        h = {"Authorization": f"Bearer {token}"}

        # Seed: overdue tabby order + amount-diff order
        c = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = c[os.environ["DB_NAME"]]
        now = datetime.now(timezone.utc)
        await db.orders.insert_one({
            "id": str(uuid.uuid4()), "user_id": uid,
            "order_number": "OVR-1", "payment_method": "tabby",
            "amount": 100,
            "received_at": (now - timedelta(days=10)).isoformat(),
            "created_at": (now - timedelta(days=10)).isoformat(),
        })
        await db.orders.insert_one({
            "id": str(uuid.uuid4()), "user_id": uid,
            "order_number": "DIF-1", "payment_method": "tabby",
            "amount": 100, "actual_amount": 70,
            "actual_payment_method": "tabby",
            "received_at": now.isoformat(),
            "created_at": now.isoformat(),
        })

        # Refresh — should produce ≥2 alerts
        r = await client.post("/api/alerts/refresh", headers=h)
        assert r.status_code == 200
        body = r.json()
        assert body["created"]["overdue_bnpl"] >= 1
        assert body["created"]["amount_diff"] >= 1
        assert body["unread"] >= 2

        # List
        r = await client.get("/api/alerts", headers=h)
        assert r.status_code == 200
        alerts = r.json()["alerts"]
        assert len(alerts) >= 2

        # Idempotency — second refresh shouldn't duplicate
        r = await client.post("/api/alerts/refresh", headers=h)
        r2 = await client.get("/api/alerts", headers=h)
        assert len(r2.json()["alerts"]) == len(alerts), \
            "alerts duplicated on re-refresh"

        # Mark first as read
        aid = alerts[0]["id"]
        r = await client.post(f"/api/alerts/{aid}/read", headers=h)
        assert r.status_code == 200
        r = await client.get("/api/alerts/unread-count", headers=h)
        assert r.json()["count"] == len(alerts) - 1

        # Snooze second
        aid2 = alerts[1]["id"]
        r = await client.post(f"/api/alerts/{aid2}/snooze",
                              json={"hours": 24}, headers=h)
        assert r.status_code == 200

        # Settings — update + persist
        r = await client.patch("/api/alerts/settings",
                                json={"bnpl_overdue_days": 14}, headers=h)
        assert r.status_code == 200
        r = await client.get("/api/alerts/settings", headers=h)
        assert r.json()["bnpl_overdue_days"] == 14

        # Cleanup
        await db.orders.delete_many({"user_id": uid})
        await db.settlement_alerts.delete_many({"user_id": uid})
        await db.alert_settings.delete_one({"user_id": uid})
