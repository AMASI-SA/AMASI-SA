"""Iter-166 — Bank balance migration fidelity test.

Critical production bug: `_legacy_bank_balances` was reading the WRONG
field (`accounts.balance`) which was always None for accounts created
via the default-banks bootstrap. The real SSOT is `accounts.current_balance`
(computed from transaction history + opening_balance by
`_recompute_balance`).

Symptom: Reconciliation Report showed bank legacy=0 while the actual
Accounts page showed real balances (200K+ SAR per bank). Migration
would have posted opening_balance=0 to the Ledger, effectively zeroing
the merchant's bank balances inside the Universal Ledger.

Fix: Read `current_balance` first; fall back to `balance` if missing.
"""
import os
import sys
import uuid

import pytest
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")
sys.path.insert(0, "/app/backend")
from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402
from migration_routes import _legacy_bank_balances  # noqa: E402


@pytest.mark.asyncio
async def test_bank_balance_reads_current_balance_field():
    """Account with current_balance set must show that value, not 0."""
    c = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = c[os.environ["DB_NAME"]]
    uid = f"u-{uuid.uuid4().hex[:8]}"

    try:
        await db.accounts.insert_one({
            "id": "acc-1", "user_id": uid, "account_type": "bank",
            "name": "بنك الإنماء",
            "balance": None,
            "current_balance": 212363.30,
            "opening_balance": 0.0,
            "expected_orders_balance": 5940.59,
            "currency": "SAR",
        })
        rows = await _legacy_bank_balances(db, uid)
        assert len(rows) == 1
        r = rows[0]
        assert r["balance"] == 212363.30, (
            f"Expected 212363.30 from current_balance, got {r['balance']}")
        # Diagnostic fields surfaced for the UI breakdown
        assert r["_opening_balance"] == 0.0
        assert r["_expected_orders_balance"] == 5940.59
        assert r["_currency"] == "SAR"
    finally:
        await db.accounts.delete_many({"user_id": uid})


@pytest.mark.asyncio
async def test_bank_balance_falls_back_to_legacy_balance_field():
    """Older accounts that only have `balance` set (no current_balance)
    must still produce the right figure."""
    c = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = c[os.environ["DB_NAME"]]
    uid = f"u-{uuid.uuid4().hex[:8]}"

    try:
        await db.accounts.insert_one({
            "id": "acc-2", "user_id": uid, "account_type": "bank",
            "name": "بنك قديم",
            "balance": 12345.67,
            # current_balance is missing entirely
        })
        rows = await _legacy_bank_balances(db, uid)
        assert rows[0]["balance"] == 12345.67
    finally:
        await db.accounts.delete_many({"user_id": uid})


@pytest.mark.asyncio
async def test_bank_balance_prefers_current_over_legacy_balance():
    """When both are set, current_balance wins (it's the SSOT)."""
    c = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = c[os.environ["DB_NAME"]]
    uid = f"u-{uuid.uuid4().hex[:8]}"

    try:
        await db.accounts.insert_one({
            "id": "acc-3", "user_id": uid, "account_type": "bank",
            "name": "بنك مزدوج",
            "balance": 999.99,
            "current_balance": 50000.00,
        })
        rows = await _legacy_bank_balances(db, uid)
        assert rows[0]["balance"] == 50000.00
    finally:
        await db.accounts.delete_many({"user_id": uid})
