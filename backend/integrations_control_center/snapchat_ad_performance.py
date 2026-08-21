"""Read-only Snapchat Ad performance for Ads Manager.

Ad-level facts are captured from the Campaign Stats endpoint with
``breakdown=ad`` and authoritative per-day TOTAL granularity. The same
account-local date semantics
used by Campaign and Ad Squad reports are preserved. These rows are diagnostic
and Ads-Manager-only: they are never accounting eligible and never write to
Snapchat.
"""
from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import date, datetime, timezone
from typing import Any, Callable

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query

from . import snapchat_account_hourly_refresh as hourly
from .snapchat_account_selection import _load_selected_accounts
from .snapchat_freshness_impl_v6 import (
    ADS_MANAGER_ACTION_REPORT_TIME,
    ADS_MANAGER_DEFAULT_ACTION_REPORT_TIME,
    ADS_MANAGER_SUPPORTED_ACTION_REPORT_TIMES,
    ADS_MANAGER_SWIPE_ATTRIBUTION_WINDOW,
    ADS_MANAGER_VIEW_ATTRIBUTION_WINDOW,
    ads_manager_source_mode,
    normalize_ads_manager_action_report_time,
)
from .snapchat_active_campaign_filtering import (
    aggregate_entity_rows,
    is_active_provider_status,
    normalize_entity_sort,
    sort_entity_rows,
)
from .snapchat_account_timezone_manager import (
    SNAPCHAT_ACCOUNT_LOCAL_PERFORMANCE_COLLECTION,
    _account_public_row,
    _aggregate_rows,
    _aware_now,
    _combined_request_window,
    _effective_currency,
    _text,
    _valid_timezone_name,
    resolve_account_report_dates,
)
from .snapchat_adsquad_performance import (
    CONFIRMED_DATA_STATES,
    DATA_STATE_CONFIRMED_DATA,
    DATA_STATE_CONFIRMED_NO_DATA,
    DATA_STATE_CONFIRMED_ZERO,
    DATA_STATE_UNKNOWN_INCOMPLETE,
    _campaign_entities,
    _has_numeric_provider_metric,
    _number,
    _performance_data_state,
    _to_list,
    _valid_provider_window,
)
from .snapchat_native_data_common import (
    ATTRIBUTION_MODEL,
    BUSINESS_TIMEZONE,
    MAX_PAGES,
    SNAPCHAT_API_BASE,
    SNAPCHAT_ENTITY_COLLECTION,
    SNAPCHAT_PERFORMANCE_COLLECTION,
    SNAPCHAT_PROVIDER_ID,
    SnapchatNativeSyncError,
    SnapchatSyncContext,
    _as_number,
    _collection,
    _parse_datetime,
    _safe_next_url,
    _timezone,
    _utcnow,
)
from .snapchat_native_entities_sync import sync_snapchat_ad_entities
from .snapchat_native_performance_sync import (
    CONVERSION_SOURCE_TYPES,
    STAT_FIELDS,
    TOTAL_STAT_FIELDS,
    SWIPE_ATTRIBUTION_WINDOW,
    VIEW_ATTRIBUTION_WINDOW,
    _add_to_bucket,
    _computed,
    _finalize_bucket,
    _funnel_metrics,
    _metric_provenance,
    _new_bucket,
)

def ad_source_mode(action_report_time: Any) -> str:
    return (
        f"{ads_manager_source_mode(action_report_time)}:"
        "ad_active_campaign_account_day_total_v4"
    )


AD_SOURCE_MODE = ad_source_mode(ADS_MANAGER_DEFAULT_ACTION_REPORT_TIME)
AD_REFRESH_SOURCE_MODE = (
    "snapchat_ads_manager_dual_attribution_ad_active_bounded_total_v2"
)
AD_REFRESH_STATE_COLLECTION = "mezan_snapchat_ad_refresh_state_v1"
AD_BREAKDOWN = "ad"
AD_PROVIDER_GRANULARITY = "TOTAL"
AD_FETCH_CONCURRENCY = 6
AD_REFRESH_INTERVAL_SECONDS = 15 * 60
MAX_REPORT_ROWS = 150_000
MAX_ENTITY_ROWS = 75_000

# The production catalog contains thousands of Ads and Creatives. Loading every
# provider snapshot here duplicates the Ad Squad report working set and can
# exhaust the web worker before FastAPI writes a response. Keep this projection
# explicit so the read-only report scales with the catalog while retaining every
# field rendered by the UI.
AD_REPORT_ENTITY_PROJECTION = {
    "_id": 0,
    "entity_type": 1,
    "external_id": 1,
    "display_name": 1,
    "status": 1,
    "campaign_id": 1,
    "ad_squad_id": 1,
    "creative_id": 1,
    "review_status": 1,
    "deleted": 1,
    "created_at_provider": 1,
    "updated_at_provider": 1,
    "delivery_state": 1,
    "delivery_status": 1,
    "delivery_reason_code": 1,
    "provider_snapshot.type": 1,
    "provider_snapshot.creative_type": 1,
    "provider_snapshot.top_snap_media_id": 1,
    "provider_snapshot.media_id": 1,
    "provider_snapshot.ad_squad_id": 1,
    "provider_snapshot.adsquad_id": 1,
    "provider_snapshot.campaign_id": 1,
    "provider_snapshot.web_view_properties.url": 1,
}


