"""Read-only hourly advertising spend for the Mezan 2 Dashboard.

The endpoint exposes only provider-reported advertising spend. It never reads
or returns booked/accounting expense. Snapchat hourly facts are aggregated from
the existing provider HOUR projection and aligned to the Riyadh business day.
Meta and TikTok remain absent from the hourly series until native hourly facts
exist; the daily Dashboard overview continues to provide their period totals.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Callable
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query

from .snapchat_account_hourly_chart import (
    SNAPCHAT_ACCOUNT_LOCAL_HOURLY_COLLECTION,
)
from .snapchat_native_data_common import SNAPCHAT_PROVIDER_ID

RIYADH_TZ = ZoneInfo("Asia/Riyadh")
MAX_HOURLY_ROWS = 1_000


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed != parsed or abs(parsed) == float("inf") or parsed < 0:
        return None
    return parsed


def _parse_utc(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, str) and value.strip():
        try:
            result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


async def _to_list(cursor: Any, length: int) -> list[dict[str, Any]]:
    if hasattr(cursor, "to_list"):
        return await cursor.to_list(length=length)
    return [row async for row in cursor]


def aggregate_riyadh_hourly_spend(
    rows: list[dict[str, Any]],
    *,
    date_string: str,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Aggregate selected Snapchat account-hour rows into 24 Riyadh buckets."""
    selected_date = date.fromisoformat(date_string)
    current_riyadh = (now or datetime.now(timezone.utc)).astimezone(RIYADH_TZ)
    buckets = [0.0 for _ in range(24)]
    observed_hours: set[int] = set()

    for row in rows:
        point = _parse_utc(row.get("hour_start_utc"))
        spend_sar = _number(row.get("spend_sar"))
        if point is None or spend_sar is None:
            continue
        local = point.astimezone(RIYADH_TZ)
        if local.date() != selected_date:
            continue
        buckets[local.hour] += spend_sar
        observed_hours.add(local.hour)

    has_hourly_facts = bool(observed_hours)
    is_today = selected_date == current_riyadh.date()
    return [
        {
            "date": date_string,
            "hour_index": hour_index,
            "hour": f"{hour_index:02d}:00",
            "snapchat": (
                round(buckets[hour_index], 2)
                if has_hourly_facts
                else None
            ),
            "meta": None,
            "tiktok": None,
            "observed": hour_index in observed_hours,
            "is_future": bool(is_today and hour_index > current_riyadh.hour),
        }
        for hour_index in range(24)
    ]


async def build_dashboard_ads_hourly_spend(
    db: Any,
    user_id: str,
    *,
    date_string: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    try:
        selected_date = date.fromisoformat(date_string)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="invalid_date") from exc

    current_riyadh = (now or datetime.now(timezone.utc)).astimezone(RIYADH_TZ)
    if selected_date > current_riyadh.date():
        raise HTTPException(status_code=422, detail="future_date_not_allowed")

    account_cursor = db.mezan_integration_accounts_v2.find(
        {
            "user_id": user_id,
            "provider": SNAPCHAT_PROVIDER_ID,
            "mezan_selected": True,
        },
        {
            "_id": 0,
            "external_account_id": 1,
            "ad_account_id": 1,
        },
    )
    selected_accounts = await _to_list(account_cursor, 100)
    selected_ids = {
        str(row.get("ad_account_id") or row.get("external_account_id") or "").strip()
        for row in selected_accounts
        if str(row.get("ad_account_id") or row.get("external_account_id") or "").strip()
    }

    local_start = datetime.combine(selected_date, time.min, tzinfo=RIYADH_TZ)
    local_end = local_start + timedelta(days=1)
    query: dict[str, Any] = {
        "user_id": user_id,
        "provider": SNAPCHAT_PROVIDER_ID,
        "hour_start_utc": {
            "$gte": local_start.astimezone(timezone.utc).isoformat(timespec="seconds"),
            "$lt": local_end.astimezone(timezone.utc).isoformat(timespec="seconds"),
        },
    }
    if selected_ids:
        query["ad_account_id"] = {"$in": sorted(selected_ids)}
    else:
        query["ad_account_id"] = {"$in": []}

    cursor = db[SNAPCHAT_ACCOUNT_LOCAL_HOURLY_COLLECTION].find(
        query,
        {
            "_id": 0,
            "ad_account_id": 1,
            "hour_start_utc": 1,
            "spend_sar": 1,
        },
    )
    if hasattr(cursor, "sort"):
        cursor = cursor.sort("hour_start_utc", 1)
    if hasattr(cursor, "limit"):
        cursor = cursor.limit(MAX_HOURLY_ROWS + 1)
    rows = await _to_list(cursor, MAX_HOURLY_ROWS + 1)
    row_limit_reached = len(rows) > MAX_HOURLY_ROWS
    rows = rows[:MAX_HOURLY_ROWS]

    hourly = aggregate_riyadh_hourly_spend(
        rows,
        date_string=date_string,
        now=now,
    )
    has_hourly_facts = any(point["snapchat"] is not None for point in hourly)
    return {
        "date": date_string,
        "timezone": "Asia/Riyadh",
        "granularity": "hour",
        "hourly": hourly,
        "available_hourly_providers": ["snapchat"] if has_hourly_facts else [],
        "unavailable_hourly_providers": ["meta", "tiktok"],
        "selected_snapchat_accounts": len(selected_ids),
        "source_rows": len(rows),
        "row_limit_reached": row_limit_reached,
        "source_collection": SNAPCHAT_ACCOUNT_LOCAL_HOURLY_COLLECTION,
        "source_only": True,
        "provider_write_reached": False,
        "campaign_write_reached": False,
        "accounting_write_reached": False,
        "qoyod_write_reached": False,
    }


def attach_dashboard_ads_hourly_spend_routes(
    router: APIRouter,
    db: Any,
    current_user: Callable,
    require_owner: Callable[[Any], dict],
) -> None:
    @router.get("/dashboard/ads-hourly-spend")
    async def dashboard_ads_hourly_spend(
        date_value: str = Query(..., alias="date"),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        owner = require_owner(user)
        return await build_dashboard_ads_hourly_spend(
            db,
            str(owner["id"]),
            date_string=date_value,
        )


__all__ = [
    "aggregate_riyadh_hourly_spend",
    "attach_dashboard_ads_hourly_spend_routes",
    "build_dashboard_ads_hourly_spend",
]
