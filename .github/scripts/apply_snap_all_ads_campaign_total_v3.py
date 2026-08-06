from pathlib import Path

# Applied only on the isolated PR branch; all provider operations remain read-only.


def replace_once(path: str, old: str, new: str) -> None:
    file = Path(path)
    text = file.read_text(encoding="utf-8")
    if new in text:
        return
    if old not in text:
        raise SystemExit(f"pattern not found in {path}: {old[:180]!r}")
    file.write_text(text.replace(old, new, 1), encoding="utf-8")


def replace_text(path: str, old: str, new: str, *, count: int | None = None) -> None:
    file = Path(path)
    text = file.read_text(encoding="utf-8")
    if old not in text:
        if new in text:
            return
        raise SystemExit(f"text not found in {path}: {old[:180]!r}")
    file.write_text(
        text.replace(old, new) if count is None else text.replace(old, new, count),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# One explicit contract: business/accounting stays conversion-time, while the
# Ads Manager workspace uses impression-time exactly like the merchant UI.
# ---------------------------------------------------------------------------
freshness = "backend/integrations_control_center/snapchat_freshness_impl_v6.py"
replace_once(
    freshness,
    '''# Compatibility names used by existing imports and focused contracts.
ADS_MANAGER_ACTION_REPORT_TIME = SNAPCHAT_ACTION_REPORT_TIME
ADS_MANAGER_SOURCE_MODE = SNAPCHAT_SOURCE_MODE
''',
    '''# The Riyadh Dashboard keeps conversion-time semantics. The Ads Manager
# workspace uses impression-time attribution so its date-filtered Purchases and
# Purchase Value match Snapchat Ads Manager.
ADS_MANAGER_ACTION_REPORT_TIME: Final[str] = "impression"
ADS_MANAGER_SOURCE_MODE: Final[str] = (
    "snapchat_ads_manager_account_timezone_impression_v7"
)
''',
)
replace_once(
    freshness,
    '''def install_snapchat_ads_manager_attribution() -> None:
    """Install conversion-time reporting and nested freshness capture."""
''',
    '''def install_snapchat_ads_manager_attribution() -> None:
    """Install conversion-time business reporting plus Ads Manager contracts.

    Ads Manager readers import ``ADS_MANAGER_ACTION_REPORT_TIME`` explicitly;
    these runtime assignments remain conversion-time for the Riyadh Dashboard
    and accounting projections.
    """
''',
)

# Allow account-local reporting to request a second attribution basis without
# changing the existing Riyadh business-day request.
hourly = "backend/integrations_control_center/snapchat_account_hourly_refresh.py"
replace_once(
    hourly,
    '''    start_date: date | None = None,
    end_date: date | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
''',
    '''    start_date: date | None = None,
    end_date: date | None = None,
    action_report_time: str | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
''',
)
replace_once(
    hourly,
    '''        "action_report_time": ACTION_REPORT_TIME,
''',
    '''        "action_report_time": action_report_time or ACTION_REPORT_TIME,
''',
)

# Account-local campaign/day rows and the hourly chart must use the same
# impression-time attribution. The Riyadh rows continue using conversion-time.
manager = "backend/integrations_control_center/snapchat_account_timezone_manager.py"
replace_once(
    manager,
    '''from .snapchat_active_campaign_filtering import is_active_provider_status
''',
    '''from .snapchat_active_campaign_filtering import is_active_provider_status
from .snapchat_freshness_impl_v6 import (
    ADS_MANAGER_ACTION_REPORT_TIME,
    ADS_MANAGER_SOURCE_MODE,
)
''',
)
replace_text(manager, '''    ACTION_REPORT_TIME,
''', '''''', count=1)
replace_once(
    manager,
    '''ACCOUNT_LOCAL_SOURCE_MODE = (
    "snapchat_account_hourly_campaign_breakdown_account_day_v1"
)
''',
    '''ACCOUNT_LOCAL_SOURCE_MODE = (
    f"{ADS_MANAGER_SOURCE_MODE}:account_day_v2"
)
''',
)
replace_once(
    manager,
    '''            "action_report_time": ACTION_REPORT_TIME,
''',
    '''            "action_report_time": ADS_MANAGER_ACTION_REPORT_TIME,
''',
)
replace_once(
    manager,
    '''    used_completed_hour_fallback = False
    try:
        rows, errors = await hourly._fetch_account_hours(
            context,
            client,
            access_token,
            account_id=account_id,
            request_start=request["provider_start"],
            request_end=request["provider_end"],
        )
    except SnapchatNativeSyncError as exc:
        fallback = _combined_request_window(
            start_date,
            end_date,
            timezone_name=timezone_name,
            now=now,
            include_current_hour=False,
        )
        can_retry = (
            exc.code == "snapchat_provider_http_400"
            and fallback is not None
            and fallback["provider_end"] < request["provider_end"]
        )
        if not can_retry:
            raise
        request = fallback
        used_completed_hour_fallback = True
        rows, errors = await hourly._fetch_account_hours(
            context,
            client,
            access_token,
            account_id=account_id,
            request_start=request["provider_start"],
            request_end=request["provider_end"],
        )

    business_campaigns, business_accounts = _campaign_day_buckets(
        rows,
        timezone_name=BUSINESS_TIMEZONE,
        start_date=start_date,
        end_date=end_date,
    )
    local_campaigns, local_accounts = _campaign_day_buckets(
        rows,
        timezone_name=timezone_name,
        start_date=request["account_local_from"],
        end_date=request["account_local_to"],
    )
''',
    '''    used_completed_hour_fallback = False

    async def fetch_both(window: dict[str, datetime]):
        business_rows, business_errors = await hourly._fetch_account_hours(
            context,
            client,
            access_token,
            account_id=account_id,
            request_start=window["provider_start"],
            request_end=window["provider_end"],
            action_report_time=hourly.ACTION_REPORT_TIME,
        )
        account_local_rows, account_local_errors = await hourly._fetch_account_hours(
            context,
            client,
            access_token,
            account_id=account_id,
            request_start=window["provider_start"],
            request_end=window["provider_end"],
            action_report_time=ADS_MANAGER_ACTION_REPORT_TIME,
        )
        return (
            business_rows,
            account_local_rows,
            [*business_errors, *account_local_errors],
        )

    try:
        business_rows, account_local_rows, errors = await fetch_both(request)
    except SnapchatNativeSyncError as exc:
        fallback = _combined_request_window(
            start_date,
            end_date,
            timezone_name=timezone_name,
            now=now,
            include_current_hour=False,
        )
        can_retry = (
            exc.code == "snapchat_provider_http_400"
            and fallback is not None
            and fallback["provider_end"] < request["provider_end"]
        )
        if not can_retry:
            raise
        request = fallback
        used_completed_hour_fallback = True
        business_rows, account_local_rows, errors = await fetch_both(request)

    business_campaigns, business_accounts = _campaign_day_buckets(
        business_rows,
        timezone_name=BUSINESS_TIMEZONE,
        start_date=start_date,
        end_date=end_date,
    )
    local_campaigns, local_accounts = _campaign_day_buckets(
        account_local_rows,
        timezone_name=timezone_name,
        start_date=request["account_local_from"],
        end_date=request["account_local_to"],
    )
''',
)
replace_once(
    manager,
    '''        "current_hour_included": not used_completed_hour_fallback,
        "source_only": True,
''',
    '''        "current_hour_included": not used_completed_hour_fallback,
        "business_action_report_time": hourly.ACTION_REPORT_TIME,
        "account_local_action_report_time": ADS_MANAGER_ACTION_REPORT_TIME,
        "source_only": True,
''',
)
replace_once(
    manager,
    '''            "date_timezone": timezone_name,
        },
''',
    '''            "date_timezone": timezone_name,
            "source_mode": ACCOUNT_LOCAL_SOURCE_MODE,
        },
''',
)
replace_once(
    manager,
    '''            "commercial_results_source": (
                "unified_orders:salla_exact_account_campaign_match"
                if result_source == RESULT_SOURCE_SALLA
                else "snapchat_conversion_reporting"
            ),
''',
    '''            "commercial_results_source": (
                "unified_orders:salla_exact_account_campaign_match"
                if result_source == RESULT_SOURCE_SALLA
                else "snapchat_ads_manager_impression_reporting"
            ),
            "account_local_action_report_time": ADS_MANAGER_ACTION_REPORT_TIME,
''',
)

# Persist only the impression-time fetch into the Ads Manager hourly chart.
hourly_chart = "backend/integrations_control_center/snapchat_account_hourly_chart.py"
replace_once(
    hourly_chart,
    '''from . import snapchat_account_timezone_manager as account_report
''',
    '''from . import snapchat_account_timezone_manager as account_report
from .snapchat_freshness_impl_v6 import (
    ADS_MANAGER_ACTION_REPORT_TIME,
    ADS_MANAGER_SOURCE_MODE,
)
''',
)
replace_text(hourly_chart, '''    ACTION_REPORT_TIME,
''', '''''', count=1)
replace_once(
    hourly_chart,
    '''ACCOUNT_LOCAL_HOURLY_SOURCE_MODE = (
    "snapchat_account_campaign_breakdown_account_hour_v1"
)
''',
    '''ACCOUNT_LOCAL_HOURLY_SOURCE_MODE = (
    f"{ADS_MANAGER_SOURCE_MODE}:account_hour_v2"
)
''',
)
replace_once(
    hourly_chart,
    '''            "action_report_time": ACTION_REPORT_TIME,
''',
    '''            "action_report_time": ADS_MANAGER_ACTION_REPORT_TIME,
''',
)
replace_once(
    hourly_chart,
    '''            capture = _CAPTURE_CONTEXT.get()
            if capture and rows:
''',
    '''            capture = _CAPTURE_CONTEXT.get()
            if (
                capture
                and rows
                and kwargs.get("action_report_time")
                == ADS_MANAGER_ACTION_REPORT_TIME
            ):
''',
)
replace_once(
    hourly_chart,
    '''            "date_timezone": timezone_name,
        },
''',
    '''            "date_timezone": timezone_name,
            "source_mode": ACCOUNT_LOCAL_HOURLY_SOURCE_MODE,
        },
''',
)
replace_once(
    hourly_chart,
    '''        "hourly_result_source": result_source,
        "salla_hourly_attribution": salla_coverage,
''',
    '''        "hourly_result_source": result_source,
        "hourly_action_report_time": ADS_MANAGER_ACTION_REPORT_TIME,
        "salla_hourly_attribution": salla_coverage,
''',
)

# TOTAL account and campaign snapshots use impression-time, matching Ads Manager.
platform = "backend/integrations_control_center/snapchat_platform_source_integrity.py"
replace_once(
    platform,
    '''from .snapchat_campaign_result_source_routes import RESULT_SOURCE_PLATFORM
''',
    '''from .snapchat_campaign_result_source_routes import RESULT_SOURCE_PLATFORM
from .snapchat_freshness_impl_v6 import (
    ADS_MANAGER_ACTION_REPORT_TIME,
    ADS_MANAGER_SOURCE_MODE,
)
''',
)
replace_text(platform, '''    ACTION_REPORT_TIME,
''', '''''', count=1)
replace_once(
    platform,
    '''PLATFORM_TOTAL_SOURCE_MODE = (
    "snapchat_account_direct_total_plus_campaign_breakdown_account_local_v2"
)
''',
    '''PLATFORM_TOTAL_SOURCE_MODE = (
    f"{ADS_MANAGER_SOURCE_MODE}:account_total_v3"
)
''',
)
replace_text(
    platform,
    '''"action_report_time": ACTION_REPORT_TIME''',
    '''"action_report_time": ADS_MANAGER_ACTION_REPORT_TIME''',
)
replace_once(
    platform,
    '''def _aggregate_visible_campaigns(
''',
    '''def _mask_pending_platform_commercial_metrics(
    result: dict[str, Any],
) -> dict[str, Any]:
    """Do not expose legacy conversion-time results while TOTAL is pending."""
    commercial_nulls = {
        "orders": None,
        "sales_sar": None,
        "sales_native": None,
        "roas": None,
        "cpa_sar": None,
        "cpa_native": None,
        "result_source": RESULT_SOURCE_PLATFORM,
        "commercial_metrics_source": "snapchat_impression_total_pending",
        "profitability": None,
    }
    result["totals"] = {**dict(result.get("totals") or {}), **commercial_nulls}
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
    result["accounts"] = [
        {**dict(row), **commercial_nulls}
        for row in (result.get("accounts") or [])
        if isinstance(row, dict)
    ]
    return result


def _aggregate_visible_campaigns(
''',
)
replace_once(
    platform,
    '''    if not account_rows and not campaign_rows:
''',
    '''    if not account_rows or not campaign_rows:
''',
)
replace_once(
    platform,
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
    platform,
    '''        "platform_source_isolated": True,
        "platform_totals_scope": totals_scope,
''',
    '''        "platform_source_isolated": True,
        "platform_action_report_time": ADS_MANAGER_ACTION_REPORT_TIME,
        "platform_totals_scope": totals_scope,
''',
)

# Ad Squad and Ad performance use the same attribution basis as campaigns.
for path, old_mode, new_mode, entity_type in (
    (
        "backend/integrations_control_center/snapchat_adsquad_performance.py",
        'ADSQUAD_SOURCE_MODE = "snapchat_campaign_stats_adsquad_account_day_v1"',
        'ADSQUAD_SOURCE_MODE = f"{ADS_MANAGER_SOURCE_MODE}:ad_squad_day_v2"',
        "ad_squad",
    ),
    (
        "backend/integrations_control_center/snapchat_ad_performance.py",
        'AD_SOURCE_MODE = "snapchat_campaign_stats_ad_account_day_v1"',
        'AD_SOURCE_MODE = f"{ADS_MANAGER_SOURCE_MODE}:ad_day_v2"',
        "ad",
    ),
):
    replace_once(
        path,
        '''from .snapchat_active_campaign_filtering import (
''',
        '''from .snapchat_freshness_impl_v6 import (
    ADS_MANAGER_ACTION_REPORT_TIME,
    ADS_MANAGER_SOURCE_MODE,
)
from .snapchat_active_campaign_filtering import (
''',
    )
    replace_text(path, '''    ACTION_REPORT_TIME,
''', '''''', count=1)
    replace_once(path, old_mode + "\n", new_mode + "\n")
    replace_text(
        path,
        '''"action_report_time": ACTION_REPORT_TIME''',
        '''"action_report_time": ADS_MANAGER_ACTION_REPORT_TIME''',
    )
    replace_once(
        path,
        f'''            "entity_type": "{entity_type}",
            "date": date_query,
            "date_timezone": timezone_name,
''',
        f'''            "entity_type": "{entity_type}",
            "date": date_query,
            "date_timezone": timezone_name,
            "source_mode": {"ADSQUAD_SOURCE_MODE" if entity_type == "ad_squad" else "AD_SOURCE_MODE"},
''',
    )
    replace_once(
        path,
        '''            "commercial_results_source": "snapchat_conversion_reporting",
''',
        '''            "commercial_results_source": "snapchat_ads_manager_impression_reporting",
            "action_report_time": ADS_MANAGER_ACTION_REPORT_TIME,
''',
    )

# Focused backend contract proves every Ads Manager entity level is aligned.
backend_test = "backend/tests/test_snapchat_platform_source_integrity_v1.py"
text = Path(backend_test).read_text(encoding="utf-8")
if "test_ads_manager_entity_levels_use_impression_time" not in text:
    text += '''


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
'''
Path(backend_test).write_text(text, encoding="utf-8")

# Pending TOTAL snapshots display an explicit state instead of old conversion rows.
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
                    نتائج Snapchat المطابقة لمدير الإعلانات قيد المزامنة. أخفى ميزان أرقام التحويل القديمة بدل عرض تقرير جزئي.
                </div>
            )}

            <AdsPerformanceExplorer
                totals={totals}
