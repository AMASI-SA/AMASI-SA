"""Account-local hourly Snapchat chart facts for Ads Manager.

The existing five-minute refresh already requests Snapchat with HOUR
Granularity. This module captures those read-only rows once, persists an
account-level hourly projection in the account's native timezone, and adds a
24-hour series to the campaign report when exactly one local date is selected.
Dashboard and accounting continue reading their Riyadh-day collections.
"""
from __future__ import annotations

from collections import defaultdict
from contextvars import ContextVar
from datetime import date, datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

from . import snapchat_account_hourly_refresh as hourly
from . import snapchat_account_timezone_manager as account_report
from .snapchat_freshness_impl_v6 import (
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
    SNAPCHAT_ENTITY_COLLECTION,
    SNAPCHAT_PROVIDER_ID,
    SnapchatSyncContext,
    _as_number,
    _collection,
    _parse_datetime,
    _timezone,
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
)

SNAPCHAT_ACCOUNT_LOCAL_HOURLY_COLLECTION = (
    "mezan_snapchat_performance_account_hour_v2"
)


def account_local_hourly_source_mode(action_report_time: Any) -> str:
    return (
        f"{ads_manager_source_mode(action_report_time)}:"
        "account_hour_v3"
    )


ACCOUNT_LOCAL_HOURLY_SOURCE_MODE = account_local_hourly_source_mode(
    ADS_MANAGER_DEFAULT_ACTION_REPORT_TIME
)
MAX_HOURLY_REPORT_ROWS = 2_000

RefreshCallable = Callable[..., Awaitable[dict[str, Any]]]
FetchCallable = Callable[..., Awaitable[hourly.AccountHourFetchResult]]
ReportCallable = Callable[..., Awaitable[dict[str, Any]]]

_CAPTURE_CONTEXT: ContextVar[dict[str, Any] | None] = ContextVar(
    "mezan_snapchat_account_hourly_capture",
    default=None,
)


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


async def _to_list(cursor: Any, length: int) -> list[dict[str, Any]]:
    if hasattr(cursor, "to_list"):
        return await cursor.to_list(length=length)
    return [row async for row in cursor]


async def _ensure_indexes(db: Any) -> None:
    collection = _collection(db, SNAPCHAT_ACCOUNT_LOCAL_HOURLY_COLLECTION)
    await collection.create_index(
        [
            ("user_id", 1),
            ("ad_account_id", 1),
            ("hour_start_utc", 1),
            ("attribution_model", 1),
            ("action_report_time", 1),
        ],
        unique=True,
        name="mezan_snapchat_account_hour_v2_identity_unique",
    )
    await collection.create_index(
        [
            ("user_id", 1),
            ("ad_account_id", 1),
            ("date", -1),
            ("hour_index", 1),
            ("action_report_time", 1),
        ],
        name="mezan_snapchat_account_hour_v2_date_hour",
    )


def aggregate_account_rows_by_local_hour(
    rows: list[dict[str, Any]],
    *,
    timezone_name: str,
    start_date: date,
    end_date: date,
) -> dict[str, dict[str, Any]]:
    """Aggregate campaign-breakdown rows into one account bucket per hour."""
    zone = _timezone(timezone_name)
    buckets: dict[str, dict[str, Any]] = {}
    for row in rows:
        point = _parse_datetime(row.get("start_time"))
        if point is None:
            continue
        if point.tzinfo is None:
            point = point.replace(tzinfo=timezone.utc)
        local_point = point.astimezone(zone).replace(minute=0, second=0, microsecond=0)
        local_date = local_point.date()
        if local_date < start_date or local_date > end_date:
            continue
        utc_hour = point.astimezone(timezone.utc).replace(
            minute=0,
            second=0,
            microsecond=0,
        )
        key = utc_hour.isoformat(timespec="seconds")
        bucket = buckets.setdefault(key, _new_bucket())
        bucket["hour_start_utc"] = key
        bucket["hour_start_local"] = local_point.isoformat(timespec="seconds")
        bucket["date"] = local_date.isoformat()
        bucket["hour_index"] = local_point.hour
        metrics = {
            field: _as_number((row.get("metrics") or {}).get(field))
            for field in STAT_FIELDS
        }
        _add_to_bucket(
            bucket,
            metrics,
            provider_start=row.get("start_time"),
            provider_end=row.get("end_time"),
        )
    return buckets


