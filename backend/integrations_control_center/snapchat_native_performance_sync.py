"""Native Snapchat V2 performance ingestion with Saudi business-day totals.

Campaign detail remains provider-native DAY data for efficient historical
reports.  Account totals use HOUR data and are re-aggregated into the shared
Mezan business day (00:00–23:59 Asia/Riyadh), so both the SAR and USD Snapchat
accounts start their Dashboard day at Saudi midnight.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

import httpx

from .snapchat_native_data_common import (
    ATTRIBUTION_MODEL,
    BUSINESS_TIMEZONE,
    MAX_PAGES,
    SNAPCHAT_API_BASE,
    SNAPCHAT_NATIVE_SYNC_SOURCE_MODE,
    SNAPCHAT_PERFORMANCE_COLLECTION,
    SNAPCHAT_PROVIDER_ID,
    SnapchatNativeSyncError,
    SnapchatSyncContext,
    _as_number,
    _collection,
    _parse_datetime,
    _safe_next_url,
    _timezone,
)

STAT_FIELDS = (
    "impressions", "swipes", "spend", "video_views", "view_completion",
    "conversion_purchases", "conversion_purchases_value",
)

SOURCE_GRANULARITY = "HOUR"
CAMPAIGN_SOURCE_GRANULARITY = "DAY"
DAY_BOUNDARY_MODE = "riyadh_midnight_hourly_aggregate"
CAMPAIGN_DAY_BOUNDARY_MODE = "provider_account_day"


def _computed(metrics: dict[str, Any]) -> dict[str, float | None]:
    impressions = _as_number(metrics.get("impressions"))
    swipes = _as_number(metrics.get("swipes"))
    spend_micro = _as_number(metrics.get("spend"))
    purchases = _as_number(metrics.get("conversion_purchases"))
    value_micro = _as_number(metrics.get("conversion_purchases_value"))
    spend = float(spend_micro) / 1_000_000 if spend_micro is not None else None
    value = float(value_micro) / 1_000_000 if value_micro is not None else None
    return {
        "ctr": round(float(swipes) / float(impressions), 8)
        if impressions not in {None, 0} and swipes is not None else None,
        "cpc": round(spend / float(swipes), 6)
        if swipes not in {None, 0} and spend is not None else None,
        "cpm": round(spend * 1000 / float(impressions), 6)
        if impressions not in {None, 0} and spend is not None else None,
        "roas": round(value / spend, 6)
        if spend not in {None, 0} and value is not None else None,
        "cost_per_purchase": round(spend / float(purchases), 6)
        if purchases not in {None, 0} and spend is not None else None,
    }


def riyadh_report_window(start_date: date, end_date: date) -> tuple[datetime, datetime]:
    """Return exact Riyadh-midnight boundaries as an exclusive UTC window."""
    report_tz = _timezone(BUSINESS_TIMEZONE)
    start_local = datetime(
        start_date.year,
        start_date.month,
        start_date.day,
        tzinfo=report_tz,
    )
    exclusive_end = end_date + timedelta(days=1)
    end_local = datetime(
        exclusive_end.year,
        exclusive_end.month,
        exclusive_end.day,
        tzinfo=report_tz,
    )
    return (
        start_local.astimezone(timezone.utc),
        end_local.astimezone(timezone.utc),
    )


def _new_aggregate() -> dict[str, Any]:
    return {
        "sums": {key: 0.0 for key in STAT_FIELDS},
        "seen": {key: 0 for key in STAT_FIELDS},
        "rows": 0,
        "provider_start": None,
        "provider_end": None,
    }


def _add_metrics(
    aggregate: dict[str, Any],
    metrics: dict[str, int | float | None],
    *,
    provider_start: Any = None,
    provider_end: Any = None,
) -> None:
    aggregate["rows"] += 1
    for key, value in metrics.items():
        if value is not None:
            aggregate["sums"][key] += float(value)
            aggregate["seen"][key] += 1
    start_text = str(provider_start or "").strip()
    end_text = str(provider_end or "").strip()
    if start_text and (
        aggregate["provider_start"] is None
        or start_text < aggregate["provider_start"]
    ):
        aggregate["provider_start"] = start_text
    if end_text and (
        aggregate["provider_end"] is None
        or end_text > aggregate["provider_end"]
    ):
        aggregate["provider_end"] = end_text


def _final_metrics(aggregate: dict[str, Any]) -> dict[str, int | float | None]:
    row_count = int(aggregate.get("rows") or 0)
    metrics: dict[str, int | float | None] = {}
    for key in STAT_FIELDS:
        if row_count and aggregate["seen"][key] == row_count:
            value = aggregate["sums"][key]
            metrics[key] = int(value) if float(value).is_integer() else value
        else:
            metrics[key] = None
    return metrics


def aggregate_hourly_rows_by_riyadh_day(
    rows: list[dict[str, Any]],
    *,
    start_date: date,
    end_date: date,
) -> tuple[dict[tuple[str, str], dict[str, Any]], dict[str, dict[str, Any]]]:
    """Aggregate provider hourly buckets into entity/account Riyadh days."""
    report_tz = _timezone(BUSINESS_TIMEZONE)
    entity_daily: dict[tuple[str, str], dict[str, Any]] = {}

    for row in rows:
        point_start = _parse_datetime(row.get("start_time"))
        if point_start is None:
            continue
        report_date = point_start.astimezone(report_tz).date()
        if report_date < start_date or report_date > end_date:
            continue
        external_id = str(row.get("external_id") or "").strip()
        if not external_id:
            continue
        date_string = report_date.isoformat()
        metrics = {
            key: _as_number((row.get("metrics") or {}).get(key))
            for key in STAT_FIELDS
        }
        aggregate = entity_daily.setdefault(
            (external_id, date_string),
            _new_aggregate(),
        )
        _add_metrics(
            aggregate,
            metrics,
            provider_start=row.get("start_time"),
            provider_end=row.get("end_time"),
        )

    account_daily: dict[str, dict[str, Any]] = {}
    for (_external_id, date_string), aggregate in entity_daily.items():
        account_aggregate = account_daily.setdefault(
            date_string,
            _new_aggregate(),
        )
        _add_metrics(
            account_aggregate,
            _final_metrics(aggregate),
            provider_start=aggregate.get("provider_start"),
            provider_end=aggregate.get("provider_end"),
        )

    return entity_daily, account_daily


async def _upsert_performance(
    context: SnapchatSyncContext,
    *,
    account: dict[str, Any],
    entity_type: str,
    external_id: str,
    date_string: str,
    metrics: dict[str, Any],
    provider_start: Any = None,
    provider_end: Any = None,
    report_timezone: str = BUSINESS_TIMEZONE,
    day_boundary_mode: str = DAY_BOUNDARY_MODE,
    source_granularity: str = SOURCE_GRANULARITY,
) -> None:
    currency = str(account.get("currency") or "").strip().upper()
    spend_micro = _as_number(metrics.get("spend"))
    value_micro = _as_number(metrics.get("conversion_purchases_value"))
    purchases = _as_number(metrics.get("conversion_purchases"))
    impressions = _as_number(metrics.get("impressions"))
    clicks = _as_number(metrics.get("swipes"))
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
        "mezan_integration_account_id": account.get("mezan_integration_account_id"),
        "entity_type": entity_type,
        "external_id": external_id,
        "date": date_string,
        "currency": currency or None,
        "account_timezone": str(account.get("timezone") or "UTC"),
        "report_timezone": report_timezone,
        "day_boundary_mode": day_boundary_mode,
        "source_granularity": source_granularity,
        "attribution_model": ATTRIBUTION_MODEL,
        "metrics": metrics,
        "spend_native": spend_native,
        "spend_sar": await context.to_sar(spend_native, currency),
        "purchases": purchases,
        "impressions": impressions,
        "clicks": clicks,
        "purchase_value_native": value_native,
        "purchase_value_sar": await context.to_sar(value_native, currency),
        "computed": _computed(metrics),
        "source_mode": SNAPCHAT_NATIVE_SYNC_SOURCE_MODE,
        "accounting_eligible": False,
        "provider_window_start": provider_start,
        "provider_window_end": provider_end,
        "updated_at": now_iso,
    }
    if entity_type == "campaign":
        document["campaign_id"] = external_id
    await _collection(context.db, SNAPCHAT_PERFORMANCE_COLLECTION).update_one(
        {
            "user_id": context.user_id,
            "ad_account_id": account["ad_account_id"],
            "entity_type": entity_type,
            "external_id": external_id,
            "date": date_string,
            "attribution_model": ATTRIBUTION_MODEL,
        },
        {"$set": document, "$setOnInsert": {"created_at": now_iso}},
        upsert=True,
    )


async def _fetch_timeseries_rows(
    context: SnapchatSyncContext,
    client: httpx.AsyncClient,
    access_token: str,
    *,
    account_id: str,
    params: dict[str, Any],
    breakdown_key: str | None,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
    }
    url = f"{SNAPCHAT_API_BASE}/adaccounts/{account_id}/stats"
    request_params: dict[str, Any] | None = params
    rows: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []

    for _ in range(MAX_PAGES):
        payload = await context.get_json(
            client,
            url,
            headers=headers,
            params=request_params,
        )
        wrapped_stats = payload.get("timeseries_stats") or []
        if not isinstance(wrapped_stats, list):
            raise SnapchatNativeSyncError(
                "snapchat_stats_payload_invalid",
                "Snapchat returned invalid performance data.",
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
                errors.append({"kind": "stats", "error": status[:80]})
                continue
            stat = wrapped.get("timeseries_stat", wrapped)
            if not isinstance(stat, dict):
                continue
            entities: list[dict[str, Any]] = []
            if breakdown_key:
                breakdown = stat.get("breakdown_stats")
                if (
                    isinstance(breakdown, dict)
                    and isinstance(breakdown.get(breakdown_key), list)
                ):
                    entities.extend(
                        item
                        for item in breakdown[breakdown_key]
                        if isinstance(item, dict)
                    )
            if not entities and stat.get("id"):
                entities = [stat]
            for entity in entities:
                external_id = str(entity.get("id") or account_id).strip()
                points = entity.get("timeseries")
                if not external_id or not isinstance(points, list):
                    continue
                for point in points:
                    if (
                        isinstance(point, dict)
                        and isinstance(point.get("stats"), dict)
                    ):
                        rows.append(
                            {
                                "external_id": external_id,
                                "start_time": point.get("start_time"),
                                "end_time": point.get("end_time"),
                                "metrics": point["stats"],
                            }
                        )
        next_url = _safe_next_url(
            (payload.get("paging") or {}).get("next_link")
        )
        if not next_url:
            break
        url, request_params = next_url, None

    return rows, errors


async def sync_snapchat_account_performance_riyadh(
    context: SnapchatSyncContext,
    client: httpx.AsyncClient,
    access_token: str,
    account: dict[str, Any],
    *,
    start_date: date,
    end_date: date,
) -> tuple[int, list[dict[str, str]]]:
    """Refresh lightweight account totals using exact Riyadh-day hours."""
    account_id = str(account["ad_account_id"])
    start, end = riyadh_report_window(start_date, end_date)
    rows, errors = await _fetch_timeseries_rows(
        context,
        client,
        access_token,
        account_id=account_id,
        breakdown_key=None,
        params={
            "start_time": start.isoformat(timespec="seconds"),
            "end_time": end.isoformat(timespec="seconds"),
            "granularity": SOURCE_GRANULARITY,
            "fields": ",".join(STAT_FIELDS),
            "limit": 200,
            "omit_empty": "false",
            "swipe_up_attribution_window": "28_DAY",
            "view_attribution_window": "1_DAY",
            "action_report_time": "conversion",
        },
    )
    _entity_daily, account_daily = aggregate_hourly_rows_by_riyadh_day(
        rows,
        start_date=start_date,
        end_date=end_date,
    )

    saved = 0
    cursor = start_date
    while cursor <= end_date:
        date_string = cursor.isoformat()
        aggregate = account_daily.get(date_string)
        metrics: dict[str, int | float | None]
        if aggregate is None:
            # Successful empty provider response is trustworthy zero spend.
            metrics = {key: 0 for key in STAT_FIELDS}
        else:
            metrics = _final_metrics(aggregate)
        day_start, day_end = riyadh_report_window(cursor, cursor)
        await _upsert_performance(
            context,
            account=account,
            entity_type="ad_account",
            external_id=account_id,
            date_string=date_string,
            metrics=metrics,
            provider_start=day_start.isoformat(timespec="seconds"),
            provider_end=day_end.isoformat(timespec="seconds"),
            report_timezone=BUSINESS_TIMEZONE,
            day_boundary_mode=DAY_BOUNDARY_MODE,
            source_granularity=SOURCE_GRANULARITY,
        )
        saved += 1
        cursor += timedelta(days=1)
    return saved, errors


async def sync_snapchat_performance(
    context: SnapchatSyncContext,
    client: httpx.AsyncClient,
    access_token: str,
    account: dict[str, Any],
    *,
    start_date: date,
    end_date: date,
) -> tuple[int, list[dict[str, str]]]:
    """Refresh campaign DAY detail plus authoritative Riyadh account totals."""
    account_id = str(account["ad_account_id"])
    account_timezone_name = str(account.get("timezone") or "UTC")
    account_tz = _timezone(account_timezone_name)
    campaign_start = datetime(
        start_date.year,
        start_date.month,
        start_date.day,
        tzinfo=account_tz,
    )
    exclusive_end = end_date + timedelta(days=1)
    campaign_end = datetime(
        exclusive_end.year,
        exclusive_end.month,
        exclusive_end.day,
        tzinfo=account_tz,
    )
    rows, errors = await _fetch_timeseries_rows(
        context,
        client,
        access_token,
        account_id=account_id,
        breakdown_key="campaign",
        params={
            "start_time": campaign_start.isoformat(timespec="seconds"),
            "end_time": campaign_end.isoformat(timespec="seconds"),
            "granularity": CAMPAIGN_SOURCE_GRANULARITY,
            "breakdown": "campaign",
            "fields": ",".join(STAT_FIELDS),
            "limit": 200,
            "omit_empty": "false",
            "swipe_up_attribution_window": "28_DAY",
            "view_attribution_window": "1_DAY",
            "action_report_time": "conversion",
        },
    )

    saved = 0
    for row in rows:
        point_start = _parse_datetime(row.get("start_time"))
        if point_start is None:
            continue
        provider_date = point_start.astimezone(account_tz).date()
        if provider_date < start_date or provider_date > end_date:
            continue
        metrics = {
            key: _as_number((row.get("metrics") or {}).get(key))
            for key in STAT_FIELDS
        }
        await _upsert_performance(
            context,
            account=account,
            entity_type="campaign",
            external_id=str(row.get("external_id") or ""),
            date_string=provider_date.isoformat(),
            metrics=metrics,
            provider_start=row.get("start_time"),
            provider_end=row.get("end_time"),
            report_timezone=account_timezone_name,
            day_boundary_mode=CAMPAIGN_DAY_BOUNDARY_MODE,
            source_granularity=CAMPAIGN_SOURCE_GRANULARITY,
        )
        saved += 1

    account_saved, account_errors = await sync_snapchat_account_performance_riyadh(
        context,
        client,
        access_token,
        account,
        start_date=start_date,
        end_date=end_date,
    )
    return saved + account_saved, errors + account_errors


__all__ = [
    "CAMPAIGN_DAY_BOUNDARY_MODE",
    "CAMPAIGN_SOURCE_GRANULARITY",
    "DAY_BOUNDARY_MODE",
    "SOURCE_GRANULARITY",
    "STAT_FIELDS",
    "aggregate_hourly_rows_by_riyadh_day",
    "riyadh_report_window",
    "sync_snapchat_account_performance_riyadh",
    "sync_snapchat_performance",
]
