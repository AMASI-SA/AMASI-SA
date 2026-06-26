"""QYD-GO — Production Readiness Layer tests.

Verifies the 11-item checklist + quantitative pre-flight report +
activation gating (refuses to flip live mode while any item fails).
"""
from __future__ import annotations

import os, uuid, pytest
from datetime import datetime, timezone
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")

from motor.motor_asyncio import AsyncIOMotorClient

from integrations.qoyod.models import ensure_qoyod_indexes
from integrations.qoyod.go_live import (
    go_live_checklist, go_live_report,
    activate_production_mode, ActivationBlocked,
)


@pytest.fixture
def db():
    return AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]


# A controlled fake API client — lets us test the lookup checks
# without hitting Qoyod.
class _FakeClient:
    def __init__(self, *, products_total=0, contacts_total=0,
                 products_raise=False, contacts_raise=False):
        self._products_total = products_total
        self._contacts_total = contacts_total
        self._products_raise = products_raise
        self._contacts_raise = contacts_raise

    async def list_products(self, *, page=1, limit=50):
        if self._products_raise:
            from integrations.qoyod.api_client import QoyodAPIError
            raise QoyodAPIError(status_code=401, code="qoyod_unauthorized",
                                message="bad key", endpoint="GET /products")
        return {"products": [{"id": i} for i in range(min(limit, 3))],
                "meta": {"total": self._products_total}}

    async def list_contacts(self, *, page=1, limit=50):
        if self._contacts_raise:
            from integrations.qoyod.api_client import QoyodAPIError
            raise QoyodAPIError(status_code=500, code="qoyod_server_error",
                                message="oops", endpoint="GET /contacts")
        return {"contacts": [{"id": i} for i in range(min(limit, 3))],
                "meta": {"total": self._contacts_total}}


async def _wipe(db, user_id: str):
    await db.qoyod_settings.delete_many({"user_id": user_id})
    await db.qoyod_credentials.delete_many({"user_id": user_id})
    await db.integration_inbox.delete_many({"user_id": user_id})
    await db.qoyod_invoices.delete_many({"user_id": user_id})
    await db.qoyod_products_mapping.delete_many({"user_id": user_id})
    await db.qoyod_customers_mapping.delete_many({"user_id": user_id})


async def _seed_full_ready(db, user_id: str):
    """Seed everything the checklist needs to pass."""
    await ensure_qoyod_indexes(db)
    await db.qoyod_settings.update_one(
        {"user_id": user_id}, {"$set": {
            "user_id": user_id, "enabled": False, "dry_run_mode": True,
            "default_branch_id": "BR-1", "default_tax_id": "TAX-15",
            "invoice_trigger_statuses": ["completed"],
            "payment_method_mapping": [
                {"salla_method": "mada", "qoyod_account_id": "ACC-9"}],
        }}, upsert=True)
    # Pretend we have stored credentials.
    await db.qoyod_credentials.update_one(
        {"user_id": user_id},
        {"$set": {"user_id": user_id,
                  "api_key_enc": "X",
                  "fingerprint": "qy-test-fp",
                  "updated_at": datetime.now(timezone.utc)}}, upsert=True)
    # An eligible NORMALIZED row.
    await db.integration_inbox.insert_one({
        "id": uuid.uuid4().hex,
        "user_id": user_id, "connector_key": "make_com_qoyod",
        "idempotency_key": f"go-{user_id}-1",
        "pipeline_stage": "CUSTOMER_RESOLVED",
        "qoyod_customer_id": "C-1",
        "canonical_payload": {
            "order_id": "GO-1", "order_status": "completed",
            "items": [{"sku":"K-1","name":"X","quantity":1,
                       "unit_price":100,"tax_amount":15,"total":115}],
            "customer": {"phone":"+966500000001"},
            "payment_method": "mada", "total_amount": 115,
        },
    })
    # One completed dry-run invoice (proves testing happened).
    await db.qoyod_invoices.insert_one({
        "user_id": user_id, "salla_order_id": "DRY-OLD",
        "trace_id": "t-old", "dry_run": True,
        "pipeline_stage": "COMPLETED", "status": "pending",
        "updated_at": datetime.now(timezone.utc),
    })
    # Product + customer mappings.
    await db.qoyod_products_mapping.insert_one({
        "user_id": user_id, "sku": "K-1",
        "qoyod_product_id": "P-1"})
    await db.qoyod_customers_mapping.insert_one({
        "user_id": user_id, "lookup_key": "+966500000001",
        "qoyod_customer_id": "C-1"})


