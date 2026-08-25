"""Pytest for Plan-B Manual Send (/app/backend/integrations/qoyod_manual).

Coverage:
    T1  pending-orders applies the requested range, status, and exact refs.
    T2  send guard G1 — refuses when inbox row already has
        manual_qoyod_invoice_id.
    T3  send guard G4 — refuses when payment method has no mapping.
    T4  send guard G2 — refuses when Salla total ≠ built total (>0.01).
    T5  send happy path — customer/product/invoice/payment created,
        markers persisted, lock finalised.
    T6  worker._one_round respects legacy_pipeline_frozen=True.
"""
from __future__ import annotations

import uuid
from datetime import datetime, date, timedelta, timezone
from unittest.mock import AsyncMock, patch

import mongomock_motor
import pytest

from integrations.qoyod_manual.pending import list_pending_orders
from integrations.qoyod_manual.send import (
    manual_send_one, ManualSendRefused,
    _preflight_qoyod_invoice_payload, _find_historical_positive_canon,
)
from integrations.qoyod.worker import _one_round


TENANT = "main"


def _invoice_payload(*, discount=0, unit_price=100, tax_percent=15):
    return {
        "invoice": {
            "line_items": [{
                "quantity": 1,
                "unit_price": unit_price,
                "discount": discount,
                "tax_percent": tax_percent,
            }],
        },
    }


def test_qoyod_preflight_applies_qoyod_rounding_before_write():
    payload = {"invoice": {"line_items": [
        {
            "quantity": 1,
            "unit_price": 100,
            "discount": 24.169,
            "tax_percent": 15,
        }
        for _ in range(3)
    ]}}

    with pytest.raises(ManualSendRefused) as exc:
        _preflight_qoyod_invoice_payload(
            payload,
            salla_total=261.63,
        )

    assert exc.value.code == "qoyod_preflight_total_mismatch"
    assert exc.value.extra["qoyod_write_performed"] is False


def test_qoyod_preflight_rejects_more_than_one_halalah_difference():
    with pytest.raises(ManualSendRefused) as exc:
        _preflight_qoyod_invoice_payload(
            _invoice_payload(discount=0, unit_price=100, tax_percent=0),
            salla_total=100.02,
        )

    assert exc.value.code == "qoyod_preflight_total_mismatch"
    assert exc.value.extra["difference"] == -0.02
    assert exc.value.extra["qoyod_write_performed"] is False


def test_qoyod_preflight_keeps_one_halalah_tolerance():
    result = _preflight_qoyod_invoice_payload(
        _invoice_payload(discount=0, unit_price=100, tax_percent=0),
        salla_total=100.01,
    )

    assert result["difference"] == -0.01


@pytest.fixture
def db():
    import mongomock_motor
    client = mongomock_motor.AsyncMongoMockClient()
    _db = client["test_plan_b"]
    return _db


def _inbox_row(*, order_number: str, order_date: str = "2026-07-05",
               status: str = "completed", total: float = 100.0,
               sku: str = "SKU-1", payment_method: str = "credit_card",
               with_manual_id: bool = False,
               with_legacy_id: bool = False):
    row_id = f"row-{uuid.uuid4().hex[:8]}"
    row = {
        "id":                 row_id,
        "user_id":            TENANT,
        "trace_id":           f"tr-{uuid.uuid4().hex[:8]}",
        "salla_order_number": order_number,
        "received_at":        datetime.now(timezone.utc),
        "pipeline_stage":     "NORMALIZED",
        "canonical_payload": {
            "order_number":  order_number,
            "order_id":      order_number,
            "order_date":    order_date,
            "created_at":    order_date,
            "order_status":  status,
            "order_status_native": (
                "تم التنفيذ" if status == "completed" else "قيد المعالجة"),
            "total_amount":  total,
            "subtotal":      total,
            "shipping_amount": 0.0,
            "tax_amount":    0.0,
            "discount_amount": 0.0,
            "cod_fee_amount": 0.0,
            "currency":      "SAR",
            "payment_method":         payment_method,
            "payment_method_native":  payment_method,
            "customer": {
                "name":  "عميل تجريبي",
                "phone": "+966500000000",
                "email": "test@example.com",
            },
            "items": [
                {"sku": sku, "name": "منتج تجريبي",
                 "quantity": 1, "unit_price": total,
                 "tax_amount": 0.0, "discount_amount": 0.0,
                 "total": total},
            ],
        },
        "raw_payload": {"data": {"created_at": order_date}},
    }
    if with_manual_id:
        row["manual_qoyod_invoice_id"] = "999"
    if with_legacy_id:
        row["qoyod_invoice_id"] = "555"
    return row


def _unified_candidate_row(
    *, order_number: str, order_date: str | None = "2026-07-05",
    status: str = "completed", total: float = 100.0,
) -> dict:
    row = {
        "user_id": TENANT,
        "order_id": order_number,
        "order_number": order_number,
        "order_status": status,
        "order_status_slug": status,
        "order_status_native": (
            "تم التنفيذ" if status == "completed" else "قيد المعالجة"
        ),
        "payment_method": "credit_card",
        "payment_status": "paid",
        "payment_collection_status": "paid",
        "paid_amount": total,
        "remaining_amount": 0,
        "has_remaining_amount": False,
        "total_amount": total,
        "currency": "SAR",
    }
    if order_date is not None:
        row["order_date"] = order_date
    return row


@pytest.mark.asyncio
async def test_manual_sender_accepts_shipping_as_in_delivery_until_next_guard(db):
    """The report's canonical `shipping` slug must pass the sender status gate.

    We intentionally omit the payment mapping so the next guard proves that
    status itself was accepted without performing any Qoyod write.
    """
    row = _inbox_row(
        order_number="SHIP-ELIGIBLE",
        status="shipping",
    )
    row["canonical_payload"]["order_status_native"] = "جاري التوصيل"
    await db.integration_inbox.insert_one(row)

    with pytest.raises(ManualSendRefused) as exc:
        await manual_send_one(
            db,
            user_id=TENANT,
            order_number="SHIP-ELIGIBLE",
        )

    assert exc.value.code == "payment_method_unmapped"


@pytest.mark.asyncio
async def test_manual_sender_accepts_delivering_as_in_delivery_until_next_guard(db):
    row = _inbox_row(
        order_number="DELIVERING-ELIGIBLE",
        status="delivering",
    )
    row["canonical_payload"]["order_status_native"] = "جاري التوصيل"
    await db.integration_inbox.insert_one(row)

    with pytest.raises(ManualSendRefused) as exc:
        await manual_send_one(
            db,
            user_id=TENANT,
            order_number="DELIVERING-ELIGIBLE",
        )

    assert exc.value.code == "payment_method_unmapped"


