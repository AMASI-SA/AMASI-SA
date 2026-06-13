"""Iter-163 — Cross-account spend aggregation bug fix.

Production bug (Feb 2026): User clicked "مزامنة الكل الآن" on the Ad
Accounts page. A Snap counterparty had no `external_account_id` set. The
old `_fetch_daily_spend` silently dropped the per-account filter and
aggregated `snapchat_account_daily` rows for EVERY snap account this
user owns — resulting in today's spend showing as 100,000 SAR across
the dashboard and every ad-account card.

Fix: `_fetch_daily_spend` MUST skip scoped sources when no
`external_id` is provided. `_run_sync_for_all` MUST skip Snap/Meta
counterparties that lack `external_account_id` and surface a warning.
"""
import os
import sys
import uuid

import pytest
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")
sys.path.insert(0, "/app/backend")

from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402
from ad_account_routes import (  # noqa: E402
    _fetch_daily_spend, _run_sync_for_all,
)


@pytest.mark.asyncio
async def test_fetch_daily_spend_skips_scoped_source_when_no_external_id():
    """When external_id is missing for Snapchat, the scoped source
    `snapchat_account_daily` must NOT be queried at all — preventing
    aggregation across other accounts.
    """
    c = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = c[os.environ["DB_NAME"]]
    uid = f"u-{uuid.uuid4().hex[:8]}"

    try:
        # Seed: 3 snap accounts each spending 30,000 on the same day.
        for i in range(3):
            await db.snapchat_account_daily.insert_one({
                "user_id": uid,
                "ad_account_id": f"acc-{i}",
                "date": "2026-02-12",
                "spend": 30000.0,
            })

        # No external_id supplied → must return empty, NOT 90,000.
        rows, src = await _fetch_daily_spend(
            db, uid, "snapchat", None, "2026-02-12", "2026-02-12")
        assert rows == [], (
            f"Expected empty rows when external_id missing, got {rows}")

        # With a valid external_id → returns only that account's spend.
        rows, src = await _fetch_daily_spend(
            db, uid, "snapchat", "acc-1", "2026-02-12", "2026-02-12")
        assert len(rows) == 1
        assert rows[0]["spend"] == 30000.0
    finally:
        await db.snapchat_account_daily.delete_many({"user_id": uid})


@pytest.mark.asyncio
async def test_run_sync_for_all_skips_missing_external_id_with_warning():
    """A snap counterparty without external_account_id must be skipped
    and surfaced with reason=missing_external_account_id. No ledger row
    must be created, no debt must be raised.
    """
    c = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = c[os.environ["DB_NAME"]]
    uid = f"u-{uuid.uuid4().hex[:8]}"
    cp_id = str(uuid.uuid4())

    try:
        await db.counterparties.insert_one({
            "id": cp_id, "user_id": uid, "kind": "ad_account",
            "ad_provider": "snapchat", "name": "Snap بدون معرّف",
            "external_account_id": None, "balance": 0,
            "debt_mode": "auto",
        })
        # Seed cross-account daily spend that should NOT be aggregated.
        await db.snapchat_account_daily.insert_one({
            "user_id": uid, "ad_account_id": "acc-A",
            "date": "2026-02-12", "spend": 50000.0,
        })
        await db.snapchat_account_daily.insert_one({
            "user_id": uid, "ad_account_id": "acc-B",
            "date": "2026-02-12", "spend": 50000.0,
        })

        results = await _run_sync_for_all(
            db, uid, "2026-02-12", "2026-02-12", force=True,
        )
        assert len(results) == 1
        r = results[0]
        assert r["skipped"] is True
        assert r["reason"] == "missing_external_account_id"
        assert "warning" in r

        # No ledger row, no liability created.
        ledger_count = await db.ad_account_ledger.count_documents(
            {"user_id": uid})
        assert ledger_count == 0
        liab_count = await db.liabilities.count_documents(
            {"user_id": uid, "kind": "ad_account"})
        assert liab_count == 0
    finally:
        await db.counterparties.delete_many({"user_id": uid})
        await db.snapchat_account_daily.delete_many({"user_id": uid})
        await db.ad_account_ledger.delete_many({"user_id": uid})
        await db.liabilities.delete_many({"user_id": uid})


@pytest.mark.asyncio
async def test_run_sync_for_all_correctly_scopes_with_external_id():
    """Sanity check: when external_account_id IS set, only that account's
    spend is counted — never the sibling account's.
    """
    c = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = c[os.environ["DB_NAME"]]
    uid = f"u-{uuid.uuid4().hex[:8]}"
    cp_id = str(uuid.uuid4())

    try:
        await db.counterparties.insert_one({
            "id": cp_id, "user_id": uid, "kind": "ad_account",
            "ad_provider": "snapchat", "name": "Snap A",
            "external_account_id": "acc-A", "balance": 0,
            "debt_mode": "auto",
        })
        await db.snapchat_account_daily.insert_one({
            "user_id": uid, "ad_account_id": "acc-A",
            "date": "2026-02-12", "spend": 1500.0,
        })
        # Sibling account's spend that MUST be ignored.
        await db.snapchat_account_daily.insert_one({
            "user_id": uid, "ad_account_id": "acc-B",
            "date": "2026-02-12", "spend": 98500.0,
        })

        results = await _run_sync_for_all(
            db, uid, "2026-02-12", "2026-02-12", force=True)
        r = next(x for x in results if x["id"] == cp_id)
        assert r.get("skipped") is None or r.get("skipped") is False
        assert r["spend"] == 1500.0, (
            f"Cross-account spend leaked: expected 1500, got {r['spend']}")
    finally:
        await db.counterparties.delete_many({"user_id": uid})
        await db.snapchat_account_daily.delete_many({"user_id": uid})
        await db.ad_account_ledger.delete_many({"user_id": uid})
        await db.liabilities.delete_many({"user_id": uid})