# ─────────────────────────────────────────────────────────────────────
# A) Checklist — happy path
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_checklist_all_passes_when_fully_ready(db):
    user_id = f"qydgo_ok_{uuid.uuid4().hex[:6]}"
    try:
        await _seed_full_ready(db, user_id)
        res = await go_live_checklist(
            db, user_id, api_client=_FakeClient(products_total=10,
                                                contacts_total=5))
        assert res["all_passed"] is True
        assert res["totals"]["checks"] == 11
        assert res["totals"]["failed"] == 0
        # Every check key must be present.
        keys = {i["key"] for i in res["items"]}
        assert keys == {
            "api_key", "branch", "tax", "payment_mapping",
            "product_mapping", "customer_mapping", "dry_run",
            "outstanding_failures", "eligible_orders",
            "products_lookup", "customers_lookup",
        }
    finally:
        await _wipe(db, user_id)


@pytest.mark.asyncio
async def test_checklist_fails_when_api_key_missing(db):
    user_id = f"qydgo_nokey_{uuid.uuid4().hex[:6]}"
    try:
        await _seed_full_ready(db, user_id)
        # Drop the credentials row.
        await db.qoyod_credentials.delete_many({"user_id": user_id})
        res = await go_live_checklist(db, user_id, api_client=_FakeClient())
        assert res["all_passed"] is False
        api = next(i for i in res["items"] if i["key"] == "api_key")
        assert api["ok"] is False
    finally:
        await _wipe(db, user_id)


@pytest.mark.asyncio
async def test_checklist_fails_when_branch_or_tax_missing(db):
    user_id = f"qydgo_no_branch_{uuid.uuid4().hex[:6]}"
    try:
        await _seed_full_ready(db, user_id)
        await db.qoyod_settings.update_one(
            {"user_id": user_id},
            {"$unset": {"default_branch_id": "", "default_tax_id": ""}})
        res = await go_live_checklist(db, user_id, api_client=_FakeClient())
        assert res["all_passed"] is False
        items = {i["key"]: i for i in res["items"]}
        assert items["branch"]["ok"] is False
        assert items["tax"]["ok"] is False
    finally:
        await _wipe(db, user_id)


@pytest.mark.asyncio
async def test_checklist_fails_when_payment_method_unmapped(db):
    user_id = f"qydgo_pm_{uuid.uuid4().hex[:6]}"
    try:
        await _seed_full_ready(db, user_id)
        await db.qoyod_settings.update_one(
            {"user_id": user_id},
            {"$set": {"payment_method_mapping": []}})
        res = await go_live_checklist(db, user_id, api_client=_FakeClient())
        items = {i["key"]: i for i in res["items"]}
        assert items["payment_mapping"]["ok"] is False
    finally:
        await _wipe(db, user_id)


@pytest.mark.asyncio
async def test_checklist_fails_when_dry_run_disabled_before_activation(db):
    user_id = f"qydgo_no_dry_{uuid.uuid4().hex[:6]}"
    try:
        await _seed_full_ready(db, user_id)
        await db.qoyod_settings.update_one(
            {"user_id": user_id}, {"$set": {"dry_run_mode": False}})
        res = await go_live_checklist(db, user_id, api_client=_FakeClient())
        items = {i["key"]: i for i in res["items"]}
        assert items["dry_run"]["ok"] is False
    finally:
        await _wipe(db, user_id)


@pytest.mark.asyncio
async def test_checklist_fails_when_outstanding_failures_present(db):
    user_id = f"qydgo_stuck_{uuid.uuid4().hex[:6]}"
    try:
        await _seed_full_ready(db, user_id)
        await db.integration_inbox.insert_one({
            "id": uuid.uuid4().hex, "user_id": user_id,
            "connector_key": "make_com_qoyod",
            "idempotency_key": f"stuck-{user_id}",
            "pipeline_stage": "DEAD_LETTER",
            "last_failed_stage": "FAILED_CUSTOMER",
        })
        res = await go_live_checklist(db, user_id, api_client=_FakeClient())
        items = {i["key"]: i for i in res["items"]}
        assert items["outstanding_failures"]["ok"] is False
    finally:
        await _wipe(db, user_id)


