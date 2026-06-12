"""Iter-149 — Per-provider accounting cutoff tests."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient

from backend.accounting_cutoffs import (
    DEFAULT_CUTOFFS,
    SUPPORTED_PROVIDERS,
    get_all_cutoffs,
    get_cutoff,
    set_cutoff,
)
from backend.bnpl.settlements_service import (
    _aggregate_official_totals,
    _compute_provider_totals,
)


@pytest_asyncio.fixture
async def mongo_db():
    import os
    mongo_url = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
    client = AsyncIOMotorClient(mongo_url)
    name = f"iter149_test_{uuid.uuid4().hex[:8]}"
    db = client[name]
    try:
        yield db
    finally:
        await client.drop_database(name)
        client.close()


def _iso(year, month, day, hour=12):
    return datetime(year, month, day, hour, 0, 0, tzinfo=timezone.utc).isoformat()


# ── Helper tests ──────────────────────────────────────────────────

def test_defaults_match_merchant_spec():
    assert DEFAULT_CUTOFFS["tabby"]         == "2026-04-27"
    assert DEFAULT_CUTOFFS["tamara"]        == "2026-04-25"
    assert DEFAULT_CUTOFFS["salla"]         == "2026-04-30"
    assert DEFAULT_CUTOFFS["cod"]           == "2026-04-30"
    assert DEFAULT_CUTOFFS["bank_transfer"] == "2026-04-30"
    assert SUPPORTED_PROVIDERS == frozenset({
        "tabby", "tamara", "salla", "cod", "bank_transfer",
    })


@pytest.mark.asyncio
async def test_get_cutoff_returns_default_for_new_user(mongo_db):
    assert await get_cutoff(mongo_db, "u1", "tabby")  == "2026-04-27"
    assert await get_cutoff(mongo_db, "u1", "tamara") == "2026-04-25"


@pytest.mark.asyncio
async def test_get_all_cutoffs_seeds_db(mongo_db):
    out = await get_all_cutoffs(mongo_db, "u1")
    # All five providers seeded.
    assert set(out.keys()) == {"tabby", "tamara", "salla", "cod", "bank_transfer"}
    # Persisted.
    n = await mongo_db.accounting_cutoffs.count_documents({"user_id": "u1"})
    assert n == 5
    # Second call returns same data without dup-inserting.
    out2 = await get_all_cutoffs(mongo_db, "u1")
    assert out == out2
    n2 = await mongo_db.accounting_cutoffs.count_documents({"user_id": "u1"})
    assert n2 == 5


@pytest.mark.asyncio
async def test_set_cutoff_idempotent_upsert(mongo_db):
    r1 = await set_cutoff(mongo_db, "u1", "tabby", "2026-05-01")
    assert r1["new"] == "2026-05-01"
    assert r1["changed"] is True
    # Reading reflects the change.
    assert await get_cutoff(mongo_db, "u1", "tabby") == "2026-05-01"
    # Same value again → no change.
    r2 = await set_cutoff(mongo_db, "u1", "tabby", "2026-05-01")
    assert r2["changed"] is False


@pytest.mark.asyncio
async def test_set_cutoff_validates_date_shape(mongo_db):
    with pytest.raises(ValueError):
        await set_cutoff(mongo_db, "u1", "tabby", "2026/05/01")


@pytest.mark.asyncio
async def test_set_cutoff_rejects_unknown_provider(mongo_db):
    with pytest.raises(ValueError):
        await set_cutoff(mongo_db, "u1", "paypal", "2026-05-01")


# ── Integration: cutoff filters BNPL settlements ─────────────────

@pytest.mark.asyncio
async def test_tabby_cutoff_excludes_old_transactions(mongo_db):
    """A Tabby txn dated BEFORE the cutoff must not appear in the
    settlements query."""
    uid = "u1"
    # Custom cutoff = 2026-05-01.
    await set_cutoff(mongo_db, uid, "tabby", "2026-05-01")

    # Two rows — one before, one after.
    await mongo_db.payment_transactions.insert_many([
        {
            "id": "old", "user_id": uid, "provider": "tabby",
            "provider_id": "tb-old",
            "amount": 100.0,
            "created_at_provider": _iso(2026, 4, 28),
        },
        {
            "id": "new", "user_id": uid, "provider": "tabby",
            "provider_id": "tb-new",
            "amount": 200.0,
            "created_at_provider": _iso(2026, 5, 5),
        },
    ])
    totals = await _compute_provider_totals(
        mongo_db, uid, "tabby",
        date_from="2026-04-25", date_to="2026-05-08",
    )
    # Only the post-cutoff row counts.
    assert totals["transactions_count"] == 1
    assert totals["gross_sales"] == 200.0


@pytest.mark.asyncio
async def test_tamara_cutoff_excludes_official_entries(mongo_db):
    """A Tamara settlement_entry dated BEFORE the cutoff is excluded
    from the official-file aggregator too."""
    uid = "u1"
    await set_cutoff(mongo_db, uid, "tamara", "2026-04-26")

    await mongo_db.settlement_entries.insert_many([
        {
            "id": "se-old", "user_id": uid, "provider": "tamara",
            "order_number": "100", "event_type": "sale",
            "actual_gross_amount": 999.0,
            "actual_net_amount": 900.0,
            "settlement_date": "2026-04-25",  # excluded
            "created_at": _iso(2026, 6, 12),
        },
        {
            "id": "se-new", "user_id": uid, "provider": "tamara",
            "order_number": "101", "event_type": "sale",
            "actual_gross_amount": 500.0,
            "actual_net_amount": 460.0,
            "settlement_date": "2026-04-28",  # included
            "created_at": _iso(2026, 6, 12),
        },
    ])
    out = await _aggregate_official_totals(
        mongo_db, uid, "2026-04-20", "2026-05-01",
    )
    assert out is not None
    assert out["transactions_count"] == 1
    assert out["gross_sales"] == 500.0


@pytest.mark.asyncio
async def test_is_pre_accounting_flag_skipped(mongo_db):
    """Rows already flagged `is_pre_accounting=true` are excluded
    regardless of date — supports manual archival workflows."""
    uid = "u1"
    await mongo_db.payment_transactions.insert_one({
        "id": "tb-flagged", "user_id": uid, "provider": "tabby",
        "provider_id": "tb-x", "amount": 333.0,
        "created_at_provider": _iso(2026, 5, 10),
        "is_pre_accounting": True,
    })
    totals = await _compute_provider_totals(
        mongo_db, uid, "tabby",
        date_from="2026-05-01", date_to="2026-05-31",
    )
    assert totals["transactions_count"] == 0


# ── Bank-balance cutoff adjustment ────────────────────────────────

@pytest.mark.asyncio
async def test_recompute_endpoint_flags_pre_cutoff_liabilities(mongo_db):
    """A liability whose `created_at` falls BEFORE the Tabby cutoff
    must be marked `is_pre_accounting=true` so the financial-position
    screen excludes it from the unpaid totals."""
    from backend.accounting_cutoffs_routes import _recompute_one
    uid = "u1"
    await set_cutoff(mongo_db, uid, "tabby", "2026-05-01")
    # Pre-cutoff liability.
    await mongo_db.liabilities.insert_one({
        "id": "L-old", "user_id": uid, "kind": "ad_account",
        "ad_provider": "snapchat",
        "expected_amount": 500.0, "paid_amount": 0.0,
        "status": "unpaid",
        "created_at": _iso(2026, 4, 15),
        "due_date":   "2026-04-20",
    })
    # Post-cutoff liability.
    await mongo_db.liabilities.insert_one({
        "id": "L-new", "user_id": uid, "kind": "ad_account",
        "ad_provider": "snapchat",
        "expected_amount": 200.0, "paid_amount": 0.0,
        "status": "unpaid",
        "created_at": _iso(2026, 5, 5),
        "due_date":   "2026-05-10",
    })
    res = await _recompute_one(mongo_db, uid, "tabby")
    assert res["liabilities"] == 1
    old = await mongo_db.liabilities.find_one({"id": "L-old"})
    new = await mongo_db.liabilities.find_one({"id": "L-new"})
    assert old["is_pre_accounting"] is True
    assert new.get("is_pre_accounting") is not True
