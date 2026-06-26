"""Day 3 — Qoyod Webhook Reception Layer tests.

Scope (locked):
    1. Token verification
    2. Idempotency on (connector_key, idempotency_key)
    3. Raw event persistence
    4. Validation (cheap structural)
    5. Normalization (Salla → SalesOrderDTO)
    6. Dead-letter routing for failures in (4) or (5)

Out of scope (per user directive): business rules, Qoyod API output.
"""
from __future__ import annotations

import os
import json
import uuid
import asyncio
import pytest
from datetime import datetime, timezone
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")

# Force the token to a known value for tests BEFORE any module reads it.
os.environ.setdefault("QOYOD_WEBHOOK_TOKEN", "mzn_qoyod_dev_token_change_me")
TEST_TOKEN = os.environ["QOYOD_WEBHOOK_TOKEN"]

from motor.motor_asyncio import AsyncIOMotorClient

from integrations.qoyod.models import ensure_qoyod_indexes
from integrations.qoyod.normalizer import (
    validate, normalize, NormalizationError,
    normalize_phone, normalize_email, _canonical_status,
)
from integrations.qoyod.dto import SalesOrderDTO, CustomerDTO, LineItemDTO
from integrations.qoyod.webhook import (
    derive_idempotency_key, _verify_token, CONNECTOR_KEY,
)
from fastapi import HTTPException


@pytest.fixture
def db():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    return client[os.environ["DB_NAME"]]


# ─────────────────────────────────────────────────────────────────────
# Fixtures — sample Salla webhook payloads
# ─────────────────────────────────────────────────────────────────────
def _sample_salla_payload(order_id: str = "12345678", *,
                          status: str = "تم التنفيذ",
                          items: int = 2) -> dict:
    """Build a realistic Salla webhook body for tests."""
    # Salla's `id` is normally numeric but Make.com sometimes passes the
    # `reference_id` directly. We keep both as strings so the helper is
    # safe for arbitrary order tokens.
    return {
        "event": "order.updated",
        "data": {
            "id": order_id,
            "reference_id": order_id,
            "status": {"name": status, "customized": {"name": status}},
            "date": {"date": "2026-06-25 10:30:00"},
            "completed_at": "2026-06-25T11:00:00+03:00",
            "amounts": {
                "sub_total": {"amount": "200.00", "currency": "SAR"},
                "tax":       {"amount":  "30.00", "currency": "SAR"},
                "shipping":  {"amount":  "25.00", "currency": "SAR"},
                "discounts": {"amount":   "5.00", "currency": "SAR"},
                "total":     {"amount": "250.00", "currency": "SAR"},
            },
            "customer": {
                "first_name": "أحمد", "last_name": "السعدي",
                "mobile": "0501234567",
                "email": "Ahmed.S@example.com",
                "city": "Riyadh", "country": "SA",
                "is_guest": False,
            },
            "items": [
                {"sku": f"SKU-{i}",
                 "name": f"منتج {i}",
                 "quantity": 1,
                 "amounts": {
                     "price_without_tax": {"amount": f"{100 + i}.00", "currency": "SAR"},
                     "tax":               {"amount":  "15.00",         "currency": "SAR"},
                     "total":             {"amount": f"{115 + i}.00", "currency": "SAR"},
                 },
                 "product": {"id": 9000 + i, "sku": f"SKU-{i}", "name": f"منتج {i}"}}
                for i in range(items)
            ],
            "payment_method": "mada",
            "shipping_address": {
                "street": "الشارع 5", "city": "Riyadh",
                "country": "SA", "postal_code": "11564",
            },
        },
    }


# ─────────────────────────────────────────────────────────────────────
# A) Token verification dependency
# ─────────────────────────────────────────────────────────────────────
def test_verify_token_accepts_correct_value():
    assert _verify_token(x_webhook_token=TEST_TOKEN) is True


def test_verify_token_rejects_missing_header():
    with pytest.raises(HTTPException) as ei:
        _verify_token(x_webhook_token=None)
    assert ei.value.status_code == 401
    assert "missing" in ei.value.detail


def test_verify_token_rejects_wrong_value():
    with pytest.raises(HTTPException) as ei:
        _verify_token(x_webhook_token="definitely-not-the-real-token")
    assert ei.value.status_code == 401
    assert "invalid" in ei.value.detail


def test_verify_token_503_when_not_configured(monkeypatch):
    monkeypatch.delenv("QOYOD_WEBHOOK_TOKEN", raising=False)
    with pytest.raises(HTTPException) as ei:
        _verify_token(x_webhook_token="anything")
    assert ei.value.status_code == 503


