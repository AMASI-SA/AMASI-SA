from __future__ import annotations

from datetime import date, datetime, timezone
import inspect

import pytest
from fastapi import APIRouter

from integrations_control_center import snapchat_account_hourly_refresh as hourly
from integrations_control_center import snapchat_account_hourly_chart as hourly_chart
from integrations_control_center import snapchat_account_timezone_manager as manager
from integrations_control_center.snapchat_account_timezone_retention import (
    install_snapchat_account_timezone_retention,
)
from integrations_control_center.snapchat_native_data_common import (
    SnapchatNativeSyncError,
)


def test_each_selected_account_has_its_own_local_today() -> None:
    now = datetime(2026, 8, 3, 21, 58, tzinfo=timezone.utc)

    assert manager.account_local_today(
        "Asia/Riyadh", now=now
    ) == date(2026, 8, 4)
    assert manager.account_local_today(
        "America/Los_Angeles", now=now
    ) == date(2026, 8, 3)


def test_default_report_range_is_today_in_account_timezone() -> None:
    now = datetime(2026, 8, 3, 21, 58, tzinfo=timezone.utc)

    riyadh = manager.resolve_account_report_dates(
        None,
        None,
        timezone_name="Asia/Riyadh",
        now=now,
    )
    los_angeles = manager.resolve_account_report_dates(
        None,
        None,
        timezone_name="America/Los_Angeles",
        now=now,
    )

    assert riyadh == [date(2026, 8, 4)]
    assert los_angeles == [date(2026, 8, 3)]


def _complete_fetch_result(
    *,
    data_state: str = "confirmed_no_data",
) -> hourly.AccountHourFetchResult:
    metrics = {key: 0 for key in hourly.STAT_FIELDS}
    if data_state == "confirmed_data":
        metrics["spend"] = 1_000_000
    rows = [] if data_state == "confirmed_no_data" else [{
        "campaign_id": "campaign-contract",
        "start_time": "2026-08-07T00:00:00+03:00",
        "end_time": "2026-08-07T01:00:00+03:00",
        "metrics": metrics,
    }]
    return hourly.AccountHourFetchResult(
        rows=rows,
        errors=[],
        coverage={
            "status": "complete",
            "data_state": data_state,
            "expected_requests": 1,
            "completed_requests": 1,
        },
    )


