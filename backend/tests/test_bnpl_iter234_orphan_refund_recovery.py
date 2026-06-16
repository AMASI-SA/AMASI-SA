"""Iter-234 — Tamara same-week capture+refund recovery.

When an order is BOTH captured AND refunded inside the SAME Tamara
weekly Statement, Tamara counts it in BOTH the Captured column AND
the Refunds column.  Our engine previously skipped the Captured side
if the order's `effective_settlement_date` was outside the window
(because attribution had only `created_at_provider` available).

Iter-234 adds a recovery pass: for every refund whose original sale
is missing from this period's gross aggregation, add the original
amount back into gross_sales.

This test seeds a controlled scenario directly into MongoDB.
"""
from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from dotenv import load_dotenv

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(_BACKEND_DIR, ".env"))


@pytest.fixture(scope="module")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="module")
async def db():
    from motor.motor_asyncio import AsyncIOMotorClient
    cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
    yield cli[os.environ["DB_NAME"]]
    cli.close()


@pytest.mark.asyncio
async def test_orphan_refund_recovers_into_gross():
    """An order created BEFORE the window but captured + refunded INSIDE
    the window should still count in Captured (Tamara behaviour)."""
    from bnpl.settlements_service import _compute_provider_totals
    from motor.motor_asyncio import AsyncIOMotorClient

    cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = cli[os.environ["DB_NAME"]]
    uid = f"iter234-{uuid.uuid4().hex[:8]}"
    pid_in = f"prov-{uuid.uuid4().hex[:8]}"
    pid_orphan = f"prov-{uuid.uuid4().hex[:8]}"
    try:
        # Seed: 1 order properly attributed INSIDE the window.
        await db.payment_transactions.insert_one({
            "id": str(uuid.uuid4()), "user_id": uid, "provider": "tamara",
            "provider_id": pid_in,
            "amount": 100.0,
            "currency": "SAR",
            "created_at_provider": "2026-06-10T10:00:00+00:00",
            "effective_settlement_date": "2026-06-10T10:00:00+00:00",
            "settlement_source": "billing_eligible",
        })
        # Seed: 1 orphan order created BEFORE the window (so
        # effective_settlement_date falls outside), but refunded
        # inside the window.
        await db.payment_transactions.insert_one({
            "id": str(uuid.uuid4()), "user_id": uid, "provider": "tamara",
            "provider_id": pid_orphan,
            "amount": 133.73,
            "currency": "SAR",
            "created_at_provider": "2026-06-04T08:00:00+00:00",
            "effective_settlement_date": "2026-06-04T08:00:00+00:00",
            "settlement_source": "estimated",
        })
        # Refund of the orphan, INSIDE the window.
        await db.payment_refunds.insert_one({
            "id": str(uuid.uuid4()), "user_id": uid, "provider": "tamara",
            "provider_refund_id": f"rf-{uuid.uuid4().hex[:8]}",
            "provider_payment_id": pid_orphan,
            "order_reference_id": f"ref-{pid_orphan}",
            "amount": 133.73,
            "currency": "SAR",
            "refunded_at": "2026-06-09T12:00:00+00:00",
            "status": "refunded",
        })

        tot = await _compute_provider_totals(
            db, uid, "tamara",
            date_from="2026-06-06", date_to="2026-06-12",
        )

        # Without recovery: gross would be 100.  With Iter-234 recovery:
        # gross == 100 + 133.73 = 233.73.
        assert abs(tot["gross_sales"] - 233.73) < 0.01, tot
        assert abs(tot["total_refunds"] - 133.73) < 0.01, tot
        assert abs(tot["net_sales"] - 100.0) < 0.01, tot
        # And the transactions count should reflect the recovery.
        assert tot["transactions_count"] == 2, tot
    finally:
        await db.payment_transactions.delete_many({"user_id": uid})
        await db.payment_refunds.delete_many({"user_id": uid})
        cli.close()


@pytest.mark.asyncio
async def test_tabby_is_unaffected_by_recovery_logic():
    """The recovery branch is gated on provider == 'tamara' — Tabby
    must keep its previous behaviour (skip orphan recovery)."""
    from bnpl.settlements_service import _compute_provider_totals
    from motor.motor_asyncio import AsyncIOMotorClient

    cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = cli[os.environ["DB_NAME"]]
    uid = f"iter234t-{uuid.uuid4().hex[:8]}"
    pid_orphan = f"prov-{uuid.uuid4().hex[:8]}"
    try:
        # Orphan Tabby order (created_at_provider outside window).
        await db.payment_transactions.insert_one({
            "id": str(uuid.uuid4()), "user_id": uid, "provider": "tabby",
            "provider_id": pid_orphan,
            "amount": 50.0,
            "currency": "SAR",
            "created_at_provider": "2026-06-04T08:00:00+00:00",
        })
        # Refund inside the window.
        await db.payment_refunds.insert_one({
            "id": str(uuid.uuid4()), "user_id": uid, "provider": "tabby",
            "provider_refund_id": f"rf-{uuid.uuid4().hex[:8]}",
            "provider_payment_id": pid_orphan,
            "amount": 50.0,
            "refunded_at": "2026-06-09T12:00:00+00:00",
            "status": "refunded",
        })
        tot = await _compute_provider_totals(
            db, uid, "tabby",
            date_from="2026-06-06", date_to="2026-06-12",
        )
        # Tabby: gross stays at 0 (no recovery), refund = 50.
        assert abs(tot["gross_sales"] - 0.0) < 0.01, tot
        assert abs(tot["total_refunds"] - 50.0) < 0.01, tot
    finally:
        await db.payment_transactions.delete_many({"user_id": uid})
        await db.payment_refunds.delete_many({"user_id": uid})
        cli.close()