async def _upsert_hour(
    context: SnapchatSyncContext,
    *,
    account: dict[str, Any],
    timezone_name: str,
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
        "entity_type": "ad_account",
        "external_id": account["ad_account_id"],
        "date": bucket["date"],
        "hour_index": int(bucket["hour_index"]),
        "hour_start_utc": bucket["hour_start_utc"],
        "hour_start_local": bucket["hour_start_local"],
        "date_timezone": timezone_name,
        "account_timezone": timezone_name,
        "business_timezone": BUSINESS_TIMEZONE,
        "currency": currency or None,
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
        "source_mode": account_local_hourly_source_mode(
            action_report_time
        ),
        "provider_granularity": "HOUR",
        "provider_breakdown": "campaign",
        "stored_granularity": "ACCOUNT_LOCAL_HOUR",
        "report_scope": "snapchat_ads_manager_account_timezone_hourly",
        "accounting_eligible": False,
        "provider_window_start": bucket.get("provider_start"),
        "provider_window_end": bucket.get("provider_end"),
        "updated_at": now_iso,
    }
    await _collection(
        context.db,
        SNAPCHAT_ACCOUNT_LOCAL_HOURLY_COLLECTION,
    ).update_one(
        {
            "user_id": context.user_id,
            "ad_account_id": account["ad_account_id"],
            "hour_start_utc": bucket["hour_start_utc"],
            "attribution_model": ATTRIBUTION_MODEL,
            "action_report_time": action_report_time,
        },
        {"$set": document, "$setOnInsert": {"created_at": now_iso}},
        upsert=True,
    )


async def persist_account_local_hours(
    context: SnapchatSyncContext,
    *,
    account: dict[str, Any],
    rows: list[dict[str, Any]],
    start_date: date,
    end_date: date,
    action_report_time: str,
) -> int:
    timezone_name = account_report._valid_timezone_name(account.get("timezone"))
    action_report_time = normalize_ads_manager_action_report_time(
        action_report_time
    )
    await _ensure_indexes(context.db)
    buckets = aggregate_account_rows_by_local_hour(
        rows,
        timezone_name=timezone_name,
        start_date=start_date,
        end_date=end_date,
    )
    for bucket in buckets.values():
        await _upsert_hour(
            context,
            account=account,
            timezone_name=timezone_name,
            bucket=bucket,
            action_report_time=action_report_time,
        )
    return len(buckets)


def _date_arg(args: tuple[Any, ...], kwargs: dict[str, Any], index: int, name: str) -> date | None:
    value = kwargs.get(name)
    if not isinstance(value, date) and len(args) > index:
        value = args[index]
    return value if isinstance(value, date) else None


def install_snapchat_account_hourly_capture() -> None:
    current_fetch: FetchCallable = hourly._fetch_account_hours
    if not getattr(current_fetch, "_mezan_account_hourly_capture", False):
        async def fetch_wrapped(
            context: SnapchatSyncContext,
            *args: Any,
            **kwargs: Any,
        ) -> hourly.AccountHourFetchResult:
            result = hourly.require_account_hour_fetch_result(
                await current_fetch(context, *args, **kwargs),
                result_name="hourly_capture_source",
            )
            rows = result.rows
            capture = _CAPTURE_CONTEXT.get()
            captured_action_report_time = str(
                kwargs.get("action_report_time") or ""
            ).strip().lower()
            if (
                capture
                and rows
                and captured_action_report_time
                in ADS_MANAGER_SUPPORTED_ACTION_REPORT_TIMES
                and kwargs.get("swipe_attribution_window")
                == ADS_MANAGER_SWIPE_ATTRIBUTION_WINDOW
                and kwargs.get("view_attribution_window")
                == ADS_MANAGER_VIEW_ATTRIBUTION_WINDOW
            ):
                try:
                    saved = await persist_account_local_hours(
                        context,
                        account=capture["account"],
                        rows=rows,
                        start_date=capture["start_date"],
                        end_date=capture["end_date"],
                        action_report_time=captured_action_report_time,
                    )
                    capture["rows_saved"] = (
                        int(capture.get("rows_saved") or 0)
                        + saved
                    )
                except Exception as exc:  # keep the established daily refresh alive
                    capture["capture_error"] = type(exc).__name__
            return result

        fetch_wrapped._mezan_account_hourly_capture = True  # type: ignore[attr-defined]
        fetch_wrapped._mezan_account_hourly_base = current_fetch  # type: ignore[attr-defined]
        hourly._fetch_account_hours = fetch_wrapped

    current_refresh: RefreshCallable = hourly.refresh_snapchat_account_hours
    if getattr(current_refresh, "_mezan_account_hourly_context", False):
        return

    async def refresh_wrapped(
        context: SnapchatSyncContext,
        client: Any,
        access_token: str,
        account: dict[str, Any],
        *args: Any,
        **kwargs: Any,
    ) -> dict[str, Any]:
        start_date = _date_arg(args, kwargs, 0, "start_date")
        end_date = _date_arg(args, kwargs, 1, "end_date")
        if start_date is None or end_date is None:
            return await current_refresh(
                context,
                client,
                access_token,
                account,
                *args,
                **kwargs,
            )
        capture = {
            "account": account,
            "start_date": start_date - timedelta(days=1),
            "end_date": end_date + timedelta(days=1),
            "rows_saved": 0,
        }
        token = _CAPTURE_CONTEXT.set(capture)
        try:
            output = dict(await current_refresh(
                context,
                client,
                access_token,
                account,
                *args,
                **kwargs,
            ) or {})
            output["account_local_hour_rows_saved"] = int(capture["rows_saved"])
            if capture.get("capture_error"):
                output["account_local_hour_capture_error"] = capture["capture_error"]
            return output
        finally:
            _CAPTURE_CONTEXT.reset(token)

    refresh_wrapped._mezan_account_hourly_context = True  # type: ignore[attr-defined]
    refresh_wrapped._mezan_account_hourly_base = current_refresh  # type: ignore[attr-defined]
    hourly.refresh_snapchat_account_hours = refresh_wrapped


