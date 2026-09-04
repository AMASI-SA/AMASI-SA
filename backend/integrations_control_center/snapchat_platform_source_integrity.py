"""Authoritative Snapchat platform snapshots and strict source isolation.

The Ads Manager workspace exposes two selectable commercial result sources:

* ``platform``: purchases and purchase value reported by Snapchat.
* ``salla``: exact Salla orders matched to a Snapchat campaign.

This module keeps those sources separate. The complete ad-account campaign
breakdown owns both the All Ads headline rollup and campaign rows. This avoids
the unsupported direct ad-account stats shape while retaining every campaign,
including inactive campaigns. Existing HOUR ingestion remains responsible for
the hourly chart and Riyadh accounting.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

import httpx

from . import snapchat_account_hourly_refresh as hourly
from . import snapchat_account_timezone_manager as manager
from .snapchat_active_campaign_filtering import is_active_provider_status
from .snapchat_campaign_result_source_routes import RESULT_SOURCE_PLATFORM
from .snapchat_freshness_impl_v6 import (
    ADS_MANAGER_ACTION_REPORT_TIME,
    ADS_MANAGER_DEFAULT_ACTION_REPORT_TIME,
    ADS_MANAGER_SUPPORTED_ACTION_REPORT_TIMES,
    ADS_MANAGER_SWIPE_ATTRIBUTION_WINDOW,
    ADS_MANAGER_VIEW_ATTRIBUTION_WINDOW,
    ads_manager_source_mode,
    normalize_ads_manager_action_report_time,
)
from .snapchat_native_data_common import (
    ATTRIBUTION_MODEL,
    BUSINESS_TIMEZONE,
    MAX_PAGES,
    SNAPCHAT_API_BASE,
    SNAPCHAT_ENTITY_COLLECTION,
    SNAPCHAT_PROVIDER_ID,
    SnapchatNativeSyncError,
    SnapchatSyncContext,
    _as_number,
    _collection,
    _safe_next_url,
    _timezone,
)
from .snapchat_native_performance_sync import (
    CONVERSION_SOURCE_TYPES,
    STAT_FIELDS,
    TOTAL_STAT_FIELDS,
    SWIPE_ATTRIBUTION_WINDOW,
    VIEW_ATTRIBUTION_WINDOW,
    _computed,
    _funnel_metrics,
    _metric_provenance,
)

SNAPCHAT_ACCOUNT_TOTAL_COLLECTION = "mezan_snapchat_performance_account_total_v2"


def platform_total_source_mode(action_report_time: Any) -> str:
    return (
        f"{ads_manager_source_mode(action_report_time)}:"
        "campaign_breakdown_all_ads_account_day_total_v15"
    )


PLATFORM_TOTAL_SOURCE_MODE = platform_total_source_mode(
    ADS_MANAGER_DEFAULT_ACTION_REPORT_TIME
)
TOTAL_CURRENT_DAY_WINDOW_POLICY = "account_local_day_boundary"
PLATFORM_TOTAL_GRANULARITY = "TOTAL"
PLATFORM_CURRENT_DAY_GRANULARITY = "HOUR"
PLATFORM_TOTAL_BREAKDOWN = "campaign"
MAX_TOTAL_ROWS = 100_000
REQUIRED_ACCOUNT_TOTAL_FIELDS = frozenset({"spend"})
DIRECT_ACCOUNT_TOTAL_FIELDS = STAT_FIELDS

RefreshCallable = Callable[..., Awaitable[dict[str, Any]]]
ReportCallable = Callable[..., Awaitable[dict[str, Any]]]
AuditCallable = Callable[..., Awaitable[dict[str, Any]]]


def _text(value: Any) -> str:
    return str(value or "").strip()


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if parsed == parsed and abs(parsed) != float("inf") else None


def _aware_now(value: datetime | None = None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current


def _ratio(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator in {None, 0}:
        return None
    return round(float(numerator) / float(denominator), 6)


def account_local_total_window(
    report_date: date,
    *,
    timezone_name: str,
    now: datetime | None = None,
) -> tuple[datetime, datetime] | None:
    """Return the exact Ads Manager day window in the account timezone."""
    zone = _timezone(timezone_name)
    current = _aware_now(now).astimezone(zone)
    if report_date > current.date():
        return None
    start = datetime(
        report_date.year,
        report_date.month,
        report_date.day,
        tzinfo=zone,
    )
    next_date = report_date + timedelta(days=1)
    nominal_end = datetime(
        next_date.year,
        next_date.month,
        next_date.day,
        tzinfo=zone,
    )
    # Snapchat TOTAL requires account-local day boundaries. The campaign
    # breakdown endpoint accepts the next-midnight boundary for the open day
    # and returns the current cumulative Ads Manager total.
    return (start, nominal_end)


def account_local_dates_for_refresh(
    start_date: date,
    end_date: date,
    *,
    timezone_name: str,
    now: datetime | None = None,
) -> list[date]:
    """Return account-local dates touched by the scheduler's Riyadh window."""
    request = hourly.snapchat_account_request_window(
        start_date,
        end_date,
        account_timezone=timezone_name,
        now=now,
        include_current_hour=True,
    )
    if request is None:
        return []
    zone = _timezone(timezone_name)
    first = request["provider_start"].astimezone(zone).date()
    last_point = request["provider_end"] - timedelta(microseconds=1)
    last = last_point.astimezone(zone).date()
    current = _aware_now(now).astimezone(zone).date()
    if last > current:
        last = current
    output: list[date] = []
    cursor = first
    while cursor <= last:
        output.append(cursor)
        cursor += timedelta(days=1)
    return output


def _subrequest_error(wrapped: dict[str, Any], status: str) -> dict[str, Any]:
    return {
        "kind": "account_total_campaign_breakdown",
        "code": _text(
            wrapped.get("error_code")
            or wrapped.get("code")
            or "snapchat_account_total_subrequest_failed"
        )[:120],
        "message": _text(
            wrapped.get("error_message")
            or wrapped.get("debug_message")
            or wrapped.get("message")
            or status
            or "Snapchat rejected an account TOTAL sub-request."
        )[:300],
        "error": status[:80],
        "retryable": True,
    }


def _normalized_requested_metrics(
    metrics: dict[str, Any] | None,
) -> dict[str, int | float]:
    source = metrics if isinstance(metrics, dict) else {}
    output: dict[str, int | float] = {}
    for key in STAT_FIELDS:
        value = _as_number(source.get(key))
        if value is None:
            continue
        number = float(value)
        output[key] = int(number) if number.is_integer() else number
    return output



def _sum_hourly_metrics(points: Any) -> dict[str, int | float]:
    sums = {key: 0.0 for key in STAT_FIELDS}
    seen: set[str] = set()
    if not isinstance(points, list):
        return {}
    for point in points:
        if not isinstance(point, dict):
            continue
        metrics = point.get("stats")
        if not isinstance(metrics, dict):
            continue
        for key in STAT_FIELDS:
            value = _as_number(metrics.get(key))
            if value is None:
                continue
            sums[key] += float(value)
            seen.add(key)
    return {
        key: int(sums[key]) if sums[key].is_integer() else sums[key]
        for key in STAT_FIELDS
        if key in seen
    }


