"""The existing TikTok scheduler saves dashboard hours without losing daily facts."""
from copy import deepcopy
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from integrations_control_center import ads_auto_sync_scheduler as scheduler
from integrations_control_center import dashboard_ads_platform_refresh as refresh
from integrations_control_center import dashboard_ads_platform_spend_routes as display
from integrations_control_center import tiktok_native_hierarchy
from tests.test_ads_auto_sync_scheduler_v2 import _DB


DAY = date(2026, 11, 15)
NOW = datetime(2026, 11, 15, 10, tzinfo=timezone.utc)
OWNER = "hourly-owner"


@pytest.fixture
def saved_daily(monkeypatch):
    async def no_network(*_args, **_kwargs):
        raise AssertionError("A scheduler test must not call a live provider")

    monkeypatch.setattr(httpx.AsyncClient, "request", no_network)
    monkeypatch.setattr(scheduler, "tiktok_oauth_configured", lambda: True)
    monkeypatch.setattr(scheduler, "tiktok_reporting_enabled", lambda: True)
    monkeypatch.setattr(tiktok_native_hierarchy, "hierarchy_refresh_due", AsyncMock(return_value=False))
    daily = {
        "status": "complete", "date_from": "2026-11-14", "date_to": DAY.isoformat(),
        "accounts_attempted": 1, "accounts_complete": 1, "rows_saved": 2,
        "errors_count": 0, "provider_calls": 2,
        "items": [{"ad_account_id": "account", "timezone": "Asia/Riyadh"}],
        "errors": [],
    }
    mocks = SimpleNamespace(
        active=AsyncMock(return_value=None),
        start=AsyncMock(return_value="scheduled-run"),
        finish=AsyncMock(),
        daily=AsyncMock(return_value=daily),
        hourly=AsyncMock(return_value={"status": "complete", "hourly_rows_saved": 24, "errors_count": 0}),
        record=AsyncMock(return_value="error"),
        reauth=AsyncMock(),
        result=daily,
    )
    for name, mock in (("_active_run", mocks.active), ("_start_run", mocks.start),
                       ("_finish_run", mocks.finish), ("run_tiktok_reporting_sync", mocks.daily),
                       ("_refresh_tiktok_hourly", mocks.hourly), ("_record_error", mocks.record),
                       ("_mark_needs_reauth", mocks.reauth)):
        monkeypatch.setattr(scheduler, name, mock)
    return mocks


async def run(db=None, *, now=NOW):
    return await scheduler._refresh_tiktok(
        db if db is not None else object(), user_id=OWNER,
        start_date=date(2026, 11, 14), end_date=DAY, now=now,
    )


@pytest.mark.asyncio
async def test_same_run_refreshes_latest_riyadh_day_after_daily_success(saved_daily):
    result = await run(now=datetime(2026, 11, 14, 21, 30, tzinfo=timezone.utc))
    assert result["status"] == "complete"
    assert result["rows_saved"] == 2
    assert saved_daily.hourly.await_args.args[1:] == (OWNER, DAY)
    assert saved_daily.hourly.await_args.kwargs == {"account_ids": ["account"]}
    saved_daily.start.assert_awaited_once()
    saved_daily.finish.assert_awaited_once()
    payload = saved_daily.daily.await_args.args[2]
    assert (payload.from_date, payload.to_date) == ("2026-11-14", "2026-11-15")
    assert result["hourly"] == {
        "status": "complete", "report_date": DAY.isoformat(), "timezone": "Asia/Riyadh",
        "source_dates": [DAY.isoformat()], "hourly_rows_saved": 24, "errors_count": 0,
    }


