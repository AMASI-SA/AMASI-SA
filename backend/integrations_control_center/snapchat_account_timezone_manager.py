"""Account-local Snapchat Ads Manager reporting without changing Riyadh books.

The five-minute scheduler continues to persist the authoritative Riyadh-day
facts used by Dashboard and accounting. In parallel, this module persists a
separate account-local-day projection used only by the Snapchat Ads Manager
workspace. Each report is scoped to one selected ad account and its native
IANA timezone, so accounts in Riyadh and America are never combined into one
calendar day.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query

from . import snapchat_account_hourly_refresh as hourly
from .snapchat_account_selection import _load_selected_accounts
from .snapchat_active_campaign_filtering import is_active_provider_status
from .snapchat_freshness_impl_v6 import (
    ADS_MANAGER_ACTION_REPORT_TIME,
    ADS_MANAGER_DEFAULT_ACTION_REPORT_TIME,
    ADS_MANAGER_SUPPORTED_ACTION_REPORT_TIMES,
    ADS_MANAGER_SWIPE_ATTRIBUTION_WINDOW,
    ADS_MANAGER_VIEW_ATTRIBUTION_WINDOW,
    ads_manager_source_mode,
    normalize_ads_manager_action_report_time,
)
from .snapchat_campaign_result_source_routes import (
    RESULT_SOURCE_PLATFORM,
    RESULT_SOURCE_SALLA,
    SUPPORTED_RESULT_SOURCES,
    _effective_currency,
    _match_order_campaign,
    _number,
    _ratio,
    _text,
    _unique_lookup,
)
from .snapchat_native_data_common import (
    ATTRIBUTION_MODEL,
    BUSINESS_TIMEZONE,
    SNAPCHAT_ENTITY_COLLECTION,
    SNAPCHAT_PERFORMANCE_COLLECTION,
    SNAPCHAT_PROVIDER_ID,
    SnapchatNativeSyncError,
    SnapchatNativeSyncInput,
    SnapchatSyncContext,
    _as_number,
    _collection,
    _parse_datetime,
    _timezone,
    _utcnow,
    enumerate_native_sync_dates,
)
from .snapchat_native_performance_sync import (
    CONVERSION_SOURCE_TYPES,
    STAT_FIELDS,
    SWIPE_ATTRIBUTION_WINDOW,
    VIEW_ATTRIBUTION_WINDOW,
    _add_to_bucket,
    _computed,
    _finalize_bucket,
    _new_bucket,
    _upsert_performance,
)

SNAPCHAT_ACCOUNT_LOCAL_PERFORMANCE_COLLECTION = (
    "mezan_snapchat_performance_account_day_v3"
)


def account_local_source_mode(action_report_time: Any) -> str:
    return f"{ads_manager_source_mode(action_report_time)}:account_day_v3"


ACCOUNT_LOCAL_SOURCE_MODE = account_local_source_mode(
    ADS_MANAGER_DEFAULT_ACTION_REPORT_TIME
)
MAX_REPORT_ROWS = 100_000
MAX_ENTITY_ROWS = 50_000
ACCOUNT_DATE_PADDING_DAYS = 1


def _aware_now(value: datetime | None = None) -> datetime:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current


def _ceil_hour(value: datetime) -> datetime:
    floor = value.replace(minute=0, second=0, microsecond=0)
    return floor if value == floor else floor + timedelta(hours=1)


def _valid_timezone_name(value: Any) -> str:
    name = _text(value)
    zone = _timezone(name)
    if not name or str(zone) != name:
        raise SnapchatNativeSyncError(
            "snapchat_account_timezone_invalid",
            "الحساب الإعلاني لا يحتوي منطقة زمنية صحيحة.",
            status_code=409,
        )
    return name


def account_local_today(
    timezone_name: str,
    *,
    now: datetime | None = None,
) -> date:
    return _aware_now(now).astimezone(_timezone(timezone_name)).date()


def resolve_account_report_dates(
    from_date: str | None,
    to_date: str | None,
    *,
    timezone_name: str,
    now: datetime | None = None,
) -> list[date]:
    today = account_local_today(timezone_name, now=now)
    payload = SnapchatNativeSyncInput(
        days=1,
        from_date=from_date,
        to_date=to_date,
    )
    dates = enumerate_native_sync_dates(payload, today=today)
    if dates[-1] > today:
        raise SnapchatNativeSyncError(
            "future_date_not_allowed",
            "لا يمكن طلب تاريخ يتجاوز اليوم الحالي في توقيت الحساب الإعلاني.",
            status_code=400,
        )
    return dates


def _local_sync_bounds(start_date: date, end_date: date) -> tuple[date, date]:
    return (
        start_date - timedelta(days=ACCOUNT_DATE_PADDING_DAYS),
        end_date + timedelta(days=ACCOUNT_DATE_PADDING_DAYS),
    )


def _combined_request_window(
    start_date: date,
    end_date: date,
    *,
    timezone_name: str,
    now: datetime | None,
    include_current_hour: bool,
) -> dict[str, Any] | None:
    business = hourly.snapchat_account_request_window(
        start_date,
        end_date,
        account_timezone=timezone_name,
        now=now,
        include_current_hour=include_current_hour,
    )
    if business is None:
        return None

    account_tz = _timezone(timezone_name)
    local_from, local_to = _local_sync_bounds(start_date, end_date)
    local_start = datetime(
        local_from.year,
        local_from.month,
        local_from.day,
        tzinfo=account_tz,
    )
    local_exclusive = local_to + timedelta(days=1)
    local_nominal_end = datetime(
        local_exclusive.year,
        local_exclusive.month,
        local_exclusive.day,
        tzinfo=account_tz,
    )
    current_local = _aware_now(now).astimezone(account_tz)
    if local_to < current_local.date():
        local_end = local_nominal_end
    elif local_from > current_local.date():
        local_end = local_start
    elif include_current_hour:
        local_end = min(local_nominal_end, _ceil_hour(current_local))
    else:
        local_end = min(
            local_nominal_end,
            current_local.replace(minute=0, second=0, microsecond=0),
        )

    provider_start = min(business["provider_start"], local_start)
    provider_end = max(business["provider_end"], local_end)
    if provider_end <= provider_start:
        return None
    return {
        **business,
        "provider_start": provider_start,
        "provider_end": provider_end,
        "account_local_from": local_from,
        "account_local_to": local_to,
    }


def _row_metrics(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: _as_number((row.get("metrics") or {}).get(key))
        for key in STAT_FIELDS
    }


def _campaign_day_buckets(
    rows: list[dict[str, Any]],
    *,
    timezone_name: str,
    start_date: date,
    end_date: date,
) -> tuple[
    dict[tuple[str, str], dict[str, Any]],
    dict[str, dict[str, Any]],
]:
    target_tz = _timezone(timezone_name)
    campaigns: dict[tuple[str, str], dict[str, Any]] = {}
    accounts: dict[str, dict[str, Any]] = {}
    for row in rows:
        point = _parse_datetime(row.get("start_time"))
        if point is None:
            continue
        if point.tzinfo is None:
            point = point.replace(tzinfo=timezone.utc)
        report_date = point.astimezone(target_tz).date()
        if report_date < start_date or report_date > end_date:
            continue
        date_string = report_date.isoformat()
        metrics = _row_metrics(row)
        account_bucket = accounts.setdefault(date_string, _new_bucket())
        _add_to_bucket(
            account_bucket,
            metrics,
            provider_start=row.get("start_time"),
            provider_end=row.get("end_time"),
        )
        campaign_id = _text(row.get("campaign_id"))
        if not campaign_id:
            continue
        campaign_bucket = campaigns.setdefault(
            (campaign_id, date_string),
            _new_bucket(),
        )
        _add_to_bucket(
            campaign_bucket,
            metrics,
            provider_start=row.get("start_time"),
            provider_end=row.get("end_time"),
        )
    return campaigns, accounts


def _merge_hourly_coverages(
    results: list[hourly.AccountHourFetchResult],
) -> dict[str, Any]:
    coverages = [
        item.coverage if isinstance(item.coverage, dict) else {}
        for item in results
    ]
    expected_requests = sum(
        int(item.get("expected_requests") or 0) for item in coverages
    )
    completed_requests = sum(
        int(item.get("completed_requests") or 0) for item in coverages
    )
    complete = len(coverages) == 3 and all(
        item.get("status") == "complete"
        and int(item.get("completed_requests") or 0)
        == int(item.get("expected_requests") or 0)
        for item in coverages
    )
    states = {str(item.get("data_state") or "") for item in coverages}
    if not complete:
        data_state = "unknown_incomplete"
    elif "confirmed_data" in states:
        data_state = "confirmed_data"
    elif "confirmed_zero" in states:
        data_state = "confirmed_zero"
    else:
        data_state = "confirmed_no_data"
    return {
        "status": "complete" if complete else "incomplete",
        "data_state": data_state,
        "expected_requests": expected_requests,
        "completed_requests": completed_requests,
    }


async def _ensure_account_local_indexes(db: Any) -> None:
    collection = _collection(
        db, SNAPCHAT_ACCOUNT_LOCAL_PERFORMANCE_COLLECTION
    )
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
        name="mezan_snapchat_account_day_v3_identity_unique",
    )
    await collection.create_index(
        [("user_id", 1), ("ad_account_id", 1), ("date", -1), ("entity_type", 1)],
        name="mezan_snapchat_account_day_v3_date",
    )


async def _upsert_account_local_performance(
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
    action_report_time: str,
) -> None:
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
        "entity_type": entity_type,
        "external_id": external_id,
        "date": date_string,
        "date_timezone": timezone_name,
        "business_timezone": BUSINESS_TIMEZONE,
        "currency": currency or None,
        "account_timezone": timezone_name,
        "attribution_model": ATTRIBUTION_MODEL,
        "metrics": metrics,
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
        "source_mode": account_local_source_mode(action_report_time),
        "accounting_eligible": False,
        "provider_window_start": provider_start,
        "provider_window_end": provider_end,
        "provider_granularity": hourly.PROVIDER_GRANULARITY,
        "provider_breakdown": hourly.PROVIDER_BREAKDOWN,
        "stored_granularity": "ACCOUNT_LOCAL_DAY",
        "report_scope": "snapchat_ads_manager_account_timezone",
        "updated_at": now_iso,
    }
    if entity_type == "campaign":
        document["campaign_id"] = external_id
    await _collection(
        context.db, SNAPCHAT_ACCOUNT_LOCAL_PERFORMANCE_COLLECTION
    ).update_one(
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


async def refresh_snapchat_account_hours_with_account_days(
    context: SnapchatSyncContext,
    client: httpx.AsyncClient,
    access_token: str,
    account: dict[str, Any],
    *,
    start_date: date,
    end_date: date,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Persist Riyadh-day facts plus a separate account-local Ads Manager view."""
    account_id = _text(account.get("ad_account_id"))
    if not account_id:
        raise SnapchatNativeSyncError(
            "snapchat_account_id_missing",
            "Selected Snapchat account is missing its ad account ID.",
            status_code=409,
        )
    timezone_name = _valid_timezone_name(account.get("timezone"))
    await _ensure_account_local_indexes(context.db)

    request = _combined_request_window(
        start_date,
        end_date,
        timezone_name=timezone_name,
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
            "account_local_rows_saved": 0,
            "errors_count": 0,
            "errors": [],
            "provider_calls": context.provider_calls,
            "source_mode": account_local_source_mode(
                ADS_MANAGER_DEFAULT_ACTION_REPORT_TIME
            ),
            "action_report_time": ADS_MANAGER_DEFAULT_ACTION_REPORT_TIME,
            "account_timezone": timezone_name,
            "business_timezone": BUSINESS_TIMEZONE,
            "coverage": {
                "status": "incomplete",
                "data_state": "unknown_incomplete",
                "expected_requests": 0,
                "completed_requests": 0,
                "reason": "request_window_unavailable",
            },
            "source_only": True,
            "accounting_write_reached": False,
            "qoyod_write_reached": False,
        }

    used_completed_hour_fallback = False

    async def fetch_all_modes(window: dict[str, datetime]):
        # Existing Riyadh accounting projection: keep its established
        # conversion-time 28d-click / 1d-view contract unchanged.
        business_result = await hourly._fetch_account_hours(
            context,
            client,
            access_token,
            account_id=account_id,
            request_start=window["provider_start"],
            request_end=window["provider_end"],
            action_report_time=hourly.ACTION_REPORT_TIME,
        )
        business_rows, business_errors = business_result

        # Ads Manager operational view: Snapchat-style 28d-click / 7d-view.
        conversion_result = await hourly._fetch_account_hours(
            context,
            client,
            access_token,
            account_id=account_id,
            request_start=window["provider_start"],
            request_end=window["provider_end"],
            action_report_time=ADS_MANAGER_DEFAULT_ACTION_REPORT_TIME,
            swipe_attribution_window=ADS_MANAGER_SWIPE_ATTRIBUTION_WINDOW,
            view_attribution_window=ADS_MANAGER_VIEW_ATTRIBUTION_WINDOW,
        )
        conversion_rows, conversion_errors = conversion_result

        impression_result = await hourly._fetch_account_hours(
            context,
            client,
            access_token,
            account_id=account_id,
            request_start=window["provider_start"],
            request_end=window["provider_end"],
            action_report_time="impression",
            swipe_attribution_window=ADS_MANAGER_SWIPE_ATTRIBUTION_WINDOW,
            view_attribution_window=ADS_MANAGER_VIEW_ATTRIBUTION_WINDOW,
        )
        impression_rows, impression_errors = impression_result

        return (
            business_rows,
            conversion_rows,
            impression_rows,
            [
                *business_errors,
                *conversion_errors,
                *impression_errors,
            ],
            _merge_hourly_coverages(
                [business_result, conversion_result, impression_result]
            ),
        )

    try:
        (
            business_rows,
            conversion_rows,
            impression_rows,
            errors,
            coverage,
        ) = await fetch_all_modes(request)
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
        (
            business_rows,
            conversion_rows,
            impression_rows,
            errors,
            coverage,
        ) = await fetch_all_modes(request)

    business_campaigns, business_accounts = _campaign_day_buckets(
        business_rows,
        timezone_name=BUSINESS_TIMEZONE,
        start_date=start_date,
        end_date=end_date,
    )
    local_conversion_campaigns, local_conversion_accounts = _campaign_day_buckets(
        conversion_rows,
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

    saved = 0
    campaign_rows_saved = 0
    for (campaign_id, date_string), bucket in sorted(business_campaigns.items()):
        await _upsert_performance(
            context,
            account=account,
            entity_type="campaign",
            external_id=campaign_id,
            date_string=date_string,
            metrics=_finalize_bucket(bucket),
            provider_start=bucket.get("provider_start"),
            provider_end=bucket.get("provider_end"),
            source_mode=hourly.ACCOUNT_REFRESH_SOURCE_MODE,
            provider_granularity=hourly.PROVIDER_GRANULARITY,
            provider_breakdown=hourly.PROVIDER_BREAKDOWN,
        )
        saved += 1
        campaign_rows_saved += 1

    for date_string, bucket in sorted(business_accounts.items()):
        await _upsert_performance(
            context,
            account=account,
            entity_type="ad_account",
            external_id=account_id,
            date_string=date_string,
            metrics=_finalize_bucket(bucket),
            provider_start=bucket.get("provider_start"),
            provider_end=bucket.get("provider_end"),
            source_mode=hourly.ACCOUNT_REFRESH_SOURCE_MODE,
            provider_granularity=hourly.PROVIDER_GRANULARITY,
            provider_breakdown=hourly.PROVIDER_BREAKDOWN,
        )
        saved += 1

    local_rows_saved = 0
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

    return {
        "provider": SNAPCHAT_PROVIDER_ID,
        "ad_account_id": account_id,
        "date_from": start_date.isoformat(),
        "date_to": end_date.isoformat(),
        "rows_saved": saved,
        "campaign_rows_saved": campaign_rows_saved,
        "account_local_rows_saved": local_rows_saved,
        "errors_count": len(errors),
        "errors": errors,
        "coverage": coverage,
        "provider_calls": context.provider_calls,
        "source_mode": ACCOUNT_LOCAL_SOURCE_MODE,
        "provider_granularity": hourly.PROVIDER_GRANULARITY,
        "provider_breakdown": hourly.PROVIDER_BREAKDOWN,
        "account_timezone": timezone_name,
        "business_timezone": BUSINESS_TIMEZONE,
        "account_local_date_from": request["account_local_from"].isoformat(),
        "account_local_date_to": request["account_local_to"].isoformat(),
        "current_hour_included": not used_completed_hour_fallback,
        "business_action_report_time": hourly.ACTION_REPORT_TIME,
        "account_local_action_report_time": ADS_MANAGER_DEFAULT_ACTION_REPORT_TIME,
        "account_local_action_report_times": list(ADS_MANAGER_SUPPORTED_ACTION_REPORT_TIMES),
        "source_only": True,
        "provider_write_reached": False,
        "campaign_write_reached": False,
        "accounting_write_reached": False,
        "qoyod_write_reached": False,
    }


def install_snapchat_account_timezone_scheduler() -> None:
    hourly.refresh_snapchat_account_hours = (
        refresh_snapchat_account_hours_with_account_days
    )


def _metric(row: dict[str, Any], key: str) -> float | None:
    metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
    return _number(metrics.get(key))


def _value(row: dict[str, Any], key: str) -> float | None:
    if key == "orders":
        direct = _number(row.get("purchases"))
        return direct if direct is not None else _metric(row, "conversion_purchases")
    if key == "sales_sar":
        return _number(row.get("purchase_value_sar"))
    if key == "sales_native":
        return _number(row.get("purchase_value_native"))
    if key == "spend_sar":
        return _number(row.get("spend_sar"))
    if key == "spend_native":
        return _number(row.get("spend_native"))
    if key in {
        "impressions",
        "swipes",
        "video_views",
        "view_content",
        "add_to_cart",
        "start_checkout",
        "add_billing",
        "paid_reach",
        "paid_frequency",
    }:
        metric_key = {
            "view_content": "conversion_view_content",
            "add_to_cart": "conversion_add_cart",
            "start_checkout": "conversion_start_checkout",
            "add_billing": "conversion_add_billing",
            "paid_reach": "uniques",
            "paid_frequency": "frequency",
        }.get(key, key)
        return _metric(row, metric_key)
    return None


def _sum_or_none(rows: list[dict[str, Any]], key: str) -> float | None:
    values = [value for row in rows if (value := _value(row, key)) is not None]
    return round(sum(values), 6) if values else None


def _latest_updated_at(rows: list[dict[str, Any]]) -> str | None:
    candidates: list[tuple[datetime, str]] = []
    for row in rows:
        raw = _text(row.get("updated_at"))
        parsed = _parse_datetime(raw)
        if raw and parsed is not None:
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            candidates.append((parsed, raw))
    return max(candidates, default=(None, None), key=lambda item: item[0])[1]


def _aggregate_rows(
    rows: list[dict[str, Any]],
    *,
    requested_days: int,
) -> dict[str, Any]:
    spend_sar = _sum_or_none(rows, "spend_sar")
    spend_native = _sum_or_none(rows, "spend_native")
    sales_sar = _sum_or_none(rows, "sales_sar")
    sales_native = _sum_or_none(rows, "sales_native")
    orders = _sum_or_none(rows, "orders")
    impressions = _sum_or_none(rows, "impressions")
    swipes = _sum_or_none(rows, "swipes")
    video_views = _sum_or_none(rows, "video_views")
    view_content = _sum_or_none(rows, "view_content")
    add_to_cart = _sum_or_none(rows, "add_to_cart")
    start_checkout = _sum_or_none(rows, "start_checkout")
    add_billing = _sum_or_none(rows, "add_billing")
    # Reach and frequency are non-additive. Daily TOTAL rows may be exposed
    # only for an exact one-day request; multi-day windows require a fresh
    # provider TOTAL for that exact interval and must never sum daily values.
    exact_audience_row = requested_days == 1 and len(rows) == 1
    paid_reach = _value(rows[0], "paid_reach") if exact_audience_row else None
    frequency_values = (
        [_value(rows[0], "paid_frequency")]
        if exact_audience_row
        else []
    )
    paid_frequency = (
        round(frequency_values[0], 6)
        if frequency_values and frequency_values[0] is not None
        else None
    )
    observed_dates = sorted({_text(row.get("date")) for row in rows if _text(row.get("date"))})
    return {
        "spend_sar": spend_sar,
        "spend_native": spend_native,
        "sales_sar": sales_sar,
        "sales_native": sales_native,
        "orders": int(round(orders)) if orders is not None else None,
        "impressions": int(round(impressions)) if impressions is not None else None,
        "swipes": int(round(swipes)) if swipes is not None else None,
        "video_views": int(round(video_views)) if video_views is not None else None,
        "view_content": int(round(view_content)) if view_content is not None else None,
        "add_to_cart": int(round(add_to_cart)) if add_to_cart is not None else None,
        "start_checkout": int(round(start_checkout)) if start_checkout is not None else None,
        "add_billing": int(round(add_billing)) if add_billing is not None else None,
        "paid_reach": int(round(paid_reach)) if paid_reach is not None else None,
        "paid_frequency": paid_frequency,
        "reach_frequency_scope": (
            "exact_one_day_total"
            if requested_days == 1 and (paid_reach is not None or paid_frequency is not None)
            else "exact_total_window_required"
        ),
        "roas": _ratio(sales_sar, spend_sar),
        "cpa_sar": _ratio(spend_sar, orders),
        "cpa_native": _ratio(spend_native, orders),
        "cpc_sar": _ratio(spend_sar, swipes),
        "cpc_native": _ratio(spend_native, swipes),
        "cpm_sar": _ratio(spend_sar, impressions, 1000.0),
        "cpm_native": _ratio(spend_native, impressions, 1000.0),
        "ctr_pct": _ratio(swipes, impressions, 100.0),
        "observed_days": len(observed_dates),
        "source_rows": len(rows),
        "last_observed_at": _latest_updated_at(rows),
        "last_observed_date": observed_dates[-1] if observed_dates else None,
        "data_complete": bool(rows) and len(observed_dates) >= requested_days,
    }


async def _to_list(cursor: Any, length: int) -> list[dict[str, Any]]:
    if hasattr(cursor, "to_list"):
        return await cursor.to_list(length=length)
    return [row async for row in cursor]


def _account_public_row(account: dict[str, Any], *, now: datetime) -> dict[str, Any]:
    timezone_name = _valid_timezone_name(account.get("timezone"))
    return {
        "account_id": _text(account.get("ad_account_id")),
        "account_name": _text(account.get("display_name")) or _text(account.get("ad_account_id")),
        "currency": _text(account.get("currency")).upper() or None,
        "timezone": timezone_name,
        "local_today": account_local_today(timezone_name, now=now).isoformat(),
    }


def _order_timestamp(order: dict[str, Any]) -> datetime | None:
    for field in (
        "created_at",
        "order_created_at",
        "created_at_utc",
        "source_created_at",
        "updated_at",
    ):
        parsed = _parse_datetime(order.get(field))
        if parsed is None or parsed.tzinfo is None:
            continue
        return parsed
    return None


async def _salla_account_outcomes(
    db: Any,
    user_id: str,
    *,
    date_from: str,
    date_to: str,
    timezone_name: str,
    identities: list[dict[str, Any]],
) -> tuple[
    dict[tuple[str, str], dict[str, Any]],
    dict[str, dict[str, Any]],
    dict[str, Any],
]:
    from dashboard_v2_routes import _filtered_orders

    start = date.fromisoformat(date_from)
    end = date.fromisoformat(date_to)
    orders = await _filtered_orders(
        db,
        user_id,
        from_date=(start - timedelta(days=1)).isoformat(),
        to_date=(end + timedelta(days=1)).isoformat(),
        payment_methods=None,
        shipping_companies=None,
        include_marketing_attribution=True,
    )
    id_lookup = _unique_lookup(identities, "campaign_id")
    name_lookup = _unique_lookup(identities, "campaign_name")
    by_campaign: dict[tuple[str, str], dict[str, Any]] = defaultdict(
        lambda: {"orders": 0, "sales_sar": 0.0}
    )
    by_date: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"orders": 0, "sales_sar": 0.0}
    )
    matched_by_id = 0
    matched_by_name = 0
    unmatched = 0
    ambiguous = 0
    localized = 0
    fallback_dates = 0
    zone = _timezone(timezone_name)
    for order in orders:
        timestamp = _order_timestamp(order)
        if timestamp is not None:
            local_date = timestamp.astimezone(zone).date().isoformat()
            localized += 1
        else:
            local_date = _text(order.get("order_date"))[:10]
            fallback_dates += 1
        if not local_date or local_date < date_from or local_date > date_to:
            continue
        key, kind = _match_order_campaign(
            order,
            id_lookup=id_lookup,
            name_lookup=name_lookup,
        )
        if key is None:
            if kind.startswith("ambiguous"):
                ambiguous += 1
            else:
                unmatched += 1
            continue
        amount = _number(order.get("total_amount") or order.get("total")) or 0.0
        by_campaign[key]["orders"] += 1
        by_campaign[key]["sales_sar"] += amount
        by_date[local_date]["orders"] += 1
        by_date[local_date]["sales_sar"] += amount
        if kind == "campaign_id":
            matched_by_id += 1
        elif kind == "campaign_name":
            matched_by_name += 1
    for container in (by_campaign, by_date):
        for value in container.values():
            value["sales_sar"] = round(float(value["sales_sar"]), 2)
    coverage = {
        "eligible_salla_orders_in_padded_window": len(orders),
        "matched_orders": matched_by_id + matched_by_name,
        "matched_by_campaign_id": matched_by_id,
        "matched_by_campaign_name": matched_by_name,
        "ambiguous_orders": ambiguous,
        "unattributed_orders_excluded_from_account": unmatched,
        "timestamp_localized_orders": localized,
        "fallback_order_date_orders": fallback_dates,
        "campaign_rows_exact_match_only": True,
        "account_totals_exact_match_only": True,
        "date_timezone": timezone_name,
    }
    return dict(by_campaign), dict(by_date), coverage


