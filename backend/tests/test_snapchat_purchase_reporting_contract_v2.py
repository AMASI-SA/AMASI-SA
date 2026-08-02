import asyncio
from datetime import date

import httpx

from integrations_control_center.snapchat_account_hourly_refresh import (
    CONVERSION_SOURCE_TYPES,
    SWIPE_ATTRIBUTION_WINDOW,
    VIEW_ATTRIBUTION_WINDOW,
    _fetch_account_hours,
)
from integrations_control_center.snapchat_ads_manager_attribution import (
    ADS_MANAGER_ACTION_REPORT_TIME,
    ADS_MANAGER_SOURCE_MODE,
    install_snapchat_ads_manager_attribution,
)


class FakeContext:
    def __init__(self):
        self.params = None

    async def get_json(self, client, url, *, headers, params=None):
        self.params = dict(params or {})
        return {"timeseries_stats": []}


def test_scheduler_requests_ads_manager_purchases_with_explicit_attribution():
    async def run():
        install_snapchat_ads_manager_attribution()
        context = FakeContext()
        async with httpx.AsyncClient() as client:
            rows, errors = await _fetch_account_hours(
                context,
                client,
                "test-token",
                account_id="snap-account-1",
                start_date=date(2026, 8, 1),
                end_date=date(2026, 8, 1),
            )
        return context.params, rows, errors

    params, rows, errors = asyncio.run(run())

    assert rows == []
    assert errors == []
    assert params["conversion_source_types"] == CONVERSION_SOURCE_TYPES == "total"
    assert params["action_report_time"] == ADS_MANAGER_ACTION_REPORT_TIME == "impression"
    assert params["swipe_up_attribution_window"] == SWIPE_ATTRIBUTION_WINDOW == "28_DAY"
    assert params["view_attribution_window"] == VIEW_ATTRIBUTION_WINDOW == "1_DAY"
    assert "conversion_purchases" in params["fields"]
    assert "conversion_purchases_value" in params["fields"]


def test_installer_keeps_request_storage_and_response_metadata_consistent():
    from integrations_control_center import snapchat_account_hourly_refresh
    from integrations_control_center import snapchat_dashboard_summary_routes
    from integrations_control_center import snapchat_native_performance_sync

    install_snapchat_ads_manager_attribution()

    assert snapchat_account_hourly_refresh.ACTION_REPORT_TIME == "impression"
    assert snapchat_native_performance_sync.ACTION_REPORT_TIME == "impression"
    assert snapchat_dashboard_summary_routes.ACTION_REPORT_TIME == "impression"
    assert snapchat_account_hourly_refresh.ACCOUNT_REFRESH_SOURCE_MODE == (
        ADS_MANAGER_SOURCE_MODE
    )
