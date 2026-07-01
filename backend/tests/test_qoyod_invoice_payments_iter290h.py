"""Iter-290h — POST /invoice_payments replaces standalone POST /receipts.

Production bug
──────────────
The previous flow called `POST /receipts`, which produced a Qoyod
Receipt that was NEVER linked to the invoice. Operator saw the
receipt in Qoyod's "غير مستعمل" (unallocated) bin and the invoice
remained with a non-zero balance — accounting could not close.

This test file locks in the corrected flow:
    Order → Invoice (POST /invoices) → Invoice Payment (POST /invoice_payments) → COMPLETED

with idempotency on `(order_id, invoice_id, payment_method, amount)`,
pre-POST guard on payment_method_id, and explicit failure stages
PAYMENT_LINK_FAILED + PAYMENT_METHOD_MAPPING_MISSING.
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
from integrations.qoyod.invoice_builder import (
    DryRunQoyodClient, build_invoice_payment_payload,
)
from integrations.qoyod.pipeline import process_customer_resolved_row
from integrations.qoyod.api_client import QoyodAPIError


# ─── HTTP-free production-mode client ────────────────────────────────
class _LiveLikeClient(DryRunQoyodClient):
    """Numeric ids so the int-coercion guard passes."""
    def _fake(self, kind: str, payload: dict) -> str:
        h = hashlib.sha1(repr(sorted(payload.items())).encode()).hexdigest()
        return str(int(h[:6], 16))


@pytest.fixture
def db():
    return AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]


# ─── Fixtures ────────────────────────────────────────────────────────
def _make_dto(order_id: str, payment_method: str = "mada"):
    return SalesOrderDTO(
        order_id=order_id, order_number=order_id,
        order_status="completed", order_status_native="تم التنفيذ",
        order_date=datetime.now(timezone.utc),
        completed_at=datetime.now(timezone.utc),
        currency="SAR", total_amount=115.0, tax_amount=15.0,
        customer=CustomerDTO(name="أحمد", phone="+966501234567"),
        items=[LineItemDTO(sku="SKU-1", name="X", quantity=1,
                           unit_price=100, tax_amount=15, total=115)],
        payment_method=payment_method,
    )


async def _seed_settings(db, user_id="main", *, with_mada=True,
                         dry_run_mode=False):
    pm_mapping = []
    if with_mada:
        pm_mapping.append({"salla_method": "mada", "qoyod_account_id": "9"})
    await db.qoyod_settings.update_one(
        {"user_id": user_id},
        {"$set": {
            "user_id":            user_id,
            "default_tax_id":     "1",
            "zero_tax_id":        "2",
            "default_account_id": "10",
            "default_inventory_id": "1",
            "default_product_category_id":   "1",
            "default_product_tax_id":        "1",
            "default_product_unit_type_id":  "1",
            "default_sales_account_id":      "17",
            "payment_method_mapping":        pm_mapping,
            "tax_mode":                       "customer_first",
            "auto_receipt":                   True,
            "capabilities":                   {"create_invoices": True,
                                               "create_receipts":  True},
            "dry_run_mode":                   dry_run_mode,
            "qoyod_api_key":                  "test-key",
            "invoice_total_policy":           "match_salla_total",
            "qoyod_tax_percent":              15,
            # Iter-001k — open Selective Send gate for legacy tests
            # + trigger status opt-in. Not production defaults.
            "production_writes_locked":       False,
            "selective_live_send_enabled":    True,
            "qoyod_enabled_invoice_trigger_statuses":
                ["completed", "تم التنفيذ",
                 "delivered", "shipping"],
            "qoyod_sync_start_date":          "2020-01-01",
        }}, upsert=True,
    )


async def _seed_row(db, *, user_id, order_id, dto=None):
    dto = dto or _make_dto(order_id)
    rid = uuid.uuid4().hex
    row = {
        "id": rid, "user_id": user_id, "trace_id": uuid.uuid4().hex,
        "connector_key": "make_com_qoyod", "source": "webhook",
        "received_at": datetime.now(timezone.utc),
        "raw_payload": {}, "raw_headers": {},
        "signature_status": "verified",
        "salla_order_id": order_id, "salla_order_number": order_id,
        "idempotency_key": f"iter290h-{order_id}-{uuid.uuid4().hex[:6]}",
        "pipeline_stage": "CUSTOMER_RESOLVED",
        "pipeline_error": None, "attempts": 0, "stage_history": [],
        "canonical_payload": dto.model_dump(mode="json"),
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
    return row


async def _cleanup(db, *, user_id="main", order_id=None):
    if order_id:
        await db.integration_inbox.delete_many({"salla_order_id": order_id})
        await db.qoyod_invoices.delete_many({"salla_order_id": order_id})
        await db.qoyod_invoice_payments.delete_many({"salla_order_id": order_id})
    await db.qoyod_products_mapping.delete_many({"user_id": user_id})


# ─── 1. Builder produces correct shape ───────────────────────────────
def test_build_invoice_payment_payload_shape():
    """Payload matches Qoyod apidoc Invoice Payments resource."""
    dto = {
        "order_id": "MZN-1", "order_number": "268784455",
        "total_amount": 134.0, "currency": "SAR",
        "payment_method": "mada",
    }
    settings = {
        "payment_method_mapping": [
            {"salla_method": "mada", "qoyod_account_id": "9"},
        ],
    }
    payload, fp = build_invoice_payment_payload(
        qoyod_invoice_id=55,
        dto_dict=dto,
        invoice_date=datetime(2026, 2, 28, tzinfo=timezone.utc),
        settings=settings,
    )
    body = payload["invoice_payment"]
    assert body["invoice_id"]        == 55
    assert body["amount"]            == 134.0
    assert body["date"]              == "2026-02-28"
    assert body["account_id"]        == 9
    assert body["reference"]         == "268784455"
    # Iter-290h.3 / 290h.6 — Sanity guards against past wire-name bugs.
    assert "payment_date" not in body
    assert "payment_method_id" not in body
    assert "account" not in body  # Iter-290h.6 — must be account_id
    # Idempotency fingerprint per user spec —
    # `order_id + invoice_id + payment_method + amount`.
    # The fingerprint uses INTERNAL logical names so historical DB
    # rows still match after the wire-name fix.
    assert fp == {
        "order_id":          "MZN-1",
        "qoyod_invoice_id":  55,
        "payment_method":    "mada",
        "payment_method_id": 9,
        "amount":            134.0,
    }


def test_build_invoice_payment_payload_returns_none_when_method_unmapped():
    """Pre-POST guard fodder: when settings don't map this method,
    `account_id` is None so the pipeline halts."""
    dto = {"order_id": "X", "total_amount": 50.0, "payment_method": "unknown"}
    settings = {"payment_method_mapping": []}
    payload, fp = build_invoice_payment_payload(
        qoyod_invoice_id=1, dto_dict=dto,
        invoice_date=datetime.now(timezone.utc), settings=settings,
    )
    assert payload["invoice_payment"]["account_id"] is None
    assert "account" not in payload["invoice_payment"]
    assert fp["payment_method_id"] is None


# ─── 2. Pipeline — happy path uses invoice_payment, not receipt ──────
@pytest.mark.asyncio
async def test_pipeline_happy_path_calls_invoice_payment_not_receipt(db):
    await ensure_qoyod_indexes(db)
    user_id = "main"; order_id = f"H-{uuid.uuid4().hex[:6]}"
    await _seed_settings(db, user_id)
    row = await _seed_row(db, user_id=user_id, order_id=order_id)

    posted_endpoints: list[str] = []

    class _Client(_LiveLikeClient):
        async def create_receipt(self, payload, *, idem):
            posted_endpoints.append("POST /receipts")
            return await super().create_receipt(payload, idem=idem)

        async def create_invoice_payment(self, payload, *, idem):
            posted_endpoints.append("POST /invoice_payments")
            return await super().create_invoice_payment(payload, idem=idem)

    try:
        out = await process_customer_resolved_row(db, row, api_client=_Client())
        assert out["outcome"] == "COMPLETED"
        assert "POST /invoice_payments" in posted_endpoints
        assert "POST /receipts" not in posted_endpoints, \
            "Iter-290h — must NEVER call /receipts in the new flow"
        # qoyod_invoice_payment_id persisted both on inbox + ledger.
        updated = await db.integration_inbox.find_one({"id": row["id"]})
        assert updated["pipeline_stage"] == "COMPLETED"
        assert updated["qoyod_invoice_payment_id"] is not None
        led = await db.qoyod_invoices.find_one({"salla_order_id": order_id})
        assert led["qoyod_invoice_payment_id"] is not None
        assert led["status"] == "sent"
        # Ledger ledger row created for DB-side idempotency.
        ip_led = await db.qoyod_invoice_payments.find_one(
            {"salla_order_id": order_id})
        assert ip_led is not None
        assert ip_led["payment_method"] == "mada"
        assert ip_led["amount"] == 115.0
        assert ip_led["payment_method_id"] == 9
    finally:
        await _cleanup(db, user_id=user_id, order_id=order_id)


# ─── 3. Missing payment_method_mapping caught by preflight ──────────
@pytest.mark.asyncio
async def test_missing_payment_method_mapping_caught_by_preflight(db):
    """When `payment_method_mapping` doesn't cover the Salla method,
    the existing preflight (Iter-285) catches it BEFORE invoice
    creation — the row lands in DEAD_LETTER with the explicit
    `payment_method_mapping_missing` error code. /invoice_payments is
    never called.

    The pipeline ALSO has a defense-in-depth PAYMENT_METHOD_MAPPING_MISSING
    stage that fires after INVOICE_CREATED for the rare race where
    settings change between preflight and the invoice_payment POST.
    Both layers carry the same error code so the operator sees a
    consistent message in the errors page."""
    await ensure_qoyod_indexes(db)
    user_id = "main"; order_id = f"PM-{uuid.uuid4().hex[:6]}"
    await _seed_settings(db, user_id, with_mada=False)
    row = await _seed_row(db, user_id=user_id, order_id=order_id)

    calls: list[str] = []

    class _Client(_LiveLikeClient):
        async def create_invoice(self, payload, *, idem):
            calls.append("invoice")
            return await super().create_invoice(payload, idem=idem)

        async def create_invoice_payment(self, payload, *, idem):
            calls.append("invoice_payment")
            return await super().create_invoice_payment(payload, idem=idem)

    try:
        out = await process_customer_resolved_row(db, row, api_client=_Client())
        assert out["outcome"] == "DEAD_LETTER"
        assert calls == [], (
            "preflight must catch missing mapping BEFORE any Qoyod POST"
            f"; calls={calls}")
        updated = await db.integration_inbox.find_one({"id": row["id"]})
        # The preflight failure carries `payment_method_mapping_missing`
        # somewhere — the operator/UI surfaces it as a clear cause.
        failures = (updated.get("pipeline_error") or {}) \
                     .get("preflight", {}).get("failures") or []
        codes = {f.get("code") for f in failures}
        assert "payment_method_mapping_missing" in codes
    finally:
        await _cleanup(db, user_id=user_id, order_id=order_id)


# ─── 4. Pre-POST guard — DB-side idempotency on fingerprint ──────────
@pytest.mark.asyncio
async def test_idempotent_short_circuit_when_payment_exists(db):
    """If `qoyod_invoice_payments` already has a row for
    (order_id, invoice_id, payment_method, amount), pipeline reuses it
    instead of double-posting."""
    await ensure_qoyod_indexes(db)
    user_id = "main"; order_id = f"IDEMP-{uuid.uuid4().hex[:6]}"
    await _seed_settings(db, user_id)
    row = await _seed_row(db, user_id=user_id, order_id=order_id)

    # Pre-seed the ledger as if a previous run already posted it.
    # invoice_id must match what build_invoice_payload returns for
    # the seeded DTO — we use _LiveLikeClient which returns deterministic
    # numeric ids, so first invoke once to discover the invoice id, then
    # pre-seed for the SECOND run. Simpler approach: pre-seed with a
    # known fake invoice_id stamped on the row before running.
    fake_invoice_id = 555
    fake_payment_id = "999"
    await db.integration_inbox.update_one(
        {"id": row["id"]},
        {"$set": {"qoyod_invoice_id": str(fake_invoice_id)}})
    await db.qoyod_invoice_payments.insert_one({
        "id":                       uuid.uuid4().hex,
        "user_id":                  user_id,
        "salla_order_id":           order_id,
        "qoyod_invoice_id":         fake_invoice_id,
        "payment_method":           "mada",
        "amount":                   115.0,
        "qoyod_invoice_payment_id": fake_payment_id,
        "created_at":               datetime.now(timezone.utc),
        "updated_at":               datetime.now(timezone.utc),
    })

    calls: list[str] = []

    class _Client(_LiveLikeClient):
        async def create_invoice_payment(self, payload, *, idem):
            calls.append("invoice_payment")
            return await super().create_invoice_payment(payload, idem=idem)

    try:
        row_fresh = await db.integration_inbox.find_one({"id": row["id"]})
        out = await process_customer_resolved_row(
            db, row_fresh, api_client=_Client())
        assert out["outcome"] == "COMPLETED"
        assert out["qoyod_invoice_payment_id"] == fake_payment_id
        # Critical — POST /invoice_payments was NOT called this run.
        assert calls == [], (
            f"idempotent short-circuit broken; api was called: {calls}")
        updated = await db.integration_inbox.find_one({"id": row["id"]})
        flag = (updated.get("qoyod_responses") or {}) \
                 .get("invoice_payment", {}) \
                 .get("idempotent_short_circuit")
        assert flag is True
    finally:
        await _cleanup(db, user_id=user_id, order_id=order_id)


# ─── 5. Failure path — POST /invoice_payments raises → PAYMENT_LINK_FAILED ──
@pytest.mark.asyncio
async def test_payment_link_failed_lands_in_partial_failure(db):
    await ensure_qoyod_indexes(db)
    user_id = "main"; order_id = f"PLF-{uuid.uuid4().hex[:6]}"
    await _seed_settings(db, user_id)
    row = await _seed_row(db, user_id=user_id, order_id=order_id)

    class _FlakyClient(_LiveLikeClient):
        async def create_invoice_payment(self, payload, *, idem):
            raise QoyodAPIError(
                status_code=422, code="qoyod_validation_error",
                message="invoice_id is invalid",
                response_excerpt='{"invoice_id":["is invalid"]}',
                endpoint="POST /invoice_payments",
            )

    try:
        out = await process_customer_resolved_row(
            db, row, api_client=_FlakyClient())
        assert out["outcome"] == "PARTIAL_FAILURE"
        assert out["reason"] == "PAYMENT_LINK_FAILED"
        updated = await db.integration_inbox.find_one({"id": row["id"]})
        assert updated["pipeline_stage"]    == "PARTIAL_FAILURE"
        assert updated["last_failed_stage"] == "PAYMENT_LINK_FAILED"
        # Iter-290h — request body + Qoyod response excerpt persisted
        # for the operator's "تشخيص الفشل" diagnostic.
        err = updated["qoyod_responses"]["invoice_payment"]["error"]
        assert err["request_body_json"]["invoice_payment"]["amount"] == 115.0
        assert "invoice_id" in (err.get("qoyod_response_excerpt") or "")
        # Ledger row reflects the partial state.
        led = await db.qoyod_invoices.find_one({"salla_order_id": order_id})
        assert led["status"] == "invoice_sent_payment_link_failed"
    finally:
        await _cleanup(db, user_id=user_id, order_id=order_id)


# ─── 6. No fallback to /receipts ─────────────────────────────────────
@pytest.mark.asyncio
async def test_no_fallback_to_receipts_when_invoice_payment_fails(db):
    """User directive — `لا يوجد fallback إلى Receipt إذا فشل invoice_payment`."""
    await ensure_qoyod_indexes(db)
    user_id = "main"; order_id = f"NOFB-{uuid.uuid4().hex[:6]}"
    await _seed_settings(db, user_id)
    row = await _seed_row(db, user_id=user_id, order_id=order_id)

    calls: list[str] = []

    class _Client(_LiveLikeClient):
        async def create_receipt(self, payload, *, idem):
            calls.append("receipt")
            return await super().create_receipt(payload, idem=idem)

        async def create_invoice_payment(self, payload, *, idem):
            calls.append("invoice_payment_attempt")
            raise QoyodAPIError(
                status_code=500, code="qoyod_server_error",
                message="boom", endpoint="POST /invoice_payments")

    try:
        out = await process_customer_resolved_row(db, row, api_client=_Client())
        assert out["outcome"] == "PARTIAL_FAILURE"
        # invoice_payment attempted, /receipts NEVER reached.
        assert calls == ["invoice_payment_attempt"]
    finally:
        await _cleanup(db, user_id=user_id, order_id=order_id)
