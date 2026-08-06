from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file = Path(path)
    text = file.read_text(encoding="utf-8")
    if new in text:
        return
    if old not in text:
        raise SystemExit(f"pattern not found in {path}: {old[:180]!r}")
    file.write_text(text.replace(old, new, 1), encoding="utf-8")


platform = "backend/integrations_control_center/snapchat_platform_source_integrity.py"

replace_once(
    platform,
    '''This module keeps those sources separate. A direct TOTAL ad-account row owns
the unfiltered All Ads total, while a second TOTAL request broken down by
campaign owns campaign rows and filtered totals. Existing HOUR ingestion remains
responsible for the hourly chart and the Riyadh accounting projection.
''',
    '''This module keeps those sources separate. Snapchat direct Ad Account TOTAL
owns authoritative All Ads spend, while TOTAL + breakdown=campaign owns
purchases, purchase value, campaign rows, and filtered totals. Existing HOUR
ingestion remains responsible for the hourly chart and Riyadh accounting.
''',
)

replace_once(
    platform,
    '''PLATFORM_TOTAL_SOURCE_MODE = (
    f"{ADS_MANAGER_SOURCE_MODE}:account_total_v3"
)
''',
    '''PLATFORM_TOTAL_SOURCE_MODE = (
    f"{ADS_MANAGER_SOURCE_MODE}:account_spend_campaign_commercial_v4"
)
''',
)

replace_once(
    platform,
    '''REQUIRED_ACCOUNT_TOTAL_FIELDS = frozenset({
    "spend",
    "conversion_purchases",
    "conversion_purchases_value",
})
''',
    '''REQUIRED_ACCOUNT_TOTAL_FIELDS = frozenset({"spend"})
''',
)

replace_once(
    platform,
    '''    """Extract the exact ad-account TOTAL row without a campaign breakdown."""
''',
    '''    """Extract direct Ad Account TOTAL spend without a breakdown."""
''',
)

replace_once(
    platform,
    '''    """Read the exact All Ads total shown at ad-account level."""
''',
    '''    """Read direct All Ads spend; conversions come from campaign TOTAL."""
''',
)

replace_once(
    platform,
    '''def total_snapshot_is_authoritative(
''',
    '''def merge_direct_spend_with_campaign_metrics(
    direct_metrics: dict[str, Any],
    campaign_rows: list[dict[str, Any]],
) -> dict[str, int | float | None]:
    """Use the authoritative provider level for each All Ads metric.

    The direct Ad Account TOTAL row is accepted for spend. Purchases, purchase
    value, impressions, swipes, and video metrics are summed from the complete
    campaign breakdown. No Salla result is introduced into the platform view.
    """
    merged = aggregate_total_campaign_metrics(campaign_rows)
    spend = _as_number(direct_metrics.get("spend"))
    if spend is None:
        raise SnapchatNativeSyncError(
            "snapchat_account_direct_spend_missing",
            "Snapchat direct Ad Account TOTAL omitted spend.",
            status_code=502,
            retryable=True,
        )
    merged["spend"] = int(spend) if float(spend).is_integer() else float(spend)
    return merged


def total_snapshot_is_authoritative(
''',
)

replace_once(
    platform,
    '''    await _upsert_total_row(
        context,
        account=account,
        entity_type="ad_account",
        external_id=_text(account.get("ad_account_id")),
        date_string=date_string,
        timezone_name=timezone_name,
        metrics=account_metrics,
''',
    '''    all_ads_metrics = merge_direct_spend_with_campaign_metrics(
        account_metrics,
        campaign_rows,
    )
    await _upsert_total_row(
        context,
        account=account,
        entity_type="ad_account",
        external_id=_text(account.get("ad_account_id")),
        date_string=date_string,
        timezone_name=timezone_name,
        metrics=all_ads_metrics,
''',
)

replace_once(
    platform,
    '''        "direct_account_total_requested": True,
        "request_windows": request_windows,
''',
    '''        "direct_account_total_requested": True,
        "account_spend_source": "direct_ad_account_total",
        "account_commercial_totals_source": "complete_campaign_breakdown_sum",
        "request_windows": request_windows,
''',
)

replace_once(
    platform,
    '''        "platform_source_isolated": True,
        "platform_action_report_time": ADS_MANAGER_ACTION_REPORT_TIME,
''',
    '''        "platform_source_isolated": True,
        "platform_action_report_time": ADS_MANAGER_ACTION_REPORT_TIME,
        "account_spend_source": "direct_ad_account_total",
        "account_commercial_totals_source": "complete_campaign_breakdown_sum",
''',
)

