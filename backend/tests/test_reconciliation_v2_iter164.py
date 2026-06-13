"""Iter-164 — Reconciliation Report clarity tests.

Validates:
  • `migration_status` is returned and reflects the `migration_cutoffs`
    collection state.
  • `projected_after_migration` cell equals legacy for each entity.
  • `projected_match_percentage` is 100% even when actual ledger is empty.
  • `safe_to_disable_legacy` is FALSE until migration is actually
    executed (regardless of legacy match).
  • Orphan supplier liabilities are surfaced when present.
"""
import os
import sys
import uuid
from datetime import datetime, timezone, timedelta

import pytest
from dotenv import load_dotenv
from httpx import ASGITransport, AsyncClient

load_dotenv("/app/backend/.env")
sys.path.insert(0, "/app/backend")
from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402
from server import app  # noqa: E402


@pytest.mark.asyncio
async def test_pre_migration_report_clarifies_status():
    async with AsyncClient(transport=ASGITransport(app=app),
                            base_url="http://test") as client:
        email = f"recon164-{os.urandom(3).hex()}@test.com"
        r = await client.post("/api/auth/register",
                              json={"name": "R", "email": email,
                                    "password": "pass1234"})
        token = r.json()["access_token"]
        uid = r.json()["id"]
        h = {"Authorization": f"Bearer {token}"}

        c = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = c[os.environ["DB_NAME"]]

        # Seed: 1 employee accruing salary + 1 supplier with debt.
        emp_id = str(uuid.uuid4())
        start = (datetime.now(timezone.utc).date() - timedelta(days=30))
        await db.operating_salaries.insert_one({
            "id": emp_id, "user_id": uid, "name": "أحمد",
            "category": "employee", "status": "active",
            "monthly_amount": 3000, "start_date": start.isoformat(),
        })
        sup_id = str(uuid.uuid4())
        await db.counterparties.insert_one({
            "id": sup_id, "user_id": uid, "kind": "supplier",
            "name": "Sup", "name_lower": "sup",
        })
        await db.liabilities.insert_one({
            "id": str(uuid.uuid4()), "user_id": uid, "kind": "supplier",
            "counterparty_id": sup_id,
            "expected_amount": 1000, "paid_amount": 0,
            "status": "unpaid",
        })

        try:
            r = await client.get(
                "/api/accounting/migration/reconciliation", headers=h)
            d = r.json()

            # 1. migration_status reflects un-executed state.
            assert d["migration_status"]["completed"] is False
            assert d["migration_status"]["cutoff_date"] is None

            # 2. Projected match is 100% even though live match is poor.
            s = d["summary"]
            assert s["projected_match_percentage"] == 100.0
            assert s["projected_matched"] == s["total_entities"]
            assert s["safe_to_disable_legacy"] is False

            # 3. Per-entity projected field equals legacy.
            emp = d["employees"][0]
            assert emp["salary_payable"]["projected"] == \
                emp["salary_payable"]["legacy"]
            assert emp["all_projected_match"] is True

            sup = d["suppliers"][0]
            assert sup["payable"]["projected"] == 1000.0
            assert sup["all_projected_match"] is True

            # 4. will_post_after_migration is non-zero & matches expectation.
            assert s["will_post_after_migration"] >= 1000.0
        finally:
            await db.operating_salaries.delete_many({"user_id": uid})
            await db.counterparties.delete_many({"user_id": uid})
            await db.liabilities.delete_many({"user_id": uid})
            await db.general_ledger.delete_many({"user_id": uid})
            await db.accounting_audit_log.delete_many({"user_id": uid})


@pytest.mark.asyncio
async def test_orphan_supplier_surfaces_in_report():
    """A supplier liability whose `counterparty_id` doesn't match any
    counterparty (and `supplier_name` doesn't match either) must appear
    in `orphan_suppliers`.
    """
    async with AsyncClient(transport=ASGITransport(app=app),
                            base_url="http://test") as client:
        email = f"orph164-{os.urandom(3).hex()}@test.com"
        r = await client.post("/api/auth/register",
                              json={"name": "O", "email": email,
                                    "password": "pass1234"})
        token = r.json()["access_token"]
        uid = r.json()["id"]
        h = {"Authorization": f"Bearer {token}"}

        c = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = c[os.environ["DB_NAME"]]

        await db.liabilities.insert_one({
            "id": str(uuid.uuid4()), "user_id": uid, "kind": "supplier",
            "counterparty_id": None,
            "supplier_name": "مورد بدون ربط",
            "expected_amount": 1239, "paid_amount": 0,
            "status": "unpaid",
            "description": "فاتورة قديمة",
        })

        try:
            r = await client.get(
                "/api/accounting/migration/reconciliation", headers=h)
            d = r.json()
            assert d["summary"]["orphan_supplier_count"] == 1
            assert d["summary"]["orphan_supplier_total"] == 1239.0
            assert d["orphan_suppliers"][0]["supplier_name"] == "مورد بدون ربط"
            assert d["orphan_suppliers"][0]["remaining"] == 1239.0
        finally:
            await db.liabilities.delete_many({"user_id": uid})


@pytest.mark.asyncio
async def test_safe_to_disable_only_after_actual_migration():
    """Even when projected_match=100%, `safe_to_disable_legacy` must
    remain False until the migration is actually executed."""
    async with AsyncClient(transport=ASGITransport(app=app),
                            base_url="http://test") as client:
        email = f"safe164-{os.urandom(3).hex()}@test.com"
        r = await client.post("/api/auth/register",
                              json={"name": "S", "email": email,
                                    "password": "pass1234"})
        token = r.json()["access_token"]
        uid = r.json()["id"]
        h = {"Authorization": f"Bearer {token}"}

        c = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = c[os.environ["DB_NAME"]]

        try:
            # Empty user with no entities → projected = 100%, but
            # safe_to_disable must still be False (migration_cutoffs
            # is not set).
            r = await client.get(
                "/api/accounting/migration/reconciliation", headers=h)
            d = r.json()
            assert d["summary"]["projected_match_percentage"] == 100.0
            # No entities → live match is also 100%, but migration
            # not completed → safe_to_disable_legacy is FALSE.
            assert d["summary"]["safe_to_disable_legacy"] is False

            # Now execute migration → marker is set.
            r = await client.post(
                "/api/accounting/migration/run", headers=h,
                json={"cutoff_date": "2026-02-12", "dry_run": False})
            assert r.status_code == 200

            # Re-fetch report → now safe_to_disable_legacy is True.
            r = await client.get(
                "/api/accounting/migration/reconciliation", headers=h)
            d = r.json()
            assert d["migration_status"]["completed"] is True
            assert d["summary"]["safe_to_disable_legacy"] is True
        finally:
            await db.migration_cutoffs.delete_many({"user_id": uid})
            await db.general_ledger.delete_many({"user_id": uid})
            await db.accounting_audit_log.delete_many({"user_id": uid})