def _hourly_payload_as_total(
    payload: dict[str, Any],
    *,
    breakdown: bool,
) -> dict[str, Any]:
    """Aggregate provider HOUR points into the existing account-day contract."""
    wrapped_stats = payload.get("timeseries_stats") or []
    if not isinstance(wrapped_stats, list):
        raise SnapchatNativeSyncError(
            "snapchat_account_hour_payload_invalid",
            "Snapchat returned invalid account HOUR performance data.",
            status_code=502,
            retryable=True,
        )
    total_stats: list[dict[str, Any]] = []
    for wrapped in wrapped_stats:
        if not isinstance(wrapped, dict):
            continue
        status = _text(wrapped.get("sub_request_status") or "SUCCESS").upper()
        if "FAIL" in status or "ERROR" in status:
            total_stats.append(dict(wrapped))
            continue
        stat = wrapped.get("timeseries_stat", wrapped)
        if not isinstance(stat, dict):
            continue
        total_stat: dict[str, Any] = {
            "id": stat.get("id"),
            "start_time": stat.get("start_time"),
            "end_time": stat.get("end_time"),
        }
        if breakdown:
            raw_breakdown = stat.get("breakdown_stats")
            campaigns = (
                raw_breakdown.get(PLATFORM_TOTAL_BREAKDOWN)
                if isinstance(raw_breakdown, dict)
                else None
            )
            if isinstance(campaigns, list):
                campaign_totals: list[dict[str, Any]] = []
                for campaign in campaigns:
                    if not isinstance(campaign, dict):
                        continue
                    campaign_totals.append({
                        "id": campaign.get("id"),
                        "stats": _sum_hourly_metrics(campaign.get("timeseries")),
                    })
                total_stat["breakdown_stats"] = {
                    PLATFORM_TOTAL_BREAKDOWN: campaign_totals,
                }
            else:
                total_stat["stats"] = _sum_hourly_metrics(
                    stat.get("timeseries")
                )
        else:
            total_stat["stats"] = _sum_hourly_metrics(stat.get("timeseries"))
        total_stats.append({
            "sub_request_status": wrapped.get("sub_request_status") or "SUCCESS",
            "total_stat": total_stat,
        })
    return {
        "total_stats": total_stats,
        "paging": payload.get("paging") or {},
    }

def extract_account_total_metrics(
    payload: dict[str, Any],
) -> tuple[dict[str, int | float] | None, list[dict[str, Any]], int]:
    """Extract direct Ad Account TOTAL spend without a breakdown."""
    wrapped_stats = payload.get("total_stats") or []
    if not isinstance(wrapped_stats, list):
        raise SnapchatNativeSyncError(
            "snapchat_account_total_payload_invalid",
            "Snapchat returned invalid direct account TOTAL data.",
            status_code=502,
            retryable=True,
        )
    errors: list[dict[str, Any]] = []
    successful_subrequests = 0
    metrics: dict[str, int | float] | None = None
    for wrapped in wrapped_stats:
        if not isinstance(wrapped, dict):
            continue
        status = _text(wrapped.get("sub_request_status") or "SUCCESS").upper()
        if "FAIL" in status or "ERROR" in status:
            errors.append(_subrequest_error(wrapped, status))
            continue
        successful_subrequests += 1
        stat = wrapped.get("total_stat", wrapped)
        if not isinstance(stat, dict):
            continue
        raw = stat.get("stats")
        if not isinstance(raw, dict):
            continue
        missing = sorted(REQUIRED_ACCOUNT_TOTAL_FIELDS - set(raw))
        if missing:
            errors.append({
                "kind": "account_total_direct",
                "code": "snapchat_account_direct_total_fields_missing",
                "message": (
                    "Snapchat direct TOTAL omitted required fields: "
                    + ",".join(missing)
                )[:300],
                "retryable": True,
            })
            continue
        metrics = _normalized_requested_metrics(raw)
    return metrics, errors, successful_subrequests


async def fetch_account_total_direct_metrics(
    context: SnapchatSyncContext,
    client: httpx.AsyncClient,
    access_token: str,
    *,
    account_id: str,
    request_start: datetime,
    request_end: datetime,
    granularity: str = PLATFORM_TOTAL_GRANULARITY,
    action_report_time: str = ADS_MANAGER_DEFAULT_ACTION_REPORT_TIME,
) -> tuple[dict[str, int | float], list[dict[str, Any]]]:
    """Read the direct All Ads headline metrics shown by Ads Manager."""
    url = f"{SNAPCHAT_API_BASE}/adaccounts/{account_id}/stats"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
    }
    payload = await context.get_json(
        client,
        url,
        headers=headers,
        params={
            "start_time": request_start.isoformat(timespec="seconds"),
            "end_time": request_end.isoformat(timespec="seconds"),
            "granularity": granularity,
            "fields": ",".join(DIRECT_ACCOUNT_TOTAL_FIELDS),
            "omit_empty": "false",
            "conversion_source_types": CONVERSION_SOURCE_TYPES,
            "swipe_up_attribution_window": ADS_MANAGER_SWIPE_ATTRIBUTION_WINDOW,
            "view_attribution_window": ADS_MANAGER_VIEW_ATTRIBUTION_WINDOW,
            "action_report_time": normalize_ads_manager_action_report_time(
                action_report_time
            ),
        },
    )
    if granularity == PLATFORM_CURRENT_DAY_GRANULARITY:
        payload = _hourly_payload_as_total(payload, breakdown=False)
    metrics, errors, successful_subrequests = extract_account_total_metrics(payload)
    if metrics is None:
        first = errors[0] if errors else {}
        raise SnapchatNativeSyncError(
            _text(first.get("code") or "snapchat_account_direct_total_missing"),
            _text(
                first.get("message")
                or "Snapchat did not return the direct ad-account TOTAL row."
            ),
            status_code=502,
            retryable=True,
            result={
                "successful_subrequests": successful_subrequests,
                "errors": errors[:10],
            },
        )
    return metrics, errors