@pytest.mark.asyncio
async def test_installed_capture_and_timezone_consumer_preserve_all_coverages(
    monkeypatch,
) -> None:
    results = [
        _complete_fetch_result(data_state="confirmed_data"),
        _complete_fetch_result(data_state="confirmed_zero"),
        _complete_fetch_result(data_state="confirmed_no_data"),
    ]
    calls = []
    accounting_writes = []

    async def base_fetch(*args, **kwargs):
        calls.append(dict(kwargs))
        return results[len(calls) - 1]

    async def base_refresh(*args, **kwargs):
        return {}

    async def noop(*args, **kwargs):
        return None

    async def capture_accounting_write(*args, **kwargs):
        accounting_writes.append(dict(kwargs))

    monkeypatch.setattr(hourly, "_fetch_account_hours", base_fetch)
    monkeypatch.setattr(hourly, "refresh_snapchat_account_hours", base_refresh)
    hourly_chart.install_snapchat_account_hourly_capture()
    monkeypatch.setattr(manager, "_ensure_account_local_indexes", noop)
    monkeypatch.setattr(
        manager,
        "_upsert_performance",
        capture_accounting_write,
    )
    monkeypatch.setattr(manager, "_upsert_account_local_performance", noop)

    class Context:
        db = object()
        provider_calls = 3

    result = await manager.refresh_snapchat_account_hours_with_account_days(
        Context(),
        object(),
        "access-token",
        {
            "ad_account_id": "account-contract",
            "timezone": "Asia/Riyadh",
            "currency": "SAR",
        },
        start_date=date(2026, 8, 7),
        end_date=date(2026, 8, 7),
        now=datetime(2026, 8, 9, 12, 0, tzinfo=timezone.utc),
    )

    assert len(calls) == 3
    assert result["coverage"] == {
        "status": "complete",
        "data_state": "confirmed_data",
        "expected_requests": 3,
        "completed_requests": 3,
    }
    account_fact = next(
        write
        for write in accounting_writes
        if write["entity_type"] == "ad_account"
    )
    assert account_fact["external_id"] == "account-contract"
    assert account_fact["date_string"] == "2026-08-07"
    assert account_fact["metrics"]["spend"] == 1_000_000


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("invalid_index", "result_name"),
    [
        (0, "business_result"),
        (1, "conversion_result"),
        (2, "impression_result"),
    ],
)
@pytest.mark.parametrize(
    "invalid_kind",
    [
        "legacy_tuple",
        "incomplete_coverage",
        "zero_request_coverage",
        "string_request_coverage",
    ],
)
async def test_each_hourly_mode_rejects_invalid_result_before_fact_write(
    monkeypatch,
    invalid_index,
    result_name,
    invalid_kind,
) -> None:
    call_index = 0
    writes = []

    async def fake_fetch(*args, **kwargs):
        nonlocal call_index
        current = call_index
        call_index += 1
        if current == invalid_index:
            if invalid_kind == "legacy_tuple":
                return [], []
            expected_requests: object = 1
            completed_requests: object = 0
            status = "incomplete"
            if invalid_kind == "zero_request_coverage":
                expected_requests = completed_requests = 0
                status = "complete"
            elif invalid_kind == "string_request_coverage":
                expected_requests = completed_requests = "1"
                status = "complete"
            return hourly.AccountHourFetchResult(
                rows=[],
                errors=[],
                coverage={
                    "status": status,
                    "data_state": (
                        "unknown_incomplete"
                        if status == "incomplete"
                        else "confirmed_no_data"
                    ),
                    "expected_requests": expected_requests,
                    "completed_requests": completed_requests,
                },
            )
        return _complete_fetch_result()

    async def noop(*args, **kwargs):
        return None

    async def capture_write(*args, **kwargs):
        writes.append(dict(kwargs))

    monkeypatch.setattr(hourly, "_fetch_account_hours", fake_fetch)
    monkeypatch.setattr(manager, "_ensure_account_local_indexes", noop)
    monkeypatch.setattr(manager, "_upsert_performance", capture_write)
    monkeypatch.setattr(
        manager,
        "_upsert_account_local_performance",
        capture_write,
    )

    class Context:
        db = object()
        provider_calls = 3

    with pytest.raises(SnapchatNativeSyncError) as raised:
        await manager.refresh_snapchat_account_hours_with_account_days(
            Context(),
            object(),
            "access-token",
            {
                "ad_account_id": "account-contract",
                "timezone": "Asia/Riyadh",
                "currency": "SAR",
            },
            start_date=date(2026, 8, 7),
            end_date=date(2026, 8, 7),
            now=datetime(2026, 8, 9, 12, 0, tzinfo=timezone.utc),
        )

    assert raised.value.code == "snapchat_account_hour_result_contract_invalid"
    assert raised.value.result == {
        "contract_valid": False,
        "result_name": result_name,
    }
    assert "coverage" not in raised.value.result
    assert writes == []


