"""Day 4 — Qoyod Business Rules + Customer Resolution tests.

Day 4 scope ceiling:
    NORMALIZED → RULES_APPLIED → CUSTOMER_RESOLVED.

We MUST NOT touch products/invoice/receipt in Day 4 — these tests
also lock in that boundary.
"""
from __future__ import annotations

import os
import uuid
import asyncio
import pytest
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")

from motor.motor_asyncio import AsyncIOMotorClient

from integrations.qoyod.models import ensure_qoyod_indexes
from integrations.qoyod.dto import SalesOrderDTO, CustomerDTO, LineItemDTO
from integrations.qoyod.business_rules import (
    evaluate as evaluate_rules,
    ELIGIBLE, SKIP_NOT_IN_TRIGGER, SKIP_ALREADY_SENT,
)
from integrations.qoyod.customer_resolver import (
    resolve_customer, derive_lookup, _build_contact_payload,
    _extract_contact_id, ResolutionResult,
)
from integrations.qoyod.pipeline import (
    process_normalized_row, process_pending_normalized,
)


@pytest.fixture
def db():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    return client[os.environ["DB_NAME"]]


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────
def _dto(order_id: str = "ORD-1",
         status: str = "completed", status_native: str = "تم التنفيذ",
         completed_at: datetime | None = None,
         **overrides) -> SalesOrderDTO:
    now = datetime.now(timezone.utc)
    # Default fixture is a clean tax-exclusive order:
    #   items: 86.96 × 1 = 86.96 subtotal
    #   + tax 13.04 → total_amount 100.0
    # The header math reconciles (subtotal+tax = total), which keeps
    # the Iter-273 Totals Guard happy without each call-site needing
    # to spell out the numbers.
    base = dict(
        order_id=order_id,
        order_number=order_id,
        order_status=status,
        order_status_native=status_native,
        order_date=now - timedelta(days=1),
        completed_at=completed_at or now,
        subtotal=86.96,
        tax_amount=13.04,
        total_amount=100.0,
        currency="SAR",
        customer=CustomerDTO(name="أحمد", phone="+966501234567",
                             email="ahmed@example.com"),
        items=[LineItemDTO(sku="A-1", name="منتج", quantity=1,
                           unit_price=86.96, tax_amount=13.04, total=100.0)],
    )
    base.update(overrides)
    return SalesOrderDTO(**base)


# ─────────────────────────────────────────────────────────────────────
# A) Business Rules — pure
# ─────────────────────────────────────────────────────────────────────
def test_rules_eligible_when_status_matches_trigger():
    dto = _dto(status="completed")
    settings = {"invoice_trigger_statuses": ["completed"],
                "invoice_date_source": "trigger_status_date",
                "trigger_once_only": True}
    d = evaluate_rules(dto, settings)
    assert d.eligible is True
    assert d.reason == ELIGIBLE
    assert d.triggered_by_status == "completed"
    assert d.invoice_date is not None
    assert d.invoice_date_source == "completed_at"


def test_rules_not_eligible_when_status_outside_triggers():
    dto = _dto(status="shipped", status_native="تم الشحن")
    settings = {"invoice_trigger_statuses": ["completed"]}
    d = evaluate_rules(dto, settings)
    assert d.eligible is False
    assert d.reason == SKIP_NOT_IN_TRIGGER
    assert d.invoice_date is None


def test_rules_supports_multiple_trigger_statuses():
    # Merchants who want both "completed" and "delivered" to fire.
    dto = _dto(status="delivered", status_native="تم التوصيل")
    settings = {"invoice_trigger_statuses": ["completed", "delivered"]}
    d = evaluate_rules(dto, settings)
    assert d.eligible is True
    assert d.triggered_by_status == "delivered"


def test_rules_invoice_date_respects_explicit_paid_at_source():
    paid = datetime(2026, 6, 20, 10, 0, tzinfo=timezone.utc)
    dto = _dto(paid_at=paid, status="completed")
    settings = {"invoice_trigger_statuses": ["completed"],
                "invoice_date_source": "paid_at"}
    d = evaluate_rules(dto, settings)
    assert d.invoice_date == paid
    assert d.invoice_date_source == "paid_at"


