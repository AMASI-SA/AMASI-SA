from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file = Path(path)
    text = file.read_text(encoding="utf-8")
    if new in text:
        return
    if old not in text:
        raise SystemExit(f"pattern not found in {path}: {old[:180]!r}")
    file.write_text(text.replace(old, new, 1), encoding="utf-8")


module = "backend/integrations_control_center/snapchat_platform_source_integrity.py"
replace_once(
    module,
    '''This module keeps those sources separate. A direct TOTAL ad-account row owns
the unfiltered All Ads total, while a second TOTAL request broken down by
campaign owns campaign rows and filtered totals. Existing HOUR ingestion remains
responsible for the hourly chart and the Riyadh accounting projection.
''',
    '''This module keeps those sources separate. Snapchat exposes only spend on the
direct ad-account entity. Therefore a direct TOTAL row owns All Ads spend, while
a second TOTAL request broken down by campaign owns purchases, purchase value,
and campaign rows. Existing HOUR ingestion remains responsible for the hourly
chart and the Riyadh accounting projection.
''',
)
replace_once(
    module,
    '''PLATFORM_TOTAL_SOURCE_MODE = (
    "snapchat_account_direct_total_plus_campaign_breakdown_account_local_v2"
)
''',
    '''PLATFORM_TOTAL_SOURCE_MODE = (
    "snapchat_account_spend_plus_campaign_commercial_totals_account_local_v3"
)
''',
)
replace_once(
    module,
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
    module,
    '''    """Extract the exact ad-account TOTAL row without a campaign breakdown."""
''',
    '''    """Extract direct ad-account TOTAL spend without a campaign breakdown."""
''',
)
replace_once(
    module,
    '''    """Read the exact All Ads total shown at ad-account level."""
''',
    '''    """Read direct All Ads spend; commercial metrics come from campaigns."""
''',
)
replace_once(
    module,
    '''def total_snapshot_is_authoritative(
''',
    '''def merge_direct_spend_with_campaign_metrics(
    direct_account_metrics: dict[str, Any],
    campaign_rows: list[dict[str, Any]],
) -> dict[str, int | float | None]:
    """Build All Ads totals from the only authoritative fields at each level.

    Snapchat documents that direct Ad Account stats expose spend only. Purchases,
    purchase value, impressions, swipes and the remaining metrics are summed from
    the complete ``breakdown=campaign`` response. This mirrors the Ads Manager
    All Ads total without inventing a direct account conversion metric.
    """
    merged = aggregate_total_campaign_metrics(campaign_rows)
    direct_spend = _as_number(direct_account_metrics.get("spend"))
    if direct_spend is None:
        raise SnapchatNativeSyncError(
            "snapchat_account_direct_spend_missing",
            "Snapchat direct Ad Account TOTAL did not include spend.",
            status_code=502,
            retryable=True,
        )
    merged["spend"] = (
        int(direct_spend)
        if float(direct_spend).is_integer()
        else float(direct_spend)
    )
    return merged


def total_snapshot_is_authoritative(
''',
)
replace_once(
    module,
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
    module,
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
    module,
    '''def _aggregate_visible_campaigns(
''',
    '''def _mask_pending_platform_commercial_metrics(
    result: dict[str, Any],
) -> dict[str, Any]:
    """Never expose legacy HOUR conversions as the new TOTAL snapshot."""
    commercial_nulls = {
        "orders": None,
        "sales_sar": None,
        "sales_native": None,
        "roas": None,
        "cpa_sar": None,
        "cpa_native": None,
        "result_source": RESULT_SOURCE_PLATFORM,
        "commercial_metrics_source": "snapchat_total_snapshot_pending",
        "profitability": None,
    }
    totals = dict(result.get("totals") or {})
    totals.update(commercial_nulls)
    result["totals"] = totals
    result["daily"] = [
        {**dict(row), **commercial_nulls}
        for row in (result.get("daily") or [])
        if isinstance(row, dict)
    ]
    result["campaigns"] = [
        {**dict(row), **commercial_nulls}
        for row in (result.get("campaigns") or [])
        if isinstance(row, dict)
    ]
    accounts = []
    for row in result.get("accounts") or []:
        accounts.append({**dict(row), **commercial_nulls})
    result["accounts"] = accounts
    return result


def _aggregate_visible_campaigns(
''',
)
replace_once(
    module,
    '''        result.setdefault("policy", {}).update({
            "platform_source_isolated": True,
            "salla_metrics_applied_to_platform": False,
        })
        return result
''',
    '''        result.setdefault("policy", {}).update({
            "platform_source_isolated": True,
            "salla_metrics_applied_to_platform": False,
            "legacy_hour_conversions_hidden_while_pending": True,
        })
        return _mask_pending_platform_commercial_metrics(result)
''',
)
replace_once(
    module,
    '''        "platform_total_snapshot_ready": bool(account_rows and campaign_rows),
        "platform_direct_account_total_ready": bool(account_rows),
        "platform_source_isolated": True,
''',
    '''        "platform_total_snapshot_ready": bool(account_rows and campaign_rows),
        "platform_direct_account_total_ready": bool(account_rows),
        "platform_source_isolated": True,
        "account_spend_source": "direct_ad_account_total",
        "account_commercial_totals_source": "complete_campaign_breakdown_sum",
''',
)
replace_once(
    module,
    '''            "direct_account_total_snapshot",
''',
    '''            "campaign_breakdown_all_ads_snapshot",
''',
)
replace_once(
    module,
    '''    "load_platform_total_rows",
    "persist_account_total_day",
''',
    '''    "load_platform_total_rows",
    "merge_direct_spend_with_campaign_metrics",
    "persist_account_total_day",
''',
)

