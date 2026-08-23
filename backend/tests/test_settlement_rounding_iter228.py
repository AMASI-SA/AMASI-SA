"""Iter-228 — Settlement rounding accuracy fix.

Validates the statement-derived Tabby component rounding. Each captured order
rounds 4.99% refundable and 2.00% non-refundable commission separately, then
adds the SAR 1 fixed fee.
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
async def test_rounding_uses_per_order_split_components():
    """50 transactions of 33.33 SAR each.
    For 33.33 SAR: round(4.99%)=1.66, round(2%)=0.67, +1 fixed = 3.33.
    Across 50 captures the official-style commission is 166.50 SAR.
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
        per_order = round(33.33 * 0.0499, 2) + round(33.33 * 0.02, 2) + 1.0
        expected = round(50 * per_order, 2)  # = 166.50

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
    """Sale 1000 + refund 100 reverses only the rounded 4.99% leg."""
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
        # Capture = 49.90 + 20.00 + 1.00; refund rebate = 4.99.
        assert tots.get("commission") == 65.91, tots.get("commission")
    finally:
        await db.payment_transactions.delete_many({"user_id": uid})
        await db.payment_refunds.delete_many({"user_id": uid})
        cli.close()
