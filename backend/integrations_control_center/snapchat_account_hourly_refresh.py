"""Lightweight Snapchat account totals for the five-minute scheduler.

Snapchat exposes full ad-account metrics through a TOTAL report broken down by
campaign. Direct ad-account stats only guarantee spend, so this module requests
one campaign-breakdown TOTAL per Riyadh business day and folds the campaigns
into one authoritative ad-account row. The server still polls every five
minutes; provider facts may advance on Snapchat's own reporting cadence.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

import httpx

from .snapchat_native_data_common import (
    BUSINESS_TIMEZONE,
    MAX_PAGES,
    SNAPCHAT_API_BASE,
    SNAPCHAT_PROVIDER_ID,
    SnapchatNativeSyncError,
    SnapchatSyncContext,
    _as_number,
    _parse_datetime,
    _safe_next_url,
    _timezone,
)
from .snapchat_native_performance_sync import (
    STAT_FIELDS,
    _add_to_bucket,
    _finalize_bucket,
    _new_bucket,
    _upsert_performance,
    riyadh_business_window,
)

ACCOUNT_REFRESH_SOURCE_MODE = (
    "snapchat_account_total_campaign_breakdown_riyadh_refresh_v2"
)
PROVIDER_GRANULARITY = "TOTAL"
PROVIDER_BREAKDOWN = "campaign"
CONVERSION_SOURCE_TYPES = "total"
ACTION_REPORT_TIME = "conversion"
SWIPE_ATTRIBUTION_WINDOW = "28_DAY"
VIEW_ATTRIBUTION_WINDOW = "1_DAY"


def _aware_now(now: datetime | None = None) -> datetime:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current


def snapchat_hourly_request_window(
    start_date: date,
    end_date: date,
    *,
    now: datetime | None = None,
) -> tuple[datetime, datetime]:
    """Backward-compatible completed-hour helper used by older contracts."""
    start, nominal_end = riyadh_business_window(start_date, end_date)
    current_business = _aware_now(now).astimezone(_timezone(BUSINESS_TIMEZONE))
    if end_date < current_business.date():
        return start, nominal_end
    completed_hour_end = current_business.replace(
        minute=0,
        second=0,
        microsecond=0,
    )
    return start, min(nominal_end, completed_hour_end)


def snapchat_total_request_window(
    report_date: date,
    *,
    now: datetime | None = None,
) -> tuple[datetime, datetime] | None:
    """Return one provider-safe TOTAL window for a Riyadh business day.

    Historical days use the complete midnight-to-midnight window. The current
    day ends at the current completed second, which TOTAL reports accept without
    HOUR alignment. Future days and the exact midnight instant are skipped.
    """
    start, nominal_end = riyadh_business_window(report_date, report_date)
    current_business = _aware_now(now).astimezone(_timezone(BUSINESS_TIMEZONE))
    if report_date < current_business.date():
        return start, nominal_end
    if report_date > current_business.date():
        return None
    current_end = current_business.replace(microsecond=0)
    end = min(nominal_end, current_end)
    return (start, end) if end > start else None


def _subrequest_error(
    wrapped: dict[str, Any],
    status: str,
) -> dict[str, Any]:
    code = str(
        wrapped.get("error_code")
        or wrapped.get("code")
        or "snapchat_account_total_subrequest_failed"
    )[:120]
    message = str(
        wrapped.get("error_message")
        or wrapped.get("debug_message")
        or wrapped.get("message")
        or status
        or "Snapchat rejected an account TOTAL sub-request."
    )[:300]
    return {
        "kind": "account_total_campaign_breakdown",
        "code": code,
        "message": message,
        "error": status[:80],
        "retryable": True,
    }


def extract_account_total_rows(
    payload: dict[str, Any],
    *,
    request_start: datetime,
    request_end: datetime,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    """Extract campaign TOTAL rows and safe sub-request errors."""
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
    for wrapped in wrapped_stats:
        if not isinstance(wrapped, dict):
            continue
        status = str(
            wrapped.get("sub_request_status") or "SUCCESS"
        ).upper()
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
            breakdown.get(PROVIDER_BREAKDOWN)
            if isinstance(breakdown, dict)
            else None
        )
        if isinstance(campaigns, list):
            for campaign in campaigns:
                if not isinstance(campaign, dict):
                    continue
                stats = campaign.get("stats")
                if isinstance(stats, dict):
                    rows.append({
                        "campaign_id": str(campaign.get("id") or "").strip(),
                        "metrics": stats,
                        "start_time": provider_start,
                        "end_time": provider_end,
                    })
            continue

        # Defensive fallback. A direct ad-account TOTAL may only contain spend;
        # retaining it is safer than inventing the other metrics.
        stats = stat.get("stats")
        if isinstance(stats, dict):
            rows.append({
                "campaign_id": "",
                "metrics": stats,
                "start_time": provider_start,
                "end_time": provider_end,
            })

    return rows, errors, successful_subrequests


def aggregate_account_total_rows(
    rows: list[dict[str, Any]],
) -> dict[str, int | float | None]:
    if not rows:
        return {key: 0 for key in STAT_FIELDS}
    bucket = _new_bucket()
    for row in rows:
        metrics = {
            key: _as_number((row.get("metrics") or {}).get(key))
            for key in STAT_FIELDS
        }
        _add_to_bucket(
            bucket,
            metrics,
            provider_start=row.get("start_time"),
            provider_end=row.get("end_time"),
        )
    return _finalize_bucket(bucket)


async def _fetch_account_day_total(
    context: SnapchatSyncContext,
    client: httpx.AsyncClient,
    access_token: str,
    *,
    account_id: str,
    request_start: datetime,
    request_end: datetime,
) -> tuple[dict[str, int | float | None], list[dict[str, Any]]]:
    url = f"{SNAPCHAT_API_BASE}/adaccounts/{account_id}/stats"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
    }
    params: dict[str, Any] | None = {
        "start_time": request_start.isoformat(timespec="seconds"),
        "end_time": request_end.isoformat(timespec="seconds"),
        "granularity": PROVIDER_GRANULARITY,
        "breakdown": PROVIDER_BREAKDOWN,
        "fields": ",".join(STAT_FIELDS),
        "limit": 200,
        "omit_empty": "false",
        "conversion_source_types": CONVERSION_SOURCE_TYPES,
        "swipe_up_attribution_window": SWIPE_ATTRIBUTION_WINDOW,
        "view_attribution_window": VIEW_ATTRIBUTION_WINDOW,
        "action_report_time": ACTION_REPORT_TIME,
    }
    rows: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    successful_subrequests = 0

    for _ in range(MAX_PAGES):
        payload = await context.get_json(
            client,
            url,
            headers=headers,
            params=params,
        )
        page_rows, page_errors, page_success = extract_account_total_rows(
            payload,
            request_start=request_start,
            request_end=request_end,
        )
        rows.extend(page_rows)
        errors.extend(page_errors)
        successful_subrequests += page_success
        next_url = _safe_next_url(
            (payload.get("paging") or {}).get("next_link")
        )
        if not next_url:
            break
        url, params = next_url, None

    if successful_subrequests == 0 and errors:
        first = errors[0]
        raise SnapchatNativeSyncError(
            str(first.get("code") or "snapchat_account_total_failed"),
            str(first.get("message") or "Snapchat account TOTAL request failed."),
            status_code=502,
            retryable=True,
            result={"errors": errors[:10]},
        )
    return aggregate_account_total_rows(rows), errors


def aggregate_account_hours_by_riyadh_day(
    rows: list[dict[str, Any]],
    *,
    start_date: date,
    end_date: date,
) -> dict[str, dict[str, Any]]:
    """Keep the historical HOUR folding helper for full native-sync tests."""
    business_tz = _timezone(BUSINESS_TIMEZONE)
    daily: dict[str, dict[str, Any]] = {}
    for row in rows:
        point = _parse_datetime(row.get("start_time"))
        if point is None:
            continue
        report_date = point.astimezone(business_tz).date()
        if report_date < start_date or report_date > end_date:
            continue
        metrics = {
            key: _as_number((row.get("metrics") or {}).get(key))
            for key in STAT_FIELDS
        }
        bucket = daily.setdefault(report_date.isoformat(), _new_bucket())
        _add_to_bucket(
            bucket,
            metrics,
            provider_start=row.get("start_time"),
            provider_end=row.get("end_time"),
        )
    return daily


async def refresh_snapchat_account_hours(
    context: SnapchatSyncContext,
    client: httpx.AsyncClient,
    access_token: str,
    account: dict[str, Any],
    *,
    start_date: date,
    end_date: date,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Refresh one account using one TOTAL campaign breakdown per day."""
    account_id = str(account.get("ad_account_id") or "").strip()
    if not account_id:
        raise SnapchatNativeSyncError(
            "snapchat_account_id_missing",
            "Selected Snapchat account is missing its ad account ID.",
            status_code=409,
        )

    saved = 0
    errors: list[dict[str, Any]] = []
    request_windows: list[dict[str, str]] = []
    cursor = start_date
    while cursor <= end_date:
        window = snapchat_total_request_window(cursor, now=now)
        if window is None:
            cursor += timedelta(days=1)
            continue
        request_start, request_end = window
        request_windows.append({
            "date": cursor.isoformat(),
            "start_time": request_start.isoformat(timespec="seconds"),
            "end_time": request_end.isoformat(timespec="seconds"),
        })
        try:
            metrics, day_errors = await _fetch_account_day_total(
                context,
                client,
                access_token,
                account_id=account_id,
                request_start=request_start,
                request_end=request_end,
            )
        except SnapchatNativeSyncError as exc:
            if exc.code == "snapchat_needs_reauth":
                raise
            errors.append({
                "date": cursor.isoformat(),
                "code": exc.code,
                "message": exc.message,
                "retryable": exc.retryable,
            })
            cursor += timedelta(days=1)
            continue

        for error in day_errors:
            errors.append({"date": cursor.isoformat(), **error})
        await _upsert_performance(
            context,
            account=account,
            entity_type="ad_account",
            external_id=account_id,
            date_string=cursor.isoformat(),
            metrics=metrics,
            provider_start=request_start.isoformat(timespec="seconds"),
            provider_end=request_end.isoformat(timespec="seconds"),
            source_mode=ACCOUNT_REFRESH_SOURCE_MODE,
            provider_granularity=PROVIDER_GRANULARITY,
            provider_breakdown=PROVIDER_BREAKDOWN,
        )
        saved += 1
        cursor += timedelta(days=1)

    return {
        "provider": SNAPCHAT_PROVIDER_ID,
        "ad_account_id": account_id,
        "date_from": start_date.isoformat(),
        "date_to": end_date.isoformat(),
        "rows_saved": saved,
        "errors_count": len(errors),
        "errors": errors,
        "provider_calls": context.provider_calls,
        "source_mode": ACCOUNT_REFRESH_SOURCE_MODE,
        "provider_granularity": PROVIDER_GRANULARITY,
        "provider_breakdown": PROVIDER_BREAKDOWN,
        "request_windows": request_windows[:10],
        "business_timezone": BUSINESS_TIMEZONE,
        "conversion_metric": "conversion_purchases",
        "conversion_source_types": [CONVERSION_SOURCE_TYPES],
        "action_report_time": ACTION_REPORT_TIME,
        "swipe_up_attribution_window": SWIPE_ATTRIBUTION_WINDOW,
        "view_attribution_window": VIEW_ATTRIBUTION_WINDOW,
        "source_only": True,
        "accounting_write_reached": False,
        "qoyod_write_reached": False,
    }


__all__ = [
    "ACCOUNT_REFRESH_SOURCE_MODE",
    "ACTION_REPORT_TIME",
    "CONVERSION_SOURCE_TYPES",
    "PROVIDER_BREAKDOWN",
    "PROVIDER_GRANULARITY",
    "SWIPE_ATTRIBUTION_WINDOW",
    "VIEW_ATTRIBUTION_WINDOW",
    "_fetch_account_day_total",
    "aggregate_account_hours_by_riyadh_day",
    "aggregate_account_total_rows",
    "extract_account_total_rows",
    "refresh_snapchat_account_hours",
    "snapchat_hourly_request_window",
    "snapchat_total_request_window",
]
