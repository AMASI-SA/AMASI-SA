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


fresh = "backend/integrations_control_center/snapchat_freshness_impl_v6.py"
replace_once(
    fresh,
    '''# The Riyadh Dashboard keeps conversion-time semantics. The Ads Manager
# workspace uses impression-time attribution so its date-filtered Purchases and
# Purchase Value match Snapchat Ads Manager.
ADS_MANAGER_ACTION_REPORT_TIME: Final[str] = "impression"
ADS_MANAGER_SOURCE_MODE: Final[str] = (
    "snapchat_ads_manager_account_timezone_impression_v7"
)
''',
    '''# The Riyadh Dashboard keeps conversion-time semantics. The Ads Manager
# workspace defaults to Snapchat's recommended conversion-time view, while
# retaining impression-time as an explicit comparison mode.
ADS_MANAGER_DEFAULT_ACTION_REPORT_TIME: Final[str] = "conversion"
ADS_MANAGER_SUPPORTED_ACTION_REPORT_TIMES: Final[tuple[str, str]] = (
    "conversion",
    "impression",
)
ADS_MANAGER_ACTION_REPORT_TIME: Final[str] = ADS_MANAGER_DEFAULT_ACTION_REPORT_TIME
ADS_MANAGER_SOURCE_MODE: Final[str] = (
    "snapchat_ads_manager_account_timezone_conversion_v8"
)


def normalize_ads_manager_action_report_time(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    return (
        normalized
        if normalized in ADS_MANAGER_SUPPORTED_ACTION_REPORT_TIMES
        else ADS_MANAGER_DEFAULT_ACTION_REPORT_TIME
    )


def ads_manager_source_mode(action_report_time: Any) -> str:
    normalized = normalize_ads_manager_action_report_time(action_report_time)
    return f"snapchat_ads_manager_account_timezone_{normalized}_v8"
''',
)