@pytest.mark.parametrize(("timezones", "now", "expected"), [
    (["Asia/Riyadh"], "2026-11-15T10:00:00+00:00", ["2026-11-15"]),
    (["UTC"], "2026-11-15T10:00:00+00:00", ["2026-11-14", "2026-11-15"]),
    (["America/Los_Angeles"], "2026-11-15T07:59:59+00:00", ["2026-11-14"]),
    (["America/Los_Angeles", "Asia/Riyadh"], "2026-11-15T10:00:00+00:00", ["2026-11-14", "2026-11-15"]),
    (["Pacific/Kiritimati"], "2026-11-15T11:00:00+00:00", ["2026-11-15", "2026-11-16"]),
    (["Asia/Riyadh"], "2026-11-14T21:00:00+00:00", []),
])
def test_hourly_source_days_cover_only_elapsed_riyadh_time(timezones, now, expected):
    result = {"items": [{"ad_account_id": f"account-{index}", "timezone": name}
                        for index, name in enumerate(timezones)]}
    assert [day.isoformat() for day in scheduler._tiktok_hourly_account_days(
        result, DAY, datetime.fromisoformat(now),
    )] == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [
    RuntimeError("synthetic-private-response-must-not-be-stored"),
    {"status": "failed", "hourly_rows_saved": 0, "errors_count": 1,
     "error_samples": [{"ad_account_id": "account", "code": "tiktok_hourly_failed"}]},
])
async def test_hourly_failure_keeps_daily_result_and_closes_run_as_partial(saved_daily, failure):
    if isinstance(failure, Exception):
        saved_daily.hourly.side_effect = failure
    else:
        saved_daily.hourly.return_value = failure
    result = await run()
    assert result["status"] == "partial"
    assert result["rows_saved"] == 2
    assert result["errors_count"] == 1
    assert result["hourly"]["status"] == "partial"
    assert saved_daily.finish.await_args.kwargs["status"] == "partial"
    assert saved_daily.finish.await_args.kwargs["result"]["rows_saved"] == 2
    assert "synthetic-private-response" not in repr(saved_daily.finish.await_args)
    saved_daily.reauth.assert_not_awaited()


@pytest.mark.asyncio
async def test_partial_daily_result_stays_partial_after_hourly_success(saved_daily):
    saved_daily.result.update(status="partial", errors_count=1, errors=[{"code": "daily_account_failed"}])
    result = await run()
    assert result["status"] == "partial"
    assert result["hourly"]["status"] == "complete"
    assert result["error_samples"] == [{"code": "daily_account_failed"}]


@pytest.mark.asyncio
@pytest.mark.parametrize("gate", ["busy", "disabled"])
async def test_existing_busy_and_disabled_gates_precede_all_refreshes(saved_daily, monkeypatch, gate):
    if gate == "busy":
        saved_daily.active.return_value = {"run_id": "other-run"}
    else:
        monkeypatch.setattr(scheduler, "tiktok_reporting_enabled", lambda: False)
    result = await run()
    assert result["status"] == "skipped"
    assert result["reason"] == ("sync_in_progress" if gate == "busy" else "disabled")
    saved_daily.start.assert_not_awaited()
    saved_daily.daily.assert_not_awaited()
    saved_daily.hourly.assert_not_awaited()


@pytest.mark.asyncio
async def test_daily_auth_failure_preserves_existing_failure_path(saved_daily):
    saved_daily.daily.side_effect = scheduler.TikTokReportingError("tiktok_needs_reauth", "synthetic auth failure")
    result = await run()
    assert result["status"] == "failed" and result["code"] == "tiktok_needs_reauth"
    saved_daily.hourly.assert_not_awaited()
    saved_daily.record.assert_awaited_once()
    saved_daily.reauth.assert_awaited_once()


@pytest.mark.asyncio
async def test_hourly_auth_failure_marks_reauth_without_discarding_daily(saved_daily):
    saved_daily.hourly.side_effect = scheduler.TikTokReportingError("tiktok_needs_reauth", "synthetic auth failure")
    result = await run()
    assert result["status"] == "partial" and result["rows_saved"] == 2
    saved_daily.reauth.assert_awaited_once()


