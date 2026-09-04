from pathlib import Path
from datetime import date, datetime, timezone

import httpx
import pytest

from integrations_control_center.snapchat_native_data_common import (
    SnapchatNativeSyncError,
    SnapchatSyncContext,
)
from integrations_control_center.snapchat_platform_source_integrity import (
    DIRECT_ACCOUNT_TOTAL_FIELDS,
    PLATFORM_TOTAL_SOURCE_MODE,
    account_local_dates_for_refresh,
    account_local_total_window,
    aggregate_total_campaign_metrics,
    audit_platform_purchase_totals,
    extract_account_total_campaign_rows,
    extract_account_total_metrics,
    fetch_account_total_direct_metrics,
    merge_direct_spend_with_campaign_metrics,
    _mask_pending_platform_commercial_metrics,
    total_snapshot_is_authoritative,
)


def _row(entity_type, external_id, *, orders, spend, sales, date_string="2026-08-06"):
    return {
        "entity_type": entity_type,
        "external_id": external_id,
        "campaign_id": external_id if entity_type == "campaign" else None,
        "date": date_string,
        "purchases": orders,
        "spend_native": spend,
        "spend_sar": spend * 3.75,
        "purchase_value_native": sales,
        "purchase_value_sar": sales * 3.75,
        "source_mode": PLATFORM_TOTAL_SOURCE_MODE,
        "updated_at": "2026-08-06T14:00:00+00:00",
    }


def test_account_local_current_day_window_uses_next_midnight_boundary():
    start, end = account_local_total_window(
        date(2026, 8, 6),
        timezone_name="America/Los_Angeles",
        now=datetime(2026, 8, 6, 15, 30, 45, tzinfo=timezone.utc),
    )
    assert start.isoformat() == "2026-08-06T00:00:00-07:00"
    assert end.isoformat() == "2026-08-07T00:00:00-07:00"


def test_refresh_dates_cover_account_days_touched_by_riyadh_window():
    dates = account_local_dates_for_refresh(
        date(2026, 8, 5),
        date(2026, 8, 6),
        timezone_name="America/Los_Angeles",
        now=datetime(2026, 8, 6, 15, 30, tzinfo=timezone.utc),
    )
    assert dates == [date(2026, 8, 4), date(2026, 8, 5), date(2026, 8, 6)]


def test_extract_total_campaign_breakdown_and_aggregate_matches_ads_manager():
    start = datetime(2026, 8, 6, 0, 0, tzinfo=timezone.utc)
    end = datetime(2026, 8, 6, 12, 0, tzinfo=timezone.utc)
    payload = {
        "total_stats": [{
            "sub_request_status": "SUCCESS",
            "total_stat": {
                "start_time": start.isoformat(),
                "end_time": end.isoformat(),
                "breakdown_stats": {
                    "campaign": [
                        {
                            "id": "campaign-1",
                            "stats": {
                                "impressions": 100,
                                "swipes": 10,
                                "spend": 203_350_000,
                                "video_views": 50,
                                "view_completion": 25,
                                "conversion_purchases": 12,
                                "conversion_purchases_value": 500_000_000,
                            },
                        },
                        {
                            "id": "campaign-2",
                            "stats": {
                                "impressions": 200,
                                "swipes": 20,
                                "spend": 179_450_000,
                                "video_views": 100,
                                "view_completion": 50,
                                "conversion_purchases": 4,
                                "conversion_purchases_value": 245_750_000,
                            },
                        },
                    ],
                },
            },
        }],
    }
    rows, errors, success, breakdown_seen = extract_account_total_campaign_rows(
        payload,
        request_start=start,
        request_end=end,
    )
    assert errors == []
    assert success == 1
    assert breakdown_seen is True
    assert [row["campaign_id"] for row in rows] == ["campaign-1", "campaign-2"]

    metrics = aggregate_total_campaign_metrics(rows)
    assert metrics["conversion_purchases"] == 16
    assert metrics["spend"] == 382_800_000
    assert metrics["conversion_purchases_value"] == 745_750_000



