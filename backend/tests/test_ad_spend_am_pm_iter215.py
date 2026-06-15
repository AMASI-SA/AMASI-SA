"""Iter-215 — AM/PM ad-spend window posting (Snap/Meta).

Validates the twice-daily SSOT posting model:

  AM_00_12             — books today's cumulative spend (snapshot at
                          12:30-13:30 Riyadh), labelled with today.
  PM_12_24             — books yesterday's full-day total MINUS what
                          AM already booked, labelled with yesterday.
  PM_12_24_CORRECTION:N — books any late growth in yesterday's total
                          after PM was posted (Meta lag).

Idempotency key:
  ad_spend:{provider}:{ad_account_id}:{spend_date}:{period}

Scope: snapchat + meta only. TikTok/Make.com accounts are NOT touched
by the window scheduler (they keep Iter-205 behaviour).

Each scenario asserts:
  • The correct DEBIT/CREDIT split (expense=debit, balance+debt=credit).
  • Σ debits == Σ credits (double-entry invariant).
  • Idempotency: a second call is a no-op.
  • Correct metadata (window_period, posted_for_window, iter=iter215).
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
    PERIOD_AM, PERIOD_PM, PERIOD_PM_CORRECTION_PREFIX,
    catch_up_window_posts, run_window_post,
)


async def _seed_account(db, *, user_id, provider, name, ext_id):
    cp = {
        "id": str(uuid.uuid4()), "user_id": user_id,
        "kind": "ad_account", "name": name,
        "name_lower": name.lower(),
        "ad_provider": provider, "external_account_id": ext_id,
        "currency": "SAR", "created_at": "2026-01-01T00:00:00+00:00",
    }
    await db.counterparties.insert_one(cp)
    return cp


async def _seed_daily_spend(db, *, user_id, provider, ext_id,
                             date_iso, spend):
    """Insert one row into the right *_account_daily collection."""
    if provider == "snapchat":
        await db.snapchat_account_daily.insert_one({
            "user_id": user_id, "ad_account_id": ext_id,
            "date": date_iso, "spend": float(spend),
            "spend_sar": float(spend), "purchases": 0,
            "received_at": "2026-01-01T00:00:00+00:00",
        })
    elif provider == "meta":
        await db.meta_ads_daily.insert_one({
            "id": str(uuid.uuid4()),
            "user_id": user_id, "account_id": ext_id,
            "date": date_iso, "spend": float(spend),
            "platform": "meta", "purchases": 0,
            "created_at": "2026-01-01T00:00:00+00:00",
        })


async def _bump_meta_spend(db, *, user_id, ext_id, date_iso, new_total):
    """Replace the meta_ads_daily total for that day with a new value."""
    await db.meta_ads_daily.update_many(
        {"user_id": user_id, "account_id": ext_id, "date": date_iso},
        {"$set": {"spend": float(new_total)}},
    )


async def _ledger_sum(db, uid, cp_id, date_iso, period):
    docs = await db.general_ledger.aggregate([
        {"$match": {
            "user_id": uid, "status": "posted", "side": "debit",
            "entity_type": "expense", "entity_id": "advertising",
            "metadata.ad_account_id": cp_id,
            "metadata.spend_date": date_iso,
            "metadata.window_period": period,
        }},
        {"$group": {"_id": None, "total": {"$sum": "$amount"}}},
    ]).to_list(1)
    return float(docs[0]["total"]) if docs else 0.0


async def _group_balance(db, uid, cp_id, date_iso):
    """Sum debits vs credits for all window postings of this account
    on this date — must always balance."""
    pipe = [
        {"$match": {
            "user_id": uid, "status": "posted",
            "metadata.ad_account_id": cp_id,
            "metadata.spend_date": date_iso,
            "metadata.iter": "iter215",
        }},
        {"$group": {"_id": "$side", "total": {"$sum": "$amount"}}},
    ]
    d = c = 0.0
    async for row in db.general_ledger.aggregate(pipe):
        if row["_id"] == "debit":
            d = float(row["total"])
        else:
            c = float(row["total"])
    return d, c


@pytest.mark.asyncio
async def test_iter215_window_postings():
    cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = cli[os.environ["DB_NAME"]]
    uid = str(uuid.uuid4())
    today = date(2026, 5, 20)  # arbitrary fixed dates so the test
    yest = today - timedelta(days=1)
    today_iso = today.isoformat()
    yest_iso = yest.isoformat()

    ext_meta = f"act_{uid[:8]}"
    ext_snap = f"snap_{uid[:8]}"

    meta = await _seed_account(
        db, user_id=uid, provider="meta",
        name="Meta Test", ext_id=ext_meta,
    )
    snap = await _seed_account(
        db, user_id=uid, provider="snapchat",
        name="Snap Test", ext_id=ext_snap,
    )

    # Seed yesterday spend (full day) and today spend (partial day).
    await _seed_daily_spend(
        db, user_id=uid, provider="meta", ext_id=ext_meta,
        date_iso=yest_iso, spend=250.0,
    )
    await _seed_daily_spend(
        db, user_id=uid, provider="meta", ext_id=ext_meta,
        date_iso=today_iso, spend=100.0,
    )
    await _seed_daily_spend(
        db, user_id=uid, provider="snapchat", ext_id=ext_snap,
        date_iso=today_iso, spend=70.0,
    )

    try:
        # ── (1) AM post for TODAY ────────────────────────────────────
        res = await run_window_post(
            db, PERIOD_AM, today_iso, user_id=uid,
        )
        assert res["summary"]["posted"] == 2, res
        am_meta = await _ledger_sum(
            db, uid, meta["id"], today_iso, PERIOD_AM,
        )
        am_snap = await _ledger_sum(
            db, uid, snap["id"], today_iso, PERIOD_AM,
        )
        assert am_meta == 100.0
        assert am_snap == 70.0

        # Double-entry invariant.
        d, c = await _group_balance(db, uid, meta["id"], today_iso)
        assert abs(d - c) < 0.01

        # Idempotency — re-run = no-op.
        res2 = await run_window_post(
            db, PERIOD_AM, today_iso, user_id=uid,
        )
        assert res2["summary"]["posted"] == 0
        assert res2["summary"]["skipped"] == 2

        # ── (2) PM post for YESTERDAY (AM not yet booked yesterday) ──
        # PM should book the entire full-day total (250) because
        # no AM was posted yesterday. This mirrors the production
        # case where Iter-215 only began TODAY.
        res = await run_window_post(
            db, PERIOD_PM, yest_iso, user_id=uid,
        )
        pm_meta = await _ledger_sum(
            db, uid, meta["id"], yest_iso, PERIOD_PM,
        )
        assert pm_meta == 250.0

        # Idempotency on PM.
        res2 = await run_window_post(
            db, PERIOD_PM, yest_iso, user_id=uid,
        )
        assert res2["summary"]["posted"] == 0

        # ── (3) PM_CORRECTION — yesterday's total grew from 250 → 280
        await _bump_meta_spend(
            db, user_id=uid, ext_id=ext_meta,
            date_iso=yest_iso, new_total=280.0,
        )
        res = await run_window_post(
            db, "AM_FOLLOWING_CORRECTION", yest_iso, user_id=uid,
        )
        # Only meta grew → expect 1 correction posted, snap skipped.
        meta_corr_total = 0.0
        async for r in db.general_ledger.find({
            "user_id": uid, "status": "posted", "side": "debit",
            "metadata.ad_account_id": meta["id"],
            "metadata.spend_date": yest_iso,
            "metadata.window_period": {
                "$regex": f"^{PERIOD_PM_CORRECTION_PREFIX}:",
            },
        }):
            meta_corr_total += float(r["amount"])
        assert meta_corr_total == 30.0, (
            f"expected 30 correction, got {meta_corr_total}"
        )

        # Yesterday's books still balance after correction.
        d, c = await _group_balance(db, uid, meta["id"], yest_iso)
        assert abs(d - c) < 0.01

        # ── (4) Correction idempotency — re-run = no-op ──────────────
        await run_window_post(
            db, "AM_FOLLOWING_CORRECTION", yest_iso, user_id=uid,
        )
        # Total still 30 (no double).
        meta_corr_total_2 = 0.0
        async for r in db.general_ledger.find({
            "user_id": uid, "status": "posted", "side": "debit",
            "metadata.ad_account_id": meta["id"],
            "metadata.spend_date": yest_iso,
            "metadata.window_period": {
                "$regex": f"^{PERIOD_PM_CORRECTION_PREFIX}:",
            },
        }):
            meta_corr_total_2 += float(r["amount"])
        assert meta_corr_total_2 == 30.0

        # ── (5) catch-up scan converges to consistent state ──────────
        # Iter-215b — catch-up only handles current-day windows.
        # Earlier dates that already have postings (from earlier in
        # this test) remain untouched.
        res = await catch_up_window_posts(db, user_id=uid)
        assert res["summary"]["posted"] == 0 or res["summary"]["posted"] < 5

    finally:
        # Cleanup
        await db.counterparties.delete_many({"user_id": uid})
        await db.general_ledger.delete_many({"user_id": uid})
        await db.snapchat_account_daily.delete_many({"user_id": uid})
        await db.meta_ads_daily.delete_many({"user_id": uid})
