"""Native, read-only Snapchat entity and performance orchestration for Mezan V2."""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

import httpx

from .snapchat_native_data_common import (
    BUSINESS_TIMEZONE,
    MAX_SYNC_ACCOUNTS,
    NATIVE_RESPONSE_KEYS,
    SNAPCHAT_NATIVE_SYNC_ENABLED_ENV,
    SNAPCHAT_NATIVE_SYNC_IDEMPOTENCY_WINDOW,
    SNAPCHAT_NATIVE_SYNC_LOCK_TTL,
    SNAPCHAT_NATIVE_SYNC_SOURCE_MODE,
    SNAPCHAT_PROVIDER_ID,
    SnapchatNativeSyncError,
    SnapchatNativeSyncInput,
    SnapchatSyncContext,
    _collection,
    _iso,
    _parse_datetime,
    _timezone,
    _utcnow,
    _start_sync_run_heartbeat,
    _stop_sync_run_heartbeat,
    ensure_snapchat_native_sync_indexes,
    enumerate_native_sync_dates,
    snapchat_native_sync_enabled,
)
from .snapchat_native_entities_sync import sync_snapchat_entities
from .snapchat_native_performance_sync import sync_snapchat_performance

CAPABILITY_EVIDENCE = (
    "campaigns.read", "budgets.read", "ads.read", "creatives.read",
    "audiences.read", "insights.read", "conversions.read",
)


