"""Iter-215b — Regression: catch-up MUST NOT backfill historical dates.

Reproduces the production incident immediately after Iter-215 deploy
(Feb 15 2026): the hourly catch-up looped 7 days back and posted
AM_00_12 for every day × every Snap/Meta account → ~21 unwanted
historical entries appeared seconds after deploy.

Iter-215b restricts catch-up to:
  • today's AM (only if Riyadh time is past 12:30)
  • yesterday's PM (only if Riyadh time is past 00:30)
  • yesterday's CORRECTION (only if Riyadh time is past 12:30)

Anything earlier — including 2-days-ago, last week, etc. — must NEVER
be created by catch-up. The merchant prefers a missed posting over an
invented one.
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
from ad_spend_windows import catch_up_window_posts  # noqa: E402
from tz_utils import riyadh_today  # noqa: E402


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


async def _seed_meta_spend(db, *, user_id, ext_id, date_iso, spend):
    await db.meta_ads_daily.insert_one({
        "id": str(uuid.uuid4()),
        "user_id": user_id, "account_id": ext_id,
        "date": date_iso, "spend": float(spend),
        "platform": "meta", "purchases": 0,
        "created_at": "2026-01-01T00:00:00+00:00",
    })


@pytest.mark.asyncio
async def test_catchup_does_not_backfill_history_iter215b():
    cli = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = cli[os.environ["DB_NAME"]]
    uid = str(uuid.uuid4())
    today = riyadh_today()
    ext = f"act_{uid[:8]}"
    cp = await _seed_account(
        db, user_id=uid, provider="meta",
        name="Catchup Test Meta", ext_id=ext,
    )

    # Seed 8 days of historical spend (today + 7 prior days).
    for n in range(8):
        d = (today - timedelta(days=n)).isoformat()
        await _seed_meta_spend(
            db, user_id=uid, ext_id=ext, date_iso=d, spend=100.0,
        )

    try:
        assert await db.general_ledger.count_documents(
            {"user_id": uid}) == 0

        # Run catch-up — the hourly scheduler trigger.
        await catch_up_window_posts(db, user_id=uid)

        # Inspect every Iter-215 entry created. None must reference a
        # spend_date older than yesterday.
        oldest_allowed = (today - timedelta(days=1)).isoformat()
        async for r in db.general_ledger.find({
            "user_id": uid, "metadata.iter": "iter215",
            "status": "posted",
        }):
            sd = r.get("metadata", {}).get("spend_date")
            assert sd >= oldest_allowed, (
                f"Iter-215b CONTRACT VIOLATED — catch-up created an "
                f"entry for spend_date={sd} which is older than "
                f"yesterday ({oldest_allowed}). Acceptable dates: "
                f"only today ({today.isoformat()}) or yesterday."
            )

        # Specifically: 6 historical days [today-7 … today-2] must
        # have ZERO Iter-215 entries.
        for n in range(2, 8):
            d = (today - timedelta(days=n)).isoformat()
            cnt = await db.general_ledger.count_documents({
                "user_id": uid, "metadata.iter": "iter215",
                "metadata.spend_date": d,
            })
            assert cnt == 0, (
                f"historical date {d} unexpectedly received "
                f"{cnt} entries"
            )

    finally:
        await db.counterparties.delete_many({"user_id": uid})
        await db.general_ledger.delete_many({"user_id": uid})
        await db.meta_ads_daily.delete_many({"user_id": uid})