def extract_ad_hour_rows(
    payload: dict[str, Any],
    *,
    campaign_id: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    """Extract HOUR Ad rows from a Campaign Stats breakdown response."""
    if not isinstance(payload, dict) or str(
        payload.get("request_status") or ""
    ).upper() != "SUCCESS":
        raise SnapchatNativeSyncError(
            "snapchat_ad_timeseries_request_incomplete",
            "Snapchat did not confirm a successful Ad timeseries response.",
            status_code=502,
            retryable=True,
        )
    wrapped_stats = payload.get("timeseries_stats")
    if not isinstance(wrapped_stats, list):
        raise SnapchatNativeSyncError(
            "snapchat_ad_stats_payload_invalid",
            "Snapchat returned invalid Ad performance data.",
            status_code=502,
            retryable=True,
        )
    rows: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    successful = 0
    if not wrapped_stats:
        errors.append({
            "kind": "ad_stats",
            "campaign_id": campaign_id,
            "code": "snapchat_ad_timeseries_stats_missing",
            "message": "Snapchat returned no Ad timeseries result envelope.",
            "retryable": True,
        })
        return rows, errors, successful
    for wrapped in wrapped_stats:
        if not isinstance(wrapped, dict):
            errors.append({
                "kind": "ad_stats",
                "campaign_id": campaign_id,
                "code": "snapchat_ad_timeseries_wrapper_invalid",
                "message": "Snapchat returned an invalid Ad timeseries wrapper.",
                "retryable": True,
            })
            continue
        status = str(wrapped.get("sub_request_status") or "").upper()
        if status != "SUCCESS":
            errors.append({
                "kind": "ad_stats",
                "campaign_id": campaign_id,
                "code": "snapchat_ad_timeseries_subrequest_incomplete",
                "message": "Snapchat did not confirm the Ad timeseries subrequest.",
                "error": status[:100],
                "retryable": True,
            })
            continue
        stat = wrapped.get("timeseries_stat")
        if not isinstance(stat, dict):
            errors.append({
                "kind": "ad_stats",
                "campaign_id": campaign_id,
                "code": "snapchat_ad_timeseries_stat_missing",
                "message": "Snapchat success response omitted timeseries_stat.",
                "retryable": True,
            })
            continue
        breakdown = stat.get("breakdown_stats")
        candidates: list[dict[str, Any]] = []
        structure_seen = False
        if isinstance(breakdown, dict):
            for key in ("ad", "ads"):
                if key not in breakdown:
                    continue
                value = breakdown[key]
                if not isinstance(value, list):
                    continue
                structure_seen = True
                candidates.extend(value)
        if not candidates and str(stat.get("type") or "").upper() == "AD":
            structure_seen = True
            candidates = [stat]
        if not structure_seen:
            errors.append({
                "kind": "ad_stats",
                "campaign_id": campaign_id,
                "code": "snapchat_ad_timeseries_breakdown_missing",
                "message": "Snapchat timeseries_stat omitted an Ad breakdown.",
                "retryable": True,
            })
            continue
        wrapper_valid = True
        for entity in candidates:
            if not isinstance(entity, dict):
                wrapper_valid = False
                continue
            ad_id = _text(entity.get("id"))
            points = entity.get("timeseries")
            if not ad_id or not isinstance(points, list):
                wrapper_valid = False
                continue
            for point in points:
                metrics = point.get("stats") if isinstance(point, dict) else None
                if (
                    not _has_numeric_provider_metric(metrics)
                    or not isinstance(point, dict)
                    or not _valid_provider_window(
                        point.get("start_time"),
                        point.get("end_time"),
                    )
                ):
                    wrapper_valid = False
                    continue
                rows.append({
                    "campaign_id": campaign_id,
                    "ad_id": ad_id,
                    "start_time": point.get("start_time"),
                    "end_time": point.get("end_time"),
                    "metrics": metrics,
                })
        if not wrapper_valid:
            errors.append({
                "kind": "ad_stats",
                "campaign_id": campaign_id,
                "code": "snapchat_ad_timeseries_row_invalid",
                "message": "Snapchat returned a partial or invalid Ad timeseries row.",
                "retryable": True,
            })
            continue
        successful += 1
    return rows, errors, successful


async def _fetch_campaign_ad_hours(
    context: SnapchatSyncContext,
    client: httpx.AsyncClient,
    access_token: str,
    *,
    campaign_id: str,
    request_start: datetime,
    request_end: datetime,
    action_report_time: str = ADS_MANAGER_DEFAULT_ACTION_REPORT_TIME,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    url = f"{SNAPCHAT_API_BASE}/campaigns/{campaign_id}/stats"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
    }
    params: dict[str, Any] | None = {
        "start_time": request_start.isoformat(timespec="seconds"),
        "end_time": request_end.isoformat(timespec="seconds"),
        "granularity": "HOUR",
        "breakdown": AD_BREAKDOWN,
        "fields": ",".join(STAT_FIELDS),
        "limit": 200,
        "omit_empty": "true",
        "conversion_source_types": CONVERSION_SOURCE_TYPES,
        "swipe_up_attribution_window": ADS_MANAGER_SWIPE_ATTRIBUTION_WINDOW,
        "view_attribution_window": ADS_MANAGER_VIEW_ATTRIBUTION_WINDOW,
        "action_report_time": normalize_ads_manager_action_report_time(action_report_time),
    }
    rows: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    successful = 0
    for page_index in range(MAX_PAGES):
        payload = await context.get_json(
            client,
            url,
            headers=headers,
            params=params,
        )
        page_rows, page_errors, page_success = extract_ad_hour_rows(
            payload,
            campaign_id=campaign_id,
        )
        rows.extend(page_rows)
        errors.extend(page_errors)
        successful += page_success
        paging = payload.get("paging")
        if paging is not None and not isinstance(paging, dict):
            errors.append({
                "kind": "ad_stats",
                "campaign_id": campaign_id,
                "code": "snapchat_ad_paging_invalid",
                "retryable": True,
            })
            break
        raw_next_url = (paging or {}).get("next_link")
        if not raw_next_url:
            break
        next_url = _safe_next_url(raw_next_url)
        if not next_url:
            errors.append({
                "kind": "ad_stats",
                "campaign_id": campaign_id,
                "code": "snapchat_ad_next_link_invalid",
                "retryable": True,
            })
            break
        if page_index == MAX_PAGES - 1:
            errors.append({
                "kind": "ad_stats",
                "campaign_id": campaign_id,
                "code": "snapchat_ad_pagination_incomplete",
                "retryable": True,
            })
            break
        url, params = next_url, None
    if successful == 0 and errors:
        raise SnapchatNativeSyncError(
            "snapchat_ad_stats_failed",
            f"Snapchat Ad stats failed for campaign {campaign_id}.",
            status_code=502,
            retryable=True,
            result={"error": errors[0]},
        )
    return rows, errors


def extract_ad_total_rows(
    payload: dict[str, Any],
    *,
    campaign_id: str,
    request_start: datetime,
    request_end: datetime,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int, bool]:
    """Extract complete Ad rows from a Campaign TOTAL breakdown response."""
    if not isinstance(payload, dict) or str(
        payload.get("request_status") or ""
    ).upper() != "SUCCESS":
        raise SnapchatNativeSyncError(
            "snapchat_ad_request_incomplete",
            "Snapchat did not confirm a successful Ad TOTAL response.",
            status_code=502,
            retryable=True,
        )
    wrapped_stats = payload.get("total_stats")
    if not isinstance(wrapped_stats, list):
        raise SnapchatNativeSyncError(
            "snapchat_ad_total_payload_invalid",
            "Snapchat returned invalid Ad TOTAL performance data.",
            status_code=502,
            retryable=True,
        )
    rows: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    successful = 0
    breakdown_seen = False
    if not wrapped_stats:
        errors.append({
            "kind": "ad_total_stats",
            "campaign_id": campaign_id,
            "code": "snapchat_ad_total_stats_missing",
            "message": "Snapchat returned no Ad TOTAL result envelope.",
            "retryable": True,
        })
        return rows, errors, successful, breakdown_seen
    for wrapped in wrapped_stats:
        if not isinstance(wrapped, dict):
            errors.append({
                "kind": "ad_total_stats",
                "campaign_id": campaign_id,
                "code": "snapchat_ad_total_wrapper_invalid",
                "message": "Snapchat returned an invalid Ad result wrapper.",
                "retryable": True,
            })
            continue
        status = str(wrapped.get("sub_request_status") or "").upper()
        if status != "SUCCESS":
            errors.append({
                "kind": "ad_total_stats",
                "campaign_id": campaign_id,
                "code": "snapchat_ad_subrequest_incomplete",
                "message": "Snapchat did not confirm the Ad subrequest.",
                "error": status[:100],
                "retryable": True,
            })
            continue
        stat = wrapped.get("total_stat")
        if not isinstance(stat, dict):
            errors.append({
                "kind": "ad_total_stats",
                "campaign_id": campaign_id,
                "code": "snapchat_ad_total_stat_missing",
                "message": "Snapchat success response omitted total_stat.",
                "retryable": True,
            })
            continue
        provider_start = stat.get("start_time") or request_start.isoformat(
            timespec="seconds"
        )
        provider_end = stat.get("end_time") or request_end.isoformat(
            timespec="seconds"
        )
        if not _valid_provider_window(
            provider_start,
            provider_end,
            expected_start=request_start,
            expected_end=request_end,
        ):
            errors.append({
                "kind": "ad_total_stats",
                "campaign_id": campaign_id,
                "code": "snapchat_ad_provider_window_invalid",
                "message": "Snapchat returned an invalid Ad report window.",
                "retryable": True,
            })
            continue
        breakdown = stat.get("breakdown_stats")
        candidates: list[dict[str, Any]] = []
        if not isinstance(breakdown, dict):
            errors.append({
                "kind": "ad_total_stats",
                "campaign_id": campaign_id,
                "code": "snapchat_ad_breakdown_missing",
                "message": "Snapchat total_stat omitted Ad breakdown_stats.",
                "retryable": True,
            })
            continue
        matching_values = [
            breakdown[key]
            for key in ("ad", "ads")
            if key in breakdown
        ]
        if not matching_values or any(
            not isinstance(value, list) for value in matching_values
        ):
            errors.append({
                "kind": "ad_total_stats",
                "campaign_id": campaign_id,
                "code": "snapchat_ad_breakdown_invalid",
                "message": "Snapchat returned an invalid Ad breakdown.",
                "retryable": True,
            })
            continue
        breakdown_seen = True
        wrapper_valid = True
        for value in matching_values:
            for item in value:
                if not isinstance(item, dict):
                    wrapper_valid = False
                    continue
                candidates.append(item)
        for entity in candidates:
            ad_id = _text(entity.get("id"))
            metrics = entity.get("stats")
            if not ad_id or not _has_numeric_provider_metric(metrics):
                wrapper_valid = False
                continue
            rows.append({
                "campaign_id": campaign_id,
                "ad_id": ad_id,
                "start_time": provider_start,
                "end_time": provider_end,
                "metrics": metrics,
            })
        if not wrapper_valid:
            errors.append({
                "kind": "ad_total_stats",
                "campaign_id": campaign_id,
                "code": "snapchat_ad_breakdown_row_invalid",
                "message": "Snapchat returned a partial or invalid Ad row.",
                "retryable": True,
            })
            continue
        successful += 1
    return rows, errors, successful, breakdown_seen


async def _fetch_campaign_ad_totals(
    context: SnapchatSyncContext,
    client: httpx.AsyncClient,
    access_token: str,
    *,
    campaign_id: str,
    request_start: datetime,
    request_end: datetime,
    action_report_time: str = ADS_MANAGER_DEFAULT_ACTION_REPORT_TIME,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool]:
    url = f"{SNAPCHAT_API_BASE}/campaigns/{campaign_id}/stats"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
    }
    params: dict[str, Any] | None = {
        "start_time": request_start.isoformat(timespec="seconds"),
        "end_time": request_end.isoformat(timespec="seconds"),
        "granularity": AD_PROVIDER_GRANULARITY,
        "breakdown": AD_BREAKDOWN,
        "fields": ",".join(TOTAL_STAT_FIELDS),
        "limit": 200,
        # Conversion-time purchases can arrive in hours without delivery.
        # omit_empty=false is required so those attributed Ad rows are retained.
        "omit_empty": "false",
        "conversion_source_types": CONVERSION_SOURCE_TYPES,
        "swipe_up_attribution_window": ADS_MANAGER_SWIPE_ATTRIBUTION_WINDOW,
        "view_attribution_window": ADS_MANAGER_VIEW_ATTRIBUTION_WINDOW,
        "action_report_time": normalize_ads_manager_action_report_time(action_report_time),
    }
    rows: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    successful = 0
    breakdown_seen = False
    for page_index in range(MAX_PAGES):
        payload = await context.get_json(
            client,
            url,
            headers=headers,
            params=params,
        )
        page_rows, page_errors, page_success, page_breakdown = (
            extract_ad_total_rows(
                payload,
                campaign_id=campaign_id,
                request_start=request_start,
                request_end=request_end,
            )
        )
        rows.extend(page_rows)
        errors.extend(page_errors)
        successful += page_success
        breakdown_seen = breakdown_seen or page_breakdown
        paging = payload.get("paging")
        if paging is not None and not isinstance(paging, dict):
            errors.append({
                "kind": "ad_total_stats",
                "campaign_id": campaign_id,
                "code": "snapchat_ad_paging_invalid",
                "message": "Snapchat returned an invalid paging envelope.",
                "retryable": True,
            })
            break
        raw_next_url = (paging or {}).get("next_link")
        if not raw_next_url:
            break
        next_url = _safe_next_url(raw_next_url)
        if not next_url:
            errors.append({
                "kind": "ad_total_stats",
                "campaign_id": campaign_id,
                "code": "snapchat_ad_next_link_invalid",
                "message": "Snapchat pagination returned an untrusted next_link.",
                "retryable": True,
            })
            break
        if page_index == MAX_PAGES - 1:
            errors.append({
                "kind": "ad_total_stats",
                "campaign_id": campaign_id,
                "code": "snapchat_ad_pagination_incomplete",
                "message": "Snapchat Ad pagination exceeded the page limit.",
                "retryable": True,
            })
            break
        url, params = next_url, None
    if successful == 0 and errors:
        raise SnapchatNativeSyncError(
            "snapchat_ad_total_stats_failed",
            f"Snapchat Ad TOTAL stats failed for campaign {campaign_id}.",
            status_code=502,
            retryable=True,
            result={"error": errors[0]},
        )
    return rows, errors, breakdown_seen


