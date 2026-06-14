"""Iter-179 — COD must be excluded from Phase 4 migration.

After reviewing `/diagnostics/cod-source` on production the merchant
formally requested (Feb 2026):

    "حالياً: ❌ عدم ترحيل 40,123.78 كرصيد افتتاحي لـ COD.
                ✅ ترحيل بقية الحسابات كالمعتاد.
                ✅ تأجيل COD حتى اكتمال Sprint شركات الشحن."

Rationale: the COD account's `expected_orders_balance` is computed
from *Confirmed* orders (not Delivered), so it includes orders
in transit and orders confirmed-but-not-shipped. Migrating that
gross figure would post phantom assets.

These tests pin the migration output: COD-shaped payment_platform
accounts must NEVER appear in the migration snapshot, regardless of
how the merchant names the account (Arabic + English + hamza
variants), regardless of `normalized_payment_method`.
"""
from __future__ import annotations

import os
import sys
import uuid

import pytest
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")
sys.path.insert(0, "/app/backend")
from migration_routes import _legacy_payment_platform_balances  # noqa: E402


@pytest.fixture
def db_client():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    return client[os.environ["DB_NAME"]]


async def _cleanup(database, uid):
    await database.accounts.delete_many({"user_id": uid})


@pytest.mark.asyncio
async def test_cod_account_excluded_by_arabic_name(db_client):
    """The COD account named exactly «الدفع عند الاستلام» must NOT
    appear in the migration list."""
    uid = f"test-iter179-{uuid.uuid4()}"
    await db_client.accounts.insert_many([
        {"id": str(uuid.uuid4()), "user_id": uid,
         "account_type": "payment_platform",
         "name": "الدفع عند الاستلام",
         "current_balance": 40123.78},
        {"id": str(uuid.uuid4()), "user_id": uid,
         "account_type": "payment_platform",
         "name": "سلة", "current_balance": 1000.0},
    ])
    rows = await _legacy_payment_platform_balances(db_client, uid)
    names = [r["name"] for r in rows]
    assert "سلة" in names
    assert not any("استلام" in (n or "") for n in names), \
        f"COD account leaked into migration: {names}"
    await _cleanup(db_client, uid)


@pytest.mark.asyncio
async def test_cod_account_excluded_by_hamza_variant(db_client):
    """The merchant's actual Salla data uses الإستلام (hamza-below
    on alef) instead of الاستلام. Both must be excluded."""
    uid = f"test-iter179-{uuid.uuid4()}"
    await db_client.accounts.insert_many([
        {"id": str(uuid.uuid4()), "user_id": uid,
         "account_type": "payment_platform",
         "name": "دفع عند الإستلام",  # hamza variant
         "current_balance": 9999.0},
    ])
    rows = await _legacy_payment_platform_balances(db_client, uid)
    assert rows == [], f"Hamza-variant COD leaked: {rows}"
    await _cleanup(db_client, uid)


@pytest.mark.asyncio
async def test_cod_account_excluded_by_english_name(db_client):
    uid = f"test-iter179-{uuid.uuid4()}"
    for variant in ("Cash on Delivery", "COD", "cash_on_delivery"):
        await db_client.accounts.insert_one({
            "id": str(uuid.uuid4()), "user_id": uid,
            "account_type": "payment_platform",
            "name": variant, "current_balance": 100.0,
        })
    rows = await _legacy_payment_platform_balances(db_client, uid)
    assert rows == [], f"English COD leaked: {rows}"
    await _cleanup(db_client, uid)


@pytest.mark.asyncio
async def test_cod_excluded_by_normalized_payment_method(db_client):
    """Even if the name is something weird, if
    `normalized_payment_method=cod` is set, exclude it."""
    uid = f"test-iter179-{uuid.uuid4()}"
    await db_client.accounts.insert_one({
        "id": str(uuid.uuid4()), "user_id": uid,
        "account_type": "payment_platform",
        "name": "Some Strange Name",
        "normalized_payment_method": "cod",
        "current_balance": 50.0,
    })
    rows = await _legacy_payment_platform_balances(db_client, uid)
    assert rows == [], f"normalized-cod leaked: {rows}"
    await _cleanup(db_client, uid)


@pytest.mark.asyncio
async def test_non_cod_platforms_still_migrate(db_client):
    """Tabby, Tamara, Salla, Imkan etc. continue to migrate
    normally. Only COD is excluded."""
    uid = f"test-iter179-{uuid.uuid4()}"
    await db_client.accounts.insert_many([
        {"id": str(uuid.uuid4()), "user_id": uid,
         "account_type": "payment_platform",
         "name": "تمارا", "current_balance": -100.0},
        {"id": str(uuid.uuid4()), "user_id": uid,
         "account_type": "payment_platform",
         "name": "تابي", "current_balance": -50.0},
        {"id": str(uuid.uuid4()), "user_id": uid,
         "account_type": "payment_platform",
         "name": "سلة", "current_balance": 5000.0},
        {"id": str(uuid.uuid4()), "user_id": uid,
         "account_type": "payment_platform",
         "name": "إمكان", "current_balance": 200.0},
    ])
    rows = await _legacy_payment_platform_balances(db_client, uid)
    names = sorted(r["name"] for r in rows)
    assert names == sorted(["تمارا", "تابي", "سلة", "إمكان"])
    await _cleanup(db_client, uid)