async def build_account_timezone_campaign_report(
    db: Any,
    user_id: str,
    *,
    account_id: str | None,
    from_date: str | None,
    to_date: str | None,
    campaign_query: str | None,
    page: int,
    limit: int,
    result_source: str,
    action_report_time: str = ADS_MANAGER_DEFAULT_ACTION_REPORT_TIME,
    active_campaigns_only: bool = False,
    now: Callable[[], datetime] = _utcnow,
) -> dict[str, Any]:
    current = _aware_now(now())
    action_report_time = normalize_ads_manager_action_report_time(action_report_time)
    selected_accounts = await _load_selected_accounts(db, user_id)
    if not selected_accounts:
        raise SnapchatNativeSyncError(
            "snapchat_accounts_not_selected",
            "لا توجد حسابات Snapchat محددة داخل ميزان.",
            status_code=409,
        )
    public_accounts = [
        _account_public_row(account, now=current)
        for account in selected_accounts
    ]
    requested_id = _text(account_id)
    selected = next(
        (
            account
            for account in selected_accounts
            if _text(account.get("ad_account_id")) == requested_id
        ),
        None,
    ) if requested_id else selected_accounts[0]
    if selected is None:
        raise SnapchatNativeSyncError(
            "snapchat_account_not_selected",
            "الحساب الإعلاني المطلوب غير محدد داخل ميزان.",
            status_code=404,
        )
    selected_meta = _account_public_row(selected, now=current)
    selected_id = selected_meta["account_id"]
    timezone_name = selected_meta["timezone"]
    dates = resolve_account_report_dates(
        from_date,
        to_date,
        timezone_name=timezone_name,
        now=current,
    )
    date_query = {"$gte": dates[0].isoformat(), "$lte": dates[-1].isoformat()}
    performance_cursor = _collection(
        db, SNAPCHAT_ACCOUNT_LOCAL_PERFORMANCE_COLLECTION
    ).find(
        {
            "user_id": user_id,
            "provider": SNAPCHAT_PROVIDER_ID,
            "ad_account_id": selected_id,
            "entity_type": {"$in": ["ad_account", "campaign"]},
            "date": date_query,
            "date_timezone": timezone_name,
            "source_mode": account_local_source_mode(action_report_time),
            "action_report_time": action_report_time,
        },
        {"_id": 0},
    )
    performance_rows = await _to_list(performance_cursor, MAX_REPORT_ROWS)
    row_limit_reached = len(performance_rows) >= MAX_REPORT_ROWS
    account_rows = [row for row in performance_rows if row.get("entity_type") == "ad_account"]
    campaign_rows = [row for row in performance_rows if row.get("entity_type") == "campaign"]
    summary_rows = account_rows or campaign_rows

    entity_cursor = _collection(db, SNAPCHAT_ENTITY_COLLECTION).find(
        {
            "user_id": user_id,
            "provider": SNAPCHAT_PROVIDER_ID,
            "ad_account_id": selected_id,
            "entity_type": "campaign",
        },
        {"_id": 0},
    )
    entity_rows = await _to_list(entity_cursor, MAX_ENTITY_ROWS)
    entity_limit_reached = len(entity_rows) >= MAX_ENTITY_ROWS
    entity_by_id = {
        _text(row.get("external_id")): row
        for row in entity_rows
        if _text(row.get("external_id"))
    }

    requested_days = len(dates)
    platform_totals = _aggregate_rows(summary_rows, requested_days=requested_days)
    daily_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in summary_rows:
        daily_groups[_text(row.get("date"))].append(row)
    daily = [
        {
            "date": day.isoformat(),
            **_aggregate_rows(
                daily_groups.get(day.isoformat(), []),
                requested_days=1,
            ),
        }
        for day in dates
    ]

    campaign_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in campaign_rows:
        campaign_id_value = _text(row.get("campaign_id") or row.get("external_id"))
        if campaign_id_value:
            campaign_groups[campaign_id_value].append(row)
    campaigns: list[dict[str, Any]] = []
    identities: list[dict[str, Any]] = []
    identity_matches = 0
    for campaign_id_value, rows in campaign_groups.items():
        entity = entity_by_id.get(campaign_id_value, {})
        if entity:
            identity_matches += 1
        campaign_name = _text(entity.get("display_name")) or campaign_id_value
        identities.append({
            "account_id": selected_id,
            "campaign_id": campaign_id_value,
            "campaign_name": campaign_name,
        })
        campaigns.append({
            "account_id": selected_id,
            "account_name": selected_meta["account_name"],
            "campaign_id": campaign_id_value,
            "campaign_name": campaign_name,
            "status": entity.get("status") or "unknown",
            "delivery_status": entity.get("delivery_status"),
            "objective": entity.get("objective"),
            "start_time": entity.get("start_time"),
            "end_time": entity.get("end_time"),
            "budget": {
                "currency": selected_meta.get("currency"),
                "daily_native": (
                    round(float(entity["daily_budget_micro"]) / 1_000_000, 6)
                    if _number(entity.get("daily_budget_micro")) is not None
                    else None
                ),
                "lifetime_native": (
                    round(float(entity["lifetime_spend_cap_micro"]) / 1_000_000, 6)
                    if _number(entity.get("lifetime_spend_cap_micro")) is not None
                    else None
                ),
            },
            **_aggregate_rows(rows, requested_days=requested_days),
        })

    from ads_manager.account_cost_settings import list_account_cost_settings
    settings_payload = await list_account_cost_settings(db, user_id)
    setting = next(
        (
            item for item in settings_payload.get("items") or []
            if _text(item.get("external_account_id")) == selected_id
        ),
        None,
    )
    currency, rate = _effective_currency(selected, setting)
    salla_by_campaign, salla_by_date, coverage = await _salla_account_outcomes(
        db,
        user_id,
        date_from=dates[0].isoformat(),
        date_to=dates[-1].isoformat(),
        timezone_name=timezone_name,
        identities=identities,
    )

    def select_metrics(
        platform_value: dict[str, Any],
        salla_value: dict[str, Any],
    ) -> dict[str, Any]:
        outcomes = salla_value if result_source == RESULT_SOURCE_SALLA else platform_value
        orders = int(outcomes.get("orders") or 0)
        sales_sar = _number(outcomes.get("sales_sar")) or 0.0
        sales_native = (
            round(sales_sar / rate, 6)
            if result_source == RESULT_SOURCE_SALLA and rate > 0
            else _number(outcomes.get("sales_native"))
        )
        spend_sar = _number(platform_value.get("spend_sar"))
        spend_native = _number(platform_value.get("spend_native"))
        return {
            "orders": orders,
            "sales_sar": round(sales_sar, 2),
            "sales_native": sales_native,
            "roas": _ratio(sales_sar, spend_sar),
            "cpa_sar": _ratio(spend_sar, orders),
            "cpa_native": _ratio(spend_native, orders),
        }

    for campaign in campaigns:
        key = (selected_id, campaign["campaign_id"])
        platform_value = dict(campaign)
        salla_value = salla_by_campaign.get(key, {"orders": 0, "sales_sar": 0.0})
        campaign.update(select_metrics(platform_value, salla_value))
        campaign.update({
            "result_source": result_source,
            "display_currency": currency,
            "exchange_rate_to_sar": round(rate, 6),
            "platform_results": {
                "orders": platform_value.get("orders"),
                "sales_sar": platform_value.get("sales_sar"),
                "sales_native": platform_value.get("sales_native"),
            },
            "salla_results": salla_value,
        })

    for row in daily:
        if result_source == RESULT_SOURCE_SALLA:
            row.update(select_metrics(
                row,
                salla_by_date.get(row["date"], {"orders": 0, "sales_sar": 0.0}),
            ))
        row["result_source"] = result_source

    exact_salla_total = {
        "orders": sum(int(value.get("orders") or 0) for value in salla_by_campaign.values()),
        "sales_sar": round(sum(float(value.get("sales_sar") or 0) for value in salla_by_campaign.values()), 2),
    }
    totals = dict(platform_totals)
    totals.update(select_metrics(platform_totals, exact_salla_total))
    totals["result_source"] = result_source

    for campaign in campaigns:
        campaign["campaign_active"] = is_active_provider_status(campaign.get("status"))
    if active_campaigns_only:
        campaigns = [campaign for campaign in campaigns if campaign.get("campaign_active") is True]

    campaigns_query = _text(campaign_query).casefold()[:120]
    if campaigns_query:
        campaigns = [
            item for item in campaigns
            if campaigns_query in " ".join([
                _text(item.get("campaign_name")),
                _text(item.get("campaign_id")),
                _text(item.get("account_name")),
            ]).casefold()
        ]
    campaigns.sort(key=lambda item: (
        -(float(item.get("spend_sar") or 0)),
        _text(item.get("campaign_name")).casefold(),
    ))
    total_campaigns = len(campaigns)
    pages = (total_campaigns + limit - 1) // limit if total_campaigns else 0
    offset = (page - 1) * limit
    campaigns = campaigns[offset:offset + limit]

    account_output = {
        **selected_meta,
        **totals,
        "display_currency": currency,
        "exchange_rate_to_sar": round(rate, 6),
        "result_source": result_source,
    }
    report_ready = bool(summary_rows)
    campaign_details_ready = bool(campaign_rows) or not (
        (_number(platform_totals.get("spend_sar")) or 0) > 0
    )
    insights: list[dict[str, Any]] = []
    if not report_ready:
        insights.append({
            "code": "snapchat_account_timezone_rows_pending",
            "severity": "warning",
            "title": "بيانات توقيت الحساب لم تكتمل بعد",
            "detail": "ستظهر بعد دورة المزامنة الخمسية التالية للحساب المحدد.",
        })
    elif not campaign_details_ready:
        insights.append({
            "code": "snapchat_campaign_rows_missing_for_account_day",
            "severity": "warning",
            "title": "إجمالي الحساب متوفر لكن تفاصيل الحملات غير مكتملة",
            "detail": "الصرف موجود على مستوى الحساب، وتنتظر الصفحة استكمال صفوف الحملات لنفس توقيت الحساب.",
        })
    elif int(platform_totals.get("observed_days") or 0) < requested_days:
        insights.append({
            "code": "snapchat_account_timezone_partial_dates",
            "severity": "warning",
            "title": "تغطية الفترة غير مكتملة",
            "detail": f"تتوفر بيانات {int(platform_totals.get('observed_days') or 0)} يوم من أصل {requested_days} يوم.",
        })
    if row_limit_reached or entity_limit_reached:
        insights.append({
            "code": "snapchat_account_timezone_row_limit",
            "severity": "critical",
            "title": "بلغ التقرير حد القراءة",
            "detail": "قلّص الفترة ثم أعد تحميل التقرير.",
        })

    return {
        "provider": SNAPCHAT_PROVIDER_ID,
        "date_from": dates[0].isoformat(),
        "date_to": dates[-1].isoformat(),
        "business_timezone": timezone_name,
        "account_timezone": timezone_name,
        "selected_account_id": selected_id,
        "selected_account": selected_meta,
        "available_accounts": public_accounts,
        "result_source": result_source,
        "action_report_time": action_report_time,
        "supported_action_report_times": list(ADS_MANAGER_SUPPORTED_ACTION_REPORT_TIMES),
        "supported_result_sources": list(SUPPORTED_RESULT_SOURCES),
        "active_campaigns_only": bool(active_campaigns_only),
        "totals": totals,
        "daily": daily,
        "accounts": [account_output],
        "campaigns": campaigns,
        "campaign_pagination": {
            "page": page,
            "limit": limit,
            "total": total_campaigns,
            "pages": pages,
        },
        "source": {
            "performance_collection": SNAPCHAT_ACCOUNT_LOCAL_PERFORMANCE_COLLECTION,
            "riyadh_performance_collection": SNAPCHAT_PERFORMANCE_COLLECTION,
            "entity_collection": SNAPCHAT_ENTITY_COLLECTION,
            "attribution_model": ATTRIBUTION_MODEL,
            "report_scope": "single_selected_account_native_timezone",
            "selected_account_count": 1,
            "performance_rows": len(performance_rows),
            "account_rows": len(account_rows),
            "campaign_rows": len(campaign_rows),
            "identity_matches": identity_matches,
            "identity_coverage_pct": (
                round(identity_matches / len(campaign_groups) * 100, 2)
                if campaign_groups else None
            ),
            "row_limit_reached": row_limit_reached,
            "entity_limit_reached": entity_limit_reached,
            "result_source": result_source,
            "spend_source": "snapchat_selected_account_native_timezone",
            "commercial_results_source": (
                "unified_orders:salla_exact_account_campaign_match"
                if result_source == RESULT_SOURCE_SALLA
                else f"snapchat_ads_manager_{action_report_time}_reporting"
            ),
            "account_local_action_report_time": action_report_time,
            "salla_attribution": coverage,
        },
        "ai_readiness": {
            "report_ready": report_ready,
            "campaign_identity_ready": bool(campaign_groups) and identity_matches == len(campaign_groups),
            "campaign_details_ready": campaign_details_ready,
            "spend_ready": totals.get("spend_sar") is not None,
            "orders_ready": totals.get("orders") is not None,
            "sales_ready": totals.get("sales_sar") is not None,
            "ratios_ready": any(totals.get(key) is not None for key in ("roas", "cpa_sar", "cpc_sar", "cpm_sar", "ctr_pct")),
            "funnel_ready": any(
                totals.get(key) is not None
                for key in ("view_content", "add_to_cart", "start_checkout", "add_billing")
            ),
            "ai_analysis_ready": (
                report_ready
                and campaign_details_ready
                and action_report_time == ADS_MANAGER_DEFAULT_ACTION_REPORT_TIME
            ),
            "ai_action_report_time": ADS_MANAGER_DEFAULT_ACTION_REPORT_TIME,
            "selected_action_report_time": action_report_time,
            "required_lifecycle": [
                "proposal", "preview", "approval", "execution",
                "verification", "audit", "rollback",
            ],
        },
        "insights": insights,
        "policy": {
            "mode": "observe_only",
            "mutations_allowed": False,
            "dashboard_accounting_timezone_unchanged": BUSINESS_TIMEZONE,
            "default_action_report_time": ADS_MANAGER_DEFAULT_ACTION_REPORT_TIME,
            "impression_time_comparison_only": True,
        },
        "source_only": True,
        "provider_read_reached": False,
        "provider_write_reached": False,
        "campaign_write_reached": False,
        "accounting_write_reached": False,
        "qoyod_write_reached": False,
    }


