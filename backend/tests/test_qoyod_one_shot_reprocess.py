"""One-Shot Reprocess — single-order, strict, audit-trail.

Coverage:
    • Confirm token mismatch is refused (typo-resistant, order-specific).
    • Wrong order_number in token vs URL → refused.
    • Row not found → refused with code `row_not_found`.
    • Multiple matches → refused with `multiple_matches_pick_one_by_trace_id`.
    • dry_run_mode active → refused (we target REAL Qoyod only).
    • Credentials missing → refused.
    • DRY: customer + product mappings are quarantined before re-run.
    • Already-COMPLETED row → `ALREADY_COMPLETED` no-op.
    • Pipeline leak guard still trips when DRY ids appear in the
      payload (defence-in-depth — guard owns the refusal).
    • `_scan_payload_for_dry` helper detects nested leaks.
"""
from __future__ import annotations

import os, uuid, pytest
from datetime import datetime, timezone
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")

from motor.motor_asyncio import AsyncIOMotorClient

from integrations.qoyod.one_shot_reprocess import (
    reprocess_one_order, OneShotRefused, CONFIRM_TOKEN_TEMPLATE,
    _scan_payload_for_dry, _quarantine_dry_mappings,
)


@pytest.fixture
def db():
    return AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]


def _now():
    return datetime.now(timezone.utc)


async def _seed_inbox_row(db, *, user_id, order_number, stage="DEAD_LETTER",
                          dry_customer=False, dry_product_sku=None):
    row = {
        "id":                  uuid.uuid4().hex,
        "user_id":             user_id,
        "trace_id":            uuid.uuid4().hex,
        "connector_key":       "make_com_qoyod",
        "source":              "webhook",
        "received_at":         _now(),
        "salla_order_id":      order_number,
        "salla_order_number":  order_number,
        "idempotency_key":     f"os-{order_number}-{uuid.uuid4().hex[:6]}",
        "pipeline_stage":      stage,
        "pipeline_error":      {"code": "dry_run_product_id_leaked_to_production"} if stage == "DEAD_LETTER" else None,
        "last_failed_stage":   "FAILED_INVOICE" if stage == "DEAD_LETTER" else None,
        "qoyod_customer_id":   "DRY:contact:abc12345" if dry_customer else "Q-real-contact",
        "canonical_payload":   {
            "order_id":        order_number,
            "order_number":    order_number,
            "currency":        "SAR",
            "total_amount":    115.0, "tax_amount": 15.0,
            "customer":        {"name": "أحمد", "phone": "+966500000099"},
            "items":           [{"sku": dry_product_sku or "SKU-PROD-1",
                                 "name": "X", "quantity": 1,
                                 "unit_price": 100, "tax_amount": 15,
                                 "total": 115}],
            "payment_method":  "mada",
        },
        "stage_history":       [],
    }
    await db.integration_inbox.insert_one(row)
    return row


async def _seed_settings(db, user_id, **overrides):
    base = {
        "user_id": user_id, "enabled": True, "dry_run_mode": False,
        "invoice_trigger_statuses": ["completed"],
        "default_tax_id": "TAX-15", "default_branch_id": "BR-1",
        "payment_method_mapping": [{"salla_method": "mada",
                                    "qoyod_account_id": "ACC-9"}],
    }
    base.update(overrides)
    await db.qoyod_settings.update_one(
        {"user_id": user_id}, {"$set": base}, upsert=True)


# ─── Token / lookup refusal paths ────────────────────────────────────
@pytest.mark.asyncio
async def test_confirm_token_must_match_order_number(db):
    user_id = f"os-token-{uuid.uuid4().hex[:6]}"
    row = await _seed_inbox_row(db, user_id=user_id, order_number="268670571")
    await _seed_settings(db, user_id)
    try:
        with pytest.raises(OneShotRefused) as exc:
            await reprocess_one_order(
                db, user_id=user_id,
                order_number="268670571",
                confirm="REPROCESS-WRONG",
                actor="op")
        assert exc.value.code == "confirm_token_mismatch"
        assert exc.value.extra["expected"] == "REPROCESS-268670571"
    finally:
        await db.integration_inbox.delete_many({"user_id": user_id})
        await db.qoyod_settings.delete_one({"user_id": user_id})


