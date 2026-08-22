"""Hourly Snapchat V2 ingestion aggregated to the Riyadh business day.

Snapchat ad accounts can use different native timezones.  A DAY request for
an America/Los_Angeles account therefore starts around midday in Riyadh.  The
merchant-facing Mezan day is always Asia/Riyadh 00:00-23:59, so this module
requests HOUR buckets and folds every bucket into the matching Riyadh date
before persisting campaign and ad-account daily rows.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
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
    "conversion_view_content", "conversion_add_cart",
    "conversion_start_checkout", "conversion_add_billing",
)
FUNNEL_STAT_FIELDS = (
    "conversion_view_content",
    "conversion_add_cart",
    "conversion_start_checkout",
    "conversion_add_billing",
    "conversion_purchases",
)
# Audience metrics are intentionally excluded from HOUR/day summation.  A
# frequency of 1.5 today plus 1.7 yesterday is not a 3.2 multi-day frequency;
# each value must remain attached to the exact provider TOTAL window that
# produced it.
NON_ADDITIVE_TOTAL_FIELDS = ("uniques", "frequency")
TOTAL_STAT_FIELDS = (*STAT_FIELDS, *NON_ADDITIVE_TOTAL_FIELDS)


def _funnel_metrics(metrics: dict[str, Any]) -> dict[str, int | float | None]:
    return {
        key: _as_number(metrics.get(key))
        for key in FUNNEL_STAT_FIELDS
    }


def _metric_provenance(
    metrics: dict[str, Any],
    *,
    provider_granularity: str,
    provider_breakdown: str | None,
) -> dict[str, Any]:
    return {
        "provider": SNAPCHAT_PROVIDER_ID,
        "api": "marketing_api_stats",
        "fields_observed": sorted(
            key for key, value in metrics.items() if value is not None
        ),
        "provider_granularity": provider_granularity,
        "provider_breakdown": provider_breakdown,
        "frequency_aggregation": (
            "exact_provider_window"
            if metrics.get("frequency") is not None
            else "not_available_for_window"
        ),
        "frequency_summed": False,
    }
CONVERSION_SOURCE_TYPES = "total"
ACTION_REPORT_TIME = "conversion"
SWIPE_ATTRIBUTION_WINDOW = "28_DAY"
VIEW_ATTRIBUTION_WINDOW = "1_DAY"


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


def _new_bucket(
    fields: tuple[str, ...] = STAT_FIELDS,
) -> dict[str, Any]:
    return {
        "fields": tuple(fields),
        "sums": {key: 0.0 for key in fields},
        "seen": {key: 0 for key in fields},
        "rows": 0,
        "provider_start": None,
        "provider_end": None,
    }


def _earlier(left: Any, right: Any) -> Any:
    if not left:
        return right
    if not right:
        return left
    left_dt = _parse_datetime(left)
    right_dt = _parse_datetime(right)
    if left_dt is None or right_dt is None:
        return min(str(left), str(right))
    return left if left_dt <= right_dt else right


def _later(left: Any, right: Any) -> Any:
    if not left:
        return right
    if not right:
        return left
    left_dt = _parse_datetime(left)
    right_dt = _parse_datetime(right)
    if left_dt is None or right_dt is None:
        return max(str(left), str(right))
    return left if left_dt >= right_dt else right


def _add_to_bucket(
    bucket: dict[str, Any],
    metrics: dict[str, Any],
    *,
    provider_start: Any = None,
    provider_end: Any = None,
) -> None:
    bucket["rows"] += 1
    bucket["provider_start"] = _earlier(
        bucket.get("provider_start"), provider_start
    )
    bucket["provider_end"] = _later(
        bucket.get("provider_end"), provider_end
    )
    for key in tuple(bucket.get("fields") or STAT_FIELDS):
        value = metrics.get(key)
        if value is not None:
            bucket["sums"][key] += float(value)
            bucket["seen"][key] += 1


def _finalize_bucket(bucket: dict[str, Any]) -> dict[str, int | float | None]:
    row_count = int(bucket.get("rows") or 0)
    metrics: dict[str, int | float | None] = {}
    for key in tuple(bucket.get("fields") or STAT_FIELDS):
        seen = int(bucket["seen"].get(key) or 0)
        if key in NON_ADDITIVE_TOTAL_FIELDS:
            # A provider TOTAL audience value is valid only as one exact
            # window.  Multiple values must never be added together.
            complete = seen == 1
        else:
            complete = bool(row_count and seen == row_count)
        if complete:
            value = float(bucket["sums"].get(key) or 0)
            metrics[key] = int(value) if value.is_integer() else value
        else:
            metrics[key] = None
    return metrics


def riyadh_business_window(
    start_date: date,
    end_date: date,
) -> tuple[datetime, datetime]:
    """Return the exact inclusive-date window in Asia/Riyadh.

    The returned end is exclusive, so a one-day request is Riyadh 00:00 of
    that date through Riyadh 00:00 of the following date.
    """
    business_tz = _timezone(BUSINESS_TIMEZONE)
    start = datetime(
        start_date.year,
        start_date.month,
        start_date.day,
        tzinfo=business_tz,
    )
    exclusive_end = end_date + timedelta(days=1)
    end = datetime(
        exclusive_end.year,
        exclusive_end.month,
        exclusive_end.day,
        tzinfo=business_tz,
    )
    return start, end


def riyadh_date_for_point(value: Any) -> str | None:
    point = _parse_datetime(value)
    if point is None:
        return None
    return point.astimezone(_timezone(BUSINESS_TIMEZONE)).date().isoformat()


async def _upsert_performance(
    context: SnapchatSyncContext,
    *, account: dict[str, Any], entity_type: str, external_id: str,
    date_string: str, metrics: dict[str, Any],
    provider_start: Any = None, provider_end: Any = None,
    source_mode: str = SNAPCHAT_NATIVE_SYNC_SOURCE_MODE,
    provider_granularity: str = "HOUR",
    provider_breakdown: str | None = None,
) -> None:
    observe_failure_stage = getattr(context, "observe_failure_stage", None)
    if callable(observe_failure_stage):
        observe_failure_stage("fact_write")
    currency = str(account.get("currency") or "").strip().upper()
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
        "date_timezone": BUSINESS_TIMEZONE,
        "business_timezone": BUSINESS_TIMEZONE,
        "currency": currency or None,
        "account_timezone": str(account.get("timezone") or "UTC"),
        "attribution_model": ATTRIBUTION_MODEL,
        "metrics": metrics,
        "funnel_metrics": _funnel_metrics(metrics),
        "metric_provenance": _metric_provenance(
            metrics,
            provider_granularity=provider_granularity,
            provider_breakdown=provider_breakdown,
        ),
        # Keep the provider's raw purchase count at the document top level so
        # Dashboard reads the exact account fact instead of reconstructing it
        # from unrelated order sources.
        "purchases": purchases,
        "spend_native": spend_native,
        "spend_sar": await context.to_sar(spend_native, currency),
        "purchase_value_native": value_native,
        "purchase_value_sar": await context.to_sar(value_native, currency),
        "computed": _computed(metrics),
        "conversion_reporting": {
            "metric": "conversion_purchases",
            "source_types": [CONVERSION_SOURCE_TYPES],
            "action_report_time": ACTION_REPORT_TIME,
            "swipe_up_attribution_window": SWIPE_ATTRIBUTION_WINDOW,
            "view_attribution_window": VIEW_ATTRIBUTION_WINDOW,
        },
        "source_mode": source_mode,
        "accounting_eligible": False,
        "provider_window_start": provider_start,
        "provider_window_end": provider_end,
        "provider_granularity": provider_granularity,
        "provider_breakdown": provider_breakdown,
        "stored_granularity": "RIYADH_DAY",
        "updated_at": now_iso,
    }
    # Optional publish marker: identifies exactly which analytics_refresh
    # run produced this fact.  The dashboard reader binds to this run_id
    # (and validates the run is complete) instead of relying on the
    # "latest" run, so a subsequent unrelated run (queued/running/failed)
    # cannot invalidate an earlier committed publication.
    published_by_run_id = getattr(context, "run_id", None)
    if isinstance(published_by_run_id, str) and published_by_run_id.strip():
        document["published_by_run_id"] = published_by_run_id.strip()
        document["published_at"] = now_iso
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


async def sync_snapchat_performance(
    context: SnapchatSyncContext,
    client: httpx.AsyncClient,
    access_token: str,
    account: dict[str, Any],
    *, start_date: date, end_date: date,
) -> tuple[int, list[dict[str, str]]]:
    account_id = account["ad_account_id"]
    start, end = riyadh_business_window(start_date, end_date)
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
    }
    url = f"{SNAPCHAT_API_BASE}/adaccounts/{account_id}/stats"
    params: dict[str, Any] | None = {
        "start_time": start.isoformat(timespec="seconds"),
        "end_time": end.isoformat(timespec="seconds"),
        "granularity": "HOUR",
        "breakdown": "campaign",
        "fields": ",".join(STAT_FIELDS),
        "limit": 200,
        "omit_empty": "false",
        "conversion_source_types": CONVERSION_SOURCE_TYPES,
        "swipe_up_attribution_window": SWIPE_ATTRIBUTION_WINDOW,
        "view_attribution_window": VIEW_ATTRIBUTION_WINDOW,
        "action_report_time": ACTION_REPORT_TIME,
    }
    rows: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for _ in range(MAX_PAGES):
        payload = await context.get_json(
            client, url, headers=headers, params=params
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
            breakdown = stat.get("breakdown_stats")
            if (
                isinstance(breakdown, dict)
                and isinstance(breakdown.get("campaign"), list)
            ):
                entities.extend(
                    item
                    for item in breakdown["campaign"]
                    if isinstance(item, dict)
                )
            if not entities and stat.get("id"):
                entities = [stat]
            for entity in entities:
                external_id = str(entity.get("id") or "").strip()
                points = entity.get("timeseries")
                if not external_id or not isinstance(points, list):
                    continue
                for point in points:
                    if (
                        isinstance(point, dict)
                        and isinstance(point.get("stats"), dict)
                    ):
                        rows.append({
                            "external_id": external_id,
                            "start_time": point.get("start_time"),
                            "end_time": point.get("end_time"),
                            "metrics": point["stats"],
                        })
        next_url = _safe_next_url(
            (payload.get("paging") or {}).get("next_link")
        )
        if not next_url:
            break
        url, params = next_url, None

    campaign_daily: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        date_string = riyadh_date_for_point(row.get("start_time"))
        if date_string is None:
            continue
        if date_string < start_date.isoformat() or date_string > end_date.isoformat():
            continue
        metrics = {
            key: _as_number((row.get("metrics") or {}).get(key))
            for key in STAT_FIELDS
        }
        bucket = campaign_daily.setdefault(
            (row["external_id"], date_string),
            _new_bucket(),
        )
        _add_to_bucket(
            bucket,
            metrics,
            provider_start=row.get("start_time"),
            provider_end=row.get("end_time"),
        )

    saved = 0
    account_daily: dict[str, dict[str, Any]] = {}
    for (external_id, date_string), bucket in sorted(
        campaign_daily.items()
    ):
        metrics = _finalize_bucket(bucket)
        await _upsert_performance(
            context,
            account=account,
            entity_type="campaign",
            external_id=external_id,
            date_string=date_string,
            metrics=metrics,
            provider_start=bucket.get("provider_start"),
            provider_end=bucket.get("provider_end"),
            provider_breakdown="campaign",
        )
        saved += 1
        aggregate = account_daily.setdefault(date_string, _new_bucket())
        _add_to_bucket(
            aggregate,
            metrics,
            provider_start=bucket.get("provider_start"),
            provider_end=bucket.get("provider_end"),
        )

    for date_string, aggregate in sorted(account_daily.items()):
        await _upsert_performance(
            context,
            account=account,
            entity_type="ad_account",
            external_id=account_id,
            date_string=date_string,
            metrics=_finalize_bucket(aggregate),
            provider_start=aggregate.get("provider_start"),
            provider_end=aggregate.get("provider_end"),
            provider_breakdown="campaign",
        )
        saved += 1
    return saved, errors


__all__ = [
    "ACTION_REPORT_TIME",
    "CONVERSION_SOURCE_TYPES",
    "FUNNEL_STAT_FIELDS",
    "NON_ADDITIVE_TOTAL_FIELDS",
    "STAT_FIELDS",
    "TOTAL_STAT_FIELDS",
    "_funnel_metrics",
    "_metric_provenance",
    "SWIPE_ATTRIBUTION_WINDOW",
    "VIEW_ATTRIBUTION_WINDOW",
    "riyadh_business_window",
    "riyadh_date_for_point",
    "sync_snapchat_performance",
]
