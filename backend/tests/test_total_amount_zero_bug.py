"""Bug fix: total_amount collapses to 0 across Salla-Direct + Make
webhooks (user report, 2026-02).

User-observed pattern:
    • If Make was OFF and Salla was updated first → amount stays right.
    • If Make ran first and Salla-Direct sync ran after → amount
      became 0.00 in Manual Send.
Root cause: Salla API `/orders` sometimes returns a status-only /
collapsed shape after the `expanded=true` deprecation, with
`amounts.total.amount == 0`. `upsert_salla_direct_to_inbox` was
blindly replacing `canonical_payload` on refresh, corrupting the
positive amount the earlier Make trace had captured. Because the
Salla-Direct row became newest, `list_pending_orders` `$group $first`
picked it → Manual Send displayed 0.

Three layers of defence tested here:
    L1. `upsert_salla_direct_to_inbox` preserves prior positive
        monetary fields when the new payload has 0/null totals.
    L2. `list_pending_orders` falls back to the highest positive
        total across all traces of the same order (Salla-Direct
        wins ties) when the newest trace has total=0.
    L3. `send.py` HARD-refuses to create a قيود invoice when the
        resolved total is 0 while another trace shows a positive
        total — prevents a zero-amount invoice from ever leaking.
"""
from __future__ import annotations

import os
os.environ.setdefault("QOYOD_WEBHOOK_TOKEN", "test-token")

from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, patch

import mongomock_motor  # noqa: F401
import pytest
import pytest_asyncio
from fastapi import APIRouter, FastAPI
from httpx import AsyncClient, ASGITransport

from integrations.qoyod.models import ensure_qoyod_indexes
from integrations.qoyod.webhook import attach_webhook_routes
from integrations.qoyod_manual.pending import list_pending_orders
from integrations.qoyod_manual import send as send_mod
from salla_integration import sync as sync_mod


INBOX_TENANT = "main"


@pytest_asyncio.fixture
async def db():
    client = mongomock_motor.AsyncMongoMockClient()
    _db = client["test_amount_zero_bug"]
    await ensure_qoyod_indexes(_db)
    return _db


@pytest_asyncio.fixture
async def http(db):
    os.environ["QOYOD_WEBHOOK_TOKEN"] = "test-token"
    router = APIRouter()
    attach_webhook_routes(router, db)
    app = FastAPI()
    app.include_router(router)
    async with AsyncClient(transport=ASGITransport(app=app),
                           base_url="http://test") as client:
        yield client


# ── Payload builders ────────────────────────────────────────────────
def _salla_full(order_num: str, *, total: str = "134.00",
                status_slug: str = "completed",
                status_name: str = "تم التنفيذ") -> dict:
    """Full Salla /orders payload (with amounts populated)."""
    return {
        "id": f"oid-{order_num}",
        "reference_id": order_num,
        "date": {"date": "2026-07-15 10:00:00", "timezone": "Asia/Riyadh"},
        "status": {
            "slug": status_slug, "name": status_name,
            "customized": {"slug": status_slug, "name": status_name}},
        "customer": {"first_name": "علي", "last_name": "ك.",
                     "mobile": "+966501234567"},
        "amounts": {
            "sub_total": {"amount": "116.52", "currency": "SAR"},
            "tax":       {"amount":  "17.48", "currency": "SAR"},
            "shipping":  {"amount":   "0.00", "currency": "SAR"},
            "total":     {"amount":   total,  "currency": "SAR"},
        },
        "items": [{
            "sku": "SKU-A", "name": "Item", "quantity": 1,
            "product": {"id": "p1", "sku": "SKU-A", "name": "Item"},
            "amounts": {
                "price_without_tax": {"amount": "116.52", "currency": "SAR"},
                "total": {"amount": "116.52", "currency": "SAR"},
                "tax": {"percent": 15,
                        "amount": {"amount": "17.48", "currency": "SAR"}},
            },
        }],
        "payment_method": "mada",
    }


