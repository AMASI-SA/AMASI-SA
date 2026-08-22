"""Cross-tab duplicate prevention — Plan-B pending list (2026-07-09).

User acceptance criteria:
    • Same order_number never appears in more than one tab.
    • Order with newest status = delivered → NOT in in_delivery tab.
    • Order with newest status = completed → NOT in delivered
      or in_delivery tabs.
    • No change to send/payment logic.

The authoritative current state is seeded in `unified_orders`, while
the historical inbox traces remain available as operational evidence.
This guards the same cross-tab contract without allowing an old inbox
trace to re-introduce an order into a different tab.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

import mongomock_motor  # noqa: F401
import pytest

from integrations.qoyod_manual.pending import list_pending_orders
from integrations.qoyod_manual.pending_exclusion_diagnose import (
    diagnose_pending_exclusion,
)


TENANT = "main"


@pytest.fixture
def db():
    client = mongomock_motor.AsyncMongoMockClient()
    return client["test_cross_tab_dedupe"]


def _canon(order_number, *, slug, native, order_date="2026-07-05"):
    return {
        "order_number":        order_number,
        "order_date":          order_date,
        "created_at":          order_date,
        "order_status":        slug,
        "order_status_native": native,
        "total_amount":        260.0,
        "currency":            "SAR",
    }


async def _add_trace(db, *, order_number, received_at,
                     slug, native, trace_id=None, row_id=None):
    await db.integration_inbox.insert_one({
        "id":                  row_id or f"row-{order_number}-{received_at.timestamp()}",
        "user_id":              TENANT,
        "trace_id":             trace_id or f"tr-{order_number}-{slug}",
        "salla_order_number":   order_number,
        "salla_order_id":       f"oid-{order_number}",
        "received_at":          received_at,
        "pipeline_stage":       "NORMALIZED",
        "canonical_payload":    _canon(order_number, slug=slug, native=native),
        "raw_payload":          {"data": {"created_at": "2026-07-05"}},
    })
    # `unified_orders` is the candidate authority. Updating this row models
    # Salla's current order state; inbox rows above are trace evidence only.
    await db.unified_orders.update_one(
        {"user_id": TENANT, "order_number": str(order_number)},
        {"$set": {
            "user_id": TENANT,
            "order_id": f"oid-{order_number}",
            "order_number": str(order_number),
            "order_date": "2026-07-05",
            "order_status_slug": slug,
            "order_status": slug,
            "order_status_native": native,
            "payment_status": "paid",
            "paid_amount": 260.0,
            "remaining_amount": 0.0,
            "has_remaining_amount": False,
            "total_amount": 260.0,
            "currency": "SAR",
            "updated_at": received_at,
        }},
        upsert=True,
    )


# ─────────────────────────────────────────────────────────────────────
# T1 — same order never in more than one tab
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_cross_tab_duplicate_prevented(db):
    """Order X has traces in ALL three statuses. It must land in
    exactly the tab matching its NEWEST trace (completed) and be
    absent from the other two."""
    now = datetime.now(timezone.utc)
    # Ascending time: in_delivery → delivered → completed (newest).
    await _add_trace(
        db, order_number="270884379",
        received_at=now - timedelta(hours=6),
        slug="in_delivery", native="جاري التوصيل",
        trace_id="tr-old")
    await _add_trace(
        db, order_number="270884379",
        received_at=now - timedelta(hours=3),
        slug="delivered", native="تم التوصيل",
        trace_id="tr-mid")
    await _add_trace(
        db, order_number="270884379",
        received_at=now,
        slug="completed", native="تم التنفيذ",
        trace_id="tr-new")

    tab_membership: dict[str, list[str]] = {}
    for tab in ("in_delivery", "delivered", "completed"):
        r = await list_pending_orders(
            db, user_id=TENANT, days=60, limit=500, status=tab)
        tab_membership[tab] = [o["order_number"] for o in r["orders"]]

    # Newest = completed → appears in completed tab only.
    assert "270884379" in tab_membership["completed"]
    assert "270884379" not in tab_membership["delivered"]
    assert "270884379" not in tab_membership["in_delivery"]


# ─────────────────────────────────────────────────────────────────────
# T2 — newest trace wins over older matching trace
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_newest_trace_wins(db):
    """Order X has a NEWER `delivered` trace and an OLDER `completed`
    trace. It must NOT appear in the completed tab even though a
    completed trace exists in history."""
    now = datetime.now(timezone.utc)
    await _add_trace(
        db, order_number="271257282",
        received_at=now - timedelta(hours=2),
        slug="completed", native="تم التنفيذ")
    await _add_trace(
        db, order_number="271257282",
        received_at=now,
        slug="delivered", native="تم التوصيل")

    r_delivered = await list_pending_orders(
        db, user_id=TENANT, days=60, limit=500, status="delivered")
    r_completed = await list_pending_orders(
        db, user_id=TENANT, days=60, limit=500, status="completed")

    ons_delivered = [o["order_number"] for o in r_delivered["orders"]]
    ons_completed = [o["order_number"] for o in r_completed["orders"]]

    assert "271257282" in ons_delivered
    assert "271257282" not in ons_completed


# ─────────────────────────────────────────────────────────────────────
# T3 — two traces with the same status dedupe correctly
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_two_traces_same_status_dedupe(db):
    """Duplicate webhooks for the same status → the order appears
    ONCE in the correct tab, never twice."""
    now = datetime.now(timezone.utc)
    await _add_trace(
        db, order_number="269591641",
        received_at=now - timedelta(minutes=10),
        slug="delivered", native="تم التوصيل",
        trace_id="tr-a")
    await _add_trace(
        db, order_number="269591641",
        received_at=now,
        slug="delivered", native="تم التوصيل",
        trace_id="tr-b")

    r = await list_pending_orders(
        db, user_id=TENANT, days=60, limit=500, status="delivered")
    ons = [o["order_number"] for o in r["orders"]]
    assert ons.count("269591641") == 1
    # And the surviving row is the NEWEST one.
    surviving = next(o for o in r["orders"]
                     if o["order_number"] == "269591641")
    assert surviving["trace_id"] == "tr-b"


# ─────────────────────────────────────────────────────────────────────
# T4 — diagnose_pending_exclusion reports is_newest + total_traces
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_diagnose_reports_newest_flag_and_trace_count(db):
    now = datetime.now(timezone.utc)
    await _add_trace(
        db, order_number="269826009",
        received_at=now - timedelta(hours=4),
        slug="in_delivery", native="جاري التوصيل",
        trace_id="tr-old-269826009")
    await _add_trace(
        db, order_number="269826009",
        received_at=now,
        slug="delivered", native="تم التوصيل",
        trace_id="tr-new-269826009")

    # Case A: default (analyse newest).
    res = await diagnose_pending_exclusion(
        db, user_id=TENANT,
        order_numbers=["269826009"], status="delivered")
    entry = res["orders"][0]
    assert entry["is_newest_trace"] is True
    assert entry["total_traces_for_order"] == 2
    assert entry["analysed_trace_id"] == "tr-new-269826009"
    assert entry["verdict"] == "would_appear_in_page_b"

    # Case B: pin the OLDER trace and confirm the not_newest_trace
    # exclusion reason surfaces cleanly.
    res2 = await diagnose_pending_exclusion(
        db, user_id=TENANT,
        order_numbers=["269826009"], status="delivered",
        trace_ids_by_order={"269826009": "tr-old-269826009"})
    entry2 = res2["orders"][0]
    assert entry2["is_newest_trace"] is False
    assert entry2["total_traces_for_order"] == 2
    assert entry2["analysed_trace_id"] == "tr-old-269826009"
    assert entry2["verdict"] == "excluded"
    assert entry2["primary_exclusion_reason"] == "not_newest_trace"


# ─────────────────────────────────────────────────────────────────────
# T5 — single-trace orders unaffected (regression guard)
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_single_trace_orders_unaffected(db):
    """Orders with exactly one trace behave identically to before
    the aggregation refactor."""
    now = datetime.now(timezone.utc)
    await _add_trace(
        db, order_number="269813481",
        received_at=now,
        slug="delivered", native="تم التوصيل")
    await _add_trace(
        db, order_number="270000042",
        received_at=now - timedelta(minutes=5),
        slug="completed", native="تم التنفيذ")

    r_del = await list_pending_orders(
        db, user_id=TENANT, days=60, limit=500, status="delivered")
    r_comp = await list_pending_orders(
        db, user_id=TENANT, days=60, limit=500, status="completed")

    ons_del  = [o["order_number"] for o in r_del["orders"]]
    ons_comp = [o["order_number"] for o in r_comp["orders"]]

    assert ons_del  == ["269813481"]
    assert ons_comp == ["270000042"]