manager = "backend/integrations_control_center/snapchat_account_timezone_manager.py"
replace_once(
    manager,
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
    manager,
    '''SNAPCHAT_ACCOUNT_LOCAL_PERFORMANCE_COLLECTION = (
    "mezan_snapchat_performance_account_day_v2"
)
ACCOUNT_LOCAL_SOURCE_MODE = (
    f"{ADS_MANAGER_SOURCE_MODE}:account_day_v2"
)
''',
    '''SNAPCHAT_ACCOUNT_LOCAL_PERFORMANCE_COLLECTION = (
    "mezan_snapchat_performance_account_day_v3"
)


def account_local_source_mode(action_report_time: Any) -> str:
    return f"{ads_manager_source_mode(action_report_time)}:account_day_v3"


ACCOUNT_LOCAL_SOURCE_MODE = account_local_source_mode(
    ADS_MANAGER_DEFAULT_ACTION_REPORT_TIME
)
''',
)
replace_once(
    manager,
    '''            ("date", 1),
            ("attribution_model", 1),
        ],
        unique=True,
        name="mezan_snapchat_account_day_v2_identity_unique",
''',
    '''            ("date", 1),
            ("attribution_model", 1),
            ("action_report_time", 1),
        ],
        unique=True,
        name="mezan_snapchat_account_day_v3_identity_unique",
''',
)
replace_once(
    manager,
    '''        name="mezan_snapchat_account_day_v2_date",
''',
    '''        name="mezan_snapchat_account_day_v3_date",
''',
)
replace_once(
    manager,
    '''    provider_start: Any,
    provider_end: Any,
) -> None:
''',
    '''    provider_start: Any,
    provider_end: Any,
    action_report_time: str,
) -> None:
''',
)
replace_once(
    manager,
    '''            "action_report_time": ADS_MANAGER_ACTION_REPORT_TIME,
''',
    '''            "action_report_time": action_report_time,
''',
)
replace_once(
    manager,
    '''        "source_mode": ACCOUNT_LOCAL_SOURCE_MODE,
''',
    '''        "action_report_time": action_report_time,
        "source_mode": account_local_source_mode(action_report_time),
''',
)
replace_once(
    manager,
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
    manager,
    '''        account_local_rows, account_local_errors = await hourly._fetch_account_hours(
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
''',
    '''        impression_rows, impression_errors = await hourly._fetch_account_hours(
            context,
            client,
            access_token,
            account_id=account_id,
            request_start=window["provider_start"],
            request_end=window["provider_end"],
            action_report_time="impression",
        )
        return (
            business_rows,
            impression_rows,
            [*business_errors, *impression_errors],
        )
''',
)
replace_once(
    manager,
    '''        business_rows, account_local_rows, errors = await fetch_both(request)
''',
    '''        business_rows, impression_rows, errors = await fetch_both(request)
''',
)
replace_once(
    manager,
    '''        business_rows, account_local_rows, errors = await fetch_both(request)

    business_campaigns, business_accounts = _campaign_day_buckets(
''',
    '''        business_rows, impression_rows, errors = await fetch_both(request)

    business_campaigns, business_accounts = _campaign_day_buckets(
''',
)
replace_once(
    manager,
    '''    local_campaigns, local_accounts = _campaign_day_buckets(
        account_local_rows,
        timezone_name=timezone_name,
        start_date=request["account_local_from"],
        end_date=request["account_local_to"],
    )
''',
    '''    local_conversion_campaigns, local_conversion_accounts = _campaign_day_buckets(
        business_rows,
        timezone_name=timezone_name,
        start_date=request["account_local_from"],
        end_date=request["account_local_to"],
    )
    local_impression_campaigns, local_impression_accounts = _campaign_day_buckets(
        impression_rows,
        timezone_name=timezone_name,
        start_date=request["account_local_from"],
        end_date=request["account_local_to"],
    )
''',
)
replace_once(
    manager,
    '''    local_rows_saved = 0
    for (campaign_id, date_string), bucket in sorted(local_campaigns.items()):
        await _upsert_account_local_performance(
            context,
            account=account,
            entity_type="campaign",
            external_id=campaign_id,
            date_string=date_string,
            timezone_name=timezone_name,
            metrics=_finalize_bucket(bucket),
            provider_start=bucket.get("provider_start"),
            provider_end=bucket.get("provider_end"),
        )
        saved += 1
        local_rows_saved += 1
    for date_string, bucket in sorted(local_accounts.items()):
        await _upsert_account_local_performance(
            context,
            account=account,
            entity_type="ad_account",
            external_id=account_id,
            date_string=date_string,
            timezone_name=timezone_name,
            metrics=_finalize_bucket(bucket),
            provider_start=bucket.get("provider_start"),
            provider_end=bucket.get("provider_end"),
        )
        saved += 1
        local_rows_saved += 1
''',
    '''    local_rows_saved = 0
    local_sets = (
        ("conversion", local_conversion_campaigns, local_conversion_accounts),
        ("impression", local_impression_campaigns, local_impression_accounts),
    )
    for action_report_time, local_campaigns, local_accounts in local_sets:
        for (campaign_id, date_string), bucket in sorted(local_campaigns.items()):
            await _upsert_account_local_performance(
                context,
                account=account,
                entity_type="campaign",
                external_id=campaign_id,
                date_string=date_string,
                timezone_name=timezone_name,
                metrics=_finalize_bucket(bucket),
                provider_start=bucket.get("provider_start"),
                provider_end=bucket.get("provider_end"),
                action_report_time=action_report_time,
            )
            saved += 1
            local_rows_saved += 1
        for date_string, bucket in sorted(local_accounts.items()):
            await _upsert_account_local_performance(
                context,
                account=account,
                entity_type="ad_account",
                external_id=account_id,
                date_string=date_string,
                timezone_name=timezone_name,
                metrics=_finalize_bucket(bucket),
                provider_start=bucket.get("provider_start"),
                provider_end=bucket.get("provider_end"),
                action_report_time=action_report_time,
            )
            saved += 1
            local_rows_saved += 1
''',
)
replace_once(
    manager,
    '''        "account_local_action_report_time": ADS_MANAGER_ACTION_REPORT_TIME,
''',
    '''        "account_local_action_report_time": ADS_MANAGER_DEFAULT_ACTION_REPORT_TIME,
        "account_local_action_report_times": list(ADS_MANAGER_SUPPORTED_ACTION_REPORT_TIMES),
''',
)
replace_once(
    manager,
    '''    result_source: str,
    active_campaigns_only: bool = False,
''',
    '''    result_source: str,
    action_report_time: str = ADS_MANAGER_DEFAULT_ACTION_REPORT_TIME,
    active_campaigns_only: bool = False,
''',
)
replace_once(
    manager,
    '''    current = _aware_now(now())
''',
    '''    current = _aware_now(now())
    action_report_time = normalize_ads_manager_action_report_time(action_report_time)
''',
)
replace_once(
    manager,
    '''            "source_mode": ACCOUNT_LOCAL_SOURCE_MODE,
''',
    '''            "source_mode": account_local_source_mode(action_report_time),
            "action_report_time": action_report_time,
''',
)
replace_once(
    manager,
    '''        "result_source": result_source,
        "supported_result_sources": list(SUPPORTED_RESULT_SOURCES),
''',
    '''        "result_source": result_source,
        "action_report_time": action_report_time,
        "supported_action_report_times": list(ADS_MANAGER_SUPPORTED_ACTION_REPORT_TIMES),
        "supported_result_sources": list(SUPPORTED_RESULT_SOURCES),
''',
)
replace_once(
    manager,
    '''                else "snapchat_ads_manager_impression_reporting"
            ),
            "account_local_action_report_time": ADS_MANAGER_ACTION_REPORT_TIME,
''',
    '''                else f"snapchat_ads_manager_{action_report_time}_reporting"
            ),
            "account_local_action_report_time": action_report_time,
''',
)
replace_once(
    manager,
    '''            "ai_analysis_ready": report_ready and campaign_details_ready,
''',
    '''            "ai_analysis_ready": (
                report_ready
                and campaign_details_ready
                and action_report_time == ADS_MANAGER_DEFAULT_ACTION_REPORT_TIME
            ),
            "ai_action_report_time": ADS_MANAGER_DEFAULT_ACTION_REPORT_TIME,
            "selected_action_report_time": action_report_time,
''',
)
replace_once(
    manager,
    '''            "dashboard_accounting_timezone_unchanged": BUSINESS_TIMEZONE,
''',
    '''            "dashboard_accounting_timezone_unchanged": BUSINESS_TIMEZONE,
            "default_action_report_time": ADS_MANAGER_DEFAULT_ACTION_REPORT_TIME,
            "impression_time_comparison_only": True,
''',
)
replace_once(
    manager,
    '''        result_source: str = Query(default=RESULT_SOURCE_SALLA, pattern="^(salla|platform)$"),
        active_campaigns_only: bool = Query(default=True),
''',
    '''        result_source: str = Query(default=RESULT_SOURCE_SALLA, pattern="^(salla|platform)$"),
        action_report_time: str = Query(default=ADS_MANAGER_DEFAULT_ACTION_REPORT_TIME, pattern="^(conversion|impression)$"),
        active_campaigns_only: bool = Query(default=True),
''',
)
replace_once(
    manager,
    '''                result_source=result_source,
                active_campaigns_only=active_campaigns_only,
''',
    '''                result_source=result_source,
                action_report_time=action_report_time,
                active_campaigns_only=active_campaigns_only,
''',
)