class SnapchatNativeDataSync:
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
                "_id": 0, "mezan_integration_account_id": 1,
                "external_account_id": 1, "ad_account_id": 1,
                "display_name": 1, "currency": 1, "timezone": 1,
                "last_sync_at": 1,
            },
        )
        if hasattr(cursor, "sort"):
            cursor = cursor.sort("display_name", 1)
        if hasattr(cursor, "limit"):
            cursor = cursor.limit(MAX_SYNC_ACCOUNTS + 1)
        rows = await cursor.to_list(length=MAX_SYNC_ACCOUNTS + 1) if hasattr(cursor, "to_list") else [row async for row in cursor]
        output = []
        for row in rows:
            account_id = str(row.get("ad_account_id") or row.get("external_account_id") or "").strip()
            if account_id:
                output.append({**row, "ad_account_id": account_id})
        if not output:
            raise SnapchatNativeSyncError(
                "snapchat_accounts_not_selected",
                "No connected Snapchat V2 ad accounts were found.", status_code=409,
            )
        if len(output) > MAX_SYNC_ACCOUNTS:
            raise SnapchatNativeSyncError(
                "snapchat_account_limit_exceeded",
                f"Snapchat native sync supports at most {MAX_SYNC_ACCOUNTS} accounts per run.",
                status_code=409,
            )
        return output

    async def _sync_account(
        self, client: httpx.AsyncClient, access_token: str, *, user_id: str,
        account: dict[str, Any], start_date, end_date,
    ) -> dict[str, Any]:
        assert self.context is not None
        entities_saved, entity_counts, errors = await sync_snapchat_entities(
            self.context, client, access_token, account
        )
        performance_saved = 0
        try:
            performance_saved, performance_errors = await sync_snapchat_performance(
                self.context, client, access_token, account,
                start_date=start_date, end_date=end_date,
            )
            errors.extend(performance_errors)
        except SnapchatNativeSyncError as exc:
            if exc.code == "snapchat_needs_reauth":
                raise
            errors.append({"kind": "performance", "error": exc.code})
        complete = not errors
        now_iso = self.context.now_iso()
        await _collection(self.db, "mezan_integration_accounts_v2").update_one(
            {
                "user_id": user_id, "provider": SNAPCHAT_PROVIDER_ID,
                "external_account_id": account["ad_account_id"],
            },
            {"$set": {
                "entity_counts": entity_counts,
                "performance_rows_saved": performance_saved,
                "has_data": bool(entities_saved or performance_saved),
                "last_sync_at": now_iso if complete else account.get("last_sync_at"),
                "data_delay_minutes": 0 if complete else None,
                "health_score": 100 if complete else 70,
                "source_mode": SNAPCHAT_NATIVE_SYNC_SOURCE_MODE,
                "connection_status": "connected",
                "connection_provenance": "api_connection",
                "last_observed_at": now_iso,
            }},
        )
        return {
            "ad_account_id": account["ad_account_id"],
            "display_name": account.get("display_name"),
            "name": account.get("display_name"),
            "currency": account.get("currency"),
            "currency_native": account.get("currency"),
            "timezone": account.get("timezone"),
            "rows_saved": entities_saved + performance_saved,
            "entities_saved": entities_saved,
            "performance_rows_saved": performance_saved,
            "entity_counts": entity_counts,
            "errors": len(errors),
            "error_items": errors[:50],
            "complete": complete,
        }

    async def run(self, user_id: str, payload: SnapchatNativeSyncInput) -> dict[str, Any]:
        if not snapchat_native_sync_enabled():
            raise SnapchatNativeSyncError(
                "snapchat_native_sync_disabled",
                "Snapchat native data sync is temporarily disabled.", status_code=503,
            )
        await ensure_snapchat_native_sync_indexes(self.db)
        dates = enumerate_native_sync_dates(
            payload,
            today=self.now().astimezone(_timezone(BUSINESS_TIMEZONE)).date(),
        )
        accounts = await self._accounts(user_id)
        self.context = SnapchatSyncContext(self.db, user_id, now=self.now)
        access_token = await self.context.access_token()
        summaries: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        async with httpx.AsyncClient(timeout=35.0) as client:
            for account in accounts:
                try:
                    summary = await self._sync_account(
                        client, access_token, user_id=user_id, account=account,
                        start_date=dates[0], end_date=dates[-1],
                    )
                except SnapchatNativeSyncError as exc:
                    if exc.code == "snapchat_needs_reauth":
                        exc.result.update({
                            "date_from": dates[0].isoformat(),
                            "date_to": dates[-1].isoformat(),
                            "accounts_synced": len(summaries),
                            "accounts_complete": sum(bool(item.get("complete")) for item in summaries),
                            "rows_saved": sum(int(item.get("rows_saved") or 0) for item in summaries),
                            "errors_count": len(errors) + 1,
                        })
                        raise
                    summary = {
                        "ad_account_id": account["ad_account_id"],
                        "display_name": account.get("display_name"),
                        "name": account.get("display_name"),
                        "currency": account.get("currency"),
                        "currency_native": account.get("currency"),
                        "timezone": account.get("timezone"),
                        "rows_saved": 0, "entities_saved": 0,
                        "performance_rows_saved": 0, "entity_counts": {},
                        "errors": 1,
                        "error_items": [{"kind": "account", "error": exc.code}],
                        "complete": False,
                    }
                summaries.append(summary)
                errors.extend(summary.get("error_items") or [])
        rows_saved = sum(int(item.get("rows_saved") or 0) for item in summaries)
        complete_accounts = sum(bool(item.get("complete")) for item in summaries)
        if rows_saved == 0:
            raise SnapchatNativeSyncError(
                "snapchat_analytics_no_rows",
                "Snapchat returned no usable native entity or performance rows.",
                status_code=502, retryable=True,
                result={
                    "date_from": dates[0].isoformat(), "date_to": dates[-1].isoformat(),
                    "accounts_synced": len(summaries),
                    "accounts_complete": complete_accounts,
                    "rows_saved": 0, "errors_count": len(errors) or 1,
                },
            )
        return {
            "provider": SNAPCHAT_PROVIDER_ID,
            "sync_status": "complete" if complete_accounts == len(summaries) else "partial",
            "source_mode": SNAPCHAT_NATIVE_SYNC_SOURCE_MODE,
            "connection_provenance": "api_connection",
            "capability_evidence": list(CAPABILITY_EVIDENCE),
            "accounts_synced": len(summaries),
            "accounts_complete": complete_accounts,
            "rows_saved": rows_saved,
            "days_requested": len(dates),
            "date_from": dates[0].isoformat(), "date_to": dates[-1].isoformat(),
            "items": summaries, "errors": errors[:200],
            "errors_count": len(errors), "errors_truncated": len(errors) > 200,
            "needs_reauth": False,
            "provider_calls": self.context.provider_calls,
            "business_timezone": BUSINESS_TIMEZONE,
            "source_only": True,
            "provider_write_reached": False, "campaign_write_reached": False,
            "accounting_write_reached": False, "qoyod_write_reached": False,
            "fetched_at": self.context.now_iso(),
        }


async def _insert_error(db: Any, *, user_id: str, run_id: str, code: str,
                        message: str, occurred_at: str, retryable: bool) -> str:
    error_id = str(uuid.uuid4())
    await _collection(db, "mezan_integration_errors_v2").insert_one({
        "error_id": error_id, "user_id": user_id, "provider": SNAPCHAT_PROVIDER_ID,
        "code": code, "message": message, "occurred_at": occurred_at,
        "retryable": retryable, "source_mode": SNAPCHAT_NATIVE_SYNC_SOURCE_MODE,
        "run_id": run_id,
    })
    return error_id