@pytest.mark.asyncio
async def test_real_saved_hourly_writer_feeds_riyadh_chart_with_synthetic_provider(saved_daily, monkeypatch):
    class Db(_DB):
        def __getattr__(self, name):
            return self[name]

    account = {"user_id": OWNER, "provider": "tiktok_ads", "ad_account_id": "account",
               "external_account_id": "account", "connection_status": "connected",
               "connection_provenance": "api_connection", "currency": "SAR", "timezone": "UTC"}
    db = Db({"mezan_integration_accounts_v2": [account,
             {**account, "ad_account_id": "disconnected", "connection_status": "disconnected"},
             {**account, "ad_account_id": "other-owner", "user_id": "other-owner"}]})
    saved_daily.result["items"] = [{"ad_account_id": "account", "timezone": "UTC"}]
    original_daily = deepcopy(saved_daily.result)
    request_days = []

    async def daily(db, _owner, _payload):
        await db["mezan_tiktok_performance_daily_v2"].update_one(
            {"user_id": OWNER, "ad_account_id": "account", "date": DAY.isoformat()},
            {"$set": {"spend_native": 5.0, "spend_sar": 5.0}}, upsert=True,
        )
        return deepcopy(original_daily)

    class Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, _url, *, headers, params):
            assert params["advertiser_id"] == "account"
            request_days.append(params["start_date"])
            return httpx.Response(200, json={"code": 0, "data": {"list": [{
                "dimensions": {"stat_time_hour": params["start_date"] + " 21:00:00"},
                "metrics": {"spend": "5"},
            }]}})

    monkeypatch.setattr(scheduler, "run_tiktok_reporting_sync", daily)
    monkeypatch.setattr(scheduler, "_refresh_tiktok_hourly", refresh._refresh_tiktok_hourly)
    monkeypatch.setattr(refresh, "tiktok_oauth_configured", lambda: True)
    monkeypatch.setattr(refresh.tiktok, "tiktok_reporting_enabled", lambda: True)
    monkeypatch.setattr(refresh.tiktok, "_credential", AsyncMock(return_value="synthetic-test-token"))
    monkeypatch.setattr(refresh.httpx, "AsyncClient", Client)

    result = await run(db)
    assert request_days == ["2026-11-14", "2026-11-15"]
    assert result["hourly"]["hourly_rows_saved"] == 48
    assert db.rows["mezan_tiktok_performance_daily_v2"][0]["spend_native"] == 5
    saved_hours = db.rows["mezan_ads_platform_hourly_v2"]
    assert len(saved_hours) == 48
    assert {row["ad_account_id"] for row in saved_hours} == {"account"}
    assert all(row["source_only"] and not row["accounting_write_reached"] for row in saved_hours)
    hours, facts, sources = await display._hourly_spend(
        db, OWNER, DAY, {"hourly_sar": {}}, tiktok_account_ids=["account"],
    )
    assert facts["tiktok"] is True and sources["tiktok"] == "provider_native"
    assert hours[0]["tiktok"] == 5
    assert sum(row["tiktok"] for row in hours) == 5


@pytest.mark.asyncio
async def test_mixed_timezones_never_refresh_another_accounts_future_day(saved_daily, monkeypatch):
    class Db(_DB):
        def __getattr__(self, name):
            return self[name]

    def account(identity, zone, **extra):
        return {"user_id": OWNER, "provider": "tiktok_ads", "ad_account_id": identity,
                "external_account_id": identity, "connection_status": "connected",
                "connection_provenance": "api_connection", "currency": "SAR", "timezone": zone, **extra}

    db = Db({"mezan_integration_accounts_v2": [
        account("riyadh", "Asia/Riyadh"), account("kiritimati", "Pacific/Kiritimati"),
        account("foreign", "Asia/Riyadh", user_id="another-owner"),
        account("disconnected", "Asia/Riyadh", connection_status="disconnected"),
        account("manual", "Asia/Riyadh", connection_provenance="manual"),
    ]})
    saved_daily.result["items"] = [
        {"ad_account_id": "riyadh", "timezone": "Asia/Riyadh"},
        {"ad_account_id": "kiritimati", "timezone": "Pacific/Kiritimati"},
    ]
    requests = []

    class Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, _url, *, headers, params):
            requests.append((params["advertiser_id"], params["start_date"]))
            return httpx.Response(200, json={"code": 0, "data": {"list": [{
                "dimensions": {"stat_time_hour": params["start_date"] + " 00:00:00"},
                "metrics": {"spend": "5"},
            }]}})

    monkeypatch.setattr(scheduler, "_refresh_tiktok_hourly", refresh._refresh_tiktok_hourly)
    monkeypatch.setattr(refresh, "tiktok_oauth_configured", lambda: True)
    monkeypatch.setattr(refresh.tiktok, "tiktok_reporting_enabled", lambda: True)
    monkeypatch.setattr(refresh.tiktok, "_credential", AsyncMock(return_value="synthetic-test-token"))
    monkeypatch.setattr(refresh.httpx, "AsyncClient", Client)
    result = await run(db, now=datetime(2026, 11, 15, 11, tzinfo=timezone.utc))

    assert result["status"] == "complete"
    assert set(requests) == {("riyadh", "2026-11-15"), ("kiritimati", "2026-11-15"),
                             ("kiritimati", "2026-11-16")}
    assert ("riyadh", "2026-11-16") not in requests
    saved_hours = db.rows["mezan_ads_platform_hourly_v2"]
    assert len(saved_hours) == 72
    assert all(row["date_account_local"] == "2026-11-15"
               for row in saved_hours if row["ad_account_id"] == "riyadh")

    # A requested identity never expands the canonical connected API scope.
    before = deepcopy(saved_hours)
    requests.clear()
    await refresh._refresh_tiktok_hourly(db, OWNER, DAY, account_ids=["foreign", "disconnected", "manual"])
    assert requests == []
    assert saved_hours == before

    # Default None preserves the existing manual refresh of authorized accounts.
    await refresh._refresh_tiktok_hourly(db, OWNER, DAY)
    assert set(requests) == {("riyadh", "2026-11-15"), ("kiritimati", "2026-11-15")}
