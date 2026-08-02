"""Lightweight Snapchat account totals for the five-minute scheduler.

The existing full native sync keeps campaign detail.  This module performs a
single HOUR-granularity account request per selected ad account and persists one
ad-account row per Riyadh business day.  It is intentionally small enough to
run every five minutes without reloading campaign entities.
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

ACCOUNT_REFRESH_SOURCE_MODE = "snapchat_account_hourly_riyadh_refresh_v2"
CONVERSION_SOURCE_TYPES = "total"
ACTION_REPORT_TIME = "conversion"
SWIPE_ATTRIBUTION_WINDOW = "28_DAY"
VIEW_ATTRIBUTION_WINDOW = "1_DAY"


def snapchat_hourly_request_window(
    start_date: date,
    end_date: date,
    *,
    now: datetime | None = None,
) -> tuple[datetime, datetime]:
    """Return a provider-safe HOUR window without asking for future data.

    The merchant date range is expressed in Riyadh time.  For a range that
    includes today, Snap must only receive completed hour boundaries; sending
    the following midnight while the day is still in progress causes account
    stats requests to fail.
    """
    start, nominal_end = riyadh_business_window(start_date, end_date)
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    business_tz = _timezone(BUSINESS_TIMEZONE)
    current_business = current.astimezone(business_tz)
    # Historical ranges are complete and must not be truncated by today's
    # clock. Clamp only when the requested range reaches the current Riyadh
    # business day.
    if end_date < current_business.date():
        return start, nominal_end
    completed_hour_end = current_business.replace(
        minute=0,
        second=0,
        microsecond=0,
    )
    return start, min(nominal_end, completed_hour_end)


async def _fetch_account_hours(
    context: SnapchatSyncContext,
    client: httpx.AsyncClient,
    access_token: str,
    *,
    account_id: str,
    request_start: datetime,
    request_end: datetime,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    url = f"{SNAPCHAT_API_BASE}/adaccounts/{account_id}/stats"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
    }
    params: dict[str, Any] | None = {
        "start_time": request_start.isoformat(timespec="seconds"),
        "end_time": request_end.isoformat(timespec="seconds"),
        "granularity": "HOUR",
        "fields": ",".join(STAT_FIELDS),
        "limit": 200,
        "omit_empty": "false",
        # Be explicit instead of relying on provider defaults.  ``total`` is
        # Snapchat's all-conversion-events bucket and prevents the Dashboard
        # purchase count from silently representing only one source subtype.
        "conversion_source_types": CONVERSION_SOURCE_TYPES,
        "swipe_up_attribution_window": SWIPE_ATTRIBUTION_WINDOW,
        "view_attribution_window": VIEW_ATTRIBUTION_WINDOW,
        "action_report_time": ACTION_REPORT_TIME,
    }
    rows: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []

    for _ in range(MAX_PAGES):
        payload = await context.get_json(
            client,
            url,
            headers=headers,
            params=params,
        )
        wrapped_stats = payload.get("timeseries_stats") or []
        if not isinstance(wrapped_stats, list):
            raise SnapchatNativeSyncError(
                "snapchat_account_stats_payload_invalid",
                "Snapchat returned invalid account performance data.",
                status_code=502,
                retryable=True,
            )
        for wrapped in wrapped_stats:
            if not isinstance(wrapped, dict):
                continue
            status = str(
                wrapped.get("sub_request_status") or "SUCCESS"
            ).upper()
            if "FAIL" in status or "ERROR" in status:
                errors.append({"kind": "account_stats", "error": status[:80]})
                continue
            stat = wrapped.get("timeseries_stat", wrapped)
            if not isinstance(stat, dict):
                continue
            points = stat.get("timeseries")
            if not isinstance(points, list):
                continue
            for point in points:
                if (
                    isinstance(point, dict)
                    and isinstance(point.get("stats"), dict)
                ):
                    rows.append(
                        {
                            "start_time": point.get("start_time"),
                            "end_time": point.get("end_time"),
                            "metrics": point["stats"],
                            "conversion_data_processed_end_time": stat.get(
                                "conversion_data_processed_end_time"
                            ),
                            "finalized_data_end_time": stat.get(
                                "finalized_data_end_time"
                            ),
                        }
                    )
        next_url = _safe_next_url(
            (payload.get("paging") or {}).get("next_link")
        )
        if not next_url:
            break
        url, params = next_url, None

    return rows, errors


def aggregate_account_hours_by_riyadh_day(
    rows: list[dict[str, Any]],
    *,
    start_date: date,
    end_date: date,
) -> dict[str, dict[str, Any]]:
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
    account_id = str(account.get("ad_account_id") or "").strip()
    if not account_id:
        raise SnapchatNativeSyncError(
            "snapchat_account_id_missing",
            "Selected Snapchat account is missing its ad account ID.",
            status_code=409,
        )

    request_start, request_end = snapchat_hourly_request_window(
        start_date,
        end_date,
        now=now,
    )
    if request_end <= request_start:
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
            "business_timezone": BUSINESS_TIMEZONE,
            "reason": "no_completed_hour_available",
            "request_start_time": request_start.isoformat(timespec="seconds"),
            "request_end_time": request_end.isoformat(timespec="seconds"),
            "source_only": True,
            "accounting_write_reached": False,
            "qoyod_write_reached": False,
        }

    rows, errors = await _fetch_account_hours(
        context,
        client,
        access_token,
        account_id=account_id,
        request_start=request_start,
        request_end=request_end,
    )
    daily = aggregate_account_hours_by_riyadh_day(
        rows,
        start_date=start_date,
        end_date=end_date,
    )

    business_tz = _timezone(BUSINESS_TIMEZONE)
    covered_end_date = (
        request_end.astimezone(business_tz) - timedelta(microseconds=1)
    ).date()
    persist_end_date = min(end_date, covered_end_date)

    saved = 0
    cursor = start_date
    while cursor <= persist_end_date:
        date_string = cursor.isoformat()
        bucket = daily.get(date_string)
        # A valid, empty account response with omit_empty=false is a confirmed
        # zero-spend day, not an unknown value.
        metrics = (
            _finalize_bucket(bucket)
            if bucket is not None
            else {key: 0 for key in STAT_FIELDS}
        )
        window_start, window_end = riyadh_business_window(cursor, cursor)
        if cursor == persist_end_date and request_end < window_end:
            window_end = request_end
        await _upsert_performance(
            context,
            account=account,
            entity_type="ad_account",
            external_id=account_id,
            date_string=date_string,
            metrics=metrics,
            provider_start=window_start.isoformat(timespec="seconds"),
            provider_end=window_end.isoformat(timespec="seconds"),
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
        "business_timezone": BUSINESS_TIMEZONE,
        "conversion_metric": "conversion_purchases",
        "conversion_source_types": [CONVERSION_SOURCE_TYPES],
        "action_report_time": ACTION_REPORT_TIME,
        "swipe_up_attribution_window": SWIPE_ATTRIBUTION_WINDOW,
        "view_attribution_window": VIEW_ATTRIBUTION_WINDOW,
        "request_start_time": request_start.isoformat(timespec="seconds"),
        "request_end_time": request_end.isoformat(timespec="seconds"),
        "source_only": True,
        "accounting_write_reached": False,
        "qoyod_write_reached": False,
    }


__all__ = [
    "ACCOUNT_REFRESH_SOURCE_MODE",
    "ACTION_REPORT_TIME",
    "CONVERSION_SOURCE_TYPES",
    "SWIPE_ATTRIBUTION_WINDOW",
    "VIEW_ATTRIBUTION_WINDOW",
    "aggregate_account_hours_by_riyadh_day",
    "refresh_snapchat_account_hours",
    "snapchat_hourly_request_window",
]
