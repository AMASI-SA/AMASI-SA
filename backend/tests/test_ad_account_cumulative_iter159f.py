"""Iter-159f — Half-hour ad-account sync must produce ONE cumulative
ledger row per (counterparty, day), not a new row each pass.

We simulate:
  • 09:00 sync — platform total 100 → ledger row created (amount=100)
  • 09:30 sync — platform total 150 → SAME row updated to amount=150
  • 10:00 sync — platform total 150 (no change) → no-op
  • Plant 2 pre-existing duplicate rows for the same day; verify the
    next sync collapses them into one.
"""
import asyncio
import os
import uuid
from datetime import datetime, timezone

import pytest
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")

# Import the sync helper directly.
import sys
sys.path.insert(0, "/app/backend")
from ad_account_routes import _run_sync_for_all  # noqa: E402


@pytest.fixture
def db():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    return client[os.environ["DB_NAME"]]


@pytest.mark.asyncio
async def test_half_hour_sync_consolidates_into_one_row(db, monkeypatch):
    uid = str(uuid.uuid4())
    cp_id = str(uuid.uuid4())
    today = "2026-06-13"

    # Seed counterparty
    await db.counterparties.insert_one({
        "id": cp_id, "user_id": uid, "kind": "ad_account",
        "name": "TestAd", "ad_provider": "snapchat", "balance": 0.0,
        "debt_mode": "auto",
    })

    # Stub _fetch_daily_spend to return controllable totals.
    import ad_account_routes as mod
    current_total = {"v": 100.0}

    async def fake_fetch(_db, _uid, provider, ext_id, frm, to):
        return [{"date": to, "spend": current_total["v"]}], "fake_collection"

    monkeypatch.setattr(mod, "_fetch_daily_spend", fake_fetch)

    # ── First sync: total=100 → expect 1 ledger row, amount=100
    await _run_sync_for_all(db, uid, today, today, force=True)
    rows = await db.ad_account_ledger.find(
        {"user_id": uid, "counterparty_id": cp_id, "date": today,
         "breakdown.auto_cron": True}, {"_id": 0}
    ).to_list(50)
    assert len(rows) == 1, f"expected 1 row after first sync, got {len(rows)}"
    assert rows[0]["amount"] == 100.0

    # ── Second sync: total=150 → SAME row, amount=150
    current_total["v"] = 150.0
    await _run_sync_for_all(db, uid, today, today, force=True)
    rows = await db.ad_account_ledger.find(
        {"user_id": uid, "counterparty_id": cp_id, "date": today,
         "breakdown.auto_cron": True}, {"_id": 0}
    ).to_list(50)
    assert len(rows) == 1, f"expected 1 row after second sync, got {len(rows)}"
    assert rows[0]["amount"] == 150.0
    # Only one liability should exist for this account
    liabs = await db.liabilities.find(
        {"user_id": uid, "counterparty_id": cp_id, "kind": "ad_account",
         "status": {"$in": ["unpaid", "partial"]}}, {"_id": 0}
    ).to_list(50)
    assert len(liabs) == 1, f"expected 1 open liability, got {len(liabs)}"
    assert liabs[0]["expected_amount"] == 150.0

    # ── Third sync: no change → no-op, still 1 row
    await _run_sync_for_all(db, uid, today, today, force=True)
    rows = await db.ad_account_ledger.find(
        {"user_id": uid, "counterparty_id": cp_id, "date": today,
         "breakdown.auto_cron": True}, {"_id": 0}
    ).to_list(50)
    assert len(rows) == 1

    # Cleanup
    await db.counterparties.delete_one({"id": cp_id})
    await db.ad_account_ledger.delete_many({"user_id": uid})
    await db.liabilities.delete_many({"user_id": uid})


@pytest.mark.asyncio
async def test_pre_existing_duplicates_are_collapsed(db, monkeypatch):
    uid = str(uuid.uuid4())
    cp_id = str(uuid.uuid4())
    today = "2026-06-13"
    await db.counterparties.insert_one({
        "id": cp_id, "user_id": uid, "kind": "ad_account",
        "name": "TestAd2", "ad_provider": "snapchat", "balance": 0.0,
        "debt_mode": "auto",
    })
    # Seed 3 pre-existing duplicate rows (sum = 80, simulating
    # old "row-per-half-hour" behavior).
    base_dt = datetime(2026, 6, 13, 9, 0, 0, tzinfo=timezone.utc)
    for i, amt in enumerate([20, 30, 30]):
        await db.ad_account_ledger.insert_one({
            "id": str(uuid.uuid4()), "user_id": uid, "counterparty_id": cp_id,
            "type": "spend", "amount": amt, "balance_after": 0,
            "debt_after": 80, "date": today,
            "breakdown": {"auto_cron": True, "from_balance": 0,
                          "uncovered": amt, "mode": "auto",
                          "delta_applied": amt, "platform_total": 80},
            "created_at": (base_dt.replace(hour=9 + i)).isoformat(),
        })
    await db.liabilities.insert_one({
        "id": str(uuid.uuid4()), "user_id": uid, "kind": "ad_account",
        "counterparty_id": cp_id, "expected_amount": 80.0, "paid_amount": 0.0,
        "due_date": today, "status": "unpaid",
        "description": "old debt",
        "source": "ad_account_cron", "auto_generated": True,
        "created_at": base_dt.isoformat(), "updated_at": base_dt.isoformat(),
    })

    import ad_account_routes as mod
    async def fake_fetch(_db, _uid, provider, ext_id, frm, to):
        return [{"date": to, "spend": 100.0}], "fake_collection"
    monkeypatch.setattr(mod, "_fetch_daily_spend", fake_fetch)

    # First sync AFTER the fix: should collapse 3 rows to 1, amount=100
    await _run_sync_for_all(db, uid, today, today, force=True)

    rows = await db.ad_account_ledger.find(
        {"user_id": uid, "counterparty_id": cp_id, "date": today,
         "breakdown.auto_cron": True}, {"_id": 0}
    ).to_list(50)
    assert len(rows) == 1, f"expected 1 row after collapse, got {len(rows)}"
    assert rows[0]["amount"] == 100.0

    # Cleanup
    await db.counterparties.delete_one({"id": cp_id})
    await db.ad_account_ledger.delete_many({"user_id": uid})
    await db.liabilities.delete_many({"user_id": uid})
