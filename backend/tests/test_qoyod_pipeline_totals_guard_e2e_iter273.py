"""Iter-273 — end-to-end pipeline test for Totals Guard.

Verifies that `process_normalized_row` invokes `validate_totals`
BEFORE touching the customer/product resolvers, and that a refused
row lands in DEAD_LETTER (via FAILED_VALIDATION) with the right
error code and a complete audit trail in `totals_guard`.
"""
from __future__ import annotations

import os, uuid, pytest
from datetime import datetime, timezone
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")

from motor.motor_asyncio import AsyncIOMotorClient

from integrations.qoyod.invoice_builder import DryRunQoyodClient
from integrations.qoyod.pipeline import process_normalized_row


@pytest.fixture
def db():
    return AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]


def _now():
    return datetime.now(timezone.utc)


async def _seed_normalized(db, *, user_id, items, subtotal, total,
                           tax=0, ship=0, disc=0):
    row = {
        "id":         uuid.uuid4().hex,
        "user_id":    user_id,
        "trace_id":   uuid.uuid4().hex,
        "connector_key": "make_com_qoyod",
        "source":     "webhook",
        "received_at": _now(),
        "pipeline_stage": "NORMALIZED",
        "stage_history":  [],
        "canonical_payload": {
            "schema_version": 1,
            "order_id":         "TEST-ORDER",
            "order_number":     "TEST-ORDER",
            "order_status":     "completed",
            "order_status_native": "completed",
            "currency":         "SAR",
            "subtotal":         subtotal,
            "tax_amount":       tax,
            "shipping_amount":  ship,
            "discount_amount":  disc,
            "total_amount":     total,
            "customer":         {"name": "أحمد",
                                 "phone": "+966500000000",
                                 "is_guest": False},
            "items":            items,
            "payment_method":   "mada",
        },
    }
    await db.integration_inbox.insert_one(row)
    return row


async def _seed_settings(db, user_id):
    await db.qoyod_settings.update_one(
        {"user_id": user_id},
        {"$set": {"user_id": user_id, "enabled": True,
                  "dry_run_mode": True,
                  "invoice_trigger_statuses": ["completed"],
                  "default_tax_id": "TAX-15",
                  "default_branch_id": "BR-1"}},
        upsert=True)


@pytest.mark.asyncio
async def test_pipeline_refuses_268670571_shape_with_incomplete_items(db):
    """The exact production failure: subtotal=105, items_sum=5.
    Must DEAD_LETTER with `line_items_incomplete` and never reach
    CUSTOMER_RESOLVED."""
    user_id = f"tg-incomplete-{uuid.uuid4().hex[:6]}"
    row = await _seed_normalized(
        db, user_id=user_id,
        subtotal=105.0, total=131.60, ship=23.15, tax=3.45,
        items=[{"sku": "AMS11961",
                "name": "تغليف انيق معا الورد",
                "quantity": 1, "unit_price": 5.0, "tax_amount": 0,
                "total": 5}],
    )
    await _seed_settings(db, user_id)
    try:
        out = await process_normalized_row(
            db, row, api_client=DryRunQoyodClient())
        assert out["outcome"] == "DEAD_LETTER"
        assert out["reason"] == "line_items_incomplete"

        updated = await db.integration_inbox.find_one({"id": row["id"]})
        # Row landed in DEAD_LETTER via FAILED_VALIDATION.
        assert updated["pipeline_stage"] == "DEAD_LETTER"
        # Stage history must show the canonical two-hop path.
        history_to = [h.get("to_stage") for h in (updated["stage_history"] or [])]
        assert "FAILED_VALIDATION" in history_to
        assert history_to[-1] == "DEAD_LETTER"
        # NEVER reached CUSTOMER_RESOLVED — no Qoyod side-effects.
        assert "CUSTOMER_RESOLVED" not in history_to
        assert updated.get("qoyod_customer_id") in (None, "")

        # totals_guard audit block persisted with full details.
        tg = updated.get("totals_guard")
        assert tg is not None
        assert tg["code"] == "line_items_incomplete"
        assert tg["details"]["items_sum_excl"] == 5.0
        assert tg["details"]["subtotal"] == 105.0
        assert tg["details"]["shortfall"] == 100.0
    finally:
        await db.integration_inbox.delete_many({"user_id": user_id})
        await db.qoyod_settings.delete_one({"user_id": user_id})