def _salla_stripped_status_only(order_num: str, *,
                                 status_slug: str = "completed",
                                 status_name: str = "تم التنفيذ") -> dict:
    """The problematic shape: Salla returns a status-only refresh
    where `amounts.total.amount == 0` (bug reproducer). Items and
    other fields are present-but-minimal (still passes validation)."""
    return {
        "id": f"oid-{order_num}",
        "reference_id": order_num,
        "date": {"date": "2026-07-15 10:00:00", "timezone": "Asia/Riyadh"},
        "status": {
            "slug": status_slug, "name": status_name,
            "customized": {"slug": status_slug, "name": status_name}},
        "customer": {"first_name": "علي", "last_name": "ك.",
                     "mobile": "+966501234567"},
        "amounts": {
            "sub_total": {"amount": "0.00", "currency": "SAR"},
            "tax":       {"amount": "0.00", "currency": "SAR"},
            "shipping":  {"amount": "0.00", "currency": "SAR"},
            "total":     {"amount": "0.00", "currency": "SAR"},
        },
        # A minimal item so validation still passes, but with a
        # collapsed total (mimics the Salla API partial response).
        "items": [{
            "sku": "SKU-A", "name": "Item", "quantity": 1,
            "product": {"id": "p1", "sku": "SKU-A", "name": "Item"},
            "amounts": {
                "price_without_tax": {"amount": "0.00", "currency": "SAR"},
                "total": {"amount": "0.00", "currency": "SAR"},
                "tax": {"percent": 0,
                        "amount": {"amount": "0.00", "currency": "SAR"}},
            },
        }],
        "payment_method": "mada",
    }


# ── L1 — money-preservation in Salla-Direct upsert ─────────────────
@pytest.mark.asyncio
async def test_salla_direct_preserves_prior_amount_on_zero_refresh(db):
    """L1: a subsequent Salla-Direct sync with a zero-total payload
    must NOT wipe out the previously stored positive amount."""
    order_num = "271463603"

    # First sync — full payload with 134 SAR.
    mock1 = AsyncMock(side_effect=[
        {"data": [_salla_full(order_num, total="134.00")],
         "pagination": {"totalPages": 1}}])
    with patch.object(sync_mod, "call_salla", new=mock1):
        await sync_mod.run_orders_sync(db, "user-uuid-1")

    row1 = await db.integration_inbox.find_one(
        {"user_id": INBOX_TENANT, "salla_order_number": order_num})

    def _amt(node):
        """canonical_payload.total_amount can be a flat float OR
        a `{amount, currency}` dict depending on the writer path."""
        if isinstance(node, dict):
            return float(node.get("amount") or 0)
        return float(node or 0)

    assert _amt(row1["canonical_payload"]["total_amount"]) == 134.0

    # Second sync — stripped payload (amounts.total = 0).
    mock2 = AsyncMock(side_effect=[
        {"data": [_salla_stripped_status_only(order_num)],
         "pagination": {"totalPages": 1}}])
    with patch.object(sync_mod, "call_salla", new=mock2):
        result = await sync_mod.run_orders_sync(db, "user-uuid-1")

    # The row was refreshed, but the amount MUST NOT collapse to 0.
    row2 = await db.integration_inbox.find_one(
        {"user_id": INBOX_TENANT, "salla_order_number": order_num})
    assert _amt(row2["canonical_payload"]["total_amount"]) == 134.0

    # Diagnostic: last stage_history entry notes what was preserved.
    last_hist = row2["stage_history"][-1]
    assert "preserved" in (last_hist.get("note") or "").lower()

    # Sanity — items also preserved on stripped refresh.
    assert row2["canonical_payload"].get("items"), "items were wiped"


