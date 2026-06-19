"""Iter-246u — Sticky historical pin protection.

Ensures `recompute_attribution_for_doc` (the function the Tamara sync
calls on every refresh) does NOT overwrite a manual
`settlement_source="settlement_entries_historical"` pin set by
iter246q.

This is the root cause the merchant hit on 2026-06-19: 13 captures
that iter246q pinned to a past Tamara settlement file got their
`settlement_source` wiped by a subsequent sync, sneaking them back
into the current Gross.

Read-only assertion + one targeted update. No GL writes.
"""
from __future__ import annotations

import os
import uuid

import pytest
import pytest_asyncio
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(_BACKEND_DIR, ".env"))
load_dotenv(os.path.join(_BACKEND_DIR, "..", "frontend", ".env"))

MONGO_URL = os.environ["MONGO_URL"]
DB_NAME = os.environ["DB_NAME"]


@pytest_asyncio.fixture
async def db_cli():
    c = AsyncIOMotorClient(MONGO_URL)
    yield c[DB_NAME]
    c.close()


@pytest.mark.asyncio
async def test_historical_pin_survives_recompute(db_cli):
    """If `settlement_source == "settlement_entries_historical"`, then
    `recompute_attribution_for_doc` MUST keep the pin and the
    `effective_settlement_date` unchanged — even if the natural
    attribution would otherwise move it."""
    from bnpl.settlement_attribution import recompute_attribution_for_doc

    uid = f"pin-test-{uuid.uuid4().hex[:8]}"
    txn_id = f"txn-{uuid.uuid4().hex[:8]}"
    pinned_date = "2026-05-23"

    # Insert a capture that iter246q has just pinned to a past cycle.
    # Note: `created_at_provider` would naturally re-attribute it to
    # 2026-06-XX without the pin.
    await db_cli.payment_transactions.insert_one({
        "id": txn_id, "user_id": uid, "provider": "tamara",
        "provider_id": f"pid-{uuid.uuid4().hex[:8]}",
        "order_reference_id": "PINNED-1",
        "amount": 100.0, "captured_amount": 100.0,
        "currency": "SAR", "status": "fully_captured",
        "created_at_provider": "2026-06-05T08:00:00Z",
        "billing_eligible_at": "2026-06-12T20:00:00Z",
        "effective_settlement_date": pinned_date,
        "settlement_source": "settlement_entries_historical",
        "is_pre_accounting": False,
    })

    try:
        result = await recompute_attribution_for_doc(
            db_cli, user_id=uid, txn_id=txn_id,
        )
        assert result["updated"] == 0, (
            f"Pin was overwritten — result={result}"
        )
        assert result["reason"] == "historical_pin_preserved", (
            f"Wrong reason — got {result.get('reason')!r}"
        )
        assert result["settlement_source"] == (
            "settlement_entries_historical")

        # Verify the DB row is UNCHANGED.
        doc = await db_cli.payment_transactions.find_one(
            {"id": txn_id})
        assert doc["settlement_source"] == (
            "settlement_entries_historical")
        assert doc["effective_settlement_date"] == pinned_date
    finally:
        await db_cli.payment_transactions.delete_many({"user_id": uid})


@pytest.mark.asyncio
async def test_non_pinned_row_still_gets_recomputed(db_cli):
    """Regression check — the sticky guard MUST only affect pinned
    rows. Non-pinned rows continue to be re-attributed normally."""
    from bnpl.settlement_attribution import recompute_attribution_for_doc

    uid = f"pin-test-{uuid.uuid4().hex[:8]}"
    txn_id = f"txn-{uuid.uuid4().hex[:8]}"

    await db_cli.payment_transactions.insert_one({
        "id": txn_id, "user_id": uid, "provider": "tamara",
        "provider_id": f"pid-{uuid.uuid4().hex[:8]}",
        "order_reference_id": "NORMAL-1",
        "amount": 200.0, "captured_amount": 200.0,
        "currency": "SAR", "status": "fully_captured",
        "created_at_provider": "2026-06-09T08:00:00Z",
        "billing_eligible_at": "2026-06-17T08:00:00Z",
        # No pin, no effective_settlement_date yet.
        "settlement_source": "estimated",
        "is_pre_accounting": False,
    })

    try:
        result = await recompute_attribution_for_doc(
            db_cli, user_id=uid, txn_id=txn_id,
        )
        # Either updated or unchanged is fine — the key check is that
        # the result does NOT report "historical_pin_preserved".
        assert result.get("reason") != "historical_pin_preserved"
    finally:
        await db_cli.payment_transactions.delete_many({"user_id": uid})