# ─────────────────────────────────────────────────────────────────────
# B) Idempotency key derivation
# ─────────────────────────────────────────────────────────────────────
def test_idempotency_key_from_explicit_header():
    raw = _sample_salla_payload("999")
    key = derive_idempotency_key(raw, "explicit-test-key-42")
    assert key == "explicit-test-key-42"


def test_idempotency_key_derived_from_payload():
    raw = _sample_salla_payload("ABC-123")
    key = derive_idempotency_key(raw, None)
    assert key.startswith("salla:order:ABC-123:")
    assert "order.updated" in key


def test_idempotency_key_random_when_no_id():
    raw = {"event": "order.something", "data": {"items": []}}
    key = derive_idempotency_key(raw, None)
    # No `id` → derive_idempotency_key falls back to a random key
    # so insertion never silently collapses different events.
    assert key.startswith("salla:unknown:")


# ─────────────────────────────────────────────────────────────────────
# C) validate() — structural sanity
# ─────────────────────────────────────────────────────────────────────
def test_validate_happy_path():
    ok, err = validate(_sample_salla_payload("777"))
    assert ok is True
    assert err is None


def test_validate_rejects_non_object():
    ok, err = validate("not a dict")
    assert ok is False
    assert err["code"] == "invalid_payload_type"


def test_validate_rejects_missing_data():
    ok, err = validate({"event": "x"})
    assert ok is False
    assert err["code"] == "missing_data_object"


def test_validate_rejects_missing_order_id():
    raw = _sample_salla_payload("X")
    raw["data"].pop("id"); raw["data"].pop("reference_id")
    ok, err = validate(raw)
    assert ok is False
    assert err["code"] == "missing_order_id"


def test_validate_rejects_missing_status():
    raw = _sample_salla_payload("X")
    raw["data"]["status"] = None
    ok, err = validate(raw)
    assert ok is False
    assert err["code"] == "missing_order_status"


def test_validate_rejects_missing_items_key():
    raw = _sample_salla_payload("X")
    raw["data"].pop("items")
    ok, err = validate(raw)
    assert ok is False
    assert err["code"] == "missing_items"


def test_validate_rejects_empty_items_list():
    raw = _sample_salla_payload("X", items=0)
    ok, err = validate(raw)
    assert ok is False
    assert err["code"] == "empty_items"


# ─────────────────────────────────────────────────────────────────────
# D) normalize() — Salla → SalesOrderDTO mapping
# ─────────────────────────────────────────────────────────────────────
def test_normalize_builds_complete_dto():
    raw = _sample_salla_payload("ORD-001", status="تم التنفيذ", items=2)
    dto = normalize(raw)
    assert isinstance(dto, SalesOrderDTO)
    # Identity
    assert dto.order_id == "ORD-001"
    assert dto.order_status == "completed"
    assert dto.order_status_native == "تم التنفيذ"
    # Money
    assert dto.currency == "SAR"
    assert dto.total_amount == 250.00
    assert dto.tax_amount == 30.00
    assert dto.shipping_amount == 25.00
    # Customer
    assert dto.customer.name == "أحمد السعدي"
    assert dto.customer.phone == "+966501234567"   # normalised
    assert dto.customer.email == "ahmed.s@example.com"
    # Items
    assert len(dto.items) == 2
    assert all(isinstance(it, LineItemDTO) for it in dto.items)
    assert dto.items[0].sku == "SKU-0"
    assert dto.items[0].product_id == "9000"
    # Payment
    assert dto.payment_method == "mada"
    # Provenance
    assert dto.metadata["source"] == "salla"
    assert dto.metadata["source_event"] == "order.updated"


def test_normalize_canonical_status_table_covers_arabic_inputs():
    pairs = [
        ("تم التنفيذ", "completed"),
        ("تم التوصيل", "delivered"),
        ("تم الشحن",   "shipped"),
        ("ملغي",       "cancelled"),
        ("مسترجع",     "refunded"),
    ]
    for native, expected in pairs:
        assert _canonical_status(native) == expected


def test_normalize_phone_handles_local_and_international():
    assert normalize_phone("0501234567")        == "+966501234567"
    assert normalize_phone("+966 50 123 4567")  == "+966501234567"
    assert normalize_phone("966501234567")      == "+966501234567"
    assert normalize_phone("00966501234567")    == "+966501234567"
    assert normalize_phone(None)                is None
    assert normalize_phone("")                  is None


def test_normalize_email_lowercases_and_validates():
    assert normalize_email("Ahmed.S@Example.COM") == "ahmed.s@example.com"
    assert normalize_email("not-an-email")        is None
    assert normalize_email("")                    is None
    assert normalize_email(None)                  is None


def test_normalize_raises_on_corrupt_items():
    raw = _sample_salla_payload("X")
    raw["data"]["items"] = ["not-a-dict"]
    with pytest.raises(NormalizationError) as ei:
        normalize(raw)
    assert ei.value.code == "invalid_item_shape"