platform = "backend/integrations_control_center/snapchat_platform_source_integrity.py"
replace_once(
    platform,
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
    platform,
    '''SNAPCHAT_ACCOUNT_TOTAL_COLLECTION = "mezan_snapchat_performance_account_total_v1"
PLATFORM_TOTAL_SOURCE_MODE = (
    f"{ADS_MANAGER_SOURCE_MODE}:account_spend_campaign_completed_hour_v6"
)
''',
    '''SNAPCHAT_ACCOUNT_TOTAL_COLLECTION = "mezan_snapchat_performance_account_total_v2"


def platform_total_source_mode(action_report_time: Any) -> str:
    return (
        f"{ads_manager_source_mode(action_report_time)}:"
        "account_spend_campaign_completed_hour_v7"
    )


PLATFORM_TOTAL_SOURCE_MODE = platform_total_source_mode(
    ADS_MANAGER_DEFAULT_ACTION_REPORT_TIME
)
''',
)
replace_once(
    platform,
    '''    request_end: datetime,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool]:
''',
    '''    request_end: datetime,
    action_report_time: str = ADS_MANAGER_DEFAULT_ACTION_REPORT_TIME,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool]:
''',
)
replace_once(
    platform,
    '''        "action_report_time": ADS_MANAGER_ACTION_REPORT_TIME,
''',
    '''        "action_report_time": normalize_ads_manager_action_report_time(action_report_time),
''',
)
replace_once(
    platform,
    '''    provider_breakdown: str | None,
) -> None:
''',
    '''    provider_breakdown: str | None,
    action_report_time: str,
) -> None:
''',
)
replace_once(
    platform,
    '''            "action_report_time": ADS_MANAGER_ACTION_REPORT_TIME,
''',
    '''            "action_report_time": action_report_time,
''',
)
replace_once(
    platform,
    '''        "source_mode": PLATFORM_TOTAL_SOURCE_MODE,
''',
    '''        "action_report_time": action_report_time,
        "source_mode": platform_total_source_mode(action_report_time),
''',
)
replace_once(
    platform,
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
    platform,
    '''    errors: list[dict[str, Any]],
) -> dict[str, Any]:
''',
    '''    errors: list[dict[str, Any]],
    action_report_time: str,
) -> dict[str, Any]:
''',
)
replace_once(
    platform,
    '''            provider_breakdown=PLATFORM_TOTAL_BREAKDOWN,
        )
''',
    '''            provider_breakdown=PLATFORM_TOTAL_BREAKDOWN,
            action_report_time=action_report_time,
        )
''',
)
replace_once(
    platform,
    '''        provider_breakdown=None,
    )
''',
    '''        provider_breakdown=None,
        action_report_time=action_report_time,
    )
''',
)
replace_once(
    platform,
    '''            "attribution_model": ATTRIBUTION_MODEL,
        }
''',
    '''            "attribution_model": ATTRIBUTION_MODEL,
            "action_report_time": action_report_time,
        }
''',
)
# Replace the single campaign TOTAL fetch/persist with one pass per attribution mode.
replace_once(
    platform,
    '''            rows, campaign_errors, breakdown_seen = (
                await fetch_account_total_campaign_rows(
                    context,
                    client,
                    access_token,
                    account_id=account_id,
                    request_start=request_start,
                    request_end=request_end,
                )
            )
            day_errors = [*account_errors, *campaign_errors]
            for error in day_errors:
                errors.append({"date": report_date.isoformat(), **error})
            if not total_snapshot_is_authoritative(
                breakdown_seen=breakdown_seen,
                account_metrics_available=bool(account_metrics),
                errors=day_errors,
            ):
                errors.append({
                    "date": report_date.isoformat(),
                    "code": "snapchat_platform_total_snapshot_partial",
                    "message": (
                        "Snapchat TOTAL snapshot was incomplete; "
                        "the previous complete snapshot was preserved."
                    ),
                    "retryable": True,
                })
                continue
            persisted = await persist_account_total_day(
                context,
                account=account,
                timezone_name=timezone_name,
                date_string=report_date.isoformat(),
                rows=rows,
                account_metrics=account_metrics,
                provider_start=request_start,
                provider_end=request_end,
                authoritative_breakdown=True,
                errors=[],
            )
            saved += int(persisted["account_rows_saved"])
            campaign_saved += int(persisted["campaign_rows_saved"])
''',
    '''            for action_report_time in ADS_MANAGER_SUPPORTED_ACTION_REPORT_TIMES:
                rows, campaign_errors, breakdown_seen = (
                    await fetch_account_total_campaign_rows(
                        context,
                        client,
                        access_token,
                        account_id=account_id,
                        request_start=request_start,
                        request_end=request_end,
                        action_report_time=action_report_time,
                    )
                )
                day_errors = [*account_errors, *campaign_errors]
                for error in day_errors:
                    errors.append({
                        "date": report_date.isoformat(),
                        "action_report_time": action_report_time,
                        **error,
                    })
                if not total_snapshot_is_authoritative(
                    breakdown_seen=breakdown_seen,
                    account_metrics_available=bool(account_metrics),
                    errors=day_errors,
                ):
                    errors.append({
                        "date": report_date.isoformat(),
                        "action_report_time": action_report_time,
                        "code": "snapchat_platform_total_snapshot_partial",
                        "message": (
                            "Snapchat TOTAL snapshot was incomplete; "
                            "the previous complete snapshot was preserved."
                        ),
                        "retryable": True,
                    })
                    continue
                persisted = await persist_account_total_day(
                    context,
                    account=account,
                    timezone_name=timezone_name,
                    date_string=report_date.isoformat(),
                    rows=rows,
                    account_metrics=account_metrics,
                    provider_start=request_start,
                    provider_end=request_end,
                    authoritative_breakdown=True,
                    errors=[],
                    action_report_time=action_report_time,
                )
                saved += int(persisted["account_rows_saved"])
                campaign_saved += int(persisted["campaign_rows_saved"])
''',
)
replace_once(
    platform,
    '''        "source_mode": PLATFORM_TOTAL_SOURCE_MODE,
''',
    '''        "source_mode": PLATFORM_TOTAL_SOURCE_MODE,
        "supported_action_report_times": list(ADS_MANAGER_SUPPORTED_ACTION_REPORT_TIMES),
''',
)
replace_once(
    platform,
    '''    timezone_name: str,
) -> list[dict[str, Any]]:
''',
    '''    timezone_name: str,
    action_report_time: str = ADS_MANAGER_DEFAULT_ACTION_REPORT_TIME,
) -> list[dict[str, Any]]:
''',
)
replace_once(
    platform,
    '''            "source_mode": PLATFORM_TOTAL_SOURCE_MODE,
            "provider_granularity": PLATFORM_TOTAL_GRANULARITY,
''',
    '''            "source_mode": platform_total_source_mode(action_report_time),
            "action_report_time": normalize_ads_manager_action_report_time(action_report_time),
            "provider_granularity": PLATFORM_TOTAL_GRANULARITY,
''',
)
replace_once(
    platform,
    '''    timezone_name = _text(result.get("account_timezone"))
''',
    '''    timezone_name = _text(result.get("account_timezone"))
    action_report_time = normalize_ads_manager_action_report_time(
        result.get("action_report_time")
    )
''',
)
replace_once(
    platform,
    '''        timezone_name=timezone_name,
    )
''',
    '''        timezone_name=timezone_name,
        action_report_time=action_report_time,
    )
''',
)
replace_once(
    platform,
    '''        "commercial_metrics_source": "snapchat_impression_total_pending",
''',
    '''        "commercial_metrics_source": f"snapchat_{action_report_time}_total_pending",
''',
)
replace_once(
    platform,
    '''            "platform_source_isolated": True,
        })
''',
    '''            "platform_source_isolated": True,
            "platform_action_report_time": action_report_time,
            "platform_total_source_mode": platform_total_source_mode(action_report_time),
        })
''',
)
# Ensure the final source declaration reflects the selected mode.
replace_once(
    platform,
    '''        "platform_action_report_time": ADS_MANAGER_ACTION_REPORT_TIME,
''',
    '''        "platform_action_report_time": action_report_time,
''',
)
replace_once(
    platform,
    '''        "platform_total_source_mode": PLATFORM_TOTAL_SOURCE_MODE,
''',
    '''        "platform_total_source_mode": platform_total_source_mode(action_report_time),
''',
)
# Audit uses the AI/default conversion-time snapshot.
replace_once(
    platform,
    '''                    timezone_name=timezone_name,
                )
''',
    '''                    timezone_name=timezone_name,
                    action_report_time=ADS_MANAGER_DEFAULT_ACTION_REPORT_TIME,
                )
''',
)

