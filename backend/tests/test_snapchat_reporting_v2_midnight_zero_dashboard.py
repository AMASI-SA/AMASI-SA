"""Known account spend stays visible without converting an empty account to 0."""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from ads_manager.account_cost_settings import COLLECTION as COST_SETTINGS
from integrations_control_center import dashboard_ads_platform_spend_routes as routes
from snapchat_v2.accounts import SNAPCHAT_ACCOUNTS_COLLECTION
from snapchat_v2.projections import SNAPCHAT_DAILY_PROJECTIONS_COLLECTION
from snapchat_v2.reconciliation import SNAPCHAT_RECONCILIATION_COLLECTION
from snapchat_v2.sync_runs import SNAPCHAT_SYNC_RUNS_COLLECTION
from tests.test_snapchat_reporting_v2_refresh_visibility import Database
from unified_marketing.readers import snapchat_v2 as reader
from unified_marketing.readers.dashboard_portfolio import combine_dashboard_spend


DAY = date(2026, 10, 26)
NOW = datetime(2026, 10, 25, 21, 40, tzinfo=timezone.utc)


@pytest.fixture
def midnight(monkeypatch):
    db = Database()
    monkeypatch.setattr(reader, "_today_in_timezone", lambda _: DAY)
    for owner, key, currency, zone in [
        ("owner", "usd", "USD", "America/Los_Angeles"),
        ("owner", "sar", "SAR", "Asia/Riyadh"),
        ("foreign-owner", "usd", "USD", "America/Los_Angeles"),
    ]:
        account_id, run_id = key + "-account", key + "-run"
        account = {"user_id": owner, "provider": "snapchat_ads", "ad_account_id": account_id,
            "external_account_id": account_id, "currency": currency, "timezone": zone,
            "connection_status": "connected", "connection_provenance": "api_connection",
            "mezan_selected": True, "mezan_integration_account_id": "integration-" + key}
        db.raw.mezan_integration_accounts_v2.insert_one(deepcopy(account))
        db.raw[SNAPCHAT_ACCOUNTS_COLLECTION].insert_one({**account, "active": True, "selected": key == "usd"})
        db.raw[COST_SETTINGS].insert_one({"user_id": owner, "provider": "snapchat_ads",
            "external_account_id": account_id, "mezan_integration_account_id": "integration-" + key,
            "native_currency": currency, "exchange_rate_to_sar": 3.75 if key == "usd" else 1,
            "bank_commission_pct": 0, "apply_bank_commission": False})
        db.raw[SNAPCHAT_SYNC_RUNS_COLLECTION].insert_one({"user_id": owner, "provider": "snapchat_ads",
            "ad_account_id": account_id, "sync_run_id": run_id, "status": "complete",
            "financial_sync_status": "complete", "request_window": {"date_from": DAY.isoformat(),
                "date_to": DAY.isoformat(), "action_report_time": "conversion"}})
        observed = key == "usd"
        amount = 1000 if owner == "foreign-owner" else 21.685333 if observed else 0
        hours = [{"sequence": index, "local_hour": f"{index:02}:00",
                  "spend_native": amount if observed and index == 0 else None,
                  "status": "provisional" if observed and index == 0 else
                            "confirmed_no_data" if index == 0 else "future"}
                 for index in range(24)]
        db.raw[SNAPCHAT_DAILY_PROJECTIONS_COLLECTION].insert_one({**account,
            "report_date": DAY.isoformat(), "projection_timezone": "Asia/Riyadh", "account_timezone": zone,
            "action_report_time": "conversion", "sync_run_id": run_id,
            "base_spend_native": amount, "amount_complete": True,
            "source_fact_count": 1 if observed else 0,
            "source_sync_run_ids": [run_id] if observed else [],
            "data_state": "confirmed_data" if observed else "confirmed_no_data",
            "coverage": {"status": "complete", "data_state": "confirmed_data" if observed else "confirmed_no_data",
                "expected_requests": 1, "completed_requests": 1, "rows_received": 2 if observed else 0},
            "source_close_proof": {"complete": False}, "hours": hours,
            "generated_at": NOW - timedelta(seconds=2)})
        if not observed:
            # The persisted fallback is a normalized zero, not proof of an
            # explicit source metric row. It must not rescue empty HOUR facts.
            db.raw[SNAPCHAT_RECONCILIATION_COLLECTION].insert_one({"user_id": owner,
                "provider": "snapchat_ads", "ad_account_id": account_id, "report_date": DAY.isoformat(),
                "currency": currency, "account_timezone": zone, "dashboard_timezone": "Asia/Riyadh",
                "action_report_time": "conversion", "sync_run_id": run_id, "reconciled": True,
                "projection_source_match": True, "provider_spend_native": 0,
                "provider_riyadh_window_spend_native": 0, "checked_at": NOW - timedelta(seconds=1),
                "dashboard_provider_coverage": {"status": "complete", "data_state": "confirmed_zero",
                    "expected_requests": 1, "completed_requests": 1, "rows_received": 1,
                    "provider_granularity": "HOUR", "fallback_from": "snapchat_provider_http_400"}})
    return db


