"""Read-only Snapchat Pixel and Conversions API diagnostics for Mezan V2."""
from __future__ import annotations

import hashlib
import math
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import httpx
from pydantic import BaseModel, Field

from .snapchat_native_data_common import (
    MAX_PROVIDER_CALLS,
    SNAPCHAT_API_BASE,
    SNAPCHAT_PROVIDER_ID,
    SnapchatNativeSyncError,
    SnapchatSyncContext,
    _collection,
    _iso,
    _parse_datetime,
    _safe_next_url,
    _utcnow,
)

TRACKING_SOURCE_MODE = "snapchat_marketing_tracking_diagnostics_v2"
TRACKING_ASSET_COLLECTION = "mezan_snapchat_tracking_assets_v2"
EVENT_DIAGNOSTIC_COLLECTION = "mezan_snapchat_event_diagnostics_v2"
MAX_DIAGNOSTIC_DAYS = 7
MAX_PIXELS_PER_ACCOUNT = 10
MAX_PIXELS_TOTAL = 50
MAX_PIXEL_PAGES = 10
MAX_DOMAINS_PER_PIXEL = 10
TRACKING_LOCK_TTL = timedelta(minutes=45)
TRACKING_IDEMPOTENCY_WINDOW = timedelta(minutes=5)
TERMINAL_STATUSES = {"complete", "partial", "failed"}


class SnapchatTrackingDiagnosticsInput(BaseModel):
    days: int = Field(default=7, ge=1, le=MAX_DIAGNOSTIC_DAYS)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=128)


