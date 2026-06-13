"""Iter-161 Phase 3 — Couriers + Migration verify + Snapchat dashboard fix.

All-in-one test (avoid event-loop-closed motor issue).
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
async def test_phase3_courier_verify_dashboard():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport,
                            base_url="http://test") as client:
        email = f"p3-{os.urandom(3).hex()}@test.com"
        r = await client.post("/api/auth/register",
                              json={"name": "T3", "email": email,
                                    "password": "pass1234"})
        token = r.json()["access_token"]
        uid = r.json()["id"]
        h = {"Authorization": f"Bearer {token}"}

        c = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = c[os.environ["DB_NAME"]]

        # Seed bank + courier
        bank_id = str(uuid.uuid4())
        courier_id = str(uuid.uuid4())
        await db.accounts.insert_one({
            "id": bank_id, "user_id": uid, "account_type": "bank",
            "name": "الراجحي", "balance": 0,
        })
        await db.counterparties.insert_one({
            "id": courier_id, "user_id": uid, "kind": "courier",
            "name": "SMSA", "name_lower": "smsa",
        })

        # ── 1) Courier charge 5000 ───────────────────────────────
        r = await client.post(
            f"/api/accounting/couriers/{courier_id}/charge", headers=h,
            json={"amount": 5000, "expense_category": "shipping",
                   "invoice_no": "SMSA-001"})
        assert r.status_code == 200
        assert r.json()["balance"]["outstanding_debt"] == 5000.0

        # ── 2) Courier pay 3000 ──────────────────────────────────
        r = await client.post(
            f"/api/accounting/couriers/{courier_id}/pay", headers=h,
            json={"amount": 3000, "paid_from_account_id": bank_id})
        assert r.status_code == 200
        assert r.json()["balance"]["outstanding_debt"] == 2000.0

        # ── 3) COD deposit ───────────────────────────────────────
        # First simulate a pending COD via direct ledger entry
        # (in real life this would come from an order delivery webhook)
        await db.general_ledger.insert_one({
            "id": str(uuid.uuid4()), "user_id": uid,
            "entry_no": 9999, "entity_type": "courier",
            "entity_id": courier_id, "sub_account": "cod_receivable",
            "entry_type": "receivable_grant", "amount": 8000,
            "side": "debit", "currency": "SAR", "status": "posted",
            "reverses_entry_id": None, "reversed_by_entry_id": None,
            "reason_code": None, "notes": "Pending COD batch",
            "metadata": {}, "txn_group_id": str(uuid.uuid4()),
            "posted_at": datetime.now(timezone.utc).isoformat(),
            "posted_by": uid,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })
        # COD balance pre-deposit = 8000
        b = (await client.get(
            f"/api/ledger/balance?entity_type=courier&entity_id={courier_id}&sub_account=cod_receivable",
            headers=h)).json()
        assert b["net_balance"] == 8000.0

        r = await client.post(
            f"/api/accounting/couriers/{courier_id}/cod-deposit", headers=h,
            json={"amount": 5000, "paid_from_account_id": bank_id})
        assert r.status_code == 200
        assert r.json()["cod_balance"]["net_balance"] == 3000.0

        # ── 4) /migration/verify ─────────────────────────────────
        r = await client.get("/api/accounting/migration/verify", headers=h)
        assert r.status_code == 200
        v = r.json()
        assert "counts" in v
        assert "legacy_totals" in v
        assert "opening_totals" in v
        assert "match" in v
        # Fresh user with no legacy data → all match True
        assert v["all_match"] is True

        # ── Cleanup ──────────────────────────────────────────────
        await db.accounts.delete_many({"user_id": uid})
        await db.counterparties.delete_many({"user_id": uid})
        await db.general_ledger.delete_many({"user_id": uid})
        await db.accounting_audit_log.delete_many({"user_id": uid})
        await db.expense_categories.delete_many({"user_id": uid})