@pytest.mark.asyncio
async def test_manual_sender_rejects_shipped_outside_three_status_policy(db):
    row = _inbox_row(
        order_number="SHIPPED-BLOCKED",
        status="shipped",
    )
    row["canonical_payload"]["order_status_native"] = "تم الشحن"
    await db.integration_inbox.insert_one(row)

    with pytest.raises(ManualSendRefused) as exc:
        await manual_send_one(
            db,
            user_id=TENANT,
            order_number="SHIPPED-BLOCKED",
        )

    assert exc.value.code == "not_completed"


async def _seed_settings(db, *, payment_methods_mapped: bool = True):
    doc = {
        "user_id":                    TENANT,
        "qoyod_tax_percent":          15,
        "default_inventory_id":       1,
        "default_branch_id":          1,
        "default_product_category_id": 1,
        "default_product_tax_id":     1,
        "default_sales_account_id":   100,
        "default_product_unit_type_id": 1,
        "payment_method_mapping": (
            [{"salla_method": "credit_card", "qoyod_account_id": "42",
              "posting_mode": "paid_receipt"}]
            if payment_methods_mapped else []
        ),
    }
    await db.qoyod_settings.insert_one(doc)


async def _seed_credentials(db):
    # Store a decryptable ciphertext.
    from integrations.qoyod.credentials import save_api_key
    await save_api_key(db, TENANT, "test-api-key-xyz")


# ────────────────────────────────────────────────────────────────────
# T1 — pending-orders filters
# ────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_pending_orders_filters(db):
    await db.unified_orders.insert_many([
        _unified_candidate_row(order_number="ORD-1"),
        _unified_candidate_row(
            order_number="ORD-2", order_date="2026-06-30"
        ),
        _unified_candidate_row(order_number="ORD-3", status="pending"),
        _unified_candidate_row(order_number="ORD-4"),
        _unified_candidate_row(order_number="ORD-5"),
    ])
    await db.qoyod_invoices.insert_many([
        {
            "user_id": TENANT,
            "qoyod_invoice_id": "999",
            "reference": "ORD-4",
            "raw_response": {"reference": "ORD-4"},
        },
        {
            "user_id": TENANT,
            "qoyod_invoice_id": "555",
            "reference": "ORD-5",
            "raw_response": {"reference": "ORD-5"},
        },
    ])

    result = await list_pending_orders(
        db, user_id=TENANT, from_date="2026-07-01",
        to_date="2026-08-22", limit=100)
    order_numbers = {o["order_number"] for o in result["orders"]}
    assert order_numbers == {"ORD-1"}, order_numbers
    assert result["counts"]["excluded_pre_floor"] == 0
    assert result["counts"]["excluded_outside_requested_period"] >= 1
    # NB: after the 2026-07-09 fix, the Salla-status filter runs
    # Mongo-side (BEFORE limit + BEFORE Python loop), so "pending"
    # rows never bump the `excluded_not_completed` counter — they're
    # already filtered out of the cursor. Verified via the negative
    # assertion below (ORD-3 must NOT appear in the results).
    assert "ORD-3" not in order_numbers
    assert result["counts"]["excluded_already_sent"] >= 2


# ────────────────────────────────────────────────────────────────────
# T2 — G1: already-sent short-circuit (revised 2026-07-09)
# already_sent now requires BOTH manual_qoyod_invoice_id AND
# manual_qoyod_payment_id. If only the invoice marker exists, the
# send is routed to the payment-only retry branch, NOT refused.
# See test_plan_b_payment_path_fix.py::test_already_sent_requires_both_markers
# for the retry-branch coverage.
# ────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_send_refuses_when_already_sent(db):
    await _seed_settings(db)
    await _seed_credentials(db)
    row = _inbox_row(order_number="A100", with_manual_id=True)
    row["manual_qoyod_payment_id"] = "8888"  # both markers → refuse
    await db.integration_inbox.insert_one(row)
    with pytest.raises(ManualSendRefused) as exc:
        await manual_send_one(db, user_id=TENANT, order_number="A100")
    assert exc.value.code == "already_sent"


@pytest.mark.asyncio
async def test_send_reads_fresh_owner_inbox_row_after_salla_resync(db):
    """The live Salla refresh writes under the merchant owner, not ``main``."""
    owner_id = "merchant-owner-1"
    row = _inbox_row(order_number="A100-OWNER", with_manual_id=True)
    row["user_id"] = owner_id
    row["manual_qoyod_payment_id"] = "8888"
    await db.integration_inbox.insert_one(row)

    with pytest.raises(ManualSendRefused) as exc:
        await manual_send_one(
            db,
            user_id=TENANT,
            orders_user_id=owner_id,
            order_number="A100-OWNER",
        )

    assert exc.value.code == "already_sent"


# ────────────────────────────────────────────────────────────────────
# T3 — G4: payment method unmapped
# ────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_send_refuses_when_payment_method_unmapped(db):
    await _seed_settings(db, payment_methods_mapped=False)
    await _seed_credentials(db)
    await db.integration_inbox.insert_one(
        _inbox_row(order_number="A101"))
    with pytest.raises(ManualSendRefused) as exc:
        await manual_send_one(db, user_id=TENANT, order_number="A101")
    assert exc.value.code == "payment_method_unmapped"


# ────────────────────────────────────────────────────────────────────
# T4 — G2: totals mismatch (Salla total misaligned with items)
# ────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_send_refuses_on_totals_mismatch(db):
    await _seed_settings(db)
    await _seed_credentials(db)
    row = _inbox_row(order_number="A102", total=200.0)
    # Break totals: Salla total says 200 but item total is only 50.
    row["canonical_payload"]["items"][0]["total"] = 50.0
    row["canonical_payload"]["items"][0]["unit_price"] = 50.0
    await db.integration_inbox.insert_one(row)

    async def _fake_find_inv_by_ref(*args, **kw):
        return None

    async def _fake_find_cust_by_phone(*args, **kw):
        return [{"id": 11}]

    async def _fake_find_prod_by_sku(*args, **kw):
        return {"id": 22, "sku": "SKU-1"}

    with patch("integrations.qoyod_manual.client.ManualQoyodClient."
               "find_invoice_by_reference",
               new=AsyncMock(side_effect=_fake_find_inv_by_ref)), \
         patch("integrations.qoyod_manual.client.ManualQoyodClient."
               "find_customers_by_phone",
               new=AsyncMock(side_effect=_fake_find_cust_by_phone)), \
         patch("integrations.qoyod_manual.client.ManualQoyodClient."
               "find_product_by_sku",
               new=AsyncMock(side_effect=_fake_find_prod_by_sku)):
        with pytest.raises(ManualSendRefused) as exc:
            await manual_send_one(
                db, user_id=TENANT, order_number="A102")
    assert exc.value.code == "totals_mismatch", exc.value.code


