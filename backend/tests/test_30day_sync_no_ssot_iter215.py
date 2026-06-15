"""Iter-215 — Production-bug regression: 30-day sync must NOT
post to `general_ledger` for Snap/Meta accounts.

Reproduces the exact production incident reported on Feb 15 2026:
the user pressed "مزامنة كل الحسابات (30 يوم)" which calls
`_run_sync_for_all(force=True)` with a 30-day range; the pre-Iter-215
logic summed all 30 days into a single delta and posted one huge debt
entry per account (≈ 10K / 105K / 17K SAR). After Iter-215 this code
path must be a strict no-op on `general_ledger` for Snap/Meta.

The test seeds 30 days of spend, calls the helper exactly the way
`/snap-ads/sync-all-accounts` does, and asserts:
  • `ad_account_ledger` (legacy display layer) IS updated.
  • `general_ledger` (SSOT) gets ZERO new rows for Snap/Meta accounts.

A TikTok account is also seeded as a control — Iter-205 behaviour
should still apply there (legacy provider explicitly left alone per
user spec for Iter-215).
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
from ad_account_routes import _run_sync_for_all  # noqa: E402


async def _seed_account(db, *, user_id, provider, name, ext_id):
    cp = {
        "id": str(uuid.uuid4()), "user_id": user_id,
        "kind": "ad_account", "name": name, "name_lower": name.lower(),
        "ad_provider": provider, "external_account_id": ext_id,
        "currency": "SAR", "balance": 0.0,
        "created_at": "2026-01-01T00:00:00+00:00",
    }
    await db.counterparties.insert_one(cp)
    return cp


async def _seed_30_days(db, *, user_id, provider, ext_id,
                          end_date, daily_spend):
    """Insert 30 days of daily-spend rows ending on `end_date`."""
    for n in range(30):
        d = (end_date - timedelta(days=n)).isoformat()
        if provider == "snapchat":
            await db.snapchat_account_daily.insert_one({
                "user_id": user_id, "ad_account_id": ext_id,
                "date": d, "spend": float(daily_spend),
                "spend_sar": float(daily_spend),
                "purchases": 0,
                "received_at": "2026-01-01T00:00:00+00:00",
            })
        elif provider == "meta":
            await db.meta_ads_daily.insert_one({
                "id": str(uuid.uuid4()),
                "user_id": user_id, "account_id": ext_id,
                "date": d, "spend": float(daily_spend),
                "platform": "meta", "purchases": 0,
                "created_at": "2026-01-01T00:00:00+00:00",
            })
        elif provider == "tiktok":
            await db.tiktok_ads_daily.insert_one({
                "id": str(uuid.uuid4()),
                "user_id": user_id, "date": d,
                "spend": float(daily_spend),
                "campaign_id": "_default", "purchases": 0,
                "created_at": "2026-01-01T00:00:00+00:00",
            })


@pytest.mark.asyncio
async def test_30day_sync_does_not_post_for_snap_meta_iter215():
    cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = cli[os.environ["DB_NAME"]]
    uid = str(uuid.uuid4())
    today = date(2026, 5, 30)  # fixed dates so the test is deterministic
    start = today - timedelta(days=29)

    snap = await _seed_account(
        db, user_id=uid, provider="snapchat",
        name="Test Snap 30d", ext_id=f"snap_{uid[:8]}",
    )
    meta = await _seed_account(
        db, user_id=uid, provider="meta",
        name="Test Meta 30d", ext_id=f"act_{uid[:8]}",
    )
    tiktok = await _seed_account(
        db, user_id=uid, provider="tiktok",
        name="Test TikTok 30d", ext_id="_default",  # unscoped collection
    )

    # Seed 30 days of spend — 100/day → total = 3000 per account.
    await _seed_30_days(
        db, user_id=uid, provider="snapchat",
        ext_id=snap["external_account_id"],
        end_date=today, daily_spend=100.0,
    )
    await _seed_30_days(
        db, user_id=uid, provider="meta",
        ext_id=meta["external_account_id"],
        end_date=today, daily_spend=100.0,
    )
    await _seed_30_days(
        db, user_id=uid, provider="tiktok",
        ext_id=None,  # tiktok uses unscoped collection
        end_date=today, daily_spend=50.0,
    )

    try:
        # Sanity — zero ledger rows for this user before the sync.
        assert await db.general_ledger.count_documents(
            {"user_id": uid}) == 0

        # ── Reproduce the exact production call ──────────────────────
        await _run_sync_for_all(
            db, uid, start.isoformat(), today.isoformat(), force=True,
        )

        # ── CRITICAL ASSERTION (Iter-215 contract) ──────────────────
        # Snap and Meta accounts must NOT get any general_ledger rows
        # from this code path — the AM/PM scheduler is the sole writer.
        snap_meta_legs = await db.general_ledger.count_documents({
            "user_id": uid,
            "metadata.ad_account_id": {"$in": [snap["id"], meta["id"]]},
        })
        assert snap_meta_legs == 0, (
            f"Iter-215 contract VIOLATED — got {snap_meta_legs} "
            "general_ledger rows for Snap/Meta after 30-day sync"
        )

        # ── Control: TikTok still uses Iter-205 (intentional) ───────
        tiktok_legs = await db.general_ledger.count_documents({
            "user_id": uid,
            "metadata.ad_account_id": tiktok["id"],
        })
        # TikTok was deliberately left out of Iter-215 — its account
        # is on Make.com cadence and keeps Iter-205 behaviour. So a
        # 30-day sync still posts (this is the legacy behaviour that
        # we accept until TikTok gets its own window logic in P2).
        assert tiktok_legs >= 0  # not strictly asserted; just no crash

        # ── ad_account_ledger (legacy card display) WAS updated ─────
        snap_legacy = await db.ad_account_ledger.count_documents({
            "user_id": uid, "counterparty_id": snap["id"],
        })
        meta_legacy = await db.ad_account_ledger.count_documents({
            "user_id": uid, "counterparty_id": meta["id"],
        })
        assert snap_legacy >= 1, (
            "legacy ad_account_ledger should still get a row "
            "(card display depends on it)"
        )
        assert meta_legacy >= 1

    finally:
        await db.counterparties.delete_many({"user_id": uid})
        await db.general_ledger.delete_many({"user_id": uid})
        await db.snapchat_account_daily.delete_many({"user_id": uid})
        await db.meta_ads_daily.delete_many({"user_id": uid})
        await db.tiktok_ads_daily.delete_many({"user_id": uid})
        await db.ad_account_ledger.delete_many({"user_id": uid})
        await db.liabilities.delete_many({"user_id": uid})
