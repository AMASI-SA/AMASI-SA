"""Snapchat account analytics aligned to the Riyadh business day.

Each Snapchat ad account keeps its native timezone. Mezan defines the reporting
calendar in Asia/Riyadh, so this module builds a Riyadh business window, converts
the same instants to the account timezone for the provider request, requests
HOUR campaign buckets, and folds those buckets back into Riyadh calendar days.
"""
from __future__ import annotations

from dataclasses import dataclass
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
CAMPAIGN_FACTS_SOURCE_MODE = "snapchat_account_hourly_campaign_facts_riyadh_v4"
CAMPAIGN_FACTS_SCHEMA_VERSION = 4
# Provider contract: granularity="HOUR" with account campaign breakdown.
PROVIDER_GRANULARITY = "HOUR"
PROVIDER_BREAKDOWN = "campaign"
CONVERSION_SOURCE_TYPES = "total"
ACTION_REPORT_TIME = "conversion"
SWIPE_ATTRIBUTION_WINDOW = "28_DAY"
VIEW_ATTRIBUTION_WINDOW = "1_DAY"


@dataclass(frozen=True)
class AccountHourFetchResult:
    """Validated provider rows plus the minimal P0 coverage contract.

    Iteration intentionally preserves the historical ``rows, errors = result``
    call contract used by the account-timezone projection.
    """

    rows: list[dict[str, Any]]
    errors: list[dict[str, Any]]
    coverage: dict[str, Any]

    def __iter__(self):
        yield self.rows
        yield self.errors


ACCOUNT_HOUR_RESULT_NAMES = frozenset({
    "business_result",
    "conversion_result",
    "impression_result",
    "hourly_capture_source",
    "hourly_result_set",
})


def require_account_hour_fetch_result(
    value: Any,
    *,
    result_name: str,
) -> AccountHourFetchResult:
    """Require the success-only fetch contract without inventing coverage.

    ``_fetch_account_hours`` returns complete provider proof or raises.  A
    wrapper or consumer must not downgrade a missing proof into rows/errors.
    """
    safe_name = (
        result_name
        if result_name in ACCOUNT_HOUR_RESULT_NAMES
        else "hourly_result_set"
    )
    valid = isinstance(value, AccountHourFetchResult)
    if valid:
        coverage = value.coverage
        expected = (
            coverage.get("expected_requests")
            if isinstance(coverage, dict)
            else None
        )
        completed = (
            coverage.get("completed_requests")
            if isinstance(coverage, dict)
            else None
        )
        data_state = (
            coverage.get("data_state")
            if isinstance(coverage, dict)
            else None
        )
        valid = bool(
            isinstance(value.rows, list)
            and all(isinstance(row, dict) for row in value.rows)
            and isinstance(value.errors, list)
            and not value.errors
            and isinstance(coverage, dict)
            and coverage.get("status") == "complete"
            and data_state in {
                "confirmed_data",
                "confirmed_zero",
                "confirmed_no_data",
            }
            and type(expected) is int
            and expected > 0
            and type(completed) is int
            and completed == expected
            and (
                (data_state == "confirmed_no_data" and not value.rows)
                or (data_state != "confirmed_no_data" and bool(value.rows))
            )
        )
    if not valid:
        raise SnapchatNativeSyncError(
            "snapchat_account_hour_result_contract_invalid",
            "Snapchat account HOUR fetch returned an invalid result contract.",
            status_code=502,
            retryable=True,
            result={
                "contract_valid": False,
                "result_name": safe_name,
            },
        )
    return value


def _coverage(
    *,
    status: str,
    data_state: str,
    expected_requests: int,
    completed_requests: int,
    reason: str | None = None,
) -> dict[str, Any]:
    result = {
        "status": status,
        "data_state": data_state,
        "expected_requests": max(int(expected_requests), 0),
        "completed_requests": max(int(completed_requests), 0),
    }
    if reason:
        result["reason"] = reason
    return result


