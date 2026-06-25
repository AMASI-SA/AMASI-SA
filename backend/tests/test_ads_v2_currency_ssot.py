"""iter-258 — Currency SSOT regression.

Bug: report layer was reading `currency_native` from `ads_daily`
(written by the platform adapter — Snap always writes USD), instead of
from `ads_accounts` (user-configured billing currency). This caused a
SAR-billed account to appear as USD in the report.

Fix: `get_spend_by_account` and `get_spend_by_provider` now join with
`ads_accounts.currency_native` as the single source of truth. For
SAR-billed accounts the `spend_native` field is set to `null` and the
account is excluded from `spend_native_by_currency` totals.
"""
import os
import asyncio
import uuid
import pytest
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")

from motor.motor_asyncio import AsyncIOMotorClient
from ads_v2.data_layer.reports import (
    get_spend_by_account,
    get_spend_by_provider,
)


@pytest.fixture
def db():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    return client[os.environ["DB_NAME"]]


@pytest.mark.asyncio
async def test_sar_billed_account_hides_usd_in_report(db):
    """An SAR-billed Snap account must not show USD in the report,
    even though ads_daily.currency_native happens to be 'USD'."""
    user_id = f"test_iter258_{uuid.uuid4().hex[:8]}"
    acct_id = f"acct_{uuid.uuid4().hex[:8]}"
    try:
        # ads_accounts SAYS this account is SAR-billed.
        await db.ads_accounts.insert_one({
            "id": acct_id, "user_id": user_id, "provider": "snapchat",
            "display_name": "متجر أماسي سعودي",
            "external_account_id": "cf8ea7c9-test",
            "currency_native": "SAR",      # ← SSOT
            "bank_fee": {"enabled": True, "method": "pct", "rate_pct": 0.023},
        })
        # ads_daily, however, was written by the Snap adapter which always
        # records 'USD' (since Snap API reports USD micros). The report
        # MUST ignore this and trust ads_accounts.
        await db.ads_daily.insert_one({
            "user_id": user_id, "account_id": acct_id,
            "provider": "snapchat", "date": "2026-06-24",
            "spend_native": 721.61, "currency_native": "USD",  # noise
            "spend_sar": 2706.04, "bank_fee_sar": 62.24,
            "gross_sar": 2768.28, "fx_rate": 3.75,
        })

        r = await get_spend_by_account(
            db, user_id, "2026-06-01", "2026-06-25")
        rows = r["data"]
        assert len(rows) == 1
        row = rows[0]

        # Currency from ads_accounts (SSOT), NOT ads_daily.
        assert row["currency_native"] == "SAR", \
            f"Expected SAR (from ads_accounts), got {row['currency_native']}"

        # Native spend hidden for SAR-billed accounts.
        assert row["spend_native"] is None, \
            f"spend_native must be null for SAR account, got {row['spend_native']}"

        # SAR figures preserved exactly.
        assert row["spend_sar"]    == 2706.04
        assert row["bank_fee_sar"] == 62.24
        assert row["gross_sar"]    == 2768.28

        # USD totals MUST NOT include this account.
        assert r["totals"]["spend_native_by_currency"] == {}, \
            f"USD totals must exclude SAR-billed accounts; got {r['totals']['spend_native_by_currency']}"

        # Provider report should also exclude this from USD totals.
        rp = await get_spend_by_provider(
            db, user_id, "2026-06-01", "2026-06-25")
        prov_rows = rp["data"]
        assert len(prov_rows) == 1
        snap = prov_rows[0]
        assert snap["provider"] == "snapchat"
        assert snap["spend_native_by_currency"] == [], \
            f"Provider USD must be empty (only SAR-billed accts present); got {snap['spend_native_by_currency']}"
        assert snap["spend_sar"] == 2706.04
    finally:
        await db.ads_daily.delete_many({"user_id": user_id})
        await db.ads_accounts.delete_many({"user_id": user_id})


@pytest.mark.asyncio
async def test_usd_billed_account_shows_usd(db):
    """A truly USD-billed account must still surface USD."""
    user_id = f"test_iter258_{uuid.uuid4().hex[:8]}"
    acct_id = f"acct_{uuid.uuid4().hex[:8]}"
    try:
        await db.ads_accounts.insert_one({
            "id": acct_id, "user_id": user_id, "provider": "snapchat",
            "display_name": "Self Service USD",
            "external_account_id": "efcdd251-test",
            "currency_native": "USD",     # ← user marks this one as USD
            "bank_fee": {"enabled": True, "method": "pct", "rate_pct": 0.023},
        })
        await db.ads_daily.insert_one({
            "user_id": user_id, "account_id": acct_id,
            "provider": "snapchat", "date": "2026-06-24",
            "spend_native": 105.41, "currency_native": "USD",
            "spend_sar": 395.76, "bank_fee_sar": 9.10,
            "gross_sar": 404.86, "fx_rate": 3.7549,
        })

        r = await get_spend_by_account(
            db, user_id, "2026-06-01", "2026-06-25")
        row = r["data"][0]
        assert row["currency_native"] == "USD"
        assert row["spend_native"] == 105.41
        assert r["totals"]["spend_native_by_currency"] == {"USD": 105.41}
    finally:
        await db.ads_daily.delete_many({"user_id": user_id})
        await db.ads_accounts.delete_many({"user_id": user_id})


@pytest.mark.asyncio
async def test_mixed_sar_and_usd_accounts_isolated(db):
    """Two accounts, one SAR, one USD — totals must isolate cleanly."""
    user_id = f"test_iter258_{uuid.uuid4().hex[:8]}"
    sar_id = f"acct_sar_{uuid.uuid4().hex[:8]}"
    usd_id = f"acct_usd_{uuid.uuid4().hex[:8]}"
    try:
        await db.ads_accounts.insert_many([
            {"id": sar_id, "user_id": user_id, "provider": "snapchat",
             "display_name": "SAR Acct", "currency_native": "SAR",
             "bank_fee": {"enabled": False}},
            {"id": usd_id, "user_id": user_id, "provider": "snapchat",
             "display_name": "USD Acct", "currency_native": "USD",
             "bank_fee": {"enabled": False}},
        ])
        await db.ads_daily.insert_many([
            {"user_id": user_id, "account_id": sar_id,
             "provider": "snapchat", "date": "2026-06-24",
             "idempotency_key": f"k_{sar_id}_1",
             "spend_native": 200.0, "currency_native": "USD",  # noise
             "spend_sar": 750.0, "bank_fee_sar": 0, "gross_sar": 750.0},
            {"user_id": user_id, "account_id": usd_id,
             "provider": "snapchat", "date": "2026-06-24",
             "idempotency_key": f"k_{usd_id}_1",
             "spend_native": 50.0, "currency_native": "USD",
             "spend_sar": 187.5, "bank_fee_sar": 0, "gross_sar": 187.5},
        ])

        r = await get_spend_by_account(
            db, user_id, "2026-06-01", "2026-06-25")
        rows = {row["account_id"]: row for row in r["data"]}
        assert rows[sar_id]["spend_native"] is None
        assert rows[sar_id]["currency_native"] == "SAR"
        assert rows[usd_id]["spend_native"] == 50.0
        assert rows[usd_id]["currency_native"] == "USD"
        # Only the USD-billed account contributes to USD totals.
        assert r["totals"]["spend_native_by_currency"] == {"USD": 50.0}
    finally:
        await db.ads_daily.delete_many({"user_id": user_id})
        await db.ads_accounts.delete_many({"user_id": user_id})


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