async def execute_snapchat_native_sync(
    db: Any, user_id: str, payload: SnapchatNativeSyncInput,
    *, now: Callable[[], datetime] = _utcnow,
    parent_run_id: str | None = None,
) -> dict[str, Any]:
    runs = _collection(db, "mezan_integration_sync_runs_v2")
    now_value = now().astimezone(timezone.utc)
    started_at = _iso(now_value)
    running = await runs.find_one(
        {"user_id": user_id, "provider": SNAPCHAT_PROVIDER_ID,
         "run_type": "analytics_refresh", "status": "running"},
        {"_id": 0, "run_id": 1, "lock_expires_at": 1},
    )
    if running:
        expiry = _parse_datetime(running.get("lock_expires_at"))
        if not expiry or expiry > now_value:
            conflict = SnapchatNativeSyncError(
                "snapchat_analytics_sync_in_progress",
                "A Snapchat native data sync is already running.",
                status_code=409, retryable=True,
            )
            conflict.run_id = running.get("run_id")
            raise conflict
        await runs.update_one(
            {"user_id": user_id, "run_id": running.get("run_id"), "status": "running"},
            {"$set": {"status": "failed", "finished_at": started_at,
                      "error": {"code": "stale_sync_lock_recovered"}}},
        )
    dates = enumerate_native_sync_dates(
        payload, today=now_value.astimezone(_timezone(BUSINESS_TIMEZONE)).date()
    )
    fingerprint = hashlib.sha256(
        f"{user_id}:{dates[0]}:{dates[-1]}:{payload.idempotency_key or ''}:snap-native-v2".encode()
    ).hexdigest()
    prior = await runs.find_one(
        {"user_id": user_id, "provider": SNAPCHAT_PROVIDER_ID,
         "run_type": "analytics_refresh", "idempotency_key": fingerprint,
         "status": {"$in": ["complete", "partial"]},
         "finished_at": {"$gte": _iso(now_value - SNAPCHAT_NATIVE_SYNC_IDEMPOTENCY_WINDOW)}},
        {"_id": 0, "summary": 1}, sort=[("finished_at", -1)],
    )
    if prior and isinstance(prior.get("summary"), dict):
        replay = {key: prior["summary"].get(key) for key in NATIVE_RESPONSE_KEYS}
        if replay.get("status") in {"complete", "partial"}:
            return replay
    run_id = str(uuid.uuid4())
    run_document = {
        "run_id": run_id, "user_id": user_id, "provider": SNAPCHAT_PROVIDER_ID,
        "run_type": "analytics_refresh", "status": "running",
        "started_at": started_at, "finished_at": None,
        "worker_started_at": started_at,
        "worker_heartbeat_at": started_at,
        "lock_expires_at": _iso(now_value + SNAPCHAT_NATIVE_SYNC_LOCK_TTL),
        "idempotency_key": fingerprint,
        "source_mode": SNAPCHAT_NATIVE_SYNC_SOURCE_MODE,
        "summary": {"requested_days": payload.days,
                    "requested_from": payload.from_date, "requested_to": payload.to_date},
        "error": None,
    }
    if parent_run_id:
        run_document["parent_run_id"] = parent_run_id
    await runs.insert_one(run_document)
    heartbeat = _start_sync_run_heartbeat(
        runs,
        {"user_id": user_id, "run_id": run_id},
        now=now,
    )
    try:
        try:
            engine = await SnapchatNativeDataSync(
                db, now=now
            ).run(user_id, payload)
        finally:
            await _stop_sync_run_heartbeat(heartbeat)
    except SnapchatNativeSyncError as exc:
        finished_at = _iso(now())
        failure = exc.result or {}
        error_id = await _insert_error(
            db, user_id=user_id, run_id=run_id, code=exc.code,
            message=exc.message, occurred_at=finished_at, retryable=exc.retryable,
        )
        summary = {
            "run_id": run_id, "provider": SNAPCHAT_PROVIDER_ID, "status": "failed",
            "date_from": failure.get("date_from") or dates[0].isoformat(),
            "date_to": failure.get("date_to") or dates[-1].isoformat(),
            "accounts_attempted": int(failure.get("accounts_synced") or 0),
            "accounts_complete": int(failure.get("accounts_complete") or 0),
            "rows_saved": int(failure.get("rows_saved") or 0),
            "errors_count": int(failure.get("errors_count") or 1),
            "source_only": True, "accounting_write_reached": False,
            "qoyod_write_reached": False,
        }
        await runs.update_one(
            {"user_id": user_id, "run_id": run_id},
            {"$set": {"status": "failed", "finished_at": finished_at,
                      "summary": summary, "error": {"error_id": error_id, "code": exc.code}}},
        )
        needs_reauth = exc.code == "snapchat_needs_reauth" or bool(failure.get("needs_reauth"))
        connection_status = "needs_reauth" if needs_reauth else "error"
        await _collection(db, "mezan_integration_health_v2").insert_one({
            "user_id": user_id, "provider": SNAPCHAT_PROVIDER_ID,
            "health_status": "unhealthy", "health_score": 20,
            "data_quality": "unavailable", "connection_status": connection_status,
            "connection_provenance": "api_connection", "data_delay_minutes": None,
            "checked_at": finished_at, "source_mode": SNAPCHAT_NATIVE_SYNC_SOURCE_MODE,
            "run_id": run_id,
        })
        await _collection(db, "mezan_integrations_v2").update_one(
            {"user_id": user_id, "provider": SNAPCHAT_PROVIDER_ID},
            {"$set": {"connection_status": connection_status,
                      "connection_provenance": "api_connection",
                      "source_mode": SNAPCHAT_NATIVE_SYNC_SOURCE_MODE,
                      "data_quality": "unavailable", "checked_at": finished_at,
                      "updated_at": finished_at}}, upsert=True,
        )
        exc.run_id = run_id
        raise

    finished_at = _iso(now())
    status = "complete" if engine.get("sync_status") == "complete" else "partial"
    response = {
        "run_id": run_id, "provider": SNAPCHAT_PROVIDER_ID, "status": status,
        "date_from": engine.get("date_from"), "date_to": engine.get("date_to"),
        "accounts_attempted": int(engine.get("accounts_synced") or 0),
        "accounts_complete": int(engine.get("accounts_complete") or 0),
        "rows_saved": int(engine.get("rows_saved") or 0),
        "errors_count": int(engine.get("errors_count") or 0),
        "source_only": True, "accounting_write_reached": False,
        "qoyod_write_reached": False,
    }
    run_summary = {
        **response, "provider_calls": int(engine.get("provider_calls") or 0),
        "business_timezone": engine.get("business_timezone"),
        "errors_truncated": bool(engine.get("errors_truncated")),
        "entity_counts": {item.get("ad_account_id"): item.get("entity_counts")
                          for item in engine.get("items") or [] if item.get("ad_account_id")},
        "legacy_collection_read": False, "legacy_collection_write": False,
        "provider_write_reached": False, "campaign_write_reached": False,
    }
    partial_error = None
    if status == "partial":
        partial_error = await _insert_error(
            db, user_id=user_id, run_id=run_id,
            code="snapchat_native_sync_partial",
            message=f"Snapchat native data sync completed with {response['errors_count']} bounded errors.",
            occurred_at=finished_at, retryable=True,
        )
    await runs.update_one(
        {"user_id": user_id, "run_id": run_id},
        {"$set": {"status": status, "finished_at": finished_at,
                  "summary": run_summary,
                  "error": ({"error_id": partial_error,
                             "code": "snapchat_native_sync_partial"}
                            if partial_error else None)}},
    )
    data_quality = "complete" if status == "complete" else "partial"
    await _collection(db, "mezan_integration_health_v2").insert_one({
        "user_id": user_id, "provider": SNAPCHAT_PROVIDER_ID,
        "health_status": "healthy" if status == "complete" else "degraded",
        "health_score": 100 if status == "complete" else 70,
        "data_quality": data_quality, "connection_status": "connected",
        "connection_provenance": "api_connection",
        "data_delay_minutes": 0 if status == "complete" else None,
        "checked_at": finished_at, "source_mode": SNAPCHAT_NATIVE_SYNC_SOURCE_MODE,
        "run_id": run_id,
    })
    patch: dict[str, Any] = {
        "user_id": user_id, "provider": SNAPCHAT_PROVIDER_ID,
        "connection_status": "connected", "connection_provenance": "api_connection",
        "source_mode": SNAPCHAT_NATIVE_SYNC_SOURCE_MODE,
        "data_quality": data_quality, "has_data": response["rows_saved"] > 0,
        "capability_evidence": list(engine.get("capability_evidence") or []),
        "checked_at": finished_at, "updated_at": finished_at,
    }
    if status == "complete" and response["rows_saved"] > 0:
        patch.update({"last_sync_at": finished_at, "data_delay_minutes": 0})
    await _collection(db, "mezan_integrations_v2").update_one(
        {"user_id": user_id, "provider": SNAPCHAT_PROVIDER_ID},
        {"$set": patch, "$setOnInsert": {"created_at": started_at}}, upsert=True,
    )
    return response


__all__ = [
    "CAPABILITY_EVIDENCE", "SNAPCHAT_NATIVE_SYNC_ENABLED_ENV", "SnapchatNativeDataSync",
    "SnapchatNativeSyncError", "SnapchatNativeSyncInput",
    "execute_snapchat_native_sync", "snapchat_native_sync_enabled",
]
