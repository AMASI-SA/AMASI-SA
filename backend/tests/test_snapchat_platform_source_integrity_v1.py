from pathlib import Path
from datetime import date, datetime, timezone

from integrations_control_center.snapchat_platform_source_integrity import (
    PLATFORM_TOTAL_SOURCE_MODE,
    account_local_dates_for_refresh,
    account_local_total_window,
    aggregate_total_campaign_metrics,
    audit_platform_purchase_totals,
    extract_account_total_campaign_rows,
    extract_account_total_metrics,
    merge_direct_spend_with_campaign_metrics,
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


def test_account_local_total_window_uses_account_midnight_and_current_second():
    start, end = account_local_total_window(
        date(2026, 8, 6),
        timezone_name="America/Los_Angeles",
        now=datetime(2026, 8, 6, 15, 30, 45, tzinfo=timezone.utc),
    )
    assert start.isoformat() == "2026-08-06T00:00:00-07:00"
    assert end.isoformat() == "2026-08-06T08:30:45-07:00"


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
    assert metrics["conversion_purchases"] == 0
    assert metrics["conversion_purchases_value"] == 0


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


def test_fixed_created_order_semantics_is_gated_to_salla_source():
    source = Path(
        "integrations_control_center/snapchat_campaign_created_order_semantics.py"
    ).read_text(encoding="utf-8")
    assert 'if result_source != "salla":' in source
    assert '"provider_metrics_preserved_for_platform_source": True' in source


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



def test_ads_manager_entity_levels_use_impression_time():
    freshness = Path(
        "integrations_control_center/snapchat_freshness_impl_v6.py"
    ).read_text(encoding="utf-8")
    assert 'ADS_MANAGER_ACTION_REPORT_TIME: Final[str] = "impression"' in freshness
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

    assert '"action_report_time": ADS_MANAGER_ACTION_REPORT_TIME' in platform
    assert 'action_report_time=ADS_MANAGER_ACTION_REPORT_TIME' in manager
    assert 'kwargs.get("action_report_time")' in hourly_chart
    assert '"action_report_time": ADS_MANAGER_ACTION_REPORT_TIME' in adsquad
    assert '"action_report_time": ADS_MANAGER_ACTION_REPORT_TIME' in ads
    assert 'if not account_rows or not campaign_rows:' in platform
    assert 'legacy_hour_conversions_hidden_while_pending' in platform
