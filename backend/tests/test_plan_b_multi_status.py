"""Multi-status support for Plan-B pending page (2026-07-08).

User directive: page must offer three tabs — تم التنفيذ (completed),
جاري التوصيل (in_delivery), تم التوصيل (delivered). Only rows in the
selected status are returned. Floor date + already-sent + no-Salla-
date filters still apply. No automatic sending — the operator hits
"إرسال إلى قيود" per row.

Coverage:
    S1  status="completed" returns only completed rows (default).
    S2  status="delivered" returns only delivered rows.
    S3  status="in_delivery" matches both canonical fallback slug and
        the native Arabic string "جاري التوصيل".
    S4  Unknown/blank `status` param falls back to "completed" — no
        500, no cross-status leak.
    S5  send.py refuses a row whose status is NOT in the supported
        set with `not_completed` (kept the code name for backward
        compat with existing UI).
    S6  send.py ACCEPTS a delivered/in_delivery row for manual send
        (i.e. the manual page can push any of the three statuses).
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import mongomock_motor
import pytest

from integrations.qoyod_manual.pending import (
    list_pending_orders, _matches_status, SUPPORTED_STATUSES,
)
from integrations.qoyod_manual.send import (
    manual_send_one, ManualSendRefused,
)


TENANT = "main"


@pytest.fixture
def db():
    return mongomock_motor.AsyncMongoMockClient()["test_multi_status"]


def _row(order_number: str, *, canonical_status: str,
         native_status: str, order_date: str = "2026-07-05"):
    return {
        "id":                 f"row-{order_number}",
        "user_id":            TENANT,
        "trace_id":           f"tr-{order_number}",
        "salla_order_number": order_number,
        "received_at":        datetime.now(timezone.utc),
        "pipeline_stage":     "NORMALIZED",
        "canonical_payload": {
            "order_number":         order_number,
            "order_id":             order_number,
            "order_date":           order_date,
            "created_at":           order_date,
            "order_status":         canonical_status,
            "order_status_native":  native_status,
            "total_amount":         100.0,
            "subtotal":             100.0,
            "shipping_amount":      0.0,
            "cod_fee_amount":       0.0,
            "currency":             "SAR",
            "payment_method":       "credit_card",
            "payment_method_native": "credit_card",
            "customer": {"name": "T", "phone": "+966500000000"},
            "items": [{"sku": "S", "name": "s", "quantity": 1,
                        "unit_price": 100.0, "total": 100.0}],
        },
        "raw_payload": {"data": {"created_at": order_date}},
    }


# ────────────────────────────────────────────────────────────────────
# Matcher unit tests (fast, no DB).
# ────────────────────────────────────────────────────────────────────
def test_matcher_completed_native_and_canonical():
    assert _matches_status(_row("A", canonical_status="completed",
                                  native_status="تم التنفيذ"), "completed")
    assert _matches_status(_row("A", canonical_status="",
                                  native_status="تم التنفيذ"), "completed")
    assert not _matches_status(_row("A", canonical_status="delivered",
                                     native_status="تم التوصيل"), "completed")


def test_matcher_delivered():
    assert _matches_status(_row("A", canonical_status="delivered",
                                  native_status="تم التوصيل"), "delivered")
    assert not _matches_status(_row("A", canonical_status="completed",
                                     native_status="تم التنفيذ"), "delivered")


def test_matcher_in_delivery_native_and_slug():
    # Native Arabic
    assert _matches_status(_row("A", canonical_status="",
                                  native_status="جاري التوصيل"), "in_delivery")
    # Canonical fallback slug (normalizer emits this when no explicit
    # mapping exists — "جاري التوصيل" → "جاري_التوصيل")
    assert _matches_status(_row("A", canonical_status="جاري_التوصيل",
                                  native_status=""), "in_delivery")


def test_supported_statuses_is_tuple():
    assert isinstance(SUPPORTED_STATUSES, tuple)
    assert set(SUPPORTED_STATUSES) == {"completed", "delivered",
                                        "in_delivery"}


# ────────────────────────────────────────────────────────────────────
# S1 / S2 / S3 — pending list filters per-tab.
# ────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_pending_list_completed_tab_only(db):
    await db.integration_inbox.insert_many([
        _row("O-COMP-1", canonical_status="completed",
             native_status="تم التنفيذ"),
        _row("O-DEL-1", canonical_status="delivered",
             native_status="تم التوصيل"),
        _row("O-IND-1", canonical_status="",
             native_status="جاري التوصيل"),
    ])
    result = await list_pending_orders(
        db, user_id=TENANT, days=365, limit=100, status="completed")
    orders = {o["order_number"] for o in result["orders"]}
    assert orders == {"O-COMP-1"}
    assert result["status"] == "completed"
    assert set(result["supported_statuses"]) == {
        "completed", "delivered", "in_delivery"}


@pytest.mark.asyncio
async def test_pending_list_delivered_tab_only(db):
    await db.integration_inbox.insert_many([
        _row("O-COMP-2", canonical_status="completed",
             native_status="تم التنفيذ"),
        _row("O-DEL-2", canonical_status="delivered",
             native_status="تم التوصيل"),
        _row("O-IND-2", canonical_status="",
             native_status="جاري التوصيل"),
    ])
    result = await list_pending_orders(
        db, user_id=TENANT, days=365, limit=100, status="delivered")
    orders = {o["order_number"] for o in result["orders"]}
    assert orders == {"O-DEL-2"}


@pytest.mark.asyncio
async def test_pending_list_in_delivery_tab(db):
    await db.integration_inbox.insert_many([
        _row("O-COMP-3", canonical_status="completed",
             native_status="تم التنفيذ"),
        _row("O-DEL-3", canonical_status="delivered",
             native_status="تم التوصيل"),
        _row("O-IND-3", canonical_status="",
             native_status="جاري التوصيل"),
        # Extra row using the fallback slug shape.
        _row("O-IND-4", canonical_status="جاري_التوصيل",
             native_status=""),
    ])
    result = await list_pending_orders(
        db, user_id=TENANT, days=365, limit=100, status="in_delivery")
    orders = {o["order_number"] for o in result["orders"]}
    assert orders == {"O-IND-3", "O-IND-4"}


# ────────────────────────────────────────────────────────────────────
# S4 — Unknown status falls back to "completed" (defensive).
# ────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_unknown_status_falls_back_to_completed(db):
    await db.integration_inbox.insert_many([
        _row("O-COMP-4", canonical_status="completed",
             native_status="تم التنفيذ"),
        _row("O-DEL-4", canonical_status="delivered",
             native_status="تم التوصيل"),
    ])
    result = await list_pending_orders(
        db, user_id=TENANT, days=365, limit=100, status="banana")
    orders = {o["order_number"] for o in result["orders"]}
    assert orders == {"O-COMP-4"}
    assert result["status"] == "completed"


# ────────────────────────────────────────────────────────────────────
# S5 / S6 — manual_send_one accepts all three statuses.
# ────────────────────────────────────────────────────────────────────
async def _seed_creds_and_settings(db):
    await db.qoyod_settings.insert_one({
        "user_id":                    TENANT,
        "qoyod_tax_percent":          15,
        "default_inventory_id":       1, "default_branch_id":      1,
        "default_product_category_id": 1, "default_product_tax_id": 1,
        "default_sales_account_id":    100,
        "default_product_unit_type_id": 1,
        "payment_method_mapping": [
            {"salla_method": "credit_card", "qoyod_account_id": "42",
             "posting_mode": "paid_receipt"},
        ],
    })
    from integrations.qoyod.credentials import save_api_key
    await save_api_key(db, TENANT, "test-key")


@pytest.mark.asyncio
async def test_send_refuses_unsupported_status(db):
    await _seed_creds_and_settings(db)
    # Status = "pending" (NOT supported)
    row = _row("O-BAD", canonical_status="pending",
                native_status="قيد المعالجة")
    await db.integration_inbox.insert_one(row)
    with pytest.raises(ManualSendRefused) as exc:
        await manual_send_one(db, user_id=TENANT, order_number="O-BAD")
    assert exc.value.code == "not_completed"
    # Message mentions ALL three allowed statuses (readable RCA).
    assert "تم التنفيذ" in exc.value.message
    assert "جاري التوصيل" in exc.value.message
    assert "تم التوصيل" in exc.value.message


@pytest.mark.asyncio
@pytest.mark.parametrize("cstatus,nstatus", [
    ("completed",   "تم التنفيذ"),
    ("delivered",   "تم التوصيل"),
    ("جاري_التوصيل", "جاري التوصيل"),
])
async def test_send_accepts_all_three_statuses(db, cstatus, nstatus):
    await _seed_creds_and_settings(db)
    row = _row(f"O-{cstatus}", canonical_status=cstatus,
                native_status=nstatus)
    await db.integration_inbox.insert_one(row)

    async def _no_inv(*_a, **_k): return None
    async def _find_cust(*_a, **_k): return [{"id": 1}]
    async def _find_prod(*_a, **_k): return {"id": 22, "sku": "S"}
    async def _create_invoice(payload, *, idem):
        return {"invoice": {"id": 111, "number": "INV-111",
                             "reference": payload["invoice"]["reference"]}}
    async def _create_payment(payload, *, idem):
        return {"invoice_payment": {"id": 222}}

    with patch("integrations.qoyod_manual.client.ManualQoyodClient."
               "find_invoice_by_reference", new=AsyncMock(side_effect=_no_inv)), \
         patch("integrations.qoyod_manual.client.ManualQoyodClient."
               "find_customers_by_phone", new=AsyncMock(side_effect=_find_cust)), \
         patch("integrations.qoyod_manual.client.ManualQoyodClient."
               "find_product_by_sku", new=AsyncMock(side_effect=_find_prod)), \
         patch("integrations.qoyod_manual.client.ManualQoyodClient."
               "create_invoice", new=AsyncMock(side_effect=_create_invoice)), \
         patch("integrations.qoyod_manual.client.ManualQoyodClient."
               "create_invoice_payment", new=AsyncMock(side_effect=_create_payment)):
        result = await manual_send_one(
            db, user_id=TENANT, order_number=f"O-{cstatus}")
    assert result["ok"] is True
    assert result["invoice_id"] == 111
