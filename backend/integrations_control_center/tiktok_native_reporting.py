"""Native read-only TikTok advertiser reporting for Mezan Integrations V2.

This module reads only the encrypted V2 OAuth credential and the advertiser
accounts discovered by the native TikTok connector. It never reads Make.com or
legacy TikTok collections and never writes campaigns, accounting, or Qoyod.
"""
from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable
from zoneinfo import ZoneInfo

import httpx
from pydantic import BaseModel, Field, model_validator

from .tiktok_oauth_security import (
    TIKTOK_CREDENTIALS_COLLECTION,
    TIKTOK_PROVIDER_ID,
    decrypt_tiktok_token,
    tiktok_oauth_configured,
)

TIKTOK_REPORT_URL = (
    "https://business-api.tiktok.com/open_api/v1.3/report/integrated/get/"
)
TIKTOK_REPORTING_COLLECTION = "mezan_tiktok_performance_daily_v2"
TIKTOK_REPORTING_SOURCE_MODE = "tiktok_marketing_reporting_v2"
TIKTOK_REPORTING_ENABLED_ENV = "TIKTOK_NATIVE_REPORTING_SYNC_ENABLED"
TIKTOK_USD_TO_SAR_RATE_ENV = "TIKTOK_USD_TO_SAR_RATE"
MAX_REPORTING_DAYS = 31
MAX_REPORTING_ACCOUNTS = 20


class TikTokReportingError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 400,
        retryable: bool = False,
        result: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.retryable = retryable
        self.result = result or {}


class TikTokReportingSyncInput(BaseModel):
    days: int = Field(default=30, ge=1, le=MAX_REPORTING_DAYS)
    from_date: str | None = None
    to_date: str | None = None

    @model_validator(mode="after")
    def validate_explicit_range(self):
        if bool(self.from_date) != bool(self.to_date):
            raise ValueError("from_date and to_date must be supplied together")
        if self.from_date and self.to_date:
            start = date.fromisoformat(self.from_date)
            end = date.fromisoformat(self.to_date)
            if end < start:
                raise ValueError("to_date must not be before from_date")
            if (end - start).days + 1 > MAX_REPORTING_DAYS:
                raise ValueError(
                    f"TikTok reporting supports at most {MAX_REPORTING_DAYS} days"
                )
        return self


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None = None) -> str:
    return (value or _utcnow()).astimezone(timezone.utc).isoformat()


