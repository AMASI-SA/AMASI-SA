"""Four-platform advertising spend for the Mezan 2 Dashboard.

The read route combines provider-reported Snapchat, Meta, TikTok, and Google Ads
facts. The refresh route performs bounded analytical reads from connected
providers and only writes isolated V2 reporting projections.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Callable
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, model_validator

from dashboard_snapchat_spend import load_snapchat_dashboard_spend

from .ads_platform_hourly import PLATFORM_HOURLY_COLLECTION
from .dashboard_ads_platform_refresh import (
    FOUR_PLATFORM_KEYS,
    MAX_REFRESH_DAYS,
    refresh_dashboard_platform_spend,
)
from .google_ads_reporting import (
    GOOGLE_ADS_DAILY_COLLECTION,
    GOOGLE_ADS_PROVIDER_ID,
)
from .meta_native_reporting import META_REPORTING_COLLECTION
from .meta_oauth_security import META_PROVIDER_ID
from .snapchat_account_hourly_chart import SNAPCHAT_ACCOUNT_LOCAL_HOURLY_COLLECTION
from .snapchat_native_data_common import (
    SNAPCHAT_PERFORMANCE_COLLECTION,
    SNAPCHAT_PROVIDER_ID,
)
from .tiktok_native_reporting import TIKTOK_REPORTING_COLLECTION
from .tiktok_oauth_security import TIKTOK_PROVIDER_ID

RIYADH_TZ = ZoneInfo("Asia/Riyadh")
MAX_READ_DAYS = 90
MAX_DAILY_ROWS = 20_000
MAX_HOURLY_ROWS = 20_000

DAILY_COLLECTION_BY_PROVIDER = {
    "snapchat": SNAPCHAT_PERFORMANCE_COLLECTION,
    "meta": META_REPORTING_COLLECTION,
    "tiktok": TIKTOK_REPORTING_COLLECTION,
    "google": GOOGLE_ADS_DAILY_COLLECTION,
}
INTEGRATION_PROVIDER_BY_KEY = {
    "snapchat": SNAPCHAT_PROVIDER_ID,
    "meta": META_PROVIDER_ID,
    "tiktok": TIKTOK_PROVIDER_ID,
    "google": GOOGLE_ADS_PROVIDER_ID,
}


class DashboardPlatformSpendRefreshInput(BaseModel):
    date_from: str
    date_to: str

    @model_validator(mode="after")
    def validate_range(self):
        start = date.fromisoformat(self.date_from)
        end = date.fromisoformat(self.date_to)
        days = (end - start).days + 1
        if end < start or days > MAX_REFRESH_DAYS:
            raise ValueError(
                f"Dashboard advertising refresh supports at most {MAX_REFRESH_DAYS} days"
            )
        return self


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
        parsed = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


async def _to_list(cursor: Any, length: int) -> list[dict[str, Any]]:
    if hasattr(cursor, "to_list"):
        return await cursor.to_list(length=length)
    return [row async for row in cursor]


def _date_list(start: date, end: date) -> list[date]:
    return [start + timedelta(days=offset) for offset in range((end - start).days + 1)]


async def _selected_account_ids(
    db: Any,
    user_id: str,
    provider: str,
) -> list[str]:
    query: dict[str, Any] = {
        "user_id": user_id,
        "provider": provider,
        "connection_status": "connected",
    }
    if provider == SNAPCHAT_PROVIDER_ID:
        query["mezan_selected"] = True
    cursor = db.mezan_integration_accounts_v2.find(
        query,
        {"_id": 0, "external_account_id": 1, "ad_account_id": 1},
    )
    ids = {
        str(row.get("ad_account_id") or row.get("external_account_id") or "").strip()
        for row in await _to_list(cursor, 250)
    }
    return sorted(account_id for account_id in ids if account_id)


async def _connection_states(db: Any, user_id: str) -> dict[str, dict[str, Any]]:
    cursor = db.mezan_integrations_v2.find(
        {
            "user_id": user_id,
            "provider": {"$in": list(INTEGRATION_PROVIDER_BY_KEY.values())},
        },
        {
            "_id": 0,
            "provider": 1,
            "connection_status": 1,
            "connection_provenance": 1,
            "data_quality": 1,
            "last_sync_at": 1,
            "data_delay_minutes": 1,
        },
    )
    by_integration = {
        str(row.get("provider") or ""): row
        for row in await _to_list(cursor, 20)
    }
    return {
        key: by_integration.get(provider_id, {})
        for key, provider_id in INTEGRATION_PROVIDER_BY_KEY.items()
    }


async def _daily_spend(
    db: Any,
    user_id: str,
    start: date,
    end: date,
    snapchat: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, bool]]:
    dates = _date_list(start, end)
    by_date: dict[str, dict[str, Any]] = {
        day.isoformat(): {
            "date": day.isoformat(),
            **{provider: None for provider in FOUR_PLATFORM_KEYS},
        }
        for day in dates
    }
    facts = {provider: False for provider in FOUR_PLATFORM_KEYS}
    for day in dates:
        by_date[day.isoformat()]["snapchat"] = (
            snapchat.get("daily_sar") or {}
        ).get(day.isoformat())
    facts["snapchat"] = (
        (snapchat.get("quality") or {}).get("amount_complete") is True
    )

    for provider, collection_name in DAILY_COLLECTION_BY_PROVIDER.items():
        if provider == "snapchat":
            continue
        query: dict[str, Any] = {
            "user_id": user_id,
            "provider": INTEGRATION_PROVIDER_BY_KEY[provider],
            "date": {
                "$gte": start.isoformat(),
                "$lte": end.isoformat(),
            },
        }
        cursor = db[collection_name].find(
            query,
            {"_id": 0, "date": 1, "spend_sar": 1},
        )
        rows = await _to_list(cursor, MAX_DAILY_ROWS)
        totals: dict[str, float] = defaultdict(float)
        observed_dates: set[str] = set()
        for row in rows:
            day_text = str(row.get("date") or "")[:10]
            spend = _number(row.get("spend_sar"))
            if day_text not in by_date or spend is None:
                continue
            totals[day_text] += spend
            observed_dates.add(day_text)
        if observed_dates:
            facts[provider] = True
            for day_text in by_date:
                by_date[day_text][provider] = round(totals.get(day_text, 0.0), 2)

    return [by_date[day.isoformat()] for day in dates], facts


async def _hourly_spend(
    db: Any,
    user_id: str,
    selected_date: date,
) -> tuple[list[dict[str, Any]], dict[str, bool]]:
    local_start = datetime.combine(selected_date, time.min, tzinfo=RIYADH_TZ)
    local_end = local_start + timedelta(days=1)
    utc_start = local_start.astimezone(timezone.utc)
    utc_end = local_end.astimezone(timezone.utc)

    buckets: dict[str, list[float]] = {
        provider: [0.0 for _ in range(24)] for provider in FOUR_PLATFORM_KEYS
    }
    facts = {provider: False for provider in FOUR_PLATFORM_KEYS}
    selected_snapchat_ids = await _selected_account_ids(
        db,
        user_id,
        SNAPCHAT_PROVIDER_ID,
    )

    snap_cursor = db[SNAPCHAT_ACCOUNT_LOCAL_HOURLY_COLLECTION].find(
        {
            "user_id": user_id,
            "provider": SNAPCHAT_PROVIDER_ID,
            "ad_account_id": {"$in": selected_snapchat_ids},
            "hour_start_utc": {
                "$gte": utc_start.isoformat(timespec="seconds"),
                "$lt": utc_end.isoformat(timespec="seconds"),
            },
        },
        {"_id": 0, "hour_start_utc": 1, "spend_sar": 1},
    )
    for row in await _to_list(snap_cursor, MAX_HOURLY_ROWS):
        point = _parse_utc(row.get("hour_start_utc"))
        spend = _number(row.get("spend_sar"))
        if point is None or spend is None:
            continue
        local = point.astimezone(RIYADH_TZ)
        if local.date() != selected_date:
            continue
        buckets["snapchat"][local.hour] += spend
        facts["snapchat"] = True

    generic_cursor = db[PLATFORM_HOURLY_COLLECTION].find(
        {
            "user_id": user_id,
            "provider": {"$in": ["meta", "tiktok", "google"]},
            "hour_start_utc": {
                "$gte": utc_start.isoformat(timespec="seconds"),
                "$lt": utc_end.isoformat(timespec="seconds"),
            },
        },
        {"_id": 0, "provider": 1, "hour_start_utc": 1, "spend_sar": 1},
    )
    for row in await _to_list(generic_cursor, MAX_HOURLY_ROWS):
        provider = str(row.get("provider") or "")
        if provider not in {"meta", "tiktok", "google"}:
            continue
        point = _parse_utc(row.get("hour_start_utc"))
        spend = _number(row.get("spend_sar"))
        if point is None or spend is None:
            continue
        local = point.astimezone(RIYADH_TZ)
        if local.date() != selected_date:
            continue
        buckets[provider][local.hour] += spend
        facts[provider] = True

    hourly = []
    for hour_index in range(24):
        hourly.append(
            {
                "date": selected_date.isoformat(),
                "hour_index": hour_index,
                "hour": f"{hour_index:02d}:00",
                **{
                    provider: (
                        round(buckets[provider][hour_index], 2)
                        if facts[provider]
                        else None
                    )
                    for provider in FOUR_PLATFORM_KEYS
                },
            }
        )
    return hourly, facts


async def build_dashboard_platform_spend(
    db: Any,
    user_id: str,
    *,
    date_from: str,
    date_to: str,
) -> dict[str, Any]:
    try:
        start = date.fromisoformat(date_from)
        end = date.fromisoformat(date_to)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="invalid_date_range") from exc
    if end < start or (end - start).days + 1 > MAX_READ_DAYS:
        raise HTTPException(status_code=422, detail="invalid_date_range")

    snapchat = await load_snapchat_dashboard_spend(
        db,
        user_id,
        start=start,
        end=end,
    )
    daily, daily_facts = await _daily_spend(db, user_id, start, end, snapchat)
    states = await _connection_states(db, user_id)
    single_day = start == end
    hourly: list[dict[str, Any]] = []
    hourly_facts = {provider: False for provider in FOUR_PLATFORM_KEYS}
    if single_day:
        hourly, hourly_facts = await _hourly_spend(db, user_id, start)
        snap_state = (snapchat.get("quality") or {}).get("data_state")
        for row in hourly:
            row["snapchat"] = 0.0 if snap_state == "confirmed_zero" else None
        hourly_facts["snapchat"] = snap_state == "confirmed_zero"

    totals = {
        provider: round(
            sum(float(row.get(provider) or 0) for row in daily),
            2,
        )
        if daily_facts[provider]
        else None
        for provider in FOUR_PLATFORM_KEYS
    }
    totals["snapchat"] = snapchat.get("total_sar")
    provider_rows = {}
    for provider in FOUR_PLATFORM_KEYS:
        state = states.get(provider) or {}
        connection_status = str(state.get("connection_status") or "not_connected")
        if provider == "snapchat":
            snap_quality = snapchat.get("quality") or {}
            provider_rows[provider] = {
                "provider": provider,
                "integration_provider": SNAPCHAT_PROVIDER_ID,
                "connection_status": connection_status,
                "connected": snap_quality.get("connected") is True,
                "daily_available": snap_quality.get("amount_complete") is True,
                "hourly_available": hourly_facts[provider],
                "total_sar": totals[provider],
                "data_quality": snap_quality.get("status"),
                "data_state": snap_quality.get("data_state"),
                "coverage_complete": snap_quality.get("coverage_complete") is True,
                "amount_complete": snap_quality.get("amount_complete") is True,
                "reason_codes": list(snap_quality.get("reason_codes") or []),
                "last_sync_at": state.get("last_sync_at"),
                "data_delay_minutes": state.get("data_delay_minutes"),
            }
            continue
        provider_rows[provider] = {
            "provider": provider,
            "integration_provider": INTEGRATION_PROVIDER_BY_KEY[provider],
            "connection_status": connection_status,
            "connected": connection_status in {
                "connected",
                "active",
                "healthy",
                "data_available",
            },
            "daily_available": daily_facts[provider],
            "hourly_available": hourly_facts[provider],
            "total_sar": totals[provider],
            "data_quality": state.get("data_quality"),
            "last_sync_at": state.get("last_sync_at"),
            "data_delay_minutes": state.get("data_delay_minutes"),
        }

    known_total_sar = round(
        sum(value for value in totals.values() if value is not None), 2
    )
    snap_amount_complete = (
        (snapchat.get("quality") or {}).get("amount_complete") is True
    )
    total_sar = known_total_sar if snap_amount_complete else None
    return {
        "date_from": start.isoformat(),
        "date_to": end.isoformat(),
        "timezone": "Asia/Riyadh",
        "chart_granularity": "hour" if single_day else "day",
        "daily_spend": daily,
        "hourly_spend": hourly,
        "providers": provider_rows,
        "provider_totals_sar": totals,
        "total_sar": total_sar,
        "known_total_sar": known_total_sar,
        "spend_quality": {
            "status": "complete" if snap_amount_complete else "incomplete",
            "amount_complete": snap_amount_complete,
            "known_total_sar": known_total_sar,
            "snapchat": snapchat.get("quality") or {},
        },
        "source_only": True,
        "provider_write_reached": False,
        "campaign_write_reached": False,
        "accounting_write_reached": False,
        "qoyod_write_reached": False,
    }


def attach_dashboard_ads_platform_spend_routes(
    router: APIRouter,
    db: Any,
    current_user: Callable,
    require_owner: Callable[[Any], dict],
) -> None:
    @router.get("/dashboard/ads-platform-spend")
    async def dashboard_ads_platform_spend(
        date_from: str = Query(...),
        date_to: str = Query(...),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        owner = require_owner(user)
        return await build_dashboard_platform_spend(
            db,
            str(owner["id"]),
            date_from=date_from,
            date_to=date_to,
        )

    @router.post("/dashboard/ads-platform-spend/refresh")
    async def dashboard_ads_platform_spend_refresh(
        payload: DashboardPlatformSpendRefreshInput,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        owner = require_owner(user)
        user_id = str(owner["id"])
        refresh = await refresh_dashboard_platform_spend(
            db,
            user_id,
            date_from=payload.date_from,
            date_to=payload.date_to,
        )
        spend = await build_dashboard_platform_spend(
            db,
            user_id,
            date_from=payload.date_from,
            date_to=payload.date_to,
        )
        return {**spend, "refresh": refresh}


__all__ = [
    "DashboardPlatformSpendRefreshInput",
    "MAX_READ_DAYS",
    "attach_dashboard_ads_platform_spend_routes",
    "build_dashboard_platform_spend",
]
