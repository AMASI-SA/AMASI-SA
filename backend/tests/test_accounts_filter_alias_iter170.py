"""Iter-170 — Account list filter accepts both `type` and `account_type`.

Production bug: «خصم من حساب» dropdown showed every bank duplicated
(الإنماء/الأهلي/الراجحي appeared twice each). Root cause: the frontend
called `/api/accounts?type=bank` but the endpoint only recognised
`?account_type=bank`. The unmatched filter was silently ignored, both
calls returned ALL 8 accounts, then the frontend merged them.

Fix: accept both query params; alias `type` → `account_type`.
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
async def test_accounts_filter_accepts_short_alias_type():
    async with AsyncClient(transport=ASGITransport(app=app),
                            base_url="http://test") as client:
        email = f"acc170-{os.urandom(3).hex()}@test.com"
        r = await client.post("/api/auth/register",
                              json={"name": "A", "email": email,
                                    "password": "pass1234"})
        token = r.json()["access_token"]
        uid = r.json()["id"]
        h = {"Authorization": f"Bearer {token}"}

        c = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = c[os.environ["DB_NAME"]]

        try:
            # Seed: 2 banks + 3 payment platforms.
            await db.accounts.insert_many([
                {"id": str(uuid.uuid4()), "user_id": uid,
                 "account_type": "bank", "name": "Bank A",
                 "current_balance": 1000, "status": "active"},
                {"id": str(uuid.uuid4()), "user_id": uid,
                 "account_type": "bank", "name": "Bank B",
                 "current_balance": 2000, "status": "active"},
                {"id": str(uuid.uuid4()), "user_id": uid,
                 "account_type": "payment_platform", "name": "Salla",
                 "current_balance": 500, "status": "active"},
                {"id": str(uuid.uuid4()), "user_id": uid,
                 "account_type": "payment_platform", "name": "Tabby",
                 "current_balance": 100, "status": "active"},
                {"id": str(uuid.uuid4()), "user_id": uid,
                 "account_type": "payment_platform", "name": "COD",
                 "current_balance": 50, "status": "active"},
            ])

            # 1) Long form: ?account_type=bank → 2 banks
            r = await client.get(
                "/api/accounts?account_type=bank", headers=h)
            assert r.status_code == 200
            banks = r.json()
            assert len(banks) == 2
            assert all(a["account_type"] == "bank" for a in banks)

            # 2) Short alias: ?type=bank → same 2 banks (Iter-170 fix)
            r = await client.get("/api/accounts?type=bank", headers=h)
            assert r.status_code == 200
            banks_alias = r.json()
            assert len(banks_alias) == 2, (
                f"Expected 2 (the bug returned 5 because filter was "
                f"ignored), got {len(banks_alias)}: "
                f"{[a['name'] for a in banks_alias]}"
            )
            assert all(a["account_type"] == "bank" for a in banks_alias)

            # 3) Payment platforms: ?type=payment_platform → 3
            r = await client.get(
                "/api/accounts?type=payment_platform", headers=h)
            assert len(r.json()) == 3

            # 4) Both params set → account_type wins (long form preferred)
            r = await client.get(
                "/api/accounts?account_type=payment_platform&type=bank",
                headers=h)
            assert all(a["account_type"] == "payment_platform"
                       for a in r.json())
        finally:
            await db.accounts.delete_many({"user_id": uid})