@pytest.mark.asyncio
async def test_provider_http_400_keeps_only_safe_snapchat_error_detail():
    class Client:
        async def get(self, url, *, headers, params=None):
            return httpx.Response(
                400,
                json={
                    "request_status": "ERROR",
                    "error_code": "E_REPORTING_PARAM",
                    "debug_message": "Invalid reporting parameter: granularity",
                },
            )

    context = SnapchatSyncContext(db=None, user_id="user-1")
    with pytest.raises(SnapchatNativeSyncError) as captured:
        await context.get_json(
            Client(),
            "https://adsapi.snapchat.com/v1/adaccounts/account-1/stats",
            headers={"Authorization": "Bearer token-not-logged"},
            params={"granularity": "HOUR"},
        )

    assert captured.value.code == "snapchat_provider_http_400"
    assert captured.value.result == {
        "provider_error_code": "E_REPORTING_PARAM",
        "provider_error_message": "Invalid reporting parameter: granularity",
    }
    assert "token-not-logged" not in captured.value.message
    assert "E_REPORTING_PARAM" in captured.value.message

@pytest.mark.asyncio
async def test_direct_account_request_uses_all_ads_metrics_and_attribution():
    class CaptureContext:
        def __init__(self):
            self.params = None

        async def get_json(self, client, url, *, headers, params=None):
            self.params = dict(params or {})
            return {
                "total_stats": [{
                    "sub_request_status": "SUCCESS",
                    "total_stat": {
                        "stats": {
                            "spend": 714_050_000,
                            "conversion_purchases": 24,
                            "conversion_purchases_value": 1_186_350_000,
                        },
                    },
                }],
            }

    context = CaptureContext()
    metrics, errors = await fetch_account_total_direct_metrics(
        context,
        object(),
        "token-not-used",
        account_id="account-1",
        request_start=datetime(2026, 8, 6, 0, 0, tzinfo=timezone.utc),
        request_end=datetime(2026, 8, 6, 12, 0, tzinfo=timezone.utc),
        action_report_time="conversion",
    )

    assert errors == []
    assert metrics["spend"] == 714_050_000
    assert metrics["conversion_purchases"] == 24
    assert tuple(context.params["fields"].split(",")) == DIRECT_ACCOUNT_TOTAL_FIELDS
    assert context.params["conversion_source_types"] == "total"
    assert context.params["swipe_up_attribution_window"] == "28_DAY"
    assert context.params["view_attribution_window"] == "7_DAY"
    assert context.params["action_report_time"] == "conversion"



@pytest.mark.asyncio
async def test_current_day_direct_request_rolls_up_completed_hours():
    class CaptureContext:
        def __init__(self):
            self.params = None

        async def get_json(self, client, url, *, headers, params=None):
            self.params = dict(params or {})
            return {
                "timeseries_stats": [{
                    "sub_request_status": "SUCCESS",
                    "timeseries_stat": {
                        "id": "account-1",
                        "timeseries": [
                            {
                                "stats": {
                                    "spend": 100_000_000,
                                    "conversion_purchases": 2,
                                    "conversion_purchases_value": 250_000_000,
                                },
                            },
                            {
                                "stats": {
                                    "spend": 125_000_000,
                                    "conversion_purchases": 3,
                                    "conversion_purchases_value": 400_000_000,
                                },
                            },
                        ],
                    },
                }],
            }

    context = CaptureContext()
    metrics, errors = await fetch_account_total_direct_metrics(
        context,
        object(),
        "token-not-used",
        account_id="account-1",
        request_start=datetime(2026, 8, 6, 0, 0, tzinfo=timezone.utc),
        request_end=datetime(2026, 8, 6, 8, 0, tzinfo=timezone.utc),
        granularity="HOUR",
        action_report_time="conversion",
    )

    assert errors == []
    assert context.params["granularity"] == "HOUR"
    assert metrics["spend"] == 225_000_000
    assert metrics["conversion_purchases"] == 5
    assert metrics["conversion_purchases_value"] == 650_000_000

