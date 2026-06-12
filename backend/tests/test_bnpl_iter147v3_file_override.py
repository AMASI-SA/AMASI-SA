"""Iter-147 v3 — Tamara settlement file overrides computed totals.

When the merchant has uploaded an OFFICIAL Tamara settlement file
that covers the queried period, the weekly invoice MUST use the
file's per-order entries as the source of truth (matching Tamara's
statement to the cent) instead of recomputing from
`payment_transactions` (which can drift due to missing webhooks).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient

from backend.bnpl.settlements_service import (
    _aggregate_official_totals,
    compute_settlement_for_provider,
)


@pytest_asyncio.fixture
async def mongo_db():
    import os
    mongo_url = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
    client = AsyncIOMotorClient(mongo_url)
    name = f"iter147v3_test_{uuid.uuid4().hex[:8]}"
    db = client[name]
    try:
        yield db
    finally:
        await client.drop_database(name)
        client.close()


def _iso(year, month, day, hour=12):
    return datetime(year, month, day, hour, 0, 0, tzinfo=timezone.utc).isoformat()


# ── Aggregate-helper tests ────────────────────────────────────────

@pytest.mark.asyncio
async def test_aggregate_returns_none_when_no_file(mongo_db):
    out = await _aggregate_official_totals(
        mongo_db, "u1", "2026-04-25", "2026-05-01",
    )
    assert out is None


@pytest.mark.asyncio
async def test_aggregate_sums_sales_and_refunds(mongo_db):
    uid = "u1"
    file_id = "f1"
    # Two sales rows + one refund row.
    await mongo_db.settlement_entries.insert_many([
        {
            "id": "e1", "file_id": file_id, "user_id": uid,
            "provider": "tamara",
            "order_number": "100", "tamara_order_id": "tx1",
            "event_type": "sale",
            "actual_amount": 1000.0, "actual_fees": 70.0,
            "actual_vat": 10.5, "actual_net_amount": 919.5,
            "settlement_date": "2026-04-28",
        },
        {
            "id": "e2", "file_id": file_id, "user_id": uid,
            "provider": "tamara",
            "order_number": "101", "tamara_order_id": "tx2",
            "event_type": "sale",
            "actual_amount": 500.0, "actual_fees": 35.0,
            "actual_vat": 5.25, "actual_net_amount": 459.75,
            "settlement_date": "2026-04-30",
        },
        {
            "id": "e3", "file_id": file_id, "user_id": uid,
            "provider": "tamara",
            "order_number": "099", "tamara_order_id": "tx0",
            "event_type": "refund",
            "actual_refund_amount": 200.0,
            "actual_partial_refund_amount": 0.0,
            "actual_fees": -14.0, "actual_vat": -2.1,
            "actual_net_amount": -183.9,
            "settlement_date": "2026-04-29",
        },
    ])
    out = await _aggregate_official_totals(
        mongo_db, uid, "2026-04-25", "2026-05-01",
    )
    assert out is not None
    assert out["transactions_count"] == 2
    assert out["refunds_count"] == 1
    assert out["gross_sales"] == 1500.0
    assert out["total_refunds"] == 200.0
    assert out["commission"] == 91.0      # 70 + 35 + (-14)
    assert out["commission_vat"] == 13.65
    assert out["net_payable"] == 1195.35  # 919.5 + 459.75 + (-183.9)


# ── Integration: full compute_settlement_for_provider override ────

@pytest.mark.asyncio
async def test_official_file_overrides_computed_totals(mongo_db):
    """Even if `payment_transactions` says 17,160 gross_sales, the
    settlement engine MUST return Tamara's official numbers when a
    file is present."""
    uid = "u1"
    # 1. Plant payment_transactions with WRONG totals (DB drift).
    await mongo_db.payment_transactions.insert_one({
        "id": "ptx-1", "user_id": uid, "provider": "tamara",
        "provider_id": "tam-1",
        "order_reference_id": "OLD-1", "order_number": "OLD-1",
        "amount": 17160.15,  # ← what our DB has, WRONG vs Tamara
        "created_at_provider": _iso(2026, 4, 28),
        "effective_settlement_date": _iso(2026, 4, 28),
        "settlement_source": "estimated",
    })
    # 2. Plant Tamara settlement_entries with the OFFICIAL numbers.
    await mongo_db.settlement_entries.insert_one({
        "id": "e-off-1", "user_id": uid, "provider": "tamara",
        "file_id": "f-1", "order_number": "REAL-1",
        "tamara_order_id": "tam-real-1",
        "event_type": "sale",
        "actual_amount": 17294.15,           # ← TRUE gross
        "actual_fees": 1336.42,              # ← TRUE commission
        "actual_vat": 200.44,                # ← TRUE VAT
        "actual_net_amount": 15617.29,       # ← TRUE net
        "settlement_date": "2026-04-28",
    })
    await mongo_db.bnpl_settings.insert_one({
        "user_id": uid, "provider": "tamara", "enabled": True,
    })

    result = await compute_settlement_for_provider(
        mongo_db, uid, "tamara",
        date_from="2026-04-25", date_to="2026-05-01",
    )
    assert result["data_source"] == "provider_official_file"
    assert result["totals"]["gross_sales"] == 17294.15
    assert result["totals"]["commission"] == 1336.42
    assert result["totals"]["commission_vat"] == 200.44
    assert result["totals"]["net_payable"] == 15617.29
    # The computed snapshot is preserved for the UI diff card.
    assert result["system_totals"] is not None
    assert result["system_totals"]["gross_sales"] == 17160.15


@pytest.mark.asyncio
async def test_no_override_when_no_file_exists(mongo_db):
    """Without any settlement file, the engine falls back to its
    normal computed totals (data_source = 'computed')."""
    uid = "u1"
    await mongo_db.bnpl_settings.insert_one({
        "user_id": uid, "provider": "tamara", "enabled": True,
    })
    await mongo_db.payment_transactions.insert_one({
        "id": "ptx-2", "user_id": uid, "provider": "tamara",
        "provider_id": "tam-2",
        "amount": 500.0,
        "created_at_provider": _iso(2026, 4, 28),
        "effective_settlement_date": _iso(2026, 4, 28),
        "settlement_source": "estimated",
    })
    result = await compute_settlement_for_provider(
        mongo_db, uid, "tamara",
        date_from="2026-04-25", date_to="2026-05-01",
    )
    assert result["data_source"] == "computed"
    assert result["system_totals"] is None
    assert result["totals"]["gross_sales"] == 500.0