def test_normalize_records_received_at_in_metadata():
    raw = _sample_salla_payload("Y")
    when = datetime(2026, 6, 25, 10, 0, 0, tzinfo=timezone.utc)
    dto = normalize(raw, received_at=when)
    assert dto.metadata["received_at"].startswith("2026-06-25T10:00:00")


def test_dto_schema_version_locked_at_1():
    dto = normalize(_sample_salla_payload("X"))
    assert dto.schema_version == 1


# ─────────────────────────────────────────────────────────────────────
# E) End-to-end — webhook insert + pipeline orchestration
# ─────────────────────────────────────────────────────────────────────
async def _call_webhook(db, payload, *, idem_header=None, token=TEST_TOKEN):
    """Drive the webhook flow without spinning up the HTTP server,
    by calling the helper functions directly. The HTTP-level tests
    are covered by integration-with-curl in the smoke phase."""
    from integrations.qoyod.webhook import (
        _process_inbox_row, derive_idempotency_key, _capture_headers,
        CONNECTOR_KEY,
    )
    from integrations.qoyod.state_machine import initial_history_entry
    # Token check (mimics the dependency)
    _verify_token(x_webhook_token=token)
    tenant = "main"
    trace_id = uuid.uuid4().hex
    now = datetime.now(timezone.utc)
    idem_key = derive_idempotency_key(payload, idem_header)
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    salla_order_id = data.get("reference_id") or data.get("id") or data.get("order_id")
    if salla_order_id is not None:
        salla_order_id = str(salla_order_id)
    row_id = uuid.uuid4().hex
    row = {
        "id": row_id, "schema_version": 1, "user_id": tenant,
        "trace_id": trace_id, "connector_key": CONNECTOR_KEY,
        "source": "webhook", "received_at": now,
        "raw_payload": payload, "raw_headers": {},
        "signature_status": "verified",
        "salla_order_id": salla_order_id,
        "salla_order_number": str(data.get("reference_id") or data.get("id") or "") or None,
        "idempotency_key": idem_key,
        "pipeline_stage": "NEW", "pipeline_error": None,
        "attempts": 0, "next_retry_at": None, "processed_at": None,
        "canonical_payload": None,
        "stage_history": [initial_history_entry(actor="webhook",
                                                note=f"trace_id={trace_id}")],
    }
    try:
        await db.integration_inbox.insert_one(row)
    except Exception:
        # Duplicate path — return whatever's there now.
        return {"duplicate": True, "trace_id": None, "idempotency_key": idem_key}
    final_stage, err = await _process_inbox_row(db, row=row, raw_payload=payload)
    return {
        "duplicate": False, "trace_id": trace_id,
        "idempotency_key": idem_key, "row_id": row_id,
        "final_stage": final_stage, "error": err,
    }


@pytest.mark.asyncio
async def test_webhook_happy_path_completes_normalization(db):
    await ensure_qoyod_indexes(db)
    user_id = "main"
    payload = _sample_salla_payload(f"E2E-{uuid.uuid4().hex[:8]}")
    res = await _call_webhook(db, payload)
    try:
        assert res["final_stage"] == "NORMALIZED"
        assert res["error"] is None
        row = await db.integration_inbox.find_one({"id": res["row_id"]})
        assert row["pipeline_stage"] == "NORMALIZED"
        # Audit trail fields populated.
        assert row["pipeline_started_at"] is not None
        assert row["last_success_stage"] == "NORMALIZED"
        # Stage history records every hop (NEW + 3 transitions).
        stages = [h["to_stage"] for h in row["stage_history"]]
        assert stages == ["NEW", "RECEIVED", "VALIDATED", "NORMALIZED"]
        # Canonical DTO present + matches the input.
        assert row["canonical_payload"]["order_status"] == "completed"
        assert row["salla_order_id"] == payload["data"]["reference_id"]
    finally:
        await db.integration_inbox.delete_many({"trace_id": res["trace_id"]})


@pytest.mark.asyncio
async def test_webhook_idempotency_returns_existing_row(db):
    await ensure_qoyod_indexes(db)
    payload = _sample_salla_payload(f"IDEM-{uuid.uuid4().hex[:8]}")
    res1 = await _call_webhook(db, payload)
    try:
        assert res1["final_stage"] == "NORMALIZED"

        # Second call with the SAME payload → row count must stay 1.
        res2 = await _call_webhook(db, payload)
        assert res2["duplicate"] is True

        count = await db.integration_inbox.count_documents(
            {"idempotency_key": res1["idempotency_key"]})
        assert count == 1
    finally:
        await db.integration_inbox.delete_many({"trace_id": res1["trace_id"]})