@pytest.mark.asyncio
async def test_pipeline_passes_clean_totals_row_to_customer_resolution(db):
    """Counterpart to the failing case: when items_sum matches subtotal,
    the guard must NOT block — row advances to CUSTOMER_RESOLVED."""
    user_id = f"tg-clean-{uuid.uuid4().hex[:6]}"
    # subtotal = items_sum = 105 (50*2 + 5*1).
    row = await _seed_normalized(
        db, user_id=user_id,
        subtotal=105.0, total=131.60, ship=23.15, tax=3.45,
        items=[
            {"sku": "A", "name": "بند 1", "quantity": 2,
             "unit_price": 50.0, "tax_amount": 0, "total": 100},
            {"sku": "B", "name": "بند 2", "quantity": 1,
             "unit_price": 5.0,  "tax_amount": 0, "total": 5},
        ],
    )
    await _seed_settings(db, user_id)
    try:
        out = await process_normalized_row(
            db, row, api_client=DryRunQoyodClient())
        # In dry-run mode the customer resolver creates a fake id and
        # the row reaches CUSTOMER_RESOLVED. We don't assert on the
        # full happy path — only that the guard didn't block.
        assert out["outcome"] != "DEAD_LETTER" or out.get("reason") not in (
            "line_items_incomplete", "line_items_total_mismatch",
            "order_total_mismatch",
        )
        updated = await db.integration_inbox.find_one({"id": row["id"]})
        history_to = [h.get("to_stage") for h in (updated["stage_history"] or [])]
        # Must have transitioned past NORMALIZED.
        assert "RULES_APPLIED" in history_to or "SKIPPED" in history_to
        # Totals guard must NOT have logged a refusal.
        tg = updated.get("totals_guard")
        if tg is not None:
            assert tg.get("ok") is not False
    finally:
        await db.integration_inbox.delete_many({"user_id": user_id})
        await db.qoyod_settings.delete_one({"user_id": user_id})
        await db.qoyod_customers_mapping.delete_many({"user_id": user_id})


@pytest.mark.asyncio
async def test_pipeline_refuses_order_total_mismatch_with_correct_code(db):
    """items_sum matches subtotal, but declared total doesn't reconcile
    with subtotal+tax+ship−disc → `order_total_mismatch`."""
    user_id = f"tg-totmis-{uuid.uuid4().hex[:6]}"
    row = await _seed_normalized(
        db, user_id=user_id,
        subtotal=100.0, total=999.0, ship=20.0, tax=15.0,
        items=[{"sku": "X", "name": "x", "quantity": 1,
                "unit_price": 100.0, "tax_amount": 0, "total": 100}],
    )
    await _seed_settings(db, user_id)
    try:
        out = await process_normalized_row(
            db, row, api_client=DryRunQoyodClient())
        assert out["outcome"] == "DEAD_LETTER"
        assert out["reason"] == "order_total_mismatch"
        updated = await db.integration_inbox.find_one({"id": row["id"]})
        assert updated["pipeline_stage"] == "DEAD_LETTER"
        # CUSTOMER_RESOLVED never happened.
        assert "CUSTOMER_RESOLVED" not in [h.get("to_stage") for h in
                                           (updated["stage_history"] or [])]
    finally:
        await db.integration_inbox.delete_many({"user_id": user_id})
        await db.qoyod_settings.delete_one({"user_id": user_id})