async def _fetch_ad_window(
    context: SnapchatSyncContext,
    client: httpx.AsyncClient,
    access_token: str,
    *,
    campaigns: list[dict[str, Any]],
    request_start: datetime,
    request_end: datetime,
) -> list[dict[str, Any]]:
    """Fetch one account-local Ad day with bounded provider concurrency."""
    semaphore = asyncio.Semaphore(AD_FETCH_CONCURRENCY)

    async def fetch_one(
        campaign_id: str,
        action_report_time: str,
    ) -> dict[str, Any]:
        async with semaphore:
            try:
                rows, report_errors, breakdown_seen = (
                    await _fetch_campaign_ad_totals(
                        context,
                        client,
                        access_token,
                        campaign_id=campaign_id,
                        request_start=request_start,
                        request_end=request_end,
                        action_report_time=action_report_time,
                    )
                )
                return {
                    "campaign_id": campaign_id,
                    "action_report_time": action_report_time,
                    "rows": rows,
                    "errors": report_errors,
                    "breakdown_seen": breakdown_seen,
                    "data_state": _performance_data_state(
                        rows,
                        errors=report_errors,
                        structure_seen=breakdown_seen,
                    ),
                }
            except SnapchatNativeSyncError as exc:
                if exc.code == "snapchat_needs_reauth":
                    raise
                return {
                    "campaign_id": campaign_id,
                    "action_report_time": action_report_time,
                    "rows": [],
                    "errors": [{
                        "kind": "ad_total_stats",
                        "campaign_id": campaign_id,
                        "code": exc.code,
                        "message": exc.message[:300],
                        "retryable": bool(exc.retryable),
                    }],
                    "breakdown_seen": False,
                    "data_state": DATA_STATE_UNKNOWN_INCOMPLETE,
                }

    tasks = [
        fetch_one(campaign_id, action_report_time)
        for campaign in campaigns
        if (campaign_id := _text(campaign.get("external_id")))
        for action_report_time in ADS_MANAGER_SUPPORTED_ACTION_REPORT_TIMES
    ]
    if not tasks:
        return []
    return list(await asyncio.gather(*tasks))


