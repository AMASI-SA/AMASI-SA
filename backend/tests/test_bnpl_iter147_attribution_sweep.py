"""Iter-147 — Daily Tamara attribution sweep test."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient

from backend.bnpl.auto_sync_service import run_tamara_attribution_sweep
from backend.bnpl.settlement_attribution import (
    SETTLEMENT_SOURCE_BILLING,
    SETTLEMENT_SOURCE_ESTIMATED,
)


@pytest_asyncio.fixture
async def mongo_db():
    import os
    mongo_url = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
    client = AsyncIOMotorClient(mongo_url)
    name = f"iter147_sweep_{uuid.uuid4().hex[:8]}"
    db = client[name]
    try:
        yield db
    finally:
        await client.drop_database(name)
        client.close()


def _iso(year, month, day, hour=12):
    return datetime(year, month, day, hour, 0, 0, tzinfo=timezone.utc).isoformat()


@pytest.mark.asyncio
async def test_daily_sweep_promotes_legacy_rows(mongo_db):
    """A legacy Tamara row inserted before Iter-147 has no
    `effective_settlement_date` / `settlement_source`.  The daily
    sweep MUST detect and stamp it as estimated."""
    uid = "u-sweep-1"
    await mongo_db.bnpl_settings.insert_one(
        {"user_id": uid, "provider": "tamara", "enabled": True},
    )
    await mongo_db.payment_transactions.insert_one({
        "id": "leg-1", "user_id": uid, "provider": "tamara",
        "provider_id": "tam-leg-1",
        "order_reference_id": "OLD-1", "order_number": "OLD-1",
        "amount": 75.0,
        "created_at_provider": _iso(2026, 4, 20),
        # Note: no effective_settlement_date / settlement_source.
    })

    summary = await run_tamara_attribution_sweep(mongo_db)
    assert summary["users_processed"] == 1
    assert summary["rows_scanned"] == 1
    assert summary["rows_updated"] == 1

    doc = await mongo_db.payment_transactions.find_one({"id": "leg-1"})
    assert doc["settlement_source"] == SETTLEMENT_SOURCE_ESTIMATED
    assert doc["effective_settlement_date"] == _iso(2026, 4, 20)


@pytest.mark.asyncio
async def test_daily_sweep_promotes_estimated_to_billing(mongo_db):
    """Between sweeps, a Salla status update set `billing_eligible_at`
    directly (without going through orders_db).  The sweep must
    pick up the upgrade and re-stamp the row."""
    uid = "u-sweep-2"
    await mongo_db.bnpl_settings.insert_one(
        {"user_id": uid, "provider": "tamara", "enabled": True},
    )
    await mongo_db.payment_transactions.insert_one({
        "id": "p-2", "user_id": uid, "provider": "tamara",
        "provider_id": "tam-2",
        "order_reference_id": "OLD-2", "order_number": "OLD-2",
        "amount": 120.0,
        "created_at_provider":      _iso(2026, 4, 20),
        "billing_eligible_at":      _iso(2026, 5,  6),
        "effective_settlement_date": _iso(2026, 4, 20),
        "settlement_source":         SETTLEMENT_SOURCE_ESTIMATED,
    })

    summary = await run_tamara_attribution_sweep(mongo_db)
    assert summary["rows_updated"] == 1

    doc = await mongo_db.payment_transactions.find_one({"id": "p-2"})
    assert doc["settlement_source"] == SETTLEMENT_SOURCE_BILLING
    assert doc["effective_settlement_date"] == _iso(2026, 5, 6)


@pytest.mark.asyncio
async def test_daily_sweep_skips_users_without_tamara(mongo_db):
    """Users who haven't enabled Tamara are ignored entirely."""
    uid = "u-no-tamara"
    # Only a Tabby setting, no Tamara.
    await mongo_db.bnpl_settings.insert_one(
        {"user_id": uid, "provider": "tabby", "enabled": True},
    )
    await mongo_db.payment_transactions.insert_one({
        "id": "tab-1", "user_id": uid, "provider": "tabby",
        "provider_id": "tabby-1",
        "amount": 50.0,
        "created_at_provider": _iso(2026, 5, 1),
    })
    summary = await run_tamara_attribution_sweep(mongo_db)
    assert summary["users_processed"] == 0
    assert summary["rows_scanned"] == 0


@pytest.mark.asyncio
async def test_daily_sweep_is_idempotent(mongo_db):
    """Running the sweep twice on unchanged data produces zero updates
    the second time."""
    uid = "u-idem"
    await mongo_db.bnpl_settings.insert_one(
        {"user_id": uid, "provider": "tamara", "enabled": True},
    )
    await mongo_db.payment_transactions.insert_one({
        "id": "i-1", "user_id": uid, "provider": "tamara",
        "provider_id": "tam-i-1",
        "amount": 200.0,
        "created_at_provider": _iso(2026, 5, 1),
    })
    s1 = await run_tamara_attribution_sweep(mongo_db)
    assert s1["rows_updated"] == 1
    s2 = await run_tamara_attribution_sweep(mongo_db)
    assert s2["rows_updated"] == 0