backend_test = "backend/tests/test_snapchat_platform_source_integrity_v1.py"
text = Path(backend_test).read_text(encoding="utf-8")
text = text.replace(
    '''    extract_account_total_metrics,
    total_snapshot_is_authoritative,
''',
    '''    extract_account_total_metrics,
    merge_direct_spend_with_campaign_metrics,
    total_snapshot_is_authoritative,
''',
    1,
)
text = text.replace(
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
            "total_stat": {
                "stats": {"spend": 658_770_000},
            },
        }],
    }
    metrics, errors, successful = extract_account_total_metrics(payload)
    assert errors == []
    assert successful == 1
    assert metrics["spend"] == 658_770_000
    assert metrics["conversion_purchases"] == 0
    assert metrics["conversion_purchases_value"] == 0


def test_direct_total_rejects_missing_spend():
    metrics, errors, successful = extract_account_total_metrics({
        "total_stats": [{
            "sub_request_status": "SUCCESS",
            "total_stat": {
                "stats": {
                    "conversion_purchases": 25,
                    "conversion_purchases_value": 1_006_990_000,
                },
            },
        }],
    })
    assert successful == 1
    assert metrics is None
    assert errors[0]["code"] == "snapchat_account_direct_total_fields_missing"


def test_all_ads_merges_direct_spend_with_complete_campaign_commercial_totals():
    direct = {"spend": 658_770_000}
    rows = [
        {
            "campaign_id": "campaign-1",
            "metrics": {
                "spend": 332_980_000,
                "conversion_purchases": 13,
                "conversion_purchases_value": 500_000_000,
                "impressions": 159_575,
                "swipes": 2_087,
            },
        },
        {
            "campaign_id": "campaign-2",
            "metrics": {
                "spend": 180_010_000,
                "conversion_purchases": 4,
                "conversion_purchases_value": 250_000_000,
                "impressions": 134_780,
                "swipes": 2_147,
            },
        },
        {
            "campaign_id": "campaign-3",
            "metrics": {
                "spend": 47_980_000,
                "conversion_purchases": 3,
                "conversion_purchases_value": 256_990_000,
                "impressions": 13_434,
                "swipes": 226,
            },
        },
        {
            "campaign_id": "campaign-rest",
            "metrics": {
                "spend": 97_800_000,
                "conversion_purchases": 5,
                "conversion_purchases_value": 0,
            },
        },
    ]
    merged = merge_direct_spend_with_campaign_metrics(direct, rows)
    assert merged["spend"] == 658_770_000
    assert merged["conversion_purchases"] == 25
    assert merged["conversion_purchases_value"] == 1_006_990_000
    assert merged["impressions"] == 307_789
    assert merged["swipes"] == 4_460
''',
    1,
)
text = text.replace(
    '''    assert source == "direct_account_total_snapshot"
''',
    '''    assert source == "campaign_breakdown_all_ads_snapshot"
''',
    1,
)
Path(backend_test).write_text(text, encoding="utf-8")

ui = "frontend/src/components/marketing/SnapchatOrderSourceAudit.jsx"
replace_once(
    ui,
    '''                            title="مشتريات Snapchat — كل الحساب"
                            value={summary.platform_attributed_purchases}
                            note={`لا يتأثر بفلتر الحملات؛ مجموع صفوف الحملات ${summary.platform_campaign_purchases ?? "—"}`}
''',
    '''                            title="مشتريات Snapchat — جميع الحملات"
                            value={summary.platform_attributed_purchases}
                            note={`مجموع breakdown الحملات الكامل؛ الصفوف الظاهرة ${summary.platform_campaign_purchases ?? "—"}`}
''',
)

page = "frontend/src/pages/MarketingPlatformWorkspace.jsx"
replace_once(
    page,
    '''export { isMarketingPerformanceProvider as isMarketingPlatformProvider };
