"""Owner-only GA4 realtime dashboard cards for the Amasi property.

The route reads Google Analytics Data API facts only. OAuth credentials remain
encrypted at rest and are never returned to the browser. The response is cached
briefly because the Dashboard may be open in more than one tab.
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, status

from .google_oauth_security import (
    GOOGLE_CREDENTIALS_COLLECTION,
    GOOGLE_SCOPE_BY_PROVIDER,
    GOOGLE_SOURCE_MODE,
    GOOGLE_TOKEN_URL,
    _iso,
    _utcnow,
    decrypt_google_token,
    encrypt_google_token,
)

GA4_PROVIDER_ID = "google_analytics_4"
DEFAULT_AMASI_GA4_PROPERTY_ID = "353865193"
GA4_PROPERTY_ID_ENV = "GOOGLE_ANALYTICS_PROPERTY_ID"
GA4_REALTIME_CACHE_COLLECTION = "mezan_google_analytics_realtime_cache_v2"
GA4_REALTIME_SOURCE_MODE = "google_analytics_data_api_realtime_v2"
GA4_REALTIME_CACHE_SECONDS = 45
GA4_REALTIME_API = (
    "https://analyticsdata.googleapis.com/v1beta/"
    "properties/{property_id}:runRealtimeReport"
)


class GA4RealtimeError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 502,
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.retryable = retryable


def ga4_property_id() -> str:
    value = (
        os.environ.get(GA4_PROPERTY_ID_ENV, "").strip()
        or DEFAULT_AMASI_GA4_PROPERTY_ID
    )
    if not value.isdigit() or not 6 <= len(value) <= 20:
        raise RuntimeError("GOOGLE_ANALYTICS_PROPERTY_ID is invalid")
    return value


def _scope_set(value: Any) -> set[str]:
    if isinstance(value, str):
        return {item for item in value.split() if item}
    if isinstance(value, (list, tuple, set)):
        return {str(item).strip() for item in value if str(item).strip()}
    return set()


def _as_utc(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _number(value: Any) -> float:
    try:
        return max(0.0, float(value or 0))
    except (TypeError, ValueError, OverflowError):
        return 0.0


def _integer(value: Any) -> int:
    return int(round(_number(value)))


async def ensure_ga4_realtime_indexes(db: Any) -> None:
    await db[GA4_REALTIME_CACHE_COLLECTION].create_index(
        [("user_id", 1), ("property_id", 1)],
        unique=True,
        name="ga4_realtime_user_property_unique",
    )
    await db[GA4_REALTIME_CACHE_COLLECTION].create_index(
        [("expires_at", 1)],
        expireAfterSeconds=0,
        name="ga4_realtime_cache_ttl",
    )


async def _fresh_google_access_token(db: Any, user_id: str) -> str:
    credentials = await db[GOOGLE_CREDENTIALS_COLLECTION].find_one(
        {"user_id": user_id},
        {
            "_id": 0,
            "access_token_ciphertext": 1,
            "refresh_token_ciphertext": 1,
            "expires_at": 1,
            "scope": 1,
        },
    )
    if not credentials:
        raise GA4RealtimeError(
            "google_oauth_connection_missing",
            "اربط Google Analytics من مركز التطبيقات أولًا.",
            status_code=409,
        )

    required_scope = GOOGLE_SCOPE_BY_PROVIDER[GA4_PROVIDER_ID]
    if required_scope not in _scope_set(credentials.get("scope")):
        raise GA4RealtimeError(
            "google_analytics_scope_missing",
            "صلاحية قراءة Google Analytics غير ممنوحة للحساب المرتبط.",
            status_code=409,
        )

    now = _utcnow()
    access_token = decrypt_google_token(
        credentials.get("access_token_ciphertext")
    )
    expires_at = _as_utc(credentials.get("expires_at"))
    if access_token and expires_at and expires_at > now + timedelta(minutes=2):
        return access_token

    refresh_token = decrypt_google_token(
        credentials.get("refresh_token_ciphertext")
    )
    if not refresh_token:
        raise GA4RealtimeError(
            "google_refresh_token_missing",
            "أعد ربط Google للحصول على جلسة طويلة الأجل.",
            status_code=409,
        )

    async with httpx.AsyncClient(timeout=25.0) as client:
        try:
            response = await client.post(
                GOOGLE_TOKEN_URL,
                data={
                    "client_id": os.environ.get("GOOGLE_OAUTH_CLIENT_ID", ""),
                    "client_secret": os.environ.get(
                        "GOOGLE_OAUTH_CLIENT_SECRET", ""
                    ),
                    "refresh_token": refresh_token,
                    "grant_type": "refresh_token",
                },
            )
        except httpx.HTTPError as exc:
            raise GA4RealtimeError(
                "google_token_refresh_network_error",
                "تعذر تحديث جلسة Google مؤقتًا.",
                retryable=True,
            ) from exc

    if response.status_code >= 400:
        raise GA4RealtimeError(
            f"google_token_refresh_http_{response.status_code}",
            "انتهت جلسة Google؛ أعد الربط ثم أعد المحاولة.",
            status_code=409,
        )
    try:
        payload = response.json()
    except ValueError as exc:
        raise GA4RealtimeError(
            "google_token_refresh_invalid_json",
            "تعذر تحديث جلسة Google.",
            retryable=True,
        ) from exc
    access_token = str(payload.get("access_token") or "").strip()
    if not access_token:
        raise GA4RealtimeError(
            "google_token_refresh_missing_access_token",
            "لم تعد Google رمز وصول جديدًا.",
            retryable=True,
        )
    expires_in = max(60, int(payload.get("expires_in") or 3600))
    refreshed_at = _utcnow()
    await db[GOOGLE_CREDENTIALS_COLLECTION].update_one(
        {"user_id": user_id},
        {
            "$set": {
                "access_token_ciphertext": encrypt_google_token(access_token),
                "expires_at": refreshed_at + timedelta(seconds=expires_in),
                "token_type": str(payload.get("token_type") or "Bearer")[:32],
                "updated_at": refreshed_at,
            }
        },
    )
    return access_token


async def _connected_property(db: Any, user_id: str, property_id: str) -> dict[str, Any]:
    row = await db.mezan_integration_accounts_v2.find_one(
        {
            "user_id": user_id,
            "provider": GA4_PROVIDER_ID,
            "external_account_id": property_id,
            "connection_status": "connected",
            "connection_provenance": "api_connection",
        },
        {
            "_id": 0,
            "external_account_id": 1,
            "display_name": 1,
            "last_sync_at": 1,
        },
    )
    if not row:
        raise GA4RealtimeError(
            "ga4_amasi_property_not_connected",
            (
                "موقع Google Analytics المطلوب غير موجود ضمن الربط الحالي. "
                f"المعرّف المطلوب: {property_id}."
            ),
            status_code=409,
        )
    return row


async def _run_realtime_report(
    client: httpx.AsyncClient,
    *,
    access_token: str,
    property_id: str,
    body: dict[str, Any],
) -> dict[str, Any]:
    try:
        response = await client.post(
            GA4_REALTIME_API.format(property_id=property_id),
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
            json=body,
        )
    except httpx.HTTPError as exc:
        raise GA4RealtimeError(
            "ga4_realtime_network_error",
            "تعذر الوصول إلى Google Analytics مؤقتًا.",
            retryable=True,
        ) from exc
    if response.status_code in {401, 403}:
        raise GA4RealtimeError(
            "ga4_realtime_needs_reauth",
            "رفضت Google جلسة Analytics الحالية؛ أعد الربط.",
            status_code=409,
        )
    if response.status_code == 429:
        raise GA4RealtimeError(
            "ga4_realtime_rate_limited",
            "تم بلوغ حد Google Analytics مؤقتًا؛ ستتم إعادة المحاولة تلقائيًا.",
            status_code=429,
            retryable=True,
        )
    if response.status_code >= 400:
        raise GA4RealtimeError(
            f"ga4_realtime_http_{response.status_code}",
            "أعادت Google Analytics خطأ أثناء قراءة البيانات اللحظية.",
            retryable=response.status_code >= 500,
        )
    try:
        payload = response.json()
    except ValueError as exc:
        raise GA4RealtimeError(
            "ga4_realtime_invalid_json",
            "أعادت Google Analytics استجابة غير صالحة.",
            retryable=True,
        ) from exc
    return payload if isinstance(payload, dict) else {}


def _rows(payload: dict[str, Any]) -> list[dict[str, list[str]]]:
    dimensions = [
        str(item.get("name") or "")
        for item in payload.get("dimensionHeaders") or []
        if isinstance(item, dict)
    ]
    metrics = [
        str(item.get("name") or "")
        for item in payload.get("metricHeaders") or []
        if isinstance(item, dict)
    ]
    output: list[dict[str, list[str]]] = []
    for row in payload.get("rows") or []:
        if not isinstance(row, dict):
            continue
        dimension_values = [
            str(item.get("value") or "")
            for item in row.get("dimensionValues") or []
            if isinstance(item, dict)
        ]
        metric_values = [
            str(item.get("value") or "0")
            for item in row.get("metricValues") or []
            if isinstance(item, dict)
        ]
        output.append(
            {
                "dimensions": dict(zip(dimensions, dimension_values)),
                "metrics": dict(zip(metrics, metric_values)),
            }
        )
    return output


def _first_metric(payload: dict[str, Any], metric: str) -> int:
    rows = _rows(payload)
    return _integer(rows[0]["metrics"].get(metric)) if rows else 0


def compose_ga4_realtime_payload(
    *,
    property_id: str,
    property_name: str | None,
    pages_payload: dict[str, Any],
    active_30_payload: dict[str, Any],
    active_5_payload: dict[str, Any],
    minute_payload: dict[str, Any],
    events_payload: dict[str, Any],
    observed_at: str,
) -> dict[str, Any]:
    top_pages: list[dict[str, Any]] = []
    for row in _rows(pages_payload):
        title = str(row["dimensions"].get("unifiedScreenName") or "").strip()
        views = _integer(row["metrics"].get("screenPageViews"))
        if not title or title == "(not set)" or views <= 0:
            continue
        top_pages.append({"title": title[:240], "views": views})

    minute_map: dict[int, int] = {}
    for row in _rows(minute_payload):
        try:
            minute = int(row["dimensions"].get("minutesAgo") or 0)
        except (TypeError, ValueError):
            continue
        if 0 <= minute <= 29:
            minute_map[minute] = _integer(row["metrics"].get("activeUsers"))
    per_minute = [
        {"minutes_ago": minute, "active_users": minute_map.get(minute, 0)}
        for minute in range(29, -1, -1)
    ]

    key_events: list[dict[str, Any]] = []
    for row in _rows(events_payload):
        event_name = str(row["dimensions"].get("eventName") or "").strip()
        count = _integer(row["metrics"].get("keyEvents"))
        if event_name and count > 0:
            key_events.append({"event_name": event_name[:120], "count": count})

    return {
        "provider": GA4_PROVIDER_ID,
        "property_id": property_id,
        "property_name": property_name or f"GA4 {property_id}",
        "window_minutes": 30,
        "observed_at": observed_at,
        "refresh_after_seconds": 60,
        "active_users": {
            "last_30_minutes": _first_metric(active_30_payload, "activeUsers"),
            "last_5_minutes": _first_metric(active_5_payload, "activeUsers"),
            "per_minute": per_minute,
        },
        "top_pages": top_pages[:10],
        "key_events": key_events[:10],
        "source_mode": GA4_REALTIME_SOURCE_MODE,
        "source_only": True,
        "provider_write_reached": False,
        "campaign_write_reached": False,
        "accounting_write_reached": False,
        "qoyod_write_reached": False,
    }


async def build_ga4_realtime_dashboard(
    db: Any,
    user_id: str,
    *,
    force: bool = False,
) -> dict[str, Any]:
    property_id = ga4_property_id()
    account = await _connected_property(db, user_id, property_id)
    await ensure_ga4_realtime_indexes(db)
    now = _utcnow()
    if not force:
        cached = await db[GA4_REALTIME_CACHE_COLLECTION].find_one(
            {
                "user_id": user_id,
                "property_id": property_id,
                "expires_at": {"$gt": now},
            },
            {"_id": 0, "payload": 1},
        )
        if cached and isinstance(cached.get("payload"), dict):
            return {**cached["payload"], "cache_hit": True}

    access_token = await _fresh_google_access_token(db, user_id)
    pages_body = {
        "dimensions": [{"name": "unifiedScreenName"}],
        "metrics": [{"name": "screenPageViews"}],
        "minuteRanges": [{"startMinutesAgo": 29, "endMinutesAgo": 0}],
        "orderBys": [
            {"metric": {"metricName": "screenPageViews"}, "desc": True}
        ],
        "limit": "10",
    }
    active_30_body = {
        "metrics": [{"name": "activeUsers"}],
        "minuteRanges": [{"startMinutesAgo": 29, "endMinutesAgo": 0}],
    }
    active_5_body = {
        "metrics": [{"name": "activeUsers"}],
        "minuteRanges": [{"startMinutesAgo": 4, "endMinutesAgo": 0}],
    }
    minute_body = {
        "dimensions": [{"name": "minutesAgo"}],
        "metrics": [{"name": "activeUsers"}],
        "minuteRanges": [{"startMinutesAgo": 29, "endMinutesAgo": 0}],
        "limit": "30",
    }
    events_body = {
        "dimensions": [{"name": "eventName"}],
        "metrics": [{"name": "keyEvents"}],
        "minuteRanges": [{"startMinutesAgo": 29, "endMinutesAgo": 0}],
        "orderBys": [{"metric": {"metricName": "keyEvents"}, "desc": True}],
        "limit": "10",
    }

    async with httpx.AsyncClient(timeout=25.0) as client:
        reports = await asyncio.gather(
            _run_realtime_report(
                client,
                access_token=access_token,
                property_id=property_id,
                body=pages_body,
            ),
            _run_realtime_report(
                client,
                access_token=access_token,
                property_id=property_id,
                body=active_30_body,
            ),
            _run_realtime_report(
                client,
                access_token=access_token,
                property_id=property_id,
                body=active_5_body,
            ),
            _run_realtime_report(
                client,
                access_token=access_token,
                property_id=property_id,
                body=minute_body,
            ),
            _run_realtime_report(
                client,
                access_token=access_token,
                property_id=property_id,
                body=events_body,
            ),
        )

    observed_at = _iso()
    payload = compose_ga4_realtime_payload(
        property_id=property_id,
        property_name=account.get("display_name"),
        pages_payload=reports[0],
        active_30_payload=reports[1],
        active_5_payload=reports[2],
        minute_payload=reports[3],
        events_payload=reports[4],
        observed_at=observed_at,
    )
    expires_at = _utcnow() + timedelta(seconds=GA4_REALTIME_CACHE_SECONDS)
    await db[GA4_REALTIME_CACHE_COLLECTION].update_one(
        {"user_id": user_id, "property_id": property_id},
        {
            "$set": {
                "user_id": user_id,
                "property_id": property_id,
                "payload": payload,
                "observed_at": observed_at,
                "expires_at": expires_at,
            }
        },
        upsert=True,
    )
    await db.mezan_integrations_v2.update_one(
        {"user_id": user_id, "provider": GA4_PROVIDER_ID},
        {
            "$set": {
                "connection_status": "connected",
                "connection_provenance": "api_connection",
                "source_mode": GA4_REALTIME_SOURCE_MODE,
                "last_sync_at": observed_at,
                "checked_at": observed_at,
                "updated_at": observed_at,
                "data_delay_minutes": 0,
                "data_quality": "good",
                "has_data": True,
            }
        },
        upsert=True,
    )
    return {**payload, "cache_hit": False}


async def _mark_google_needs_reauth(db: Any, user_id: str) -> None:
    now_iso = _iso()
    await db.mezan_integrations_v2.update_one(
        {"user_id": user_id, "provider": GA4_PROVIDER_ID},
        {
            "$set": {
                "connection_status": "needs_reauth",
                "data_quality": "unavailable",
                "data_delay_minutes": None,
                "checked_at": now_iso,
                "updated_at": now_iso,
            }
        },
        upsert=True,
    )


def attach_google_analytics_realtime_routes(
    router: APIRouter,
    db: Any,
    current_user: Callable,
    require_owner: Callable[[Any], dict],
) -> None:
    @router.get(f"/{GA4_PROVIDER_ID}/realtime-dashboard")
    async def google_analytics_realtime_dashboard(
        force: bool = Query(default=False),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        owner = require_owner(user)
        user_id = str(owner["id"])
        try:
            return await build_ga4_realtime_dashboard(
                db,
                user_id,
                force=force,
            )
        except GA4RealtimeError as exc:
            if exc.code in {
                "ga4_realtime_needs_reauth",
                "google_refresh_token_missing",
            } or exc.code.startswith("google_token_refresh_http_"):
                await _mark_google_needs_reauth(db, user_id)
            raise HTTPException(
                status_code=exc.status_code,
                detail={
                    "code": exc.code,
                    "message": exc.message,
                    "retryable": exc.retryable,
                },
            ) from exc


__all__ = [
    "DEFAULT_AMASI_GA4_PROPERTY_ID",
    "GA4_PROVIDER_ID",
    "attach_google_analytics_realtime_routes",
    "build_ga4_realtime_dashboard",
    "compose_ga4_realtime_payload",
    "ga4_property_id",
]