service = "frontend/src/services/marketingPerformance.js"
replace_once(
    service,
    '''        result_source: text(value.result_source, "salla"),
''',
    '''        result_source: text(value.result_source, "salla"),
        action_report_time: text(value.action_report_time, "conversion"),
        supported_action_report_times: Array.isArray(value.supported_action_report_times)
            ? value.supported_action_report_times.filter((item) => ["conversion", "impression"].includes(item))
            : ["conversion", "impression"],
''',
)
replace_once(
    service,
    '''            platform_action_report_time: nullableText(value.source?.platform_action_report_time),
''',
    '''            platform_action_report_time: nullableText(value.source?.platform_action_report_time),
            account_local_action_report_time: nullableText(value.source?.account_local_action_report_time),
''',
)
replace_once(
    service,
    '''    activeCampaignsOnly = true,
} = {}) {
''',
    '''    activeCampaignsOnly = true,
    actionReportTime = "conversion",
} = {}) {
''',
)
replace_once(
    service,
    '''                    active_campaigns_only: activeCampaignsOnly,
''',
    '''                    active_campaigns_only: activeCampaignsOnly,
                    action_report_time: ["conversion", "impression"].includes(actionReportTime)
                        ? actionReportTime
                        : "conversion",
''',
)

page = "frontend/src/pages/MarketingPlatformWorkspace.jsx"
replace_once(
    page,
    '''    const [activeCampaignsOnly, setActiveCampaignsOnly] = useState(true);
''',
    '''    const [activeCampaignsOnly, setActiveCampaignsOnly] = useState(true);
    const [actionReportTime, setActionReportTime] = useState("conversion");
''',
)
replace_once(
    page,
    '''                activeCampaignsOnly,
            });
''',
    '''                activeCampaignsOnly,
                actionReportTime,
            });
''',
)
replace_once(
    page,
    '''    }, [activeCampaignsOnly, appliedQuery, appliedRange, page, platform]);
''',
    '''    }, [actionReportTime, activeCampaignsOnly, appliedQuery, appliedRange, page, platform]);
''',
)
replace_once(
    page,
    '''        setActiveCampaignsOnly(true);
''',
    '''        setActiveCampaignsOnly(true);
        setActionReportTime("conversion");
''',
)
# Add native control under the filter form.
replace_once(
    page,
    '''            </form>

            {error && (
''',
    '''            </form>

            {platform === "snapchat" && (
                <section
                    className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm"
                    data-testid="snapchat-action-report-time-control"
                >
                    <div className="flex flex-wrap items-center justify-between gap-3">
                        <div>
                            <div className="text-sm font-black text-slate-900">توقيت نتائج Snapchat</div>
                            <div className="mt-1 text-xs font-semibold text-slate-500">
                                وقت التحويل هو الافتراضي لقرارات التشغيل والذكاء الاصطناعي. وقت الظهور متاح للمقارنة التاريخية مع إعداد Snapchat نفسه.
                            </div>
                        </div>
                        <div className="inline-flex rounded-xl border border-slate-200 bg-slate-50 p-1">
                            {[
                                ["conversion", "وقت التحويل · موصى به"],
                                ["impression", "وقت الظهور · مقارنة"],
                            ].map(([value, label]) => (
                                <button
                                    key={value}
                                    type="button"
                                    onClick={() => {
                                        setPage(1);
                                        setAdSquadPage(1);
                                        setActionReportTime(value);
                                    }}
                                    aria-pressed={actionReportTime === value}
                                    data-testid={`snapchat-action-report-time-${value}`}
                                    className={`rounded-lg px-4 py-2 text-xs font-black transition ${
                                        actionReportTime === value
                                            ? "bg-slate-950 text-white shadow-sm"
                                            : "text-slate-600 hover:bg-white"
                                    }`}
                                >
                                    {label}
                                </button>
                            ))}
                        </div>
                    </div>
                </section>
            )}

            {error && (
''',
)
replace_once(
    page,
    '''                    resultSource={data?.result_source || "salla"}
                    entityLevel={entityLevel}
''',
    '''                    resultSource={data?.result_source || "salla"}
                    actionReportTime={actionReportTime}
                    entityLevel={entityLevel}
''',
)
# Make AI note explicit when impression comparison is selected.
replace_once(
    page,
    '''                                <p className="mt-1 text-xs font-semibold text-slate-500">تتحقق الجاهزية من الدليل الفعلي، لا من مجرد وجود الربط.</p>
''',
    '''                                <p className="mt-1 text-xs font-semibold text-slate-500">تتحقق الجاهزية من الدليل الفعلي، لا من مجرد وجود الربط. قرارات الذكاء الاصطناعي تعتمد وقت التحويل فقط.</p>
''',
)