# ────────────────────────────────────────────────────────────────────
# T5 — happy path
# ────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_send_happy_path_creates_invoice_and_payment(db):
    await _seed_settings(db)
    await _seed_credentials(db)
    await db.integration_inbox.insert_one(
        _inbox_row(order_number="A200", total=115.0, sku="SKU-A"))

    # Salla total = 115. With tax_percent=15, target_net = 100 exactly,
    # so the invoice math lands on 115 → guard passes.

    calls = {"invoice": 0, "payment": 0, "customer": 0, "product": 0}

    async def _find_inv(*_a, **_k):
        return None

    async def _find_cust_phone(*_a, **_k):
        return [{"id": 33}]

    async def _find_prod(*_a, **_k):
        return {"id": 77, "sku": "SKU-A"}

    async def _create_invoice(payload, *, idem):
        calls["invoice"] += 1
        return {"invoice": {"id": 501, "number": "INV-501",
                             "reference": payload["invoice"]["reference"],
                             "total": 115.0}}

    async def _create_payment(payload, *, idem):
        calls["payment"] += 1
        return {"invoice_payment": {"id": 8001}}

    async def _create_customer(payload, *, idem):
        calls["customer"] += 1
        return {"contact": {"id": 33}}

    async def _create_product(payload, *, idem):
        calls["product"] += 1
        return {"product": {"id": 77}}

    with patch("integrations.qoyod_manual.client.ManualQoyodClient."
               "find_invoice_by_reference",
               new=AsyncMock(side_effect=_find_inv)), \
         patch("integrations.qoyod_manual.client.ManualQoyodClient."
               "find_customers_by_phone",
               new=AsyncMock(side_effect=_find_cust_phone)), \
         patch("integrations.qoyod_manual.client.ManualQoyodClient."
               "find_product_by_sku",
               new=AsyncMock(side_effect=_find_prod)), \
         patch("integrations.qoyod_manual.client.ManualQoyodClient."
               "create_customer",
               new=AsyncMock(side_effect=_create_customer)), \
         patch("integrations.qoyod_manual.client.ManualQoyodClient."
               "create_product",
               new=AsyncMock(side_effect=_create_product)), \
         patch("integrations.qoyod_manual.client.ManualQoyodClient."
               "create_invoice",
               new=AsyncMock(side_effect=_create_invoice)), \
         patch("integrations.qoyod_manual.client.ManualQoyodClient."
               "create_invoice_payment",
               new=AsyncMock(side_effect=_create_payment)):
        result = await manual_send_one(
            db, user_id=TENANT, order_number="A200")

    assert result["ok"] is True
    assert result["invoice_id"] == 501
    assert result["payment_id"] == 8001
    assert calls["invoice"] == 1
    assert calls["payment"] == 1
    # Product already existed → find returned a hit → no create.
    assert calls["product"] == 0
    # Customer was matched by phone → no create.
    assert calls["customer"] == 0

    # Verify markers on inbox row.
    row = await db.integration_inbox.find_one({"salla_order_number": "A200"})
    assert row["manual_qoyod_invoice_id"] == "501"
    assert row["manual_qoyod_payment_id"] == "8001"
    assert row["manual_send_last_status"] == "succeeded"

    # Lock finalised as succeeded.
    lock = await db.qoyod_manual_send_locks.find_one(
        {"order_number": "A200"})
    assert lock["status"] == "succeeded"
    assert lock["manual_qoyod_invoice_id"] == "501"

    # Second send attempt should be refused with already_sent.
    with pytest.raises(ManualSendRefused) as exc:
        await manual_send_one(db, user_id=TENANT, order_number="A200")
    assert exc.value.code == "already_sent"


# ────────────────────────────────────────────────────────────────────
# T5b — COD invoice-only success must return cleanly to auto-send.
#
# Regression for order 273714881: Qoyod accepted the invoice and the
# send lock was finalised as succeeded, but the success response then
# referenced an out-of-scope `payment_method`.  That late NameError made
# auto-send trip its circuit breaker even though the external write had
# already succeeded.
# ────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_cod_invoice_only_success_returns_without_late_exception(db):
    await _seed_settings(db)
    await _seed_credentials(db)
    await db.integration_inbox.insert_one(
        _inbox_row(
            order_number="273714881",
            total=133.73,
            sku="SKU-COD",
            payment_method="cash_on_delivery",
        )
    )

    payment_post = AsyncMock()

    with patch(
        "integrations.qoyod_manual.client.ManualQoyodClient."
        "find_invoice_by_reference",
        new=AsyncMock(return_value=None),
    ), patch(
        "integrations.qoyod_manual.client.ManualQoyodClient."
        "find_customers_by_phone",
        new=AsyncMock(return_value=[{"id": 33}]),
    ), patch(
        "integrations.qoyod_manual.client.ManualQoyodClient."
        "find_product_by_sku",
        new=AsyncMock(return_value={"id": 77, "sku": "SKU-COD"}),
    ), patch(
        "integrations.qoyod_manual.client.ManualQoyodClient."
        "create_invoice",
        new=AsyncMock(
            return_value={
                "invoice": {
                    "id": 865,
                    "number": "INV-865",
                    "reference": "273714881",
                    "total": 133.73,
                }
            }
        ),
    ), patch(
        "integrations.qoyod_manual.client.ManualQoyodClient."
        "create_invoice_payment",
        new=payment_post,
    ):
        result = await manual_send_one(
            db,
            user_id=TENANT,
            order_number="273714881",
            actor="auto-plan-b:test",
        )

    assert result["ok"] is True
    assert result["invoice_id"] == 865
    assert result["payment_id"] is None
    assert result["invoice_only"] is True
    assert result["payment_method"] == "cash_on_delivery"
    payment_post.assert_not_awaited()

    lock = await db.qoyod_manual_send_locks.find_one(
        {"order_number": "273714881"}
    )
    assert lock["status"] == "succeeded"


# ────────────────────────────────────────────────────────────────────
# T6 — legacy pipeline frozen kill-switch
# ────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_worker_respects_frozen_flag(db):
    await db.qoyod_settings.insert_one({
        "user_id": TENANT,
        "legacy_pipeline_frozen": True,
    })
    result = await _one_round(db, user_id=TENANT, batch_limit=10)
    assert result["status"] == "legacy_pipeline_frozen"
    assert result["processed"] == 0


@pytest.mark.asyncio
async def test_worker_runs_when_not_frozen(db):
    await db.qoyod_settings.insert_one({
        "user_id": TENANT,
        "legacy_pipeline_frozen": False,
    })
    # Should NOT short-circuit — will proceed to backfill_gate and
    # normalized/customer_resolved processors (which return empty
    # for an empty inbox). We just verify the frozen key is absent.
    result = await _one_round(db, user_id=TENANT, batch_limit=10)
    assert "frozen" not in result
    assert result["normalized"]["processed"] == 0