async def dashboard(db):
    return await reader.load_snapchat_v2_dashboard_spend(
        db, "owner", date_from=DAY, date_to=DAY, timezone_name="Asia/Riyadh")


@pytest.mark.asyncio
async def test_actual_reader_keeps_usd_subtotal_and_empty_sar_unknown_at_riyadh_midnight(midnight):
    db = midnight
    before = list(db.raw[SNAPCHAT_DAILY_PROJECTIONS_COLLECTION].find())
    result = await dashboard(db)
    assert result["total_sar"] is None
    assert result["known_total_sar"] == 81.32
    assert result["known_account_count"] == 1 and result["incomplete_account_count"] == 1
    assert {account["account_id"] for account in result["accounts"]} == {"usd-account", "sar-account"}
    assert result["quality"]["amount_available"] is False and result["quality"]["complete"] is False
    assert result["quality"]["amount_complete"] is False
    assert result["rows"] == [] and result["hourly_sar"][DAY.isoformat()][0]["spend_sar"] is None
    empty = next(account for account in result["accounts"] if account["account_id"] == "sar-account")
    assert empty["total_sar"] is None and empty["quality"]["financial_status"] == "partial"
    assert empty["quality"]["amount_available"] is False
    assert list(db.raw[SNAPCHAT_DAILY_PROJECTIONS_COLLECTION].find()) == before
    assert result["provider_write_reached"] is False


@pytest.mark.asyncio
async def test_no_accounts_with_available_financial_evidence_has_no_known_subtotal(midnight):
    midnight.raw[SNAPCHAT_SYNC_RUNS_COLLECTION].update_one(
        {"user_id": "owner", "ad_account_id": "usd-account"}, {"$set": {"financial_sync_status": "partial"}})
    result = await dashboard(midnight)
    assert result["total_sar"] is None and result["known_total_sar"] is None
    assert result["known_account_count"] == 0 and result["incomplete_account_count"] == 2
    assert result["quality"]["amount_available"] is False


@pytest.mark.asyncio
async def test_missing_fx_does_not_turn_observed_native_spend_into_a_known_sar_amount(midnight, monkeypatch):
    monkeypatch.setattr(reader, "calculate_cost_components", AsyncMock(side_effect=ValueError("synthetic_fx_unavailable")))
    result = await dashboard(midnight)
    assert result["total_sar"] is None and result["known_total_sar"] is None
    assert result["known_account_count"] == 0 and result["incomplete_account_count"] == 2
    assert result["hourly_sar"][DAY.isoformat()][0]["spend_sar"] is None


@pytest.mark.asyncio
async def test_closed_day_financial_gates_do_not_gain_availability(midnight, monkeypatch):
    monkeypatch.setattr(reader, "_today_in_timezone", lambda _: DAY + timedelta(days=1))
    result = await dashboard(midnight)
    assert result["total_sar"] is None and result["known_total_sar"] is None
    assert result["quality"]["amount_complete"] is False and result["quality"]["amount_available"] is False