class _OptionalEndpointError(Exception):
    def __init__(self, code: str, *, retryable: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable


def _safe_text(value: Any, *, limit: int = 1000) -> str | None:
    text = str(value or "").strip()
    return text[:limit] if text else None


def _safe_number(value: Any) -> int | float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(parsed):
        return None
    return int(parsed) if parsed.is_integer() else parsed


def _safe_domain(value: Any) -> str | None:
    domain = str(value or "").strip().lower().rstrip(".")
    if not domain or len(domain) > 253 or "/" in domain or " " in domain:
        return None
    return domain


def _safe_recommendation(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    recommendation = {
        "title": _safe_text(value.get("title"), limit=300),
        "description": _safe_text(value.get("description"), limit=1200),
        "recommendation_code": _safe_text(value.get("recommendation_code"), limit=120),
        "priority": _safe_text(value.get("priority"), limit=30),
        "score": _safe_text(value.get("score"), limit=30),
    }
    return recommendation if any(recommendation.values()) else None


async def ensure_snapchat_tracking_indexes(db: Any) -> None:
    await _collection(db, TRACKING_ASSET_COLLECTION).create_index(
        [("user_id", 1), ("pixel_id", 1)],
        unique=True,
        name="mezan_snapchat_tracking_assets_v2_pixel_unique",
    )
    await _collection(db, TRACKING_ASSET_COLLECTION).create_index(
        [("user_id", 1), ("ad_account_id", 1), ("last_observed_at", -1)],
        name="mezan_snapchat_tracking_assets_v2_account_latest",
    )
    await _collection(db, TRACKING_ASSET_COLLECTION).create_index(
        [("user_id", 1), ("ad_account_ids", 1), ("last_observed_at", -1)],
        name="mezan_snapchat_tracking_assets_v2_shared_account_latest",
    )
    await _collection(db, EVENT_DIAGNOSTIC_COLLECTION).create_index(
        [("user_id", 1), ("pixel_id", 1), ("diagnostic_type", 1), ("diagnostic_key", 1)],
        unique=True,
        name="mezan_snapchat_event_diagnostics_v2_identity_unique",
    )
    await _collection(db, EVENT_DIAGNOSTIC_COLLECTION).create_index(
        [("user_id", 1), ("event_type", 1), ("last_observed_at", -1)],
        name="mezan_snapchat_event_diagnostics_v2_event_latest",
    )
    await _collection(db, "mezan_integration_sync_runs_v2").create_index(
        [("user_id", 1), ("provider", 1), ("run_type", 1), ("status", 1)],
        unique=True,
        partialFilterExpression={"run_type": "tracking_diagnostics", "status": "running"},
        name="mezan_snapchat_tracking_v2_one_running",
    )


async def _optional_get_json(
    context: SnapchatSyncContext,
    client: httpx.AsyncClient,
    url: str,
    *,
    headers: dict[str, str],
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    context.provider_calls += 1
    if context.provider_calls > MAX_PROVIDER_CALLS:
        raise SnapchatNativeSyncError(
            "snapchat_provider_call_budget_exceeded",
            f"Snapchat diagnostics exceeded the {MAX_PROVIDER_CALLS} call budget.",
            status_code=400,
        )
    try:
        response = await client.get(url, headers=headers, params=params)
    except httpx.HTTPError as exc:
        raise _OptionalEndpointError(
            "snapchat_tracking_provider_network_error", retryable=True
        ) from exc
    if response.status_code == 401:
        raise SnapchatNativeSyncError(
            "snapchat_needs_reauth",
            "Snapchat authorization must be renewed.",
            status_code=409,
            result={"needs_reauth": True},
        )
    if response.status_code in {403, 404}:
        raise _OptionalEndpointError(
            f"snapchat_tracking_http_{response.status_code}", retryable=False
        )
    if response.status_code >= 400:
        raise _OptionalEndpointError(
            f"snapchat_tracking_http_{response.status_code}",
            retryable=response.status_code >= 500,
        )
    try:
        payload = response.json() or {}
    except (TypeError, ValueError) as exc:
        raise _OptionalEndpointError(
            "snapchat_tracking_invalid_json", retryable=True
        ) from exc
    if not isinstance(payload, dict):
        raise _OptionalEndpointError(
            "snapchat_tracking_invalid_payload", retryable=True
        )
    request_status = str(payload.get("request_status") or "").upper()
    if "FAIL" in request_status or "ERROR" in request_status:
        raise _OptionalEndpointError(
            "snapchat_tracking_provider_request_failed", retryable=True
        )
    return payload


def _unwrap_rows(payload: dict[str, Any], plural: str, singular: str) -> list[dict[str, Any]]:
    wrappers = payload.get(plural) or []
    if not isinstance(wrappers, list):
        return []
    rows: list[dict[str, Any]] = []
    for wrapper in wrappers:
        if not isinstance(wrapper, dict):
            continue
        status = str(wrapper.get("sub_request_status") or "SUCCESS").upper()
        if "FAIL" in status or "ERROR" in status:
            continue
        row = wrapper.get(singular, wrapper)
        if isinstance(row, dict):
            rows.append(row)
    return rows


async def _list_pixels(
    context: SnapchatSyncContext,
    client: httpx.AsyncClient,
    access_token: str,
    account_id: str,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
    url = f"{SNAPCHAT_API_BASE}/adaccounts/{account_id}/pixels"
    params: dict[str, Any] | None = {"limit": 1000}
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    errors: list[dict[str, str]] = []
    for page in range(1, MAX_PIXEL_PAGES + 1):
        payload = await context.get_json(client, url, headers=headers, params=params)
        for pixel in _unwrap_rows(payload, "pixels", "pixel"):
            pixel_id = str(pixel.get("id") or "").strip()
            if not pixel_id or pixel_id in seen:
                continue
            seen.add(pixel_id)
            rows.append(pixel)
            if len(rows) >= MAX_PIXELS_PER_ACCOUNT:
                next_url = _safe_next_url((payload.get("paging") or {}).get("next_link"))
                if next_url or len(_unwrap_rows(payload, "pixels", "pixel")) > len(rows):
                    errors.append({"kind": "pixels", "error": "pixel_limit_reached"})
                return rows[:MAX_PIXELS_PER_ACCOUNT], errors
        next_url = _safe_next_url((payload.get("paging") or {}).get("next_link"))
        if not next_url:
            return rows, errors
        if page == MAX_PIXEL_PAGES:
            errors.append({"kind": "pixels", "error": "pixel_page_limit_reached"})
            return rows, errors
        url, params = next_url, None
    return rows, errors


def _parse_domains(payload: dict[str, Any]) -> list[dict[str, Any]]:
    domains: dict[str, int | float | None] = {}
    candidates: list[Any] = []
    direct = payload.get("domains")
    if isinstance(direct, list):
        candidates.extend(direct)
    for wrapper in payload.get("timeseries_stats") or []:
        if not isinstance(wrapper, dict):
            continue
        stat = wrapper.get("timeseries_stat", wrapper)
        if isinstance(stat, dict) and isinstance(stat.get("domains"), list):
            candidates.extend(stat["domains"])
    for item in candidates:
        if not isinstance(item, dict):
            continue
        domain = _safe_domain(item.get("domain_name") or item.get("domain"))
        if not domain:
            continue
        total = _safe_number(item.get("total_events"))
        previous = domains.get(domain)
        if previous is None:
            domains[domain] = total
        elif total is not None:
            domains[domain] = float(previous) + float(total)
    rows = [
        {"domain_name": domain, "total_events": value}
        for domain, value in domains.items()
    ]
    rows.sort(
        key=lambda item: (
            item.get("total_events") is not None,
            float(item.get("total_events") or 0),
            item["domain_name"],
        ),
        reverse=True,
    )
    return rows[:MAX_DOMAINS_PER_PIXEL]


def _parse_event_counts(payload: dict[str, Any]) -> dict[str, int | float]:
    counts: dict[str, float] = {}

    def add(event_type: Any, value: Any) -> None:
        event = str(event_type or "").strip().upper()
        number = _safe_number(value)
        if not event or number is None:
            return
        counts[event] = counts.get(event, 0.0) + float(number)

    def visit(node: Any) -> None:
        if isinstance(node, dict):
            breakdown = node.get("event_type_breakdown")
            if isinstance(breakdown, dict):
                for event_type, value in breakdown.items():
                    add(event_type, value)
            event_type = node.get("event_type")
            if event_type:
                add(
                    event_type,
                    node.get("total_events")
                    if node.get("total_events") is not None
                    else node.get("count"),
                )
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)

    visit(payload)
    return {
        key: int(value) if float(value).is_integer() else value
        for key, value in sorted(counts.items())
    }


def _parse_quality_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for wrapper in payload.get("event_quality_scores") or []:
        if not isinstance(wrapper, dict):
            continue
        status = str(wrapper.get("sub_request_status") or "SUCCESS").upper()
        if "FAIL" in status or "ERROR" in status:
            continue
        score = wrapper.get("event_quality_score", wrapper)
        if not isinstance(score, dict):
            continue
        recommendations = [
            safe
            for safe in (
                _safe_recommendation(item)
                for item in (score.get("recommendations") or [])[:30]
            )
            if safe
        ]
        action_source = _safe_text(score.get("action_source"), limit=50) or "UNKNOWN"
        event_source = _safe_text(score.get("event_source"), limit=50) or "UNKNOWN"
        event_type = _safe_text(score.get("event_type"), limit=80) or "UNKNOWN"
        rows.append(
            {
                "action_source": action_source.upper(),
                "event_source": event_source.upper(),
                "event_type": event_type.upper(),
                "recommendations": recommendations,
            }
        )
    return rows


async def _upsert_asset(
    context: SnapchatSyncContext,
    *,
    account: dict[str, Any],
    pixel: dict[str, Any],
    domains: list[dict[str, Any]],
    total_events: int | float | None,
    diagnostics_status: str,
) -> None:
    pixel_id = str(pixel.get("id") or "").strip()
    account_id = str(account.get("ad_account_id") or "").strip()
    now_iso = context.now_iso()
    await _collection(context.db, TRACKING_ASSET_COLLECTION).update_one(
        {"user_id": context.user_id, "pixel_id": pixel_id},
        {
            "$set": {
                "user_id": context.user_id,
                "provider": SNAPCHAT_PROVIDER_ID,
                "pixel_id": pixel_id,
                "ad_account_id": account_id,
                "mezan_integration_account_id": account.get("mezan_integration_account_id"),
                "organization_id": pixel.get("organization_id") or account.get("organization_id"),
                "display_name": pixel.get("name") or pixel_id,
                "status": pixel.get("status"),
                "effective_status": pixel.get("effective_status"),
                "pixel_created_at": pixel.get("created_at"),
                "pixel_updated_at": pixel.get("updated_at"),
                "domain_count": len(domains),
                "domains": domains,
                "total_events_7d": total_events,
                "has_event_data": bool(total_events and total_events > 0),
                "diagnostics_status": diagnostics_status,
                "source_mode": TRACKING_SOURCE_MODE,
                "last_observed_at": now_iso,
                "updated_at": now_iso,
            },
            "$addToSet": {"ad_account_ids": account_id},
            "$setOnInsert": {"created_at": now_iso},
        },
        upsert=True,
    )


async def _upsert_diagnostic(
    context: SnapchatSyncContext,
    *,
    pixel_id: str,
    account_id: str,
    diagnostic_type: str,
    diagnostic_key: str,
    document: dict[str, Any],
) -> None:
    now_iso = context.now_iso()
    await _collection(context.db, EVENT_DIAGNOSTIC_COLLECTION).update_one(
        {
            "user_id": context.user_id,
            "pixel_id": pixel_id,
            "diagnostic_type": diagnostic_type,
            "diagnostic_key": diagnostic_key,
        },
        {
            "$set": {
                "user_id": context.user_id,
                "provider": SNAPCHAT_PROVIDER_ID,
                "pixel_id": pixel_id,
                "ad_account_id": account_id,
                "diagnostic_type": diagnostic_type,
                "diagnostic_key": diagnostic_key,
                **document,
                "source_mode": TRACKING_SOURCE_MODE,
                "last_observed_at": now_iso,
                "updated_at": now_iso,
            },
            "$setOnInsert": {"created_at": now_iso},
        },
        upsert=True,
    )


class SnapchatTrackingDiagnostics:
    def __init__(self, db: Any, *, now: Callable[[], datetime] = _utcnow) -> None:
        self.db = db
        self.now = now
        self.context: SnapchatSyncContext | None = None

    async def _accounts(self, user_id: str) -> list[dict[str, Any]]:
        cursor = _collection(self.db, "mezan_integration_accounts_v2").find(
            {
                "user_id": user_id,
                "provider": SNAPCHAT_PROVIDER_ID,
                "connection_provenance": "api_connection",
                "connection_status": "connected",
            },
            {
                "_id": 0,
                "mezan_integration_account_id": 1,
                "external_account_id": 1,
                "ad_account_id": 1,
                "display_name": 1,
                "organization_id": 1,
                "organization_name": 1,
            },
        )
        if hasattr(cursor, "sort"):
            cursor = cursor.sort("display_name", 1)
        rows = await cursor.to_list(length=21) if hasattr(cursor, "to_list") else [row async for row in cursor]
        accounts = []
        for row in rows[:20]:
            account_id = str(row.get("ad_account_id") or row.get("external_account_id") or "").strip()
            if account_id:
                accounts.append({**row, "ad_account_id": account_id})
        if not accounts:
            raise SnapchatNativeSyncError(
                "snapchat_accounts_not_selected",
                "No connected Snapchat V2 ad accounts were found.",
                status_code=409,
            )
        return accounts

    async def _diagnose_pixel(
        self,
        client: httpx.AsyncClient,
        access_token: str,
        *,
        account: dict[str, Any],
        pixel: dict[str, Any],
        start: datetime,
        end: datetime,
    ) -> dict[str, Any]:
        assert self.context is not None
        pixel_id = str(pixel.get("id") or "").strip()
        headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
        errors: list[dict[str, str]] = []
        domains: list[dict[str, Any]] = []
        diagnostic_rows = 0
        recommendations_count = 0
        bad_recommendations = 0

        try:
            domain_payload = await _optional_get_json(
                self.context,
                client,
                f"{SNAPCHAT_API_BASE}/pixels/{pixel_id}/domains/stats",
                headers=headers,
            )
            domains = _parse_domains(domain_payload)
        except _OptionalEndpointError as exc:
            errors.append({"kind": "domains", "error": exc.code})

        for domain in domains:
            event_counts: dict[str, int | float] = {}
            stats_status = "available"
            try:
                stats_payload = await _optional_get_json(
                    self.context,
                    client,
                    f"{SNAPCHAT_API_BASE}/pixels/{pixel_id}/stats",
                    headers=headers,
                    params={
                        "start_time": start.isoformat(timespec="seconds"),
                        "end_time": end.isoformat(timespec="seconds"),
                        "granularity": "DAY",
                        "domain": domain["domain_name"],
                        "fields": "event_type",
                    },
                )
                event_counts = _parse_event_counts(stats_payload)
            except _OptionalEndpointError as exc:
                stats_status = "unavailable"
                errors.append({"kind": "pixel_stats", "error": exc.code})
            domain["event_counts"] = event_counts
            domain["stats_status"] = stats_status
            await _upsert_diagnostic(
                self.context,
                pixel_id=pixel_id,
                account_id=account["ad_account_id"],
                diagnostic_type="domain_event_stats",
                diagnostic_key=domain["domain_name"],
                document={
                    "domain_name": domain["domain_name"],
                    "total_events": domain.get("total_events"),
                    "event_counts": event_counts,
                    "data_status": stats_status,
                    "window_start": start.isoformat(),
                    "window_end": end.isoformat(),
                },
            )
            diagnostic_rows += 1

        quality_rows: list[dict[str, Any]] = []
        try:
            quality_payload = await _optional_get_json(
                self.context,
                client,
                f"{SNAPCHAT_API_BASE}/pixels/{pixel_id}/event_quality_scores",
                headers=headers,
                params={"locale": "ar"},
            )
            quality_rows = _parse_quality_rows(quality_payload)
        except _OptionalEndpointError as exc:
            errors.append({"kind": "signal_readiness", "error": exc.code})

        for row in quality_rows:
            recommendations = row["recommendations"]
            recommendations_count += len(recommendations)
            bad_recommendations += sum(
                str(item.get("score") or "").upper() == "BAD"
                or str(item.get("priority") or "").upper() in {"P0", "P1"}
                for item in recommendations
            )
            key = f"{row['action_source']}:{row['event_source']}:{row['event_type']}"
            await _upsert_diagnostic(
                self.context,
                pixel_id=pixel_id,
                account_id=account["ad_account_id"],
                diagnostic_type="signal_readiness",
                diagnostic_key=key,
                document={
                    "action_source": row["action_source"],
                    "event_source": row["event_source"],
                    "event_type": row["event_type"],
                    "recommendations": recommendations,
                    "recommendation_count": len(recommendations),
                    "data_status": "available",
                },
            )
            diagnostic_rows += 1

        totals = [
            _safe_number(domain.get("total_events"))
            for domain in domains
            if _safe_number(domain.get("total_events")) is not None
        ]
        total_events = sum(float(value) for value in totals) if totals else None
        if total_events is not None and float(total_events).is_integer():
            total_events = int(total_events)
        status = "complete" if not errors else "partial"
        await _upsert_asset(
            self.context,
            account=account,
            pixel=pixel,
            domains=domains,
            total_events=total_events,
            diagnostics_status=status,
        )
        return {
            "pixel_id": pixel_id,
            "status": status,
            "domains_observed": len(domains),
            "diagnostics_saved": diagnostic_rows,
            "recommendations_count": recommendations_count,
            "bad_recommendations": bad_recommendations,
            "errors": errors,
        }

    async def run(self, user_id: str, payload: SnapchatTrackingDiagnosticsInput) -> dict[str, Any]:
        await ensure_snapchat_tracking_indexes(self.db)
        accounts = await self._accounts(user_id)
        self.context = SnapchatSyncContext(self.db, user_id, now=self.now)
        access_token = await self.context.access_token()
        end = self.now().astimezone(timezone.utc)
        start = end - timedelta(days=payload.days)
        items: list[dict[str, Any]] = []
        all_errors: list[dict[str, str]] = []
        pixels_total = 0
        pixels_complete = 0
        domains_observed = 0
        diagnostics_saved = 0
        recommendations_count = 0
        bad_recommendations = 0
        accounts_complete = 0

        async with httpx.AsyncClient(timeout=35.0) as client:
            for account in accounts:
                account_errors: list[dict[str, str]] = []
                pixel_summaries: list[dict[str, Any]] = []
                try:
                    pixels, list_errors = await _list_pixels(
                        self.context,
                        client,
                        access_token,
                        account["ad_account_id"],
                    )
                    account_errors.extend(list_errors)
                    if pixels_total + len(pixels) > MAX_PIXELS_TOTAL:
                        allowed = max(0, MAX_PIXELS_TOTAL - pixels_total)
                        pixels = pixels[:allowed]
                        account_errors.append({"kind": "pixels", "error": "pixel_global_limit_reached"})
                    for pixel in pixels:
                        summary = await self._diagnose_pixel(
                            client,
                            access_token,
                            account=account,
                            pixel=pixel,
                            start=start,
                            end=end,
                        )
                        pixel_summaries.append(summary)
                        pixels_total += 1
                        pixels_complete += int(summary["status"] == "complete")
                        domains_observed += int(summary["domains_observed"])
                        diagnostics_saved += int(summary["diagnostics_saved"])
                        recommendations_count += int(summary["recommendations_count"])
                        bad_recommendations += int(summary["bad_recommendations"])
                        account_errors.extend(summary["errors"])
                except SnapchatNativeSyncError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    account_errors.append({"kind": "account", "error": type(exc).__name__})

                account_complete = not account_errors
                accounts_complete += int(account_complete)
                all_errors.extend(account_errors)
                now_iso = self.context.now_iso()
                await _collection(self.db, "mezan_integration_accounts_v2").update_one(
                    {
                        "user_id": user_id,
                        "provider": SNAPCHAT_PROVIDER_ID,
                        "external_account_id": account["ad_account_id"],
                    },
                    {"$set": {
                        "tracking_pixel_count": len(pixel_summaries),
                        "tracking_diagnostics_status": "complete" if account_complete else "partial",
                        "tracking_last_checked_at": now_iso,
                        "tracking_recommendations_count": sum(
                            int(item["recommendations_count"]) for item in pixel_summaries
                        ),
                        "last_observed_at": now_iso,
                    }},
                )
                items.append({
                    "ad_account_id": account["ad_account_id"],
                    "display_name": account.get("display_name"),
                    "status": "complete" if account_complete else "partial",
                    "pixels_found": len(pixel_summaries),
                    "pixels": pixel_summaries,
                    "errors": account_errors,
                })

        return {
            "provider": SNAPCHAT_PROVIDER_ID,
            "status": "complete" if accounts_complete == len(accounts) else "partial",
            "date_from": start.date().isoformat(),
            "date_to": end.date().isoformat(),
            "accounts_attempted": len(accounts),
            "accounts_complete": accounts_complete,
            "pixels_found": pixels_total,
            "pixels_complete": pixels_complete,
            "domains_observed": domains_observed,
            "diagnostics_saved": diagnostics_saved,
            "recommendations_count": recommendations_count,
            "bad_recommendations": bad_recommendations,
            "errors_count": len(all_errors),
            "errors": all_errors[:200],
            "errors_truncated": len(all_errors) > 200,
            "items": items,
            "provider_calls": self.context.provider_calls,
            "source_mode": TRACKING_SOURCE_MODE,
            "source_only": True,
            "provider_write_reached": False,
            "event_write_reached": False,
            "campaign_write_reached": False,
            "accounting_write_reached": False,
            "qoyod_write_reached": False,
            "fetched_at": self.context.now_iso(),
        }


async def _insert_error(
    db: Any,
    *,
    user_id: str,
    run_id: str,
    code: str,
    message: str,
    occurred_at: str,
    retryable: bool,
) -> str:
    error_id = str(uuid.uuid4())
    await _collection(db, "mezan_integration_errors_v2").insert_one({
        "error_id": error_id,
        "user_id": user_id,
        "provider": SNAPCHAT_PROVIDER_ID,
        "code": code,
        "message": message,
        "occurred_at": occurred_at,
        "retryable": retryable,
        "source_mode": TRACKING_SOURCE_MODE,
        "run_id": run_id,
    })
    return error_id


async def execute_snapchat_tracking_diagnostics(
    db: Any,
    user_id: str,
    payload: SnapchatTrackingDiagnosticsInput,
    *,
    now: Callable[[], datetime] = _utcnow,
) -> dict[str, Any]:
    await ensure_snapchat_tracking_indexes(db)
    runs = _collection(db, "mezan_integration_sync_runs_v2")
    now_value = now().astimezone(timezone.utc)
    started_at = _iso(now_value)
    running = await runs.find_one(
        {
            "user_id": user_id,
            "provider": SNAPCHAT_PROVIDER_ID,
            "run_type": "tracking_diagnostics",
            "status": "running",
        },
        {"_id": 0, "run_id": 1, "lock_expires_at": 1},
    )
    if running:
        expiry = _parse_datetime(running.get("lock_expires_at"))
        if not expiry or expiry > now_value:
            conflict = SnapchatNativeSyncError(
                "snapchat_tracking_diagnostics_in_progress",
                "Snapchat tracking diagnostics are already running.",
                status_code=409,
                retryable=True,
            )
            conflict.run_id = running.get("run_id")
            raise conflict
        await runs.update_one(
            {"user_id": user_id, "run_id": running.get("run_id"), "status": "running"},
            {"$set": {
                "status": "failed",
                "finished_at": started_at,
                "error": {"code": "stale_tracking_lock_recovered"},
            }},
        )

    fingerprint = hashlib.sha256(
        f"{user_id}:{payload.days}:{payload.idempotency_key or ''}:snap-tracking-v2".encode()
    ).hexdigest()
    prior = await runs.find_one(
        {
            "user_id": user_id,
            "provider": SNAPCHAT_PROVIDER_ID,
            "run_type": "tracking_diagnostics",
            "idempotency_key": fingerprint,
            "status": {"$in": ["complete", "partial"]},
            "finished_at": {"$gte": _iso(now_value - TRACKING_IDEMPOTENCY_WINDOW)},
        },
        {"_id": 0, "summary": 1},
        sort=[("finished_at", -1)],
    )
    if prior and isinstance(prior.get("summary"), dict):
        return dict(prior["summary"])

    run_id = str(uuid.uuid4())
    await runs.insert_one({
        "run_id": run_id,
        "user_id": user_id,
        "provider": SNAPCHAT_PROVIDER_ID,
        "run_type": "tracking_diagnostics",
        "status": "running",
        "started_at": started_at,
        "finished_at": None,
        "lock_expires_at": _iso(now_value + TRACKING_LOCK_TTL),
        "idempotency_key": fingerprint,
        "source_mode": TRACKING_SOURCE_MODE,
        "summary": {"requested_days": payload.days},
        "error": None,
    })

    try:
        engine = await SnapchatTrackingDiagnostics(db, now=now).run(user_id, payload)
    except SnapchatNativeSyncError as exc:
        finished_at = _iso(now())
        error_id = await _insert_error(
            db,
            user_id=user_id,
            run_id=run_id,
            code=exc.code,
            message=exc.message,
            occurred_at=finished_at,
            retryable=exc.retryable,
        )
        await runs.update_one(
            {"user_id": user_id, "run_id": run_id},
            {"$set": {
                "status": "failed",
                "finished_at": finished_at,
                "summary": {
                    "run_id": run_id,
                    "provider": SNAPCHAT_PROVIDER_ID,
                    "status": "failed",
                    "accounts_attempted": 0,
                    "accounts_complete": 0,
                    "pixels_found": 0,
                    "pixels_complete": 0,
                    "domains_observed": 0,
                    "diagnostics_saved": 0,
                    "recommendations_count": 0,
                    "errors_count": 1,
                    "source_only": True,
                    "provider_write_reached": False,
                    "event_write_reached": False,
                    "accounting_write_reached": False,
                    "qoyod_write_reached": False,
                },
                "error": {"error_id": error_id, "code": exc.code},
            }},
        )
        exc.run_id = run_id
        raise

    finished_at = _iso(now())
    response = {
        "run_id": run_id,
        "provider": SNAPCHAT_PROVIDER_ID,
        "status": engine["status"],
        "date_from": engine["date_from"],
        "date_to": engine["date_to"],
        "accounts_attempted": engine["accounts_attempted"],
        "accounts_complete": engine["accounts_complete"],
        "pixels_found": engine["pixels_found"],
        "pixels_complete": engine["pixels_complete"],
        "domains_observed": engine["domains_observed"],
        "diagnostics_saved": engine["diagnostics_saved"],
        "recommendations_count": engine["recommendations_count"],
        "errors_count": engine["errors_count"],
        "source_only": True,
        "provider_write_reached": False,
        "event_write_reached": False,
        "accounting_write_reached": False,
        "qoyod_write_reached": False,
    }
    run_summary = {
        **response,
        "bad_recommendations": engine["bad_recommendations"],
        "provider_calls": engine["provider_calls"],
        "errors_truncated": engine["errors_truncated"],
        "legacy_collection_read": False,
        "legacy_collection_write": False,
        "campaign_write_reached": False,
    }
    partial_error_id = None
    if response["status"] == "partial":
        partial_error_id = await _insert_error(
            db,
            user_id=user_id,
            run_id=run_id,
            code="snapchat_tracking_diagnostics_partial",
            message=(
                "Snapchat tracking diagnostics completed with bounded unavailable endpoints; "
                "unknown values were not converted to zero."
            ),
            occurred_at=finished_at,
            retryable=True,
        )
    await runs.update_one(
        {"user_id": user_id, "run_id": run_id},
        {"$set": {
            "status": response["status"],
            "finished_at": finished_at,
            "summary": run_summary,
            "error": (
                {"error_id": partial_error_id, "code": "snapchat_tracking_diagnostics_partial"}
                if partial_error_id
                else None
            ),
        }},
    )
    await _collection(db, "mezan_integrations_v2").update_one(
        {"user_id": user_id, "provider": SNAPCHAT_PROVIDER_ID},
        {"$set": {
            "tracking_last_checked_at": finished_at,
            "tracking_diagnostics_status": response["status"],
            "tracking_pixel_count": response["pixels_found"],
            "tracking_domains_observed": response["domains_observed"],
            "tracking_recommendations_count": response["recommendations_count"],
            "tracking_bad_recommendations": engine["bad_recommendations"],
            "updated_at": finished_at,
        }},
        upsert=True,
    )
    return response


__all__ = [
    "EVENT_DIAGNOSTIC_COLLECTION",
    "TRACKING_ASSET_COLLECTION",
    "TRACKING_SOURCE_MODE",
    "SnapchatTrackingDiagnostics",
    "SnapchatTrackingDiagnosticsInput",
    "ensure_snapchat_tracking_indexes",
    "execute_snapchat_tracking_diagnostics",
]