# ── L2 — pending list amount fallback across traces ─────────────────
@pytest.mark.asyncio
async def test_pending_falls_back_to_positive_total_across_traces(db, http):
    """L2: even if the newest trace has total=0, `list_pending_orders`
    surfaces the highest positive total across all traces of that
    order in `total_amount`."""
    order_num = "271463604"

    # (a) Make webhook arrives first — carries the correct 134.00.
    r = await http.post("/webhook",
                        json={"event": "order.status.updated",
                              "data": _salla_full(order_num, total="134.00")},
                        headers={"X-Webhook-Token": "test-token"})
    assert r.status_code in (200, 201)

    # (b) Now insert a "salla_direct" trace that (simulating the API
    # bug) has amount = 0 and is NEWER than the Make row.
    newer_ts = datetime.now(timezone.utc) + timedelta(seconds=30)
    await db.integration_inbox.insert_one({
        "id": "row-salla-zero",
        "user_id": INBOX_TENANT,
        "trace_id": "trace-salla-zero",
        "connector_key": "salla_direct",
        "source": "salla_direct",
        "received_at": newer_ts,
        "salla_order_number": order_num,
        "salla_order_id": f"oid-{order_num}",
        "idempotency_key": f"salla_direct:order:{order_num}",
        "pipeline_stage": "NORMALIZED",
        "raw_payload": {"event": "salla_direct_sync",
                        "data": _salla_stripped_status_only(order_num)},
        "canonical_payload": {
            "order_id": f"oid-{order_num}",
            "order_number": order_num,
            "order_status": "completed",
            "order_status_native": "تم التنفيذ",
            "order_date": "2026-07-15T10:00:00+00:00",
            "total_amount": {"amount": 0.0, "currency": "SAR"},
            "currency": "SAR",
            "customer": {"name": "علي ك.", "phone": "+966501234567"},
            "items": [],
        },
        "stage_history": [],
    })

    pending = await list_pending_orders(
        db, user_id=INBOX_TENANT, days=365, status="completed")

    match = [o for o in pending["orders"] if o["order_number"] == order_num]
    assert len(match) == 1
    total_node = match[0]["total_amount"]
    assert total_node is not None

    def _amt(node):
        if isinstance(node, dict):
            return float(node.get("amount") or 0)
        return float(node or 0)

    assert _amt(total_node) == 134.0, (
        f"pending falsely displayed a zero total. Got: {total_node}"
    )


# ── L3 — send.py refuses zero-total invoices ────────────────────────
@pytest.mark.asyncio
async def test_send_refuses_zero_total_invoice(db):
    """L3: even if a caller manages to hit the send handler with a
    canonical showing 0 total, we NEVER post a zero invoice to قيود."""
    order_num = "271463605"

    # Insert only a zero-total salla_direct row (no fallback trace).
    await db.integration_inbox.insert_one({
        "id":            "row-zero-only",
        "user_id":       INBOX_TENANT,
        "trace_id":      "trace-zero",
        "connector_key": "salla_direct",
        "source":        "salla_direct",
        "received_at":   datetime.now(timezone.utc),
        "salla_order_number": order_num,
        "salla_order_id":     f"oid-{order_num}",
        "idempotency_key":    f"salla_direct:order:{order_num}",
        "pipeline_stage":     "NORMALIZED",
        "canonical_payload": {
            "order_number": order_num,
            "order_status": "completed",
            "order_status_native": "تم التنفيذ",
            "order_date": "2026-07-15T10:00:00+00:00",
            "total_amount": 0.0,   # normalizer emits flat float
            "currency":     "SAR",
            "customer":     {"name": "X", "phone": "+966501112222"},
            "items":        [],
            "payment_method": "mada",
        },
        "stage_history": [],
    })

    with pytest.raises(send_mod.ManualSendRefused) as exc:
        await send_mod.manual_send_one(
            db, user_id=INBOX_TENANT, order_number=order_num,
            actor="test")
    assert exc.value.code == "zero_total_refused"