''',
)
Path(
    "frontend/src/pages/MarketingPlatformWorkspacePlatformSnapshot.test.js"
).write_text(
    '''import { isSnapchatPlatformSnapshotPending } from "./MarketingPlatformWorkspace";\n\ntest("flags only an incomplete Snapchat platform TOTAL snapshot", () => {\n  expect(isSnapchatPlatformSnapshotPending("snapchat", {\n    result_source: "platform",\n    source: { platform_total_snapshot_ready: false },\n  })).toBe(true);\n  expect(isSnapchatPlatformSnapshotPending("snapchat", {\n    result_source: "salla",\n    source: { platform_total_snapshot_ready: false },\n  })).toBe(false);\n  expect(isSnapchatPlatformSnapshotPending("snapchat", {\n    result_source: "platform",\n    source: { platform_total_snapshot_ready: true },\n  })).toBe(false);\n});\n''',
    encoding="utf-8",
)

# Null provider attribution must never be rendered as a real zero.
audit = "frontend/src/components/marketing/SnapchatOrderSourceAudit.jsx"
replace_once(
    audit,
    '''function numeric(value) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed.toLocaleString("en-US") : "0";
}
''',
    '''function numeric(value) {
    if (value === null || value === undefined || value === "") return "—";
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed.toLocaleString("en-US") : "—";
}
''',
)

# Extend the permanent CI contract.
workflow = ".github/workflows/prod-snap-platform-source-isolation-v1.yml"
text = Path(workflow).read_text(encoding="utf-8")
for section_anchor in (
    '      - "frontend/src/pages/MarketingPlatformWorkspace.jsx"\n',
):
    addition = section_anchor + '      - "frontend/src/pages/MarketingPlatformWorkspacePlatformSnapshot.test.js"\n'
    while section_anchor in text and addition not in text:
        text = text.replace(section_anchor, addition, 1)
text = text.replace(
    '''          src/components/marketing/SnapchatOrderSourceAudit.test.jsx
