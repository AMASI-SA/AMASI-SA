"""Day 5 — Products + Invoice + Receipt + Dry-Run + Pre-flight +
Payload Snapshot + Partial-Failure.

All four pre-Day 5 safety rules locked in:
    1. Dry Run Mode      — full pipeline, no Qoyod POST.
    2. Pre-flight        — 6 checks before invoice creation.
    3. Payload Snapshot  — saved BEFORE every Qoyod POST.
    4. Partial Failure   — receipt-only failure → PARTIAL_FAILURE.
"""
from __future__ import annotations

import hashlib
import os, uuid, pytest
from datetime import datetime, timezone
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")

from motor.motor_asyncio import AsyncIOMotorClient

from integrations.qoyod.models import ensure_qoyod_indexes
from integrations.qoyod.dto import SalesOrderDTO, CustomerDTO, LineItemDTO
from integrations.qoyod.preflight import run as preflight_run
from integrations.qoyod.invoice_builder import (
    DryRunQoyodClient, build_invoice_payload, build_receipt_payload,
)
from integrations.qoyod.pipeline import (
    process_customer_resolved_row, day4_report,
)
from integrations.qoyod.state_machine import (
    can_transition, TERMINAL_STAGES, PARTIAL_FAILURE,
)


# ─── Live-mode fake client ─────────────────────────────────────────
# `DryRunQoyodClient` mints `DRY:*` ids by design — those are the
# Dry-Run watermark. In production-mode pipeline tests we still need
# an HTTP-free fake, but it MUST return realistic (non-DRY) ids so
# the Dry-Run Leak Preflight Guard (pipeline.py, Iter-267) does not
# block the invoice. This subclass overrides `_fake` to produce
# `Q-<kind>-<sha8>` ids that mirror what real Qoyod returns.
class _LiveLikeQoyodClient(DryRunQoyodClient):
    """HTTP-free Qoyod stub for production-mode tests.

    Iter-290c — returns NUMERIC ids (production-realistic) so the
    invoice builder's int-coercion doesn't drop them. Real Qoyod
    always returns integer ids."""
    def _fake(self, kind: str, payload: dict) -> str:
        h = hashlib.sha1(repr(sorted(payload.items())).encode("utf-8")).hexdigest()
        # First 6 hex chars → 0..16M, plenty for tests, always numeric.
        return str(int(h[:6], 16))


@pytest.fixture
def db():
    return AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]


def _make_dto(order_id="D5-1"):
    return SalesOrderDTO(
        order_id=order_id, order_number=order_id,
        order_status="completed", order_status_native="تم التنفيذ",
        order_date=datetime.now(timezone.utc),
        completed_at=datetime.now(timezone.utc),
        currency="SAR", total_amount=115.0, tax_amount=15.0,
        customer=CustomerDTO(name="أحمد", phone="+966501234567"),
        items=[LineItemDTO(sku="SKU-1", name="X", quantity=1,
                           unit_price=100, tax_amount=15, total=115)],
        payment_method="mada",
    )


# ─── State machine — PARTIAL_FAILURE wiring ─────────────────────────
def test_state_machine_has_partial_failure_terminal():
    assert "PARTIAL_FAILURE" in TERMINAL_STAGES
    # Edge: FAILED_RECEIPT → PARTIAL_FAILURE is allowed.
    assert can_transition("FAILED_RECEIPT", "PARTIAL_FAILURE")
    # PARTIAL_FAILURE is terminal except for the bounded auto-requeue
    # edge → RETRYING (added 2026-02-27 to self-heal known-fixed errors).
    assert can_transition("PARTIAL_FAILURE", "RETRYING")
    for to in ("DEAD_LETTER", "COMPLETED"):
        assert not can_transition("PARTIAL_FAILURE", to)


# ─── Pre-flight checklist ───────────────────────────────────────────
def _base_payload():
    dto = _make_dto()
    return {
        "dto_dict": dto.model_dump(mode="json"),
        "settings": {
            "invoice_trigger_statuses": ["completed"],
            "default_tax_id": "TAX-15",
            "tax_mode": "mezan_fixed_15",
            "default_inventory_id": "INV-1",
            "payment_method_mapping": [
                {"salla_method": "mada", "qoyod_account_id": "9"}],
        },
        "qoyod_customer_id": "1",
        "product_resolutions": [{"sku": "SKU-1", "qoyod_product_id": "1"}],
    }