def test_rules_invoice_date_falls_back_to_order_date_when_missing():
    """When completed_at is missing the trigger_status_date source
    falls back to order_date (and reports the actual source used)."""
    dto = _dto(status="completed", completed_at=None)
    # Force completed_at = None on the DTO (constructor sets a default)
    dto2 = dto.model_copy(update={"completed_at": None})
    settings = {"invoice_trigger_statuses": ["completed"],
                "invoice_date_source": "trigger_status_date"}
    d = evaluate_rules(dto2, settings)
    assert d.eligible is True
    assert d.invoice_date_source == "order_date"
    assert d.invoice_date == dto2.order_date


def test_rules_trigger_once_only_blocks_when_invoice_exists():
    dto = _dto(status="completed")
    settings = {"invoice_trigger_statuses": ["completed"],
                "trigger_once_only": True}
    existing = {"status": "sent"}
    d = evaluate_rules(dto, settings, existing_invoice_row=existing)
    assert d.eligible is False
    assert d.reason == SKIP_ALREADY_SENT


def test_rules_trigger_once_only_false_allows_resend():
    dto = _dto(status="completed")
    settings = {"invoice_trigger_statuses": ["completed"],
                "trigger_once_only": False}
    existing = {"status": "sent"}
    d = evaluate_rules(dto, settings, existing_invoice_row=existing)
    assert d.eligible is True


def test_rules_defaults_when_settings_blank():
    """No settings → defaults to ['completed'] + trigger_once_only=True."""
    dto = _dto(status="completed")
    d = evaluate_rules(dto, {})
    assert d.eligible is True


def test_rules_never_uses_paid_as_implicit_trigger():
    """Critical: order with status="paid" must NOT be auto-eligible
    unless the merchant explicitly opts into 'paid' as a trigger."""
    dto = _dto(status="paid", status_native="مدفوع")
    d = evaluate_rules(dto, {"invoice_trigger_statuses": ["completed"]})
    assert d.eligible is False
    assert d.reason == SKIP_NOT_IN_TRIGGER


# ─────────────────────────────────────────────────────────────────────
# B) Customer Resolver — pure helpers
# ─────────────────────────────────────────────────────────────────────
def test_derive_lookup_prefers_phone():
    c = CustomerDTO(name="أحمد", phone="+966501234567",
                    email="ahmed@example.com")
    key, kind = derive_lookup(c)
    assert key == "+966501234567"
    assert kind == "phone"


def test_derive_lookup_falls_back_to_email():
    c = CustomerDTO(name="أحمد", email="ahmed@example.com")
    key, kind = derive_lookup(c)
    assert key == "ahmed@example.com"
    assert kind == "email"


def test_derive_lookup_guest_when_no_phone_no_email():
    c = CustomerDTO(name="ضيف")
    key, kind = derive_lookup(c)
    assert key is None
    assert kind == "guest_order"


def test_contact_payload_only_includes_supported_fields():
    c = CustomerDTO(name="أحمد",
                    phone="+966501234567", email="x@y.com",
                    city="Riyadh", country="SA", is_guest=False)
    # Qoyod's legacy.qoyod.com uses `{"customer": {...}}` per the
    # 2026-06-26 endpoint audit. `_build_contact_payload` was renamed
    # internally but kept its Python name to minimise call-site churn.
    p = _build_contact_payload(c)["contact"]
    assert p["name"] == "أحمد"
    assert p["phone_number"] == "+966501234567"
    assert p["email"] == "x@y.com"
    assert p["city"] == "Riyadh"
    assert p["country"] == "SA"


def test_extract_contact_id_handles_wrapped_response():
    # New canonical shape on legacy.qoyod.com
    assert _extract_contact_id({"customer": {"id": 42}}) == "42"
    # Tolerant of the older `contact` alias for resilience.
    assert _extract_contact_id({"contact": {"id": 17}}) == "17"
    assert _extract_contact_id({"id": "abc"}) == "abc"
    assert _extract_contact_id({"customer_id": 7}) == "7"
    assert _extract_contact_id({"contact_id": 9}) == "9"
    assert _extract_contact_id(None) is None


