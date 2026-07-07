"""rev45 — Customer resolved DURING the audited send (user option أ).

Pins:
  • policy: `pending_resolution_during_send` skips the pre-send
    customer blocker; DRY/PREVIEW customer ids remain FATAL; the
    auto-send path (no flag) is UNCHANGED.
  • SSOT: a 270939808-class order (transient SKIPPED, completed,
    mapped, customer unresolved) is now fully GREEN.
  • one_shot: 7a no longer refuses on customer_not_resolved — the
    SSOT gate is reached (proven via a product blocker downstream).
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient

from integrations.qoyod.one_shot_reprocess import (
    OneShotRefused, reprocess_one_order,
)
from integrations.qoyod.selective_send_policy import (
    should_allow_selective_live_send,
)
from integrations.qoyod.send_eligibility_ssot import (
    evaluate_order_for_qoyod_send,
)

TENANT = f"test-r45-{uuid4().hex[:8]}"
COLLS = ("integration_inbox", "qoyod_settings",
         "qoyod_products_mapping", "qoyod_invoices",
         "qoyod_per_order_approvals")


@pytest_asyncio.fixture()
async def db():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    database = client[os.environ["DB_NAME"]]
    for c in COLLS:
        await database[c].delete_many({"user_id": TENANT})
    yield database
    for c in COLLS:
        await database[c].delete_many({"user_id": TENANT})
    client.close()


def _policy_order(customer_status):
    return {
        "order_number": "1", "salla_order_id": "1",
        "salla_order_created_at": "2026-07-06T10:00:00Z",
        "status": "completed", "payment_method": "credit_card",
        "existing_qoyod_invoice_id": None,
        "customer_status": customer_status,
        "products_status": {"resolved": True, "resolved_count": 1,
                            "dry_run_only": 0, "missing": []},
        "totals_status": {"valid": True, "total": 0.0,
                          "expected": 0.0, "diff": 0.0},
    }


_SETTINGS = {"selective_live_send_enabled": True,
             "production_writes_locked": False}


def test_policy_pending_resolution_allows_unresolved_customer():
    d = should_allow_selective_live_send(
        order=_policy_order({"resolved": False, "qoyod_id": None,
                             "pending_resolution_during_send": True}),
        settings=_SETTINGS)
    assert d.decision == "allow"


def test_policy_pending_resolution_keeps_dry_fatal():
    d = should_allow_selective_live_send(
        order=_policy_order({"resolved": True,
                             "qoyod_id": "DRY:contact:abc",
                             "pending_resolution_during_send": True}),
        settings=_SETTINGS)
    assert d.decision == "block"
    assert d.blocker_code == "customer_dry_or_null"


def test_policy_without_flag_unchanged_auto_send_path():
    d = should_allow_selective_live_send(
        order=_policy_order({"resolved": False, "qoyod_id": None}),
        settings=_SETTINGS)
    assert d.decision == "block"
    assert d.blocker_code == "customer_not_resolved"
    d2 = should_allow_selective_live_send(
        order=_policy_order({"resolved": True, "qoyod_id": None}),
        settings=_SETTINGS)
    assert d2.blocker_code == "customer_dry_or_null"


def _row(order, *, sku="SKU-45", stage="SKIPPED",
         skip_class="transient", customer_id=None):
    r = {
        "user_id": TENANT, "id": f"row-{order}",
        "trace_id": f"tr-{order}",
        "idempotency_key": f"idem-{order}",
        "connector_key": "salla",
        "salla_order_number": str(order), "salla_order_id": str(order),
        "pipeline_stage": stage,
        "qoyod_customer_id": customer_id,
        "stage_history": [
            {"from_stage": "RECEIVED", "to_stage": "NORMALIZED",
             "at": datetime.now(timezone.utc), "actor": "webhook"},
            {"from_stage": "NORMALIZED", "to_stage": "SKIPPED",
             "at": datetime.now(timezone.utc), "actor": "worker"}],
        "received_at": datetime.now(timezone.utc),
        "canonical_payload": {
            "order_id": str(order), "order_number": str(order),
            "order_date": "2026-07-06T10:00:00Z",
            "order_status": "completed",
            "order_status_native": "تم التنفيذ",
            "payment_method": "credit_card",
            "customer": {"name": "ع", "phone": "0500000001"},
            "items": [{"sku": sku, "name": "منتج", "quantity": 1,
                       "unit_price": 106.91, "total": 122.95,
                       "tax_amount": 16.04, "discount_amount": 0.0}],
            "subtotal": 106.91, "tax_amount": 16.04,
            "shipping_amount": 0.0, "discount_amount": 0.0,
            "total_amount": 122.95,
        },
    }
    if skip_class:
        r["skip_class"] = skip_class
    return r


@pytest.mark.asyncio
async def test_ssot_270939808_class_order_fully_green(db):
    await db.integration_inbox.insert_one(_row("270939808"))
    await db.qoyod_products_mapping.insert_one(
        {"user_id": TENANT, "sku": "SKU-45", "qoyod_product_id": "9"})
    await db.qoyod_settings.update_one(
        {"user_id": TENANT},
        {"$set": {"payment_method_mapping": [
            {"salla_method": "credit_card",
             "qoyod_account_id": "77"}]}}, upsert=True)
    ev = await evaluate_order_for_qoyod_send(
        db, user_id=TENANT, order_number="270939808")
    assert ev["ready_to_send"] is True
    assert ev["blockers"] == []
    assert ev["policy_check"]["passed"] is True


@pytest.mark.asyncio
async def test_ssot_dry_customer_stays_fatal(db):
    await db.integration_inbox.insert_one(
        _row("902", customer_id="DRY:contact:8f73e010"))
    await db.qoyod_products_mapping.insert_one(
        {"user_id": TENANT, "sku": "SKU-45", "qoyod_product_id": "9"})
    ev = await evaluate_order_for_qoyod_send(
        db, user_id=TENANT, order_number="902")
    assert ev["ready_to_send"] is False
    codes = [b["code"] for b in ev["blockers"]]
    assert "dry_check" in codes
    assert "customer_dry_or_null" not in codes  # deduped root cause


@pytest.mark.asyncio
async def test_one_shot_passes_customer_gate_reaches_ssot(db):
    # Unresolved customer + UNMAPPED product: before rev45 one_shot
    # died at 7a (customer_not_resolved). Now it must pass 7a and be
    # refused by the SSOT gate for the REAL blocker (product).
    await db.qoyod_settings.insert_one(
        {"user_id": TENANT, "production_writes_locked": False,
         "selective_live_send_enabled": True, "dry_run_mode": False,
         "payment_method_mapping": [
             {"salla_method": "credit_card",
              "qoyod_account_id": "77"}]})
    await db.integration_inbox.insert_one(
        _row("903", sku="SKU-NOMAP", stage="NORMALIZED",
             skip_class=None))
    with patch(
        "integrations.qoyod.one_shot_reprocess.get_api_key",
        new_callable=AsyncMock, return_value="fake-key",
    ), patch(
        "integrations.qoyod.one_shot_reprocess._quarantine_dry_mappings",
        new_callable=AsyncMock, return_value={"quarantined": 0},
    ), patch(
        "integrations.qoyod.one_shot_reprocess._reset_row_to_stage",
        new_callable=AsyncMock,
    ):
        with pytest.raises(OneShotRefused) as exc:
            await reprocess_one_order(
                db, user_id=TENANT, order_number="903",
                confirm="REPROCESS-903", actor="test")
    assert exc.value.code == "ssot_not_ready_to_send"
    assert exc.value.extra["primary_blocker_code"] \
        == "product_mapping_check"