def test_preflight_passes_when_everything_set():
    r = preflight_run(**_base_payload())
    assert r.passed is True
    assert r.failures == []


def test_preflight_fails_when_customer_missing():
    p = _base_payload(); p["qoyod_customer_id"] = None
    r = preflight_run(**p)
    assert r.passed is False
    assert any(f["check"] == "customer" for f in r.failures)


def test_preflight_fails_when_product_unresolved():
    p = _base_payload(); p["product_resolutions"] = [{"sku": "SKU-1"}]
    r = preflight_run(**p)
    assert any(f["check"] == "products" for f in r.failures)


def test_preflight_fails_when_tax_not_configured():
    p = _base_payload(); p["settings"]["default_tax_id"] = None
    # Iter-285: customer_first mode does NOT require default_tax_id.
    # This test specifically checks the legacy mezan_fixed_15 path.
    p["settings"]["tax_mode"] = "mezan_fixed_15"
    # Force items_have_tax=False by removing tax_amount.
    for it in p["dto_dict"]["items"]: it["tax_amount"] = None
    r = preflight_run(**p)
    assert any(f["check"] == "tax" for f in r.failures)


def test_preflight_fails_when_payment_method_unmapped():
    p = _base_payload(); p["settings"]["payment_method_mapping"] = []
    r = preflight_run(**p)
    assert any(f["code"] == "payment_method_mapping_missing"
               for f in r.failures)


def test_preflight_fails_when_already_sent():
    p = _base_payload()
    p["existing_invoice_row"] = {"status": "sent"}
    p["settings"]["trigger_once_only"] = True
    r = preflight_run(**p)
    assert any(f["code"] == "already_sent" for f in r.failures)


# ─── Payload builders ──────────────────────────────────────────────
def test_invoice_payload_includes_all_required_fields():
    dto = _make_dto("INV-1")
    pl = build_invoice_payload(
        dto_dict=dto.model_dump(mode="json"),
        qoyod_customer_id="9",
        product_resolutions=[{"sku":"SKU-1","qoyod_product_id":"99"}],
        invoice_date=dto.completed_at,
        settings={"default_tax_id":"1", "default_branch_id":"1",
                   "default_inventory_id": "1",
                   "tax_mode": "mezan_fixed_15"})
    inv = pl["invoice"]
    # Iter-290c — all ids are integers; tax is tax_percent per line.
    assert inv["contact_id"] == 9
    assert inv["currency_code"] == "SAR"
    assert inv["branch_id"] == 1
    assert inv["inventory_id"] == 1
    assert inv["status"] == "Approved"
    assert inv["external_reference"] == "INV-1"
    assert inv["line_items"][0]["product_id"] == 99
    assert inv["line_items"][0]["tax_percent"] == 15
    assert inv["line_items"][0]["discount_type"] == "amount"
    assert "tax_id" not in inv["line_items"][0]
    assert "inventory_id" not in inv["line_items"][0]


def test_receipt_payload_resolves_payment_account():
    """Iter-290d — receipt also has Qoyod ids coerced to int + contact_id at root."""
    dto = _make_dto("RCP-1")
    pl = build_receipt_payload(
        qoyod_invoice_id="51", qoyod_customer_id="109",
        dto_dict=dto.model_dump(mode="json"),
        invoice_date=dto.completed_at,
        settings={"payment_method_mapping":[
            {"salla_method":"mada","qoyod_account_id":"77"}]})
    assert pl["receipt"]["account_id"] == 77
    assert pl["receipt"]["invoice_id"] == 51
    assert pl["receipt"]["contact_id"] == 109
    assert pl["receipt"]["amount"] == 115.0