def extract_account_total_campaign_rows(
    payload: dict[str, Any],
    *,
    request_start: datetime,
    request_end: datetime,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int, bool]:
    """Extract campaign rows from an ad-account TOTAL campaign breakdown."""
    wrapped_stats = payload.get("total_stats") or []
    if not isinstance(wrapped_stats, list):
        raise SnapchatNativeSyncError(
            "snapchat_account_total_payload_invalid",
            "Snapchat returned invalid account TOTAL performance data.",
            status_code=502,
            retryable=True,
        )

    rows: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    successful_subrequests = 0
    breakdown_seen = False
    for wrapped in wrapped_stats:
        if not isinstance(wrapped, dict):
            continue
        status = _text(wrapped.get("sub_request_status") or "SUCCESS").upper()
        if "FAIL" in status or "ERROR" in status:
            errors.append(_subrequest_error(wrapped, status))
            continue
        successful_subrequests += 1
        stat = wrapped.get("total_stat", wrapped)
        if not isinstance(stat, dict):
            continue
        provider_start = stat.get("start_time") or request_start.isoformat(
            timespec="seconds"
        )
        provider_end = stat.get("end_time") or request_end.isoformat(
            timespec="seconds"
        )
        breakdown = stat.get("breakdown_stats")
        campaigns = (
            breakdown.get(PLATFORM_TOTAL_BREAKDOWN)
            if isinstance(breakdown, dict)
            else None
        )
        if isinstance(campaigns, list):
            breakdown_seen = True
            for campaign in campaigns:
                if not isinstance(campaign, dict):
                    continue
                stats = campaign.get("stats")
                if not isinstance(stats, dict):
                    continue
                rows.append({
                    "campaign_id": _text(campaign.get("id")),
                    "metrics": stats,
                    "start_time": provider_start,
                    "end_time": provider_end,
                })
            continue

        stats = stat.get("stats")
        if isinstance(stats, dict):
            rows.append({
                "campaign_id": "",
                "metrics": stats,
                "start_time": provider_start,
                "end_time": provider_end,
                "direct_account_fallback": True,
            })
    return rows, errors, successful_subrequests, breakdown_seen


async def fetch_account_total_campaign_rows(
    context: SnapchatSyncContext,
    client: httpx.AsyncClient,
    access_token: str,
    *,
    account_id: str,
    request_start: datetime,
    request_end: datetime,
    granularity: str = PLATFORM_TOTAL_GRANULARITY,
    action_report_time: str = ADS_MANAGER_DEFAULT_ACTION_REPORT_TIME,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool]:
    url = f"{SNAPCHAT_API_BASE}/adaccounts/{account_id}/stats"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
    }
    params: dict[str, Any] | None = {
        "start_time": request_start.isoformat(timespec="seconds"),
        "end_time": request_end.isoformat(timespec="seconds"),
        "granularity": granularity,
        "breakdown": PLATFORM_TOTAL_BREAKDOWN,
        "fields": ",".join(
            TOTAL_STAT_FIELDS
            if granularity == PLATFORM_TOTAL_GRANULARITY
            else STAT_FIELDS
        ),
        "limit": 200,
        "omit_empty": "false",
        "conversion_source_types": CONVERSION_SOURCE_TYPES,
        "swipe_up_attribution_window": ADS_MANAGER_SWIPE_ATTRIBUTION_WINDOW,
        "view_attribution_window": ADS_MANAGER_VIEW_ATTRIBUTION_WINDOW,
        "action_report_time": normalize_ads_manager_action_report_time(action_report_time),
    }
    rows: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    successful_subrequests = 0
    breakdown_seen = False
    for _ in range(MAX_PAGES):
        payload = await context.get_json(
            client,
            url,
            headers=headers,
            params=params,
        )
        if granularity == PLATFORM_CURRENT_DAY_GRANULARITY:
            payload = _hourly_payload_as_total(payload, breakdown=True)
        page_rows, page_errors, page_success, page_breakdown = (
            extract_account_total_campaign_rows(
                payload,
                request_start=request_start,
                request_end=request_end,
            )
        )
        rows.extend(page_rows)
        errors.extend(page_errors)
        successful_subrequests += page_success
        breakdown_seen = breakdown_seen or page_breakdown
        next_url = _safe_next_url((payload.get("paging") or {}).get("next_link"))
        if not next_url:
            break
        url, params = next_url, None

    if successful_subrequests == 0 and errors:
        first = errors[0]
        raise SnapchatNativeSyncError(
            _text(first.get("code") or "snapchat_account_total_failed"),
            _text(first.get("message") or "Snapchat account TOTAL request failed."),
            status_code=502,
            retryable=True,
            result={"errors": errors[:10]},
        )
    return rows, errors, breakdown_seen


def aggregate_total_campaign_metrics(
    rows: list[dict[str, Any]],
) -> dict[str, int | float | None]:
    """Sum requested campaign TOTAL metrics; omitted zero fields become zero."""
    campaign_rows = [row for row in rows if _text(row.get("campaign_id"))]
    source = campaign_rows or [
        row for row in rows if row.get("direct_account_fallback") is True
    ]
    if not source:
        return {key: 0 for key in STAT_FIELDS}
    sums = {key: 0.0 for key in STAT_FIELDS}
    for row in source:
        metrics = row.get("metrics") or {}
        for key in STAT_FIELDS:
            value = _as_number(metrics.get(key))
            if value is not None:
                sums[key] += float(value)
    return {
        key: int(value) if float(value).is_integer() else value
        for key, value in sums.items()
    }


def merge_direct_spend_with_campaign_metrics(
    direct_metrics: dict[str, Any],
    campaign_rows: list[dict[str, Any]],
) -> dict[str, int | float | None]:
    """Use the authoritative provider level for each All Ads metric.

    The direct Ad Account TOTAL row is accepted for spend. Purchases, purchase
    value, impressions, swipes, and video metrics are summed from the complete
    campaign breakdown. No Salla result is introduced into the platform view.
    """
    merged = aggregate_total_campaign_metrics(campaign_rows)
    if _as_number(direct_metrics.get("spend")) is None:
        raise SnapchatNativeSyncError(
            "snapchat_account_direct_spend_missing",
            "Snapchat direct Ad Account TOTAL omitted spend.",
            status_code=502,
            retryable=True,
        )
    for key in STAT_FIELDS:
        value = _as_number(direct_metrics.get(key))
        if value is not None:
            merged[key] = int(value) if float(value).is_integer() else float(value)
    return merged


def total_snapshot_is_authoritative(
    *,
    breakdown_seen: bool,
    account_metrics_available: bool,
    errors: list[dict[str, Any]],
) -> bool:
    """Require the direct account total and complete campaign breakdown."""
    return bool(breakdown_seen and account_metrics_available and not errors)


async def _ensure_total_indexes(db: Any) -> None:
    collection = _collection(db, SNAPCHAT_ACCOUNT_TOTAL_COLLECTION)
    await collection.create_index(
        [
            ("user_id", 1),
            ("ad_account_id", 1),
            ("entity_type", 1),
            ("external_id", 1),
            ("date", 1),
            ("attribution_model", 1),
            ("action_report_time", 1),
        ],
        unique=True,
        name="mezan_snapchat_account_total_v2_identity_unique",
    )
    await collection.create_index(
        [
            ("user_id", 1),
            ("ad_account_id", 1),
            ("date", -1),
            ("entity_type", 1),
            ("action_report_time", 1),
        ],
        name="mezan_snapchat_account_total_v2_date",
    )