# ─────────────────────────────────────────────────────────────────────
# C) Customer Resolver — live DB (mapping hit + create-new + guest)
# ─────────────────────────────────────────────────────────────────────
class _FakeAPIClient:
    """Records the contact payloads it received + returns a canned id."""
    def __init__(self, *, fail: bool = False, return_id: str = "QY-CONTACT-1"):
        self.fail = fail
        self.return_id = return_id
        self.calls: list[dict] = []

    async def create_contact(self, payload, *, idem):
        self.calls.append({"payload": payload, "idem": idem})
        if self.fail:
            from integrations.qoyod.api_client import QoyodAPIError
            raise QoyodAPIError(
                status_code=422, code="qoyod_validation_error",
                message="email already exists",
                response_excerpt="…",
                endpoint="POST /contacts",
            )
        return {"customer": {"id": self.return_id, "name": payload.get("customer", payload.get("contact", {})).get("name")}}


@pytest.mark.asyncio
async def test_resolver_returns_local_mapping_when_present(db):
    user_id = f"day4_local_{uuid.uuid4().hex[:8]}"
    try:
        await db.qoyod_customers_mapping.insert_one({
            "user_id": user_id, "lookup_key": "+966500000000",
            "lookup_kind": "phone", "qoyod_customer_id": "QY-LOCAL-9",
            "schema_version": 1,
        })
        c = CustomerDTO(name="موجود", phone="+966500000000")
        fake = _FakeAPIClient()
        res = await resolve_customer(db, user_id, c,
                                     trace_id="t-1", api_client=fake)
        assert res.success is True
        assert res.qoyod_customer_id == "QY-LOCAL-9"
        assert res.created_new is False
        assert fake.calls == []   # no API call needed
    finally:
        await db.qoyod_customers_mapping.delete_many({"user_id": user_id})


@pytest.mark.asyncio
async def test_resolver_creates_new_when_no_local_mapping(db):
    user_id = f"day4_create_{uuid.uuid4().hex[:8]}"
    try:
        c = CustomerDTO(name="جديد", phone="+966511111111")
        fake = _FakeAPIClient(return_id="QY-NEW-77")
        res = await resolve_customer(db, user_id, c,
                                     trace_id="t-2", api_client=fake)
        assert res.success is True
        assert res.qoyod_customer_id == "QY-NEW-77"
        assert res.created_new is True
        # Mapping persisted.
        m = await db.qoyod_customers_mapping.find_one(
            {"user_id": user_id, "lookup_key": "+966511111111"})
        assert m["qoyod_customer_id"] == "QY-NEW-77"
        # Idempotency key forwarded to Qoyod.
        assert fake.calls[0]["idem"].startswith("mzn-t-2-contact-phone-")
    finally:
        await db.qoyod_customers_mapping.delete_many({"user_id": user_id})


@pytest.mark.asyncio
async def test_resolver_returns_api_error_dict_on_failure(db):
    user_id = f"day4_apierr_{uuid.uuid4().hex[:8]}"
    try:
        c = CustomerDTO(name="فشل", phone="+966522222222")
        fake = _FakeAPIClient(fail=True)
        res = await resolve_customer(db, user_id, c,
                                     trace_id="t-3", api_client=fake)
        assert res.success is False
        assert res.error["code"] == "qoyod_validation_error"
        # No mapping persisted on failure.
        m = await db.qoyod_customers_mapping.find_one(
            {"user_id": user_id, "lookup_key": "+966522222222"})
        assert m is None
    finally:
        await db.qoyod_customers_mapping.delete_many({"user_id": user_id})


@pytest.mark.asyncio
async def test_resolver_guest_path_uses_default_customer_when_set(db):
    user_id = f"day4_guest_{uuid.uuid4().hex[:8]}"
    try:
        c = CustomerDTO(name="ضيف")
        res = await resolve_customer(
            db, user_id, c, trace_id="t-4",
            default_customer_id="QY-DEFAULT-100",
            api_client=_FakeAPIClient(),
        )
        assert res.success is True
        assert res.qoyod_customer_id == "QY-DEFAULT-100"
        assert res.created_new is False
        assert res.lookup_kind == "guest_order"
    finally:
        pass


@pytest.mark.asyncio
async def test_resolver_guest_path_fails_without_default(db):
    user_id = f"day4_noguest_{uuid.uuid4().hex[:8]}"
    c = CustomerDTO(name="ضيف")
    res = await resolve_customer(db, user_id, c, trace_id="t-5",
                                 api_client=_FakeAPIClient())
    assert res.success is False
    assert res.error["code"] == "missing_customer_data"