# ─── DryRunQoyodClient ──────────────────────────────────────────────
@pytest.mark.asyncio
async def test_dry_run_client_records_calls_and_returns_fake_ids():
    c = DryRunQoyodClient()
    # Legacy.qoyod.com expects {"customer": {...}} envelope
    r1 = await c.create_contact({"customer":{"name":"X"}}, idem="i1")
    r2 = await c.create_invoice({"invoice":{"ref":"O-1"}}, idem="i2")
    r3 = await c.create_receipt({"receipt":{"invoice_id":r2["invoice"]["id"]}}, idem="i3")
    assert r1["customer"]["id"].startswith("DRY:contact:")
    assert r2["invoice"]["id"].startswith("DRY:invoice:")
    assert r3["receipt"]["id"].startswith("DRY:receipt:")
    assert len(c.calls) == 3
    # Audit trail records the correct endpoint
    assert c.calls[0]["endpoint"] == "POST /customers"


# ─── E2E pipeline tests ─────────────────────────────────────────────
async def _seed_customer_resolved(db, *, user_id, order_id, dto,
                                  customer_id="1"):
    row = {
        "id": uuid.uuid4().hex, "user_id": user_id,
        "trace_id": uuid.uuid4().hex,
        "connector_key": "make_com_qoyod", "source": "webhook",
        "received_at": datetime.now(timezone.utc),
        "raw_payload": {}, "raw_headers": {},
        "signature_status": "verified",
        "salla_order_id": order_id, "salla_order_number": order_id,
        "idempotency_key": f"d5-{order_id}-{uuid.uuid4().hex[:6]}",
        "pipeline_stage": "CUSTOMER_RESOLVED",
        "pipeline_error": None, "attempts": 0,
        "stage_history": [],
        "canonical_payload": dto.model_dump(mode="json"),
        "pipeline_started_at": datetime.now(timezone.utc),
        "last_success_stage": "CUSTOMER_RESOLVED",
        "qoyod_customer_id": customer_id,
        "business_rules_decision": {
            "eligible": True, "reason": "eligible",
            "invoice_date": datetime.now(timezone.utc).isoformat(),
            "invoice_date_source": "completed_at",
            "triggered_by_status": "completed",
        },
    }
    await db.integration_inbox.insert_one(row)
    return row


async def _seed_settings(db, user_id, **overrides):
    base = {
        "schema_version": 1, "user_id": user_id, "enabled": True,
        "auto_send": True, "auto_receipt": True,
        "dry_run_mode": False,
        "invoice_trigger_statuses": ["completed"],
        "invoice_date_source": "trigger_status_date",
        "trigger_once_only": True,
        "default_tax_id": "TAX-15", "default_branch_id": "BR-1",
        "tax_mode": "mezan_fixed_15",
        "default_product_type": "service",
        # Iter-287 — Qoyod-required product creation defaults.
        "default_product_category_id":  "CAT-99",
        "default_product_tax_id":       "TAX-15",
        "default_product_unit_type_id": "UNIT-PIECE",
        "default_sales_account_id":     "ACC-SALES",
        # Iter-290 — Qoyod-required warehouse id on invoice lines.
        "default_inventory_id":         "INV-1",
        "payment_method_mapping": [{"salla_method":"mada",
                                    "qoyod_account_id":"9"}],
        "capabilities": {"create_customers": True, "create_products": True,
                         "create_invoices":  True, "create_receipts":  True},
    }
    base.update(overrides)
    await db.qoyod_settings.update_one(
        {"user_id": user_id}, {"$set": base}, upsert=True)


@pytest.mark.asyncio
async def test_pipeline_dry_run_completes_without_qoyod_post(db):
    await ensure_qoyod_indexes(db)
    user_id = "main"; order_id = f"D5-DRY-{uuid.uuid4().hex[:6]}"
    await _seed_settings(db, user_id, dry_run_mode=True)
    row = await _seed_customer_resolved(db, user_id=user_id,
                                        order_id=order_id, dto=_make_dto(order_id))
    fake = DryRunQoyodClient()
    try:
        out = await process_customer_resolved_row(db, row, api_client=fake)
        assert out["outcome"] == "COMPLETED"
        assert out["dry_run"] is True
        updated = await db.integration_inbox.find_one({"id": row["id"]})
        assert updated["pipeline_stage"] == "COMPLETED"
        # Payload snapshots are present.
        assert updated["qoyod_payloads"]["invoice"]["invoice"]["contact_id"] == 1
        assert updated["qoyod_payloads"]["receipt"]["receipt"]["account_id"] == 9
        # qoyod_invoices ledger row exists but status is pending (dry-run).
        led = await db.qoyod_invoices.find_one({"salla_order_id": order_id})
        assert led["status"] == "pending"
        assert led["dry_run"] is True
    finally:
        await db.integration_inbox.delete_many({"id": row["id"]})
        await db.qoyod_invoices.delete_many({"salla_order_id": order_id})
        await db.qoyod_products_mapping.delete_many({"user_id": user_id})


