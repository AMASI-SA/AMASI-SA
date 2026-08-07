"""Snapchat account analytics aligned to the Riyadh business day.

Each Snapchat ad account keeps its native timezone. Mezan defines the reporting
calendar in Asia/Riyadh, so this module builds a Riyadh business window, converts
the same instants to the account timezone for the provider request, requests
HOUR campaign buckets, and folds those buckets back into Riyadh calendar days.
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
    "snapchat_account_hourly_campaign_breakdown_riyadh_refresh_v3"
)
# Provider contract: granularity="HOUR" with account campaign breakdown.
PROVIDER_GRANULARITY = "HOUR"
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


def _floor_hour(value: datetime) -> datetime:
    return value.replace(minute=0, second=0, microsecond=0)


def _ceil_hour(value: datetime) -> datetime:
    floor = _floor_hour(value)
    return floor if value == floor else floor + timedelta(hours=1)


def _validated_account_timezone(account: dict[str, Any]) -> tuple[str, Any]:
    timezone_name = str(account.get("timezone") or "").strip()
    if not timezone_name:
        raise SnapchatNativeSyncError(
            "snapchat_account_timezone_missing",
            "Selected Snapchat account is missing its native timezone.",
            status_code=409,
            retryable=False,
        )
    account_timezone = _timezone(timezone_name)
    if str(account_timezone) != timezone_name:
        raise SnapchatNativeSyncError(
            "snapchat_account_timezone_invalid",
            "Selected Snapchat account has an invalid native timezone.",
            status_code=409,
            retryable=False,
            result={"account_timezone": timezone_name},
        )
    return timezone_name, account_timezone


def snapchat_hourly_request_window(
    start_date: date,
    end_date: date,
    *,
    now: datetime | None = None,
) -> tuple[datetime, datetime]:
    """Return a provider-safe Riyadh window ending at a completed hour.

    The helper is retained for contracts and as the fallback when Snapchat does
    not accept a request that includes the current, still-open hour.
    """
    start, nominal_end = riyadh_business_window(start_date, end_date)
    current_business = _aware_now(now).astimezone(_timezone(BUSINESS_TIMEZONE))
    if end_date < current_business.date():
        return start, nominal_end
    completed_hour_end = _floor_hour(current_business)
    return start, min(nominal_end, completed_hour_end)


def _riyadh_live_request_window(
    start_date: date,
    end_date: date,
    *,
    now: datetime | None = None,
) -> tuple[datetime, datetime] | None:
    """Return the Riyadh range through the currently open hour.

    Snap requires HOUR requests to use hour-aligned boundaries. The exclusive
    end therefore advances to the next hour while the current hour is open.
    Response rows are still filtered into the requested Riyadh business dates.
    """
    start, nominal_end = riyadh_business_window(start_date, end_date)
    current_business = _aware_now(now).astimezone(_timezone(BUSINESS_TIMEZONE))
    if start_date > current_business.date():
        return None
    if end_date < current_business.date():
        return start, nominal_end
    end = min(nominal_end, _ceil_hour(current_business))
    return (start, end) if end > start else None


def snapchat_account_request_window(
    start_date: date,
    end_date: date,
    *,
    account_timezone: str,
    now: datetime | None = None,
    include_current_hour: bool = True,
) -> dict[str, datetime] | None:
    """Convert one Riyadh business range to the account's native timezone."""
    if include_current_hour:
        business_window = _riyadh_live_request_window(
            start_date,
            end_date,
            now=now,
        )
    else:
        business_window = snapchat_hourly_request_window(
            start_date,
            end_date,
            now=now,
        )
        if business_window[1] <= business_window[0]:
            return None
    if business_window is None:
        return None

    timezone_name = str(account_timezone or "").strip()
    account_tz = _timezone(timezone_name)
    if not timezone_name or str(account_tz) != timezone_name:
        raise ValueError("account_timezone must be a valid IANA timezone")

    business_start, business_end = business_window
    return {
        "business_start": business_start,
        "business_end": business_end,
        "provider_start": business_start.astimezone(account_tz),
        "provider_end": business_end.astimezone(account_tz),
    }


def snapchat_total_request_window(
    report_date: date,
    *,
    now: datetime | None = None,
) -> tuple[datetime, datetime] | None:
    """Backward-compatible TOTAL helper; the scheduler no longer uses TOTAL."""
    start, nominal_end = riyadh_business_window(report_date, report_date)
    current_business = _aware_now(now).astimezone(_timezone(BUSINESS_TIMEZONE))
    if report_date < current_business.date():
        return start, nominal_end
    if report_date > current_business.date():
        return None
    end = min(nominal_end, current_business.replace(microsecond=0))
    return (start, end) if end > start else None