# ─────────────────────────────────────────────────────────────────────
# D) Pipeline orchestrator — NORMALIZED → CUSTOMER_RESOLVED
# ─────────────────────────────────────────────────────────────────────
async def _seed_normalized_row(db, *, user_id: str, order_id: str,
                               dto: SalesOrderDTO,
                               started_at: datetime | None = None) -> dict:
    row = {
        "id": uuid.uuid4().hex,
        "schema_version": 1,
        "user_id": user_id,
        "trace_id": uuid.uuid4().hex,
        "connector_key": "make_com_qoyod",
        "source": "webhook",
        "received_at": datetime.now(timezone.utc),
        "raw_payload": {"data": {}},
        "raw_headers": {},
        "signature_status": "verified",
        "salla_order_id": order_id,
        "salla_order_number": order_id,
        "idempotency_key": f"day4-{order_id}-{uuid.uuid4().hex[:6]}",
        "pipeline_stage": "NORMALIZED",
        "pipeline_error": None,
        "attempts": 0,
        "stage_history": [],
        "canonical_payload": dto.model_dump(mode="json"),
        "pipeline_started_at": started_at or datetime.now(timezone.utc),
        "last_success_stage": "NORMALIZED",
    }
    await db.integration_inbox.insert_one(row)
    return row


@pytest.mark.asyncio
async def test_pipeline_happy_path_advances_to_customer_resolved(db):
    await ensure_qoyod_indexes(db)
    user_id = "main"
    order_id = f"D4-HP-{uuid.uuid4().hex[:8]}"
    dto = _dto(order_id=order_id, status="completed")
    row = await _seed_normalized_row(db, user_id=user_id, order_id=order_id, dto=dto)
    fake = _FakeAPIClient(return_id="QY-HP-1")
    try:
        out = await process_normalized_row(db, row, api_client=fake)
        assert out["outcome"] == "CUSTOMER_RESOLVED"
        updated = await db.integration_inbox.find_one({"id": row["id"]})
        assert updated["pipeline_stage"] == "CUSTOMER_RESOLVED"
        assert updated["last_success_stage"] == "CUSTOMER_RESOLVED"
        assert updated["qoyod_customer_id"] == "QY-HP-1"
        stages = [h["to_stage"] for h in updated["stage_history"]]
        assert stages == ["RULES_APPLIED", "CUSTOMER_RESOLVED"]
        # Day-4 ceiling: NO products/invoice/receipt created.
        assert await db.qoyod_invoices.count_documents({
            "user_id": user_id, "salla_order_id": order_id}) == 0
    finally:
        await db.integration_inbox.delete_many({"id": row["id"]})
        await db.qoyod_customers_mapping.delete_many({"user_id": user_id})


@pytest.mark.asyncio
async def test_pipeline_skips_when_status_not_in_triggers(db):
    await ensure_qoyod_indexes(db)
    user_id = "main"
    order_id = f"D4-SKIP-{uuid.uuid4().hex[:8]}"
    dto = _dto(order_id=order_id, status="shipped", status_native="تم الشحن")
    row = await _seed_normalized_row(db, user_id=user_id, order_id=order_id, dto=dto)
    fake = _FakeAPIClient()
    try:
        out = await process_normalized_row(db, row, api_client=fake)
        assert out["outcome"] == "SKIPPED"
        assert out["reason"] == SKIP_NOT_IN_TRIGGER
        updated = await db.integration_inbox.find_one({"id": row["id"]})
        assert updated["pipeline_stage"] == "SKIPPED"
        # Terminal stage → finished_at + duration set, NO customer API call.
        assert updated["pipeline_finished_at"] is not None
        assert fake.calls == []
    finally:
        await db.integration_inbox.delete_many({"id": row["id"]})


