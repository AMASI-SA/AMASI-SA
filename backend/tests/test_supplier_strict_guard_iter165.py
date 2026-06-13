"""Iter-165 — Strict counterparty_id guard for supplier liabilities.

Permanent protection against the "orphan supplier" recurrence the
merchant flagged on Feb 2026. Creating a supplier liability without a
registered counterparty_id is now rejected at the API boundary.
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
async def test_supplier_liability_rejects_missing_counterparty_id():
    async with AsyncClient(transport=ASGITransport(app=app),
                            base_url="http://test") as client:
        email = f"sup165-{os.urandom(3).hex()}@test.com"
        r = await client.post("/api/auth/register",
                              json={"name": "S", "email": email,
                                    "password": "pass1234"})
        token = r.json()["access_token"]
        uid = r.json()["id"]
        h = {"Authorization": f"Bearer {token}"}

        c = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = c[os.environ["DB_NAME"]]

        try:
            # 1) supplier_name only — must be rejected.
            r = await client.post("/api/liabilities", json={
                "kind": "supplier",
                "expected_amount": 100,
                "due_date": "2026-12-31",
                "supplier_name": "اسم بدون مرجع",
            }, headers=h)
            assert r.status_code == 422, (
                f"Expected 422, got {r.status_code}: {r.text}")
            assert "counterparty_id" in r.text

            # 2) With valid counterparty_id — must succeed.
            cp_id = str(uuid.uuid4())
            await db.counterparties.insert_one({
                "id": cp_id, "user_id": uid, "kind": "supplier",
                "name": "مورد رسمي", "name_lower": "مورد رسمي",
            })
            r = await client.post("/api/liabilities", json={
                "kind": "supplier",
                "expected_amount": 100,
                "due_date": "2026-12-31",
                "counterparty_id": cp_id,
            }, headers=h)
            assert r.status_code == 200, (
                f"Expected 200, got {r.status_code}: {r.text}")
            d = r.json()
            assert d["counterparty_id"] == cp_id
            assert d["supplier_name"] == "مورد رسمي"
        finally:
            await db.counterparties.delete_many({"user_id": uid})
            await db.liabilities.delete_many({"user_id": uid})