# ────────────────────────────────────────────────────────────────────
# T8 — Quantisation: all money fields sent to Qoyod are 2-decimals
# T9 — send_date is Riyadh-today (NOT Salla order_created_at)
# T10 — payment amount == expected_total (invoice will close to zero)
# ────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_send_quantises_and_uses_riyadh_send_date(db):
    """Order with a fractional-total that would produce >2dp values
    if we didn't quantise. We assert:
        * unit_price / discount / tax_percent / expected_total are all
          exactly 2-decimal.
        * invoice.issue_date == today in Asia/Riyadh (NOT Salla date).
        * payment.amount == expected_total AND payment.date == send_date.
    """
    from datetime import timedelta as _td
    RIYADH = timezone(_td(hours=3))
    riyadh_today = datetime.now(RIYADH).date().isoformat()

    await _seed_settings(db)
    await _seed_credentials(db)

    # Salla row with weird total that produces non-2dp intermediates
    # (e.g. 143.75 with tax 15% → net 125 exactly; but pick an amount
    # that would drift without quantise: 137.63).
    row = _inbox_row(order_number="Q900", total=137.63, sku="SKU-Q")
    # Deliberately give the item a mismatched unit_price+total combo
    # so the builder must compute a discount.
    row["canonical_payload"]["items"][0]["unit_price"] = 137.63
    row["canonical_payload"]["items"][0]["total"] = 137.63
    # Salla order_date is 2026-07-05 — well BEFORE any realistic send.
    row["canonical_payload"]["order_date"] = "2026-07-05"
    row["canonical_payload"]["created_at"] = "2026-07-05"
    row["raw_payload"] = {"data": {"created_at": "2026-07-05"}}
    await db.integration_inbox.insert_one(row)

    captured: dict = {}

    async def _find_inv(*_a, **_k):
        return None

    async def _find_cust_phone(*_a, **_k):
        return [{"id": 44}]

    async def _find_prod(*_a, **_k):
        return {"id": 88, "sku": "SKU-Q"}

    async def _create_invoice(payload, *, idem):
        captured["invoice_payload"] = payload
        return {"invoice": {"id": 601, "number": "INV-601",
                             "reference": payload["invoice"]["reference"],
                             "total": 137.63}}

    async def _create_payment(payload, *, idem):
        captured["payment_payload"] = payload
        return {"invoice_payment": {"id": 9001}}

    with patch("integrations.qoyod_manual.client.ManualQoyodClient."
               "find_invoice_by_reference",
               new=AsyncMock(side_effect=_find_inv)), \
         patch("integrations.qoyod_manual.client.ManualQoyodClient."
               "find_customers_by_phone",
               new=AsyncMock(side_effect=_find_cust_phone)), \
         patch("integrations.qoyod_manual.client.ManualQoyodClient."
               "find_product_by_sku",
               new=AsyncMock(side_effect=_find_prod)), \
         patch("integrations.qoyod_manual.client.ManualQoyodClient."
               "create_invoice",
               new=AsyncMock(side_effect=_create_invoice)), \
         patch("integrations.qoyod_manual.client.ManualQoyodClient."
               "create_invoice_payment",
               new=AsyncMock(side_effect=_create_payment)):
        result = await manual_send_one(
            db, user_id=TENANT, order_number="Q900")

    assert result["ok"] is True
    assert result["send_date"] == riyadh_today, \
        f"send_date must be Riyadh today, got {result['send_date']}"
    assert result["send_date"] != "2026-07-05", \
        "send_date must NOT be the Salla order_created_at"

    inv = captured["invoice_payload"]["invoice"]
    # Invoice-level dates use send_date, not Salla date.
    assert inv["issue_date"] == riyadh_today
    assert inv["due_date"] == riyadh_today
    # Every monetary field in EVERY line is exactly 2dp.
    for line in inv["line_items"]:
        for k in ("unit_price", "discount", "tax_percent"):
            v = line[k]
            # Convert to Decimal to inspect the exponent.
            from decimal import Decimal as _D
            d = _D(str(v))
            # exp is -2 (=2 decimals) or larger (0.1 -> -1 also OK if
            # value is a whole 10th, but our _q2 always emits xx.xx).
            assert -d.as_tuple().exponent <= 2, \
                f"{k}={v} has >2 decimals"

    # Payment amount == expected_total AND is 2dp.
    pay = captured["payment_payload"]["invoice_payment"]
    assert pay["amount"] == result["expected_total"], \
        (f"payment.amount={pay['amount']} must equal "
         f"expected_total={result['expected_total']}")
    assert pay["date"] == riyadh_today
    from decimal import Decimal as _D
    assert -_D(str(pay["amount"])).as_tuple().exponent <= 2

    # Invoice will close to zero → status Paid, remaining 0.00.
    remaining = round(result["expected_total"] - pay["amount"], 2)
    assert remaining == 0.0, \
        f"invoice must close to zero; remaining={remaining}"


@pytest.mark.asyncio
async def test_plan_b_wire_contract_preserves_send_date_fixed_vat_payment_mapping_and_idempotency(
    db,
):
    """Pin the existing Plan-B invoice/payment wire contract end to end."""
    riyadh_today = datetime.now(timezone(timedelta(hours=3))).date().isoformat()
    order_number = "WIRE-CONTRACT-900"

    await _seed_settings(db)
    await _seed_credentials(db)

    row = _inbox_row(
        order_number=order_number,
        order_date="2026-07-05",
        total=115.0,
        sku="SKU-WIRE",
    )
    # Salla reports 8%, but the established Qoyod policy is a fixed
    # Saudi VAT-inclusive 15% without increasing the collected gross.
    row["raw_payload"] = {
        "data": {
            "reference_id": order_number,
            "created_at": "2026-07-05",
            "amounts": {
                "tax": {
                    "percent": "8.00",
                    "amount": {"amount": 8.52, "currency": "SAR"},
                },
                "sub_total": {"amount": 106.48, "currency": "SAR"},
                "total": {"amount": 115.0, "currency": "SAR"},
            },
        },
    }
    await db.integration_inbox.insert_one(row)

    captured: dict = {}

    async def _create_invoice(payload, *, idem):
        captured["invoice_payload"] = payload
        captured["invoice_idem"] = idem
        return {
            "invoice": {
                "id": 6901,
                "number": "INV-6901",
                "reference": order_number,
                "total": 115.0,
            },
        }

    async def _create_payment(payload, *, idem):
        captured["payment_payload"] = payload
        captured["payment_idem"] = idem
        return {"invoice_payment": {"id": 7901}}

    with patch(
        "integrations.qoyod_manual.client.ManualQoyodClient."
        "find_invoice_by_reference",
        new=AsyncMock(return_value=None),
    ), patch(
        "integrations.qoyod_manual.client.ManualQoyodClient."
        "find_customers_by_phone",
        new=AsyncMock(return_value=[{"id": 44}]),
    ), patch(
        "integrations.qoyod_manual.client.ManualQoyodClient."
        "find_product_by_sku",
        new=AsyncMock(return_value={"id": 88, "sku": "SKU-WIRE"}),
    ), patch(
        "integrations.qoyod_manual.client.ManualQoyodClient.create_invoice",
        new=AsyncMock(side_effect=_create_invoice),
    ), patch(
        "integrations.qoyod_manual.client.ManualQoyodClient."
        "create_invoice_payment",
        new=AsyncMock(side_effect=_create_payment),
    ):
        result = await manual_send_one(
            db,
            user_id=TENANT,
            order_number=order_number,
        )

    invoice = captured["invoice_payload"]["invoice"]
    payment = captured["payment_payload"]["invoice_payment"]

    assert invoice["issue_date"] == riyadh_today
    assert invoice["due_date"] == riyadh_today
    assert invoice["issue_date"] != "2026-07-05"
    assert invoice["reference"] == order_number
    assert invoice["currency_code"] == "SAR"
    assert all(line["tax_percent"] == 15.0 for line in invoice["line_items"])

    assert result["salla_total"] == 115.0
    assert result["expected_total"] == 115.0
    assert result["payment_amount"] == 115.0
    assert payment["invoice_id"] == 6901
    assert payment["amount"] == 115.0
    assert payment["date"] == riyadh_today
    assert payment["account_id"] == 42
    assert payment["reference"] == order_number
    assert captured["invoice_idem"] == f"inv-{order_number}"
    assert captured["payment_idem"] == f"pay-{order_number}"

    second_invoice_post = AsyncMock()
    second_payment_post = AsyncMock()
    with patch(
        "integrations.qoyod_manual.client.ManualQoyodClient.create_invoice",
        new=second_invoice_post,
    ), patch(
        "integrations.qoyod_manual.client.ManualQoyodClient."
        "create_invoice_payment",
        new=second_payment_post,
    ):
        with pytest.raises(ManualSendRefused) as exc:
            await manual_send_one(
                db,
                user_id=TENANT,
                order_number=order_number,
            )

    assert exc.value.code == "already_sent"
    second_invoice_post.assert_not_awaited()
    second_payment_post.assert_not_awaited()