replace_once(
    platform,
    '''            "direct_account_total_snapshot",
''',
    '''            "campaign_breakdown_all_ads_snapshot",
''',
)

replace_once(
    platform,
    '''    "load_platform_total_rows",
    "persist_account_total_day",
''',
    '''    "load_platform_total_rows",
    "merge_direct_spend_with_campaign_metrics",
    "persist_account_total_day",
''',
)

backend_test = "backend/tests/test_snapchat_platform_source_integrity_v1.py"
replace_once(
    backend_test,
    '''    extract_account_total_metrics,
    total_snapshot_is_authoritative,
''',
    '''    extract_account_total_metrics,
    merge_direct_spend_with_campaign_metrics,
    total_snapshot_is_authoritative,
''',
)

replace_once(
    backend_test,
    '''def test_direct_account_total_stays_separate_from_campaign_breakdown():
    payload = {
        "total_stats": [{
            "sub_request_status": "SUCCESS",
            "total_stat": {
                "stats": {
                    "spend": 489_090_000,
                    "conversion_purchases": 21,
                    "conversion_purchases_value": 811_370_000,
                },
            },
        }],
    }
    metrics, errors, successful = extract_account_total_metrics(payload)
    assert errors == []
    assert successful == 1
    assert metrics["spend"] == 489_090_000
    assert metrics["conversion_purchases"] == 21
    assert metrics["conversion_purchases_value"] == 811_370_000
    assert metrics["impressions"] == 0


def test_direct_total_rejects_missing_commercial_fields():
    metrics, errors, successful = extract_account_total_metrics({
        "total_stats": [{
            "sub_request_status": "SUCCESS",
            "total_stat": {"stats": {"spend": 489_090_000}},
        }],
    })
    assert successful == 1
    assert metrics is None
    assert errors[0]["code"] == "snapchat_account_direct_total_fields_missing"
''',
    '''def test_direct_account_total_accepts_documented_spend_only_payload():
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
''',
)

replace_once(
    backend_test,
    '''    assert source == "direct_account_total_snapshot"
''',
    '''    assert source == "campaign_breakdown_all_ads_snapshot"
''',
)

frontend_service = "frontend/src/services/marketingPerformance.js"
replace_once(
    frontend_service,
    '''            entity_limit_reached: value.source?.entity_limit_reached === true,
        },
''',
    '''            entity_limit_reached: value.source?.entity_limit_reached === true,
            platform_total_snapshot_ready: value.source?.platform_total_snapshot_ready === true,
            platform_direct_account_total_ready: value.source?.platform_direct_account_total_ready === true,
            platform_action_report_time: nullableText(value.source?.platform_action_report_time),
            account_spend_source: nullableText(value.source?.account_spend_source),
            account_commercial_totals_source: nullableText(
                value.source?.account_commercial_totals_source,
            ),
        },
''',
)

explorer = "frontend/src/components/marketing/AdsPerformanceExplorer.jsx"
replace_once(
    explorer,
    '''function finite(value) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
}
''',
    '''function finite(value) {
    if (value === null || value === undefined || value === "") return null;
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
}
''',
)

frontend_test = "frontend/src/services/marketingPerformance.test.js"
replace_once(
    frontend_test,
    '''                row_limit_reached: false,
            },
''',
    '''                row_limit_reached: false,
                platform_total_snapshot_ready: true,
                platform_direct_account_total_ready: true,
                platform_action_report_time: "impression",
                account_spend_source: "direct_ad_account_total",
                account_commercial_totals_source: "complete_campaign_breakdown_sum",
            },
''',
)
replace_once(
    frontend_test,
    '''    expect(result.ai_readiness.ai_analysis_ready).toBe(true);
''',
    '''    expect(result.source).toMatchObject({
        platform_total_snapshot_ready: true,
        platform_direct_account_total_ready: true,
        platform_action_report_time: "impression",
        account_spend_source: "direct_ad_account_total",
        account_commercial_totals_source: "complete_campaign_breakdown_sum",
    });
    expect(result.ai_readiness.ai_analysis_ready).toBe(true);
''',
)

print("SNAP_CAMPAIGN_TOTAL_DIRECT_SPEND_V4_APPLIED")