def test_direct_account_total_accepts_documented_spend_only_payload():
    payload = {
        "total_stats": [{
            "sub_request_status": "SUCCESS",
            "total_stat": {"stats": {"spend": 714_050_000}},
        }],
    }
    metrics, errors, successful = extract_account_total_metrics(payload)
    assert errors == []
    assert successful == 1
    assert metrics["spend"] == 714_050_000
    assert "conversion_purchases" not in metrics
    assert "conversion_purchases_value" not in metrics


def test_direct_total_rejects_missing_spend():
    metrics, errors, successful = extract_account_total_metrics({
        "total_stats": [{
            "sub_request_status": "SUCCESS",
            "total_stat": {
                "stats": {
                    "conversion_purchases": 29,
                    "conversion_purchases_value": 1_186_860_000,
                },
            },
        }],
    })
    assert successful == 1
    assert metrics is None
    assert errors[0]["code"] == "snapchat_account_direct_total_fields_missing"


def test_scheduler_resolves_installed_snapchat_refresh_at_runtime():
    source = Path(
        "integrations_control_center/ads_auto_sync_scheduler.py"
    ).read_text(encoding="utf-8")

    assert (
        "from . import snapchat_account_hourly_refresh as snapchat_hourly"
        in source
    )
    assert "snapchat_hourly.refresh_snapchat_account_hours(" in source


def test_platform_total_v15_uses_complete_campaign_day_total():
    source = Path(
        "integrations_control_center/snapchat_platform_source_integrity.py"
    ).read_text(encoding="utf-8")

    assert "campaign_breakdown_all_ads_account_day_total_v15" in source
    refresh = source.split(
        "async def refresh_account_total_snapshots", 1
    )[1].split("async def _to_list", 1)[0]
    assert "await fetch_account_total_direct_metrics(" not in refresh
    assert "account_metrics = aggregate_total_campaign_metrics(rows)" in refresh
    assert '"direct_account_total_requested": False' in refresh
    assert "request_granularity = PLATFORM_TOTAL_GRANULARITY" in refresh
    assert "if report_date == local_current_date" not in refresh
    assert (
        '"current_day_provider_granularity": PLATFORM_TOTAL_GRANULARITY'
        in refresh
    )


def test_all_ads_merges_direct_spend_with_campaign_commercial_metrics():
    direct = {"spend": 714_050_000}
    campaign_rows = [
        {
            "campaign_id": "campaign-1",
            "metrics": {
                "spend": 357_860_000,
                "conversion_purchases": 15,
                "conversion_purchases_value": 500_000_000,
                "impressions": 173_368,
                "swipes": 2_261,
            },
        },
        {
            "campaign_id": "campaign-2",
            "metrics": {
                "spend": 180_180_000,
                "conversion_purchases": 4,
                "conversion_purchases_value": 250_000_000,
                "impressions": 134_850,
                "swipes": 2_148,
            },
        },
        {
            "campaign_id": "campaign-rest",
            "metrics": {
                "spend": 176_010_000,
                "conversion_purchases": 10,
                "conversion_purchases_value": 436_860_000,
            },
        },
    ]
    merged = merge_direct_spend_with_campaign_metrics(direct, campaign_rows)
    assert merged["spend"] == 714_050_000
    assert merged["conversion_purchases"] == 29
    assert merged["conversion_purchases_value"] == 1_186_860_000
    assert merged["impressions"] == 308_218
    assert merged["swipes"] == 4_409


