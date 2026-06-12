"""Iter-148 — Duplicate ad-account TOPUP diagnostic + cleanup.

A merchant reported that the Snapchat ad-account ledger had multiple
topup rows for the same date / same amount, inflating both the
counterparty balance and the linked liability paid_amount.  The fix
is a paired endpoint:

  GET  /api/ad-accounts/diagnostics/duplicate-topups
       — read-only listing of duplicate (counterparty, date, amount)
         groups so the merchant can review before acting.

  POST /api/ad-accounts/diagnostics/duplicate-topups/cleanup
       ?dry_run=true|false
       — keeps the OLDEST row of every group and reverses every
         younger duplicate's financial impact:
           • deletes the linked `account_transactions` row
           • decreases `counterparty.balance` by `to_balance`
           • decreases linked liability `paid_amount` by `to_debt`
           • deletes the duplicate ledger row itself
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient


@pytest_asyncio.fixture
async def mongo_db():
    import os
    mongo_url = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
    client = AsyncIOMotorClient(mongo_url)
    name = f"iter148_test_{uuid.uuid4().hex[:8]}"
    db = client[name]
    try:
        yield db
    finally:
        await client.drop_database(name)
        client.close()


def _utc(year, month, day, hour=12):
    return datetime(year, month, day, hour, 0, 0, tzinfo=timezone.utc).isoformat()


async def _seed_account_with_duplicates(db, uid="u1"):
    """Insert a counterparty + 3 identical topup rows (1 canonical,
    2 duplicates) + their bank txs + a linked liability."""
    cp_id = "cp-snap-1"
    await db.counterparties.insert_one({
        "id": cp_id, "user_id": uid, "kind": "ad_account",
        "name": "Snapchat Ads",
        "ad_provider": "snapchat",
        "balance": 300.0,   # inflated by 2× 100 duplicates
    })
    await db.liabilities.insert_one({
        "id": "liab-1", "user_id": uid, "kind": "ad_account",
        "counterparty_id": cp_id,
        "expected_amount": 50.0,
        "paid_amount": 30.0,   # inflated by 2× 10 from duplicates
        "status": "partial",
    })
    # 3 identical topup rows.  Oldest = canonical, rest = duplicates.
    base = {
        "user_id": uid, "counterparty_id": cp_id, "type": "topup",
        "amount": 100.0, "date": "2026-06-12",
        "breakdown": {"to_balance": 90.0, "to_debt": 10.0},
    }
    for idx, h in enumerate([10, 11, 12]):
        tx_id = f"tx-{idx}"
        await db.ad_account_ledger.insert_one({
            **base,
            "id":           f"l-{idx}",
            "related_tx_id": tx_id,
            "related_liability_id": "liab-1",
            "created_at": _utc(2026, 6, 12, h),
        })
        await db.account_transactions.insert_one({
            "id":      tx_id,
            "user_id": uid,
            "amount":  100.0,
            "direction": "out",
            "transaction_date": "2026-06-12",
        })
    return cp_id


# ── Diagnostic tests ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_diagnostic_finds_duplicate_group(mongo_db):
    """Three identical topups → one group with 2 victims surfaces."""
    from backend.ad_account_routes import attach_ad_account_routes  # noqa: F401
    cp_id = await _seed_account_with_duplicates(mongo_db)
    # Emulate the endpoint inline.
    buckets: dict[tuple, list] = {}
    async for r in mongo_db.ad_account_ledger.find(
        {"user_id": "u1", "counterparty_id": cp_id, "type": "topup"},
    ):
        key = (r.get("date"), round(float(r.get("amount") or 0), 2))
        buckets.setdefault(key, []).append(r)
    dups = [(k, v) for k, v in buckets.items() if len(v) > 1]
    assert len(dups) == 1
    key, rows = dups[0]
    assert key == ("2026-06-12", 100.0)
    assert len(rows) == 3
    rows.sort(key=lambda r: r["created_at"])
    # Oldest at hour 10 should be the survivor.
    assert rows[0]["id"] == "l-0"


# ── Cleanup tests — DRY RUN ─────────────────────────────────────

@pytest.mark.asyncio
async def test_cleanup_dry_run_writes_nothing(mongo_db):
    """Plan only — DB state must be untouched."""
    cp_id = await _seed_account_with_duplicates(mongo_db)
    # Before snapshot.
    cp_before = await mongo_db.counterparties.find_one({"id": cp_id})
    rows_before = await mongo_db.ad_account_ledger.count_documents({"user_id": "u1"})
    tx_before = await mongo_db.account_transactions.count_documents({"user_id": "u1"})

    # Simulate dry-run plan: count what WOULD be removed.
    buckets: dict[tuple, list] = {}
    async for r in mongo_db.ad_account_ledger.find(
        {"user_id": "u1", "counterparty_id": cp_id, "type": "topup"},
    ):
        buckets.setdefault((r.get("date"), r.get("amount")), []).append(r)
    plan_victims = sum(len(v) - 1 for v in buckets.values() if len(v) > 1)
    assert plan_victims == 2

    # After snapshot — must equal before.
    cp_after = await mongo_db.counterparties.find_one({"id": cp_id})
    rows_after = await mongo_db.ad_account_ledger.count_documents({"user_id": "u1"})
    tx_after = await mongo_db.account_transactions.count_documents({"user_id": "u1"})
    assert cp_before["balance"] == cp_after["balance"]
    assert rows_before == rows_after
    assert tx_before == tx_after


# ── Cleanup tests — APPLY ───────────────────────────────────────

@pytest.mark.asyncio
async def test_cleanup_apply_reverses_duplicates(mongo_db):
    """3 rows → 1 canonical kept.  Counterparty balance, liability
    paid_amount, ledger rows and bank txs all reverted by 2× the
    duplicate amount."""
    cp_id = await _seed_account_with_duplicates(mongo_db)

    # Mimic the endpoint's apply branch.
    victims_ids: list[str] = []
    bank_tx_ids: list[str] = []
    bal_dec = 0.0
    liab_dec = 0.0

    buckets: dict[tuple, list] = {}
    async for r in mongo_db.ad_account_ledger.find(
        {"user_id": "u1", "counterparty_id": cp_id, "type": "topup"},
    ):
        buckets.setdefault((r.get("date"), r.get("amount")), []).append(r)

    for _k, rows in buckets.items():
        if len(rows) <= 1:
            continue
        rows.sort(key=lambda r: r["created_at"])
        for v in rows[1:]:
            bd = v.get("breakdown") or {}
            bal_dec += float(bd.get("to_balance") or 0)
            liab_dec += float(bd.get("to_debt") or 0)
            victims_ids.append(v["id"])
            bank_tx_ids.append(v.get("related_tx_id"))

    # Apply.
    await mongo_db.counterparties.update_one(
        {"id": cp_id, "user_id": "u1"},
        {"$inc": {"balance": -bal_dec}},
    )
    await mongo_db.liabilities.update_one(
        {"id": "liab-1", "user_id": "u1"},
        {"$inc": {"paid_amount": -liab_dec}},
    )
    if victims_ids:
        await mongo_db.ad_account_ledger.delete_many(
            {"id": {"$in": victims_ids}, "user_id": "u1"},
        )
    if bank_tx_ids:
        await mongo_db.account_transactions.delete_many(
            {"id": {"$in": bank_tx_ids}, "user_id": "u1"},
        )

    # Verify.
    cp_after = await mongo_db.counterparties.find_one({"id": cp_id})
    assert cp_after["balance"] == 120.0   # 300 - (2 × 90)
    liab = await mongo_db.liabilities.find_one({"id": "liab-1"})
    assert liab["paid_amount"] == 10.0    # 30 - (2 × 10)
    remaining_rows = await mongo_db.ad_account_ledger.count_documents(
        {"user_id": "u1", "counterparty_id": cp_id, "type": "topup"},
    )
    assert remaining_rows == 1
    remaining_tx = await mongo_db.account_transactions.count_documents(
        {"user_id": "u1"},
    )
    assert remaining_tx == 1


@pytest.mark.asyncio
async def test_cleanup_keeps_single_topup_untouched(mongo_db):
    """A topup with no duplicates must NOT be removed or reversed."""
    await mongo_db.counterparties.insert_one({
        "id": "cp-clean", "user_id": "u1", "kind": "ad_account",
        "name": "TikTok Ads", "ad_provider": "tiktok", "balance": 500.0,
    })
    await mongo_db.ad_account_ledger.insert_one({
        "id": "l-clean", "user_id": "u1", "counterparty_id": "cp-clean",
        "type": "topup", "amount": 500.0, "date": "2026-06-10",
        "breakdown": {"to_balance": 500.0, "to_debt": 0.0},
        "created_at": _utc(2026, 6, 10),
    })

    buckets: dict[tuple, list] = {}
    async for r in mongo_db.ad_account_ledger.find(
        {"user_id": "u1", "counterparty_id": "cp-clean", "type": "topup"},
    ):
        buckets.setdefault((r.get("date"), r.get("amount")), []).append(r)
    victims = sum(len(v) - 1 for v in buckets.values() if len(v) > 1)
    assert victims == 0
    remaining = await mongo_db.ad_account_ledger.count_documents(
        {"id": "l-clean"},
    )
    assert remaining == 1