''',
    '''export { isMarketingPerformanceProvider as isMarketingPlatformProvider };

export function isSnapchatPlatformSnapshotPending(platform, data) {
    return platform === "snapchat"
        && data?.result_source === "platform"
        && data?.source?.platform_total_snapshot_ready === false;
}
''',
)
replace_once(
    page,
    '''    const totals = data?.totals || {};
    const connection = data?.connection || {};
''',
    '''    const totals = data?.totals || {};
    const connection = data?.connection || {};
    const platformSnapshotPending = isSnapchatPlatformSnapshotPending(
        platform,
        data,
    );
''',
)
replace_once(
    page,
    '''            <AdsPerformanceExplorer
                totals={totals}
''',
    '''            {platformSnapshotPending && (
                <div
                    className="rounded-2xl border border-amber-300 bg-amber-50 p-4 text-sm font-bold leading-6 text-amber-950"
                    data-testid="snapchat-platform-total-pending"
                >
                    <WarningCircle size={20} weight="fill" className="ml-2 inline" />
                    نتائج Snapchat الكاملة قيد المزامنة. أخفى ميزان أرقام التحويل القديمة بدل عرض تقرير جزئي؛ أعد التحديث بعد دورة المزامنة التالية.
                </div>
            )}

            <AdsPerformanceExplorer
                totals={totals}
''',
)

page_test = "frontend/src/pages/MarketingPlatformWorkspacePlatformSnapshot.test.js"
Path(page_test).write_text(
    '''import { isSnapchatPlatformSnapshotPending } from "./MarketingPlatformWorkspace";\n\n'
    'test("flags only an incomplete Snapchat platform TOTAL snapshot", () => {\n'
    '  expect(isSnapchatPlatformSnapshotPending("snapchat", {\n'
    '    result_source: "platform",\n'
    '    source: { platform_total_snapshot_ready: false },\n'
    '  })).toBe(true);\n'
    '  expect(isSnapchatPlatformSnapshotPending("snapchat", {\n'
    '    result_source: "salla",\n'
    '    source: { platform_total_snapshot_ready: false },\n'
    '  })).toBe(false);\n'
    '  expect(isSnapchatPlatformSnapshotPending("snapchat", {\n'
    '    result_source: "platform",\n'
    '    source: { platform_total_snapshot_ready: true },\n'
    '  })).toBe(false);\n'
    '});\n'''.replace("'\n    '", ""),
    encoding="utf-8",
)

workflow = ".github/workflows/prod-snap-platform-source-isolation-v1.yml"
replace_once(
    workflow,
    '''      - "frontend/src/pages/MarketingPlatformWorkspace.jsx"
''',
    '''      - "frontend/src/pages/MarketingPlatformWorkspace.jsx"
      - "frontend/src/pages/MarketingPlatformWorkspacePlatformSnapshot.test.js"
''',
)
# The same path appears in the push section.
text = Path(workflow).read_text(encoding="utf-8")
needle = '''      - "frontend/src/pages/MarketingPlatformWorkspace.jsx"\n'''
if text.count(needle) == 1:
    text = text.replace(
        needle,
        needle + '      - "frontend/src/pages/MarketingPlatformWorkspacePlatformSnapshot.test.js"\n',
        1,
    )
Path(workflow).write_text(text, encoding="utf-8")
replace_once(
    workflow,
    '''          src/components/marketing/SnapchatOrderSourceAudit.test.jsx
''',
    '''          src/components/marketing/SnapchatOrderSourceAudit.test.jsx
          src/pages/MarketingPlatformWorkspacePlatformSnapshot.test.js
''',
)
replace_once(
    workflow,
    '''          grep -q 'all_ads_direct_account_total' backend/integrations_control_center/snapchat_platform_source_integrity.py
''',
    '''          grep -q 'all_ads_direct_account_total' backend/integrations_control_center/snapchat_platform_source_integrity.py
          grep -q 'complete_campaign_breakdown_sum' backend/integrations_control_center/snapchat_platform_source_integrity.py
          grep -q 'REQUIRED_ACCOUNT_TOTAL_FIELDS = frozenset({"spend"})' backend/integrations_control_center/snapchat_platform_source_integrity.py
''',
)
replace_once(
    workflow,
    '''          grep -q 'مشتريات Snapchat — كل الحساب' frontend/src/components/marketing/SnapchatOrderSourceAudit.jsx
''',
    '''          grep -q 'مشتريات Snapchat — جميع الحملات' frontend/src/components/marketing/SnapchatOrderSourceAudit.jsx
          grep -q 'snapchat-platform-total-pending' frontend/src/pages/MarketingPlatformWorkspace.jsx
''',
)
