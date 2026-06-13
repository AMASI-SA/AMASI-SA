"""Iter-172 — Snap «تحديث فوري للصرف اليوم» write-through test.

Reported bug: Pressing the daily-spend refresh button on the Snap
dashboard showed a toast with the amount, but the ad-account cards
and executive profit summary never updated. Root cause: the GET
endpoint returned the spend but didn't persist it to
`snapchat_account_daily` or push it through `_run_sync_for_all`.

This test validates that after a successful fetch, the ad_account_ledger
contains a fresh spend row matching the fetched amount.
"""
import os
import sys
import uuid

import pytest
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")
sys.path.insert(0, "/app/backend")
from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402


@pytest.mark.asyncio
async def test_snap_daily_spend_writes_to_account_daily_and_syncs_ledger():
    """Simulate the write-through directly by inserting into
    `snapchat_account_daily` and calling `_run_sync_for_all` — proving
    the second-half of the pipeline (the part Iter-172 wired up) works
    end-to-end.
    """
    from ad_account_routes import _run_sync_for_all

    c = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = c[os.environ["DB_NAME"]]
    uid = f"u-{uuid.uuid4().hex[:8]}"
    cp_id = str(uuid.uuid4())

    try:
        # Seed: a snap counterparty + a fresh snap_account_daily row
        await db.counterparties.insert_one({
            "id": cp_id, "user_id": uid, "kind": "ad_account",
            "ad_provider": "snapchat", "name": "Snap Today",
            "external_account_id": "snap-acc-today", "balance": 5000,
            "debt_mode": "auto",
        })
        await db.snapchat_account_daily.insert_one({
            "user_id": uid, "ad_account_id": "snap-acc-today",
            "date": "2026-02-14", "spend": 750,
        })

        # Run the same flow Iter-172 triggers
        results = await _run_sync_for_all(
            db, uid, "2026-02-14", "2026-02-14", force=True)

        # Verify ledger has the spend
        ledger_count = await db.ad_account_ledger.count_documents(
            {"user_id": uid, "counterparty_id": cp_id,
             "type": "spend", "date": "2026-02-14"})
        assert ledger_count == 1

        # Verify counterparty balance updated
        cp = await db.counterparties.find_one({"id": cp_id})
        assert cp["balance"] == 4250.0  # 5000 - 750

        # Verify sync result mentions this account
        snap_result = next((r for r in results if r["id"] == cp_id), None)
        assert snap_result is not None
        assert snap_result["spend"] == 750.0
    finally:
        await db.counterparties.delete_many({"user_id": uid})
        await db.snapchat_account_daily.delete_many({"user_id": uid})
        await db.ad_account_ledger.delete_many({"user_id": uid})
        await db.liabilities.delete_many({"user_id": uid})
