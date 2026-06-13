"""Iter-167 — Payment platforms & couriers participate in the migration.

Validates:
  • `_legacy_payment_platform_balances` reads each platform's
    current_balance (or BNPL SSOT for Tabby/Tamara) — not 0.
  • `_legacy_courier_balances` aggregates open shipping liabilities.
  • Migration plans opening_balance entries for both new entity types.
  • After migration, ledger balances match legacy 100%.
"""
import os
import sys
import uuid
from datetime import datetime, timezone

import pytest
from dotenv import load_dotenv
from httpx import ASGITransport, AsyncClient

load_dotenv("/app/backend/.env")
sys.path.insert(0, "/app/backend")
from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402
from server import app  # noqa: E402
from migration_routes import (  # noqa: E402
    _legacy_payment_platform_balances,
    _legacy_courier_balances,
)


@pytest.mark.asyncio
async def test_payment_platforms_legacy_reads_current_balance():
    c = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = c[os.environ["DB_NAME"]]
    uid = f"u-{uuid.uuid4().hex[:8]}"

    try:
        await db.accounts.insert_many([
            {"id": "p-salla", "user_id": uid,
             "account_type": "payment_platform",
             "name": "سلة", "current_balance": 211680.67,
             "opening_balance": 0, "expected_orders_balance": 251680.67},
            {"id": "p-cod", "user_id": uid,
             "account_type": "payment_platform",
             "name": "COD", "current_balance": 8540.26,
             "opening_balance": 0},
        ])
        rows = await _legacy_payment_platform_balances(db, uid)
        by_name = {r["name"]: r for r in rows}
        assert by_name["سلة"]["balance"] == 211680.67
        assert by_name["COD"]["balance"] == 8540.26
        # Diagnostic fields propagate
        assert by_name["سلة"]["_balance_source"] == "current_balance"
        assert by_name["سلة"]["_expected_orders_balance"] == 251680.67
    finally:
        await db.accounts.delete_many({"user_id": uid})


@pytest.mark.asyncio
async def test_courier_legacy_aggregates_open_shipping_liabilities():
    c = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = c[os.environ["DB_NAME"]]
    uid = f"u-{uuid.uuid4().hex[:8]}"

    try:
        cp_id = str(uuid.uuid4())
        await db.counterparties.insert_one({
            "id": cp_id, "user_id": uid, "kind": "courier",
            "name": "SMSA", "name_lower": "smsa",
        })
        # 2 open + 1 paid + 1 pre-accounting (excluded)
        await db.liabilities.insert_many([
            {"id": str(uuid.uuid4()), "user_id": uid, "kind": "shipping",
             "counterparty_id": cp_id, "expected_amount": 500,
             "paid_amount": 100, "status": "partial"},
            {"id": str(uuid.uuid4()), "user_id": uid, "kind": "shipping",
             "counterparty_id": cp_id, "expected_amount": 300,
             "paid_amount": 0, "status": "unpaid"},
            {"id": str(uuid.uuid4()), "user_id": uid, "kind": "shipping",
             "counterparty_id": cp_id, "expected_amount": 200,
             "paid_amount": 200, "status": "paid"},
            {"id": str(uuid.uuid4()), "user_id": uid, "kind": "shipping",
             "counterparty_id": cp_id, "expected_amount": 999,
             "paid_amount": 0, "status": "unpaid",
             "is_pre_accounting": True},
        ])
        rows = await _legacy_courier_balances(db, uid)
        r = next(x for x in rows if x["courier_id"] == cp_id)
        # 400 (500-100) + 300 = 700; paid and pre-accounting excluded
        assert r["payable"] == 700.0
    finally:
        await db.counterparties.delete_many({"user_id": uid})
        await db.liabilities.delete_many({"user_id": uid})


@pytest.mark.asyncio
async def test_migration_executes_for_platforms_and_couriers():
    async with AsyncClient(transport=ASGITransport(app=app),
                            base_url="http://test") as client:
        email = f"plat167-{os.urandom(3).hex()}@test.com"
        r = await client.post("/api/auth/register",
                              json={"name": "P", "email": email,
                                    "password": "pass1234"})
        token = r.json()["access_token"]
        uid = r.json()["id"]
        h = {"Authorization": f"Bearer {token}"}

        c = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = c[os.environ["DB_NAME"]]

        try:
            # Seed a payment platform + a courier with liability
            await db.accounts.insert_one({
                "id": "plat-A", "user_id": uid,
                "account_type": "payment_platform",
                "name": "Salla", "current_balance": 50000,
            })
            cour_id = str(uuid.uuid4())
            await db.counterparties.insert_one({
                "id": cour_id, "user_id": uid, "kind": "courier",
                "name": "SMSA", "name_lower": "smsa",
            })
            await db.liabilities.insert_one({
                "id": str(uuid.uuid4()), "user_id": uid,
                "kind": "shipping", "counterparty_id": cour_id,
                "expected_amount": 1500, "paid_amount": 0,
                "status": "unpaid",
            })

            # Reconciliation before migration
            r = await client.get(
                "/api/accounting/migration/reconciliation", headers=h)
            d = r.json()
            plats = {p["name"]: p for p in d["payment_platforms"]}
            assert plats["Salla"]["balance"]["legacy"] == 50000
            assert plats["Salla"]["balance"]["projected"] == 50000
            cours = {x["name"]: x for x in d["couriers"]}
            assert cours["SMSA"]["payable"]["legacy"] == 1500
            assert d["summary"]["projected_match_percentage"] == 100.0

            # Execute migration
            r = await client.post(
                "/api/accounting/migration/run", headers=h,
                json={"cutoff_date": "2026-02-12", "dry_run": False})
            assert r.json()["status"] == "applied"

            # Post-migration: ledger matches legacy
            r = await client.get(
                "/api/accounting/migration/reconciliation", headers=h)
            d = r.json()
            plats = {p["name"]: p for p in d["payment_platforms"]}
            assert plats["Salla"]["balance"]["ledger"] == 50000
            assert plats["Salla"]["balance"]["match"] is True
            cours = {x["name"]: x for x in d["couriers"]}
            assert cours["SMSA"]["payable"]["ledger"] == 1500
            assert d["summary"]["safe_to_disable_legacy"] is True
        finally:
            await db.accounts.delete_many({"user_id": uid})
            await db.counterparties.delete_many({"user_id": uid})
            await db.liabilities.delete_many({"user_id": uid})
            await db.general_ledger.delete_many({"user_id": uid})
            await db.migration_cutoffs.delete_many({"user_id": uid})
            await db.accounting_audit_log.delete_many({"user_id": uid})
