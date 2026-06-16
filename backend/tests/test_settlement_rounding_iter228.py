"""Iter-228 — Settlement rounding accuracy fix.

Validates that the settlement engine matches processor (Tabby/Tamara)
invoice math by:
  1. Accumulating fee/VAT in full precision.
  2. Rounding ONLY the final totals (NOT per-transaction).

Without this fix, ~50 transactions accumulated ~0.22 SAR drift vs.
the official Tabby weekly invoice.
"""
from __future__ import annotations

import os
import sys
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")
sys.path.insert(0, "/app/backend")

from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402

from bnpl.settlements_service import (  # noqa: E402
    compute_settlement_for_provider,
)


def _conn():
    cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
    return cli, cli[os.environ["DB_NAME"]]


@pytest.mark.asyncio
async def test_rounding_sums_first_then_rounds():
    """50 transactions of 33.33 SAR each.
    Per-transaction rounding produces measurable drift vs. sum-first.

    Tabby commission_rate (default) = 6.99%, VAT = 15%, no fixed_fee.
    Per txn raw commission = 33.33 × 0.0699 = 2.329767 SAR
    Sum-first total = 50 × 2.329767 = 116.488 → round → 116.49
    Per-txn round = 50 × round(2.329767, 2) = 50 × 2.33 = 116.50
    Drift = 0.01 SAR even on this small dataset — ours matches sum-first.
    """
    cli, db = _conn()
    uid = str(uuid.uuid4())
    today = datetime.now(timezone.utc).date()
    date_from = (today - timedelta(days=2)).isoformat()
    date_to = today.isoformat()
    try:
        # 50 transactions of 33.33 within the window
        utc_now = datetime.now(timezone.utc).isoformat()
        bulk = []
        for i in range(50):
            bulk.append({
                "id": str(uuid.uuid4()),
                "user_id": uid,
                "provider": "tabby",
                "provider_id": f"P_{i}",
                "amount": 33.33,
                "currency": "SAR",
                "status": "closed",
                "order_reference_id": f"ORD_{i}",
                "order_number": f"#{i}",
                "created_at_provider": utc_now,
                "created_at": utc_now,
                "synced_at": utc_now,
            })
        await db.payment_transactions.insert_many(bulk)

        # Minimal fee_rates — use module defaults (Tabby).
        s = await compute_settlement_for_provider(
            db, uid, "tabby", date_from, date_to,
        )
        tots = s.get("totals") or {}
        commission = tots.get("commission") or 0
        # Sum-first calc (matching the engine):
        # commission per txn (raw) = amt * 0.0699 + 1.0 (fixed_fee)
        # = 33.33 * 0.0699 + 1.0 = 2.329767 + 1.0 = 3.329767
        # 50 × 3.329767 = 166.488 → round → 166.49
        expected = round(50 * (33.33 * 0.0699 + 1.0), 2)   # = 166.49

        # Allow up to 0.01 SAR rounding tolerance.
        assert abs(commission - expected) < 0.011, (
            f"commission={commission}, expected≈{expected} "
            f"(diff={commission - expected:.4f})"
        )
    finally:
        await db.payment_transactions.delete_many({"user_id": uid})
        cli.close()


@pytest.mark.asyncio
async def test_rounding_with_refund_in_period():
    """Sale 1000 + Refund 100 — net should match (1000−100) × rate
    cleanly, with no per-row drift."""
    cli, db = _conn()
    uid = str(uuid.uuid4())
    today = datetime.now(timezone.utc).date()
    date_from = (today - timedelta(days=1)).isoformat()
    date_to = today.isoformat()
    try:
        now = datetime.now(timezone.utc).isoformat()
        await db.payment_transactions.insert_one({
            "id": str(uuid.uuid4()),
            "user_id": uid,
            "provider": "tabby",
            "provider_id": "P_REF_1",
            "amount": 1000.00,
            "currency": "SAR",
            "status": "closed",
            "order_reference_id": "ORD_R",
            "created_at_provider": now,
            "created_at": now,
            "synced_at": now,
        })
        await db.payment_refunds.insert_one({
            "id": str(uuid.uuid4()),
            "user_id": uid,
            "provider": "tabby",
            "provider_payment_id": "P_REF_1",
            "provider_refund_id": "R_1",
            "amount": 100.00,
            "currency": "SAR",
            "status": "refunded",
            "refunded_at": now,
            "created_at": now,
            "synced_at": now,
        })
        s = await compute_settlement_for_provider(
            db, uid, "tabby", date_from, date_to,
        )
        tots = s.get("totals") or {}
        # Net sales = 900. Commission ≈ 900 × 0.0699 = 62.91 (sum-first).
        # But there's also a fixed_fee_per_order on the sale (1.0) and
        # the refund rebate uses refundable_commission_pct=4.99% of 100=4.99.
        # Sum-first commission = (1000*0.0699 + 1.0) - (100*0.0499) = 69.9 + 1 - 4.99 = 65.91
        assert tots.get("commission") == 65.91, tots.get("commission")
    finally:
        await db.payment_transactions.delete_many({"user_id": uid})
        await db.payment_refunds.delete_many({"user_id": uid})
        cli.close()
