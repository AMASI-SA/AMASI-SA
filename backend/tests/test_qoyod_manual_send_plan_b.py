"""Pytest for Plan-B Manual Send (/app/backend/integrations/qoyod_manual).

Coverage:
    T1  pending-orders excludes pre-floor + non-completed + already-sent.
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
from datetime import datetime, date, timezone
from unittest.mock import AsyncMock, patch

import mongomock_motor
import pytest

from integrations.qoyod_manual.pending import list_pending_orders
from integrations.qoyod_manual.send import (
    manual_send_one, ManualSendRefused,
)
from integrations.qoyod.worker import _one_round


TENANT = "main"


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
    # Row 1: eligible (completed, on 2026-07-05, no invoice)
    await db.integration_inbox.insert_one(
        _inbox_row(order_number="ORD-1"))
    # Row 2: pre-floor (2026-06-30) — should be excluded
    await db.integration_inbox.insert_one(
        _inbox_row(order_number="ORD-2", order_date="2026-06-30"))
    # Row 3: not completed — should be excluded
    await db.integration_inbox.insert_one(
        _inbox_row(order_number="ORD-3", status="pending"))
    # Row 4: already sent (manual marker)
    await db.integration_inbox.insert_one(
        _inbox_row(order_number="ORD-4", with_manual_id=True))
    # Row 5: legacy invoice id (non-DRY)
    await db.integration_inbox.insert_one(
        _inbox_row(order_number="ORD-5", with_legacy_id=True))

    result = await list_pending_orders(
        db, user_id=TENANT, days=365, limit=100)
    order_numbers = {o["order_number"] for o in result["orders"]}
    assert order_numbers == {"ORD-1"}, order_numbers
    assert result["counts"]["excluded_pre_floor"] >= 1
    assert result["counts"]["excluded_not_completed"] >= 1
    assert result["counts"]["excluded_already_sent"] >= 2


# ────────────────────────────────────────────────────────────────────
# T2 — G1: already-sent short-circuit
# ────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_send_refuses_when_already_sent(db):
    await _seed_settings(db)
    await _seed_credentials(db)
    await db.integration_inbox.insert_one(
        _inbox_row(order_number="A100", with_manual_id=True))
    with pytest.raises(ManualSendRefused) as exc:
        await manual_send_one(db, user_id=TENANT, order_number="A100")
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
                             "reference": payload["invoice"]["reference"]}}

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
# T6 — legacy pipeline frozen kill-switch
# ────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_worker_respects_frozen_flag(db):
    await db.qoyod_settings.insert_one({
        "user_id": TENANT,
        "legacy_pipeline_frozen": True,
    })
    result = await _one_round(db, user_id=TENANT, batch_limit=10)
    assert result["frozen"] is True
    assert result["normalized"] == {"processed": 0, "outcomes": {}}
    assert result["customer_resolved"] == {"processed": 0, "outcomes": {}}
    assert result["backfill_gate"]["mode"] == "frozen"


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
                             "reference": payload["invoice"]["reference"]}}

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
# T12 — Floor-date boundary (bug: order 268552119 leaked with pre-floor
#       Salla date because the old helper fell back to received_at).
#
#   • 2026-06-30 → EXCLUDED
#   • 2026-07-01 → INCLUDED
#   • 2026-07-05 → INCLUDED
#   • no Salla date at all → EXCLUDED (never promoted via received_at)
# ────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_floor_date_boundary_strict(db):
    # Order that MUST be excluded (2026-06-30) — reproduces the bug
    # reported for order 268552119.
    row_before = _inbox_row(order_number="268552119",
                             order_date="2026-06-30")
    await db.integration_inbox.insert_one(row_before)

    # Exactly ON the floor — MUST be included.
    row_boundary = _inbox_row(order_number="ORDER-JUL1",
                               order_date="2026-07-01")
    await db.integration_inbox.insert_one(row_boundary)

    # After floor — MUST be included.
    row_after = _inbox_row(order_number="ORDER-JUL5",
                            order_date="2026-07-05")
    await db.integration_inbox.insert_one(row_after)

    # No Salla date at all — MUST be excluded even if received_at is
    # today (post-floor). Simulates a webhook that arrived after
    # 2026-07-01 for an order that was actually created much earlier
    # but whose Salla date fields are missing / malformed.
    row_no_date = {
        "id":                 "no-date-1",
        "user_id":            TENANT,
        "trace_id":           "tr-nd",
        "salla_order_number": "ORDER-NO-DATE",
        "received_at":        datetime.now(timezone.utc),
        "pipeline_stage":     "NORMALIZED",
        "canonical_payload": {
            "order_number":  "ORDER-NO-DATE",
            "order_id":      "ORDER-NO-DATE",
            # NO order_date / created_at at all.
            "order_status":  "completed",
            "order_status_native": "تم التنفيذ",
            "total_amount":  10.0,
            "currency":      "SAR",
            "payment_method": "credit_card",
            "customer": {"name": "بدون تاريخ"},
            "items": [{"sku": "SKU-ND", "name": "بند بدون تاريخ",
                        "quantity": 1, "unit_price": 10.0,
                        "tax_amount": 0.0, "discount_amount": 0.0,
                        "total": 10.0}],
        },
        "raw_payload": {},
    }
    await db.integration_inbox.insert_one(row_no_date)

    result = await list_pending_orders(
        db, user_id=TENANT, days=365, limit=100)
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
    assert result["counts"]["excluded_pre_floor"] >= 1
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
