from __future__ import annotations

from datetime import date, datetime, timezone
import inspect

import pytest
from fastapi import APIRouter

from integrations_control_center import snapchat_account_hourly_refresh as hourly
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