def _day_buckets(
    rows: list[dict[str, Any]],
    *,
    timezone_name: str,
    start_date: date,
    end_date: date,
) -> dict[tuple[str, str, str], dict[str, Any]]:
    zone = _timezone(timezone_name)
    buckets: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in rows:
        point = _parse_datetime(row.get("start_time"))
        if point is None:
            continue
        if point.tzinfo is None:
            point = point.replace(tzinfo=timezone.utc)
        report_date = point.astimezone(zone).date()
        if report_date < start_date or report_date > end_date:
            continue
        campaign_id = _text(row.get("campaign_id"))
        ad_id = _text(row.get("ad_id"))
        if not campaign_id or not ad_id:
            continue
        metrics = {
            key: _as_number((row.get("metrics") or {}).get(key))
            for key in TOTAL_STAT_FIELDS
        }
        bucket = buckets.setdefault(
            (campaign_id, ad_id, report_date.isoformat()),
            _new_bucket(TOTAL_STAT_FIELDS),
        )
        _add_to_bucket(
            bucket,
            metrics,
            provider_start=row.get("start_time"),
            provider_end=row.get("end_time"),
        )
    return buckets


async def _upsert_projection(
    context: SnapchatSyncContext,
    *,
    collection_name: str,
    account: dict[str, Any],
    timezone_name: str,
    stored_granularity: str,
    campaign_id: str,
    ad_id: str,
    date_string: str,
    bucket: dict[str, Any],
    action_report_time: str,
) -> None:
    metrics = _finalize_bucket(bucket)
    currency = _text(account.get("currency")).upper()
    spend_micro = _as_number(metrics.get("spend"))
    value_micro = _as_number(metrics.get("conversion_purchases_value"))
    purchases = _as_number(metrics.get("conversion_purchases"))
    spend_native = (
        round(float(spend_micro) / 1_000_000, 6)
        if spend_micro is not None else None
    )
    value_native = (
        round(float(value_micro) / 1_000_000, 6)
        if value_micro is not None else None
    )
    now_iso = context.now_iso()
    document = {
        "user_id": context.user_id,
        "provider": SNAPCHAT_PROVIDER_ID,
        "ad_account_id": account["ad_account_id"],
        "mezan_integration_account_id": account.get(
            "mezan_integration_account_id"
        ),
        "entity_type": "ad",
        "external_id": ad_id,
        "ad_id": ad_id,
        "campaign_id": campaign_id,
        "date": date_string,
        "date_timezone": timezone_name,
        "business_timezone": BUSINESS_TIMEZONE,
        "currency": currency or None,
        "account_timezone": _text(account.get("timezone")) or timezone_name,
        "attribution_model": ATTRIBUTION_MODEL,
        "metrics": metrics,
        "funnel_metrics": _funnel_metrics(metrics),
        "metric_provenance": _metric_provenance(
            metrics,
            provider_granularity=AD_PROVIDER_GRANULARITY,
            provider_breakdown=AD_BREAKDOWN,
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
        "source_mode": ad_source_mode(action_report_time),
        "provider_breakdown": AD_BREAKDOWN,
        "provider_granularity": AD_PROVIDER_GRANULARITY,
        "stored_granularity": stored_granularity,
        "accounting_eligible": False,
        "report_scope": (
            "snapchat_ads_manager_account_timezone"
            if collection_name == SNAPCHAT_ACCOUNT_LOCAL_PERFORMANCE_COLLECTION
            else "riyadh_business_day_diagnostics"
        ),
        "provider_window_start": bucket.get("provider_start"),
        "provider_window_end": bucket.get("provider_end"),
        "updated_at": now_iso,
    }
    identity = {
        "user_id": context.user_id,
        "ad_account_id": account["ad_account_id"],
        "entity_type": "ad",
        "external_id": ad_id,
        "date": date_string,
        "attribution_model": ATTRIBUTION_MODEL,
    }
    if collection_name == SNAPCHAT_ACCOUNT_LOCAL_PERFORMANCE_COLLECTION:
        identity["action_report_time"] = action_report_time

    await _collection(context.db, collection_name).update_one(
        identity,
        {"$set": document, "$setOnInsert": {"created_at": now_iso}},
        upsert=True,
    )


async def _recent_refresh(
    db: Any,
    user_id: str,
    account_id: str,
    *,
    now: datetime,
    start_date: date,
    end_date: date,
    timezone_name: str,
) -> dict[str, Any] | None:
    row = await _collection(db, AD_REFRESH_STATE_COLLECTION).find_one(
        {"user_id": user_id, "ad_account_id": account_id},
    )
    observed = _parse_datetime((row or {}).get("last_success_at"))
    if observed is None:
        return None
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=timezone.utc)
    coverage = (row or {}).get("coverage")
    if not (
        isinstance(coverage, dict)
        and coverage.get("status") == "complete"
        and coverage.get("data_state") in CONFIRMED_DATA_STATES
    ):
        return None
    age_seconds = (now - observed.astimezone(timezone.utc)).total_seconds()
    return row if (
        _text((row or {}).get("source_mode")) == AD_REFRESH_SOURCE_MODE
        and _text((row or {}).get("date_from")) == start_date.isoformat()
        and _text((row or {}).get("date_to")) == end_date.isoformat()
        and _text((row or {}).get("account_timezone")) == timezone_name
        and 0 <= age_seconds < AD_REFRESH_INTERVAL_SECONDS
    ) else None


