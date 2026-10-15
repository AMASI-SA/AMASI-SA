"""TikTok Make.com spend must reconcile into the financial ad ledger."""

import os
import sys
import uuid
from pathlib import Path

import pytest
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

load_dotenv("/app/backend/.env")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ad_account_routes as routes  # noqa: E402


@pytest.fixture
def db():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    return client[os.environ["DB_NAME"]]


@pytest.mark.asyncio
async def test_make_tiktok_is_targeted_and_idempotent(db, monkeypatch):
    uid = str(uuid.uuid4())
    account_id = str(uuid.uuid4())
    spend_date = "2026-08-25"
    current_total = {"value": 100.0}

    async def fake_fetch(_db, _uid, provider, ext_id, from_date, to_date):
        assert provider == "tiktok"
        assert ext_id is None
        assert from_date == spend_date
        assert to_date == spend_date
        return [{"date": spend_date, "spend": current_total["value"]}], "tiktok_ads_daily"

    monkeypatch.setattr(routes, "_fetch_daily_spend", fake_fetch)
    await db.counterparties.insert_one({
        "id": account_id,
        "user_id": uid,
        "kind": "ad_account",
        "name": "TikTok via Make",
        "ad_provider": "tiktok",
        "sync_via": "make_com",
        "balance": 0.0,
        "debt_mode": "auto",
    })

    try:
        # The half-hour direct-API scheduler must continue ignoring Make.com.
        default_result = await routes._run_sync_for_all(
            db, uid, spend_date, spend_date, force=True,
        )
        assert default_result == []

        targeted = dict(
            force=True,
            provider_filter={"tiktok"},
            include_make=True,
            account_ids={account_id},
        )
        await routes._run_sync_for_all(
            db, uid, spend_date, spend_date, **targeted,
        )
        rows = await db.ad_account_ledger.find({
            "user_id": uid,
            "counterparty_id": account_id,
            "date": spend_date,
            "breakdown.auto_cron": True,
        }, {"_id": 0}).to_list(10)
        assert len(rows) == 1
        assert rows[0]["amount"] == 100.0

        # A later Make retry updates the same cumulative row, not a duplicate.
        current_total["value"] = 150.0
        await routes._run_sync_for_all(
            db, uid, spend_date, spend_date, **targeted,
        )
        rows = await db.ad_account_ledger.find({
            "user_id": uid,
            "counterparty_id": account_id,
            "date": spend_date,
            "breakdown.auto_cron": True,
        }, {"_id": 0}).to_list(10)
        assert len(rows) == 1
        assert rows[0]["amount"] == 150.0
    finally:
        await db.counterparties.delete_one({"id": account_id})