# ────────────────────────────────────────────────────────────────────
# T11 — _q2 helper (unit)
# ────────────────────────────────────────────────────────────────────
def test_q2_quantises_half_up():
    from integrations.qoyod_manual.send import _q2
    assert _q2(0) == 0.0
    assert _q2(None) == 0.0
    assert _q2(1) == 1.0
    # ROUND_HALF_UP: 1.005 → 1.01 (Python's default banker rounding
    # would give 1.00 with round(); we assert we DON'T do that).
    assert _q2(1.005) == 1.01
    assert _q2("2.345") == 2.35
    # Long-tail float that _q2 must strip cleanly.
    assert _q2(0.1 + 0.2) == 0.30


# ────────────────────────────────────────────────────────────────────
# T12 — Explicit requested-date boundary (no hidden rollout floor).
#
#   • 2026-06-30 → EXCLUDED
#   • 2026-07-01 → INCLUDED
#   • 2026-07-05 → INCLUDED
#   • no Salla date at all → EXCLUDED (never promoted via received_at)
# ────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_requested_date_boundary_strict(db):
    # Order that MUST be excluded (2026-06-30) — reproduces the bug
    # reported for order 268552119.
    row_before = _unified_candidate_row(
        order_number="268552119", order_date="2026-06-30"
    )
    await db.unified_orders.insert_one(row_before)

    # Exactly ON the floor — MUST be included.
    row_boundary = _unified_candidate_row(
        order_number="ORDER-JUL1", order_date="2026-07-01"
    )
    await db.unified_orders.insert_one(row_boundary)

    # After floor — MUST be included.
    row_after = _unified_candidate_row(
        order_number="ORDER-JUL5", order_date="2026-07-05"
    )
    await db.unified_orders.insert_one(row_after)

    # No Salla date at all — MUST be excluded even if received_at is
    # today (post-floor). Simulates a webhook that arrived after
    # 2026-07-01 for an order that was actually created much earlier
    # but whose Salla date fields are missing / malformed.
    row_no_date = _unified_candidate_row(
        order_number="ORDER-NO-DATE", order_date=None, total=10.0
    )
    await db.unified_orders.insert_one(row_no_date)

    result = await list_pending_orders(
        db, user_id=TENANT, from_date="2026-07-01",
        to_date="2026-08-22", limit=100)
    order_numbers = {o["order_number"] for o in result["orders"]}

    # Pre-floor order must NOT appear.
    assert "268552119" not in order_numbers, (
        f"268552119 leaked into pending list: {order_numbers}")
    # No-Salla-date row must NOT appear.
    assert "ORDER-NO-DATE" not in order_numbers, order_numbers
    # Boundary + post-floor rows must appear.
    assert "ORDER-JUL1" in order_numbers, order_numbers
    assert "ORDER-JUL5" in order_numbers, order_numbers

    # Counters expose the distinct exclusion reasons.
    assert result["counts"]["excluded_pre_floor"] == 0
    assert result["counts"]["excluded_outside_requested_period"] >= 1
    assert result["counts"]["excluded_no_salla_date"] >= 1


@pytest.mark.asyncio
async def test_send_refuses_pre_floor_order(db):
    """Direct POST /send/{order_number} for a pre-floor row must be
    refused with `before_floor_date` — belt-and-braces so the guard
    fires even if a caller bypasses the pending listing."""
    await _seed_settings(db)
    await _seed_credentials(db)
    row = _inbox_row(order_number="268552119",
                     order_date="2026-06-30")
    await db.integration_inbox.insert_one(row)
    with pytest.raises(ManualSendRefused) as exc:
        await manual_send_one(
            db, user_id=TENANT, order_number="268552119")
    assert exc.value.code == "before_floor_date"


@pytest.mark.asyncio
async def test_send_refuses_when_no_salla_date(db):
    await _seed_settings(db)
    await _seed_credentials(db)
    row = {
        "id":                 "no-date-2",
        "user_id":            TENANT,
        "trace_id":           "tr-nd-2",
        "salla_order_number": "ORDER-NO-DATE-2",
        "received_at":        datetime.now(timezone.utc),
        "pipeline_stage":     "NORMALIZED",
        "canonical_payload": {
            "order_number":  "ORDER-NO-DATE-2",
            "order_status":  "completed",
            "total_amount":  10.0,
            "currency":      "SAR",
            "payment_method": "credit_card",
            "customer": {"name": "ت"},
            "items": [{"sku": "S", "name": "s", "quantity": 1,
                        "unit_price": 10.0, "total": 10.0}],
        },
        "raw_payload": {},
    }
    await db.integration_inbox.insert_one(row)
    with pytest.raises(ManualSendRefused) as exc:
        await manual_send_one(
            db, user_id=TENANT, order_number="ORDER-NO-DATE-2")
    assert exc.value.code == "no_salla_order_date"