async def _upsert_total_row(
    context: SnapchatSyncContext,
    *,
    account: dict[str, Any],
    entity_type: str,
    external_id: str,
    date_string: str,
    timezone_name: str,
    metrics: dict[str, Any],
    provider_start: Any,
    provider_end: Any,
    provider_breakdown: str | None,
    action_report_time: str,
) -> None:
    currency = _text(account.get("currency")).upper()
    spend_micro = _as_number(metrics.get("spend"))
    value_micro = _as_number(metrics.get("conversion_purchases_value"))
    purchases = _as_number(metrics.get("conversion_purchases"))
    spend_native = (
        round(float(spend_micro) / 1_000_000, 6)
        if spend_micro is not None
        else None
    )
    value_native = (
        round(float(value_micro) / 1_000_000, 6)
        if value_micro is not None
        else None
    )
    now_iso = context.now_iso()
    document = {
        "user_id": context.user_id,
        "provider": SNAPCHAT_PROVIDER_ID,
        "ad_account_id": account["ad_account_id"],
        "mezan_integration_account_id": account.get(
            "mezan_integration_account_id"
        ),
        "entity_type": entity_type,
        "external_id": external_id,
        "date": date_string,
        "date_timezone": timezone_name,
        "business_timezone": BUSINESS_TIMEZONE,
        "currency": currency or None,
        "account_timezone": timezone_name,
        "attribution_model": ATTRIBUTION_MODEL,
        "metrics": metrics,
        "funnel_metrics": _funnel_metrics(metrics),
        "metric_provenance": _metric_provenance(
            metrics,
            provider_granularity=PLATFORM_TOTAL_GRANULARITY,
            provider_breakdown=provider_breakdown,
        ),
        "purchases": purchases,
        "spend_native": spend_native,
        "spend_sar": await context.to_sar(spend_native, currency),
        "purchase_value_native": value_native,
        "purchase_value_sar": await context.to_sar(value_native, currency),
        "computed": _computed(metrics),
        "conversion_reporting": {
            "metric": "conversion_purchases",
            "source_types": [CONVERSION_SOURCE_TYPES],
            "action_report_time": action_report_time,
            "swipe_up_attribution_window": ADS_MANAGER_SWIPE_ATTRIBUTION_WINDOW,
            "view_attribution_window": ADS_MANAGER_VIEW_ATTRIBUTION_WINDOW,
        },
        "action_report_time": action_report_time,
        "source_mode": platform_total_source_mode(action_report_time),
        "accounting_eligible": False,
        "provider_window_start": provider_start,
        "provider_window_end": provider_end,
        "provider_granularity": PLATFORM_TOTAL_GRANULARITY,
        "provider_breakdown": provider_breakdown,
        "stored_granularity": "ACCOUNT_LOCAL_TOTAL_DAY",
        "report_scope": "snapchat_ads_manager_account_timezone",
        "updated_at": now_iso,
    }
    if entity_type == "campaign":
        document["campaign_id"] = external_id
    await _collection(context.db, SNAPCHAT_ACCOUNT_TOTAL_COLLECTION).update_one(
        {
            "user_id": context.user_id,
            "ad_account_id": account["ad_account_id"],
            "entity_type": entity_type,
            "external_id": external_id,
            "date": date_string,
            "attribution_model": ATTRIBUTION_MODEL,
            "action_report_time": action_report_time,
        },
        {"$set": document, "$setOnInsert": {"created_at": now_iso}},
        upsert=True,
    )


async def persist_account_total_day(
    context: SnapchatSyncContext,
    *,
    account: dict[str, Any],
    timezone_name: str,
    date_string: str,
    rows: list[dict[str, Any]],
    account_metrics: dict[str, Any],
    provider_start: datetime,
    provider_end: datetime,
    authoritative_breakdown: bool,
    errors: list[dict[str, Any]],
    action_report_time: str,
) -> dict[str, Any]:
    await _ensure_total_indexes(context.db)
    campaign_rows = [row for row in rows if _text(row.get("campaign_id"))]
    for row in campaign_rows:
        await _upsert_total_row(
            context,
            account=account,
            entity_type="campaign",
            external_id=_text(row.get("campaign_id")),
            date_string=date_string,
            timezone_name=timezone_name,
            metrics=dict(row.get("metrics") or {}),
            provider_start=row.get("start_time") or provider_start,
            provider_end=row.get("end_time") or provider_end,
            provider_breakdown=PLATFORM_TOTAL_BREAKDOWN,
            action_report_time=action_report_time,
        )
    all_ads_metrics = merge_direct_spend_with_campaign_metrics(
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
        provider_start=provider_start,
        provider_end=provider_end,
        provider_breakdown=None,
        action_report_time=action_report_time,
    )

    if authoritative_breakdown and not errors:
        campaign_ids = [
            _text(row.get("campaign_id"))
            for row in campaign_rows
            if _text(row.get("campaign_id"))
        ]
        stale_query: dict[str, Any] = {
            "user_id": context.user_id,
            "provider": SNAPCHAT_PROVIDER_ID,
            "ad_account_id": account["ad_account_id"],
            "entity_type": "campaign",
            "date": date_string,
            "date_timezone": timezone_name,
            "attribution_model": ATTRIBUTION_MODEL,
            "action_report_time": action_report_time,
        }
        if campaign_ids:
            stale_query["external_id"] = {"$nin": campaign_ids}
        await _collection(
            context.db,
            SNAPCHAT_ACCOUNT_TOTAL_COLLECTION,
        ).delete_many(stale_query)

    return {
        "campaign_rows_saved": len(campaign_rows),
        "account_rows_saved": 1,
        "authoritative_breakdown": bool(authoritative_breakdown and not errors),
    }


