"""Iter-161 Phase 4 close-out — Reconciliation report.

Verifies side-by-side comparison computes legacy vs ledger correctly
and the safe_to_disable_legacy flag is true only when all match.
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
async def test_reconciliation_endpoint():
    async with AsyncClient(transport=ASGITransport(app=app),
                            base_url="http://test") as client:
        email = f"rec-{os.urandom(3).hex()}@test.com"
        r = await client.post("/api/auth/register",
                              json={"name": "Rec", "email": email,
                                    "password": "pass1234"})
        token = r.json()["access_token"]
        uid = r.json()["id"]
        h = {"Authorization": f"Bearer {token}"}

        c = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = c[os.environ["DB_NAME"]]

        # Empty user → should be 100% match with 0 entities
        r = await client.get(
            "/api/accounting/migration/reconciliation", headers=h)
        assert r.status_code == 200
        d = r.json()
        assert d["summary"]["safe_to_disable_legacy"] is True
        assert d["summary"]["total_entities"] == 0
        assert d["summary"]["match_percentage"] == 100.0

        # Seed: 1 employee + supplier + bank, both in legacy AND ledger
        emp_id = str(uuid.uuid4())
        sup_id = str(uuid.uuid4())
        bank_id = str(uuid.uuid4())
        await db.operating_salaries.insert_one({
            "id": emp_id, "user_id": uid, "name": "أحمد",
            "category": "employee", "monthly_amount": 3000,
        })
        await db.counterparties.insert_one({
            "id": sup_id, "user_id": uid, "kind": "supplier",
            "name": "Sup1", "name_lower": "sup1",
        })
        await db.accounts.insert_one({
            "id": bank_id, "user_id": uid, "account_type": "bank",
            "name": "Rajhi", "balance": 5000,  # legacy says 5000
        })
        # Legacy: 500 advance + 1000 supplier liability
        now = datetime.now(timezone.utc).isoformat()
        await db.liabilities.insert_one({
            "id": str(uuid.uuid4()), "user_id": uid,
            "kind": "salary_advance", "employee_salary_id": emp_id,
            "expected_amount": 500, "paid_amount": 500,
            "status": "paid", "advance_status": "open",
            "created_at": now,
        })
        await db.liabilities.insert_one({
            "id": str(uuid.uuid4()), "user_id": uid,
            "kind": "supplier", "counterparty_id": sup_id,
            "expected_amount": 1000, "paid_amount": 0,
            "status": "unpaid", "created_at": now,
        })

        # Without ledger entries: reconciliation should show mismatches
        r = await client.get(
            "/api/accounting/migration/reconciliation", headers=h)
        d = r.json()
        assert d["summary"]["safe_to_disable_legacy"] is False
        assert d["summary"]["mismatched"] >= 2

        # Verify the diff fields exist for emp + supplier + bank
        emp_row = d["employees"][0]
        assert emp_row["advance"]["legacy"] == 500.0
        assert emp_row["advance"]["ledger"] == 0.0
        assert emp_row["advance"]["delta"] == -500.0
        assert emp_row["advance"]["match"] is False

        sup_row = d["suppliers"][0]
        assert sup_row["payable"]["legacy"] == 1000.0
        assert sup_row["payable"]["delta"] == -1000.0

        bank_row = d["banks"][0]
        assert bank_row["balance"]["legacy"] == 5000.0
        assert bank_row["balance"]["delta"] == -5000.0

        # Cleanup
        await db.operating_salaries.delete_many({"user_id": uid})
        await db.counterparties.delete_many({"user_id": uid})
        await db.accounts.delete_many({"user_id": uid})
        await db.liabilities.delete_many({"user_id": uid})
        await db.general_ledger.delete_many({"user_id": uid})
        await db.accounting_audit_log.delete_many({"user_id": uid})