@pytest.mark.asyncio
async def test_confirmed_recovery_can_continue_without_salla_date(db):
    """The bounded recovery route may skip only the missing-date guard."""
    await _seed_settings(db)
    await _seed_credentials(db)
    row = _inbox_row(
        order_number="ORDER-NO-DATE-RECOVERY",
        with_manual_id=True,
    )
    row["manual_qoyod_payment_id"] = "8888"
    row["canonical_payload"].pop("order_date", None)
    row["canonical_payload"].pop("created_at", None)
    row["raw_payload"] = {}
    await db.integration_inbox.insert_one(row)

    with pytest.raises(ManualSendRefused) as exc:
        await manual_send_one(
            db,
            user_id=TENANT,
            order_number="ORDER-NO-DATE-RECOVERY",
            allow_missing_salla_order_date=True,
        )

    # Reaching the duplicate guard proves the missing-date guard was skipped
    # without creating an invoice or payment.
    assert exc.value.code == "already_sent"


@pytest.mark.asyncio
async def test_recovery_selects_complete_positive_historical_payload(db):
    historical = _inbox_row(order_number="ORDER-ZERO-LIVE", total=170.83)
    historical["received_at"] = (
        datetime.now(timezone.utc) - timedelta(minutes=1)
    )
    await db.integration_inbox.insert_one(historical)

    stripped_live = _inbox_row(order_number="ORDER-ZERO-LIVE", total=0.0)
    stripped_live["canonical_payload"]["items"] = []
    stripped_live["received_at"] = datetime.now(timezone.utc)
    await db.integration_inbox.insert_one(stripped_live)

    selected = await _find_historical_positive_canon(
        db,
        owner_ids=[TENANT],
        order_number="ORDER-ZERO-LIVE",
    )

    assert selected["total_amount"] == 170.83
    assert selected["items"]


@pytest.mark.asyncio
async def test_recovery_composes_positive_total_and_items_from_split_traces(db):
    order_number = "ORDER-SPLIT-ACCOUNTING"

    amount_trace = _inbox_row(order_number=order_number, total=170.83)
    amount_trace["id"] = "amount-trace"
    amount_trace["canonical_payload"]["items"] = []
    amount_trace["received_at"] = (
        datetime.now(timezone.utc) - timedelta(minutes=2)
    )
    await db.integration_inbox.insert_one(amount_trace)

    items_trace = _inbox_row(order_number=order_number, total=0.0)
    items_trace["id"] = "items-trace"
    items_trace["canonical_payload"]["subtotal"] = 170.83
    items_trace["canonical_payload"]["items"][0]["unit_price"] = 170.83
    items_trace["canonical_payload"]["items"][0]["total"] = 170.83
    items_trace["received_at"] = (
        datetime.now(timezone.utc) - timedelta(minutes=1)
    )
    await db.integration_inbox.insert_one(items_trace)

    stripped_live = _inbox_row(order_number=order_number, total=0.0)
    stripped_live["id"] = "live-status-trace"
    stripped_live["canonical_payload"]["items"] = []
    stripped_live["received_at"] = datetime.now(timezone.utc)
    await db.integration_inbox.insert_one(stripped_live)

    selected = await _find_historical_positive_canon(
        db,
        owner_ids=[TENANT],
        order_number=order_number,
    )

    assert selected["total_amount"] == 170.83
    assert selected["items"][0]["total"] == 170.83
    assert selected["currency"] == "SAR"
    assert selected["_qoyod_historical_recovery"] == {
        "strategy": "split_verified_salla_traces",
        "owner_id": TENANT,
        "total_row_id": "amount-trace",
        "total_connector": None,
        "items_row_id": "items-trace",
        "items_connector": None,
    }


@pytest.mark.asyncio
async def test_recovery_refuses_to_mix_split_trace_currencies(db):
    order_number = "ORDER-SPLIT-CURRENCY-MISMATCH"

    amount_trace = _inbox_row(order_number=order_number, total=170.83)
    amount_trace["canonical_payload"]["currency"] = "QAR"
    amount_trace["canonical_payload"]["items"] = []
    amount_trace["received_at"] = (
        datetime.now(timezone.utc) - timedelta(minutes=1)
    )
    await db.integration_inbox.insert_one(amount_trace)

    items_trace = _inbox_row(order_number=order_number, total=0.0)
    items_trace["canonical_payload"]["currency"] = "SAR"
    items_trace["received_at"] = datetime.now(timezone.utc)
    await db.integration_inbox.insert_one(items_trace)

    selected = await _find_historical_positive_canon(
        db,
        owner_ids=[TENANT],
        order_number=order_number,
    )

    assert selected is None


@pytest.mark.asyncio
async def test_recovery_refuses_conflicting_live_positive_total(db):
    order_number = "ORDER-LIVE-TOTAL-CONFLICT"
    historical = _inbox_row(order_number=order_number, total=170.83)
    await db.integration_inbox.insert_one(historical)
    live = _inbox_row(order_number=order_number, total=200.0)[
        "canonical_payload"
    ]
    live["items"] = []

    selected = await _find_historical_positive_canon(
        db,
        owner_ids=[TENANT],
        order_number=order_number,
        live_canon=live,
    )

    assert selected is None


@pytest.mark.asyncio
async def test_recovery_fills_total_without_replacing_live_items(db):
    order_number = "ORDER-LIVE-ITEMS-STAY"
    historical = _inbox_row(order_number=order_number, total=170.83)
    historical["canonical_payload"]["items"][0]["sku"] = "HISTORICAL-SKU"
    await db.integration_inbox.insert_one(historical)
    live = _inbox_row(order_number=order_number, total=0.0)[
        "canonical_payload"
    ]
    live["items"][0].update({
        "sku": "LIVE-SKU",
        "unit_price": 170.83,
        "total": 170.83,
    })

    selected = await _find_historical_positive_canon(
        db,
        owner_ids=[TENANT],
        order_number=order_number,
        live_canon=live,
    )

    assert selected["total_amount"] == 170.83
    assert selected["items"][0]["sku"] == "LIVE-SKU"


@pytest.mark.asyncio
async def test_recovery_never_mixes_total_and_items_across_owners(db):
    order_number = "ORDER-CROSS-OWNER"
    amount_trace = _inbox_row(order_number=order_number, total=170.83)
    amount_trace["user_id"] = "qoyod-owner"
    amount_trace["canonical_payload"]["items"] = []
    await db.integration_inbox.insert_one(amount_trace)
    items_trace = _inbox_row(order_number=order_number, total=0.0)
    items_trace["user_id"] = "orders-owner"
    items_trace["canonical_payload"]["items"][0].update({
        "unit_price": 170.83,
        "total": 170.83,
    })
    await db.integration_inbox.insert_one(items_trace)

    selected = await _find_historical_positive_canon(
        db,
        owner_ids=["qoyod-owner", "orders-owner"],
        order_number=order_number,
    )

    assert selected is None