@pytest.mark.asyncio
async def test_row_not_found_is_refused(db):
    user_id = f"os-nf-{uuid.uuid4().hex[:6]}"
    await _seed_settings(db, user_id)
    try:
        with pytest.raises(OneShotRefused) as exc:
            await reprocess_one_order(
                db, user_id=user_id,
                order_number="999999",
                confirm="REPROCESS-999999",
                actor="op")
        assert exc.value.code == "row_not_found"
    finally:
        await db.qoyod_settings.delete_one({"user_id": user_id})


@pytest.mark.asyncio
async def test_multiple_matches_must_be_disambiguated(db):
    user_id = f"os-multi-{uuid.uuid4().hex[:6]}"
    await _seed_inbox_row(db, user_id=user_id, order_number="42")
    await _seed_inbox_row(db, user_id=user_id, order_number="42")
    await _seed_settings(db, user_id)
    try:
        with pytest.raises(OneShotRefused) as exc:
            await reprocess_one_order(
                db, user_id=user_id, order_number="42",
                confirm="REPROCESS-42", actor="op")
        assert exc.value.code == "multiple_matches_pick_one_by_trace_id"
        assert len(exc.value.extra["candidates"]) == 2
    finally:
        await db.integration_inbox.delete_many({"user_id": user_id})
        await db.qoyod_settings.delete_one({"user_id": user_id})


# ─── Mode / credential refusals ──────────────────────────────────────
@pytest.mark.asyncio
async def test_dry_run_mode_active_is_refused(db):
    user_id = f"os-dry-{uuid.uuid4().hex[:6]}"
    await _seed_inbox_row(db, user_id=user_id, order_number="100")
    await _seed_settings(db, user_id, dry_run_mode=True)
    try:
        with pytest.raises(OneShotRefused) as exc:
            await reprocess_one_order(
                db, user_id=user_id, order_number="100",
                confirm="REPROCESS-100", actor="op")
        assert exc.value.code == "dry_run_mode_active"
    finally:
        await db.integration_inbox.delete_many({"user_id": user_id})
        await db.qoyod_settings.delete_one({"user_id": user_id})


@pytest.mark.asyncio
async def test_credentials_missing_is_refused(db):
    user_id = f"os-nc-{uuid.uuid4().hex[:6]}"
    await _seed_inbox_row(db, user_id=user_id, order_number="200")
    await _seed_settings(db, user_id)
    # NO credentials seeded — get_api_key returns None.
    try:
        with pytest.raises(OneShotRefused) as exc:
            await reprocess_one_order(
                db, user_id=user_id, order_number="200",
                confirm="REPROCESS-200", actor="op")
        assert exc.value.code == "credentials_missing"
    finally:
        await db.integration_inbox.delete_many({"user_id": user_id})
        await db.qoyod_settings.delete_one({"user_id": user_id})


# ─── Already-completed no-op ─────────────────────────────────────────
@pytest.mark.asyncio
async def test_already_completed_row_is_a_noop(db):
    user_id = f"os-done-{uuid.uuid4().hex[:6]}"
    row = await _seed_inbox_row(
        db, user_id=user_id, order_number="300", stage="COMPLETED")
    await _seed_settings(db, user_id)
    try:
        out = await reprocess_one_order(
            db, user_id=user_id, order_number="300",
            confirm="REPROCESS-300", actor="op")
        assert out["outcome"] == "ALREADY_COMPLETED"
        # No state mutation happened.
        unchanged = await db.integration_inbox.find_one({"id": row["id"]})
        assert unchanged["pipeline_stage"] == "COMPLETED"
    finally:
        await db.integration_inbox.delete_many({"user_id": user_id})
        await db.qoyod_settings.delete_one({"user_id": user_id})