def _incomplete_coverage_error(
    code: str,
    message: str,
    *,
    expected_requests: int,
    completed_requests: int,
    errors: list[dict[str, Any]] | None = None,
) -> SnapchatNativeSyncError:
    result: dict[str, Any] = {
        "coverage": _coverage(
            status="incomplete",
            data_state="unknown_incomplete",
            expected_requests=expected_requests,
            completed_requests=completed_requests,
            reason=code,
        )
    }
    if errors:
        result["errors"] = errors[:10]
    return SnapchatNativeSyncError(
        code,
        message,
        status_code=502,
        retryable=True,
        result=result,
    )


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
    if not isinstance(payload, dict) or str(
        payload.get("request_status") or ""
    ).upper() != "SUCCESS":
        raise _incomplete_coverage_error(
            "snapchat_account_hour_request_status_invalid",
            "Snapchat did not confirm a successful account HOUR response.",
            expected_requests=1,
            completed_requests=0,
        )
    if "timeseries_stats" not in payload:
        raise _incomplete_coverage_error(
            "snapchat_account_hour_timeseries_missing",
            "Snapchat account HOUR response is missing timeseries_stats.",
            expected_requests=1,
            completed_requests=0,
        )

    wrapped_stats = payload.get("timeseries_stats")
    if not isinstance(wrapped_stats, list):
        raise _incomplete_coverage_error(
            "snapchat_account_hour_payload_invalid",
            "Snapchat returned invalid account HOUR performance data.",
            expected_requests=1,
            completed_requests=0,
        )
    if not wrapped_stats:
        raise _incomplete_coverage_error(
            "snapchat_account_hour_result_envelope_missing",
            "Snapchat returned no account HOUR result envelope.",
            expected_requests=1,
            completed_requests=0,
        )

    rows: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    successful_subrequests = 0
    for wrapped in wrapped_stats:
        if not isinstance(wrapped, dict):
            raise _incomplete_coverage_error(
                "snapchat_account_hour_wrapper_invalid",
                "Snapchat returned an invalid account HOUR wrapper.",
                expected_requests=1,
                completed_requests=0,
            )
        status = str(wrapped.get("sub_request_status") or "").upper()
        if status != "SUCCESS":
            errors.append(_subrequest_error(wrapped, status))
            continue

        stat = wrapped.get("timeseries_stat")
        if not isinstance(stat, dict):
            raise _incomplete_coverage_error(
                "snapchat_account_hour_timeseries_stat_missing",
                "Snapchat account HOUR wrapper is missing timeseries_stat.",
                expected_requests=1,
                completed_requests=0,
            )
        granularity = str(stat.get("granularity") or "").upper()
        if granularity and granularity != PROVIDER_GRANULARITY:
            raise _incomplete_coverage_error(
                "snapchat_account_hour_granularity_invalid",
                "Snapchat account HOUR response used an unexpected granularity.",
                expected_requests=1,
                completed_requests=0,
            )

        breakdown = stat.get("breakdown_stats")
        campaign_rows = (
            breakdown.get(PROVIDER_BREAKDOWN)
            if isinstance(breakdown, dict)
            else None
        )
        if not isinstance(campaign_rows, list):
            raise _incomplete_coverage_error(
                "snapchat_account_hour_campaign_breakdown_missing",
                "Snapchat account HOUR response is missing its campaign breakdown.",
                expected_requests=1,
                completed_requests=0,
            )

        wrapper_rows: list[dict[str, Any]] = []
        for entity in campaign_rows:
            if not isinstance(entity, dict):
                raise _incomplete_coverage_error(
                    "snapchat_account_hour_campaign_invalid",
                    "Snapchat returned an invalid campaign HOUR row.",
                    expected_requests=1,
                    completed_requests=0,
                )
            campaign_id = str(entity.get("id") or "").strip()
            if not campaign_id:
                raise _incomplete_coverage_error(
                    "snapchat_account_hour_campaign_id_missing",
                    "Snapchat campaign HOUR data is missing its campaign ID.",
                    expected_requests=1,
                    completed_requests=0,
                )
            points = entity.get("timeseries")
            if not isinstance(points, list):
                raise _incomplete_coverage_error(
                    "snapchat_account_hour_timeseries_invalid",
                    "Snapchat campaign HOUR data has an invalid timeseries.",
                    expected_requests=1,
                    completed_requests=0,
                )
            for point in points:
                if not isinstance(point, dict):
                    raise _incomplete_coverage_error(
                        "snapchat_account_hour_point_invalid",
                        "Snapchat returned an invalid campaign HOUR point.",
                        expected_requests=1,
                        completed_requests=0,
                    )
                raw_metrics = point.get("stats")
                if not isinstance(raw_metrics, dict):
                    raise _incomplete_coverage_error(
                        "snapchat_account_hour_stats_missing",
                        "Snapchat campaign HOUR point is missing stats.",
                        expected_requests=1,
                        completed_requests=0,
                    )
                start_time = _parse_datetime(point.get("start_time"))
                end_time = _parse_datetime(point.get("end_time"))
                if start_time is None or end_time is None or end_time <= start_time:
                    raise _incomplete_coverage_error(
                        "snapchat_account_hour_window_invalid",
                        "Snapchat campaign HOUR point has an invalid time window.",
                        expected_requests=1,
                        completed_requests=0,
                    )
                metrics: dict[str, int | float | None] = {}
                observed_values = 0
                for key in STAT_FIELDS:
                    if key not in raw_metrics:
                        continue
                    value = _as_number(raw_metrics.get(key))
                    if raw_metrics.get(key) is not None and value is None:
                        raise _incomplete_coverage_error(
                            "snapchat_account_hour_metric_invalid",
                            "Snapchat campaign HOUR point contains an invalid metric.",
                            expected_requests=1,
                            completed_requests=0,
                        )
                    metrics[key] = value
                    if value is not None:
                        observed_values += 1
                if observed_values == 0:
                    raise _incomplete_coverage_error(
                        "snapchat_account_hour_metrics_empty",
                        "Snapchat campaign HOUR point contains no observed metrics.",
                        expected_requests=1,
                        completed_requests=0,
                    )
                wrapper_rows.append(
                    {
                        "campaign_id": campaign_id,
                        "start_time": point.get("start_time"),
                        "end_time": point.get("end_time"),
                        "metrics": metrics,
                    }
                )
        rows.extend(wrapper_rows)
        successful_subrequests += 1

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
) -> AccountHourFetchResult:
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
    completed_requests = 0

    for _ in range(MAX_PAGES):
        payload = await context.get_json(
            client,
            url,
            headers=headers,
            params=params,
        )
        try:
            page_rows, page_errors, _ = extract_account_hour_rows(payload)
        except SnapchatNativeSyncError as exc:
            exc.result = {
                **dict(exc.result or {}),
                "coverage": _coverage(
                    status="incomplete",
                    data_state="unknown_incomplete",
                    expected_requests=completed_requests + 1,
                    completed_requests=completed_requests,
                    reason=exc.code,
                ),
            }
            raise
        normalized_request_start = (
            request_start
            if request_start.tzinfo is not None
            else request_start.replace(tzinfo=timezone.utc)
        ).astimezone(timezone.utc)
        normalized_request_end = (
            request_end
            if request_end.tzinfo is not None
            else request_end.replace(tzinfo=timezone.utc)
        ).astimezone(timezone.utc)
        for row in page_rows:
            row_start = _parse_datetime(row.get("start_time"))
            row_end = _parse_datetime(row.get("end_time"))
            if row_start is not None and row_start.tzinfo is None:
                row_start = row_start.replace(tzinfo=timezone.utc)
            if row_end is not None and row_end.tzinfo is None:
                row_end = row_end.replace(tzinfo=timezone.utc)
            if (
                row_start is None
                or row_end is None
                or row_start.astimezone(timezone.utc) < normalized_request_start
                or row_end.astimezone(timezone.utc) > normalized_request_end
            ):
                raise _incomplete_coverage_error(
                    "snapchat_account_hour_window_mismatch",
                    "Snapchat account HOUR data falls outside the requested window.",
                    expected_requests=completed_requests + 1,
                    completed_requests=completed_requests,
                )
        rows.extend(page_rows)
        errors.extend(page_errors)
        if page_errors:
            first = page_errors[0]
            raise _incomplete_coverage_error(
                str(first.get("code") or "snapchat_account_hours_partial"),
                str(
                    first.get("message")
                    or "Snapchat account HOUR response was partial."
                ),
                expected_requests=completed_requests + 1,
                completed_requests=completed_requests,
                errors=page_errors,
            )
        completed_requests += 1

        paging = payload.get("paging")
        if paging is not None and not isinstance(paging, dict):
            raise _incomplete_coverage_error(
                "snapchat_account_hour_paging_invalid",
                "Snapchat returned invalid account HOUR pagination metadata.",
                expected_requests=completed_requests + 1,
                completed_requests=completed_requests,
            )
        raw_next_url = (paging or {}).get("next_link")
        if not str(raw_next_url or "").strip():
            break
        next_url = _safe_next_url(raw_next_url)
        if not next_url:
            raise _incomplete_coverage_error(
                "snapchat_account_hour_next_link_invalid",
                "Snapchat returned an invalid account HOUR next page link.",
                expected_requests=completed_requests + 1,
                completed_requests=completed_requests,
            )
        if completed_requests >= MAX_PAGES:
            raise _incomplete_coverage_error(
                "snapchat_account_hour_pagination_incomplete",
                "Snapchat account HOUR response exceeded the pagination limit.",
                expected_requests=completed_requests + 1,
                completed_requests=completed_requests,
            )
        url, params = next_url, None

    confirmed_zero = bool(rows) and all(
        all(
            key in (row.get("metrics") or {})
            and isinstance((row.get("metrics") or {}).get(key), (int, float))
            and not isinstance((row.get("metrics") or {}).get(key), bool)
            and (row.get("metrics") or {}).get(key) == 0
            for key in STAT_FIELDS
        )
        for row in rows
    )
    data_state = (
        "confirmed_no_data"
        if not rows
        else "confirmed_zero"
        if confirmed_zero
        else "confirmed_data"
    )
    return AccountHourFetchResult(
        rows=rows,
        errors=errors,
        coverage=_coverage(
            status="complete",
            data_state=data_state,
            expected_requests=completed_requests,
            completed_requests=completed_requests,
        ),
    )


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