async def _salla_hourly_outcomes(
    db: Any,
    user_id: str,
    *,
    account_id: str,
    date_string: str,
    timezone_name: str,
) -> tuple[dict[int, dict[str, Any]], dict[str, Any]]:
    from dashboard_v2_routes import _filtered_orders

    entity_cursor = _collection(db, SNAPCHAT_ENTITY_COLLECTION).find(
        {
            "user_id": user_id,
            "provider": SNAPCHAT_PROVIDER_ID,
            "ad_account_id": account_id,
            "entity_type": "campaign",
        },
        {"_id": 0, "external_id": 1, "display_name": 1},
    )
    entities = await _to_list(entity_cursor, account_report.MAX_ENTITY_ROWS)
    identities = [
        {
            "account_id": account_id,
            "campaign_id": _text(row.get("external_id")),
            "campaign_name": _text(row.get("display_name")),
        }
        for row in entities
        if _text(row.get("external_id"))
    ]
    id_lookup = account_report._unique_lookup(identities, "campaign_id")
    name_lookup = account_report._unique_lookup(identities, "campaign_name")
    selected_date = date.fromisoformat(date_string)
    orders = await _filtered_orders(
        db,
        user_id,
        from_date=(selected_date - timedelta(days=1)).isoformat(),
        to_date=(selected_date + timedelta(days=1)).isoformat(),
        payment_methods=None,
        shipping_companies=None,
        include_marketing_attribution=True,
    )
    zone = _timezone(timezone_name)
    by_hour: dict[int, dict[str, Any]] = defaultdict(
        lambda: {"orders": 0, "sales_sar": 0.0}
    )
    matched = 0
    fallback_without_hour = 0
    for order in orders:
        timestamp = account_report._order_timestamp(order)
        if timestamp is None:
            fallback_without_hour += 1
            continue
        local = timestamp.astimezone(zone)
        if local.date().isoformat() != date_string:
            continue
        key, _kind = account_report._match_order_campaign(
            order,
            id_lookup=id_lookup,
            name_lookup=name_lookup,
        )
        if key is None:
            continue
        amount = _number(order.get("total_amount") or order.get("total")) or 0.0
        by_hour[local.hour]["orders"] += 1
        by_hour[local.hour]["sales_sar"] += amount
        matched += 1
    for value in by_hour.values():
        value["sales_sar"] = round(float(value["sales_sar"]), 2)
    return dict(by_hour), {
        "matched_hourly_orders": matched,
        "orders_without_timestamp_excluded_from_hourly": fallback_without_hour,
        "campaign_rows_exact_match_only": True,
        "date_timezone": timezone_name,
    }


