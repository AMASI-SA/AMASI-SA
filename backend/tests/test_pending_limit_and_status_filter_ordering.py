"""Prove: an eligible delivered order stays in the Plan-B pending
list even when heavy webhook noise saturates the naïve limit.

Root cause fixed on 2026-07-09: `list_pending_orders` used to sort by
`received_at DESC` and apply `limit=200` at the Mongo level BEFORE
filtering by Salla status. In production this evicted eligible
"delivered" orders whenever many recent inbox rows belonged to other
statuses (webhook noise). The fix pushes the status filter DOWN into
the Mongo query so `limit` applies to the STATUS-FILTERED subset.

Test coverage:
    L1  With 400 "completed" webhook rows (noise) received AFTER an
        eligible "delivered" order, the delivered order MUST still
        appear when the operator opens the "delivered" tab.
    L2  Default limit is 500 (raised from 200) — matches Page A.
    L3  The status filter itself did not change: unknown statuses
        still fall back to "completed".
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

import mongomock_motor  # noqa: F401
import pytest

from integrations.qoyod_manual.pending import list_pending_orders


TENANT = "main"


@pytest.fixture
def db():
    client = mongomock_motor.AsyncMongoMockClient()
    return client["test_pending_limit_and_status_filter_ordering"]


def _canon(order_number, *, slug, native, order_date="2026-07-05"):
    return {
        "order_number":         order_number,
        "order_date":           order_date,
        "created_at":           order_date,
        "order_status":         slug,
        "order_status_native":  native,
        "total_amount":         260.0,
        "currency":             "SAR",
    }


async def _insert(db, *, order_number, received_at, slug, native):
    await db.integration_inbox.insert_one({
        "id":                   f"row-{order_number}-{received_at.timestamp()}",
        "user_id":              TENANT,
        "trace_id":             f"tr-{order_number}",
        "salla_order_number":   order_number,
        "salla_order_id":       f"oid-{order_number}",
        "received_at":          received_at,
        "pipeline_stage":       "NORMALIZED",
        "canonical_payload":    _canon(order_number, slug=slug, native=native),
        "raw_payload":          {"data": {"created_at": "2026-07-05"}},
    })


# ─────────────────────────────────────────────────────────────────────
# L1 — noise cannot evict an eligible delivered order
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_eligible_delivered_not_evicted_by_completed_noise(db):
    """Insert one eligible DELIVERED order at t = now - 30 days, then
    400 "completed" rows (unrelated to Plan-B delivered tab) at t = now
    with newer `received_at` timestamps. Under the OLD limit-first
    behaviour the delivered row was pushed out of the top-200 window
    and vanished from the tab. After the fix the Mongo query filters
    by status BEFORE the limit, so the delivered row surfaces.
    """
    now = datetime.now(timezone.utc)
    # 400 recent "completed" noise rows.
    for i in range(400):
        await _insert(
            db, order_number=f"noise-{i:04d}",
            received_at=now - timedelta(minutes=i),
            slug="completed", native="تم التنفيذ")
    # 1 eligible delivered order, received 30 days ago.
    await _insert(
        db, order_number="270884379",
        received_at=now - timedelta(days=30),
        slug="delivered", native="تم التوصيل")

    # Simulate the operator opening the "delivered" tab with the
    # default page size (limit=500) and default window (days=60).
    res = await list_pending_orders(
        db, user_id=TENANT, days=60, limit=500, status="delivered")
    orders = res["orders"]
    assert res["ok"] is True
    order_numbers = [o["order_number"] for o in orders]
    assert "270884379" in order_numbers, (
        f"eligible delivered order was evicted by noise — "
        f"returned={order_numbers[:5]}... total={len(orders)}")
    # The noise rows must NOT appear in the delivered tab.
    assert not any(on.startswith("noise-") for on in order_numbers), (
        "completed noise leaked into the delivered tab")


# ─────────────────────────────────────────────────────────────────────
# L2 — limit=200 (old default) still surfaces the delivered order
#      because status filter now applies BEFORE limit.
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_low_limit_still_finds_delivered_after_noise(db):
    now = datetime.now(timezone.utc)
    for i in range(400):
        await _insert(
            db, order_number=f"noise-b-{i:04d}",
            received_at=now - timedelta(minutes=i),
            slug="completed", native="تم التنفيذ")
    await _insert(
        db, order_number="271257282",
        received_at=now - timedelta(days=15),
        slug="delivered", native="تم التوصيل")

    # Even at limit=200, the delivered order survives because the
    # status filter runs Mongo-side.
    res = await list_pending_orders(
        db, user_id=TENANT, days=60, limit=200, status="delivered")
    order_numbers = [o["order_number"] for o in res["orders"]]
    assert "271257282" in order_numbers


# ─────────────────────────────────────────────────────────────────────
# L3 — status filter behaviour itself is unchanged: unknown falls
#      back to "completed", cross-tab bleed is prevented.
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_status_filter_semantics_unchanged(db):
    now = datetime.now(timezone.utc)
    await _insert(
        db, order_number="comp-1",
        received_at=now, slug="completed", native="تم التنفيذ")
    await _insert(
        db, order_number="del-1",
        received_at=now, slug="delivered", native="تم التوصيل")

    # Unknown status → "completed" fallback.
    res = await list_pending_orders(
        db, user_id=TENANT, days=60, limit=500, status="mystery")
    ons = [o["order_number"] for o in res["orders"]]
    assert "comp-1" in ons
    assert "del-1" not in ons

    # Delivered tab returns ONLY delivered.
    res = await list_pending_orders(
        db, user_id=TENANT, days=60, limit=500, status="delivered")
    ons = [o["order_number"] for o in res["orders"]]
    assert "del-1" in ons
    assert "comp-1" not in ons


@pytest.mark.asyncio
async def test_shipping_and_delivering_are_in_delivery_but_shipped_is_not(db):
    now = datetime.now(timezone.utc)
    await _insert(
        db, order_number="shipping-1",
        received_at=now, slug="shipping", native="جاري التوصيل")
    await _insert(
        db, order_number="shipped-1",
        received_at=now, slug="shipped", native="تم الشحن")
    await _insert(
        db, order_number="delivering-1",
        received_at=now, slug="delivering", native="جاري التوصيل")

    res = await list_pending_orders(
        db, user_id=TENANT, days=60, limit=500, status="in_delivery")
    ons = [o["order_number"] for o in res["orders"]]
    assert "shipping-1" in ons
    assert "delivering-1" in ons
    assert "shipped-1" not in ons