def test_same_hour_is_stored_under_each_calendar_semantics() -> None:
    rows = [
        {
            "campaign_id": "campaign-1",
            "start_time": "2026-08-03T22:00:00+00:00",
            "end_time": "2026-08-03T23:00:00+00:00",
            "metrics": {
                "impressions": 100,
                "swipes": 5,
                "spend": 1_000_000,
                "video_views": 70,
                "view_completion": 20,
                "conversion_purchases": 1,
                "conversion_purchases_value": 5_000_000,
            },
        }
    ]

    riyadh_campaigns, riyadh_accounts = manager._campaign_day_buckets(
        rows,
        timezone_name="Asia/Riyadh",
        start_date=date(2026, 8, 4),
        end_date=date(2026, 8, 4),
    )
    la_campaigns, la_accounts = manager._campaign_day_buckets(
        rows,
        timezone_name="America/Los_Angeles",
        start_date=date(2026, 8, 3),
        end_date=date(2026, 8, 3),
    )

    assert ("campaign-1", "2026-08-04") in riyadh_campaigns
    assert "2026-08-04" in riyadh_accounts
    assert ("campaign-1", "2026-08-03") in la_campaigns
    assert "2026-08-03" in la_accounts


def test_account_local_projection_keeps_seven_days_without_widening_riyadh() -> None:
    install_snapchat_account_timezone_retention()

    local_from, local_to = manager._local_sync_bounds(
        date(2026, 8, 3),
        date(2026, 8, 4),
    )

    assert local_from == date(2026, 7, 29)
    assert local_to == date(2026, 8, 5)
    assert manager.SNAPCHAT_ACCOUNT_LOCAL_PERFORMANCE_COLLECTION != (
        "mezan_snapchat_performance_daily_v2"
    )


def test_scheduler_installer_keeps_dashboard_collection_and_adds_campaign_rows() -> None:
    original = hourly.refresh_snapchat_account_hours
    try:
        manager.install_snapchat_account_timezone_scheduler()
        assert hourly.refresh_snapchat_account_hours is (
            manager.refresh_snapchat_account_hours_with_account_days
        )
        source = inspect.getsource(
            manager.refresh_snapchat_account_hours_with_account_days
        )
        assert 'entity_type="campaign"' in source
        assert "_upsert_account_local_performance" in source
        assert manager.SNAPCHAT_ACCOUNT_LOCAL_PERFORMANCE_COLLECTION == (
            "mezan_snapchat_performance_account_day_v3"
        )
    finally:
        hourly.refresh_snapchat_account_hours = original


def test_account_timezone_report_route_accepts_account_id() -> None:
    router = APIRouter(prefix="/integrations-v2")

    async def current_user():
        return {"id": "owner-1", "role": "owner"}

    manager.attach_snapchat_account_timezone_campaign_routes(
        router,
        object(),
        current_user,
        lambda user: user,
    )

    route = next(
        item
        for item in router.routes
        if item.path == "/integrations-v2/snapchat_ads/campaign-report"
    )
    assert "GET" in route.methods
    assert "account_id" in inspect.signature(route.endpoint).parameters


def test_account_timezone_report_is_explicitly_read_only() -> None:
    source = inspect.getsource(manager.build_account_timezone_campaign_report)

    assert "provider_read_reached" in source
    assert "provider_write_reached" in source
    assert "accounting_write_reached" in source
    assert "qoyod_write_reached" in source
    assert "dashboard_accounting_timezone_unchanged" in source

