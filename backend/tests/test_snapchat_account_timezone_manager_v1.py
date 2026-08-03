from __future__ import annotations

from datetime import date, datetime, timezone
import inspect

from fastapi import APIRouter

from integrations_control_center import snapchat_account_hourly_refresh as hourly
from integrations_control_center import snapchat_account_timezone_manager as manager
from integrations_control_center.snapchat_account_timezone_retention import (
    install_snapchat_account_timezone_retention,
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
        assert "SNAPCHAT_ACCOUNT_LOCAL_PERFORMANCE_COLLECTION" in source
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