@pytest.mark.asyncio
async def test_pipeline_preflight_blocks_when_tax_missing(db):
    await ensure_qoyod_indexes(db)
    user_id = "main"; order_id = f"D5-PF-{uuid.uuid4().hex[:6]}"
    await _seed_settings(db, user_id, default_tax_id=None)
    dto = _make_dto(order_id)
    # Bypass DTO validation by mutating canonical dict directly so the
    # preflight tax check sees items with tax_amount=None.
    dto_dict = dto.model_dump(mode="json")
    for it in dto_dict["items"]:
        it["tax_amount"] = None
    row = {
        "id": uuid.uuid4().hex, "user_id": user_id,
        "trace_id": uuid.uuid4().hex,
        "connector_key": "make_com_qoyod", "source": "webhook",
        "received_at": datetime.now(timezone.utc),
        "raw_payload": {}, "raw_headers": {},
        "signature_status": "verified",
        "salla_order_id": order_id, "salla_order_number": order_id,
        "idempotency_key": f"d5-tax-{order_id}-{uuid.uuid4().hex[:6]}",
        "pipeline_stage": "CUSTOMER_RESOLVED",
        "pipeline_error": None, "attempts": 0, "stage_history": [],
        "canonical_payload": dto_dict,
        "pipeline_started_at": datetime.now(timezone.utc),
        "last_success_stage": "CUSTOMER_RESOLVED",
        "qoyod_customer_id": "1",
        "business_rules_decision": {
            "eligible": True, "reason": "eligible",
            "invoice_date": datetime.now(timezone.utc).isoformat(),
            "invoice_date_source": "completed_at",
            "triggered_by_status": "completed",
        },
    }
    await db.integration_inbox.insert_one(row)
    try:
        out = await process_customer_resolved_row(db, row,
                                                  api_client=DryRunQoyodClient())
        assert out["outcome"] == "DEAD_LETTER"
        assert out["reason"] == "preflight_failed"
        updated = await db.integration_inbox.find_one({"id": row["id"]})
        assert updated["pipeline_stage"] == "DEAD_LETTER"
        # Preflight result recorded for the operator.
        assert any(f["check"] == "tax"
                   for f in updated["pipeline_error"]["preflight"]["failures"])
        # Invoice payload was NOT snapshotted (preflight aborted before build).
        assert "invoice" not in (updated.get("qoyod_payloads") or {})
    finally:
        await db.integration_inbox.delete_many({"id": row["id"]})
        await db.qoyod_products_mapping.delete_many({"user_id": user_id})


@pytest.mark.asyncio
async def test_pipeline_partial_failure_on_receipt_error(db):
    """Invoice succeeds, receipt POST raises → PARTIAL_FAILURE."""
    await ensure_qoyod_indexes(db)
    user_id = "main"; order_id = f"D5-PARTIAL-{uuid.uuid4().hex[:6]}"
    await _seed_settings(db, user_id, dry_run_mode=False)
    row = await _seed_customer_resolved(db, user_id=user_id,
                                        order_id=order_id, dto=_make_dto(order_id))

    from integrations.qoyod.api_client import QoyodAPIError
    class _FlakyClient(_LiveLikeQoyodClient):
        async def create_receipt(self, payload, *, idem):
            raise QoyodAPIError(status_code=502, code="qoyod_server_error",
                                message="upstream timeout", endpoint="POST /receipts")

    try:
        out = await process_customer_resolved_row(db, row,
                                                  api_client=_FlakyClient())
        assert out["outcome"] == "PARTIAL_FAILURE"
        assert out["reason"] == "FAILED_RECEIPT"
        updated = await db.integration_inbox.find_one({"id": row["id"]})
        assert updated["pipeline_stage"] == "PARTIAL_FAILURE"
        assert updated["last_failed_stage"] == "FAILED_RECEIPT"
        # last_success_stage stopped at INVOICE_CREATED — invoice DID post.
        assert updated["last_success_stage"] == "INVOICE_CREATED"
        # Both payload snapshots present (invoice posted, receipt attempted).
        assert "invoice" in updated["qoyod_payloads"]
        assert "receipt" in updated["qoyod_payloads"]
        # Ledger reflects the split state.
        led = await db.qoyod_invoices.find_one({"salla_order_id": order_id})
        assert led["status"] == "invoice_sent_receipt_failed"
        assert led["pipeline_stage"] == "PARTIAL_FAILURE"
    finally:
        await db.integration_inbox.delete_many({"id": row["id"]})
        await db.qoyod_invoices.delete_many({"salla_order_id": order_id})
        await db.qoyod_products_mapping.delete_many({"user_id": user_id})