def report(amount, *, available=True, complete=True):
    return {"total_sar": amount, "daily_sar": {DAY.isoformat(): amount}, "hourly_sar": {},
            "quality": {"amount_available": available, "complete": complete, "coverage_complete": complete}}


@pytest.mark.parametrize("reports,known,count,incomplete,total", [
    ([report(81.32), report(5)], 86.32, 2, 0, 86.32),
    ([report(0), report(None, available=False, complete=False)], 0, 1, 1, None),
    ([report(None, available=False), report(99, available=False)], None, 0, 2, None),
    ([], None, 0, 0, None),
])
def test_known_counts_preserve_healthy_explicit_zero_and_no_known_accounts(reports, known, count, incomplete, total):
    result = combine_dashboard_spend(reports, [], DAY, DAY, "Asia/Riyadh")
    assert result["known_total_sar"] == known and result["known_account_count"] == count
    assert result["incomplete_account_count"] == incomplete and result["total_sar"] == total


@pytest.mark.parametrize("invalid", [None, True, False, -1, float("nan"), float("inf"), "81.32",
                                    pytest.param(10 ** 1000, id="overflowing-integer")])
def test_invalid_or_unavailable_account_amount_never_enters_known_subtotal(invalid):
    invalid_report = report(invalid)
    # A malformed headline has no trusted daily series. Keep this regression
    # on the new subtotal boundary rather than legacy daily-number parsing.
    invalid_report["daily_sar"] = {}
    reports = [report(81.32), invalid_report, report(900, available=False, complete=False)]
    result = combine_dashboard_spend(reports, [], DAY, DAY, "Asia/Riyadh")
    assert result["known_total_sar"] == 81.32
    assert result["known_account_count"] == 1 and result["incomplete_account_count"] == 2
    assert result["total_sar"] is None and result["quality"]["amount_available"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("full_snapchat_available", [False, True])
async def test_four_platform_route_forwards_partial_and_counts_snapchat_once(monkeypatch, full_snapchat_available):
    snap = {"total_sar": 81.32 if full_snapchat_available else None, "known_total_sar": 81.32,
            "known_account_count": 2 if full_snapchat_available else 1,
            "incomplete_account_count": 0 if full_snapchat_available else 1,
            "daily_sar": {DAY.isoformat(): 81.32}, "hourly_sar": {},
            "quality": {"connected": True, "amount_available": full_snapchat_available,
                        "amount_complete": full_snapchat_available, "complete": full_snapchat_available}}

    async def daily(*args, **options):
        return [{"date": DAY.isoformat(), "snapchat": 81.32, "meta": 12.68, "tiktok": 3.95, "google": 0.44}], {
            provider: True for provider in routes.FOUR_PLATFORM_KEYS}

    async def hourly(*args, **options):
        return [], {provider: False for provider in routes.FOUR_PLATFORM_KEYS}, {
            provider: "" for provider in routes.FOUR_PLATFORM_KEYS}

    monkeypatch.setattr(routes, "load_unified_marketing_dashboard_spend", AsyncMock(return_value=snap))
    monkeypatch.setattr(routes, "load_dashboard_unified_shadow", AsyncMock(return_value={}))
    monkeypatch.setattr(routes, "_daily_spend", daily)
    monkeypatch.setattr(routes, "_hourly_spend", hourly)
    monkeypatch.setattr(routes, "_connection_states", AsyncMock(return_value={}))
    result = await routes.build_dashboard_platform_spend(object(), "owner", date_from=DAY.isoformat(), date_to=DAY.isoformat())
    provider = result["providers"]["snapchat"]
    assert provider["known_total_sar"] == 81.32
    assert provider["known_account_count"] == snap["known_account_count"]
    assert provider["incomplete_account_count"] == snap["incomplete_account_count"]
    assert provider["total_sar"] == snap["total_sar"] and provider["amount_available"] is full_snapchat_available
    assert result["provider_totals_sar"]["snapchat"] == snap["total_sar"]
    assert result["known_total_sar"] == 98.39 and result["spend_quality"]["known_total_sar"] == 98.39
    assert result["total_sar"] == (98.39 if full_snapchat_available else None)
    assert result["spend_quality"]["amount_available"] is full_snapchat_available