def test_all_ads_prefers_direct_headline_metrics_over_campaign_sum():
    direct = {
        "spend": 474_720_000,
        "conversion_purchases": 24,
        "conversion_purchases_value": 1_186_350_000,
        "impressions": 500_000,
        "swipes": 3_200,
        "video_views": 250_000,
        "view_completion": 120_000,
    }
    campaign_rows = [{
        "campaign_id": "campaign-stale",
        "metrics": {
            "spend": 474_720_000,
            "conversion_purchases": 8,
            "conversion_purchases_value": 400_000_000,
            "impressions": 300_000,
            "swipes": 2_000,
        },
    }]

    merged = merge_direct_spend_with_campaign_metrics(direct, campaign_rows)

    assert merged["spend"] == 474_720_000
    assert merged["conversion_purchases"] == 24
    assert merged["conversion_purchases_value"] == 1_186_350_000
    assert merged["impressions"] == 500_000
    assert merged["swipes"] == 3_200


def test_audit_prefers_direct_account_total_and_keeps_campaign_sum_separate():
    rows = [
        _row("ad_account", "account-1", orders=21, spend=489.09, sales=811.37),
        _row("campaign", "campaign-1", orders=12, spend=203.35, sales=500),
        _row("campaign", "campaign-2", orders=4, spend=179.45, sales=245.75),
    ]
    account, campaigns, source = audit_platform_purchase_totals(
        rows,
        requested_days=1,
    )
    assert account == 21
    assert campaigns == 16
    assert source == "campaign_breakdown_all_ads_snapshot"


def test_fixed_created_order_semantics_preserves_platform_and_attaches_salla():
    source = Path(
        "integrations_control_center/snapchat_campaign_created_order_semantics.py"
    ).read_text(encoding="utf-8")
    assert 'salla_view = result_source == "salla"' in source
    assert 'campaign["salla_profitability"] = by_campaign[key]' in source
    assert '"provider_metrics_preserved_for_platform_source": not salla_view' in source


def test_created_order_dependencies_are_lazy_and_local():
    source = Path(
        "integrations_control_center/snapchat_campaign_created_order_semantics.py"
    ).read_text(encoding="utf-8")
    assert "\nfrom auth import ensure_user_settings\n" not in source
    assert "\nfrom dashboard_v2_routes import _matches_any\n" not in source
    assert "\nfrom . import snapchat_campaign_profitability as profitability\n" not in source
    assert "def _matches_any(" in source

    outcomes_start = source.index("async def build_created_and_financial_outcomes")
    outcomes_body = source[outcomes_start:outcomes_start + 1400]
    assert "from auth import ensure_user_settings" in outcomes_body

    profitability_start = source.index("async def calculate_financial_profitability")
    profitability_body = source[profitability_start:profitability_start + 900]
    assert "from . import snapchat_campaign_profitability as profitability" in profitability_body


def test_package_lazy_loads_salla_profitability_stack_only_during_router_composition():
    source = Path("integrations_control_center/__init__.py").read_text(
        encoding="utf-8"
    )
    prefix, router_body = source.split(
        "def make_integrations_control_center_router", 1
    )
    for module in (
        "snapchat_campaign_created_order_semantics",
        "snapchat_campaign_current_catalog_cost",
        "snapchat_campaign_profitability",
        "snapchat_campaign_profitability_exact_reuse",
    ):
        assert module not in prefix
        assert module in router_body


def test_platform_total_collection_partitions_conversion_and_impression():
    source = Path(
        "integrations_control_center/snapchat_platform_source_integrity.py"
    ).read_text(encoding="utf-8")
    assert 'mezan_snapchat_performance_account_total_v2' in source
    assert 'action_report_time' in source
    assert 'for action_report_time in ADS_MANAGER_SUPPORTED_ACTION_REPORT_TIMES' in source
    assert 'platform_total_source_mode(action_report_time)' in source


def test_account_local_collection_partitions_conversion_and_impression():
    source = Path(
        "integrations_control_center/snapchat_account_timezone_manager.py"
    ).read_text(encoding="utf-8")
    assert 'mezan_snapchat_performance_account_day_v3' in source
    assert '("action_report_time", 1)' in source
    assert '("conversion", local_conversion_campaigns, local_conversion_accounts)' in source
    assert '("impression", local_impression_campaigns, local_impression_accounts)' in source
    assert 'action_report_time: str = ADS_MANAGER_DEFAULT_ACTION_REPORT_TIME' in source


