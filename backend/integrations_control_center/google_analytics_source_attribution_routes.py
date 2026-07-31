"""GA4 session-source traffic and order attribution for Mezan Dashboard.

Realtime Data API does not expose traffic-source dimensions. This module uses
Core Reporting ``runReport`` with ``sessionSource`` and reads only store-side
facts: sessions, active users, transactions, and purchase revenue.

These figures are an independent GA4 attribution view. They never replace the
provider-native conversions shown on Snapchat or Meta cards.
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable
from zoneinfo import ZoneInfo

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query

from .google_analytics_realtime_routes import (
    GA4_PROVIDER_ID,
    GA4RealtimeError,
    _connected_property,
    _fresh_google_access_token,
    _rows,
    ga4_property_id,
)
from .google_oauth_security import _iso, _utcnow

BUSINESS_TIMEZONE = "Asia/Riyadh"
RIYADH_TZ = ZoneInfo(BUSINESS_TIMEZONE)
GA4_CORE_REPORT_API = (
    "https://analyticsdata.googleapis.com/v1beta/"
    "properties/{property_id}:runReport"
)
GA4_SOURCE_CACHE_COLLECTION = "mezan_google_analytics_source_cache_v2"
GA4_SOURCE_CACHE_SECONDS = 60
GA4_SOURCE_MODE = "google_analytics_core_session_source_v2"
_SOURCE_LOCKS: dict[str, asyncio.Lock] = {}

PRIORITY_PLATFORMS = ("snapchat", "tiktok", "meta", "google", "direct")
PLATFORM_LABELS = {
    "snapchat": "Snapchat",
    "tiktok": "TikTok",
    "meta": "Meta / Instagram",
    "google": "Google",
    "direct": "Direct",
    "other": "مصادر أخرى",
}


def _number(value: Any) -> float:
    try:
        return max(0.0, float(value or 0))
    except (TypeError, ValueError, OverflowError):
        return 0.0


def _integer(value: Any) -> int:
    return int(round(_number(value)))


def _riyadh_today(now: datetime | None = None) -> date:
    value = now or datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(RIYADH_TZ).date()


def normalize_session_source(value: Any) -> tuple[str, str, str]:
    raw = str(value or "(direct)").strip() or "(direct)"
    lowered = raw.lower().strip()
    compact = lowered.replace(" ", "")

    if lowered in {"(direct)", "direct", "(none)", "none"}:
        return "direct", PLATFORM_LABELS["direct"], "direct"
    if "snapchat" in compact or compact in {"snap", "sc"}:
        return "snapchat", PLATFORM_LABELS["snapchat"], "snapchat"
    if "tiktok" in compact or compact in {"tik_tok", "tt"}:
        return "tiktok", PLATFORM_LABELS["tiktok"], "tiktok"
    if (
        "instagram" in compact
        or "facebook" in compact
        or compact in {"fb", "ig", "meta"}
        or compact.endswith("facebook.com")
        or compact.endswith("instagram.com")
    ):
        return "meta", PLATFORM_LABELS["meta"], "meta"
    if "google" in compact or compact.endswith("google.com"):
        return "google", PLATFORM_LABELS["google"], "google"
    safe_key = "other:" + lowered[:100]
    return safe_key, raw[:160], "other"


def _empty_bucket(key: str, label: str, platform: str) -> dict[str, Any]:
    return {
        "key": key,
        "platform": platform,
        "label": label,
        "sessions": 0,
        "active_users": 0,
        "orders": 0,
        "purchase_revenue": 0.0,
        "source_rows": 0,
        "raw_sources": set(),
    }


def compose_source_period(
    payload: dict[str, Any],
    *,
    start_date: date,
    end_date: date,
) -> dict[str, Any]:
    buckets: dict[str, dict[str, Any]] = {
        platform: _empty_bucket(
            platform,
            PLATFORM_LABELS[platform],
            platform,
        )
        for platform in PRIORITY_PLATFORMS
    }

    for row in _rows(payload):
        raw_source = str(
            row.get("dimensions", {}).get("sessionSource") or "(direct)"
        ).strip()
        key, label, platform = normalize_session_source(raw_source)
        bucket = buckets.setdefault(key, _empty_bucket(key, label, platform))
        metrics = row.get("metrics", {})
        bucket["sessions"] += _integer(metrics.get("sessions"))
        bucket["active_users"] += _integer(metrics.get("activeUsers"))
        bucket["orders"] += _integer(metrics.get("transactions"))
        bucket["purchase_revenue"] += _number(metrics.get("purchaseRevenue"))
        bucket["source_rows"] += 1
        if raw_source:
            bucket["raw_sources"].add(raw_source[:160])

    rows: list[dict[str, Any]] = []
    for bucket in buckets.values():
        rows.append(
            {
                **{key: value for key, value in bucket.items() if key != "raw_sources"},
                "purchase_revenue": round(bucket["purchase_revenue"], 2),
                "raw_sources": sorted(bucket["raw_sources"]),
            }
        )

    priority_rank = {name: index for index, name in enumerate(PRIORITY_PLATFORMS)}
    rows.sort(
        key=lambda item: (
            -int(item.get("sessions") or 0),
            -int(item.get("orders") or 0),
            priority_rank.get(str(item.get("platform")), 99),
            str(item.get("label") or ""),
        )
    )
    platform_totals = {
        platform: next(
            (
                item
                for item in rows
                if item.get("key") == platform
            ),
            {
                **_empty_bucket(platform, PLATFORM_LABELS[platform], platform),
                "raw_sources": [],
            },
        )
        for platform in PRIORITY_PLATFORMS
    }
    for item in platform_totals.values():
        if isinstance(item.get("raw_sources"), set):
            item["raw_sources"] = sorted(item["raw_sources"])

    return {
        "start": start_date.isoformat(),
        "end": end_date.isoformat(),
        "sessions": sum(int(item.get("sessions") or 0) for item in rows),
        "orders": sum(int(item.get("orders") or 0) for item in rows),
        "purchase_revenue": round(
            sum(float(item.get("purchase_revenue") or 0) for item in rows),
            2,
        ),
        "sources": rows,
        "platforms": platform_totals,
    }


async def ensure_ga4_source_indexes(db: Any) -> None:
    await db[GA4_SOURCE_CACHE_COLLECTION].create_index(
        [("user_id", 1), ("property_id", 1)],
        unique=True,
        name="ga4_source_user_property_unique",
    )
    await db[GA4_SOURCE_CACHE_COLLECTION].create_index(
        [("expires_at", 1)],
        expireAfterSeconds=0,
        name="ga4_source_cache_ttl",
    )


async def _run_source_report(
    client: httpx.AsyncClient,
    *,
    access_token: str,
    property_id: str,
    start_date: date,
    end_date: date,
) -> dict[str, Any]:
    body = {
        "dateRanges": [
            {
                "startDate": start_date.isoformat(),
                "endDate": end_date.isoformat(),
            }
        ],
        "dimensions": [{"name": "sessionSource"}],
        "metrics": [
            {"name": "sessions"},
            {"name": "activeUsers"},
            {"name": "transactions"},
            {"name": "purchaseRevenue"},
        ],
        "orderBys": [{"metric": {"metricName": "sessions"}, "desc": True}],
        "keepEmptyRows": True,
        "limit": "100",
    }
    try:
        response = await client.post(
            GA4_CORE_REPORT_API.format(property_id=property_id),
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
            json=body,
        )
    except httpx.HTTPError as exc:
        raise GA4RealtimeError(
            "ga4_source_report_network_error",
            "تعذر قراءة مصادر الزيارات من Google Analytics مؤقتًا.",
            retryable=True,
        ) from exc
    if response.status_code in {401, 403}:
        raise GA4RealtimeError(
            "ga4_source_report_needs_reauth",
            "رفضت Google جلسة Analytics الحالية؛ أعد الربط.",
            status_code=409,
        )
    if response.status_code == 429:
        raise GA4RealtimeError(
            "ga4_source_report_rate_limited",
            "تم بلوغ حد Google Analytics مؤقتًا.",
            status_code=429,
            retryable=True,
        )
    if response.status_code >= 400:
        raise GA4RealtimeError(
            f"ga4_source_report_http_{response.status_code}",
            "أعادت Google Analytics خطأ أثناء قراءة مصادر الزيارات.",
            retryable=response.status_code >= 500,
        )
    try:
        payload = response.json()
    except ValueError as exc:
        raise GA4RealtimeError(
            "ga4_source_report_invalid_json",
            "أعادت Google Analytics استجابة غير صالحة.",
            retryable=True,
        ) from exc
    return payload if isinstance(payload, dict) else {}


async def _cached_payload(
    db: Any,
    *,
    user_id: str,
    property_id: str,
    now: datetime,
) -> dict[str, Any] | None:
    cached = await db[GA4_SOURCE_CACHE_COLLECTION].find_one(
        {
            "user_id": user_id,
            "property_id": property_id,
            "expires_at": {"$gt": now},
        },
        {"_id": 0, "payload": 1},
    )
    payload = cached.get("payload") if cached else None
    return payload if isinstance(payload, dict) else None


async def build_ga4_source_attribution(
    db: Any,
    user_id: str,
    *,
    force: bool = False,
    now: Callable[[], datetime] = _utcnow,
) -> dict[str, Any]:
    property_id = ga4_property_id()
    account = await _connected_property(db, user_id, property_id)
    await ensure_ga4_source_indexes(db)
    current = now()
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current = current.astimezone(timezone.utc)

    if not force:
        cached = await _cached_payload(
            db,
            user_id=user_id,
            property_id=property_id,
            now=current,
        )
        if cached:
            return {**cached, "cache_hit": True}

    lock = _SOURCE_LOCKS.setdefault(user_id, asyncio.Lock())
    async with lock:
        if not force:
            cached = await _cached_payload(
                db,
                user_id=user_id,
                property_id=property_id,
                now=_utcnow(),
            )
            if cached:
                return {**cached, "cache_hit": True}

        access_token = await _fresh_google_access_token(db, user_id)
        today = _riyadh_today(current)
        month_start = today.replace(day=1)
        last_30_start = today - timedelta(days=29)
        async with httpx.AsyncClient(timeout=25.0) as client:
            reports = await asyncio.gather(
                _run_source_report(
                    client,
                    access_token=access_token,
                    property_id=property_id,
                    start_date=today,
                    end_date=today,
                ),
                _run_source_report(
                    client,
                    access_token=access_token,
                    property_id=property_id,
                    start_date=month_start,
                    end_date=today,
                ),
                _run_source_report(
                    client,
                    access_token=access_token,
                    property_id=property_id,
                    start_date=last_30_start,
                    end_date=today,
                ),
            )

        observed_at = _iso()
        payload = {
            "provider": GA4_PROVIDER_ID,
            "property_id": property_id,
            "property_name": account.get("display_name")
            or f"GA4 {property_id}",
            "dimension": "sessionSource",
            "business_timezone": BUSINESS_TIMEZONE,
            "observed_at": observed_at,
            "refresh_after_seconds": GA4_SOURCE_CACHE_SECONDS,
            "today": compose_source_period(
                reports[0], start_date=today, end_date=today
            ),
            "month": compose_source_period(
                reports[1], start_date=month_start, end_date=today
            ),
            "last_30d": compose_source_period(
                reports[2], start_date=last_30_start, end_date=today
            ),
            "source_mode": GA4_SOURCE_MODE,
            "source_only": True,
            "provider_write_reached": False,
            "campaign_write_reached": False,
            "accounting_write_reached": False,
            "qoyod_write_reached": False,
        }
        await db[GA4_SOURCE_CACHE_COLLECTION].update_one(
            {"user_id": user_id, "property_id": property_id},
            {
                "$set": {
                    "user_id": user_id,
                    "property_id": property_id,
                    "payload": payload,
                    "observed_at": observed_at,
                    "expires_at": _utcnow()
                    + timedelta(seconds=GA4_SOURCE_CACHE_SECONDS),
                }
            },
            upsert=True,
        )
        return {**payload, "cache_hit": False}


def attach_google_analytics_source_routes(
    router: APIRouter,
    db: Any,
    current_user: Callable,
    require_owner: Callable[[Any], dict],
) -> None:
    @router.get(f"/{GA4_PROVIDER_ID}/source-attribution-dashboard")
    async def google_analytics_source_dashboard(
        force: bool = Query(default=False),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        owner = require_owner(user)
        user_id = str(owner["id"])
        try:
            return await build_ga4_source_attribution(
                db,
                user_id,
                force=force,
            )
        except GA4RealtimeError as exc:
            raise HTTPException(
                status_code=exc.status_code,
                detail={
                    "code": exc.code,
                    "message": exc.message,
                    "retryable": exc.retryable,
                },
            ) from exc


__all__ = [
    "BUSINESS_TIMEZONE",
    "GA4_SOURCE_MODE",
    "attach_google_analytics_source_routes",
    "build_ga4_source_attribution",
    "compose_source_period",
    "normalize_session_source",
]