async def refresh_account_total_snapshots(
    context: SnapchatSyncContext,
    client: httpx.AsyncClient,
    access_token: str,
    account: dict[str, Any],
    *,
    start_date: date,
    end_date: date,
    now: datetime | None = None,
) -> dict[str, Any]:
    account_id = _text(account.get("ad_account_id"))
    if not account_id:
        raise SnapchatNativeSyncError(
            "snapchat_account_id_missing",
            "Selected Snapchat account is missing its ad account ID.",
            status_code=409,
        )
    timezone_name = manager._valid_timezone_name(account.get("timezone"))
    dates = account_local_dates_for_refresh(
        start_date,
        end_date,
        timezone_name=timezone_name,
        now=now,
    )
    saved = 0
    campaign_saved = 0
    errors: list[dict[str, Any]] = []
    request_windows: list[dict[str, str]] = []
    for report_date in dates:
        window = account_local_total_window(
            report_date,
            timezone_name=timezone_name,
            now=now,
        )
        if window is None:
            continue
        request_start, request_end = window
        request_granularity = PLATFORM_TOTAL_GRANULARITY
        request_windows.append({
            "date": report_date.isoformat(),
            "start_time": request_start.isoformat(timespec="seconds"),
            "end_time": request_end.isoformat(timespec="seconds"),
            "granularity": request_granularity,
        })
        try:
            for action_report_time in ADS_MANAGER_SUPPORTED_ACTION_REPORT_TIMES:
                rows, campaign_errors, breakdown_seen = (
                    await fetch_account_total_campaign_rows(
                        context,
                        client,
                        access_token,
                        account_id=account_id,
                        request_start=request_start,
                        request_end=request_end,
                        granularity=request_granularity,
                        action_report_time=action_report_time,
                    )
                )
                account_metrics = aggregate_total_campaign_metrics(rows)
                account_errors: list[dict[str, Any]] = []
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
                            "Snapchat platform snapshot was incomplete; "
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
        except SnapchatNativeSyncError as exc:
            if exc.code == "snapchat_needs_reauth":
                raise
            errors.append({
                "date": report_date.isoformat(),
                "code": exc.code,
                "message": exc.message,
                "retryable": exc.retryable,
            })

    return {
        "provider": SNAPCHAT_PROVIDER_ID,
        "ad_account_id": account_id,
        "date_from": dates[0].isoformat() if dates else None,
        "date_to": dates[-1].isoformat() if dates else None,
        "account_total_rows_saved": saved,
        "campaign_total_rows_saved": campaign_saved,
        "errors_count": len(errors),
        "errors": errors,
        "provider_calls": context.provider_calls,
        "source_mode": PLATFORM_TOTAL_SOURCE_MODE,
        "supported_action_report_times": list(ADS_MANAGER_SUPPORTED_ACTION_REPORT_TIMES),
        "provider_granularity": PLATFORM_TOTAL_GRANULARITY,
        "current_day_provider_granularity": PLATFORM_TOTAL_GRANULARITY,
        "provider_breakdown": PLATFORM_TOTAL_BREAKDOWN,
        "direct_account_total_requested": False,
        "direct_account_request_fields": [],
        "account_spend_source": "complete_campaign_breakdown_rollup",
        "account_commercial_totals_source": "complete_campaign_breakdown_rollup",
        "current_day_total_window_policy": TOTAL_CURRENT_DAY_WINDOW_POLICY,
        "request_windows": request_windows,
        "account_timezone": timezone_name,
        "business_timezone": BUSINESS_TIMEZONE,
        "source_only": True,
        "provider_write_reached": False,
        "campaign_write_reached": False,
        "accounting_write_reached": False,
        "qoyod_write_reached": False,
    }


async def _to_list(cursor: Any, length: int) -> list[dict[str, Any]]:
    if hasattr(cursor, "to_list"):
        return await cursor.to_list(length=length)
    return [row async for row in cursor]


async def load_platform_total_rows(
    db: Any,
    user_id: str,
    *,
    account_id: str,
    date_from: str,
    date_to: str,
    timezone_name: str,
    action_report_time: str = ADS_MANAGER_DEFAULT_ACTION_REPORT_TIME,
) -> list[dict[str, Any]]:
    cursor = _collection(db, SNAPCHAT_ACCOUNT_TOTAL_COLLECTION).find(
        {
            "user_id": user_id,
            "provider": SNAPCHAT_PROVIDER_ID,
            "ad_account_id": account_id,
            "date": {"$gte": date_from, "$lte": date_to},
            "date_timezone": timezone_name,
            "source_mode": platform_total_source_mode(action_report_time),
            "action_report_time": normalize_ads_manager_action_report_time(action_report_time),
            "provider_granularity": PLATFORM_TOTAL_GRANULARITY,
        },
        {"_id": 0},
    )
    rows = await _to_list(cursor, MAX_TOTAL_ROWS + 1)
    if len(rows) > MAX_TOTAL_ROWS:
        raise SnapchatNativeSyncError(
            "snapchat_platform_total_row_limit",
            "بلغت قراءة TOTAL الحد الآمن؛ قلّص الفترة وأعد المحاولة.",
            status_code=409,
        )
    return rows


def _campaign_key(row: dict[str, Any]) -> str:
    return _text(row.get("campaign_id") or row.get("external_id"))


def _platform_result_from_metrics(metrics: dict[str, Any]) -> dict[str, Any]:
    return {
        "orders": int(metrics.get("orders") or 0),
        "sales_sar": round(float(metrics.get("sales_sar") or 0), 2),
        "sales_native": metrics.get("sales_native"),
    }


def _restore_platform_campaign(
    campaign: dict[str, Any],
    metrics: dict[str, Any],
) -> dict[str, Any]:
    restored = dict(campaign)
    salla_profitability = restored.get("profitability")
    restored.pop("profitability", None)
    spend_sar = _number(metrics.get("spend_sar"))
    spend_native = _number(metrics.get("spend_native"))
    sales_sar = _number(metrics.get("sales_sar")) or 0.0
    orders = int(metrics.get("orders") or 0)
    restored.update({
        **metrics,
        "orders": orders,
        "sales_sar": round(sales_sar, 2),
        "roas": _ratio(sales_sar, spend_sar),
        "cpa_sar": _ratio(spend_sar, orders),
        "cpa_native": _ratio(spend_native, orders),
        "result_source": RESULT_SOURCE_PLATFORM,
        "platform_results": _platform_result_from_metrics(metrics),
        "snapchat_purchases": orders,
        "snapchat_purchase_value_sar": round(sales_sar, 2),
        "snapchat_spend_sar": spend_sar,
        "salla_profitability": salla_profitability,
        "commercial_metrics_source": "snapchat_ads_api",
        "profitability": None,
    })
    return restored


def platform_rows_by_campaign(
    rows: list[dict[str, Any]],
    *,
    requested_days: int,
) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row.get("entity_type") != "campaign":
            continue
        campaign_id = _campaign_key(row)
        if campaign_id:
            grouped[campaign_id].append(row)
    return {
        campaign_id: manager._aggregate_rows(
            campaign_rows,
            requested_days=requested_days,
        )
        for campaign_id, campaign_rows in grouped.items()
    }


def _scope_campaigns(
    campaigns: list[dict[str, Any]],
    *,
    active_campaigns_only: bool,
    campaign_query: str | None,
) -> list[dict[str, Any]]:
    scoped = list(campaigns)
    if active_campaigns_only:
        scoped = [row for row in scoped if row.get("campaign_active") is True]
    needle = _text(campaign_query).casefold()[:120]
    if needle:
        scoped = [
            row
            for row in scoped
            if needle
            in " ".join([
                _text(row.get("campaign_name")),
                _text(row.get("campaign_id")),
                _text(row.get("account_name")),
            ]).casefold()
        ]
    scoped.sort(
        key=lambda row: (
            -(float(row.get("spend_sar") or 0)),
            _text(row.get("campaign_name")).casefold(),
        )
    )
    return scoped