def aggregate_campaign_hours_by_riyadh_day(
    rows: list[dict[str, Any]],
    *,
    start_date: date,
    end_date: date,
) -> dict[tuple[str, str], dict[str, Any]]:
    """Fold the already-fetched HOUR rows into campaign/Riyadh-day facts.

    This deliberately consumes the same rows used for the account total.  It
    never makes another provider request, and rows without a campaign identity
    remain eligible for the account aggregate while failing closed for a
    campaign write.
    """
    business_tz = _timezone(BUSINESS_TIMEZONE)
    daily: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        campaign_id = str(row.get("campaign_id") or "").strip()
        point = _parse_datetime(row.get("start_time"))
        if not campaign_id or point is None:
            continue
        report_date = point.astimezone(business_tz).date()
        if report_date < start_date or report_date > end_date:
            continue
        metrics = {
            key: _as_number((row.get("metrics") or {}).get(key))
            for key in STAT_FIELDS
        }
        bucket = daily.setdefault(
            (campaign_id, report_date.isoformat()),
            _new_bucket(),
        )
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
            "campaign_rows_saved": 0,
            "campaign_facts_source_mode": CAMPAIGN_FACTS_SOURCE_MODE,
            "campaign_facts_schema_version": CAMPAIGN_FACTS_SCHEMA_VERSION,
            "errors_count": 0,
            "errors": [],
            "provider_calls": context.provider_calls,
            "source_mode": ACCOUNT_REFRESH_SOURCE_MODE,
            "provider_granularity": PROVIDER_GRANULARITY,
            "provider_breakdown": PROVIDER_BREAKDOWN,
            "account_timezone": timezone_name,
            "business_timezone": BUSINESS_TIMEZONE,
            "request_windows": [],
            "coverage": _coverage(
                status="incomplete",
                data_state="unknown_incomplete",
                expected_requests=0,
                completed_requests=0,
                reason="request_window_unavailable",
            ),
            "source_only": True,
            "accounting_write_reached": False,
            "qoyod_write_reached": False,
        }

    used_completed_hour_fallback = False
    try:
        fetched = await _fetch_account_hours(
            context,
            client,
            access_token,
            account_id=account_id,
            request_start=request["provider_start"],
            request_end=request["provider_end"],
        )
        rows, errors = fetched
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
        fetched = await _fetch_account_hours(
            context,
            client,
            access_token,
            account_id=account_id,
            request_start=request["provider_start"],
            request_end=request["provider_end"],
        )
        rows, errors = fetched

    daily = aggregate_account_hours_by_riyadh_day(
        rows,
        start_date=start_date,
        end_date=end_date,
    )
    campaign_daily = aggregate_campaign_hours_by_riyadh_day(
        rows,
        start_date=start_date,
        end_date=end_date,
    )

    saved = 0
    campaign_rows_saved = 0
    for (campaign_id, date_string), bucket in sorted(campaign_daily.items()):
        await _upsert_performance(
            context,
            account=account,
            entity_type="campaign",
            external_id=campaign_id,
            date_string=date_string,
            metrics=_finalize_bucket(bucket),
            provider_start=bucket.get("provider_start"),
            provider_end=bucket.get("provider_end"),
            source_mode=CAMPAIGN_FACTS_SOURCE_MODE,
            provider_granularity=PROVIDER_GRANULARITY,
            provider_breakdown=PROVIDER_BREAKDOWN,
        )
        saved += 1
        campaign_rows_saved += 1

    for date_string, bucket in sorted(daily.items()):
        cursor = date.fromisoformat(date_string)
        provider_window = _day_provider_window(
            cursor,
            business_start=request["business_start"],
            business_end=request["business_end"],
            account_timezone=account_timezone,
        )
        if provider_window is None:
            continue

        provider_start, provider_end = provider_window
        await _upsert_performance(
            context,
            account=account,
            entity_type="ad_account",
            external_id=account_id,
            date_string=date_string,
            metrics=_finalize_bucket(bucket),
            provider_start=provider_start.isoformat(timespec="seconds"),
            provider_end=provider_end.isoformat(timespec="seconds"),
            source_mode=ACCOUNT_REFRESH_SOURCE_MODE,
            provider_granularity=PROVIDER_GRANULARITY,
            provider_breakdown=PROVIDER_BREAKDOWN,
        )
        saved += 1

    return {
        "provider": SNAPCHAT_PROVIDER_ID,
        "ad_account_id": account_id,
        "date_from": start_date.isoformat(),
        "date_to": end_date.isoformat(),
        "rows_saved": saved,
        "campaign_rows_saved": campaign_rows_saved,
        "campaign_facts_source_mode": CAMPAIGN_FACTS_SOURCE_MODE,
        "campaign_facts_schema_version": CAMPAIGN_FACTS_SCHEMA_VERSION,
        "errors_count": len(errors),
        "errors": errors,
        "coverage": fetched.coverage,
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
    "AccountHourFetchResult",
    "ACTION_REPORT_TIME",
    "CAMPAIGN_FACTS_SCHEMA_VERSION",
    "CAMPAIGN_FACTS_SOURCE_MODE",
    "CONVERSION_SOURCE_TYPES",
    "PROVIDER_BREAKDOWN",
    "PROVIDER_GRANULARITY",
    "SWIPE_ATTRIBUTION_WINDOW",
    "VIEW_ATTRIBUTION_WINDOW",
    "_fetch_account_hours",
    "aggregate_account_hours_by_riyadh_day",
    "aggregate_campaign_hours_by_riyadh_day",
    "extract_account_hour_rows",
    "require_account_hour_fetch_result",
    "refresh_snapchat_account_hours",
    "snapchat_account_request_window",
    "snapchat_hourly_request_window",
    "snapchat_total_request_window",
]