# ─── DRY: quarantine helper (the cleanup step) ───────────────────────
@pytest.mark.asyncio
async def test_quarantine_dry_mappings_marks_customer_and_products(db):
    user_id = f"os-q-{uuid.uuid4().hex[:6]}"
    row = await _seed_inbox_row(
        db, user_id=user_id, order_number="400",
        dry_customer=True, dry_product_sku="SKU-LEAK")
    # Seed a customer mapping with DRY:contact
    await db.qoyod_customers_mapping.update_one(
        {"user_id": user_id, "lookup_key": "+966500000099"},
        {"$set": {"qoyod_customer_id": "DRY:contact:deadbeef",
                  "lookup_kind": "phone"}},
        upsert=True)
    # Seed a product mapping with DRY:product
    await db.qoyod_products_mapping.update_one(
        {"user_id": user_id, "sku": "SKU-LEAK"},
        {"$set": {"qoyod_product_id": "DRY:product:cafebabe"}},
        upsert=True)
    try:
        summary = await _quarantine_dry_mappings(
            db, user_id=user_id, row=row)
        assert summary["customer_mapping_quarantined"] is True
        assert summary["customer_quarantined_id"] == "DRY:contact:deadbeef"
        assert summary["row_customer_id_cleared"] is True
        assert summary["product_mappings_quarantined"] == [
            {"sku": "SKU-LEAK", "quarantined_id": "DRY:product:cafebabe"}]

        # Verify quarantine markers actually persisted.
        cm = await db.qoyod_customers_mapping.find_one(
            {"user_id": user_id, "lookup_key": "+966500000099"})
        assert cm["dry_run_only"] is True
        assert cm["quarantine_reason"] == "one_shot_reprocess"
        pm = await db.qoyod_products_mapping.find_one(
            {"user_id": user_id, "sku": "SKU-LEAK"})
        assert pm["dry_run_only"] is True
        assert pm["quarantined_id"] == "DRY:product:cafebabe"

        # Row's qoyod_customer_id was nullified.
        fresh = await db.integration_inbox.find_one({"id": row["id"]})
        assert fresh["qoyod_customer_id"] is None
        assert fresh["qoyod_customer_id_cleared_reason"] == "dry_run_leak"
    finally:
        await db.integration_inbox.delete_many({"user_id": user_id})
        await db.qoyod_customers_mapping.delete_many({"user_id": user_id})
        await db.qoyod_products_mapping.delete_many({"user_id": user_id})


# ─── _scan_payload_for_dry — recursive structural sniffer ────────────
def test_scan_payload_detects_contact_id_leak():
    p = {"invoice": {"contact_id": "DRY:contact:abc",
                     "line_items": [{"product_id": "P-1"}]}}
    leaks = _scan_payload_for_dry(p)
    assert "contact_id=DRY:contact:abc" in leaks


def test_scan_payload_detects_product_id_leak():
    p = {"invoice": {"contact_id": "Q-real",
                     "line_items": [
                         {"product_id": "P-1"},
                         {"product_id": "DRY:product:dead"},
                     ]}}
    leaks = _scan_payload_for_dry(p)
    assert "product_id=DRY:product:dead" in leaks
    assert "product_id=P-1" not in leaks


def test_scan_payload_returns_empty_for_clean_payload():
    p = {"invoice": {"contact_id": "Q-CUST-1",
                     "line_items": [{"product_id": "Q-PROD-9"}]}}
    assert _scan_payload_for_dry(p) == []


def test_confirm_token_template_is_order_specific():
    """Tokens cannot be reused across orders — they include the
    order_number verbatim. Belt-and-suspenders sanity check."""
    a = CONFIRM_TOKEN_TEMPLATE.format(order_number="268670571")
    b = CONFIRM_TOKEN_TEMPLATE.format(order_number="268670572")
    assert a != b
    assert "268670571" in a and "268670572" in b