def _mask_pending_platform_commercial_metrics(
    result: dict[str, Any],
    *,
    action_report_time: str,
) -> dict[str, Any]:
    """Hide commercial metrics until the selected TOTAL partition is ready."""
    action_report_time = normalize_ads_manager_action_report_time(
        action_report_time
    )
    commercial_nulls = {
        "orders": None,
        "sales_sar": None,
        "sales_native": None,
        "roas": None,
        "cpa_sar": None,
        "cpa_native": None,
        "result_source": RESULT_SOURCE_PLATFORM,
        "commercial_metrics_source": f"snapchat_{action_report_time}_total_pending",
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
    campaigns: list[dict[str, Any]],
    *,
    requested_days: int,
) -> dict[str, Any]:
    if not campaigns:
        empty = manager._aggregate_rows([], requested_days=requested_days)
        return {
            **empty,
            "spend_sar": 0.0,
            "spend_native": 0.0,
            "sales_sar": 0.0,
            "sales_native": 0.0,
            "orders": 0,
            "roas": None,
            "cpa_sar": None,
            "cpa_native": None,
        }
    return manager._aggregate_rows(campaigns, requested_days=requested_days)


async def apply_platform_snapshot_to_report(
    db: Any,
    user_id: str,
    result: dict[str, Any],
    *,
    active_campaigns_only: bool,
    campaign_query: str | None,
    page: int,
    limit: int,
) -> dict[str, Any]:
    """Attach authoritative TOTAL facts, replacing only the platform view."""
    platform_view = (
        _text(result.get("result_source")).lower() == RESULT_SOURCE_PLATFORM
    )

    account_id = _text(result.get("selected_account_id"))
    date_from = _text(result.get("date_from"))
    date_to = _text(result.get("date_to"))
    timezone_name = _text(result.get("account_timezone"))
    action_report_time = normalize_ads_manager_action_report_time(
        result.get("action_report_time")
    )
    if not all((account_id, date_from, date_to, timezone_name)):
        return result
    try:
        rows = await load_platform_total_rows(
            db,
            user_id,
            account_id=account_id,
            date_from=date_from,
            date_to=date_to,
            timezone_name=timezone_name,
            action_report_time=action_report_time,
        )
    except Exception:
        result.setdefault("source", {}).update({
            "platform_total_snapshot_ready": False,
            "platform_direct_account_total_ready": False,
            "platform_source_status": "failed",
            "platform_source_reason": "snapchat_total_read_failed",
        })
        if not platform_view:
            return result
        return _mask_pending_platform_commercial_metrics(
            result,
            action_report_time=action_report_time,
        )
    account_rows = [row for row in rows if row.get("entity_type") == "ad_account"]
    campaign_rows = [row for row in rows if row.get("entity_type") == "campaign"]
    base_snapchat_status = _text(result.get("snapchat_status")).lower()
    if base_snapchat_status not in {"complete", "partial", "stale", "failed"}:
        base_snapchat_status = "partial"
    platform_source_status = base_snapchat_status
    platform_source_reason = _text(
        (result.get("coverage_reasons") or {}).get("snapchat")
    ) or "provider_window_not_complete"
    if account_rows and campaign_rows and base_snapchat_status == "complete":
        platform_source_status = "complete"
        platform_source_reason = "selected_account_total_complete"
    elif not account_rows or not campaign_rows:
        platform_source_status = (
            "failed" if base_snapchat_status == "failed" else "partial"
        )
        platform_source_reason = "account_or_campaign_total_missing"
    if (not account_rows or not campaign_rows) and platform_view:
        result.setdefault("insights", []).append({
            "code": "snapchat_platform_total_snapshot_pending",
            "severity": "warning",
            "title": "نتائج Snapchat المباشرة قيد المزامنة",
            "detail": (
                "مصدر الحساب الإعلاني يعرض بيانات Snapchat فقط، "
                "وسيكتمل بعد دورة المزامنة التالية."
            ),
        })
        result.setdefault("source", {}).update({
            "platform_total_snapshot_ready": False,
            "platform_direct_account_total_ready": False,
            "platform_source_isolated": True,
            "platform_action_report_time": action_report_time,
            "platform_total_source_mode": platform_total_source_mode(action_report_time),
        })
        result.setdefault("ai_readiness", {}).update({
            "report_ready": False,
            "ai_analysis_ready": False,
        })
        result.setdefault("policy", {}).update({
            "platform_source_isolated": True,
            "salla_metrics_applied_to_platform": False,
            "legacy_hour_conversions_hidden_while_pending": True,
        })
        return _mask_pending_platform_commercial_metrics(
            result,
            action_report_time=action_report_time,
        )

    requested_days = (
        date.fromisoformat(date_to) - date.fromisoformat(date_from)
    ).days + 1
    metrics_by_campaign = platform_rows_by_campaign(
        campaign_rows,
        requested_days=requested_days,
    )
    if not platform_view:
        account_total = (
            manager._aggregate_rows(account_rows, requested_days=requested_days)
            if account_rows else {}
        )
        for campaign in result.get("campaigns") or []:
            campaign_id = _campaign_key(campaign)
            metrics = metrics_by_campaign.get(campaign_id)
            campaign.update({
                "snapchat_purchases": (
                    metrics.get("orders") if metrics is not None else None
                ),
                "snapchat_purchase_value_sar": (
                    metrics.get("sales_sar") if metrics is not None else None
                ),
                "snapchat_spend_sar": (
                    metrics.get("spend_sar") if metrics is not None else None
                ),
            })
        result.setdefault("totals", {}).update({
            "snapchat_purchases": account_total.get("orders"),
            "snapchat_purchase_value_sar": account_total.get("sales_sar"),
            "snapchat_spend_sar": account_total.get("spend_sar"),
        })
        result["snapchat_as_of"] = account_total.get("last_observed_at")
        result.setdefault("source", {}).update({
            "platform_total_collection": SNAPCHAT_ACCOUNT_TOTAL_COLLECTION,
            "platform_total_snapshot_ready": bool(account_rows and campaign_rows),
            "platform_direct_account_total_ready": bool(account_rows),
            "platform_source_status": (
                platform_source_status
            ),
            "platform_source_reason": platform_source_reason,
            "account_spend_source": "direct_ad_account_total",
            "platform_source_isolated": True,
            "salla_metrics_applied_to_platform": False,
        })
        return result
    existing = {
        _campaign_key(row): row
        for row in result.get("campaigns") or []
        if _campaign_key(row)
    }
    entity_cursor = _collection(db, SNAPCHAT_ENTITY_COLLECTION).find(
        {
            "user_id": user_id,
            "provider": SNAPCHAT_PROVIDER_ID,
            "ad_account_id": account_id,
            "entity_type": "campaign",
        },
        {"_id": 0},
    )
    entities = {
        _text(row.get("external_id")): row
        for row in await _to_list(entity_cursor, manager.MAX_ENTITY_ROWS)
        if _text(row.get("external_id"))
    }
    selected_account = result.get("selected_account") or {}
    account_name = _text(
        selected_account.get("account_name")
        or (result.get("accounts") or [{}])[0].get("account_name")
        or account_id
    )
    currency = _text(
        selected_account.get("currency")
        or (result.get("accounts") or [{}])[0].get("currency")
    ) or None
    rebuilt: list[dict[str, Any]] = []
    for campaign_id in sorted(set(metrics_by_campaign) | set(entities)):
        metrics = metrics_by_campaign.get(campaign_id) or manager._aggregate_rows(
            [], requested_days=requested_days
        )
        base = dict(existing.get(campaign_id) or {})
        entity = entities.get(campaign_id, {})
        status = entity.get("status") or base.get("status") or "unknown"
        base.update({
            "account_id": account_id,
            "account_name": account_name,
            "campaign_id": campaign_id,
            "campaign_name": (
                _text(entity.get("display_name"))
                or _text(base.get("campaign_name"))
                or campaign_id
            ),
            "status": status,
            "configured_status": status,
            "delivery_status": (
                entity.get("delivery_status") or base.get("delivery_status")
            ),
            "objective": entity.get("objective") or base.get("objective"),
            "start_time": entity.get("start_time") or base.get("start_time"),
            "end_time": entity.get("end_time") or base.get("end_time"),
            "budget": {
                "currency": currency,
                "daily_native": (
                    round(float(entity["daily_budget_micro"]) / 1_000_000, 6)
                    if _number(entity.get("daily_budget_micro")) is not None
                    else (base.get("budget") or {}).get("daily_native")
                ),
                "lifetime_native": (
                    round(float(entity["lifetime_spend_cap_micro"]) / 1_000_000, 6)
                    if _number(entity.get("lifetime_spend_cap_micro")) is not None
                    else (base.get("budget") or {}).get("lifetime_native")
                ),
            },
            "campaign_active": is_active_provider_status(status),
        })
        rebuilt.append(_restore_platform_campaign(base, metrics))

    scoped = _scope_campaigns(
        rebuilt,
        active_campaigns_only=active_campaigns_only,
        campaign_query=campaign_query,
    )
    total_campaigns = len(scoped)
    pages = (total_campaigns + limit - 1) // limit if total_campaigns else 0
    offset = (page - 1) * limit
    visible = scoped[offset:offset + limit]

    if not active_campaigns_only and not _text(campaign_query) and account_rows:
        totals = manager._aggregate_rows(account_rows, requested_days=requested_days)
        totals_scope = "all_ads_direct_account_total"
    else:
        totals = _aggregate_visible_campaigns(scoped, requested_days=requested_days)
        totals_scope = "filtered_campaign_sum"
    previous_totals = dict(result.get("totals") or {})
    totals["salla_total_orders"] = previous_totals.get("salla_total_orders")
    totals["salla_matched_orders"] = previous_totals.get("salla_matched_orders")
    totals["salla_unmatched_orders"] = previous_totals.get("salla_unmatched_orders")
    totals["salla_sales_sar"] = previous_totals.get("salla_sales_sar")
    totals["salla_profitability"] = previous_totals.get("profitability")
    totals["snapchat_purchases"] = totals.get("orders")
    totals["snapchat_purchase_value_sar"] = totals.get("sales_sar")
    totals["snapchat_spend_sar"] = totals.get("spend_sar")
    totals.pop("profitability", None)
    totals["result_source"] = RESULT_SOURCE_PLATFORM

    daily_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in account_rows:
        daily_groups[_text(row.get("date"))].append(row)
    daily: list[dict[str, Any]] = []
    cursor = date.fromisoformat(date_from)
    last = date.fromisoformat(date_to)
    while cursor <= last:
        date_string = cursor.isoformat()
        daily.append({
            "date": date_string,
            **manager._aggregate_rows(
                daily_groups.get(date_string, []),
                requested_days=1,
            ),
            "result_source": RESULT_SOURCE_PLATFORM,
        })
        cursor += timedelta(days=1)

    result.update({
        "totals": totals,
        "daily": daily,
        "campaigns": visible,
        "campaign_pagination": {
            "page": page,
            "limit": limit,
            "total": total_campaigns,
            "pages": pages,
        },
    })
    if result.get("accounts"):
        result["accounts"][0].update({
            **totals,
            "result_source": RESULT_SOURCE_PLATFORM,
        })
    campaign_sum = _aggregate_visible_campaigns(
        rebuilt,
        requested_days=requested_days,
    )
    account_total = (
        manager._aggregate_rows(account_rows, requested_days=requested_days)
        if account_rows
        else {}
    )
    result.setdefault("source", {}).update({
        "platform_total_collection": SNAPCHAT_ACCOUNT_TOTAL_COLLECTION,
        "platform_total_source_mode": platform_total_source_mode(
            action_report_time
        ),
        "platform_total_snapshot_ready": bool(account_rows and campaign_rows),
        "platform_direct_account_total_ready": bool(account_rows),
        "platform_source_status": platform_source_status,
        "platform_source_reason": platform_source_reason,
        "platform_source_isolated": True,
        "platform_action_report_time": action_report_time,
        "account_spend_source": "direct_ad_account_total",
        "account_commercial_totals_source": "direct_ad_account_total",
        "platform_totals_scope": totals_scope,
        "platform_account_orders": account_total.get("orders"),
        "platform_campaign_orders": campaign_sum.get("orders"),
        "platform_account_sales_sar": account_total.get("sales_sar"),
        "platform_campaign_sales_sar": campaign_sum.get("sales_sar"),
        "platform_account_spend_sar": account_total.get("spend_sar"),
        "platform_campaign_spend_sar": campaign_sum.get("spend_sar"),
        "salla_metrics_applied_to_platform": False,
    })
    result.setdefault("ai_readiness", {}).update({
        "report_ready": bool(account_rows and campaign_rows),
        "orders_ready": totals.get("orders") is not None,
        "sales_ready": totals.get("sales_sar") is not None,
        "ratios_ready": any(
            totals.get(key) is not None for key in ("roas", "cpa_sar")
        ),
        "ai_analysis_ready": (
            bool(account_rows and campaign_rows)
            and action_report_time
            == ADS_MANAGER_DEFAULT_ACTION_REPORT_TIME
        ),
        "ai_action_report_time": ADS_MANAGER_DEFAULT_ACTION_REPORT_TIME,
        "selected_action_report_time": action_report_time,
    })
    result.setdefault("policy", {}).update({
        "platform_source_isolated": True,
        "salla_metrics_applied_to_platform": False,
        "platform_profitability_hidden": True,
    })
    return result