@pytest.mark.asyncio
async def test_pipeline_routes_customer_failure_to_dead_letter(db):
    await ensure_qoyod_indexes(db)
    user_id = "main"
    order_id = f"D4-FAIL-{uuid.uuid4().hex[:8]}"
    # Customer has no phone AND no email → guest path → no default → fail.
    dto = _dto(order_id=order_id,
               customer=CustomerDTO(name="ضيف", is_guest=True))
    row = await _seed_normalized_row(db, user_id=user_id, order_id=order_id, dto=dto)
    fake = _FakeAPIClient()
    try:
        out = await process_normalized_row(db, row, api_client=fake)
        assert out["outcome"] == "DEAD_LETTER"
        updated = await db.integration_inbox.find_one({"id": row["id"]})
        assert updated["pipeline_stage"] == "DEAD_LETTER"
        assert updated["last_failed_stage"] == "FAILED_CUSTOMER"
        assert updated["pipeline_error"]["code"] == "missing_customer_data"
        # The RULES_APPLIED hop still happened (last_success_stage points there).
        assert updated["last_success_stage"] == "RULES_APPLIED"
        # Row preserved.
        assert await db.integration_inbox.count_documents({"id": row["id"]}) == 1
    finally:
        await db.integration_inbox.delete_many({"id": row["id"]})


@pytest.mark.asyncio
async def test_pipeline_trigger_once_only_blocks_resend(db):
    await ensure_qoyod_indexes(db)
    user_id = "main"
    order_id = f"D4-ONCE-{uuid.uuid4().hex[:8]}"
    try:
        # Seed an already-sent invoice row so the policy MUST short-circuit.
        await db.qoyod_invoices.insert_one({
            "user_id": user_id, "salla_order_id": order_id,
            "status": "sent", "trace_id": "prev"})
        dto = _dto(order_id=order_id, status="completed")
        row = await _seed_normalized_row(db, user_id=user_id, order_id=order_id, dto=dto)
        out = await process_normalized_row(db, row, api_client=_FakeAPIClient())
        assert out["outcome"] == "SKIPPED"
        assert out["reason"] == SKIP_ALREADY_SENT
    finally:
        await db.integration_inbox.delete_many({"salla_order_id": order_id})
        await db.qoyod_invoices.delete_many({"salla_order_id": order_id})


@pytest.mark.asyncio
async def test_pipeline_idempotent_on_already_advanced_rows(db):
    """Re-running the orchestrator on a CUSTOMER_RESOLVED row is a no-op."""
    await ensure_qoyod_indexes(db)
    user_id = "main"
    order_id = f"D4-IDEM-{uuid.uuid4().hex[:8]}"
    row = await _seed_normalized_row(
        db, user_id=user_id, order_id=order_id,
        dto=_dto(order_id=order_id, status="completed"))
    try:
        # Force-advance the row so it's NOT in NORMALIZED any more.
        await db.integration_inbox.update_one(
            {"id": row["id"]},
            {"$set": {"pipeline_stage": "CUSTOMER_RESOLVED"}})
        row["pipeline_stage"] = "CUSTOMER_RESOLVED"
        out = await process_normalized_row(db, row,
                                           api_client=_FakeAPIClient())
        assert out["skipped"] is True
        assert out["reason"] == "not_in_normalized_stage"
    finally:
        await db.integration_inbox.delete_many({"id": row["id"]})


@pytest.mark.asyncio
async def test_batch_processor_counts_outcomes(db):
    await ensure_qoyod_indexes(db)
    user_id = f"main"
    base = uuid.uuid4().hex[:6]
    ids = []
    try:
        # 1 eligible, 1 skip-not-trigger, 1 customer-fail
        eligible = await _seed_normalized_row(
            db, user_id=user_id, order_id=f"BATCH-{base}-OK",
            dto=_dto(order_id=f"BATCH-{base}-OK", status="completed"))
        skipped  = await _seed_normalized_row(
            db, user_id=user_id, order_id=f"BATCH-{base}-SK",
            dto=_dto(order_id=f"BATCH-{base}-SK", status="shipped",
                     status_native="تم الشحن"))
        failed   = await _seed_normalized_row(
            db, user_id=user_id, order_id=f"BATCH-{base}-DL",
            dto=_dto(order_id=f"BATCH-{base}-DL", status="completed",
                     customer=CustomerDTO(name="ضيف", is_guest=True)))
        ids = [eligible["id"], skipped["id"], failed["id"]]
        out = await process_pending_normalized(
            db, user_id, limit=10, api_client=_FakeAPIClient(return_id="QY-B-1"))
        assert out["counts"]["customer_resolved"] == 1
        assert out["counts"]["skipped"] == 1
        assert out["counts"]["dead_letter"] == 1
        assert out["processed"] == 3
    finally:
        await db.integration_inbox.delete_many({"id": {"$in": ids}})
        await db.qoyod_customers_mapping.delete_many({"user_id": user_id})


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