async def build_hourly_chart_series(
    db: Any,
    user_id: str,
    *,
    account_id: str,
    date_string: str,
    timezone_name: str,
    result_source: str,
    action_report_time: str = ADS_MANAGER_DEFAULT_ACTION_REPORT_TIME,
    now: datetime | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    action_report_time = normalize_ads_manager_action_report_time(
        action_report_time
    )
    cursor = _collection(db, SNAPCHAT_ACCOUNT_LOCAL_HOURLY_COLLECTION).find(
        {
            "user_id": user_id,
            "provider": SNAPCHAT_PROVIDER_ID,
            "ad_account_id": account_id,
            "date": date_string,
            "date_timezone": timezone_name,
            "source_mode": account_local_hourly_source_mode(
                action_report_time
            ),
            "action_report_time": action_report_time,
        },
        {"_id": 0},
    )
    rows = await _to_list(cursor, MAX_HOURLY_REPORT_ROWS)
    by_hour: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        hour_index = int(_number(row.get("hour_index")) or 0)
        if 0 <= hour_index <= 23:
            by_hour[hour_index].append(row)

    salla_by_hour: dict[int, dict[str, Any]] = {}
    salla_coverage: dict[str, Any] = {}
    if result_source == account_report.RESULT_SOURCE_SALLA:
        salla_by_hour, salla_coverage = await _salla_hourly_outcomes(
            db,
            user_id,
            account_id=account_id,
            date_string=date_string,
            timezone_name=timezone_name,
        )

    zone = _timezone(timezone_name)
    current_local = (now or datetime.now(timezone.utc)).astimezone(zone)
    selected_date = date.fromisoformat(date_string)
    is_today = selected_date == current_local.date()
    series: list[dict[str, Any]] = []
    for hour_index in range(24):
        platform = account_report._aggregate_rows(
            by_hour.get(hour_index, []),
            requested_days=1,
        )
        if result_source == account_report.RESULT_SOURCE_SALLA:
            outcomes = salla_by_hour.get(
                hour_index,
                {"orders": 0, "sales_sar": 0.0},
            )
        else:
            outcomes = platform
        orders = int(round(_number(outcomes.get("orders")) or 0))
        sales_sar = round(_number(outcomes.get("sales_sar")) or 0.0, 2)
        spend_sar = round(_number(platform.get("spend_sar")) or 0.0, 2)
        series.append({
            "date": date_string,
            "hour_index": hour_index,
            "hour": f"{hour_index:02d}:00",
            "orders": orders,
            "sales_sar": sales_sar,
            "spend_sar": spend_sar,
            "roas": account_report._ratio(sales_sar, spend_sar),
            "cpa_sar": account_report._ratio(spend_sar, orders),
            "observed": bool(by_hour.get(hour_index)),
            "is_future": bool(is_today and hour_index > current_local.hour),
            "result_source": result_source,
        })
    return series, {
        "hourly_collection": SNAPCHAT_ACCOUNT_LOCAL_HOURLY_COLLECTION,
        "hourly_source_mode": account_local_hourly_source_mode(
            action_report_time
        ),
        "stored_granularity": "ACCOUNT_LOCAL_HOUR",
        "provider_granularity": "HOUR",
        "hourly_rows": len(rows),
        "hourly_available": bool(rows),
        "hourly_result_source": result_source,
        "hourly_action_report_time": action_report_time,
        "salla_hourly_attribution": salla_coverage,
        "accounting_eligible": False,
    }


def install_snapchat_account_hourly_report() -> None:
    current: ReportCallable = account_report.build_account_timezone_campaign_report
    if getattr(current, "_mezan_account_hourly_report", False):
        return

    async def wrapped(
        db: Any,
        user_id: str,
        *args: Any,
        **kwargs: Any,
    ) -> dict[str, Any]:
        output = dict(await current(db, user_id, *args, **kwargs) or {})
        date_from = _text(output.get("date_from"))
        date_to = _text(output.get("date_to"))
        output["hourly"] = []
        if date_from and date_from == date_to:
            series, hourly_source = await build_hourly_chart_series(
                db,
                user_id,
                account_id=_text(output.get("selected_account_id")),
                date_string=date_from,
                timezone_name=_text(output.get("account_timezone")),
                result_source=_text(output.get("result_source"))
                or account_report.RESULT_SOURCE_SALLA,
                action_report_time=_text(
                    output.get("action_report_time")
                )
                or ADS_MANAGER_DEFAULT_ACTION_REPORT_TIME,
            )
            output["hourly"] = series
            source = output.setdefault("source", {})
            if isinstance(source, dict):
                source.update(hourly_source)
        return output

    wrapped._mezan_account_hourly_report = True  # type: ignore[attr-defined]
    wrapped._mezan_account_hourly_base = current  # type: ignore[attr-defined]
    account_report.build_account_timezone_campaign_report = wrapped


def install_snapchat_account_hourly_chart() -> None:
    install_snapchat_account_hourly_capture()
    install_snapchat_account_hourly_report()


__all__ = [
    "ACCOUNT_LOCAL_HOURLY_SOURCE_MODE",
    "SNAPCHAT_ACCOUNT_LOCAL_HOURLY_COLLECTION",
    "aggregate_account_rows_by_local_hour",
    "build_hourly_chart_series",
    "install_snapchat_account_hourly_chart",
    "persist_account_local_hours",
]
