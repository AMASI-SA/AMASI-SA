import asyncio
from datetime import date, datetime, timezone

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
    _account_id_from_stats_url,
    install_snapchat_ads_manager_attribution,
    summarize_conversion_freshness,
)


class FakeContext:
    def __init__(self):
        self.params = None

    async def get_json(self, client, url, *, headers, params=None):
        self.params = dict(params or {})
        return {"timeseries_stats": []}


def test_scheduler_requests_conversion_time_purchases_with_explicit_attribution():
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
    assert params["action_report_time"] == ADS_MANAGER_ACTION_REPORT_TIME == "conversion"
    assert params["swipe_up_attribution_window"] == SWIPE_ATTRIBUTION_WINDOW == "28_DAY"
    assert params["view_attribution_window"] == VIEW_ATTRIBUTION_WINDOW == "1_DAY"
    assert "conversion_purchases" in params["fields"]
    assert "conversion_purchases_value" in params["fields"]


def test_installer_keeps_request_storage_and_response_metadata_consistent():
    from integrations_control_center import snapchat_account_hourly_refresh
    from integrations_control_center import snapchat_dashboard_summary_routes
    from integrations_control_center import snapchat_native_performance_sync

    install_snapchat_ads_manager_attribution()

    assert snapchat_account_hourly_refresh.ACTION_REPORT_TIME == "conversion"
    assert snapchat_native_performance_sync.ACTION_REPORT_TIME == "conversion"
    assert snapchat_dashboard_summary_routes.ACTION_REPORT_TIME == "conversion"
    assert snapchat_account_hourly_refresh.ACCOUNT_REFRESH_SOURCE_MODE == (
        ADS_MANAGER_SOURCE_MODE
    )
    assert ADS_MANAGER_SOURCE_MODE.endswith("conversion_freshness_v5")


def test_stats_url_extracts_only_the_ad_account_id():
    assert _account_id_from_stats_url(
        "https://adsapi.snapchat.com/v1/adaccounts/account-123/stats"
    ) == "account-123"
    assert _account_id_from_stats_url(
        "https://adsapi.snapchat.com/v1/campaigns/campaign-1/stats"
    ) is None


def test_freshness_is_conservative_across_selected_accounts():
    result = summarize_conversion_freshness(
        [
            {
                "ad_account_id": "account-1",
                "conversion_data_processed_end_time": "2026-08-02T15:20:00Z",
                "finalized_data_end_time": "2026-08-02T15:35:00Z",
                "request_end_time": "2026-08-02T16:00:00Z",
                "observed_at": "2026-08-02T15:37:19Z",
            },
            {
                "ad_account_id": "account-2",
                "conversion_data_processed_end_time": "2026-08-02T15:25:00Z",
                "finalized_data_end_time": "2026-08-02T15:36:00Z",
                "request_end_time": "2026-08-02T16:00:00Z",
                "observed_at": "2026-08-02T15:37:19Z",
            },
        ],
        expected_account_ids=["account-1", "account-2"],
        now=datetime(2026, 8, 2, 15, 40, tzinfo=timezone.utc),
    )

    assert result["status"] == "processing"
    assert result["provisional"] is True
    assert result["accounts_expected"] == 2
    assert result["accounts_reporting"] == 2
    assert result["conversion_data_processed_end_time"] == (
        "2026-08-02T15:20:00+00:00"
    )
    assert result["conversion_lag_minutes"] == 20.0


def test_freshness_is_complete_only_when_every_account_covers_request():
    result = summarize_conversion_freshness(
        [
            {
                "ad_account_id": "account-1",
                "conversion_data_processed_end_time": "2026-08-02T16:05:00Z",
                "request_end_time": "2026-08-02T16:00:00Z",
            },
            {
                "ad_account_id": "account-2",
                "conversion_data_processed_end_time": "2026-08-02T16:02:00Z",
                "request_end_time": "2026-08-02T16:00:00Z",
            },
        ],
        expected_account_ids=["account-1", "account-2"],
        now=datetime(2026, 8, 2, 16, 10, tzinfo=timezone.utc),
    )

    assert result["status"] == "complete"
    assert result["provisional"] is False
