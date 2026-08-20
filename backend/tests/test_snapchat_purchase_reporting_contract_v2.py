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
    SNAPCHAT_SOURCE_MODE,
    _account_id_from_stats_url,
    extract_provider_freshness,
    install_snapchat_ads_manager_attribution,
    summarize_conversion_freshness,
)
from integrations_control_center.snapchat_salla_source_hybrid import (
    HYBRID_CONTRACT_VERSION,
    aggregate_salla_reported_source,
    canonical_marketing_source,
    merge_hybrid_snapchat_metrics,
)


class FakeContext:
    def __init__(self):
        self.params = None

    async def get_json(self, client, url, *, headers, params=None):
        self.params = dict(params or {})
        return {
            "request_status": "SUCCESS",
            "timeseries_stats": [
                {
                    "sub_request_status": "SUCCESS",
                    "timeseries_stat": {
                        "granularity": "HOUR",
                        "breakdown_stats": {"campaign": []},
                    },
                }
            ],
        }


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
        SNAPCHAT_SOURCE_MODE
    )
    assert SNAPCHAT_SOURCE_MODE.endswith(
        "conversion_freshness_nested_v6"
    )
    assert SNAPCHAT_SOURCE_MODE != ADS_MANAGER_SOURCE_MODE
    assert ADS_MANAGER_SOURCE_MODE.endswith(
        "account_timezone_conversion_v8"
    )
    assert HYBRID_CONTRACT_VERSION == "salla_reported_source_hybrid_v1"


def test_stats_url_extracts_only_the_ad_account_id():
    assert _account_id_from_stats_url(
        "https://adsapi.snapchat.com/v1/adaccounts/account-123/stats"
    ) == "account-123"
    assert _account_id_from_stats_url(
        "https://adsapi.snapchat.com/v1/campaigns/campaign-1/stats"
    ) is None


def test_nested_snapchat_response_exposes_conservative_freshness_times():
    payload = {
        "request_status": "SUCCESS",
        "timeseries_stats": [
            {
                "sub_request_status": "SUCCESS",
                "timeseries_stat": {
                    "id": "campaign-1",
                    "conversion_data_processed_end_time": (
                        "2026-08-02T15:25:00.000Z"
                    ),
                    "finalized_data_end_time": "2026-08-02T15:38:00.000Z",
                    "timeseries": [],
                },
            },
            {
                "sub_request_status": "SUCCESS",
                "timeseries_stat": {
                    "id": "campaign-2",
                    "conversion_data_processed_end_time": (
                        "2026-08-02T15:20:00.000Z"
                    ),
                    "finalized_data_end_time": "2026-08-02T15:35:00.000Z",
                    "timeseries": [],
                },
            },
        ],
    }

    result = extract_provider_freshness(payload)

    assert result == {
        "conversion_data_processed_end_time": "2026-08-02T15:20:00+00:00",
        "finalized_data_end_time": "2026-08-02T15:35:00+00:00",
    }


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
    assert result["capture_version"] == "nested_v6"
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


def test_salla_source_normalizer_accepts_arabic_and_nested_snapchat_values():
    assert canonical_marketing_source({"source": "سناب شات"}) == "snapchat"
    assert canonical_marketing_source({"utm_source": "snap_ads"}) == "snapchat"
    assert canonical_marketing_source({
        "raw_by_source": {
            "salla_direct": {
                "source": {"name": "Snapchat"},
            }
        }
    }) == "snapchat"
    assert canonical_marketing_source({"source": "Tik Tok"}) == "tiktok"


def test_salla_source_aggregation_counts_actual_orders_and_gross_revenue():
    result = aggregate_salla_reported_source(
        [
            {
                "order_number": "1",
                "order_date": "2026-08-02",
                "source": "snapchat",
                "total_amount": 250,
                "order_status": "جديد",
            },
            {
                "order_number": "2",
                "order_date": "2026-08-02",
                "utm_source": "سناب",
                "total_amount": 300,
                "order_status": "ملغى",
            },
            {
                "order_number": "3",
                "order_date": "2026-08-02",
                "source": "instagram",
                "total_amount": 500,
                "order_status": "جديد",
            },
            {
                "order_number": "4",
                "order_date": "2026-08-01",
                "source": "snapchat",
                "total_amount": 100,
                "order_status": "جديد",
            },
        ],
        start="2026-08-02",
        end="2026-08-02",
    )

    assert result["orders"] == 2
    assert result["revenue"] == 550.0
    assert result["source_observed_orders"] == 3
    assert result["total_period_orders"] == 3
    assert result["cancelled_orders"] == 1
    assert result["active_orders"] == 1


def test_hybrid_card_uses_salla_orders_but_preserves_snapchat_attribution():
    result = merge_hybrid_snapchat_metrics(
        {
            "spend": 3922.28,
            "orders": 14,
            "revenue": 2976.16,
            "impressions": 383013,
            "clicks": 8092,
            "roas": 0.76,
            "cpa": 280.16,
            "cost_per_order": 280.16,
        },
        {
            "orders": 32,
            "revenue": 7900,
            "source_observed_orders": 40,
            "total_period_orders": 44,
            "reported_source_coverage_pct": 90.91,
            "active_orders": 30,
            "cancelled_orders": 2,
            "refunded_orders": 0,
        },
    )

    assert result["hybrid_applied"] is True
    assert result["orders"] == 32
    assert result["revenue"] == 7900.0
    assert result["roas"] == 2.01
    assert result["cpa"] == 122.57
    assert result["attributed_orders"] == 14
    assert result["attributed_revenue"] == 2976.16
    assert result["attribution_gap_orders"] == 18
    assert result["attribution_coverage_pct"] == 43.75
    assert result["orders_source"] == "salla_reported_source"


def test_hybrid_card_keeps_provider_metrics_when_salla_source_is_unavailable():
    result = merge_hybrid_snapchat_metrics(
        {"spend": 100, "orders": 4, "revenue": 500, "roas": 5},
        {
            "orders": 0,
            "revenue": 0,
            "source_observed_orders": 0,
            "total_period_orders": 8,
        },
    )

    assert result["hybrid_applied"] is False
    assert result["orders"] == 4
    assert result["revenue"] == 500
    assert result["attributed_orders"] == 4