def _subrequest_error(
    wrapped: dict[str, Any],
    status: str,
) -> dict[str, Any]:
    code = str(
        wrapped.get("error_code")
        or wrapped.get("code")
        or "snapchat_account_hour_subrequest_failed"
    )[:120]
    message = str(
        wrapped.get("error_message")
        or wrapped.get("debug_message")
        or wrapped.get("message")
        or status
        or "Snapchat rejected an account HOUR sub-request."
    )[:300]
    return {
        "kind": "account_hour_campaign_breakdown",
        "code": code,
        "message": message,
        "error": status[:80],
        "retryable": False,
    }


def extract_account_hour_rows(
    payload: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    """Extract campaign HOUR rows and provider sub-request errors."""
    wrapped_stats = payload.get("timeseries_stats") or []
    if not isinstance(wrapped_stats, list):
        raise SnapchatNativeSyncError(
            "snapchat_account_hour_payload_invalid",
            "Snapchat returned invalid account HOUR performance data.",
            status_code=502,
            retryable=True,
        )

    rows: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    successful_subrequests = 0
    for wrapped in wrapped_stats:
        if not isinstance(wrapped, dict):
            continue
        status = str(wrapped.get("sub_request_status") or "SUCCESS").upper()
        if "FAIL" in status or "ERROR" in status:
            errors.append(_subrequest_error(wrapped, status))
            continue

        successful_subrequests += 1
        stat = wrapped.get("timeseries_stat", wrapped)
        if not isinstance(stat, dict):
            continue

        entities: list[dict[str, Any]] = []
        breakdown = stat.get("breakdown_stats")
        if isinstance(breakdown, dict):
            campaign_rows = breakdown.get(PROVIDER_BREAKDOWN)
            if isinstance(campaign_rows, list):
                entities.extend(
                    item for item in campaign_rows if isinstance(item, dict)
                )
        if not entities and isinstance(stat.get("timeseries"), list):
            entities = [stat]

        for entity in entities:
            campaign_id = str(entity.get("id") or "").strip()
            points = entity.get("timeseries")
            if not isinstance(points, list):
                continue
            for point in points:
                if not isinstance(point, dict):
                    continue
                metrics = point.get("stats")
                if not isinstance(metrics, dict):
                    continue
                rows.append(
                    {
                        "campaign_id": campaign_id,
                        "start_time": point.get("start_time"),
                        "end_time": point.get("end_time"),
                        "metrics": metrics,
                    }
                )

    return rows, errors, successful_subrequests


async def _fetch_account_hours(
    context: SnapchatSyncContext,
    client: httpx.AsyncClient,
    access_token: str,
    *,
    account_id: str,
    request_start: datetime | None = None,
    request_end: datetime | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    action_report_time: str | None = None,
    swipe_attribution_window: str | None = None,
    view_attribution_window: str | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Fetch HOUR rows, accepting the legacy date-range call contract too."""
    if request_start is None or request_end is None:
        if start_date is None or end_date is None:
            raise ValueError(
                "request_start/request_end or start_date/end_date are required"
            )
        request_start, request_end = riyadh_business_window(start_date, end_date)

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
        "swipe_up_attribution_window": (
            swipe_attribution_window or SWIPE_ATTRIBUTION_WINDOW
        ),
        "view_attribution_window": (
            view_attribution_window or VIEW_ATTRIBUTION_WINDOW
        ),
        "action_report_time": action_report_time or ACTION_REPORT_TIME,
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
        page_rows, page_errors, page_success = extract_account_hour_rows(payload)
        rows.extend(page_rows)
        errors.extend(page_errors)
        successful_subrequests += page_success
        next_url = _safe_next_url((payload.get("paging") or {}).get("next_link"))
        if not next_url:
            break
        url, params = next_url, None

    if successful_subrequests == 0 and errors:
        first = errors[0]
        raise SnapchatNativeSyncError(
            str(first.get("code") or "snapchat_account_hours_failed"),
            str(first.get("message") or "Snapchat account HOUR request failed."),
            status_code=502,
            retryable=bool(first.get("retryable")),
            result={"errors": errors[:10]},
        )
    return rows, errors


def aggregate_account_hours_by_riyadh_day(
    rows: list[dict[str, Any]],
    *,
    start_date: date,
    end_date: date,
) -> dict[str, dict[str, Any]]:
    """Fold provider-native hourly campaign facts into Riyadh dates."""
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


def _day_provider_window(
    report_date: date,
    *,
    business_start: datetime,
    business_end: datetime,
    account_timezone: Any,
) -> tuple[datetime, datetime] | None:
    day_start, day_end = riyadh_business_window(report_date, report_date)
    clipped_start = max(day_start, business_start)
    clipped_end = min(day_end, business_end)
    if clipped_end <= clipped_start:
        return None
    return (
        clipped_start.astimezone(account_timezone),
        clipped_end.astimezone(account_timezone),
    )


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
    """Refresh one account while preserving Riyadh-day reporting semantics."""
    account_id = str(account.get("ad_account_id") or "").strip()
    if not account_id:
        raise SnapchatNativeSyncError(
            "snapchat_account_id_missing",
            "Selected Snapchat account is missing its ad account ID.",
            status_code=409,
            retryable=False,
        )
    timezone_name, account_timezone = _validated_account_timezone(account)

    request = snapchat_account_request_window(
        start_date,
        end_date,
        account_timezone=timezone_name,
        now=now,
        include_current_hour=True,
    )
    if request is None:
        return {
            "provider": SNAPCHAT_PROVIDER_ID,
            "ad_account_id": account_id,
            "date_from": start_date.isoformat(),
            "date_to": end_date.isoformat(),
            "rows_saved": 0,
            "errors_count": 0,
            "errors": [],
            "provider_calls": context.provider_calls,
            "source_mode": ACCOUNT_REFRESH_SOURCE_MODE,
            "provider_granularity": PROVIDER_GRANULARITY,
            "provider_breakdown": PROVIDER_BREAKDOWN,
            "account_timezone": timezone_name,
            "business_timezone": BUSINESS_TIMEZONE,
            "request_windows": [],
            "source_only": True,
            "accounting_write_reached": False,
            "qoyod_write_reached": False,
        }

    used_completed_hour_fallback = False
    try:
        rows, errors = await _fetch_account_hours(
            context,
            client,
            access_token,
            account_id=account_id,
            request_start=request["provider_start"],
            request_end=request["provider_end"],
        )
    except SnapchatNativeSyncError as exc:
        fallback = snapchat_account_request_window(
            start_date,
            end_date,
            account_timezone=timezone_name,
            now=now,
            include_current_hour=False,
        )
        can_retry_completed = (
            exc.code == "snapchat_provider_http_400"
            and fallback is not None
            and fallback["provider_end"] < request["provider_end"]
        )
        if not can_retry_completed:
            raise
        request = fallback
        used_completed_hour_fallback = True
        rows, errors = await _fetch_account_hours(
            context,
            client,
            access_token,
            account_id=account_id,
            request_start=request["provider_start"],
            request_end=request["provider_end"],
        )

    daily = aggregate_account_hours_by_riyadh_day(
        rows,
        start_date=start_date,
        end_date=end_date,
    )

    saved = 0
    cursor = start_date
    while cursor <= end_date:
        provider_window = _day_provider_window(
            cursor,
            business_start=request["business_start"],
            business_end=request["business_end"],
            account_timezone=account_timezone,
        )
        if provider_window is None:
            cursor += timedelta(days=1)
            continue

        date_string = cursor.isoformat()
        bucket = daily.get(date_string)
        metrics = (
            _finalize_bucket(bucket)
            if bucket is not None
            else {key: 0 for key in STAT_FIELDS}
        )
        provider_start, provider_end = provider_window
        await _upsert_performance(
            context,
            account=account,
            entity_type="ad_account",
            external_id=account_id,
            date_string=date_string,
            metrics=metrics,
            provider_start=provider_start.isoformat(timespec="seconds"),
            provider_end=provider_end.isoformat(timespec="seconds"),
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
        "request_windows": [
            {
                "business_timezone": BUSINESS_TIMEZONE,
                "business_start": request["business_start"].isoformat(
                    timespec="seconds"
                ),
                "business_end": request["business_end"].isoformat(
                    timespec="seconds"
                ),
                "account_timezone": timezone_name,
                "provider_start": request["provider_start"].isoformat(
                    timespec="seconds"
                ),
                "provider_end": request["provider_end"].isoformat(
                    timespec="seconds"
                ),
                "current_hour_included": not used_completed_hour_fallback,
            }
        ],
        "business_timezone": BUSINESS_TIMEZONE,
        "account_timezone": timezone_name,
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
    "_fetch_account_hours",
    "aggregate_account_hours_by_riyadh_day",
    "extract_account_hour_rows",
    "refresh_snapchat_account_hours",
    "snapchat_account_request_window",
    "snapchat_hourly_request_window",
    "snapchat_total_request_window",
]