@pytest.mark.asyncio
async def test_webhook_validation_failure_routes_to_dead_letter(db):
    await ensure_qoyod_indexes(db)
    bad = _sample_salla_payload("BADV-1")
    bad["data"].pop("status")   # break validation
    res = await _call_webhook(db, bad)
    try:
        assert res["final_stage"] == "DEAD_LETTER"
        assert res["error"]["code"] == "missing_order_status"
        row = await db.integration_inbox.find_one({"id": res["row_id"]})
        assert row["pipeline_stage"] == "DEAD_LETTER"
        assert row["last_failed_stage"] == "FAILED_VALIDATION"
        assert row["pipeline_finished_at"] is not None
        # Canonical payload must NOT be present.
        assert row.get("canonical_payload") in (None, {})
        # Row is preserved — NOT deleted (per user directive).
        assert await db.integration_inbox.count_documents({"id": res["row_id"]}) == 1
        stages = [h["to_stage"] for h in row["stage_history"]]
        assert "FAILED_VALIDATION" in stages
        assert stages[-1] == "DEAD_LETTER"
    finally:
        await db.integration_inbox.delete_many({"trace_id": res["trace_id"]})


@pytest.mark.asyncio
async def test_webhook_normalization_failure_routes_to_dead_letter(db):
    await ensure_qoyod_indexes(db)
    bad = _sample_salla_payload("BADN-1")
    # Passes structural validation but normalization will choke on item shape.
    bad["data"]["items"] = ["not-an-object"]
    res = await _call_webhook(db, bad)
    try:
        assert res["final_stage"] == "DEAD_LETTER"
        row = await db.integration_inbox.find_one({"id": res["row_id"]})
        assert row["pipeline_stage"] == "DEAD_LETTER"
        assert row["last_failed_stage"] == "FAILED_NORMALIZATION"
        # Validation succeeded → last_success_stage stops at VALIDATED.
        assert row["last_success_stage"] == "VALIDATED"
        # No canonical payload was persisted.
        assert row.get("canonical_payload") in (None, {})
    finally:
        await db.integration_inbox.delete_many({"trace_id": res["trace_id"]})


@pytest.mark.asyncio
async def test_webhook_audit_trail_records_duration(db):
    await ensure_qoyod_indexes(db)
    payload = _sample_salla_payload(f"AUD-{uuid.uuid4().hex[:8]}")
    res = await _call_webhook(db, payload)
    try:
        row = await db.integration_inbox.find_one({"id": res["row_id"]})
        # Day 3 happy path ends at NORMALIZED (not a terminal stage),
        # so finished_at + duration MUST stay None — only DEAD_LETTER /
        # COMPLETED / SKIPPED fill those.
        assert row["pipeline_stage"] == "NORMALIZED"
        assert row.get("pipeline_finished_at") is None
        assert row.get("pipeline_duration_ms") is None
        # But started_at MUST be set (NEW → RECEIVED).
        assert row["pipeline_started_at"] is not None
    finally:
        await db.integration_inbox.delete_many({"trace_id": res["trace_id"]})


@pytest.mark.asyncio
async def test_webhook_audit_trail_records_duration_on_dead_letter(db):
    await ensure_qoyod_indexes(db)
    bad = _sample_salla_payload("AUD-DL-1")
    bad["data"].pop("status")
    res = await _call_webhook(db, bad)
    try:
        row = await db.integration_inbox.find_one({"id": res["row_id"]})
        assert row["pipeline_stage"] == "DEAD_LETTER"
        assert row["pipeline_finished_at"] is not None
        # duration_ms is set because we passed existing_started_at on the
        # second hop (FAILED_VALIDATION → DEAD_LETTER).
        assert row.get("pipeline_duration_ms") is not None
        assert row["pipeline_duration_ms"] >= 0
    finally:
        await db.integration_inbox.delete_many({"trace_id": res["trace_id"]})


@pytest.mark.asyncio
async def test_webhook_does_not_call_qoyod_or_apply_business_rules(db):
    """Strict Day-3 scope check. Even on the happy path the row must
    NOT have any of these downstream fields populated."""
    await ensure_qoyod_indexes(db)
    payload = _sample_salla_payload(f"SCOPE-{uuid.uuid4().hex[:8]}")
    res = await _call_webhook(db, payload)
    try:
        row = await db.integration_inbox.find_one({"id": res["row_id"]})
        assert row.get("qoyod_invoice_row_id") in (None, "")
        # No qoyod_invoices row written at all.
        cnt = await db.qoyod_invoices.count_documents({
            "user_id": "main",
            "salla_order_id": row["salla_order_id"],
        })
        assert cnt == 0
        # last_success_stage tops out at NORMALIZED (no RULES_APPLIED).
        assert row["last_success_stage"] == "NORMALIZED"
    finally:
        await db.integration_inbox.delete_many({"trace_id": res["trace_id"]})


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
