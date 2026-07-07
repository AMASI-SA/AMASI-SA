"""rev39 — MADA canary send guard tests (real Mongo, isolated tenant).

Pins: exact approval phrase, single-order scope, duplicate veto,
SKIPPED-history veto, pinned budget requirement, tolerated unmapped
REQUIRED_SKU (AMS11981), scoped settings overlay never touching the
DB, and dispatch delegation (mocked — NOTHING is sent in tests).
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from uuid import uuid4

import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient

import integrations.qoyod.one_shot_reprocess as osr
from integrations.qoyod.canary_budget import (
    CANARY_SCOPE_ALLOWLIST, arm_canary_budget, reserve_canary_budget,
)
from integrations.qoyod.mada_canary_send import (
    MADA_CANARY_APPROVAL_PHRASE, MADA_CANARY_ORDER_NUMBER,
    _ScopedDB, execute_mada_canary_send,
)

TENANT = f"test-mada-{uuid4().hex[:8]}"
COLLS = ("integration_inbox", "qoyod_invoices", "qoyod_settings",
         "qoyod_canary_budget", "mada_canary_audit_log",
         "qoyod_products_mapping")


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


def _row(stage="CUSTOMER_RESOLVED", history=None):
    return {
        "user_id": TENANT, "id": "row-mada",
        "trace_id": "trace-mada-1",
        "salla_order_number": MADA_CANARY_ORDER_NUMBER,
        "salla_order_id": MADA_CANARY_ORDER_NUMBER,
        "pipeline_stage": stage,
        "stage_history": history or [{"stage": "RECEIVED"},
                                     {"stage": "NORMALIZED"}],
        "received_at": datetime.now(timezone.utc),
        "canonical_payload": {
            "order_id": MADA_CANARY_ORDER_NUMBER,
            "order_number": MADA_CANARY_ORDER_NUMBER,
            "order_date": "2026-07-04", "order_status": "completed",
            "order_status_native": "تم التنفيذ",
            "payment_method": "credit_card",
            "customer": {"name": "عميلة", "phone": "0500000009"},
            "items": [{"sku": "AMS11981",
                       "name": "عباية ستيتش بناتي",
                       "quantity": 1, "unit_price": 172.8,
                       "total": 198.72, "tax_amount": 25.92,
                       "discount_amount": 0.0}],
            "subtotal": 172.8, "tax_amount": 25.92,
            "shipping_amount": 0.0, "discount_amount": 0.0,
            "total_amount": 198.72,
        },
    }


async def _arm_pinned(db):
    return await arm_canary_budget(
        db, user_id=TENANT, confirm_token="ARM-CANARY-BUDGET",
        max_orders=1,
        pinned_order_number=MADA_CANARY_ORDER_NUMBER, actor="test")


@pytest.mark.asyncio
async def test_scope_constant_is_mada_phase():
    assert CANARY_SCOPE_ALLOWLIST == ["credit_card"]


@pytest.mark.asyncio
async def test_refuses_wrong_phrase_and_wrong_order(db):
    out = await execute_mada_canary_send(
        db, user_id=TENANT, order_number=MADA_CANARY_ORDER_NUMBER,
        approval_phrase="wrong")
    assert out["outcome"] == "REFUSED" and out["guard_no"] == 1
    out2 = await execute_mada_canary_send(
        db, user_id=TENANT, order_number="123",
        approval_phrase=MADA_CANARY_APPROVAL_PHRASE)
    assert out2["outcome"] == "REFUSED" and out2["guard_no"] == 2
    assert out2["no_qoyod_api_calls"] is True


@pytest.mark.asyncio
async def test_refuses_duplicate_real_invoice(db):
    await db.integration_inbox.insert_one(_row())
    await db.qoyod_invoices.insert_one(
        {"user_id": TENANT,
         "salla_order_number": MADA_CANARY_ORDER_NUMBER,
         "qoyod_invoice_id": "888"})
    await _arm_pinned(db)
    out = await execute_mada_canary_send(
        db, user_id=TENANT, order_number=MADA_CANARY_ORDER_NUMBER,
        approval_phrase=MADA_CANARY_APPROVAL_PHRASE)
    assert out["outcome"] == "REFUSED"
    assert out["code"] == "duplicate_real_invoice"


@pytest.mark.asyncio
async def test_refuses_skipped_history(db):
    await db.integration_inbox.insert_one(
        _row(history=[{"stage": "RECEIVED"}, {"stage": "SKIPPED"}]))
    await _arm_pinned(db)
    out = await execute_mada_canary_send(
        db, user_id=TENANT, order_number=MADA_CANARY_ORDER_NUMBER,
        approval_phrase=MADA_CANARY_APPROVAL_PHRASE)
    assert out["outcome"] == "REFUSED"
    assert out["code"] == "skipped_history_veto"


@pytest.mark.asyncio
async def test_refuses_unarmed_or_unpinned_budget(db):
    await db.integration_inbox.insert_one(_row())
    out = await execute_mada_canary_send(
        db, user_id=TENANT, order_number=MADA_CANARY_ORDER_NUMBER,
        approval_phrase=MADA_CANARY_APPROVAL_PHRASE)
    assert out["code"] == "budget_not_armed"
    await arm_canary_budget(
        db, user_id=TENANT, confirm_token="ARM-CANARY-BUDGET",
        max_orders=1, actor="test")  # armed but NOT pinned
    out2 = await execute_mada_canary_send(
        db, user_id=TENANT, order_number=MADA_CANARY_ORDER_NUMBER,
        approval_phrase=MADA_CANARY_APPROVAL_PHRASE)
    assert out2["code"] == "budget_not_pinned_to_order"


@pytest.mark.asyncio
async def test_pinned_budget_refuses_other_orders(db):
    await _arm_pinned(db)
    ok, reason = await reserve_canary_budget(
        db, user_id=TENANT, order_number="999999")
    assert (ok, reason) == (False, "order_not_pinned")
    ok2, reason2 = await reserve_canary_budget(
        db, user_id=TENANT, order_number=MADA_CANARY_ORDER_NUMBER)
    assert ok2 is True and reason2 == "reserved"


@pytest.mark.asyncio
async def test_guards_pass_dispatches_once_with_unmapped_sku(db, monkeypatch):
    """AMS11981 unmapped is TOLERATED (created inside the send) —
    guards pass and dispatch fires exactly once. Dispatch mocked."""
    await db.integration_inbox.insert_one(_row())
    await _arm_pinned(db)
    calls = []

    async def _fake_reprocess(dbx, **kw):
        calls.append(kw)
        return {"outcome": "COMPLETED", "qoyod_invoice_id": "901",
                "qoyod_invoice_payment_id": "555"}

    monkeypatch.setattr(osr, "reprocess_one_order", _fake_reprocess)
    out = await execute_mada_canary_send(
        db, user_id=TENANT, order_number=MADA_CANARY_ORDER_NUMBER,
        approval_phrase=MADA_CANARY_APPROVAL_PHRASE, actor="test")
    assert out["outcome"] == "COMPLETED", out
    assert len(calls) == 1
    assert calls[0]["order_number"] == MADA_CANARY_ORDER_NUMBER
    assert calls[0]["confirm"] == f"REPROCESS-{MADA_CANARY_ORDER_NUMBER}"
    assert out["result"]["qoyod_invoice_id"] == "901"
    audits = await db.mada_canary_audit_log.count_documents({})
    assert audits >= 3  # received + guards_passed + dispatch


@pytest.mark.asyncio
async def test_scoped_db_overlay_never_mutates_settings(db):
    await db.qoyod_settings.insert_one(
        {"user_id": TENANT, "dry_run_mode": True,
         "production_writes_locked": True,
         "selective_live_send_enabled": False,
         "selective_auto_send_enabled": False})
    scoped = _ScopedDB(db)
    seen = await scoped.qoyod_settings.find_one({"user_id": TENANT})
    assert seen["dry_run_mode"] is False
    assert seen["production_writes_locked"] is False
    assert seen["selective_auto_send_allowed_payment_methods"] == ["credit_card"]
    raw = await db.qoyod_settings.find_one({"user_id": TENANT})
    assert raw["dry_run_mode"] is True
    assert raw["production_writes_locked"] is True
    assert "selective_auto_send_allowed_payment_methods" not in raw


@pytest.mark.asyncio
async def test_arm_response_shows_pin_and_mada_no_tabby(db):
    """rev39.4 — user requirement: the arm response must state the
    pin, payment scope, and counters explicitly; no Tabby wording."""
    out = await _arm_pinned(db)
    assert out["pinned_order_number"] == MADA_CANARY_ORDER_NUMBER
    assert out["canary_payment_method"] == "credit_card"
    assert out["allowed_payment_methods"] == ["credit_card"]
    assert out["max_orders"] == 1
    assert out["used"] == 0 and out["remaining"] == 1
    assert "Tabby" not in out["human_message"]
    assert "tabby" not in out["human_message"].lower()
    assert MADA_CANARY_ORDER_NUMBER in out["human_message"]
    # Status endpoint mirrors the same facts.
    from integrations.qoyod.canary_budget import get_canary_budget
    st = await get_canary_budget(db, user_id=TENANT)
    assert st["pinned_order_number"] == MADA_CANARY_ORDER_NUMBER
    assert st["canary_payment_method"] == "credit_card"
    assert st["used"] == 0 and st["remaining"] == 1


@pytest.mark.asyncio
async def test_partial_ic_flag_passed_only_for_invoice_created(
        db, monkeypatch):
    """rev39.5 — INVOICE_CREATED rows opt in to one_shot's audited
    partial-IC escape hatch; other stages do NOT."""
    row = _row(stage="INVOICE_CREATED")
    row["qoyod_invoice_id"] = "DRY:invoice:old"  # dry-era leftover
    await db.integration_inbox.insert_one(row)
    await _arm_pinned(db)
    calls = []

    async def _fake(dbx, **kw):
        calls.append(kw)
        return {"outcome": "COMPLETED", "qoyod_invoice_id": "950",
                "qoyod_invoice_payment_id": "601"}

    monkeypatch.setattr(osr, "reprocess_one_order", _fake)
    out = await execute_mada_canary_send(
        db, user_id=TENANT, order_number=MADA_CANARY_ORDER_NUMBER,
        approval_phrase=MADA_CANARY_APPROVAL_PHRASE, actor="test")
    assert out["outcome"] == "COMPLETED", out
    assert calls[0]["allow_reset_from_partial_invoice_created"] is True

    # Non-IC stage → flag False.
    await db.integration_inbox.delete_many({"user_id": TENANT})
    await db.integration_inbox.insert_one(_row(stage="CUSTOMER_RESOLVED"))
    calls.clear()
    out2 = await execute_mada_canary_send(
        db, user_id=TENANT, order_number=MADA_CANARY_ORDER_NUMBER,
        approval_phrase=MADA_CANARY_APPROVAL_PHRASE, actor="test")
    assert out2["outcome"] == "COMPLETED"
    assert calls[0]["allow_reset_from_partial_invoice_created"] is False


@pytest.mark.asyncio
async def test_error_surfaces_blocker_code_and_guard_snapshot(
        db, monkeypatch):
    """rev39.6 — OneShotRefused structured fields (incl.
    selective_send_blocker_code) must reach the operator response."""
    from integrations.qoyod.one_shot_reprocess import OneShotRefused
    await db.integration_inbox.insert_one(_row(stage="INVOICE_CREATED"))
    await _arm_pinned(db)

    async def _fake(dbx, **kw):
        raise OneShotRefused(
            "selective_send_policy_blocked", "policy said no",
            selective_send_blocker_code="TRIGGER_STATUS_NOT_ENABLED",
            selective_send_blocker_reason="status not in triggers")

    monkeypatch.setattr(osr, "reprocess_one_order", _fake)
    out = await execute_mada_canary_send(
        db, user_id=TENANT, order_number=MADA_CANARY_ORDER_NUMBER,
        approval_phrase=MADA_CANARY_APPROVAL_PHRASE, actor="test")
    assert out["outcome"] == "ERROR"
    assert out["one_shot_refusal"]["selective_send_blocker_code"] \
        == "TRIGGER_STATUS_NOT_ENABLED"
    snap = out["guards_snapshot"]
    assert snap["budget"]["pinned_order_number"] == MADA_CANARY_ORDER_NUMBER
    assert snap["budget"]["used"] == 0 and snap["budget"]["remaining"] == 1
    assert snap["pipeline_stage"] == "INVOICE_CREATED"
    assert snap["allow_reset_from_partial_invoice_created"] is True
    assert snap["checks"]["duplicate_check"]["passed"] is True
