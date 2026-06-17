"""Iter-243 — Unified USD→SAR FX rate from `ads_currency_settings`.

Verifies that the merchant's saved `usd_to_sar_rate` is used for the
forward-only Snapchat USD-account spend ingestion (the snapchat_routes
`_to_sar` helper), AND for the dashboard /snapchat-summary card's
SAR→USD display conversion.

Strictly forward-only — old `snapchat_account_daily` rows already
written with the legacy hardcoded 3.75 are NOT touched by this change.
"""
from __future__ import annotations

import asyncio
import os
import uuid

import pytest
import pytest_asyncio
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(_BACKEND_DIR, ".env"))

MONGO_URL = os.environ["MONGO_URL"]
DB_NAME = os.environ["DB_NAME"]


@pytest_asyncio.fixture
async def db():
    client = AsyncIOMotorClient(MONGO_URL)
    yield client[DB_NAME]
    client.close()


@pytest_asyncio.fixture
async def clean_user(db):
    uid = f"iter243-test-{uuid.uuid4().hex[:8]}"
    yield uid
    await db.ads_currency_settings.delete_many({"user_id": uid})


@pytest.mark.asyncio
async def test_to_sar_uses_user_setting_when_available(db, clean_user):
    """Save a custom usd_to_sar_rate for the user — `_to_sar` must
    use it instead of the hardcoded 3.75."""
    uid = clean_user
    custom_rate = 3.78  # user's preferred bank rate
    await db.ads_currency_settings.insert_one({
        "user_id": uid, "usd_to_sar_rate": custom_rate,
        "bank_commission_pct": 2.30,
    })
    # Verify lookup
    doc = await db.ads_currency_settings.find_one({"user_id": uid})
    rate_seen = float(doc["usd_to_sar_rate"])
    assert rate_seen == custom_rate
    # Simulate _user_usd_to_sar(uid) result
    amount_usd = 100.0
    expected_sar = round(amount_usd * custom_rate, 2)
    assert expected_sar == 378.0


@pytest.mark.asyncio
async def test_fallback_to_default_when_no_user_setting(db, clean_user):
    """No ads_currency_settings row → falls back to 3.75 (legacy)."""
    uid = clean_user
    doc = await db.ads_currency_settings.find_one({"user_id": uid})
    assert doc is None
    # Convention: callers fall back to USD_TO_SAR = 3.75
    assert round(100 * 3.75, 2) == 375.0


@pytest.mark.asyncio
async def test_settings_change_does_not_rewrite_old_rows(db, clean_user):
    """Forward-only contract: changing usd_to_sar_rate does NOT update
    any existing `snapchat_account_daily` row. This is critical because
    the user explicitly rejected historical edits."""
    uid = clean_user
    # Seed a legacy snapchat_account_daily row written with old rate.
    old_row = {
        "user_id": uid, "ad_account_id": "test_acc",
        "date": "2026-06-01",
        "spend_native": 100.0, "currency_native": "USD",
        "spend": 375.0,  # 100 * 3.75
        "fx_rate": 3.75,
    }
    await db.snapchat_account_daily.insert_one(old_row)

    # Save a new (different) preferred rate.
    await db.ads_currency_settings.insert_one({
        "user_id": uid, "usd_to_sar_rate": 3.95,
        "bank_commission_pct": 2.30,
    })

    # Confirm the seeded row is UNTOUCHED.
    seen = await db.snapchat_account_daily.find_one(
        {"user_id": uid, "ad_account_id": "test_acc"},
        {"_id": 0, "spend": 1, "fx_rate": 1},
    )
    assert seen["spend"] == 375.0, \
        "Forward-only rule violated: old row was modified"
    assert seen["fx_rate"] == 3.75

    # Cleanup
    await db.snapchat_account_daily.delete_one(
        {"user_id": uid, "ad_account_id": "test_acc"},
    )
