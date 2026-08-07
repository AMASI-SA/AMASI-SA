from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    if new in text:
        return
    if old not in text:
        raise SystemExit(f"pattern not found in {path}: {old[:180]!r}")
    p.write_text(text.replace(old, new, 1), encoding="utf-8")


def append_once(path: str, marker: str, addition: str) -> None:
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    if addition in text:
        return
    if marker not in text:
        raise SystemExit(f"marker not found in {path}: {marker[:180]!r}")
    p.write_text(text.replace(marker, addition + marker, 1), encoding="utf-8")


adsquad = "backend/integrations_control_center/snapchat_adsquad_performance.py"
replace_once(
    adsquad,
    '''from .snapchat_freshness_impl_v6 import (
    ADS_MANAGER_ACTION_REPORT_TIME,
    ADS_MANAGER_SOURCE_MODE,
)
''',
    '''from .snapchat_freshness_impl_v6 import (
    ADS_MANAGER_ACTION_REPORT_TIME,
    ADS_MANAGER_DEFAULT_ACTION_REPORT_TIME,
    ADS_MANAGER_SUPPORTED_ACTION_REPORT_TIMES,
    ads_manager_source_mode,
    normalize_ads_manager_action_report_time,
)
''',
)
replace_once(
    adsquad,
    '''ADSQUAD_SOURCE_MODE = f"{ADS_MANAGER_SOURCE_MODE}:ad_squad_day_v2"
ADSQUAD_REFRESH_STATE_COLLECTION = "mezan_snapchat_adsquad_refresh_state_v1"
''',
    '''def adsquad_source_mode(action_report_time: Any) -> str:
    return f"{ads_manager_source_mode(action_report_time)}:ad_squad_day_v3"


ADSQUAD_SOURCE_MODE = adsquad_source_mode(ADS_MANAGER_DEFAULT_ACTION_REPORT_TIME)
ADSQUAD_REFRESH_SOURCE_MODE = "snapchat_ads_manager_dual_attribution_ad_squad_v1"
ADSQUAD_REFRESH_STATE_COLLECTION = "mezan_snapchat_adsquad_refresh_state_v1"
''',
)
replace_once(
    adsquad,
    '''    request_end: datetime,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
''',
    '''    request_end: datetime,
    action_report_time: str = ADS_MANAGER_DEFAULT_ACTION_REPORT_TIME,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
''',
)
replace_once(
    adsquad,
    '''        "action_report_time": ADS_MANAGER_ACTION_REPORT_TIME,
''',
    '''        "action_report_time": normalize_ads_manager_action_report_time(action_report_time),
''',
)
replace_once(
    adsquad,
    '''    bucket: dict[str, Any],
) -> None:
''',
    '''    bucket: dict[str, Any],
    action_report_time: str,
) -> None:
''',
)
replace_once(
    adsquad,
    '''            "action_report_time": ADS_MANAGER_ACTION_REPORT_TIME,
''',
    '''            "action_report_time": action_report_time,
''',
)
replace_once(
    adsquad,
    '''        "source_mode": ADSQUAD_SOURCE_MODE,
''',
    '''        "action_report_time": action_report_time,
        "source_mode": adsquad_source_mode(action_report_time),
''',
)
replace_once(
    adsquad,
    '''            "date": date_string,
            "attribution_model": ATTRIBUTION_MODEL,
        },
''',
    '''            "date": date_string,
            "attribution_model": ATTRIBUTION_MODEL,
            "action_report_time": action_report_time,
        },
''',
)
replace_once(
    adsquad,
    '''    return (
        now - observed.astimezone(timezone.utc)
    ).total_seconds() < ADSQUAD_REFRESH_INTERVAL_SECONDS
''',
    '''    return (
        _text((row or {}).get("source_mode")) == ADSQUAD_REFRESH_SOURCE_MODE
        and (now - observed.astimezone(timezone.utc)).total_seconds()
        < ADSQUAD_REFRESH_INTERVAL_SECONDS
    )
''',
)
replace_once(
    adsquad,
    '''    rows: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    calls_before = context.provider_calls
    for campaign in campaigns:
        campaign_id = _text(campaign.get("external_id"))
        if not campaign_id:
            continue
        try:
            campaign_rows, campaign_errors = await _fetch_campaign_adsquad_hours(
                context,
                client,
                access_token,
                campaign_id=campaign_id,
                request_start=request["provider_start"],
                request_end=request["provider_end"],
            )
            rows.extend(campaign_rows)
            errors.extend(campaign_errors)
        except SnapchatNativeSyncError as exc:
            if exc.code == "snapchat_needs_reauth":
                raise
            errors.append({
                "kind": "adsquad_stats",
                "campaign_id": campaign_id,
                "code": exc.code,
                "message": exc.message[:300],
                "retryable": bool(exc.retryable),
            })
    business = _day_buckets(
        rows,
        timezone_name=BUSINESS_TIMEZONE,
        start_date=start_date,
        end_date=end_date,
    )
    local = _day_buckets(
        rows,
        timezone_name=timezone_name,
        start_date=request["account_local_from"],
        end_date=request["account_local_to"],
    )
''',
    '''    rows_by_mode: dict[str, list[dict[str, Any]]] = {
        mode: [] for mode in ADS_MANAGER_SUPPORTED_ACTION_REPORT_TIMES
    }
    errors: list[dict[str, Any]] = []
    calls_before = context.provider_calls
    for campaign in campaigns:
        campaign_id = _text(campaign.get("external_id"))
        if not campaign_id:
            continue
        for action_report_time in ADS_MANAGER_SUPPORTED_ACTION_REPORT_TIMES:
            try:
                campaign_rows, campaign_errors = await _fetch_campaign_adsquad_hours(
                    context,
                    client,
                    access_token,
                    campaign_id=campaign_id,
                    request_start=request["provider_start"],
                    request_end=request["provider_end"],
                    action_report_time=action_report_time,
                )
                rows_by_mode[action_report_time].extend(campaign_rows)
                errors.extend({
                    **error,
                    "action_report_time": action_report_time,
                } for error in campaign_errors)
            except SnapchatNativeSyncError as exc:
                if exc.code == "snapchat_needs_reauth":
                    raise
                errors.append({
                    "kind": "adsquad_stats",
                    "campaign_id": campaign_id,
                    "action_report_time": action_report_time,
                    "code": exc.code,
                    "message": exc.message[:300],
                    "retryable": bool(exc.retryable),
                })
    business = _day_buckets(
        rows_by_mode[ADS_MANAGER_DEFAULT_ACTION_REPORT_TIME],
        timezone_name=BUSINESS_TIMEZONE,
        start_date=start_date,
        end_date=end_date,
    )
    local_by_mode = {
        mode: _day_buckets(
            rows_by_mode[mode],
            timezone_name=timezone_name,
            start_date=request["account_local_from"],
            end_date=request["account_local_to"],
        )
        for mode in ADS_MANAGER_SUPPORTED_ACTION_REPORT_TIMES
    }
''',
)
replace_once(
    adsquad,
    '''            bucket=bucket,
        )
        saved += 1
    for (campaign_id, adsquad_id, date_string), bucket in sorted(local.items()):
        await _upsert_projection(
            context,
            collection_name=SNAPCHAT_ACCOUNT_LOCAL_PERFORMANCE_COLLECTION,
            account=account,
            timezone_name=timezone_name,
            stored_granularity="ACCOUNT_LOCAL_DAY",
            campaign_id=campaign_id,
            adsquad_id=adsquad_id,
            date_string=date_string,
            bucket=bucket,
        )
        saved += 1
''',
    '''            bucket=bucket,
            action_report_time=ADS_MANAGER_DEFAULT_ACTION_REPORT_TIME,
        )
        saved += 1
    account_local_saved = 0
    for action_report_time, local in local_by_mode.items():
        for (campaign_id, adsquad_id, date_string), bucket in sorted(local.items()):
            await _upsert_projection(
                context,
                collection_name=SNAPCHAT_ACCOUNT_LOCAL_PERFORMANCE_COLLECTION,
                account=account,
                timezone_name=timezone_name,
                stored_granularity="ACCOUNT_LOCAL_DAY",
                campaign_id=campaign_id,
                adsquad_id=adsquad_id,
                date_string=date_string,
                bucket=bucket,
                action_report_time=action_report_time,
            )
            saved += 1
            account_local_saved += 1
''',
)
replace_once(
    adsquad,
    '''                "source_mode": ADSQUAD_SOURCE_MODE,
''',
    '''                "source_mode": ADSQUAD_REFRESH_SOURCE_MODE,
''',
)
replace_once(
    adsquad,
    '''        "source_mode": ADSQUAD_SOURCE_MODE,
        "skipped": False,
''',
    '''        "source_mode": ADSQUAD_REFRESH_SOURCE_MODE,
        "supported_action_report_times": list(ADS_MANAGER_SUPPORTED_ACTION_REPORT_TIMES),
        "skipped": False,
''',
)
replace_once(
    adsquad,
    '''        "account_local_rows_saved": len(local),
''',
    '''        "account_local_rows_saved": account_local_saved,
''',
)
replace_once(
    adsquad,
    '''    active_campaigns_only: bool = False,
    sort_by: str = "orders",
''',
    '''    active_campaigns_only: bool = False,
    sort_by: str = "orders",
    action_report_time: str = ADS_MANAGER_DEFAULT_ACTION_REPORT_TIME,
''',
)
replace_once(
    adsquad,
    '''    current = _aware_now(now())
''',
    '''    current = _aware_now(now())
    action_report_time = normalize_ads_manager_action_report_time(action_report_time)
''',
)
replace_once(
    adsquad,
    '''            "source_mode": ADSQUAD_SOURCE_MODE,
        },
''',
    '''            "source_mode": adsquad_source_mode(action_report_time),
            "action_report_time": action_report_time,
        },
''',
)
replace_once(
    adsquad,
    '''        "result_source": "platform",
        "supported_result_sources": ["platform"],
''',
    '''        "result_source": "platform",
        "action_report_time": action_report_time,
        "supported_action_report_times": list(ADS_MANAGER_SUPPORTED_ACTION_REPORT_TIMES),
        "supported_result_sources": ["platform"],
''',
)
replace_once(
    adsquad,
    '''            "source_mode": ADSQUAD_SOURCE_MODE,
''',
    '''            "source_mode": adsquad_source_mode(action_report_time),
''',
)
replace_once(
    adsquad,
    '''            "commercial_results_source": "snapchat_ads_manager_impression_reporting",
            "action_report_time": ADS_MANAGER_ACTION_REPORT_TIME,
''',
    '''            "commercial_results_source": f"snapchat_ads_manager_{action_report_time}_reporting",
            "action_report_time": action_report_time,
''',
)
replace_once(
    adsquad,
    '''        sort_by: str = Query(default="orders", pattern="^(orders|spend|newest|active)$"),
        user: dict = Depends(current_user),
''',
    '''        sort_by: str = Query(default="orders", pattern="^(orders|spend|newest|active)$"),
        action_report_time: str = Query(default=ADS_MANAGER_DEFAULT_ACTION_REPORT_TIME, pattern="^(conversion|impression)$"),
        user: dict = Depends(current_user),
''',
)
replace_once(
    adsquad,
    '''                sort_by=sort_by,
            )
''',
    '''                sort_by=sort_by,
                action_report_time=action_report_time,
            )
''',
)