@pytest.mark.asyncio
async def test_send_refuses_zero_total_even_when_other_trace_has_amount(db):
    """L3-b: guard fires when THIS row is zero and reports the
    positive amount from another trace as diagnostic info so the
    operator understands what went wrong."""
    order_num = "271463606"

    # Zero salla_direct row.
    await db.integration_inbox.insert_one({
        "id": "row-zero", "user_id": INBOX_TENANT,
        "trace_id": "trace-z",
        "connector_key": "salla_direct", "source": "salla_direct",
        "received_at": datetime.now(timezone.utc),
        "salla_order_number": order_num,
        "salla_order_id":     f"oid-{order_num}",
        "idempotency_key":    f"salla_direct:order:{order_num}",
        "pipeline_stage":     "NORMALIZED",
        "canonical_payload": {
            "order_number": order_num,
            "order_status": "completed",
            "order_status_native": "تم التنفيذ",
            "order_date":  "2026-07-15T10:00:00+00:00",
            "total_amount": 0.0,   # flat float, normalizer shape
            "customer": {"name": "X", "phone": "+966501112222"},
            "items": [], "payment_method": "mada",
        },
        "stage_history": [],
    })
    # Older Make row with the real amount.
    await db.integration_inbox.insert_one({
        "id": "row-make", "user_id": INBOX_TENANT,
        "trace_id": "trace-m",
        "connector_key": "make_com_qoyod", "source": "webhook",
        "received_at": datetime.now(timezone.utc) - timedelta(minutes=5),
        "salla_order_number": order_num,
        "salla_order_id":     f"oid-{order_num}",
        "idempotency_key":    f"salla:order:oid-{order_num}:x:completed",
        "pipeline_stage":     "NORMALIZED",
        "canonical_payload": {
            "order_number": order_num,
            "order_status": "completed",
            "total_amount": 134.0,   # flat float
            "items": [],
        },
        "stage_history": [],
    })

    with pytest.raises(send_mod.ManualSendRefused) as exc:
        await send_mod.manual_send_one(
            db, user_id=INBOX_TENANT, order_number=order_num,
            actor="test")
    assert exc.value.code == "zero_total_refused"
    extra = getattr(exc.value, "extra", None) or {}
    other = extra.get("other_trace_total") or {}
    if isinstance(other, dict):
        got = float(other.get("amount") or 0)
    else:
        got = float(other or 0)
    assert got == 134.0


# ── Regression: send still works for a healthy 134 SAR order ────────
@pytest.mark.asyncio
async def test_send_allowed_for_healthy_non_zero_order(db):
    """Baseline sanity: a healthy 134-SAR order does NOT trip the
    new guard (guard must be zero-total specific, not a blanket
    reject)."""
    order_num = "271463607"

    await db.integration_inbox.insert_one({
        "id": "row-ok", "user_id": INBOX_TENANT,
        "trace_id": "trace-ok",
        "connector_key": "salla_direct", "source": "salla_direct",
        "received_at": datetime.now(timezone.utc),
        "salla_order_number": order_num,
        "salla_order_id":     f"oid-{order_num}",
        "idempotency_key":    f"salla_direct:order:{order_num}",
        "pipeline_stage":     "NORMALIZED",
        "canonical_payload": {
            "order_number": order_num,
            "order_status": "completed",
            "order_status_native": "تم التنفيذ",
            "order_date": "2026-07-15T10:00:00+00:00",
            # Normalizer emits flat floats — mirror production shape.
            "total_amount": 134.0,
            "currency":     "SAR",
            "customer": {"name": "Y", "phone": "+966501113333"},
            "items": [], "payment_method": "mada",
        },
        "stage_history": [],
    })

    # We only need to prove the zero-total guard does NOT fire on
    # a healthy total. Send will fail later (no Qoyod creds), but
    # the failure code must NOT be `zero_total_refused`.
    with pytest.raises(send_mod.ManualSendRefused) as exc:
        await send_mod.manual_send_one(
            db, user_id=INBOX_TENANT, order_number=order_num,
            actor="test")
    assert exc.value.code != "zero_total_refused"