def test_entity_level_reports_partition_attribution_modes():
    adsquad = Path(
        "integrations_control_center/snapchat_adsquad_performance.py"
    ).read_text(encoding="utf-8")
    ad = Path(
        "integrations_control_center/snapchat_ad_performance.py"
    ).read_text(encoding="utf-8")
    for source in (adsquad, ad):
        assert "ADS_MANAGER_SUPPORTED_ACTION_REPORT_TIMES" in source
        assert 'pattern="^(conversion|impression)$"' in source
        assert 'action_report_time=action_report_time' in source
        assert '"action_report_time": action_report_time' in source
    assert "adsquad_source_mode(action_report_time)" in adsquad
    assert "ad_source_mode(action_report_time)" in ad


def test_pending_platform_mask_is_explicitly_partitioned_by_action_time():
    result = {
        "totals": {"orders": 99, "sales_sar": 999.0},
        "daily": [],
        "campaigns": [],
        "accounts": [],
    }
    masked = _mask_pending_platform_commercial_metrics(
        result,
        action_report_time="impression",
    )
    assert masked["totals"]["orders"] is None
    assert masked["totals"]["sales_sar"] is None
    assert (
        masked["totals"]["commercial_metrics_source"]
        == "snapchat_impression_total_pending"
    )


def test_ads_manager_uses_28d_click_7d_view_without_changing_riyadh_contract():
    freshness = Path(
        "integrations_control_center/snapchat_freshness_impl_v6.py"
    ).read_text(encoding="utf-8")
    hourly_refresh = Path(
        "integrations_control_center/snapchat_account_hourly_refresh.py"
    ).read_text(encoding="utf-8")
    manager = Path(
        "integrations_control_center/snapchat_account_timezone_manager.py"
    ).read_text(encoding="utf-8")
    hourly_chart = Path(
        "integrations_control_center/snapchat_account_hourly_chart.py"
    ).read_text(encoding="utf-8")
    platform = Path(
        "integrations_control_center/snapchat_platform_source_integrity.py"
    ).read_text(encoding="utf-8")

    assert (
        'ADS_MANAGER_SWIPE_ATTRIBUTION_WINDOW: Final[str] = "28_DAY"'
        in freshness
    )
    assert (
        'ADS_MANAGER_VIEW_ATTRIBUTION_WINDOW: Final[str] = "7_DAY"'
        in freshness
    )

    # Dashboard/accounting keeps its existing contract.
    assert 'VIEW_ATTRIBUTION_WINDOW = "1_DAY"' in hourly_refresh

    # Ads Manager requests use the isolated 7-day view window.
    assert (
        "view_attribution_window=ADS_MANAGER_VIEW_ATTRIBUTION_WINDOW"
        in manager
    )
    assert (
        '"view_attribution_window": '
        "ADS_MANAGER_VIEW_ATTRIBUTION_WINDOW"
        in platform
    )

    # Hourly chart is partitioned by the same selected mode.
    assert "mezan_snapchat_performance_account_hour_v2" in hourly_chart
    assert '("action_report_time", 1)' in hourly_chart
    assert "def account_local_hourly_source_mode(" in hourly_chart
    assert '"source_mode": account_local_hourly_source_mode(' in hourly_chart
    assert '"action_report_time": action_report_time' in hourly_chart

    # TOTAL snapshots also partition conversion vs impression.
    assert "mezan_snapchat_account_total_v2_identity_unique" in platform
    assert '("action_report_time", 1)' in platform

    # Impression comparison must never become AI-operational truth.
    assert (
        "and action_report_time\n"
        "            == ADS_MANAGER_DEFAULT_ACTION_REPORT_TIME"
        in platform
    )