ad = "backend/integrations_control_center/snapchat_ad_performance.py"
replace_once(
    ad,
    '''from .snapchat_freshness_impl_v6 import (
    ADS_MANAGER_ACTION_REPORT_TIME,
    ADS_MANAGER_SOURCE_MODE,
)
''',
    '''from .snapchat_freshness_impl_v6 import (
    ADS_MANAGER_ACTION_REPORT_TIME,
    ADS_MANAGER_DEFAULT_ACTION_REPORT_TIME,
    ADS_MANAGER_SUPPORTED_ACTION_REPORT_TIMES,
    ads_manager_source_mode,
    normalize_ads_manager_action_report_time,
)
''',
)
replace_once(
    ad,
    '''AD_SOURCE_MODE = f"{ADS_MANAGER_SOURCE_MODE}:ad_day_v2"
AD_REFRESH_STATE_COLLECTION = "mezan_snapchat_ad_refresh_state_v1"
''',
    '''def ad_source_mode(action_report_time: Any) -> str:
    return f"{ads_manager_source_mode(action_report_time)}:ad_day_v3"


AD_SOURCE_MODE = ad_source_mode(ADS_MANAGER_DEFAULT_ACTION_REPORT_TIME)
AD_REFRESH_SOURCE_MODE = "snapchat_ads_manager_dual_attribution_ad_v1"
AD_REFRESH_STATE_COLLECTION = "mezan_snapchat_ad_refresh_state_v1"
''',
)
replace_once(
    ad,
    '''    request_end: datetime,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
''',
    '''    request_end: datetime,
    action_report_time: str = ADS_MANAGER_DEFAULT_ACTION_REPORT_TIME,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
''',
)
replace_once(
    ad,
    '''        "action_report_time": ADS_MANAGER_ACTION_REPORT_TIME,
''',
    '''        "action_report_time": normalize_ads_manager_action_report_time(action_report_time),
''',
)
replace_once(
    ad,
    '''    bucket: dict[str, Any],
) -> None:
''',
    '''    bucket: dict[str, Any],
    action_report_time: str,
) -> None:
''',
)
replace_once(
    ad,
    '''            "action_report_time": ADS_MANAGER_ACTION_REPORT_TIME,
''',
    '''            "action_report_time": action_report_time,
''',
)
replace_once(
    ad,
    '''        "source_mode": AD_SOURCE_MODE,
''',
    '''        "action_report_time": action_report_time,
        "source_mode": ad_source_mode(action_report_time),
''',
)
replace_once(
    ad,
    '''            "date": date_string,
            "attribution_model": ATTRIBUTION_MODEL,
        },
''',
    '''            "date": date_string,
            "attribution_model": ATTRIBUTION_MODEL,
            "action_report_time": action_report_time,
        },
''',
)
replace_once(
    ad,
    '''    return (
        now - observed.astimezone(timezone.utc)
    ).total_seconds() < AD_REFRESH_INTERVAL_SECONDS
''',
    '''    return (
        _text((row or {}).get("source_mode")) == AD_REFRESH_SOURCE_MODE
        and (now - observed.astimezone(timezone.utc)).total_seconds()
        < AD_REFRESH_INTERVAL_SECONDS
    )
''',
)
replace_once(
    ad,
    '''    rows: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    calls_before = context.provider_calls
    for campaign in campaigns:
        campaign_id = _text(campaign.get("external_id"))
        if not campaign_id:
            continue
        try:
            campaign_rows, campaign_errors = await _fetch_campaign_ad_hours(
                context,
                client,
                access_token,
                campaign_id=campaign_id,
                request_start=request["provider_start"],
                request_end=request["provider_end"],
            )
            rows.extend(campaign_rows)
            errors.extend(campaign_errors)
        except SnapchatNativeSyncError as exc:
            if exc.code == "snapchat_needs_reauth":
                raise
            errors.append({
                "kind": "ad_stats",
                "campaign_id": campaign_id,
                "code": exc.code,
                "message": exc.message[:300],
                "retryable": bool(exc.retryable),
            })
    business = _day_buckets(
        rows,
        timezone_name=BUSINESS_TIMEZONE,
        start_date=start_date,
        end_date=end_date,
    )
    local = _day_buckets(
        rows,
        timezone_name=timezone_name,
        start_date=request["account_local_from"],
        end_date=request["account_local_to"],
    )
''',
    '''    rows_by_mode: dict[str, list[dict[str, Any]]] = {
        mode: [] for mode in ADS_MANAGER_SUPPORTED_ACTION_REPORT_TIMES
    }
    errors: list[dict[str, Any]] = []
    calls_before = context.provider_calls
    for campaign in campaigns:
        campaign_id = _text(campaign.get("external_id"))
        if not campaign_id:
            continue
        for action_report_time in ADS_MANAGER_SUPPORTED_ACTION_REPORT_TIMES:
            try:
                campaign_rows, campaign_errors = await _fetch_campaign_ad_hours(
                    context,
                    client,
                    access_token,
                    campaign_id=campaign_id,
                    request_start=request["provider_start"],
                    request_end=request["provider_end"],
                    action_report_time=action_report_time,
                )
                rows_by_mode[action_report_time].extend(campaign_rows)
                errors.extend({
                    **error,
                    "action_report_time": action_report_time,
                } for error in campaign_errors)
            except SnapchatNativeSyncError as exc:
                if exc.code == "snapchat_needs_reauth":
                    raise
                errors.append({
                    "kind": "ad_stats",
                    "campaign_id": campaign_id,
                    "action_report_time": action_report_time,
                    "code": exc.code,
                    "message": exc.message[:300],
                    "retryable": bool(exc.retryable),
                })
    business = _day_buckets(
        rows_by_mode[ADS_MANAGER_DEFAULT_ACTION_REPORT_TIME],
        timezone_name=BUSINESS_TIMEZONE,
        start_date=start_date,
        end_date=end_date,
    )
    local_by_mode = {
        mode: _day_buckets(
            rows_by_mode[mode],
            timezone_name=timezone_name,
            start_date=request["account_local_from"],
            end_date=request["account_local_to"],
        )
        for mode in ADS_MANAGER_SUPPORTED_ACTION_REPORT_TIMES
    }
''',
)
replace_once(
    ad,
    '''            bucket=bucket,
        )
        saved += 1
    for (campaign_id, ad_id, date_string), bucket in sorted(local.items()):
        await _upsert_projection(
            context,
            collection_name=SNAPCHAT_ACCOUNT_LOCAL_PERFORMANCE_COLLECTION,
            account=account,
            timezone_name=timezone_name,
            stored_granularity="ACCOUNT_LOCAL_DAY",
            campaign_id=campaign_id,
            ad_id=ad_id,
            date_string=date_string,
            bucket=bucket,
        )
        saved += 1
''',
    '''            bucket=bucket,
            action_report_time=ADS_MANAGER_DEFAULT_ACTION_REPORT_TIME,
        )
        saved += 1
    account_local_saved = 0
    for action_report_time, local in local_by_mode.items():
        for (campaign_id, ad_id, date_string), bucket in sorted(local.items()):
            await _upsert_projection(
                context,
                collection_name=SNAPCHAT_ACCOUNT_LOCAL_PERFORMANCE_COLLECTION,
                account=account,
                timezone_name=timezone_name,
                stored_granularity="ACCOUNT_LOCAL_DAY",
                campaign_id=campaign_id,
                ad_id=ad_id,
                date_string=date_string,
                bucket=bucket,
                action_report_time=action_report_time,
            )
            saved += 1
            account_local_saved += 1
''',
)
replace_once(
    ad,
    '''                "source_mode": AD_SOURCE_MODE,
''',
    '''                "source_mode": AD_REFRESH_SOURCE_MODE,
''',
)
replace_once(
    ad,
    '''        "source_mode": AD_SOURCE_MODE,
        "skipped": False,
''',
    '''        "source_mode": AD_REFRESH_SOURCE_MODE,
        "supported_action_report_times": list(ADS_MANAGER_SUPPORTED_ACTION_REPORT_TIMES),
        "skipped": False,
''',
)
replace_once(
    ad,
    '''        "account_local_rows_saved": len(local),
''',
    '''        "account_local_rows_saved": account_local_saved,
''',
)
replace_once(
    ad,
    '''    active_campaigns_only: bool = False,
    sort_by: str = "orders",
''',
    '''    active_campaigns_only: bool = False,
    sort_by: str = "orders",
    action_report_time: str = ADS_MANAGER_DEFAULT_ACTION_REPORT_TIME,
''',
)
replace_once(
    ad,
    '''    current = _aware_now(now())
''',
    '''    current = _aware_now(now())
    action_report_time = normalize_ads_manager_action_report_time(action_report_time)
''',
)
replace_once(
    ad,
    '''            "source_mode": AD_SOURCE_MODE,
        },
''',
    '''            "source_mode": ad_source_mode(action_report_time),
            "action_report_time": action_report_time,
        },
''',
)
replace_once(
    ad,
    '''        sort_by="spend",
        now=lambda: current,
''',
    '''        sort_by="spend",
        action_report_time=action_report_time,
        now=lambda: current,
''',
)
replace_once(
    ad,
    '''        "result_source": "platform",
        "supported_result_sources": ["platform"],
''',
    '''        "result_source": "platform",
        "action_report_time": action_report_time,
        "supported_action_report_times": list(ADS_MANAGER_SUPPORTED_ACTION_REPORT_TIMES),
        "supported_result_sources": ["platform"],
''',
)
replace_once(
    ad,
    '''            "source_mode": AD_SOURCE_MODE,
''',
    '''            "source_mode": ad_source_mode(action_report_time),
''',
)
replace_once(
    ad,
    '''            "commercial_results_source": "snapchat_ads_manager_impression_reporting",
            "action_report_time": ADS_MANAGER_ACTION_REPORT_TIME,
''',
    '''            "commercial_results_source": f"snapchat_ads_manager_{action_report_time}_reporting",
            "action_report_time": action_report_time,
''',
)
replace_once(
    ad,
    '''        sort_by: str = Query(default="orders", pattern="^(orders|spend|newest|active)$"),
        user: dict = Depends(current_user),
''',
    '''        sort_by: str = Query(default="orders", pattern="^(orders|spend|newest|active)$"),
        action_report_time: str = Query(default=ADS_MANAGER_DEFAULT_ACTION_REPORT_TIME, pattern="^(conversion|impression)$"),
        user: dict = Depends(current_user),
''',
)
replace_once(
    ad,
    '''                sort_by=sort_by,
            )
''',
    '''                sort_by=sort_by,
                action_report_time=action_report_time,
            )
''',
)

