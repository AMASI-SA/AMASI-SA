"""rev41 — Unified Send Diagnosis tests (real Mongo, isolated tenant).

Pins: READ-ONLY contract (zero writes), qoyod_write_reached always
false, one verdict rule for all payment methods, REAL policy engine
blocker surfaced, guards_snapshot (stored vs canary-effective),
budget snapshot, duplicate/stage vetoes.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from uuid import uuid4

import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient

from integrations.qoyod.canary_budget import arm_canary_budget
from integrations.qoyod.send_diagnosis import build_send_diagnosis

TENANT = f"test-diag-{uuid4().hex[:8]}"
ORDER = "269875747"
COLLS = ("integration_inbox", "qoyod_invoices", "qoyod_settings",
         "qoyod_canary_budget", "mada_canary_audit_log",
         "qoyod_products_mapping", "qoyod_customers_mapping")


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


def _row(stage="CUSTOMER_RESOLVED", status="completed",
         status_native="تم التنفيذ", invoice_id=None, history=None,
         pm="mada", customer_id=None):
    return {
        "user_id": TENANT, "id": "row-diag",
        "trace_id": "trace-diag-1",
        "salla_order_number": ORDER,
        "salla_order_id": ORDER,
        "pipeline_stage": stage,
        "qoyod_invoice_id": invoice_id,
        "qoyod_customer_id": customer_id,
        "stage_history": history or [{"stage": "RECEIVED"},
                                     {"stage": "NORMALIZED"}],
        "received_at": datetime.now(timezone.utc),
        "canonical_payload": {
            "order_id": ORDER, "order_number": ORDER,
            "order_date": "2026-07-04", "order_status": status,
            "order_status_native": status_native,
            "payment_method": pm,
            "customer": {"name": "عميلة", "phone": "0500000009"},
            "items": [{"sku": "AMS11981", "name": "عباية",
                       "quantity": 1, "unit_price": 172.8,
                       "total": 198.72, "tax_amount": 25.92,
                       "discount_amount": 0.0}],
            "subtotal": 172.8, "tax_amount": 25.92,
            "shipping_amount": 0.0, "discount_amount": 0.0,
            "total_amount": 198.72,
        },
    }


async def _seed_mapping(db):
    await db.qoyod_products_mapping.insert_one(
        {"user_id": TENANT, "sku": "AMS11981",
         "qoyod_product_id": "777", "dry_run_only": False})
    # rev46.1 — SSOT now mirrors SAS gate check 8 (payment account).
    await db.qoyod_settings.update_one(
        {"user_id": TENANT},
        {"$set": {"payment_method_mapping": [
            {"salla_method": "mada", "qoyod_account_id": "77"}]}},
        upsert=True)


async def _snapshot_counts(db):
    return {c: await db[c].count_documents({"user_id": TENANT})
            for c in COLLS}


@pytest.mark.asyncio
async def test_order_not_found_is_refused_read_only(db):
    out = await build_send_diagnosis(db, user_id=TENANT,
                                     order_number="000000")
    assert out["verdict"] == "REFUSED"
    assert out["blocker_code"] == "order_not_found"
    assert out["qoyod_write_reached"] is False
    assert out["read_only"] is True


@pytest.mark.asyncio
async def test_ready_order_gets_ready_verdict_and_snapshots(db):
    # Mirrors the real prod state of 269875747: partial
    # INVOICE_CREATED (no real invoice id) + resolved customer.
    await db.integration_inbox.insert_one(
        _row(stage="INVOICE_CREATED", invoice_id=None,
             customer_id="555"))
    await _seed_mapping(db)
    await arm_canary_budget(
        db, user_id=TENANT, confirm_token="ARM-CANARY-BUDGET",
        max_orders=1, pinned_order_number=ORDER, actor="test")
    before = await _snapshot_counts(db)
    out = await build_send_diagnosis(db, user_id=TENANT,
                                     order_number=ORDER)
    after = await _snapshot_counts(db)
    assert before == after, "READ-ONLY violated — a write happened"
    assert out["verdict"] == "READY_TO_SEND_ONCE"
    assert out["blocker_code"] is None
    assert out["all_blockers"] == []
    assert out["payment_method"] == "mada"
    assert out["qoyod_write_reached"] is False
    # budget snapshot
    assert out["budget"]["armed"] is True
    assert out["budget"]["pinned_order_number"] == ORDER
    assert out["budget_used"] == 0 and out["budget_remaining"] == 1
    # guards snapshot: stored is fail-closed, effective is overlaid
    gs = out["guards_snapshot"]
    stored = gs["stored_settings_gates"]
    eff = gs["effective_during_canary_send"]
    assert stored["selective_live_send_enabled"] is False
    assert stored["production_writes_locked"] is True
    assert eff["selective_live_send_enabled"] is True
    assert eff["production_writes_locked"] is False
    assert gs["canary_overlay_applied"]["dry_run_mode"] is False


@pytest.mark.asyncio
async def test_duplicate_real_invoice_blocks(db):
    await db.integration_inbox.insert_one(
        _row(stage="INVOICE_CREATED", invoice_id="888"))
    await _seed_mapping(db)
    await arm_canary_budget(
        db, user_id=TENANT, confirm_token="ARM-CANARY-BUDGET",
        max_orders=1, pinned_order_number=ORDER, actor="test")
    out = await build_send_diagnosis(db, user_id=TENANT,
                                     order_number=ORDER)
    assert out["verdict"] == "REFUSED"
    codes = [b["code"] for b in out["all_blockers"]]
    assert "duplicate_check" in codes
    assert "stage_check" in codes  # real invoice → hatch closed
    assert out["duplicate_check"]["existing_qoyod_invoice_id"] == "888"


@pytest.mark.asyncio
async def test_real_policy_blocker_is_surfaced(db):
    # delivered-status order → policy engine must refuse with the
    # REAL blocker code, not a guessed one.
    await db.integration_inbox.insert_one(
        _row(status="delivered", status_native="تم التوصيل"))
    await _seed_mapping(db)
    await arm_canary_budget(
        db, user_id=TENANT, confirm_token="ARM-CANARY-BUDGET",
        max_orders=1, pinned_order_number=ORDER, actor="test")
    out = await build_send_diagnosis(db, user_id=TENANT,
                                     order_number=ORDER)
    assert out["verdict"] == "REFUSED"
    codes = [b["code"] for b in out["all_blockers"]]
    assert "invoice_trigger_status_not_enabled" in codes
    pol = out["selective_send_policy"]
    assert pol["decision"] == "block"
    assert pol["blocker_code"] == "invoice_trigger_status_not_enabled"
    assert pol["blocker_reason"]


@pytest.mark.asyncio
async def test_budget_blockers(db):
    await db.integration_inbox.insert_one(_row())
    await _seed_mapping(db)
    out = await build_send_diagnosis(db, user_id=TENANT,
                                     order_number=ORDER)
    codes = [b["code"] for b in out["all_blockers"]]
    assert "budget_not_armed" in codes
    await arm_canary_budget(
        db, user_id=TENANT, confirm_token="ARM-CANARY-BUDGET",
        max_orders=1, pinned_order_number="999999", actor="test")
    out2 = await build_send_diagnosis(db, user_id=TENANT,
                                      order_number=ORDER)
    codes2 = [b["code"] for b in out2["all_blockers"]]
    assert "budget_pinned_to_other_order" in codes2


@pytest.mark.asyncio
async def test_skipped_stage_blocks(db):
    await db.integration_inbox.insert_one(_row(stage="SKIPPED"))
    await _seed_mapping(db)
    out = await build_send_diagnosis(db, user_id=TENANT,
                                     order_number=ORDER)
    assert out["verdict"] == "REFUSED"
    codes = [b["code"] for b in out["all_blockers"]]
    assert "skipped_dead_letter_check" in codes
    assert "stage_check" in codes
