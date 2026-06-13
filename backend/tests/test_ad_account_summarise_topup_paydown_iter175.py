"""Iter-175 — Tests for the topup-paydown fix in _summarise.

Reproduces the production bug where balance and debt would inflate on
every half-hour cron sync for Snap/Meta ad-accounts. Root cause: the
ledger walk used to add topups straight to balance without paying off
existing debt first, even though the /topup endpoint allocates part of
the cash to debt and part to balance. This caused debt to accumulate
without ever being recognised as paid by the walk.
"""
from __future__ import annotations

import asyncio
import uuid
import os
import sys
from datetime import datetime, timezone, timedelta

import pytest
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")
sys.path.insert(0, "/app/backend")
from ad_account_routes import _summarise  # noqa: E402


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


@pytest.fixture
def db():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    return client[os.environ["DB_NAME"]]


def _new_uid() -> str:
    return f"test-iter175-{uuid.uuid4()}"


async def _cleanup(database, uid):
    await database.counterparties.delete_many({"user_id": uid})
    await database.ad_account_ledger.delete_many({"user_id": uid})
    await database.liabilities.delete_many({"user_id": uid})
    await database.general_ledger.delete_many({"user_id": uid})


@pytest.mark.asyncio
async def test_topup_pays_down_debt_in_walk(db):
    """Topup should pay off existing debt first in the walk,
    mirroring the /topup endpoint logic.
    """
    database = db
    uid = _new_uid()
    cp_id = str(uuid.uuid4())
    cp = {
        "id": cp_id, "user_id": uid, "kind": "ad_account",
        "ad_provider": "snapchat", "name": "Snap Test", "balance": 0.0,
        "debt_mode": "auto",
    }
    await database.counterparties.insert_one(cp)

    base = datetime(2026, 2, 1, 8, 0, 0, tzinfo=timezone.utc)
    # T0: opening 50
    await database.ad_account_ledger.insert_one({
        "id": str(uuid.uuid4()), "user_id": uid, "counterparty_id": cp_id,
        "type": "opening", "amount": 50.0, "balance_after": 50.0,
        "debt_after": 0.0, "date": "2026-02-01",
        "created_at": _iso(base),
    })
    # T1: spend 100 (creates debt of 50)
    await database.ad_account_ledger.insert_one({
        "id": str(uuid.uuid4()), "user_id": uid, "counterparty_id": cp_id,
        "type": "spend", "amount": 100.0, "balance_after": 0.0,
        "debt_after": 50.0, "date": "2026-02-01",
        "created_at": _iso(base + timedelta(hours=1)),
        "breakdown": {"auto_cron": True, "from_balance": 50.0,
                      "uncovered": 50.0, "mode": "auto"},
    })
    # T2: topup 100 (should pay 50 debt + 50 to balance)
    await database.ad_account_ledger.insert_one({
        "id": str(uuid.uuid4()), "user_id": uid, "counterparty_id": cp_id,
        "type": "topup", "amount": 100.0, "balance_after": 50.0,
        "debt_after": 0.0, "date": "2026-02-01",
        "created_at": _iso(base + timedelta(hours=2)),
        "breakdown": {"to_debt": 50.0, "to_balance": 50.0},
    })

    summary = await _summarise(database, uid, cp)
    assert summary["balance"] == 50.0, f"balance={summary['balance']}"
    assert summary["open_debt"] == 0.0, f"debt={summary['open_debt']}"
    await _cleanup(database, uid)


@pytest.mark.asyncio
async def test_cumulative_spend_then_topup_no_inflation(db):
    """The production scenario: cumulative spend row mutated higher
    after sync, with topup happening before walk processes the larger
    cumulative. Walk must NOT inflate balance or debt.
    """
    database = db
    uid = _new_uid()
    cp_id = str(uuid.uuid4())
    cp = {
        "id": cp_id, "user_id": uid, "kind": "ad_account",
        "ad_provider": "meta", "name": "Meta Test", "balance": 0.0,
        "debt_mode": "auto",
    }
    await database.counterparties.insert_one(cp)

    base = datetime(2026, 2, 1, 8, 0, 0, tzinfo=timezone.utc)
    await database.ad_account_ledger.insert_one({
        "id": str(uuid.uuid4()), "user_id": uid, "counterparty_id": cp_id,
        "type": "opening", "amount": 50.0, "balance_after": 50.0,
        "debt_after": 0.0, "date": "2026-02-01",
        "created_at": _iso(base),
    })
    # Cumulative spend row that grew from 100 -> 200 over two syncs
    await database.ad_account_ledger.insert_one({
        "id": str(uuid.uuid4()), "user_id": uid, "counterparty_id": cp_id,
        "type": "spend", "amount": 200.0,  # cumulative
        "balance_after": 0.0, "debt_after": 150.0,
        "date": "2026-02-01",
        "created_at": _iso(base + timedelta(hours=1)),
        "breakdown": {"auto_cron": True, "from_balance": 50.0,
                      "uncovered": 150.0, "mode": "auto",
                      "platform_total": 200.0},
    })
    # Topup of 100 happened between sync 1 (amount=100) and sync 2 (amount=200).
    # Reality: paid 50 debt + 50 to balance.
    # After sync 2 (extends debt by another 100 delta): debt = 50, balance = 0.
    await database.ad_account_ledger.insert_one({
        "id": str(uuid.uuid4()), "user_id": uid, "counterparty_id": cp_id,
        "type": "topup", "amount": 100.0, "balance_after": 50.0,
        "debt_after": 0.0, "date": "2026-02-01",
        "created_at": _iso(base + timedelta(hours=2)),
        "breakdown": {"to_debt": 50.0, "to_balance": 50.0},
    })

    summary = await _summarise(database, uid, cp)
    # Walk processes in created_at order:
    #   opening +50 → balance=50
    #   spend +200 → covered=50, debt=150, balance=0
    #   topup +100 → pays 100 of 150 debt → debt=50, balance=0
    assert summary["balance"] == 0.0, f"balance inflated: {summary['balance']}"
    assert summary["open_debt"] == 50.0, f"debt inflated: {summary['open_debt']}"
    await _cleanup(database, uid)