squad_service = "frontend/src/services/snapchatAdSquadPerformance.js"
replace_once(
    squad_service,
    '''        result_source: "platform",
        totals: normalizeTotals(value.totals),
''',
    '''        result_source: "platform",
        action_report_time: text(value.action_report_time, "conversion"),
        totals: normalizeTotals(value.totals),
''',
)
replace_once(
    squad_service,
    '''    sortBy = "orders",
} = {}) {
''',
    '''    sortBy = "orders",
    actionReportTime = "conversion",
} = {}) {
''',
)
replace_once(
    squad_service,
    '''            sort_by: sortBy,
''',
    '''            sort_by: sortBy,
            action_report_time: ["conversion", "impression"].includes(actionReportTime)
                ? actionReportTime
                : "conversion",
''',
)

ad_service = "frontend/src/services/snapchatAdPerformance.js"
replace_once(
    ad_service,
    '''        result_source: "platform",
        totals: metrics(value.totals),
''',
    '''        result_source: "platform",
        action_report_time: text(value.action_report_time, "conversion"),
        totals: metrics(value.totals),
''',
)
replace_once(
    ad_service,
    '''    sortBy = "orders",
} = {}) {
''',
    '''    sortBy = "orders",
    actionReportTime = "conversion",
} = {}) {
''',
)
replace_once(
    ad_service,
    '''            sort_by: sortBy,
''',
    '''            sort_by: sortBy,
            action_report_time: ["conversion", "impression"].includes(actionReportTime)
                ? actionReportTime
                : "conversion",
''',
)

