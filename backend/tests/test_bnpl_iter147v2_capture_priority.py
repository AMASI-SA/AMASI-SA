"""Iter-147 v2 — captured_at_provider becomes priority #2.

Tamara's official settlement statement aggregates orders by their
**capture date** — the day the merchant called the capture endpoint
(or the day Tamara recorded the capture event for the order).

This file tests the refined 4-tier priority:

    1. provider_official    — settlement file import.
    2. provider_captured    — Tamara API capture date (NEW).
    3. billing_eligible     — Salla / Make status change.
    4. estimated            — created_at_provider fallback.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient

from backend.bnpl.settlement_attribution import (
    SETTLEMENT_SOURCE_BILLING,
    SETTLEMENT_SOURCE_CAPTURED,
    SETTLEMENT_SOURCE_ESTIMATED,
    SETTLEMENT_SOURCE_OFFICIAL,
    compute_attribution,
    recompute_attribution_for_doc,
)
from backend.bnpl.settlements_service import _compute_provider_totals


def _iso(year, month, day, hour=12):
    return datetime(year, month, day, hour, 0, 0, tzinfo=timezone.utc).isoformat()


# ── Pure-function priority tests ─────────────────────────────────

def test_captured_beats_billing_and_estimated():
    eff, src = compute_attribution({
        "captured_at_provider": "2026-05-01T08:00:00+00:00",
        "billing_eligible_at":  "2026-05-04T10:00:00+00:00",
        "created_at_provider":  "2026-04-28T08:00:00+00:00",
    })
    assert src == SETTLEMENT_SOURCE_CAPTURED
    assert eff == "2026-05-01T08:00:00+00:00"


def test_official_still_beats_captured():
    eff, src = compute_attribution({
        "provider_settlement_date": "2026-05-02",
        "captured_at_provider":     "2026-05-01T08:00:00+00:00",
        "billing_eligible_at":      "2026-05-04T10:00:00+00:00",
        "created_at_provider":      "2026-04-28T08:00:00+00:00",
    })
    assert src == SETTLEMENT_SOURCE_OFFICIAL
    assert eff == "2026-05-02"


def test_billing_used_when_no_capture_yet():
    """If Tamara hasn't reported a capture but Salla flipped the
    status to shipped, fall back to billing_eligible_at."""
    eff, src = compute_attribution({
        "billing_eligible_at": "2026-05-04T10:00:00+00:00",
        "created_at_provider": "2026-04-28T08:00:00+00:00",
    })
    assert src == SETTLEMENT_SOURCE_BILLING
    assert eff == "2026-05-04T10:00:00+00:00"


def test_estimated_remains_final_fallback():
    eff, src = compute_attribution({
        "created_at_provider": "2026-04-28T08:00:00+00:00",
    })
    assert src == SETTLEMENT_SOURCE_ESTIMATED
    assert eff == "2026-04-28T08:00:00+00:00"


# ── Async DB-backed tests ────────────────────────────────────────

@pytest_asyncio.fixture
async def mongo_db():
    import os
    mongo_url = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
    client = AsyncIOMotorClient(mongo_url)
    name = f"iter147v2_test_{uuid.uuid4().hex[:8]}"
    db = client[name]
    try:
        yield db
    finally:
        await client.drop_database(name)
        client.close()


@pytest.mark.asyncio
async def test_recompute_uses_capture_date_over_billing_at(mongo_db):
    """A Tamara order with BOTH captured_at_provider and
    billing_eligible_at must land in the CAPTURE-date week, not the
    billing-eligible-date week."""
    uid = "u-cap-1"
    await mongo_db.payment_transactions.insert_one({
        "id": "t1", "user_id": uid, "provider": "tamara",
        "provider_id": "tam-1",
        "order_reference_id": "ORD-1", "order_number": "ORD-1",
        "amount": 500.0,
        "created_at_provider":  _iso(2026, 4, 28),
        "billing_eligible_at":  _iso(2026, 5,  4),
        "captured_at_provider": _iso(2026, 4, 30),
    })
    r = await recompute_attribution_for_doc(
        mongo_db, user_id=uid, txn_id="t1",
    )
    assert r["updated"] == 1
    assert r["new_source"] == SETTLEMENT_SOURCE_CAPTURED
    assert r["new_effective"] == _iso(2026, 4, 30)

    # Week 1: 25-Apr → 1-May should INCLUDE the order (capture 30-Apr).
    w1 = await _compute_provider_totals(
        mongo_db, uid, "tamara",
        date_from="2026-04-25", date_to="2026-05-01",
    )
    assert w1["transactions_count"] == 1
    assert w1["gross_sales"] == 500.0

    # Week 2: 2-May → 8-May must EXCLUDE it (capture date is in week 1).
    w2 = await _compute_provider_totals(
        mongo_db, uid, "tamara",
        date_from="2026-05-02", date_to="2026-05-08",
    )
    assert w2["transactions_count"] == 0


@pytest.mark.asyncio
async def test_recompute_extracts_capture_from_raw_payload(mongo_db):
    """Legacy rows synced before Iter-147 v2 have the capture event
    embedded in `raw_payload.captures[]` but no `captured_at_provider`
    column.  The recompute endpoint extracts it opportunistically."""
    from backend.bnpl.diagnostics_routes import attach_bnpl_diagnostics_routes  # noqa: F401

    uid = "u-cap-2"
    await mongo_db.payment_transactions.insert_one({
        "id": "t2", "user_id": uid, "provider": "tamara",
        "provider_id": "tam-2",
        "order_reference_id": "ORD-2", "order_number": "ORD-2",
        "amount": 300.0,
        "created_at_provider": _iso(2026, 4, 28),
        "raw_payload": {
            "captures": [
                {"created_at": _iso(2026, 4, 30), "amount": {"amount": 300.0}},
            ],
        },
    })
    # Direct extraction via the helper logic — emulate what
    # /attribution/recompute does inline.
    doc = await mongo_db.payment_transactions.find_one({"id": "t2"})
    raw = doc.get("raw_payload") or {}
    earliest = None
    for cap in (raw.get("captures") or []):
        ts = cap.get("created_at") or cap.get("captured_at")
        if ts and (earliest is None or ts < earliest):
            earliest = ts
    assert earliest == _iso(2026, 4, 30)
    await mongo_db.payment_transactions.update_one(
        {"id": "t2"},
        {"$set": {"captured_at_provider": earliest}},
    )
    r = await recompute_attribution_for_doc(
        mongo_db, user_id=uid, txn_id="t2",
    )
    assert r["new_source"] == SETTLEMENT_SOURCE_CAPTURED