def tiktok_reporting_enabled() -> bool:
    return os.environ.get(TIKTOK_REPORTING_ENABLED_ENV, "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _dates(payload: TikTokReportingSyncInput, today: date) -> list[date]:
    if payload.from_date and payload.to_date:
        start = date.fromisoformat(payload.from_date)
        end = date.fromisoformat(payload.to_date)
    else:
        end = today
        start = end - timedelta(days=payload.days - 1)
    return [start + timedelta(days=offset) for offset in range((end - start).days + 1)]


def _fx_to_sar(currency: str | None) -> tuple[float | None, str]:
    normalized = str(currency or "").strip().upper()
    if normalized == "SAR":
        return 1.0, "implicit_sar"
    if normalized == "USD":
        try:
            rate = float(os.environ.get(TIKTOK_USD_TO_SAR_RATE_ENV, "3.75"))
        except ValueError:
            rate = 0.0
        if rate > 0:
            return rate, "configured_usd_peg"
    return None, "missing"


def _provider_data(response: httpx.Response, operation: str) -> dict[str, Any]:
    if response.status_code == 401:
        raise TikTokReportingError(
            "tiktok_needs_reauth",
            "TikTok rejected the saved access token.",
            status_code=409,
        )
    if response.status_code == 429:
        raise TikTokReportingError(
            "tiktok_rate_limited",
            "TikTok temporarily rate-limited reporting requests.",
            status_code=429,
            retryable=True,
        )
    if response.status_code >= 400:
        raise TikTokReportingError(
            f"{operation}_http_{response.status_code}",
            "TikTok reporting returned an HTTP error.",
            status_code=502,
            retryable=response.status_code >= 500,
        )
    payload = response.json() or {}
    provider_code = payload.get("code")
    if provider_code not in (0, "0", None):
        if provider_code in (40103, 40105, "40103", "40105"):
            raise TikTokReportingError(
                "tiktok_needs_reauth",
                "TikTok rejected the saved access token.",
                status_code=409,
            )
        raise TikTokReportingError(
            f"{operation}_provider_{provider_code}",
            "TikTok reporting returned a provider error.",
            status_code=502,
            retryable=True,
        )
    data = payload.get("data")
    if not isinstance(data, dict):
        raise TikTokReportingError(
            f"{operation}_missing_data",
            "TikTok reporting returned no usable data object.",
            status_code=502,
            retryable=True,
        )
    return {**data, "_request_id": payload.get("request_id")}


async def ensure_tiktok_reporting_indexes(db: Any) -> None:
    await db[TIKTOK_REPORTING_COLLECTION].create_index(
        [("user_id", 1), ("ad_account_id", 1), ("date", 1)],
        unique=True,
        name="tiktok_performance_user_account_date_unique",
    )
    await db[TIKTOK_REPORTING_COLLECTION].create_index(
        [("user_id", 1), ("date", -1)],
        name="tiktok_performance_user_date",
    )


async def _credential(db: Any, user_id: str) -> str:
    row = await db[TIKTOK_CREDENTIALS_COLLECTION].find_one(
        {"user_id": user_id, "provider": TIKTOK_PROVIDER_ID},
        {"_id": 0, "access_token_ciphertext": 1},
    )
    token = decrypt_tiktok_token((row or {}).get("access_token_ciphertext"))
    if not token:
        raise TikTokReportingError(
            "tiktok_oauth_credential_missing",
            "No native TikTok OAuth credential is available.",
            status_code=409,
        )
    return token


async def _accounts(db: Any, user_id: str) -> list[dict[str, Any]]:
    cursor = db.mezan_integration_accounts_v2.find(
        {
            "user_id": user_id,
            "provider": TIKTOK_PROVIDER_ID,
            "connection_status": "connected",
            "connection_provenance": "api_connection",
        },
        {
            "_id": 0,
            "external_account_id": 1,
            "ad_account_id": 1,
            "display_name": 1,
            "currency": 1,
            "timezone": 1,
            "last_sync_at": 1,
        },
    )
    if hasattr(cursor, "sort"):
        cursor = cursor.sort("display_name", 1)
    if hasattr(cursor, "limit"):
        cursor = cursor.limit(MAX_REPORTING_ACCOUNTS + 1)
    rows = (
        await cursor.to_list(length=MAX_REPORTING_ACCOUNTS + 1)
        if hasattr(cursor, "to_list")
        else [row async for row in cursor]
    )
    output = []
    for row in rows:
        account_id = str(
            row.get("ad_account_id") or row.get("external_account_id") or ""
        ).strip()
        if account_id:
            output.append({**row, "ad_account_id": account_id})
    if not output:
        raise TikTokReportingError(
            "tiktok_reporting_accounts_missing",
            "No connected TikTok advertiser accounts were found.",
            status_code=409,
        )
    if len(output) > MAX_REPORTING_ACCOUNTS:
        raise TikTokReportingError(
            "tiktok_reporting_account_limit_exceeded",
            f"At most {MAX_REPORTING_ACCOUNTS} TikTok accounts may sync per run.",
            status_code=409,
        )
    return output


async def _fetch_day(
    client: httpx.AsyncClient,
    access_token: str,
    account: dict[str, Any],
    day: date,
) -> dict[str, Any]:
    account_id = account["ad_account_id"]
    response = await client.get(
        TIKTOK_REPORT_URL,
        headers={"Access-Token": access_token},
        params={
            "advertiser_id": account_id,
            "report_type": "BASIC",
            "data_level": "AUCTION_ADVERTISER",
            "dimensions": json.dumps(["advertiser_id"], separators=(",", ":")),
            "metrics": json.dumps(
                ["spend", "impressions", "clicks", "conversion"],
                separators=(",", ":"),
            ),
            "start_date": day.isoformat(),
            "end_date": day.isoformat(),
            "page": 1,
            "page_size": 10,
        },
    )
    data = _provider_data(response, "tiktok_integrated_report")
    rows = data.get("list") or []
    metrics = (rows[0] or {}).get("metrics") if rows else {}
    metrics = metrics if isinstance(metrics, dict) else {}
    return {
        "spend_native": float(metrics.get("spend") or 0),
        "impressions": int(float(metrics.get("impressions") or 0)),
        "clicks": int(float(metrics.get("clicks") or 0)),
        "conversions": float(metrics.get("conversion") or 0),
        "request_id": data.get("_request_id"),
        "empty": not bool(rows),
    }


async def run_tiktok_reporting_sync(
    db: Any,
    user_id: str,
    payload: TikTokReportingSyncInput,
    *,
    now: Callable[[], datetime] = _utcnow,
) -> dict[str, Any]:
    if not tiktok_oauth_configured():
        raise TikTokReportingError(
            "tiktok_oauth_not_configured",
            "TikTok Marketing API configuration is incomplete.",
            status_code=503,
        )
    if not tiktok_reporting_enabled():
        raise TikTokReportingError(
            "tiktok_reporting_disabled",
            "TikTok native reporting is disabled by the operational safety flag.",
            status_code=503,
        )

    await ensure_tiktok_reporting_indexes(db)
    access_token = await _credential(db, user_id)
    accounts = await _accounts(db, user_id)
    now_value = now().astimezone(timezone.utc)
    days = _dates(payload, now_value.date())
    observed_at = _iso(now_value)
    account_summaries = []
    error_items = []
    provider_calls = 0

    async with httpx.AsyncClient(timeout=30.0) as client:
        for account in accounts:
            saved = 0
            empty_rows = 0
            account_errors = []
            currency = str(account.get("currency") or "").strip().upper() or None
            fx_rate, fx_source = _fx_to_sar(currency)
            for day in days:
                try:
                    row = await _fetch_day(client, access_token, account, day)
                    provider_calls += 1
                    spend_sar = (
                        round(row["spend_native"] * fx_rate, 2)
                        if fx_rate is not None
                        else None
                    )
                    await db[TIKTOK_REPORTING_COLLECTION].update_one(
                        {
                            "user_id": user_id,
                            "provider": TIKTOK_PROVIDER_ID,
                            "ad_account_id": account["ad_account_id"],
                            "date": day.isoformat(),
                        },
                        {
                            "$set": {
                                "user_id": user_id,
                                "provider": TIKTOK_PROVIDER_ID,
                                "ad_account_id": account["ad_account_id"],
                                "display_name": account.get("display_name"),
                                "date": day.isoformat(),
                                "account_timezone": account.get("timezone"),
                                "currency_native": currency,
                                "spend_native": row["spend_native"],
                                "fx_rate_to_sar": fx_rate,
                                "fx_source": fx_source,
                                "spend_sar": spend_sar,
                                "impressions": row["impressions"],
                                "clicks": row["clicks"],
                                "conversions": row["conversions"],
                                "conversion_metric": "conversion",
                                "empty_provider_row": row["empty"],
                                "request_id": row["request_id"],
                                "source_mode": TIKTOK_REPORTING_SOURCE_MODE,
                                "source_only": True,
                                "accounting_eligible": False,
                                "observed_at": observed_at,
                                "updated_at": observed_at,
                            },
                            "$setOnInsert": {"created_at": observed_at},
                        },
                        upsert=True,
                    )
                    saved += 1
                    empty_rows += int(row["empty"] is True)
                except TikTokReportingError as exc:
                    provider_calls += 1
                    if exc.code == "tiktok_needs_reauth":
                        raise
                    item = {
                        "ad_account_id": account["ad_account_id"],
                        "date": day.isoformat(),
                        "code": exc.code,
                    }
                    account_errors.append(item)
                    error_items.append(item)

            complete = not account_errors
            await db.mezan_integration_accounts_v2.update_one(
                {
                    "user_id": user_id,
                    "provider": TIKTOK_PROVIDER_ID,
                    "external_account_id": account["ad_account_id"],
                },
                {
                    "$set": {
                        "performance_rows_saved": saved,
                        "has_data": saved > 0,
                        "last_sync_at": observed_at if complete else account.get("last_sync_at"),
                        "data_delay_minutes": 0 if complete else None,
                        "health_score": 100 if complete else 70,
                        "source_mode": TIKTOK_REPORTING_SOURCE_MODE,
                        "last_observed_at": observed_at,
                    }
                },
            )
            account_summaries.append(
                {
                    "ad_account_id": account["ad_account_id"],
                    "display_name": account.get("display_name"),
                    "currency": currency,
                    "timezone": account.get("timezone"),
                    "rows_saved": saved,
                    "empty_rows": empty_rows,
                    "errors": len(account_errors),
                    "complete": complete,
                }
            )

    rows_saved = sum(item["rows_saved"] for item in account_summaries)
    empty_rows = sum(int(item.get("empty_rows") or 0) for item in account_summaries)
    accounts_complete = sum(bool(item["complete"]) for item in account_summaries)
    if rows_saved == 0:
        raise TikTokReportingError(
            "tiktok_reporting_no_rows",
            "TikTok returned no usable reporting rows.",
            status_code=502,
            retryable=True,
            result={
                "accounts_attempted": len(account_summaries),
                "accounts_complete": accounts_complete,
                "rows_saved": 0,
                "errors_count": len(error_items) or 1,
            },
        )

    sync_status = (
        "complete" if accounts_complete == len(account_summaries) else "partial"
    )
    riyadh_today = now_value.astimezone(ZoneInfo("Asia/Riyadh")).date()
    open_day_waiting = (
        days[0] <= riyadh_today <= days[-1]
        and rows_saved > 0
        and empty_rows == rows_saved
    )
    if open_day_waiting and sync_status == "complete":
        sync_status = "partial"
    await db.mezan_integrations_v2.update_one(
        {"user_id": user_id, "provider": TIKTOK_PROVIDER_ID},
        {
            "$set": {
                "connection_status": "connected",
                "connection_provenance": "api_connection",
                "source_mode": TIKTOK_REPORTING_SOURCE_MODE,
                "last_sync_at": observed_at,
                "data_delay_minutes": 0 if sync_status == "complete" else None,
                "data_quality": "good" if sync_status == "complete" else "incomplete" if open_day_waiting else "degraded",
                "has_data": True,
                "checked_at": observed_at,
                "updated_at": observed_at,
            }
        },
        upsert=True,
    )
    await db.mezan_integration_health_v2.insert_one(
        {
            "user_id": user_id,
            "provider": TIKTOK_PROVIDER_ID,
            "health_status": "healthy" if sync_status == "complete" else "degraded",
            "health_score": 100 if sync_status == "complete" else 70,
            "data_quality": "good" if sync_status == "complete" else "degraded",
            "connection_status": "connected",
            "connection_provenance": "api_connection",
            "data_delay_minutes": 0 if sync_status == "complete" else None,
            "checked_at": observed_at,
            "source_mode": TIKTOK_REPORTING_SOURCE_MODE,
        }
    )
    return {
        "provider": TIKTOK_PROVIDER_ID,
        "status": sync_status,
        "date_from": days[0].isoformat(),
        "date_to": days[-1].isoformat(),
        "accounts_attempted": len(account_summaries),
        "accounts_complete": accounts_complete,
        "rows_saved": rows_saved,
        "empty_rows": empty_rows,
        "open_day_waiting": open_day_waiting,
        "errors_count": len(error_items),
        "items": account_summaries,
        "errors": error_items[:100],
        "provider_calls": provider_calls,
        "source_only": True,
        "provider_write_reached": False,
        "campaign_write_reached": False,
        "accounting_write_reached": False,
        "qoyod_write_reached": False,
        "fetched_at": observed_at,
    }


__all__ = [
    "MAX_REPORTING_DAYS",
    "TIKTOK_REPORTING_COLLECTION",
    "TIKTOK_REPORTING_ENABLED_ENV",
    "TikTokReportingError",
    "TikTokReportingSyncInput",
    "ensure_tiktok_reporting_indexes",
    "run_tiktok_reporting_sync",
    "tiktok_reporting_enabled",
]