page = "frontend/src/pages/MarketingPlatformWorkspace.jsx"
replace_once(
    page,
    '''                sortBy: adSquadSort,
            });
''',
    '''                sortBy: adSquadSort,
                actionReportTime,
            });
''',
)
replace_once(
    page,
    '''    }, [activeCampaignsOnly, adSquadPage, adSquadSort, appliedQuery, appliedRange, platform, selectedAccountId]);
''',
    '''    }, [actionReportTime, activeCampaignsOnly, adSquadPage, adSquadSort, appliedQuery, appliedRange, platform, selectedAccountId]);
''',
)

ad_table = "frontend/src/components/marketing/AdManagerTable.jsx"
replace_once(
    ad_table,
    '''export default function AdManagerTable({ activeCampaignsOnly = true }) {
''',
    '''export default function AdManagerTable({
    activeCampaignsOnly = true,
    actionReportTime = "conversion",
}) {
''',
)
replace_once(
    ad_table,
    '''                sortBy: serverSort,
            });
''',
    '''                sortBy: serverSort,
                actionReportTime,
            });
''',
)
replace_once(
    ad_table,
    '''    }, [activeCampaignsOnly, appliedQuery, page, serverSort]);
''',
    '''    }, [actionReportTime, activeCampaignsOnly, appliedQuery, page, serverSort]);
''',
)

backend_test = "backend/tests/test_snapchat_platform_source_integrity_v1.py"
append_once(
    backend_test,
    '''def test_total_aggregation_treats_omitted_zero_metrics_as_zero():
''',
    '''def test_entity_level_reports_partition_attribution_modes():
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


''',
)

print("SNAP_ATTRIBUTION_ENTITY_LEVELS_V1_APPLIED")