async def refresh_snapchat_ad_performance(
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
            retryable=False,
        )
    timezone_name = _valid_timezone_name(account.get("timezone"))
    current = _aware_now(now)
    calls_before = context.provider_calls
    (
        identity_rows_saved,
        identity_counts,
        identity_errors,
    ) = await sync_snapchat_ad_entities(
        context, client, access_token, account
    )
    recent_refresh = await _recent_refresh(
        context.db,
        context.user_id,
        account_id,
        now=current,
        start_date=start_date,
        end_date=end_date,
        timezone_name=timezone_name,
    )
    if recent_refresh:
        cached_coverage = dict(recent_refresh.get("coverage") or {})
        if identity_errors:
            cached_coverage = {
                **cached_coverage,
                "status": "incomplete",
                "data_state": DATA_STATE_UNKNOWN_INCOMPLETE,
            }
        return {
            "source_mode": AD_SOURCE_MODE,
            "skipped": True,
            "skip_reason": "fresh_within_15_minutes",
            "rows_saved": 0,
            "provider_calls": context.provider_calls - calls_before,
            "identity_rows_saved": identity_rows_saved,
            "identity_counts": identity_counts,
            "identity_errors": identity_errors[:20],
            "coverage": cached_coverage,
            "source_only": True,
        }

    # Match the proven Ad Squad path: one authoritative TOTAL snapshot for
    # each account-local day. HOUR rows undercount late conversion-time
    # purchases at Ad breakdown level on the open day.
    from .snapchat_platform_source_integrity import (
        account_local_dates_for_refresh,
        account_local_total_window,
    )

    report_dates = account_local_dates_for_refresh(
        start_date,
        end_date,
        timezone_name=timezone_name,
        now=current,
    )
    if not report_dates:
        return {
            "source_mode": AD_SOURCE_MODE,
            "skipped": True,
            "skip_reason": "empty_request_window",
            "rows_saved": 0,
            "provider_calls": context.provider_calls - calls_before,
            "identity_rows_saved": identity_rows_saved,
            "identity_counts": identity_counts,
            "identity_errors": identity_errors[:20],
            "coverage": {
                "status": "incomplete",
                "data_state": DATA_STATE_UNKNOWN_INCOMPLETE,
                "expected_requests": 0,
                "completed_requests": 0,
            },
            "source_only": True,
        }

    campaigns, campaign_limit_reached = await _campaign_entities(
        context.db,
        context.user_id,
        account_id,
    )
    errors: list[dict[str, Any]] = [
        {**error, "kind": _text(error.get("kind")) or "ad_identity"}
        for error in identity_errors
    ]
    account_local_saved = 0
    request_windows: list[dict[str, str]] = []
    breakdown_days = 0
    expected_requests = 0
    completed_requests = 0
    confirmed_states: list[str] = []
    pending_rows: list[tuple[str, date, list[dict[str, Any]]]] = []

    for report_date in report_dates:
        window = account_local_total_window(
            report_date,
            timezone_name=timezone_name,
            now=current,
        )
        if window is None:
            errors.append({
                "kind": "ad_total_stats",
                "date": report_date.isoformat(),
                "code": "snapchat_ad_window_unavailable",
                "message": "The requested Ad report window was unavailable.",
                "retryable": True,
            })
            continue
        request_start, request_end = window
        request_windows.append({
            "date": report_date.isoformat(),
            "start_time": request_start.isoformat(timespec="seconds"),
            "end_time": request_end.isoformat(timespec="seconds"),
            "granularity": AD_PROVIDER_GRANULARITY,
        })
        rows_by_mode: dict[str, list[dict[str, Any]]] = {
            mode: [] for mode in ADS_MANAGER_SUPPORTED_ACTION_REPORT_TIMES
        }
        complete_by_mode = {
            mode: False for mode in ADS_MANAGER_SUPPORTED_ACTION_REPORT_TIMES
        }
        expected_identities = {
            (_text(campaign.get("external_id")), mode)
            for campaign in campaigns
            if _text(campaign.get("external_id"))
            for mode in ADS_MANAGER_SUPPORTED_ACTION_REPORT_TIMES
        }
        expected_requests += len(expected_identities)
        completed_identities: set[tuple[str, str]] = set()
        if not expected_identities:
            errors.append({
                "kind": "ad_total_stats",
                "date": report_date.isoformat(),
                "code": "snapchat_ad_campaign_coverage_unproven",
                "message": "No active campaign performance request could be proven.",
                "retryable": True,
            })

        window_results = await _fetch_ad_window(
            context,
            client,
            access_token,
            campaigns=campaigns,
            request_start=request_start,
            request_end=request_end,
        )
        for result in window_results:
            campaign_id = _text(result.get("campaign_id"))
            action_report_time = _text(result.get("action_report_time"))
            identity = (campaign_id, action_report_time)
            if identity not in expected_identities:
                errors.append({
                    "kind": "ad_total_stats",
                    "date": report_date.isoformat(),
                    "code": "snapchat_ad_result_identity_unexpected",
                    "campaign_id": campaign_id,
                    "action_report_time": action_report_time,
                    "retryable": True,
                })
                continue
            campaign_rows = result.get("rows") or []
            campaign_errors = result.get("errors") or []
            data_state = _text(result.get("data_state"))
            request_complete = (
                data_state in CONFIRMED_DATA_STATES
                and bool(result.get("breakdown_seen"))
                and not campaign_errors
            )
            if request_complete and identity not in completed_identities:
                completed_identities.add(identity)
                confirmed_states.append(data_state)
                rows_by_mode[action_report_time].extend(campaign_rows)
            errors.extend({
                **error,
                "date": report_date.isoformat(),
                "action_report_time": action_report_time,
            } for error in campaign_errors)

        completed_requests += len(completed_identities)
        for action_report_time in ADS_MANAGER_SUPPORTED_ACTION_REPORT_TIMES:
            expected_for_mode = {
                identity for identity in expected_identities
                if identity[1] == action_report_time
            }
            complete_by_mode[action_report_time] = bool(expected_for_mode) and (
                expected_for_mode <= completed_identities
            )

        for action_report_time, mode_rows in rows_by_mode.items():
            if not complete_by_mode[action_report_time]:
                continue
            pending_rows.append((action_report_time, report_date, mode_rows))

    now_iso = context.now_iso()
    coverage_complete = (
        bool(request_windows)
        and len(request_windows) == len(report_dates)
        and expected_requests > 0
        and completed_requests == expected_requests
        and not errors
        and not campaign_limit_reached
    )
    if coverage_complete:
        for action_report_time, report_date, mode_rows in pending_rows:
            local = _day_buckets(
                mode_rows,
                timezone_name=timezone_name,
                start_date=report_date,
                end_date=report_date,
            )
            for (campaign_id, ad_id, date_string), bucket in sorted(
                local.items()
            ):
                await _upsert_projection(
                    context,
                    collection_name=SNAPCHAT_ACCOUNT_LOCAL_PERFORMANCE_COLLECTION,
                    account=account,
                    timezone_name=timezone_name,
                    stored_granularity="ACCOUNT_LOCAL_TOTAL_DAY",
                    campaign_id=campaign_id,
                    ad_id=ad_id,
                    date_string=date_string,
                    bucket=bucket,
                    action_report_time=action_report_time,
                )
                account_local_saved += 1
            breakdown_days += 1

    if not coverage_complete:
        aggregate_data_state = DATA_STATE_UNKNOWN_INCOMPLETE
    elif DATA_STATE_CONFIRMED_DATA in confirmed_states:
        aggregate_data_state = DATA_STATE_CONFIRMED_DATA
    elif DATA_STATE_CONFIRMED_ZERO in confirmed_states:
        aggregate_data_state = DATA_STATE_CONFIRMED_ZERO
    else:
        aggregate_data_state = DATA_STATE_CONFIRMED_NO_DATA
    coverage = {
        "status": "complete" if coverage_complete else "incomplete",
        "data_state": aggregate_data_state,
        "expected_requests": expected_requests,
        "completed_requests": completed_requests,
    }
    state_set: dict[str, Any] = {
        "user_id": context.user_id,
        "ad_account_id": account_id,
        "rows_saved": account_local_saved,
        "campaigns_requested": len(campaigns),
        "campaign_limit_reached": campaign_limit_reached,
        "errors_count": len(errors),
        "source_mode": AD_REFRESH_SOURCE_MODE,
        "provider_granularity": AD_PROVIDER_GRANULARITY,
        "date_from": start_date.isoformat(),
        "date_to": end_date.isoformat(),
        "account_timezone": timezone_name,
        "coverage": coverage,
        "updated_at": now_iso,
        "last_attempt_at": now_iso,
    }
    if coverage_complete:
        state_set["last_success_at"] = now_iso
    await _collection(
        context.db,
        AD_REFRESH_STATE_COLLECTION,
    ).update_one(
        {"user_id": context.user_id, "ad_account_id": account_id},
        {
            "$set": state_set,
            "$setOnInsert": {"created_at": now_iso},
        },
        upsert=True,
    )
    return {
        "source_mode": AD_REFRESH_SOURCE_MODE,
        "supported_action_report_times": list(
            ADS_MANAGER_SUPPORTED_ACTION_REPORT_TIMES
        ),
        "skipped": False,
        "rows_saved": account_local_saved,
        "riyadh_rows_saved": 0,
        "account_local_rows_saved": account_local_saved,
        "campaigns_requested": len(campaigns),
        "campaign_limit_reached": campaign_limit_reached,
        "errors_count": len(errors),
        "errors": errors[:50],
        "coverage": coverage,
        "identity_rows_saved": identity_rows_saved,
        "identity_counts": identity_counts,
        "identity_errors": identity_errors[:20],
        "provider_calls": context.provider_calls - calls_before,
        "provider_granularity": AD_PROVIDER_GRANULARITY,
        "provider_breakdown": AD_BREAKDOWN,
        "request_windows": request_windows,
        "authoritative_breakdown_days": breakdown_days,
        "source_only": True,
        "provider_write_reached": False,
        "ad_write_reached": False,
        "accounting_write_reached": False,
        "qoyod_write_reached": False,
    }