def attach_snapchat_account_timezone_campaign_routes(
    router: APIRouter,
    db: Any,
    current_user: Callable,
    require_owner: Callable[[Any], dict],
) -> None:
    @router.get(
        f"/{SNAPCHAT_PROVIDER_ID}/campaign-report",
        name="get_snapchat_campaign_report",
    )
    async def campaign_report(
        account_id: str | None = Query(default=None, max_length=120),
        from_date: str | None = Query(default=None),
        to_date: str | None = Query(default=None),
        campaign_query: str | None = Query(default=None, max_length=120),
        page: int = Query(default=1, ge=1),
        limit: int = Query(default=25, ge=10, le=100),
        result_source: str = Query(default=RESULT_SOURCE_SALLA, pattern="^(salla|platform)$"),
        action_report_time: str = Query(default=ADS_MANAGER_DEFAULT_ACTION_REPORT_TIME, pattern="^(conversion|impression)$"),
        active_campaigns_only: bool = Query(default=True),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        owner = require_owner(user)
        try:
            return await build_account_timezone_campaign_report(
                db,
                str(owner["id"]),
                account_id=account_id,
                from_date=from_date,
                to_date=to_date,
                campaign_query=campaign_query,
                page=page,
                limit=limit,
                result_source=result_source,
                action_report_time=action_report_time,
                active_campaigns_only=active_campaigns_only,
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
                    "campaign_write_reached": False,
                    "accounting_write_reached": False,
                    "qoyod_write_reached": False,
                },
            ) from exc


__all__ = [
    "ACCOUNT_LOCAL_SOURCE_MODE",
    "SNAPCHAT_ACCOUNT_LOCAL_PERFORMANCE_COLLECTION",
    "account_local_today",
    "attach_snapchat_account_timezone_campaign_routes",
    "build_account_timezone_campaign_report",
    "install_snapchat_account_timezone_scheduler",
    "refresh_snapchat_account_hours_with_account_days",
    "resolve_account_report_dates",
]
