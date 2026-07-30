"""Campaign-level DAY performance ingestion for native Snapchat V2."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

import httpx

from .snapchat_native_data_common import (
    ATTRIBUTION_MODEL,
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


async def _upsert_performance(
    context: SnapchatSyncContext,
    *, account: dict[str, Any], entity_type: str, external_id: str,
    date_string: str, metrics: dict[str, Any],
    provider_start: Any = None, provider_end: Any = None,
) -> None:
    currency = str(account.get("currency") or "").strip().upper()
    spend_micro = _as_number(metrics.get("spend"))
    value_micro = _as_number(metrics.get("conversion_purchases_value"))
    spend_native = round(float(spend_micro) / 1_000_000, 6) if spend_micro is not None else None
    value_native = round(float(value_micro) / 1_000_000, 6) if value_micro is not None else None
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
        "attribution_model": ATTRIBUTION_MODEL,
        "metrics": metrics,
        "spend_native": spend_native,
        "spend_sar": await context.to_sar(spend_native, currency),
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


async def sync_snapchat_performance(
    context: SnapchatSyncContext,
    client: httpx.AsyncClient,
    access_token: str,
    account: dict[str, Any],
    *, start_date: date, end_date: date,
) -> tuple[int, list[dict[str, str]]]:
    account_id = account["ad_account_id"]
    account_tz = _timezone(account.get("timezone"))
    start = datetime(start_date.year, start_date.month, start_date.day, tzinfo=account_tz)
    exclusive_end = end_date + timedelta(days=1)
    end = datetime(exclusive_end.year, exclusive_end.month, exclusive_end.day, tzinfo=account_tz)
    headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
    url = f"{SNAPCHAT_API_BASE}/adaccounts/{account_id}/stats"
    params: dict[str, Any] | None = {
        "start_time": start.isoformat(timespec="seconds"),
        "end_time": end.isoformat(timespec="seconds"),
        "granularity": "DAY",
        "breakdown": "campaign",
        "fields": ",".join(STAT_FIELDS),
        "limit": 200,
        "omit_empty": "false",
        "swipe_up_attribution_window": "28_DAY",
        "view_attribution_window": "1_DAY",
        "action_report_time": "conversion",
    }
    rows: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for _ in range(MAX_PAGES):
        payload = await context.get_json(client, url, headers=headers, params=params)
        wrapped_stats = payload.get("timeseries_stats") or []
        if not isinstance(wrapped_stats, list):
            raise SnapchatNativeSyncError(
                "snapchat_stats_payload_invalid",
                "Snapchat returned invalid performance data.", status_code=502, retryable=True,
            )
        for wrapped in wrapped_stats:
            if not isinstance(wrapped, dict):
                continue
            status = str(wrapped.get("sub_request_status") or "SUCCESS").upper()
            if "FAIL" in status or "ERROR" in status:
                errors.append({"kind": "stats", "error": status[:80]})
                continue
            stat = wrapped.get("timeseries_stat", wrapped)
            if not isinstance(stat, dict):
                continue
            entities: list[dict[str, Any]] = []
            breakdown = stat.get("breakdown_stats")
            if isinstance(breakdown, dict) and isinstance(breakdown.get("campaign"), list):
                entities.extend(item for item in breakdown["campaign"] if isinstance(item, dict))
            if not entities and stat.get("id"):
                entities = [stat]
            for entity in entities:
                external_id = str(entity.get("id") or "").strip()
                points = entity.get("timeseries")
                if not external_id or not isinstance(points, list):
                    continue
                for point in points:
                    if isinstance(point, dict) and isinstance(point.get("stats"), dict):
                        rows.append({
                            "external_id": external_id,
                            "start_time": point.get("start_time"),
                            "end_time": point.get("end_time"),
                            "metrics": point["stats"],
                        })
        next_url = _safe_next_url((payload.get("paging") or {}).get("next_link"))
        if not next_url:
            break
        url, params = next_url, None

    saved = 0
    account_daily: dict[str, dict[str, Any]] = {}
    for row in rows:
        point_start = _parse_datetime(row.get("start_time"))
        if point_start is None:
            continue
        date_string = point_start.astimezone(account_tz).date().isoformat()
        metrics = {key: _as_number((row.get("metrics") or {}).get(key)) for key in STAT_FIELDS}
        await _upsert_performance(
            context, account=account, entity_type="campaign",
            external_id=row["external_id"], date_string=date_string, metrics=metrics,
            provider_start=row.get("start_time"), provider_end=row.get("end_time"),
        )
        saved += 1
        aggregate = account_daily.setdefault(date_string, {
            "sums": {key: 0.0 for key in STAT_FIELDS},
            "seen": {key: 0 for key in STAT_FIELDS},
            "rows": 0,
        })
        aggregate["rows"] += 1
        for key, value in metrics.items():
            if value is not None:
                aggregate["sums"][key] += float(value)
                aggregate["seen"][key] += 1

    for date_string, aggregate in account_daily.items():
        row_count = int(aggregate["rows"] or 0)
        metrics: dict[str, int | float | None] = {}
        for key in STAT_FIELDS:
            if row_count and aggregate["seen"][key] == row_count:
                value = aggregate["sums"][key]
                metrics[key] = int(value) if float(value).is_integer() else value
            else:
                metrics[key] = None
        await _upsert_performance(
            context, account=account, entity_type="ad_account",
            external_id=account_id, date_string=date_string, metrics=metrics,
        )
        saved += 1
    return saved, errors


__all__ = ["STAT_FIELDS", "sync_snapchat_performance"]