def install_snapchat_ad_performance_refresh() -> None:
    current = hourly.refresh_snapchat_account_hours
    if getattr(current, "_mezan_ad_performance_refresh", False):
        return

    async def wrapped(
        context: SnapchatSyncContext,
        client: httpx.AsyncClient,
        access_token: str,
        account: dict[str, Any],
        *args: Any,
        **kwargs: Any,
    ) -> dict[str, Any]:
        output = dict(
            await current(
                context,
                client,
                access_token,
                account,
                *args,
                **kwargs,
            ) or {}
        )
        start_date = kwargs.get("start_date")
        end_date = kwargs.get("end_date")
        if not isinstance(start_date, date) and len(args) >= 1:
            start_date = args[0]
        if not isinstance(end_date, date) and len(args) >= 2:
            end_date = args[1]
        if not isinstance(start_date, date) or not isinstance(end_date, date):
            return output
        try:
            output["ad_performance"] = await refresh_snapchat_ad_performance(
                context,
                client,
                access_token,
                account,
                start_date=start_date,
                end_date=end_date,
                now=kwargs.get("now"),
            )
        except SnapchatNativeSyncError as exc:
            if exc.code == "snapchat_needs_reauth":
                raise
            output["ad_performance"] = {
                "source_mode": AD_SOURCE_MODE,
                "skipped": False,
                "rows_saved": 0,
                "errors_count": 1,
                "errors": [{
                    "code": exc.code,
                    "message": exc.message[:300],
                    "retryable": bool(exc.retryable),
                }],
                "coverage": {
                    "status": "incomplete",
                    "data_state": DATA_STATE_UNKNOWN_INCOMPLETE,
                    "expected_requests": 0,
                    "completed_requests": 0,
                },
                "source_only": True,
            }
        return output

    wrapped._mezan_ad_performance_refresh = True  # type: ignore[attr-defined]
    wrapped._mezan_ad_performance_base = current  # type: ignore[attr-defined]
    hourly.refresh_snapchat_account_hours = wrapped


def _entity_maps(entity_rows: list[dict[str, Any]]) -> tuple[dict, dict, dict, dict]:
    campaigns: dict[str, dict[str, Any]] = {}
    squads: dict[str, dict[str, Any]] = {}
    ads: dict[str, dict[str, Any]] = {}
    creatives: dict[str, dict[str, Any]] = {}
    for row in entity_rows:
        external_id = _text(row.get("external_id"))
        if not external_id:
            continue
        entity_type = _text(row.get("entity_type"))
        if entity_type == "campaign":
            campaigns[external_id] = row
        elif entity_type == "ad_squad":
            squads[external_id] = row
        elif entity_type == "ad":
            ads[external_id] = row
        elif entity_type == "creative":
            creatives[external_id] = row
    return campaigns, squads, ads, creatives


def _creative_summary(entity: dict[str, Any] | None) -> dict[str, Any]:
    row = entity or {}
    snapshot = row.get("provider_snapshot")
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    creative_type = (
        _text(snapshot.get("type"))
        or _text(snapshot.get("creative_type"))
        or None
    )
    media_id = (
        _text(snapshot.get("top_snap_media_id"))
        or _text(snapshot.get("media_id"))
        or None
    )
    destination = None
    web_view = snapshot.get("web_view_properties")
    if isinstance(web_view, dict):
        destination = _text(web_view.get("url")) or None
    return {
        "creative_id": _text(row.get("external_id")) or None,
        "creative_name": _text(row.get("display_name")) or None,
        "creative_type": creative_type,
        "media_id": media_id,
        "destination_url": destination,
    }


def _delivery_codes(value: Any) -> set[str]:
    if isinstance(value, (list, tuple, set)):
        return {
            _text(item).upper()
            for item in value
            if _text(item)
        }
    raw = _text(value).upper()
    if not raw:
        return set()
    for char in "[]'\"":
        raw = raw.replace(char, " ")
    return {
        item.strip()
        for item in raw.replace(",", " ").split()
        if item.strip()
    }