@pytest.mark.asyncio
async def test_current_hour_400_retries_all_modes_with_completed_hour(
    monkeypatch,
) -> None:
    live_end = datetime(2026, 8, 7, 20, 0, tzinfo=timezone.utc)
    completed_end = datetime(2026, 8, 7, 19, 0, tzinfo=timezone.utc)
    window_calls = []

    def fake_window(
        start_date,
        end_date,
        *,
        timezone_name,
        now,
        include_current_hour,
    ):
        window_calls.append(include_current_hour)
        request_end = live_end if include_current_hour else completed_end
        return {
            "business_start": datetime(
                2026, 8, 7, 0, 0, tzinfo=timezone.utc
            ),
            "business_end": request_end,
            "provider_start": datetime(
                2026, 8, 7, 0, 0, tzinfo=timezone.utc
            ),
            "provider_end": request_end,
            "account_local_from": date(2026, 8, 7),
            "account_local_to": date(2026, 8, 7),
        }

    fetch_calls = []

    async def fake_fetch(*args, **kwargs):
        fetch_calls.append(dict(kwargs))
        if len(fetch_calls) == 1:
            raise SnapchatNativeSyncError(
                "snapchat_provider_http_400",
                "Open HOUR window rejected.",
                status_code=400,
                retryable=False,
            )
        return hourly.AccountHourFetchResult(
            rows=[],
            errors=[],
            coverage={
                "status": "complete",
                "data_state": "confirmed_no_data",
                "expected_requests": 1,
                "completed_requests": 1,
            },
        )

    async def noop(*args, **kwargs):
        return None

    monkeypatch.setattr(manager, "_combined_request_window", fake_window)
    monkeypatch.setattr(hourly, "_fetch_account_hours", fake_fetch)
    monkeypatch.setattr(manager, "_ensure_account_local_indexes", noop)
    monkeypatch.setattr(manager, "_upsert_account_local_performance", noop)
    monkeypatch.setattr(manager, "_upsert_performance", noop)

    class Context:
        db = object()
        provider_calls = 0

    result = await manager.refresh_snapchat_account_hours_with_account_days(
        Context(),
        object(),
        "access-token",
        {
            "ad_account_id": "account-1",
            "timezone": "America/Los_Angeles",
            "currency": "USD",
        },
        start_date=date(2026, 8, 7),
        end_date=date(2026, 8, 7),
        now=datetime(2026, 8, 7, 19, 30, tzinfo=timezone.utc),
    )

    assert window_calls == [True, False]
    assert len(fetch_calls) == 4
    assert [
        call["action_report_time"] for call in fetch_calls[1:]
    ] == ["conversion", "conversion", "impression"]
    assert all(
        call["request_end"] == completed_end
        for call in fetch_calls[1:]
    )
    assert result["current_hour_included"] is False
    assert result["errors_count"] == 0
    assert result["coverage"] == {
        "status": "complete",
        "data_state": "confirmed_no_data",
        "expected_requests": 3,
        "completed_requests": 3,
    }


@pytest.mark.asyncio
async def test_missing_business_day_is_not_materialized_as_zero(monkeypatch) -> None:
    row = {
        "campaign_id": "campaign-1",
        "start_time": "2026-08-07T00:00:00+03:00",
        "end_time": "2026-08-07T01:00:00+03:00",
        "metrics": {key: 0 for key in hourly.STAT_FIELDS},
    }

    async def fake_fetch(*args, **kwargs):
        return hourly.AccountHourFetchResult(
            rows=[row],
            errors=[],
            coverage={
                "status": "complete",
                "data_state": "confirmed_zero",
                "expected_requests": 1,
                "completed_requests": 1,
            },
        )

    writes = []

    async def capture_write(*args, **kwargs):
        writes.append(dict(kwargs))

    async def noop(*args, **kwargs):
        return None

    monkeypatch.setattr(hourly, "_fetch_account_hours", fake_fetch)
    monkeypatch.setattr(manager, "_ensure_account_local_indexes", noop)
    monkeypatch.setattr(manager, "_upsert_performance", capture_write)
    monkeypatch.setattr(manager, "_upsert_account_local_performance", capture_write)

    class Context:
        db = object()
        provider_calls = 3

    result = await manager.refresh_snapchat_account_hours_with_account_days(
        Context(),
        object(),
        "access-token",
        {
            "ad_account_id": "account-1",
            "timezone": "Asia/Riyadh",
            "currency": "SAR",
        },
        start_date=date(2026, 8, 7),
        end_date=date(2026, 8, 8),
        now=datetime(2026, 8, 9, 12, 0, tzinfo=timezone.utc),
    )

    assert result["coverage"]["data_state"] == "confirmed_zero"
    assert writes
    assert {write["date_string"] for write in writes} == {"2026-08-07"}

