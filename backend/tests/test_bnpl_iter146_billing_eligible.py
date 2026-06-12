"""Iter-146 — Tamara `billing_eligible_at` settlement-cycle tests.

Validates the new rule:

  • Tamara orders enter the weekly settlement on the week they FIRST
    reach a billable status (shipped / prepared / out-for-delivery /
    delivered / executed), NOT on `created_at_provider`.
  • Stamp is idempotent — the first billable transition wins.
  • Refunds keep `refunded_at` aggregation (Iter-120 unchanged).
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient

from backend.bnpl.billing_eligible import (
    BILLABLE_STATUSES,
    is_billable_status,
    mark_billing_eligible_for_order,
    propagate_status_to_billing_eligible,
)
from backend.bnpl.settlements_service import _compute_provider_totals


# ── Pure-function tests (no DB) ───────────────────────────────────

def test_billable_statuses_include_all_arabic_targets():
    """The five Arabic statuses the merchant requested are all present."""
    must_have = {
        "تم التنفيذ", "جاري التوصيل",
        "تم التوصيل", "تم التجهيز", "تم الشحن",
    }
    assert must_have.issubset(BILLABLE_STATUSES)


def test_is_billable_status_arabic_and_english():
    assert is_billable_status("تم التوصيل")
    assert is_billable_status("delivered")
    assert is_billable_status("SHIPPED")  # case-insensitive English
    assert not is_billable_status("بانتظار المراجعة")
    assert not is_billable_status("pending")
    assert not is_billable_status("")
    assert not is_billable_status(None)


# ── Async DB-backed tests ─────────────────────────────────────────

@pytest_asyncio.fixture
async def mongo_db():
    """Spin up a throwaway DB tied to a unique name per test session."""
    import os
    mongo_url = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
    client = AsyncIOMotorClient(mongo_url)
    name = f"iter146_test_{uuid.uuid4().hex[:8]}"
    db = client[name]
    try:
        yield db
    finally:
        await client.drop_database(name)
        client.close()


def _iso(year, month, day, hour=12):
    return datetime(year, month, day, hour, 0, 0, tzinfo=timezone.utc).isoformat()


@pytest.mark.asyncio
async def test_mark_billing_eligible_first_stamp_wins(mongo_db):
    uid = "user-1"
    ref = "ORD-001"
    # Insert a Tamara txn with no billing_eligible_at.
    await mongo_db.payment_transactions.insert_one({
        "id": "t1", "user_id": uid, "provider": "tamara",
        "provider_id": "tamara-1", "order_reference_id": ref,
        "order_number": ref,
        "amount": 250.0, "created_at_provider": _iso(2026, 5, 10),
    })
    # First stamp = May 12.
    r1 = await mark_billing_eligible_for_order(
        mongo_db, uid, order_reference_id=ref, event_at=_iso(2026, 5, 12),
    )
    assert r1["updated"] == 1
    # Second stamp = May 20 should NOT overwrite (idempotent).
    r2 = await mark_billing_eligible_for_order(
        mongo_db, uid, order_reference_id=ref, event_at=_iso(2026, 5, 20),
    )
    assert r2["updated"] == 0
    doc = await mongo_db.payment_transactions.find_one({"id": "t1"})
    assert doc["billing_eligible_at"] == _iso(2026, 5, 12)


@pytest.mark.asyncio
async def test_propagate_skips_non_billable_status(mongo_db):
    uid = "user-1"
    ref = "ORD-002"
    await mongo_db.payment_transactions.insert_one({
        "id": "t2", "user_id": uid, "provider": "tamara",
        "provider_id": "tamara-2", "order_reference_id": ref,
        "order_number": ref,
        "amount": 100.0, "created_at_provider": _iso(2026, 5, 1),
    })
    r = await propagate_status_to_billing_eligible(
        mongo_db, uid, order_reference_id=ref,
        new_status="بانتظار المراجعة", event_at=_iso(2026, 5, 5),
    )
    assert r["updated"] == 0
    doc = await mongo_db.payment_transactions.find_one({"id": "t2"})
    assert doc.get("billing_eligible_at") in (None, "")


@pytest.mark.asyncio
async def test_settlement_uses_billing_eligible_for_tamara(mongo_db):
    """An order created in week-1 but only made billable in week-2 must
    appear in WEEK-2's Tamara settlement, not week-1's."""
    uid = "user-1"
    ref = "ORD-003"
    # Order created on 2026-04-30 (Thursday) but billable on 2026-05-04 (Mon).
    await mongo_db.payment_transactions.insert_one({
        "id": "t3", "user_id": uid, "provider": "tamara",
        "provider_id": "tamara-3", "order_reference_id": ref,
        "order_number": ref,
        "amount": 500.0,
        "created_at_provider": _iso(2026, 4, 30),
        "billing_eligible_at":  _iso(2026, 5,  4),
        # Iter-147 — settlement engine now filters by effective_settlement_date.
        "effective_settlement_date": _iso(2026, 5, 4),
        "settlement_source": "billing_eligible",
    })

    # Settlement window 2026-04-25 → 2026-05-01 must EXCLUDE this order.
    totals_w1 = await _compute_provider_totals(
        mongo_db, uid, "tamara",
        date_from="2026-04-25", date_to="2026-05-01",
    )
    assert totals_w1["transactions_count"] == 0
    assert totals_w1["gross_sales"] == 0.0

    # Settlement window 2026-05-02 → 2026-05-08 must INCLUDE this order.
    totals_w2 = await _compute_provider_totals(
        mongo_db, uid, "tamara",
        date_from="2026-05-02", date_to="2026-05-08",
    )
    assert totals_w2["transactions_count"] == 1
    assert totals_w2["gross_sales"] == 500.0


@pytest.mark.asyncio
async def test_settlement_excludes_unstamped_tamara_orders(mongo_db):
    """A Tamara order that NEVER reached a billable status is excluded
    from every weekly settlement until it does."""
    uid = "user-1"
    ref = "ORD-004"
    await mongo_db.payment_transactions.insert_one({
        "id": "t4", "user_id": uid, "provider": "tamara",
        "provider_id": "tamara-4", "order_reference_id": ref,
        "order_number": ref,
        "amount": 999.0,
        "created_at_provider": _iso(2026, 5, 1),
        # NO billing_eligible_at — still in awaiting-review status.
    })
    totals = await _compute_provider_totals(
        mongo_db, uid, "tamara",
        date_from="2026-04-25", date_to="2026-05-08",
    )
    assert totals["transactions_count"] == 0


@pytest.mark.asyncio
async def test_settlement_still_uses_created_at_for_tabby(mongo_db):
    """Tabby is unaffected — keeps the legacy `created_at_provider`
    filter so its settlement statement still reconciles."""
    uid = "user-1"
    await mongo_db.payment_transactions.insert_one({
        "id": "p1", "user_id": uid, "provider": "tabby",
        "provider_id": "tabby-1", "order_reference_id": "T-1",
        "order_number": "T-1",
        "amount": 700.0,
        "created_at_provider": _iso(2026, 4, 28),
        # billing_eligible_at intentionally absent for tabby.
    })
    totals = await _compute_provider_totals(
        mongo_db, uid, "tabby",
        date_from="2026-04-25", date_to="2026-05-01",
    )
    assert totals["transactions_count"] == 1
    assert totals["gross_sales"] == 700.0


@pytest.mark.asyncio
async def test_refunds_keep_refunded_at_filter(mongo_db):
    """Iter-120 unchanged — Tamara refunds still aggregate by
    `refunded_at`, not by the original order's billing_eligible_at."""
    uid = "user-1"
    # Original order placed AND made billable in April.
    await mongo_db.payment_transactions.insert_one({
        "id": "t5", "user_id": uid, "provider": "tamara",
        "provider_id": "tamara-5", "order_reference_id": "ORD-005",
        "order_number": "ORD-005",
        "amount": 800.0,
        "created_at_provider": _iso(2026, 4, 10),
        "billing_eligible_at":  _iso(2026, 4, 14),
        # Iter-147 — engine filters by effective_settlement_date.
        "effective_settlement_date": _iso(2026, 4, 14),
        "settlement_source": "billing_eligible",
    })
    # Refund happens in May.
    await mongo_db.payment_refunds.insert_one({
        "id": "r5", "user_id": uid, "provider": "tamara",
        "provider_payment_id": "tamara-5",
        "provider_refund_id":  "ref-5",
        "order_reference_id":  "ORD-005",
        "amount": 200.0,
        "refunded_at": _iso(2026, 5, 6),
    })
    # April week — sales present, refunds absent.
    apr = await _compute_provider_totals(
        mongo_db, uid, "tamara",
        date_from="2026-04-13", date_to="2026-04-19",
    )
    assert apr["transactions_count"] == 1
    assert apr["total_refunds"] == 0.0

    # May week — sales absent, refund present (Iter-120 rule preserved).
    may = await _compute_provider_totals(
        mongo_db, uid, "tamara",
        date_from="2026-05-04", date_to="2026-05-10",
    )
    assert may["transactions_count"] == 0
    assert may["total_refunds"] == 200.0