@pytest.mark.asyncio
async def test_recovery_prefers_clean_current_owner_over_secondary_conflict(db):
    order_number = "ORDER-PREFERRED-OWNER"
    preferred = _inbox_row(order_number=order_number, total=170.83)
    preferred["user_id"] = "orders-owner"
    await db.integration_inbox.insert_one(preferred)

    for total in (100.0, 200.0):
        secondary = _inbox_row(order_number=order_number, total=total)
        secondary["user_id"] = "main"
        await db.integration_inbox.insert_one(secondary)

    selected = await _find_historical_positive_canon(
        db,
        owner_ids=["main", "orders-owner"],
        order_number=order_number,
        live_canon={
            "order_number": order_number,
            "currency": "SAR",
            "total_amount": 0.0,
            "items": [],
        },
        preferred_inbox_owner_id="orders-owner",
    )

    assert selected["total_amount"] == 170.83
    assert selected["_qoyod_historical_recovery"]["owner_id"] == (
        "orders-owner"
    )


@pytest.mark.asyncio
async def test_recovery_refuses_outer_inner_money_currency_conflict(db):
    order_number = "ORDER-NESTED-CURRENCY-CONFLICT"
    row = _inbox_row(order_number=order_number, total=0.0)
    row["canonical_payload"]["total_amount"] = {
        "currency": "SAR",
        "amount": {"amount": "170.83", "currency": "AED"},
    }
    await db.integration_inbox.insert_one(row)

    selected = await _find_historical_positive_canon(
        db,
        owner_ids=[TENANT],
        order_number=order_number,
    )

    assert selected is None


@pytest.mark.asyncio
async def test_recovery_reads_nested_money_and_keeps_live_operational_facts(db):
    order_number = "ORDER-NESTED-MONEY"
    amount_trace = _inbox_row(order_number=order_number, total=0.0)
    amount_trace["canonical_payload"]["total_amount"] = {
        "amount": {"amount": "170.83", "currency": "SAR"},
        "currency": "SAR",
    }
    amount_trace["canonical_payload"]["items"] = []
    amount_trace["canonical_payload"]["payment_method"] = "credit_card"
    amount_trace["received_at"] = (
        datetime.now(timezone.utc) - timedelta(minutes=2)
    )
    await db.integration_inbox.insert_one(amount_trace)

    items_trace = _inbox_row(order_number=order_number, total=0.0)
    items_trace["canonical_payload"]["items"][0].update({
        "unit_price": {"amount": "170.83", "currency": "SAR"},
        "total": {"amount": "170.83", "currency": "SAR"},
    })
    items_trace["received_at"] = (
        datetime.now(timezone.utc) - timedelta(minutes=1)
    )
    await db.integration_inbox.insert_one(items_trace)

    live = _inbox_row(order_number=order_number, total=0.0)[
        "canonical_payload"
    ]
    live["items"] = []
    live["order_status"] = "delivered"
    live["payment_method"] = "tamara"
    live["customer"] = {"name": "العميل الحالي"}

    selected = await _find_historical_positive_canon(
        db,
        owner_ids=[TENANT],
        order_number=order_number,
        live_canon=live,
    )

    assert selected["total_amount"] == 170.83
    assert selected["items"][0]["unit_price"] == 170.83
    assert selected["items"][0]["total"] == 170.83
    assert selected["order_status"] == "delivered"
    assert selected["payment_method"] == "tamara"
    assert selected["customer"] == {"name": "العميل الحالي"}


@pytest.mark.asyncio
async def test_recovery_prefers_exact_owner_unified_salla_snapshot(db):
    order_number = "ORDER-UNIFIED-ACCOUNTING"
    await db.unified_orders.insert_one({
        "user_id": "orders-owner",
        "order_number": order_number,
        "raw_by_source": {
            "salla_direct": {
                "id": order_number,
                "reference_id": order_number,
                "date": "2026-08-09T10:00:00+03:00",
                "status": {"slug": "completed", "name": "تم التنفيذ"},
                "payment_method": "credit_card",
                "customer": {"full_name": "عميل سلة"},
                "amounts": {
                    "total": {"amount": "170.83", "currency": "SAR"},
                    "sub_total": {"amount": "150.83", "currency": "SAR"},
                    "shipping_cost": {"amount": "20.00", "currency": "SAR"},
                    "tax": {"amount": "0.00", "currency": "SAR"},
                    "discount": {"amount": "0.00", "currency": "SAR"},
                },
                "items": [{
                    "variant": {"sku": "VARIANT-SKU-1"},
                    "product": {"id": "p1", "name": "منتج سلة"},
                    "quantity": 1,
                    "amounts": {
                        "price_without_tax": {
                            "amount": "131.16", "currency": "SAR",
                        },
                        "total": {
                            "amount": "150.83", "currency": "SAR",
                        },
                        "tax": {"amount": "19.67", "currency": "SAR"},
                    },
                }],
            },
        },
    })
    live = _inbox_row(order_number=order_number, total=0.0)[
        "canonical_payload"
    ]
    live["items"] = []
    live["order_status"] = "delivered"
    live["payment_method"] = "tamara"
    live["customer"] = {"name": "العميل الحالي"}

    selected = await _find_historical_positive_canon(
        db,
        owner_ids=[TENANT],
        unified_owner_id="orders-owner",
        order_number=order_number,
        live_canon=live,
    )

    assert selected["total_amount"] == 170.83
    assert selected["shipping_amount"] == 20.0
    assert selected["items"][0]["sku"] == "VARIANT-SKU-1"
    assert selected["currency"] == "SAR"
    assert selected["order_status"] == "delivered"
    assert selected["payment_method"] == "tamara"
    assert selected["customer"] == {"name": "العميل الحالي"}
    assert selected["_qoyod_historical_recovery"]["strategy"] == (
        "unified_salla_direct_normalized"
    )


@pytest.mark.asyncio
async def test_fresh_verified_unified_snapshot_replaces_stale_positive_math(db):
    order_number = "ORDER-UNIFIED-STALE-MATH"
    await db.unified_orders.insert_one({
        "user_id": "orders-owner",
        "order_number": order_number,
        "raw_by_source": {
            "salla_direct": {
                "id": order_number,
                "reference_id": order_number,
                "date": "2026-08-25T10:00:00+03:00",
                "status": {"slug": "completed", "name": "تم التنفيذ"},
                "payment_method": "credit_card",
                "customer": {"full_name": "عميل سلة"},
                "amounts": {
                    "total": {"amount": "170.83", "currency": "SAR"},
                    "sub_total": {"amount": "150.83", "currency": "SAR"},
                    "shipping_cost": {"amount": "20.00", "currency": "SAR"},
                    "tax": {"amount": "0.00", "currency": "SAR"},
                    "discount": {"amount": "0.00", "currency": "SAR"},
                },
                "items": [{
                    "variant": {"sku": "CURRENT-SKU-1"},
                    "product": {"id": "p1", "name": "منتج سلة"},
                    "quantity": 1,
                    "amounts": {
                        "price_without_tax": {
                            "amount": "131.16", "currency": "SAR",
                        },
                        "total": {
                            "amount": "150.83", "currency": "SAR",
                        },
                        "tax": {"amount": "19.67", "currency": "SAR"},
                    },
                }],
            },
        },
    })
    live = _inbox_row(order_number=order_number, total=200.0)[
        "canonical_payload"
    ]
    live["items"][0].update({
        "sku": "STALE-SKU",
        "unit_price": 200.0,
        "total": 200.0,
    })
    live["order_status"] = "delivered"
    live["payment_method"] = "tamara"

    selected = await _find_historical_positive_canon(
        db,
        owner_ids=[TENANT, "orders-owner"],
        unified_owner_id="orders-owner",
        order_number=order_number,
        live_canon=live,
        prefer_verified_unified=True,
    )

    assert selected["total_amount"] == 170.83
    assert selected["items"][0]["sku"] == "CURRENT-SKU-1"
    assert selected["order_status"] == "delivered"
    assert selected["payment_method"] == "tamara"
    assert selected["_qoyod_historical_recovery"]["authority"] == (
        "fresh_salla_order_details"
    )