def _delivery_for_ad(
    ad: dict[str, Any],
    parent: dict[str, Any] | None,
) -> dict[str, Any]:
    configured = _text(ad.get("status") or "UNKNOWN").upper()
    review = _text(ad.get("review_status") or "").upper()
    if ad.get("deleted") is True:
        return {
            "configured_status": configured,
            "delivery_state": "NOT_DELIVERING",
            "delivery_status": "غير نشط — الإعلان محذوف",
            "delivery_reason_code": "AD_DELETED",
            "deliverable": False,
        }
    if configured in {"UNKNOWN", ""}:
        return {
            "configured_status": "UNKNOWN",
            "delivery_state": "UNKNOWN",
            "delivery_status": "حالة الإعلان غير متوفرة من Snapchat",
            "delivery_reason_code": "AD_IDENTITY_NOT_SYNCED",
            "deliverable": False,
        }
    if configured not in {"ACTIVE", "ENABLED"}:
        return {
            "configured_status": configured,
            "delivery_state": "NOT_DELIVERING",
            "delivery_status": "غير نشط — الإعلان متوقف",
            "delivery_reason_code": "AD_CONFIGURED_PAUSED",
            "deliverable": False,
        }
    if review in {"REJECTED", "DISAPPROVED"}:
        return {
            "configured_status": configured,
            "delivery_state": "NOT_DELIVERING",
            "delivery_status": "لا تسليم — الإعلان مرفوض",
            "delivery_reason_code": "AD_REVIEW_REJECTED",
            "deliverable": False,
        }
    if review in {"PENDING", "IN_REVIEW", "PENDING_REVIEW"}:
        return {
            "configured_status": configured,
            "delivery_state": "PENDING",
            "delivery_status": "قيد المراجعة",
            "delivery_reason_code": "AD_REVIEW_PENDING",
            "deliverable": False,
        }

    provider_state = _text(ad.get("delivery_state")).upper()
    provider_codes = _delivery_codes(ad.get("delivery_status"))
    blocked_codes = provider_codes & {
        "NOT_DELIVERING",
        "INVALID",
        "PAUSED",
        "INACTIVE",
        "REJECTED",
        "DISAPPROVED",
        "BUDGET_EXHAUSTED",
        "OUT_OF_BUDGET",
    }
    if provider_state and provider_state not in {"DELIVERING", "ACTIVE"}:
        return {
            "configured_status": configured,
            "delivery_state": provider_state,
            "delivery_status": "لا تسليم — أبلغ Snapchat بمانع على الإعلان",
            "delivery_reason_code": next(iter(blocked_codes), provider_state),
            "deliverable": False,
        }
    if blocked_codes:
        reason = sorted(blocked_codes)[0]
        return {
            "configured_status": configured,
            "delivery_state": "NOT_DELIVERING",
            "delivery_status": "لا تسليم — أبلغ Snapchat بمانع على الإعلان",
            "delivery_reason_code": reason,
            "deliverable": False,
        }

    parent_row = parent or {}
    parent_configured = _text(parent_row.get("status")).upper()
    if parent_configured and parent_configured not in {"ACTIVE", "ENABLED"}:
        return {
            "configured_status": configured,
            "delivery_state": "NOT_DELIVERING",
            "delivery_status": "غير نشط — المجموعة الإعلانية متوقفة",
            "delivery_reason_code": "PARENT_AD_SQUAD_CONFIGURED_PAUSED",
            "deliverable": False,
            "delivery_inherited_from_ad_squad": True,
        }
    parent_state = _text(parent_row.get("delivery_state")).upper()
    parent_label = _text(
        parent_row.get("delivery_status")
        or parent_row.get("delivery_label")
    )
    if parent_state and parent_state != "DELIVERING":
        return {
            "configured_status": configured,
            "delivery_state": parent_state,
            "delivery_status": parent_label or "لا تسليم — المجموعة الإعلانية لا تسلّم",
            "delivery_reason_code": _text(
                parent_row.get("delivery_reason_code")
            ) or "PARENT_AD_SQUAD_NOT_DELIVERING",
            "deliverable": False,
            "delivery_inherited_from_ad_squad": True,
        }
    learning = "LEARNING_PHASE" in provider_codes
    return {
        "configured_status": configured,
        "delivery_state": "DELIVERING",
        "delivery_status": (
            "يتم التسليم — مرحلة التعلم"
            if learning
            else "يتم التسليم"
        ),
        "delivery_reason_code": (
            "LEARNING_PHASE"
            if learning
            else "DELIVERING"
        ),
        "deliverable": True,
    }