entity = "frontend/src/components/marketing/AdsEntityLevelWorkspace.jsx"
replace_once(
    entity,
    '''    resultSource = "salla",
    entityLevel,
''',
    '''    resultSource = "salla",
    actionReportTime = "conversion",
    entityLevel,
''',
)
replace_once(
    entity,
    '''            {entityLevel === "ads" && <AdManagerTable activeCampaignsOnly={activeCampaignsOnly} />}
''',
    '''            {entityLevel === "ads" && (
                <AdManagerTable
                    activeCampaignsOnly={activeCampaignsOnly}
                    actionReportTime={actionReportTime}
                />
            )}
''',
)

# Focused regression contracts.
test = "backend/tests/test_snapchat_platform_source_integrity_v1.py"
replace_once(
    test,
    '''def test_ads_manager_entity_levels_use_impression_time():
''',
    '''def test_ads_manager_defaults_to_conversion_and_supports_impression_comparison():
''',
)
replace_once(
    test,
    '''    assert 'ADS_MANAGER_ACTION_REPORT_TIME: Final[str] = "impression"' in freshness
    assert 'SNAPCHAT_ACTION_REPORT_TIME: Final[str] = "conversion"' in freshness
''',
    '''    assert 'ADS_MANAGER_DEFAULT_ACTION_REPORT_TIME: Final[str] = "conversion"' in freshness
    assert 'ADS_MANAGER_SUPPORTED_ACTION_REPORT_TIMES' in freshness
    assert '"impression"' in freshness
    assert 'SNAPCHAT_ACTION_REPORT_TIME: Final[str] = "conversion"' in freshness
''',
)
replace_once(
    test,
    '''    assert '"action_report_time": ADS_MANAGER_ACTION_REPORT_TIME' in platform
    assert 'action_report_time=ADS_MANAGER_ACTION_REPORT_TIME' in manager
''',
    '''    assert 'normalize_ads_manager_action_report_time' in platform
    assert 'action_report_time=action_report_time' in manager
''',
)
append_once(
    test,
    '''def test_total_aggregation_treats_omitted_zero_metrics_as_zero():
''',
    '''def test_platform_total_collection_partitions_conversion_and_impression():
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


''',
)

front_test = "frontend/src/pages/MarketingPlatformWorkspacePlatformSnapshot.test.js"
append_once(
    front_test,
    '''test("flags only an incomplete Snapchat platform TOTAL snapshot", () => {
''',
    '''test("Snapchat attribution UI defaults to conversion-time semantics", () => {
  const source = require("fs").readFileSync(
    require("path").join(__dirname, "MarketingPlatformWorkspace.jsx"),
    "utf8",
  );
  expect(source).toContain('useState("conversion")');
  expect(source).toContain("وقت التحويل · موصى به");
  expect(source).toContain("وقت الظهور · مقارنة");
  expect(source).toContain("قرارات الذكاء الاصطناعي تعتمد وقت التحويل فقط");
});

''',
)

print("SNAP_ATTRIBUTION_MODE_V1_APPLIED")