@pytest.mark.asyncio
async def test_multiple_sync_cycles_no_unbounded_growth(db):
    """Simulates 5 half-hour cron passes with one topup per day,
    confirms balance and debt do NOT keep growing across iterations.
    """
    database = db
    uid = _new_uid()
    cp_id = str(uuid.uuid4())
    cp = {
        "id": cp_id, "user_id": uid, "kind": "ad_account",
        "ad_provider": "snapchat", "name": "Snap Test", "balance": 0.0,
        "debt_mode": "auto",
    }
    await database.counterparties.insert_one(cp)

    base = datetime(2026, 2, 1, 8, 0, 0, tzinfo=timezone.utc)
    # Day 1 — opening 100
    await database.ad_account_ledger.insert_one({
        "id": str(uuid.uuid4()), "user_id": uid, "counterparty_id": cp_id,
        "type": "opening", "amount": 100.0, "balance_after": 100.0,
        "debt_after": 0.0, "date": "2026-02-01",
        "created_at": _iso(base),
    })
    # Day 1 — cumulative spend grew to 300 over multiple syncs
    await database.ad_account_ledger.insert_one({
        "id": str(uuid.uuid4()), "user_id": uid, "counterparty_id": cp_id,
        "type": "spend", "amount": 300.0,
        "balance_after": 0.0, "debt_after": 200.0, "date": "2026-02-01",
        "created_at": _iso(base + timedelta(hours=1)),
        "breakdown": {"auto_cron": True, "from_balance": 100.0,
                      "uncovered": 200.0, "mode": "auto"},
    })
    # Day 1 — topup 200 (pays 200 debt, 0 to balance)
    await database.ad_account_ledger.insert_one({
        "id": str(uuid.uuid4()), "user_id": uid, "counterparty_id": cp_id,
        "type": "topup", "amount": 200.0, "balance_after": 0.0,
        "debt_after": 0.0, "date": "2026-02-01",
        "created_at": _iso(base + timedelta(hours=2)),
        "breakdown": {"to_debt": 200.0, "to_balance": 0.0},
    })

    summary = await _summarise(database, uid, cp)
    # opening +100, spend +300 (cov 100, debt 200), topup +200 (pays 200 debt)
    # End: balance=0, debt=0
    assert summary["balance"] == 0.0, summary
    assert summary["open_debt"] == 0.0, summary

    # Now Day 2 — spend +100 (no topup yet)
    day2 = base + timedelta(days=1)
    await database.ad_account_ledger.insert_one({
        "id": str(uuid.uuid4()), "user_id": uid, "counterparty_id": cp_id,
        "type": "spend", "amount": 100.0,
        "balance_after": 0.0, "debt_after": 100.0, "date": "2026-02-02",
        "created_at": _iso(day2),
        "breakdown": {"auto_cron": True, "from_balance": 0.0,
                      "uncovered": 100.0, "mode": "auto"},
    })

    summary = await _summarise(database, uid, cp)
    assert summary["balance"] == 0.0, summary
    assert summary["open_debt"] == 100.0, summary
    await _cleanup(database, uid)


@pytest.mark.asyncio
async def test_topup_without_debt_goes_to_balance(db):
    """Topup with no existing debt should add fully to balance."""
    database = db
    uid = _new_uid()
    cp_id = str(uuid.uuid4())
    cp = {
        "id": cp_id, "user_id": uid, "kind": "ad_account",
        "ad_provider": "tiktok", "name": "TikTok Test", "balance": 0.0,
        "debt_mode": "auto",
    }
    await database.counterparties.insert_one(cp)

    base = datetime(2026, 2, 1, 8, 0, 0, tzinfo=timezone.utc)
    await database.ad_account_ledger.insert_one({
        "id": str(uuid.uuid4()), "user_id": uid, "counterparty_id": cp_id,
        "type": "topup", "amount": 500.0, "balance_after": 500.0,
        "debt_after": 0.0, "date": "2026-02-01",
        "created_at": _iso(base),
        "breakdown": {"to_debt": 0.0, "to_balance": 500.0},
    })

    summary = await _summarise(database, uid, cp)
    assert summary["balance"] == 500.0
    assert summary["open_debt"] == 0.0
    await _cleanup(database, uid)
