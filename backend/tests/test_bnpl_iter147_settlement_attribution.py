"""Iter-147 — Tamara settlement attribution priority tests.

Verifies the 3-tier priority:

    1. provider_official  — wins whenever any of (provider_settlement_id,
       provider_invoice_id, provider_settlement_date) is set.
    2. billing_eligible   — fallback to Iter-146 status-driven stamp.
    3. estimated          — last-resort fallback to created_at_provider.

Also verifies that:
  • Re-importing the same settlement file does NOT clobber the
    previously-set provider_settlement_date (first-stamp wins).
  • `effective_settlement_date` flips correctly when an estimated
    row gets upgraded to provider_official via a later import.
  • The audit log in `tamara_attribution_log` captures the transition.
  • `_compute_provider_totals` groups Tamara orders by
    `effective_settlement_date`, not by raw `created_at_provider`.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient

from backend.bnpl.settlement_attribution import (
    SETTLEMENT_SOURCE_BILLING,
    SETTLEMENT_SOURCE_ESTIMATED,
    SETTLEMENT_SOURCE_OFFICIAL,
    compute_attribution,
    recompute_attribution_for_doc,
    set_provider_official_attribution,
)
from backend.bnpl.settlements_service import _compute_provider_totals


# ── Pure-function tests ──────────────────────────────────────────

def test_priority_provider_official_wins():
    """Even if billing_eligible_at and created_at_provider both exist,
    a provider_settlement_date overrides them."""
    eff, src = compute_attribution({
        "provider_settlement_date": "2026-05-02",
        "billing_eligible_at":      "2026-05-04T10:00:00+00:00",
        "created_at_provider":      "2026-04-28T08:00:00+00:00",
    })
    assert eff == "2026-05-02"
    assert src == SETTLEMENT_SOURCE_OFFICIAL


def test_priority_provider_official_via_settlement_id_alone():
    """If only provider_settlement_id is set but the date isn't, we
    fall back through payout_date → billing_eligible_at →
    created_at_provider to derive the date — source stays OFFICIAL."""
    eff, src = compute_attribution({
        "provider_settlement_id": "STMT-XYZ",
        "billing_eligible_at":    "2026-05-04T10:00:00+00:00",
        "created_at_provider":    "2026-04-28T08:00:00+00:00",
    })
    assert eff == "2026-05-04T10:00:00+00:00"
    assert src == SETTLEMENT_SOURCE_OFFICIAL


def test_priority_billing_eligible_when_no_official():
    eff, src = compute_attribution({
        "billing_eligible_at": "2026-05-04T10:00:00+00:00",
        "created_at_provider": "2026-04-28T08:00:00+00:00",
    })
    assert eff == "2026-05-04T10:00:00+00:00"
    assert src == SETTLEMENT_SOURCE_BILLING


def test_priority_estimated_as_last_resort():
    eff, src = compute_attribution({
        "created_at_provider": "2026-04-28T08:00:00+00:00",
    })
    assert eff == "2026-04-28T08:00:00+00:00"
    assert src == SETTLEMENT_SOURCE_ESTIMATED


def test_priority_returns_none_when_no_signal():
    eff, src = compute_attribution({})
    assert eff is None
    assert src == SETTLEMENT_SOURCE_ESTIMATED


# ── Async DB-backed tests ────────────────────────────────────────

@pytest_asyncio.fixture
async def mongo_db():
    import os
    mongo_url = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
    client = AsyncIOMotorClient(mongo_url)
    name = f"iter147_test_{uuid.uuid4().hex[:8]}"
    db = client[name]
    try:
        yield db
    finally:
        await client.drop_database(name)
        client.close()


def _iso(year, month, day, hour=12):
    return datetime(year, month, day, hour, 0, 0, tzinfo=timezone.utc).isoformat()


@pytest.mark.asyncio
async def test_recompute_promotes_estimated_to_billing(mongo_db):
    """An estimated row gets promoted to billing_eligible once
    billing_eligible_at is set, and the audit log captures it."""
    uid = "u1"
    await mongo_db.payment_transactions.insert_one({
        "id": "t1", "user_id": uid, "provider": "tamara",
        "provider_id": "tam-1",
        "order_reference_id": "ORD-1", "order_number": "ORD-1",
        "amount": 100.0,
        "created_at_provider": _iso(2026, 4, 28),
        "effective_settlement_date": _iso(2026, 4, 28),
        "settlement_source": SETTLEMENT_SOURCE_ESTIMATED,
    })
    # Now stamp billing_eligible_at and re-compute.
    await mongo_db.payment_transactions.update_one(
        {"id": "t1"},
        {"$set": {"billing_eligible_at": _iso(2026, 5, 4)}},
    )
    r = await recompute_attribution_for_doc(
        mongo_db, user_id=uid, txn_id="t1",
    )
    assert r["updated"] == 1
    assert r["old_source"] == SETTLEMENT_SOURCE_ESTIMATED
    assert r["new_source"] == SETTLEMENT_SOURCE_BILLING

    log = await mongo_db.tamara_attribution_log.find_one({"txn_id": "t1"})
    assert log is not None
    assert log["new_source"] == SETTLEMENT_SOURCE_BILLING


@pytest.mark.asyncio
async def test_set_provider_official_upgrades_attribution(mongo_db):
    """A row currently `billing_eligible` becomes `provider_official`
    after a settlement-file import, and its effective_settlement_date
    moves to the official date."""
    uid = "u1"
    await mongo_db.payment_transactions.insert_one({
        "id": "t2", "user_id": uid, "provider": "tamara",
        "provider_id": "tam-2",
        "order_reference_id": "ORD-2", "order_number": "ORD-2",
        "amount": 250.0,
        "created_at_provider": _iso(2026, 4, 28),
        "billing_eligible_at": _iso(2026, 5, 4),
        "effective_settlement_date": _iso(2026, 5, 4),
        "settlement_source": SETTLEMENT_SOURCE_BILLING,
    })
    r = await set_provider_official_attribution(
        mongo_db, uid,
        order_number="ORD-2",
        provider_settlement_id="STMT-9001",
        provider_settlement_date="2026-05-02",
    )
    assert r["matched"] == 1
    assert r["recomputed"] >= 1
    doc = await mongo_db.payment_transactions.find_one({"id": "t2"})
    assert doc["provider_settlement_id"] == "STMT-9001"
    assert doc["provider_settlement_date"] == "2026-05-02"
    assert doc["effective_settlement_date"] == "2026-05-02"
    assert doc["settlement_source"] == SETTLEMENT_SOURCE_OFFICIAL


@pytest.mark.asyncio
async def test_set_provider_official_is_first_stamp_wins(mongo_db):
    """Re-importing a different settlement file MUST NOT clobber the
    first official attribution (data-integrity contract)."""
    uid = "u1"
    await mongo_db.payment_transactions.insert_one({
        "id": "t3", "user_id": uid, "provider": "tamara",
        "provider_id": "tam-3",
        "order_reference_id": "ORD-3", "order_number": "ORD-3",
        "amount": 300.0,
        "created_at_provider": _iso(2026, 4, 28),
    })
    # First import — STMT-A.
    await set_provider_official_attribution(
        mongo_db, uid, order_number="ORD-3",
        provider_settlement_id="STMT-A",
        provider_settlement_date="2026-05-02",
    )
    # Second import — STMT-B, DIFFERENT date.
    await set_provider_official_attribution(
        mongo_db, uid, order_number="ORD-3",
        provider_settlement_id="STMT-B",
        provider_settlement_date="2026-05-09",
    )
    doc = await mongo_db.payment_transactions.find_one({"id": "t3"})
    assert doc["provider_settlement_id"] == "STMT-A"
    assert doc["provider_settlement_date"] == "2026-05-02"


@pytest.mark.asyncio
async def test_settlement_engine_uses_effective_date(mongo_db):
    """The settlement engine for Tamara MUST aggregate by
    `effective_settlement_date`, not by `created_at_provider`."""
    uid = "u1"
    # Order created in week-1 but settlement officially booked in week-2.
    await mongo_db.payment_transactions.insert_one({
        "id": "t4", "user_id": uid, "provider": "tamara",
        "provider_id": "tam-4",
        "order_reference_id": "ORD-4", "order_number": "ORD-4",
        "amount": 600.0,
        "created_at_provider": _iso(2026, 4, 28),
        "billing_eligible_at": _iso(2026, 5, 4),
        "provider_settlement_id": "STMT-W2",
        "provider_settlement_date": "2026-05-02",
        "effective_settlement_date": "2026-05-02T00:00:00+00:00",
        "settlement_source": SETTLEMENT_SOURCE_OFFICIAL,
    })

    # Week-1 query — must NOT include the order.
    w1 = await _compute_provider_totals(
        mongo_db, uid, "tamara",
        date_from="2026-04-25", date_to="2026-05-01",
    )
    assert w1["transactions_count"] == 0

    # Week-2 query — MUST include the order.
    w2 = await _compute_provider_totals(
        mongo_db, uid, "tamara",
        date_from="2026-05-02", date_to="2026-05-08",
    )
    assert w2["transactions_count"] == 1
    assert w2["gross_sales"] == 600.0


@pytest.mark.asyncio
async def test_estimated_rows_excluded_until_attribution_runs(mongo_db):
    """Sanity: a row with neither billing_eligible_at nor provider_*
    fields and no effective_settlement_date yet is excluded from the
    settlement until a recompute fills it in."""
    uid = "u1"
    await mongo_db.payment_transactions.insert_one({
        "id": "t5", "user_id": uid, "provider": "tamara",
        "provider_id": "tam-5",
        "order_reference_id": "ORD-5", "order_number": "ORD-5",
        "amount": 90.0,
        "created_at_provider": _iso(2026, 5, 1),
        # NO effective_settlement_date — should NOT appear.
    })
    apr = await _compute_provider_totals(
        mongo_db, uid, "tamara",
        date_from="2026-04-25", date_to="2026-05-08",
    )
    assert apr["transactions_count"] == 0

    # Now run recompute → estimated source picks created_at_provider.
    await recompute_attribution_for_doc(
        mongo_db, user_id=uid, txn_id="t5",
    )
    doc = await mongo_db.payment_transactions.find_one({"id": "t5"})
    assert doc["settlement_source"] == SETTLEMENT_SOURCE_ESTIMATED
    assert doc["effective_settlement_date"] == _iso(2026, 5, 1)

    apr2 = await _compute_provider_totals(
        mongo_db, uid, "tamara",
        date_from="2026-04-25", date_to="2026-05-08",
    )
    assert apr2["transactions_count"] == 1
    assert apr2["gross_sales"] == 90.0
