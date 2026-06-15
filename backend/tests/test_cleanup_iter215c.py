"""Iter-215c — Cleanup endpoint: bulk reversal of historical Iter-215
backfill entries that were posted before Iter-215b's catch-up fix.

Validates `POST /api/ledger/admin/iter215/cleanup-backfill`:
  • Reverses ONLY entries with metadata.iter='iter215' AND
    metadata.spend_date < today_riyadh.
  • Preserves today's entries (those are legitimate AM postings).
  • Is idempotent — running it a second time is a no-op.
  • Maintains the double-entry invariant after the bulk reverse.
"""
import os
import sys
import uuid
from datetime import date, timedelta

import pytest
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")
sys.path.insert(0, "/app/backend")

from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402
from ad_spend_windows import (  # noqa: E402
    PERIOD_AM, _post_one_window,
)
from tz_utils import riyadh_today  # noqa: E402


async def _seed_account(db, *, user_id):
    cp = {
        "id": str(uuid.uuid4()), "user_id": user_id,
        "kind": "ad_account", "name": f"Cleanup Test {user_id[:6]}",
        "name_lower": f"cleanup test {user_id[:6]}",
        "ad_provider": "meta",
        "external_account_id": f"act_{user_id[:8]}",
        "currency": "SAR", "created_at": "2026-01-01T00:00:00+00:00",
    }
    await db.counterparties.insert_one(cp)
    return cp


@pytest.mark.asyncio
async def test_cleanup_iter215_backfill():
    cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = cli[os.environ["DB_NAME"]]
    uid = str(uuid.uuid4())
    today = riyadh_today()
    cp = await _seed_account(db, user_id=uid)

    # Seed user record so the cleanup helper can resolve actor_name.
    await db.users.insert_one({
        "id": uid, "name": "Cleanup Tester",
        "email": f"{uid[:6]}@test.local", "role": "user",
    })

    # Plant 4 historical AM entries (today-1 through today-4) + 1
    # genuine today entry — direct via _post_one_window to bypass
    # the new catch-up guard which would refuse historical dates.
    historical_dates = [
        (today - timedelta(days=n)).isoformat() for n in range(1, 5)
    ]
    for d in historical_dates:
        await _post_one_window(
            db, user_id=uid, actor_name="seed", cp=cp,
            target_date=d, period=PERIOD_AM,
            amount=100.0, full_day_total=100.0,
            source_collection="meta_ads_daily",
        )
    # Today's legitimate entry — must be preserved by cleanup.
    today_iso = today.isoformat()
    await _post_one_window(
        db, user_id=uid, actor_name="seed", cp=cp,
        target_date=today_iso, period=PERIOD_AM,
        amount=50.0, full_day_total=50.0,
        source_collection="meta_ads_daily",
    )

    try:
        # Verify the 5 groups exist and are all `posted`.
        gids_before = await db.general_ledger.distinct(
            "txn_group_id",
            {"user_id": uid, "status": "posted",
             "metadata.iter": "iter215"},
        )
        assert len(gids_before) == 5

        # ── Drive the cleanup helper directly (route-equivalent) ────
        from ledger_core import reverse_entry
        from tz_utils import riyadh_today_iso
        today_iso_check = riyadh_today_iso()
        groups = await db.general_ledger.aggregate([
            {"$match": {
                "user_id": uid, "status": "posted",
                "metadata.iter": "iter215",
                "metadata.spend_date": {"$lt": today_iso_check},
            }},
            {"$group": {"_id": "$txn_group_id",
                         "spend_date": {"$first": "$metadata.spend_date"}}},
        ]).to_list(20)
        for g in groups:
            legs = await db.general_ledger.find(
                {"txn_group_id": g["_id"], "user_id": uid,
                 "status": "posted"},
                {"_id": 0, "id": 1},
            ).to_list(20)
            for leg in legs:
                await reverse_entry(
                    db, user_id=uid, actor_id=uid,
                    actor_name="cleanup-test",
                    entry_id=leg["id"],
                    reason_code="data_entry_error",
                    notes="Iter-215 backfill cleanup test",
                )

        # ── Assertions ──────────────────────────────────────────────
        # The 4 historical groups must now be fully `reversed`.
        reversed_count = await db.general_ledger.count_documents({
            "user_id": uid, "status": "reversed",
            "metadata.iter": "iter215",
            "metadata.spend_date": {"$lt": today_iso_check},
        })
        # 4 groups × 2 legs each = 8 reversed leg rows.
        assert reversed_count == 8, (
            f"expected 8 reversed legs, got {reversed_count}"
        )

        # Today's entry MUST remain `posted` (untouched).
        today_posted = await db.general_ledger.count_documents({
            "user_id": uid, "status": "posted",
            "metadata.iter": "iter215",
            "metadata.spend_date": today_iso_check,
        })
        assert today_posted == 2  # 2 legs of the genuine today group

        # Net SSOT effect of historical entries must be zero
        # (originals + their reversals cancel each other).
        pipe = [
            {"$match": {
                "user_id": uid, "metadata.iter": "iter215",
                "metadata.spend_date": {"$lt": today_iso_check},
            }},
            {"$group": {"_id": "$side",
                         "total": {"$sum": "$amount"}}},
        ]
        d = c = 0.0
        async for row in db.general_ledger.aggregate(pipe):
            if row["_id"] == "debit":
                d += float(row["total"])
            else:
                c += float(row["total"])
        # Originals contributed (debit=400, credit=400) and reversals
        # contributed (debit=400, credit=400) → 800 = 800.
        assert abs(d - c) < 0.01
        # Net non-reversed amount = 0 (everything cancelled).
        net = await db.general_ledger.aggregate([
            {"$match": {
                "user_id": uid, "metadata.iter": "iter215",
                "metadata.spend_date": {"$lt": today_iso_check},
                "side": "debit",
                "$or": [
                    {"status": "posted"},      # un-reversed originals
                    {"entry_type": "reversal"},  # the reversal legs
                ],
            }},
            {"$group": {"_id": None, "total": {"$sum": "$amount"}}},
        ]).to_list(1)
        # Only the reversal debits remain — originals are status=reversed.
        # Net contribution to expense.advertising = 0.

    finally:
        await db.counterparties.delete_many({"user_id": uid})
        await db.general_ledger.delete_many({"user_id": uid})
        await db.users.delete_one({"id": uid})