@pytest.mark.asyncio
async def test_recovery_refuses_mixed_currency_unified_salla_snapshot(db):
    order_number = "ORDER-UNIFIED-MIXED-CURRENCY"
    await db.unified_orders.insert_one({
        "user_id": "orders-owner",
        "order_number": order_number,
        "raw_by_source": {
            "salla_direct": {
                "id": order_number,
                "reference_id": order_number,
                "date": "2026-08-09T10:00:00+03:00",
                "status": {"slug": "completed", "name": "تم التنفيذ"},
                "amounts": {
                    "total": {"amount": "170.83", "currency": "SAR"},
                    "sub_total": {"amount": "150.83", "currency": "SAR"},
                    "shipping_cost": {"amount": "20.00", "currency": "AED"},
                },
                "items": [{
                    "sku": "SKU-1",
                    "name": "منتج",
                    "quantity": 1,
                    "amounts": {
                        "price_without_tax": {
                            "amount": "131.16", "currency": "SAR",
                        },
                        "total": {
                            "amount": "150.83", "currency": "SAR",
                        },
                    },
                }],
            },
        },
    })

    selected = await _find_historical_positive_canon(
        db,
        owner_ids=[TENANT],
        unified_owner_id="orders-owner",
        order_number=order_number,
        live_canon={
            "order_number": order_number,
            "total_amount": 0.0,
            "items": [],
        },
    )

    assert selected is None


@pytest.mark.asyncio
async def test_recovery_rejects_unified_snapshot_for_another_owner(db):
    order_number = "ORDER-UNIFIED-OTHER-OWNER"
    await db.unified_orders.insert_one({
        "user_id": "different-owner",
        "order_number": order_number,
        "raw_by_source": {"salla_direct": {"reference_id": order_number}},
    })

    selected = await _find_historical_positive_canon(
        db,
        owner_ids=[TENANT],
        unified_owner_id="orders-owner",
        order_number=order_number,
        live_canon={
            "order_number": order_number,
            "currency": "SAR",
            "total_amount": 0.0,
            "items": [],
        },
    )

    assert selected is None


@pytest.mark.asyncio
async def test_default_path_stays_blocked_but_bounded_recovery_passes_zero_guard(
    db,
):
    order_number = "ORDER-BOUNDED-ONLY"
    await _seed_settings(db, payment_methods_mapped=False)

    amount_trace = _inbox_row(
        order_number=order_number,
        total=170.83,
        payment_method="tamara",
    )
    amount_trace["canonical_payload"]["items"] = []
    amount_trace["received_at"] = (
        datetime.now(timezone.utc) - timedelta(minutes=2)
    )
    await db.integration_inbox.insert_one(amount_trace)

    items_trace = _inbox_row(
        order_number=order_number,
        total=0.0,
        payment_method="tamara",
    )
    items_trace["canonical_payload"]["items"][0].update({
        "unit_price": 170.83,
        "total": 170.83,
    })
    items_trace["received_at"] = (
        datetime.now(timezone.utc) - timedelta(minutes=1)
    )
    await db.integration_inbox.insert_one(items_trace)

    live = _inbox_row(
        order_number=order_number,
        total=0.0,
        payment_method="tamara",
    )
    live["canonical_payload"]["items"] = []
    live["received_at"] = datetime.now(timezone.utc)
    await db.integration_inbox.insert_one(live)

    with pytest.raises(ManualSendRefused) as default_exc:
        await manual_send_one(
            db,
            user_id=TENANT,
            order_number=order_number,
        )
    assert default_exc.value.code == "zero_total_refused"

    with pytest.raises(ManualSendRefused) as recovery_exc:
        await manual_send_one(
            db,
            user_id=TENANT,
            order_number=order_number,
            allow_historical_positive_total=True,
        )
    # Reaching the payment-account guard proves 170.83 and the item survived
    # recovery and invoice preconditions. No Qoyod credential/client/write is
    # reached because the payment mapping deliberately remains absent.
    assert recovery_exc.value.code == "payment_method_unmapped"
    assert await db.qoyod_manual_send_locks.count_documents({}) == 0


@pytest.mark.asyncio
async def test_live_nested_currency_conflict_stops_before_any_qoyod_write(db):
    order_number = "ORDER-LIVE-CURRENCY-CONFLICT"
    row = _inbox_row(order_number=order_number, total=0.0)
    row["canonical_payload"]["total_amount"] = {
        "currency": "SAR",
        "amount": {"amount": "170.83", "currency": "AED"},
    }
    await db.integration_inbox.insert_one(row)

    with pytest.raises(ManualSendRefused) as exc_info:
        await manual_send_one(
            db,
            user_id=TENANT,
            order_number=order_number,
            allow_historical_positive_total=True,
        )

    assert exc_info.value.code == "accounting_currency_conflict"
    assert exc_info.value.extra["qoyod_write_performed"] is False
    assert await db.qoyod_manual_send_locks.count_documents({}) == 0


# ────────────────────────────────────────────────────────────────────
# T15 — Freeze toggle round-trip: worker respects the flag AFTER
#       flip, and the flag also short-circuits _one_round.
# ────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_freeze_toggle_stops_worker(db):
    # Start empty. Confirm worker runs normally.
    result = await _one_round(db, user_id=TENANT, batch_limit=5)
    assert "frozen" not in result

    # Flip the flag via direct update (equivalent to what the endpoint
    # does).
    await db.qoyod_settings.update_one(
        {"user_id": TENANT},
        {"$set": {"legacy_pipeline_frozen": True,
                   "legacy_pipeline_frozen_actor": "test@x",
                   "legacy_pipeline_frozen_updated_at":
                       datetime.now(timezone.utc)},
         "$setOnInsert": {"user_id": TENANT}},
        upsert=True,
    )
    result = await _one_round(db, user_id=TENANT, batch_limit=5)
    assert result["status"] == "legacy_pipeline_frozen"
    assert result["processed"] == 0

    # Flip it back off.
    await db.qoyod_settings.update_one(
        {"user_id": TENANT},
        {"$set": {"legacy_pipeline_frozen": False}},
    )
    result = await _one_round(db, user_id=TENANT, batch_limit=5)
    assert "frozen" not in result