@pytest.mark.asyncio
async def test_checklist_lookups_fail_on_qoyod_api_error(db):
    user_id = f"qydgo_lookup_fail_{uuid.uuid4().hex[:6]}"
    try:
        await _seed_full_ready(db, user_id)
        res = await go_live_checklist(
            db, user_id,
            api_client=_FakeClient(products_raise=True, contacts_raise=True))
        items = {i["key"]: i for i in res["items"]}
        assert items["products_lookup"]["ok"] is False
        assert items["customers_lookup"]["ok"] is False
    finally:
        await _wipe(db, user_id)


# ─────────────────────────────────────────────────────────────────────
# B) Quantitative report
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_report_reflects_creation_vs_lookup_counts(db):
    user_id = f"qydgo_rep_{uuid.uuid4().hex[:6]}"
    try:
        await _seed_full_ready(db, user_id)
        # Add a SECOND eligible order with an unmapped SKU and unmapped pm.
        await db.integration_inbox.insert_one({
            "id": uuid.uuid4().hex,
            "user_id": user_id, "connector_key": "make_com_qoyod",
            "idempotency_key": f"go-{user_id}-2",
            "pipeline_stage": "NORMALIZED",
            "canonical_payload": {
                "order_id": "GO-2", "order_status": "completed",
                "items": [{"sku":"NEW-99","name":"Y","quantity":1,
                           "unit_price":50,"tax_amount":7.5,"total":57.5}],
                "customer": {"phone": "+966522222222"},
                "payment_method": "apple_pay", "total_amount": 57.5,
            },
        })
        rep = await go_live_report(
            db, user_id,
            api_client=_FakeClient(products_total=200, contacts_total=120))
        assert rep["eligible_orders_count"] == 2
        # K-1 exists locally, NEW-99 doesn't → 1 product needs creation.
        assert rep["products_needing_creation"] == 1
        assert rep["products_already_in_qoyod"] >= 1
        assert rep["qoyod_products_total"] == 200
        assert rep["qoyod_contacts_total"] == 120
        # Customer +966522222222 is unmapped → needs creation.
        assert rep["customers_needing_creation"] == 1
        assert rep["customers_already_local"] >= 1
        # apple_pay is not in payment mapping.
        assert "apple_pay" in rep["unmapped_payment_methods"]
        # would_fail: the second order will fail preflight on PM mapping.
        assert rep["would_fail_if_live_now"] >= 1
    finally:
        await _wipe(db, user_id)


# ─────────────────────────────────────────────────────────────────────
# C) Activation gating
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_activation_blocked_when_checklist_fails(db):
    user_id = f"qydgo_block_{uuid.uuid4().hex[:6]}"
    try:
        await _seed_full_ready(db, user_id)
        # Break one item.
        await db.qoyod_settings.update_one(
            {"user_id": user_id}, {"$set": {"default_tax_id": None}})
        with pytest.raises(ActivationBlocked) as ei:
            await activate_production_mode(db, user_id,
                                           api_client=_FakeClient(products_total=10))
        assert any("الضريبة" in r for r in ei.value.reasons)
        # Settings stayed unchanged.
        s = await db.qoyod_settings.find_one({"user_id": user_id})
        assert s["dry_run_mode"] is True
        assert s.get("enabled") in (False, None)
    finally:
        await _wipe(db, user_id)


@pytest.mark.asyncio
async def test_activation_flips_settings_when_all_pass(db):
    user_id = f"qydgo_go_{uuid.uuid4().hex[:6]}"
    try:
        await _seed_full_ready(db, user_id)
        res = await activate_production_mode(
            db, user_id,
            api_client=_FakeClient(products_total=10, contacts_total=5))
        assert res["ok"] is True
        assert res["activated_at"] is not None
        s = await db.qoyod_settings.find_one({"user_id": user_id})
        assert s["dry_run_mode"] is False     # flipped off
        assert s["enabled"] is True           # flipped on
        assert s["activated_at"] is not None
    finally:
        await _wipe(db, user_id)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