def test_ready_platform_metadata_uses_selected_action_report_time():
    source = Path(
        "integrations_control_center/snapchat_platform_source_integrity.py"
    ).read_text(encoding="utf-8")

    assert (
        '"platform_action_report_time": action_report_time'
        in source
    )
    assert (
        '"platform_action_report_time": ADS_MANAGER_ACTION_REPORT_TIME'
        not in source
    )


def test_entity_upserts_preserve_legacy_riyadh_identity():
    paths = (
        "integrations_control_center/snapchat_adsquad_performance.py",
        "integrations_control_center/snapchat_ad_performance.py",
    )

    for path in paths:
        source = Path(path).read_text(encoding="utf-8")

        assert (
            "if collection_name "
            "== SNAPCHAT_ACCOUNT_LOCAL_PERFORMANCE_COLLECTION:"
            in source
        )
        assert (
            'identity["action_report_time"] = action_report_time'
            in source
        )
        assert "update_one(\n        identity," in source


def test_total_aggregation_treats_omitted_zero_metrics_as_zero():
    rows = [
        {
            "campaign_id": "campaign-1",
            "metrics": {
                "spend": 100_000_000,
                "conversion_purchases": 2,
                "conversion_purchases_value": 300_000_000,
            },
        },
        {
            "campaign_id": "campaign-2",
            "metrics": {"spend": 50_000_000},
        },
    ]
    metrics = aggregate_total_campaign_metrics(rows)
    assert metrics["spend"] == 150_000_000
    assert metrics["conversion_purchases"] == 2
    assert metrics["conversion_purchases_value"] == 300_000_000
    assert metrics["impressions"] == 0


def test_partial_total_response_never_replaces_complete_snapshot():
    assert total_snapshot_is_authoritative(
        breakdown_seen=True,
        account_metrics_available=True,
        errors=[],
    ) is True
    assert total_snapshot_is_authoritative(
        breakdown_seen=False,
        account_metrics_available=True,
        errors=[],
    ) is False
    assert total_snapshot_is_authoritative(
        breakdown_seen=True,
        account_metrics_available=False,
        errors=[],
    ) is False
    assert total_snapshot_is_authoritative(
        breakdown_seen=True,
        account_metrics_available=True,
        errors=[{"code": "partial"}],
    ) is False



def test_ads_manager_defaults_to_conversion_and_supports_impression_comparison():
    freshness = Path(
        "integrations_control_center/snapchat_freshness_impl_v6.py"
    ).read_text(encoding="utf-8")
    assert 'ADS_MANAGER_DEFAULT_ACTION_REPORT_TIME: Final[str] = "conversion"' in freshness
    assert 'ADS_MANAGER_SUPPORTED_ACTION_REPORT_TIMES' in freshness
    assert '"impression"' in freshness
    assert 'SNAPCHAT_ACTION_REPORT_TIME: Final[str] = "conversion"' in freshness

    platform = Path(
        "integrations_control_center/snapchat_platform_source_integrity.py"
    ).read_text(encoding="utf-8")
    manager = Path(
        "integrations_control_center/snapchat_account_timezone_manager.py"
    ).read_text(encoding="utf-8")
    hourly_chart = Path(
        "integrations_control_center/snapchat_account_hourly_chart.py"
    ).read_text(encoding="utf-8")
    adsquad = Path(
        "integrations_control_center/snapchat_adsquad_performance.py"
    ).read_text(encoding="utf-8")
    ads = Path(
        "integrations_control_center/snapchat_ad_performance.py"
    ).read_text(encoding="utf-8")

    assert 'normalize_ads_manager_action_report_time' in platform
    assert 'action_report_time=action_report_time' in manager
    assert 'kwargs.get("action_report_time")' in hourly_chart
    assert '"action_report_time": normalize_ads_manager_action_report_time(action_report_time)' in adsquad
    assert '"action_report_time": normalize_ads_manager_action_report_time(action_report_time)' in ads
    assert 'if (not account_rows or not campaign_rows) and platform_view:' in platform
    assert 'legacy_hour_conversions_hidden_while_pending' in platform
