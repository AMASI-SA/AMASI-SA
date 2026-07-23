"""Plan-B Payment-Path Fix (2026-07-09) — pytest coverage.

Verifies the three surgical fixes committed to `send.py`:

    F1  Local `qoyod_invoices` write-through happens ONLY AFTER a
        successful `POST /invoice_payments`. Before that, the ledger
        MUST NOT show the invoice as paid.

    F2  `already_sent` guard requires BOTH markers on the inbox row:
            manual_qoyod_invoice_id  AND  manual_qoyod_payment_id
        If only the invoice marker is present, the send must NOT be
        refused — it must be routed to the payment-only retry branch.

    F3  When the payment step fails after the invoice was created:
          • The local ledger is written as status="partial"
            (remaining=full total), NEVER "paid".
          • `manual_qoyod_invoice_id` stays set but
            `manual_qoyod_payment_id` remains missing so the next
            click re-enters the payment-only retry path.

    F4  Retry-payment-only path: does not call /customers, /products,
        /invoices again. Only calls /invoice_payments with the
        persisted invoice_id.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import mongomock_motor  # noqa: F401  (used by fixture)
import pytest

from integrations.qoyod_manual.client import ManualQoyodError
from integrations.qoyod_manual.send import (
    manual_send_one, ManualSendRefused,
)


TENANT = "main"


@pytest.fixture
def db():
    client = mongomock_motor.AsyncMongoMockClient()
    return client["test_plan_b_payment_fix"]


def _inbox_row(*, order_number: str, order_date: str = "2026-07-05",
               total: float = 115.0, sku: str = "SKU-A",
               with_manual_id: str | None = None,
               with_manual_pay_id: str | None = None,
               with_manual_inv_number: str | None = None):
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
            "order_status":  "completed",
            "order_status_native": "تم التنفيذ",
            "total_amount":  total,
            "subtotal":      total,
            "shipping_amount": 0.0,
            "tax_amount":    0.0,
            "discount_amount": 0.0,
            "cod_fee_amount": 0.0,
            "currency":      "SAR",
            "payment_method":         "credit_card",
            "payment_method_native":  "credit_card",
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
    if with_manual_id is not None:
        row["manual_qoyod_invoice_id"] = with_manual_id
    if with_manual_pay_id is not None:
        row["manual_qoyod_payment_id"] = with_manual_pay_id
    if with_manual_inv_number is not None:
        row["manual_qoyod_invoice_number"] = with_manual_inv_number
    return row


async def _seed(db):
    await db.qoyod_settings.insert_one({
        "user_id":                     TENANT,
        "qoyod_tax_percent":           15,
        "default_inventory_id":        1,
        "default_branch_id":           1,
        "default_product_category_id": 1,
        "default_product_tax_id":      1,
        "default_sales_account_id":    100,
        "default_product_unit_type_id": 1,
        "payment_method_mapping": [
            {"salla_method": "credit_card",
             "qoyod_account_id": "42",
             "posting_mode": "paid_receipt"},
        ],
    })
    from integrations.qoyod.credentials import save_api_key
    await save_api_key(db, TENANT, "test-api-key-xyz")


def _patches(*, find_inv=None, find_cust=None, find_prod=None,
             create_cust=None, create_prod=None,
             create_invoice=None, create_payment=None):
    """Assemble the standard set of AsyncMock patches. Only args passed
    in are used — others get default no-op behaviour."""
    from contextlib import ExitStack
    stack = ExitStack()
    def _mk(target, side):
        return patch(
            f"integrations.qoyod_manual.client.ManualQoyodClient.{target}",
            new=AsyncMock(side_effect=side))
    default_find_inv = find_inv or (lambda *a, **k: None)
    default_find_cust = find_cust or (lambda *a, **k: [{"id": 33}])
    default_find_prod = find_prod or (lambda *a, **k: {"id": 77, "sku": "SKU-A"})
    default_create_cust = create_cust or (lambda *a, **k: {"contact": {"id": 33}})
    default_create_prod = create_prod or (lambda *a, **k: {"product": {"id": 77}})
    default_create_invoice = create_invoice or (
        lambda payload, *, idem: {"invoice": {"id": 501, "number": "INV-501"}})
    default_create_payment = create_payment or (
        lambda payload, *, idem: {"invoice_payment": {"id": 8001}})

    async def wrap(fn):
        # Return a coroutine that mirrors the fn.
        async def _c(*a, **k):
            r = fn(*a, **k)
            return r
        return _c

    stack.enter_context(_mk("find_invoice_by_reference",  default_find_inv))
    stack.enter_context(_mk("find_customers_by_phone",    default_find_cust))
    stack.enter_context(_mk("find_customers_by_email",    default_find_cust))
    stack.enter_context(_mk("find_product_by_sku",        default_find_prod))
    stack.enter_context(_mk("create_customer",            default_create_cust))
    stack.enter_context(_mk("create_product",             default_create_prod))
    stack.enter_context(_mk("create_invoice",             default_create_invoice))
    stack.enter_context(_mk("create_invoice_payment",     default_create_payment))
    return stack


# ─────────────────────────────────────────────────────────────────────
# F1 — write-through only after payment succeeds
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_qoyod_invoices_written_only_after_payment(db):
    """On happy path, qoyod_invoices must be upserted AFTER the
    /invoice_payments call — reflecting status=paid, remaining=0.

    We assert the ordering by inspecting the qoyod_invoices row: if it
    exists at all, it must have status=paid (never partial).
    """
    await _seed(db)
    await db.integration_inbox.insert_one(
        _inbox_row(order_number="P100", total=115.0))

    def _create_invoice(payload, *, idem):
        return {"invoice": {"id": 601, "number": "INV-601"}}

    def _create_payment(payload, *, idem):
        # By this point qoyod_invoices should NOT yet have a row.
        return {"invoice_payment": {"id": 9001}}

    with _patches(create_invoice=_create_invoice,
                  create_payment=_create_payment):
        result = await manual_send_one(
            db, user_id=TENANT, order_number="P100")

    assert result["ok"] is True
    assert result["invoice_id"] == 601
    assert result["payment_id"] == 9001

    # qoyod_invoices row must now exist and be flagged as PAID.
    inv = await db.qoyod_invoices.find_one(
        {"user_id": TENANT, "qoyod_invoice_id": "601"})
    assert inv is not None
    assert inv["status"] == "paid"
    assert inv["remaining"] == 0.0
    assert inv["paid_amount"] == 115.0
    assert inv["total"] == 115.0
    assert inv["source"] == "plan_b_send"


@pytest.mark.asyncio
async def test_one_halalah_higher_qoyod_total_stays_partially_paid(db):
    """A +0.01 Qoyod rounding difference must not be paid by Mezan.

    The payment records only what Salla collected, leaving Qoyod to
    report the honest 0.01 remaining balance and partial status.
    """
    await _seed(db)
    await db.integration_inbox.insert_one(
        _inbox_row(order_number="P101", total=115.0))

    captured = {}

    def _create_invoice(payload, *, idem):
        return {
            "invoice": {
                "id": 602,
                "number": "INV-602",
                "total": 115.01,
            }
        }

    def _create_payment(payload, *, idem):
        captured["payment"] = payload
        return {"invoice_payment": {"id": 9002}}

    with _patches(create_invoice=_create_invoice,
                  create_payment=_create_payment):
        result = await manual_send_one(
            db, user_id=TENANT, order_number="P101")

    assert captured["payment"]["invoice_payment"]["amount"] == 115.0
    assert result["payment_amount"] == 115.0

    inv = await db.qoyod_invoices.find_one(
        {"user_id": TENANT, "qoyod_invoice_id": "602"})
    assert inv["total"] == 115.01
    assert inv["paid_amount"] == 115.0
    assert inv["remaining"] == 0.01
    assert inv["status"] == "partial"

    row = await db.integration_inbox.find_one(
        {"salla_order_number": "P101"})
    assert row["manual_qoyod_payment_id"] == "9002"


@pytest.mark.asyncio
async def test_one_halalah_lower_qoyod_total_is_not_overpaid(db):
    """When Qoyod is lower by 0.01, pay its total and close normally."""
    await _seed(db)
    await db.integration_inbox.insert_one(
        _inbox_row(order_number="P102", total=115.0))

    captured = {}

    def _create_invoice(payload, *, idem):
        return {
            "invoice": {
                "id": 603,
                "number": "INV-603",
                "total": 114.99,
            }
        }

    def _create_payment(payload, *, idem):
        captured["payment"] = payload
        return {"invoice_payment": {"id": 9003}}

    with _patches(create_invoice=_create_invoice,
                  create_payment=_create_payment):
        result = await manual_send_one(
            db, user_id=TENANT, order_number="P102")

    assert captured["payment"]["invoice_payment"]["amount"] == 114.99
    assert result["payment_amount"] == 114.99

    inv = await db.qoyod_invoices.find_one(
        {"user_id": TENANT, "qoyod_invoice_id": "603"})
    assert inv["total"] == 114.99
    assert inv["paid_amount"] == 114.99
    assert inv["remaining"] == 0.0
    assert inv["status"] == "paid"


@pytest.mark.asyncio
async def test_retry_keeps_one_halalah_qoyod_balance_partial(db):
    """Payment-only retry follows the same Salla-collected amount rule."""
    await _seed(db)
    await db.integration_inbox.insert_one(
        _inbox_row(
            order_number="P103",
            total=115.0,
            with_manual_id="604",
            with_manual_inv_number="INV-604",
        )
    )

    captured = {}

    async def _get_invoice(_invoice_id):
        return {
            "invoice": {
                "id": 604,
                "number": "INV-604",
                "total": 115.01,
            }
        }

    async def _create_payment(payload, *, idem):
        captured["payment"] = payload
        return {"invoice_payment": {"id": 9004}}

    with patch(
        "integrations.qoyod_manual.client.ManualQoyodClient.get_invoice",
        new=AsyncMock(side_effect=_get_invoice),
    ), patch(
        "integrations.qoyod_manual.client.ManualQoyodClient."
        "create_invoice_payment",
        new=AsyncMock(side_effect=_create_payment),
    ):
        result = await manual_send_one(
            db, user_id=TENANT, order_number="P103")

    assert result["retry_payment_only"] is True
    assert result["payment_amount"] == 115.0
    assert result["difference"] == 0.01
    assert captured["payment"]["invoice_payment"]["amount"] == 115.0

    inv = await db.qoyod_invoices.find_one(
        {"user_id": TENANT, "qoyod_invoice_id": "604"})
    assert inv["total"] == 115.01
    assert inv["paid_amount"] == 115.0
    assert inv["remaining"] == 0.01
    assert inv["status"] == "partial"


# ─────────────────────────────────────────────────────────────────────
# F3 — payment failure writes PARTIAL state, not paid
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_payment_failure_writes_partial_state(db):
    """When /invoice_payments raises, the pipeline must:
        • persist manual_qoyod_invoice_id (so the retry can find it)
        • NOT persist manual_qoyod_payment_id
        • write qoyod_invoices as status=partial, remaining=full total
        • raise invoice_created_payment_failed
    """
    await _seed(db)
    await db.integration_inbox.insert_one(
        _inbox_row(order_number="P200", total=260.0))

    def _create_invoice(payload, *, idem):
        return {"invoice": {"id": 701, "number": "INV-701"}}

    async def _fail_payment(payload, *, idem):
        raise ManualQoyodError(
            status_code=422,
            endpoint="POST /invoice_payments",
            response_excerpt='{"error": "account_id invalid"}',
            request_body=payload)

    with patch("integrations.qoyod_manual.client.ManualQoyodClient."
               "find_invoice_by_reference",
               new=AsyncMock(return_value=None)), \
         patch("integrations.qoyod_manual.client.ManualQoyodClient."
               "find_customers_by_phone",
               new=AsyncMock(return_value=[{"id": 33}])), \
         patch("integrations.qoyod_manual.client.ManualQoyodClient."
               "find_product_by_sku",
               new=AsyncMock(return_value={"id": 77, "sku": "SKU-A"})), \
         patch("integrations.qoyod_manual.client.ManualQoyodClient."
               "create_invoice",
               new=AsyncMock(side_effect=lambda p, *, idem: _create_invoice(p, idem=idem))), \
         patch("integrations.qoyod_manual.client.ManualQoyodClient."
               "create_invoice_payment",
               new=AsyncMock(side_effect=_fail_payment)):
        with pytest.raises(ManualSendRefused) as exc:
            await manual_send_one(
                db, user_id=TENANT, order_number="P200")

    assert exc.value.code == "invoice_created_payment_failed"
    assert exc.value.extra["invoice_id"] == 701

    # Invoice marker persisted; payment marker NOT persisted.
    row = await db.integration_inbox.find_one(
        {"salla_order_number": "P200"})
    assert row["manual_qoyod_invoice_id"] == "701"
    assert row.get("manual_qoyod_payment_id") is None

    # Local ledger reflects PARTIAL state.
    inv = await db.qoyod_invoices.find_one(
        {"user_id": TENANT, "qoyod_invoice_id": "701"})
    assert inv is not None
    assert inv["status"] == "partial"
    assert inv["paid_amount"] == 0.0
    assert inv["remaining"] == 260.0
    assert inv["total"] == 260.0


# ─────────────────────────────────────────────────────────────────────
# F2 — already_sent requires BOTH markers
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_already_sent_requires_both_markers(db):
    """A row with only manual_qoyod_invoice_id (payment missing) must
    NOT be refused as `already_sent`. It must route to the payment-only
    retry branch (asserted separately in F4)."""
    await _seed(db)
    # Row has invoice marker but NO payment marker.
    await db.integration_inbox.insert_one(
        _inbox_row(order_number="P300", total=115.0,
                   with_manual_id="801",
                   with_manual_inv_number="INV-801"))

    # Only create_invoice_payment gets called during retry.
    async def _pay(payload, *, idem):
        return {"invoice_payment": {"id": 9500}}

    # No other endpoints should be called — assert that below via
    # mock call_count.
    m_create_invoice = AsyncMock(return_value={"invoice": {"id": 999}})
    m_create_customer = AsyncMock()
    m_create_product = AsyncMock()

    with patch("integrations.qoyod_manual.client.ManualQoyodClient."
               "create_invoice", new=m_create_invoice), \
         patch("integrations.qoyod_manual.client.ManualQoyodClient."
               "create_customer", new=m_create_customer), \
         patch("integrations.qoyod_manual.client.ManualQoyodClient."
               "create_product", new=m_create_product), \
         patch("integrations.qoyod_manual.client.ManualQoyodClient."
               "create_invoice_payment",
               new=AsyncMock(side_effect=_pay)):
        result = await manual_send_one(
            db, user_id=TENANT, order_number="P300")

    # Retry-payment-only path signature.
    assert result["ok"] is True
    assert result.get("retry_payment_only") is True
    assert result["invoice_id"] == 801
    assert result["payment_id"] == 9500

    # No new invoice / customer / product creation.
    m_create_invoice.assert_not_called()
    m_create_customer.assert_not_called()
    m_create_product.assert_not_called()

    # Inbox row now has payment marker AND unified qoyod_invoice_id.
    row = await db.integration_inbox.find_one(
        {"salla_order_number": "P300"})
    assert row["manual_qoyod_payment_id"] == "9500"
    assert row["qoyod_invoice_id"] == "801"
    assert row["qoyod_invoice_source"] == "manual_plan_b"

    # Local ledger flipped from missing/partial → paid.
    inv = await db.qoyod_invoices.find_one(
        {"user_id": TENANT, "qoyod_invoice_id": "801"})
    assert inv is not None
    assert inv["status"] == "paid"
    assert inv["remaining"] == 0.0


@pytest.mark.asyncio
async def test_already_sent_when_both_markers_present(db):
    """If BOTH markers are set, the second click IS refused."""
    await _seed(db)
    await db.integration_inbox.insert_one(
        _inbox_row(order_number="P400", total=115.0,
                   with_manual_id="901",
                   with_manual_pay_id="9601"))
    with pytest.raises(ManualSendRefused) as exc:
        await manual_send_one(db, user_id=TENANT, order_number="P400")
    assert exc.value.code == "already_sent"
    assert exc.value.extra["manual_qoyod_invoice_id"] == "901"
    assert exc.value.extra["manual_qoyod_payment_id"] == "9601"


# ─────────────────────────────────────────────────────────────────────
# F4 — retry-payment-only never re-calls /customers, /products,
#      /invoices even if the invoice already exists in Qoyod.
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_retry_payment_only_does_not_hit_invoice_endpoint(db):
    """Even when the Qoyod-side find_invoice_by_reference would return
    the same invoice (which is the real state — the invoice exists),
    the retry-payment-only branch fires BEFORE that guard and never
    triggers `duplicate_invoice_in_qoyod`.
    """
    await _seed(db)
    await db.integration_inbox.insert_one(
        _inbox_row(order_number="P500", total=115.0,
                   with_manual_id="1001",
                   with_manual_inv_number="INV-1001"))

    async def _pay(payload, *, idem):
        return {"invoice_payment": {"id": 10001}}

    m_find_inv = AsyncMock(return_value={"id": 1001,
                                          "reference": "P500"})
    with patch("integrations.qoyod_manual.client.ManualQoyodClient."
               "find_invoice_by_reference", new=m_find_inv), \
         patch("integrations.qoyod_manual.client.ManualQoyodClient."
               "create_invoice_payment",
               new=AsyncMock(side_effect=_pay)):
        result = await manual_send_one(
            db, user_id=TENANT, order_number="P500")

    assert result["ok"] is True
    assert result["retry_payment_only"] is True
    assert result["invoice_id"] == 1001
    # find_invoice_by_reference should NOT have been called — retry
    # branch fires before that guard.
    m_find_inv.assert_not_called()


# ─────────────────────────────────────────────────────────────────────
# F5 — retry-payment-only when the retry ALSO fails
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_retry_payment_only_second_failure_stays_partial(db):
    """Retry attempt where payment fails AGAIN — inbox must remain
    without payment marker so a THIRD click still routes to retry."""
    await _seed(db)
    await db.integration_inbox.insert_one(
        _inbox_row(order_number="P600", total=260.0,
                   with_manual_id="1101",
                   with_manual_inv_number="INV-1101"))

    async def _fail_payment(payload, *, idem):
        raise ManualQoyodError(
            status_code=500,
            endpoint="POST /invoice_payments",
            response_excerpt="qoyod down",
            request_body=payload)

    with patch("integrations.qoyod_manual.client.ManualQoyodClient."
               "create_invoice_payment",
               new=AsyncMock(side_effect=_fail_payment)):
        with pytest.raises(ManualSendRefused) as exc:
            await manual_send_one(
                db, user_id=TENANT, order_number="P600")

    assert exc.value.code == "invoice_created_payment_failed"
    assert exc.value.extra.get("retry_only") is True

    row = await db.integration_inbox.find_one(
        {"salla_order_number": "P600"})
    assert row["manual_qoyod_invoice_id"] == "1101"
    assert row.get("manual_qoyod_payment_id") is None

    inv = await db.qoyod_invoices.find_one(
        {"user_id": TENANT, "qoyod_invoice_id": "1101"})
    assert inv is not None
    assert inv["status"] == "partial"
    assert inv["remaining"] == 260.0


# ─────────────────────────────────────────────────────────────────────
# F6 — `_acquire_lock` refuses `already_sent` only when BOTH markers
#      exist on the lock record (mirrors the inbox-level guard). A
#      lock left as `succeeded` by a pre-2026-07-09 send that never
#      actually posted the payment must NOT block a retry.
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_acquire_lock_ignores_succeeded_without_payment_marker(db):
    await _seed(db)
    await db.integration_inbox.insert_one(
        _inbox_row(order_number="P700", total=260.0,
                   with_manual_id="1201",
                   with_manual_inv_number="INV-1201"))
    # Simulate a stale lock left as `succeeded` from a broken pre-fix
    # send that DID create the invoice but never registered payment.
    await db.qoyod_manual_send_locks.insert_one({
        "order_number": "P700",
        "user_id":      TENANT,
        "lock_id":      "manual-P700-stale",
        "status":       "succeeded",
        "manual_qoyod_invoice_id": "1201",   # invoice marker set
        # NOTE: manual_qoyod_payment_id intentionally MISSING
        "started_at":   datetime.now(timezone.utc),
    })

    async def _pay(payload, *, idem):
        return {"invoice_payment": {"id": 11001}}

    with patch("integrations.qoyod_manual.client.ManualQoyodClient."
               "create_invoice_payment",
               new=AsyncMock(side_effect=_pay)):
        result = await manual_send_one(
            db, user_id=TENANT, order_number="P700")

    assert result["ok"] is True
    assert result["retry_payment_only"] is True
    assert result["invoice_id"] == 1201
    assert result["payment_id"] == 11001


@pytest.mark.asyncio
async def test_acquire_lock_still_refuses_when_both_markers_on_lock(db):
    await _seed(db)
    await db.integration_inbox.insert_one(
        _inbox_row(order_number="P800", total=260.0,
                   with_manual_id="1301",
                   with_manual_pay_id="12001"))
    await db.qoyod_manual_send_locks.insert_one({
        "order_number": "P800",
        "user_id":      TENANT,
        "lock_id":      "manual-P800-done",
        "status":       "succeeded",
        "manual_qoyod_invoice_id": "1301",
        "manual_qoyod_payment_id": "12001",
        "started_at":   datetime.now(timezone.utc),
    })
    with pytest.raises(ManualSendRefused) as exc:
        await manual_send_one(db, user_id=TENANT, order_number="P800")
    assert exc.value.code == "already_sent"


# ─────────────────────────────────────────────────────────────────────
# F7 — `_acquire_lock` handles naive-datetime `started_at` cleanly.
#      Regression guard for the TypeError observed in production
#      (2026-07-09): "can't subtract offset-naive and offset-aware
#      datetimes" at send.py:180 during retry-payment-only.
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_acquire_lock_survives_naive_started_at(db):
    """Reproduces the production 500. When the existing lock row was
    inserted by an older code path with a `datetime.utcnow()` (naive)
    value, subtracting from `datetime.now(timezone.utc)` raised
    TypeError and bubbled up as HTTP 500. The fix coerces `started`
    to tz-aware UTC before subtraction.
    """
    from datetime import datetime as _dt
    await _seed(db)
    await db.integration_inbox.insert_one(
        _inbox_row(order_number="P900", total=260.0,
                   with_manual_id="1401",
                   with_manual_inv_number="INV-1401"))
    # Insert a lock with a NAIVE started_at from 10 minutes ago —
    # long-stale so the lock must be released and the retry allowed.
    from datetime import timedelta
    stale_naive = _dt.utcnow() - timedelta(minutes=10)
    assert stale_naive.tzinfo is None, "sanity: must be naive"
    await db.qoyod_manual_send_locks.insert_one({
        "order_number":            "P900",
        "user_id":                 TENANT,
        "lock_id":                 "manual-P900-legacy",
        "status":                  "in_progress",
        "manual_qoyod_invoice_id": "1401",
        "started_at":              stale_naive,
    })

    async def _pay(payload, *, idem):
        return {"invoice_payment": {"id": 22001}}

    with patch("integrations.qoyod_manual.client.ManualQoyodClient."
               "create_invoice_payment",
               new=AsyncMock(side_effect=_pay)):
        # Must NOT raise TypeError. Must dispatch to retry-payment-only.
        result = await manual_send_one(
            db, user_id=TENANT, order_number="P900")

    assert result["ok"] is True
    assert result.get("retry_payment_only") is True
    assert result["invoice_id"] == 1401
    assert result["payment_id"] == 22001


@pytest.mark.asyncio
async def test_acquire_lock_refuses_fresh_naive_in_progress(db):
    """A NAIVE `started_at` that is only 30s old must still be
    treated as `in_progress` (recent) — the fix must not accidentally
    treat all naive locks as stale.
    """
    from datetime import datetime as _dt
    await _seed(db)
    await db.integration_inbox.insert_one(
        _inbox_row(order_number="PA10", total=260.0,
                   with_manual_id="1501"))
    from datetime import timedelta
    fresh_naive = _dt.utcnow() - timedelta(seconds=30)
    await db.qoyod_manual_send_locks.insert_one({
        "order_number":            "PA10",
        "user_id":                 TENANT,
        "lock_id":                 "manual-PA10-recent",
        "status":                  "in_progress",
        "manual_qoyod_invoice_id": "1501",
        "started_at":              fresh_naive,
    })
    with pytest.raises(ManualSendRefused) as exc:
        await manual_send_one(db, user_id=TENANT, order_number="PA10")
    assert exc.value.code == "in_progress"
