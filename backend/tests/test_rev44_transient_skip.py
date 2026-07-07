"""rev44 — Transient vs Fatal SKIPPED (user decree, prod forensics).

Pins:
  • classification: transitional-status & payment-scope = transient;
    cancelled/refunded, pre-floor, duplicate, unknown = fatal.
  • SAS cutover clamped to the 2026-07-01 integration floor.
  • pipeline stamps skip_class at write time (new flow only).
  • SSOT: transient SKIPPED does not block; legacy SKIPPED stays fatal.
  • one_shot: transient SKIPPED resumable via RETRYING; fatal refused.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from uuid import uuid4

import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient

from integrations.qoyod.one_shot_reprocess import (
    OneShotRefused, _reset_row_to_stage,
)
from integrations.qoyod.pipeline import process_normalized_row
from integrations.qoyod.selective_auto_send_gate import (
    evaluate_selective_auto_send_gate,
)
from integrations.qoyod.send_eligibility_ssot import (
    evaluate_order_for_qoyod_send,
)
from integrations.qoyod.skip_classification import classify_skip

TENANT = f"test-r44-{uuid4().hex[:8]}"
COLLS = ("integration_inbox", "qoyod_settings",
         "qoyod_products_mapping", "qoyod_invoices")


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


def _row(order, *, stage="NORMALIZED", pm="mada", sku="SKU-R",
         status="completed", status_native="تم التنفيذ",
         order_date="2026-07-03T10:00:00Z", skip_class=None,
         customer_id="55"):
    r = {
        "user_id": TENANT, "id": f"row-{order}",
        "trace_id": f"tr-{order}",
        "idempotency_key": f"idem-{order}",
        "connector_key": "salla",
        "salla_order_number": str(order), "salla_order_id": str(order),
        "pipeline_stage": stage,
        "qoyod_customer_id": customer_id,
        "pipeline_started_at": datetime.now(timezone.utc),
        "stage_history": [{"from_stage": "RECEIVED",
                           "to_stage": "NORMALIZED",
                           "at": datetime.now(timezone.utc),
                           "actor": "webhook"}],
        "received_at": datetime.now(timezone.utc),
        "canonical_payload": {
            "order_id": str(order), "order_number": str(order),
            "order_date": order_date, "order_status": status,
            "order_status_native": status_native,
            "payment_method": pm,
            "customer": {"name": "ع", "phone": "0500000001"},
            "items": [{"sku": sku, "name": "منتج", "quantity": 1,
                       "unit_price": 100.0, "total": 115.0,
                       "tax_amount": 15.0, "discount_amount": 0.0}],
            "subtotal": 100.0, "tax_amount": 15.0,
            "shipping_amount": 0.0, "discount_amount": 0.0,
            "total_amount": 115.0,
        },
    }
    if stage == "SKIPPED":
        r["stage_history"].append(
            {"from_stage": "NORMALIZED", "to_stage": "SKIPPED",
             "at": datetime.now(timezone.utc), "actor": "worker"})
    if skip_class:
        r["skip_class"] = skip_class
    return r


# ── 1. Classification rules ──────────────────────────────────────────
def test_classification_matrix():
    assert classify_skip("payment_method_not_in_allow_list") \
        == "transient"
    assert classify_skip("status_hard_blocked",
                         status_native="جاري التوصيل") == "transient"
    assert classify_skip("not_in_trigger_statuses",
                         status_native="جاري التوصيل") == "transient"
    assert classify_skip("payment_method_hard_blocked") == "transient"
    # cancelled/refunded overrides ANY reason → fatal.
    assert classify_skip("status_not_in_allow_list",
                         status_native="ملغي") == "fatal"
    assert classify_skip("not_in_trigger_statuses",
                         status_native="مسترجع") == "fatal"
    # fatal by reason / unknown → fail-closed.
    assert classify_skip("order_created_before_cutover") == "fatal"
    assert classify_skip("duplicate_real_invoice") == "fatal"
    assert classify_skip("pre_activation_skipped") == "fatal"
    assert classify_skip("some_future_reason") == "fatal"
    assert classify_skip(None) == "fatal"


# ── 2. Cutover clamped to the 2026-07-01 floor ──────────────────────
def test_cutover_clamped_to_integration_floor():
    settings = {
        "selective_auto_send_enabled": True,
        "selective_auto_send_cutover_at": "2026-07-05T00:00:00Z",
        "selective_auto_send_allowed_payment_methods": ["mada"],
        "payment_method_mapping": [
            {"salla_method": "mada", "qoyod_account_id": "77"}],
    }
    row = _row("100")
    d = evaluate_selective_auto_send_gate(
        canonical=row["canonical_payload"], row=row, settings=settings)
    # order 2026-07-03 > clamped cutover 2026-07-01 → NOT before_cutover
    assert d.reason != "order_created_before_cutover"
    assert d.eligible is True
    assert d.cutover_at.startswith("2026-07-01")


# ── 3. Pipeline stamps skip_class (new flow only) ────────────────────
@pytest.mark.asyncio
async def test_pipeline_stamps_transient_on_payment_scope(db):
    await db.qoyod_settings.insert_one({
        "user_id": TENANT,
        "selective_auto_send_enabled": True,
        "selective_auto_send_cutover_at": "2026-07-05T00:00:00Z",
        "selective_auto_send_allowed_payment_methods":
            ["tabby_installment"],
    })
    row = _row("200")
    await db.integration_inbox.insert_one(dict(row))
    out = await process_normalized_row(db, row)
    assert out["outcome"] == "SKIPPED"
    assert out["reason"] == "payment_method_not_in_allow_list"
    saved = await db.integration_inbox.find_one({"id": row["id"]})
    assert saved["pipeline_stage"] == "SKIPPED"
    assert saved["skip_class"] == "transient"
    assert saved["skip_class_reason"] == \
        "payment_method_not_in_allow_list"


@pytest.mark.asyncio
async def test_pipeline_stamps_fatal_on_cancelled(db):
    await db.qoyod_settings.insert_one({
        "user_id": TENANT,
        "selective_auto_send_enabled": True,
        "selective_auto_send_cutover_at": "2026-07-01T00:00:00Z",
        "selective_auto_send_allowed_payment_methods": ["mada"],
        "payment_method_mapping": [
            {"salla_method": "mada", "qoyod_account_id": "77"}],
    })
    row = _row("201", status="ملغي", status_native="ملغي")
    await db.integration_inbox.insert_one(dict(row))
    out = await process_normalized_row(db, row)
    assert out["outcome"] == "SKIPPED"
    saved = await db.integration_inbox.find_one({"id": row["id"]})
    assert saved["skip_class"] == "fatal"


# ── 4. SSOT honours the classification ───────────────────────────────
@pytest.mark.asyncio
async def test_ssot_transient_skip_not_blocking(db):
    await db.integration_inbox.insert_one(
        _row("300", stage="SKIPPED", skip_class="transient"))
    await db.qoyod_products_mapping.insert_one(
        {"user_id": TENANT, "sku": "SKU-R", "qoyod_product_id": "9"})
    ev = await evaluate_order_for_qoyod_send(
        db, user_id=TENANT, order_number="300")
    assert ev["skipped_dead_letter_check"]["passed"] is True
    assert ev["stage_check"]["passed"] is True
    assert ev["ready_to_send"] is True
    assert ev["blockers"] == []


@pytest.mark.asyncio
async def test_ssot_legacy_skip_stays_fatal(db):
    await db.integration_inbox.insert_one(
        _row("301", stage="SKIPPED"))  # no skip_class (legacy row)
    await db.qoyod_products_mapping.insert_one(
        {"user_id": TENANT, "sku": "SKU-R", "qoyod_product_id": "9"})
    ev = await evaluate_order_for_qoyod_send(
        db, user_id=TENANT, order_number="301")
    assert ev["ready_to_send"] is False
    codes = [b["code"] for b in ev["blockers"]]
    assert "skipped_dead_letter_check" in codes
    assert "stage_check" in codes


# ── 5. one_shot reset — transient resumable, fatal refused ──────────
@pytest.mark.asyncio
async def test_one_shot_reset_transient_vs_fatal(db):
    tr = _row("400", stage="SKIPPED", skip_class="transient")
    await db.integration_inbox.insert_one(dict(tr))
    await _reset_row_to_stage(db, tr, resume_stage="NORMALIZED",
                              actor="test")
    saved = await db.integration_inbox.find_one({"id": tr["id"]})
    assert saved["pipeline_stage"] == "NORMALIZED"
    stages = [h["to_stage"] for h in saved["stage_history"]]
    assert "RETRYING" in stages  # audited two-hop path

    fa = _row("401", stage="SKIPPED", skip_class="fatal")
    await db.integration_inbox.insert_one(dict(fa))
    with pytest.raises(OneShotRefused) as exc:
        await _reset_row_to_stage(db, fa, resume_stage="NORMALIZED",
                                  actor="test")
    assert exc.value.code == "skipped_is_terminal_rev33"

    legacy = _row("402", stage="SKIPPED")  # unclassified
    await db.integration_inbox.insert_one(dict(legacy))
    with pytest.raises(OneShotRefused):
        await _reset_row_to_stage(db, legacy,
                                  resume_stage="NORMALIZED",
                                  actor="test")