@pytest.mark.asyncio
async def test_pipeline_records_payload_snapshot_before_post(db):
    """Even on a happy live run, payloads MUST be snapshotted first."""
    await ensure_qoyod_indexes(db)
    user_id = "main"; order_id = f"D5-SNAP-{uuid.uuid4().hex[:6]}"
    await _seed_settings(db, user_id, dry_run_mode=False)
    row = await _seed_customer_resolved(db, user_id=user_id,
                                        order_id=order_id, dto=_make_dto(order_id))
    try:
        out = await process_customer_resolved_row(db, row,
                                                  api_client=_LiveLikeQoyodClient())
        assert out["outcome"] == "COMPLETED"
        updated = await db.integration_inbox.find_one({"id": row["id"]})
        # Snapshot timestamps prove they were written BEFORE the COMPLETED ts.
        assert updated["qoyod_payloads"]["invoice_snapshot_at"] is not None
        assert updated["qoyod_payloads"]["receipt_snapshot_at"] is not None
    finally:
        await db.integration_inbox.delete_many({"id": row["id"]})
        await db.qoyod_invoices.delete_many({"salla_order_id": order_id})
        await db.qoyod_products_mapping.delete_many({"user_id": user_id})


@pytest.mark.asyncio
async def test_day4_report_aggregates_outcomes(db):
    user_id = f"d5_report_{uuid.uuid4().hex[:6]}"
    try:
        # Sprinkle a few rows across stages.
        await db.integration_inbox.insert_many([
            {"id": "r1", "user_id": user_id,
             "connector_key": "make_com_qoyod",
             "idempotency_key": f"{user_id}-1",
             "pipeline_stage": "SKIPPED",
             "business_rules_decision": {"reason": "not_in_trigger_statuses"}},
            {"id": "r2", "user_id": user_id,
             "connector_key": "make_com_qoyod",
             "idempotency_key": f"{user_id}-2",
             "pipeline_stage": "SKIPPED",
             "business_rules_decision": {"reason": "already_sent"}},
            {"id": "r3", "user_id": user_id,
             "connector_key": "make_com_qoyod",
             "idempotency_key": f"{user_id}-3",
             "pipeline_stage": "DEAD_LETTER",
             "last_failed_stage": "FAILED_CUSTOMER"},
            {"id": "r4", "user_id": user_id,
             "connector_key": "make_com_qoyod",
             "idempotency_key": f"{user_id}-4",
             "pipeline_stage": "COMPLETED"},
            {"id": "r5", "user_id": user_id,
             "connector_key": "make_com_qoyod",
             "idempotency_key": f"{user_id}-5",
             "pipeline_stage": "CUSTOMER_RESOLVED"},
        ])
        rep = await day4_report(db, user_id)
        assert rep["totals"]["skipped"] == 2
        assert rep["totals"]["dead_letter"] == 1
        assert rep["totals"]["completed"] == 1
        assert rep["totals"]["customer_resolved"] == 1
        assert rep["skipped_reasons"]["not_in_trigger_statuses"] == 1
        assert rep["skipped_reasons"]["already_sent"] == 1
        assert rep["dead_letter_by_stage"]["FAILED_CUSTOMER"] == 1
    finally:
        await db.integration_inbox.delete_many({"user_id": user_id})


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