''',
    '''          src/components/marketing/SnapchatOrderSourceAudit.test.jsx
          src/pages/MarketingPlatformWorkspacePlatformSnapshot.test.js
''',
    1,
)
text = text.replace(
    '''          grep -q 'total_snapshot_is_authoritative' backend/integrations_control_center/snapchat_platform_source_integrity.py
''',
    '''          grep -q 'total_snapshot_is_authoritative' backend/integrations_control_center/snapchat_platform_source_integrity.py
          grep -q 'ADS_MANAGER_ACTION_REPORT_TIME' backend/integrations_control_center/snapchat_platform_source_integrity.py
          grep -q 'legacy_hour_conversions_hidden_while_pending' backend/integrations_control_center/snapchat_platform_source_integrity.py
''',
    1,
)
text = text.replace(
    '''          grep -q 'مشتريات Snapchat — كل الحساب' frontend/src/components/marketing/SnapchatOrderSourceAudit.jsx
''',
    '''          grep -q 'مشتريات Snapchat — كل الحساب' frontend/src/components/marketing/SnapchatOrderSourceAudit.jsx
          grep -q 'snapchat-platform-total-pending' frontend/src/pages/MarketingPlatformWorkspace.jsx
''',
    1,
)
Path(workflow).write_text(text, encoding="utf-8")
