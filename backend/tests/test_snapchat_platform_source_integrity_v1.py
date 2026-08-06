from pathlib import Path
from datetime import date, datetime, timezone

from integrations_control_center.snapchat_platform_source_integrity import (
    PLATFORM_TOTAL_SOURCE_MODE,
    account_local_dates_for_refresh,
    account_local_total_window,
    aggregate_total_campaign_metrics,
    audit_platform_purchase_totals,
    extract_account_total_campaign_rows,
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


def test_audit_prefers_account_total_and_keeps_campaign_sum_separate():
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
    assert source == "account_total_snapshot"



def test_fixed_created_order_semantics_is_gated_to_salla_source():
    source = Path(
        "integrations_control_center/snapchat_campaign_created_order_semantics.py"
    ).read_text(encoding="utf-8")
    assert 'if result_source != "salla":' in source
    assert '"provider_metrics_preserved_for_platform_source": True' in source