def audit_platform_purchase_totals(
    rows: list[dict[str, Any]],
    *,
    requested_days: int,
) -> tuple[int, int, str]:
    account_rows = [row for row in rows if row.get("entity_type") == "ad_account"]
    campaign_rows = [row for row in rows if row.get("entity_type") == "campaign"]
    if account_rows:
        account = manager._aggregate_rows(
            account_rows,
            requested_days=requested_days,
        )
        campaigns = manager._aggregate_rows(
            campaign_rows,
            requested_days=requested_days,
        )
        return (
            int(account.get("orders") or 0),
            int(campaigns.get("orders") or 0),
            "campaign_breakdown_all_ads_snapshot",
        )
    campaigns = manager._aggregate_rows(
        campaign_rows,
        requested_days=requested_days,
    )
    count = int(campaigns.get("orders") or 0)
    return count, count, "campaign_total_fallback"


def install_snapchat_platform_source_integrity() -> None:
    """Install TOTAL refresh, report isolation and audit diagnostics."""
    current_refresh: RefreshCallable = hourly.refresh_snapchat_account_hours
    if not getattr(current_refresh, "_mezan_platform_total_refresh", False):
        async def refresh_with_platform_total(
            context: SnapchatSyncContext,
            client: httpx.AsyncClient,
            access_token: str,
            account: dict[str, Any],
            *args: Any,
            **kwargs: Any,
        ) -> dict[str, Any]:
            output = dict(await current_refresh(
                context,
                client,
                access_token,
                account,
                *args,
                **kwargs,
            ) or {})
            refresh_start = kwargs.get("start_date")
            refresh_end = kwargs.get("end_date")
            if not isinstance(refresh_start, date) and args:
                refresh_start = args[0]
            if not isinstance(refresh_end, date) and len(args) > 1:
                refresh_end = args[1]
            if not isinstance(refresh_start, date) or not isinstance(refresh_end, date):
                return output
            total = await refresh_account_total_snapshots(
                context,
                client,
                access_token,
                account,
                start_date=refresh_start,
                end_date=refresh_end,
                now=kwargs.get("now"),
            )
            output["platform_total_snapshot"] = total
            output["rows_saved"] = int(output.get("rows_saved") or 0) + int(
                total.get("account_total_rows_saved") or 0
            ) + int(total.get("campaign_total_rows_saved") or 0)
            output["errors_count"] = int(output.get("errors_count") or 0) + int(
                total.get("errors_count") or 0
            )
            output["errors"] = list(output.get("errors") or []) + list(
                total.get("errors") or []
            )
            return output

        refresh_with_platform_total._mezan_platform_total_refresh = True  # type: ignore[attr-defined]
        refresh_with_platform_total._mezan_platform_total_base = current_refresh  # type: ignore[attr-defined]
        hourly.refresh_snapchat_account_hours = refresh_with_platform_total

    current_report: ReportCallable = manager.build_account_timezone_campaign_report
    if not getattr(current_report, "_mezan_platform_source_integrity", False):
        async def report_with_platform_integrity(
            *args: Any,
            **kwargs: Any,
        ) -> dict[str, Any]:
            result = dict(await current_report(*args, **kwargs) or {})
            db = args[0] if args else kwargs.get("db")
            user_id = args[1] if len(args) > 1 else kwargs.get("user_id")
            if db is None or not user_id:
                return result
            return await apply_platform_snapshot_to_report(
                db,
                str(user_id),
                result,
                active_campaigns_only=bool(
                    kwargs.get(
                        "active_campaigns_only",
                        result.get("active_campaigns_only", False),
                    )
                ),
                campaign_query=kwargs.get("campaign_query"),
                page=int(kwargs.get("page") or 1),
                limit=int(kwargs.get("limit") or 25),
            )

        report_with_platform_integrity._mezan_platform_source_integrity = True  # type: ignore[attr-defined]
        report_with_platform_integrity._mezan_platform_source_base = current_report  # type: ignore[attr-defined]
        manager.build_account_timezone_campaign_report = report_with_platform_integrity

    from . import snapchat_native_tracking_routes as tracking_routes
    from . import snapchat_order_source_audit as audit

    current_audit: AuditCallable = audit.build_snapchat_order_source_audit
    if not getattr(current_audit, "_mezan_platform_total_audit", False):
        async def audit_with_platform_total(
            db: Any,
            user_id: str,
            *args: Any,
            **kwargs: Any,
        ) -> dict[str, Any]:
            result = dict(await current_audit(db, user_id, *args, **kwargs) or {})
            account = result.get("account") or {}
            account_id = _text(
                account.get("account_id") or account.get("ad_account_id")
            )
            audit_from = _text(result.get("date_from"))
            audit_to = _text(result.get("date_to"))
            timezone_name = _text(
                account.get("timezone")
                or result.get("summary", {}).get("date_timezone")
            )
            if all((account_id, audit_from, audit_to, timezone_name)):
                rows = await load_platform_total_rows(
                    db,
                    user_id,
                    account_id=account_id,
                    date_from=audit_from,
                    date_to=audit_to,
                    timezone_name=timezone_name,
                    action_report_time=ADS_MANAGER_DEFAULT_ACTION_REPORT_TIME,
                )
                requested_days = (
                    date.fromisoformat(audit_to) - date.fromisoformat(audit_from)
                ).days + 1
                account_count, campaign_count, source = (
                    audit_platform_purchase_totals(
                        rows,
                        requested_days=requested_days,
                    )
                )
                summary = result.setdefault("summary", {})
                summary.update({
                    "platform_attributed_purchases": account_count,
                    "platform_campaign_purchases": campaign_count,
                    "platform_purchase_source": source,
                    "platform_minus_confirmed_campaign_orders": (
                        account_count
                        - int(summary.get("campaign_matched_orders") or 0)
                    ),
                    "platform_account_minus_campaign_rows": (
                        account_count - campaign_count
                    ),
                })
            return result

        audit_with_platform_total._mezan_platform_total_audit = True  # type: ignore[attr-defined]
        audit.build_snapchat_order_source_audit = audit_with_platform_total
        tracking_routes.build_snapchat_order_source_audit = audit_with_platform_total


__all__ = [
    "DIRECT_ACCOUNT_TOTAL_FIELDS",
    "PLATFORM_TOTAL_BREAKDOWN",
    "PLATFORM_TOTAL_GRANULARITY",
    "PLATFORM_TOTAL_SOURCE_MODE",
    "REQUIRED_ACCOUNT_TOTAL_FIELDS",
    "SNAPCHAT_ACCOUNT_TOTAL_COLLECTION",
    "account_local_dates_for_refresh",
    "account_local_total_window",
    "aggregate_total_campaign_metrics",
    "apply_platform_snapshot_to_report",
    "audit_platform_purchase_totals",
    "extract_account_total_campaign_rows",
    "extract_account_total_metrics",
    "fetch_account_total_campaign_rows",
    "fetch_account_total_direct_metrics",
    "install_snapchat_platform_source_integrity",
    "load_platform_total_rows",
    "merge_direct_spend_with_campaign_metrics",
    "persist_account_total_day",
    "platform_rows_by_campaign",
    "refresh_account_total_snapshots",
    "total_snapshot_is_authoritative",
]