async def build_account_timezone_ad_report(
    db: Any,
    user_id: str,
    *,
    account_id: str | None,
    from_date: str | None,
    to_date: str | None,
    query: str | None,
    page: int,
    limit: int,
    campaign_id: str | None = None,
    ad_squad_id: str | None = None,
    active_campaigns_only: bool = False,
    sort_by: str = "orders",
    action_report_time: str = ADS_MANAGER_DEFAULT_ACTION_REPORT_TIME,
    now: Callable[[], datetime] = _utcnow,
) -> dict[str, Any]:
    current = _aware_now(now())
    action_report_time = normalize_ads_manager_action_report_time(action_report_time)
    accounts = await _load_selected_accounts(db, user_id)
    if not accounts:
        raise SnapchatNativeSyncError(
            "snapchat_accounts_not_selected",
            "لا توجد حسابات Snapchat محددة داخل ميزان.",
            status_code=409,
        )
    requested_id = _text(account_id)
    selected = next(
        (
            row for row in accounts
            if _text(row.get("ad_account_id")) == requested_id
        ),
        None,
    ) if requested_id else accounts[0]
    if selected is None:
        raise SnapchatNativeSyncError(
            "snapchat_account_not_selected",
            "الحساب الإعلاني المطلوب غير محدد داخل ميزان.",
            status_code=404,
        )
    account = _account_public_row(selected, now=current)
    timezone_name = account["timezone"]
    dates = resolve_account_report_dates(
        from_date,
        to_date,
        timezone_name=timezone_name,
        now=current,
    )
    date_query = {
        "$gte": dates[0].isoformat(),
        "$lte": dates[-1].isoformat(),
    }
    performance_cursor = _collection(
        db,
        SNAPCHAT_ACCOUNT_LOCAL_PERFORMANCE_COLLECTION,
    ).find(
        {
            "user_id": user_id,
            "provider": SNAPCHAT_PROVIDER_ID,
            "ad_account_id": account["account_id"],
            "entity_type": "ad",
            "date": date_query,
            "date_timezone": timezone_name,
            "source_mode": ad_source_mode(action_report_time),
            "action_report_time": action_report_time,
        },
        {"_id": 0},
    )
    performance_rows = await _to_list(performance_cursor, MAX_REPORT_ROWS)
    row_limit_reached = len(performance_rows) >= MAX_REPORT_ROWS
    entity_cursor = _collection(db, SNAPCHAT_ENTITY_COLLECTION).find(
        {
            "user_id": user_id,
            "provider": SNAPCHAT_PROVIDER_ID,
            "ad_account_id": account["account_id"],
            "entity_type": {"$in": ["campaign", "ad_squad", "ad", "creative"]},
        },
        AD_REPORT_ENTITY_PROJECTION,
    )
    entity_rows = await _to_list(entity_cursor, MAX_ENTITY_ROWS)
    entity_limit_reached = len(entity_rows) >= MAX_ENTITY_ROWS
    campaigns, squads, ads, creatives = _entity_maps(entity_rows)

    # The Ad report already loaded the Ad Squad entities. Reusing them avoids a
    # second full performance/catalog scan and also covers parents beyond the
    # first 100 rows of the paginated Ad Squad report.
    parent_rows = squads

    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in performance_rows:
        ad_id = _text(row.get("ad_id") or row.get("external_id"))
        if ad_id:
            groups[ad_id].append(row)
    requested_days = len(dates)
    from ads_manager.account_cost_settings import list_account_cost_settings

    settings = await list_account_cost_settings(db, user_id)
    setting = next(
        (
            item for item in settings.get("items") or []
            if _text(item.get("external_account_id")) == account["account_id"]
        ),
        None,
    )
    currency, rate = _effective_currency(selected, setting)
    rows: list[dict[str, Any]] = []
    identity_matches = 0
    requested_campaign_id = _text(campaign_id)
    requested_ad_squad_id = _text(ad_squad_id)
    all_ad_ids = sorted(set(groups) | set(ads))
    for ad_id in all_ad_ids:
        facts = groups.get(ad_id, [])
        entity = ads.get(ad_id, {})
        if entity:
            identity_matches += 1
        snapshot = entity.get("provider_snapshot")
        snapshot = snapshot if isinstance(snapshot, dict) else {}
        ad_squad_id = (
            _text(entity.get("ad_squad_id"))
            or _text(snapshot.get("ad_squad_id"))
            or _text(snapshot.get("adsquad_id"))
        )
        if not ad_squad_id and facts:
            ad_squad_id = _text(
                facts[0].get("ad_squad_id")
                or facts[0].get("adsquad_id")
            )
        campaign_id = (
            _text(entity.get("campaign_id"))
            or _text(snapshot.get("campaign_id"))
        )
        if not campaign_id and facts:
            campaign_id = _text(facts[0].get("campaign_id"))
        squad = squads.get(ad_squad_id, {})
        if not campaign_id:
            campaign_id = _text(squad.get("campaign_id"))
        campaign = campaigns.get(campaign_id, {})
        if requested_campaign_id and campaign_id != requested_campaign_id:
            continue
        if requested_ad_squad_id and ad_squad_id != requested_ad_squad_id:
            continue
        campaign_active = is_active_provider_status(campaign.get("status"))
        if active_campaigns_only and not campaign_active:
            continue
        parent = parent_rows.get(ad_squad_id)
        creative_id = _text(entity.get("creative_id"))
        metrics = _aggregate_rows(facts, requested_days=requested_days)
        delivery = _delivery_for_ad(entity, parent)
        rows.append({
            "account_id": account["account_id"],
            "account_name": account["account_name"],
            "ad_id": ad_id,
            "ad_name": _text(entity.get("display_name")) or ad_id,
            "ad_squad_id": ad_squad_id or None,
            "ad_squad_name": _text(squad.get("display_name")) or ad_squad_id or "مجموعة غير معروفة",
            "campaign_id": campaign_id or None,
            "campaign_name": _text(campaign.get("display_name")) or campaign_id or "حملة غير معروفة",
            "campaign_status": campaign.get("status") or "unknown",
            "campaign_active": campaign_active,
            "ad_squad_status": squad.get("status") or "unknown",
            "status": delivery["configured_status"],
            "review_status": entity.get("review_status"),
            "created_at_provider": entity.get("created_at_provider"),
            "updated_at_provider": entity.get("updated_at_provider"),
            "display_currency": currency,
            "exchange_rate_to_sar": round(rate, 6),
            "result_source": "platform",
            "commercial_results_scope": (
                f"snapchat_ad_{action_report_time}_reporting"
            ),
            **_creative_summary(creatives.get(creative_id)),
            **delivery,
            **metrics,
        })
    search = _text(query).casefold()[:120]
    if search:
        rows = [
            item for item in rows
            if search in " ".join([
                _text(item.get("ad_name")),
                _text(item.get("ad_id")),
                _text(item.get("ad_squad_name")),
                _text(item.get("ad_squad_id")),
                _text(item.get("campaign_name")),
                _text(item.get("campaign_id")),
                _text(item.get("creative_name")),
                _text(item.get("creative_id")),
            ]).casefold()
        ]
    if active_campaigns_only:
        rows = [item for item in rows if item.get("campaign_active") is True]
    sort_mode = normalize_entity_sort(sort_by)
    rows = sort_entity_rows(
        rows,
        sort_mode,
        name_field="ad_name",
        status_field="status",
    )
    filtered_totals = aggregate_entity_rows(rows)
    total = len(rows)
    pages = (total + limit - 1) // limit if total else 0
    offset = (page - 1) * limit
    page_rows = rows[offset:offset + limit]
    totals = filtered_totals
    return {
        "provider": SNAPCHAT_PROVIDER_ID,
        "entity_level": "ad",
        "date_from": dates[0].isoformat(),
        "date_to": dates[-1].isoformat(),
        "account_timezone": timezone_name,
        "selected_account_id": account["account_id"],
        "selected_account": account,
        "result_source": "platform",
        "action_report_time": action_report_time,
        "supported_action_report_times": list(ADS_MANAGER_SUPPORTED_ACTION_REPORT_TIMES),
        "supported_result_sources": ["platform"],
        "active_campaigns_only": bool(active_campaigns_only),
        "campaign_id": requested_campaign_id or None,
        "ad_squad_id": requested_ad_squad_id or None,
        "sort_by": sort_mode,
        "totals": totals,
        "ads": page_rows,
        "pagination": {
            "page": page,
            "limit": limit,
            "total": total,
            "pages": pages,
        },
        "source": {
            "performance_collection": SNAPCHAT_ACCOUNT_LOCAL_PERFORMANCE_COLLECTION,
            "entity_collection": SNAPCHAT_ENTITY_COLLECTION,
            "source_mode": ad_source_mode(action_report_time),
            "performance_rows": len(performance_rows),
            "entity_rows": len(entity_rows),
            "ad_entities": len(ads),
            "creative_entities": len(creatives),
            "ads_returned_before_pagination": total,
            "identity_matches": identity_matches,
            "identity_coverage_pct": (
                round(identity_matches / len(all_ad_ids) * 100, 2)
                if all_ad_ids else None
            ),
            "row_limit_reached": row_limit_reached,
            "entity_limit_reached": entity_limit_reached,
            "entity_projection_bounded": True,
            "parent_catalog_reused": True,
            "commercial_results_source": f"snapchat_ads_manager_{action_report_time}_reporting",
            "action_report_time": action_report_time,
            "salla_results_supported": False,
        },
        "policy": {"mode": "observe_only", "mutations_allowed": False},
        "source_only": True,
        "provider_read_reached": False,
        "provider_write_reached": False,
        "ad_write_reached": False,
        "accounting_write_reached": False,
        "qoyod_write_reached": False,
    }


def attach_snapchat_ad_routes(
    router: APIRouter,
    db: Any,
    current_user: Callable,
    require_owner: Callable[[Any], dict],
) -> None:
    @router.get(
        f"/{SNAPCHAT_PROVIDER_ID}/ad-report",
        name="get_snapchat_ad_report",
    )
    async def ad_report(
        account_id: str | None = Query(default=None, max_length=120),
        from_date: str | None = Query(default=None),
        to_date: str | None = Query(default=None),
        query: str | None = Query(default=None, max_length=120),
        campaign_id: str | None = Query(default=None, max_length=120),
        ad_squad_id: str | None = Query(default=None, max_length=120),
        page: int = Query(default=1, ge=1),
        limit: int = Query(default=9, ge=1, le=100),
        active_campaigns_only: bool = Query(default=True),
        sort_by: str = Query(default="orders", pattern="^(orders|spend|newest|active)$"),
        action_report_time: str = Query(default=ADS_MANAGER_DEFAULT_ACTION_REPORT_TIME, pattern="^(conversion|impression)$"),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        owner = require_owner(user)
        try:
            return await build_account_timezone_ad_report(
                db,
                str(owner["id"]),
                account_id=account_id,
                from_date=from_date,
                to_date=to_date,
                query=query,
                page=page,
                limit=limit,
                campaign_id=campaign_id,
                ad_squad_id=ad_squad_id,
                active_campaigns_only=active_campaigns_only,
                sort_by=sort_by,
                action_report_time=action_report_time,
            )
        except SnapchatNativeSyncError as exc:
            raise HTTPException(
                status_code=exc.status_code,
                detail={
                    "code": exc.code,
                    "message": exc.message,
                    "retryable": exc.retryable,
                    "source_only": True,
                    "provider_write_reached": False,
                    "ad_write_reached": False,
                    "accounting_write_reached": False,
                    "qoyod_write_reached": False,
                },
            ) from exc


__all__ = [
    "AD_BREAKDOWN",
    "AD_REFRESH_INTERVAL_SECONDS",
    "AD_SOURCE_MODE",
    "attach_snapchat_ad_routes",
    "build_account_timezone_ad_report",
    "extract_ad_hour_rows",
    "extract_ad_total_rows",
    "install_snapchat_ad_performance_refresh",
    "refresh_snapchat_ad_performance",
]
