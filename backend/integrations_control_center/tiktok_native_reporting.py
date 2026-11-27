"""Native read-only TikTok advertiser reporting for Mezan Integrations V2.

This module reads only the encrypted V2 OAuth credential and the advertiser
accounts discovered by the native TikTok connector. It never reads Make.com or
legacy TikTok collections and never writes campaigns, accounting, or Qoyod.
"""
from __future__ import annotations

import json
import math
import os
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

import httpx
from pydantic import BaseModel, Field, model_validator

from dashboard_tiktok_coverage import tiktok_reporting_coverage

from .ad_daily_close_proof import close_proof, tiktok_evidence
from .tiktok_account_refresh import refresh_tiktok_authorized_accounts

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
MAX_REPORTING_ACCOUNTS = 200
# Explicit request ceiling avoids an unbounded accounts × days API fan-out.
MAX_REPORTING_CALLS_PER_SYNC = 120


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
    include_hierarchy: bool = False
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
            "last_reporting_attempt_at": 1,
            "tiktok_backfill_next_date": 1,
            "tiktok_backfill_complete_at": 1,
            "tiktok_evidence_repair_next_date": 1,
            "tiktok_evidence_repair_complete_at": 1,
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
    seen_ids: set[str] = set()
    for row in rows:
        account_id = str(
            row.get("ad_account_id") or row.get("external_account_id") or ""
        ).strip()
        if account_id:
            if account_id in seen_ids:
                raise TikTokReportingError(
                    "tiktok_reporting_duplicate_account_identity",
                    "TikTok account identity is duplicated; reporting cannot safely aggregate it.",
                    status_code=409,
                )
            seen_ids.add(account_id)
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
    # Fair even when one advertiser repeatedly fails: last attempt, not the
    # last successful sync, determines which accounts are processed next.
    return sorted(output, key=lambda row: (
        str(row.get("last_reporting_attempt_at") or ""), row["ad_account_id"]
    ))


async def _fetch_day(
    client: httpx.AsyncClient,
    access_token: str,
    account: dict[str, Any],
    day: date,
) -> dict[str, Any]:
    account_id = account["ad_account_id"]
    try:
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
    except httpx.HTTPError as exc:
        raise TikTokReportingError(
            "tiktok_reporting_request_unavailable",
            "TikTok reporting request could not be completed.",
            status_code=502,
        ) from exc
    data = _provider_data(response, "tiktok_integrated_report")
    rows = data.get("list") or []
    if not isinstance(rows, list) or len(rows) > 1:
        raise TikTokReportingError(
            "tiktok_reporting_unexpected_advertiser_rows",
            "TikTok returned an unexpected number of advertiser rows.",
            status_code=502,
        )
    if rows:
        item = rows[0]
        dimensions = item.get("dimensions") if isinstance(item, dict) else None
        if not isinstance(dimensions, dict) or str(dimensions.get("advertiser_id") or "") != account_id:
            raise TikTokReportingError(
                "tiktok_reporting_advertiser_mismatch",
                "TikTok report advertiser did not match the requested account.",
                status_code=502,
            )
    metrics = (rows[0] or {}).get("metrics") if rows else {}
    metrics = metrics if isinstance(metrics, dict) else {}
    try:
        spend_native = float(metrics.get("spend") or 0)
        impressions_native = float(metrics.get("impressions") or 0)
        clicks_native = float(metrics.get("clicks") or 0)
        conversions_native = float(metrics.get("conversion") or 0)
        values = (spend_native, impressions_native, clicks_native, conversions_native)
        if any(not math.isfinite(v) or v < 0 for v in values):
            raise ValueError("nonfinite_or_negative")
        impressions = int(impressions_native)
        clicks = int(clicks_native)
    except (TypeError, ValueError, OverflowError) as exc:
        raise TikTokReportingError(
            "tiktok_reporting_invalid_metrics",
            "TikTok returned invalid or non-finite advertiser metrics.",
            status_code=502,
        ) from exc
    return {
        "close_evidence": tiktok_evidence(data, account_id),
        "spend_native": spend_native,
        "impressions": impressions,
        "clicks": clicks,
        "conversions": conversions_native,
        "request_id": data.get("_request_id"),
        "empty": not bool(rows),
    }


async def _legacy_evidence_repair_start(
    db: Any, user_id: str, account_id: str, start: date, today: date,
) -> date | None:
    """Locate historical gaps in a bounded, observed account history.

    Never use the new-account discovery marker for an existing advertiser.
    Only consider dates within the rolling 30-day window before this sync;
    a historic native fact establishes the start of known coverage, not zero.
    """
    lower = today - timedelta(days=29)
    upper = min(start, today + timedelta(days=1))
    if upper <= lower:
        return None
    cursor = db[TIKTOK_REPORTING_COLLECTION].find({
        "user_id": user_id, "provider": TIKTOK_PROVIDER_ID,
        "ad_account_id": account_id,
        "date": {"$gte": lower.isoformat(), "$lt": upper.isoformat()},
    }, {"_id": 0})
    rows = await cursor.sort("date", 1).limit(31).to_list(length=31)
    dated = [
        row for row in rows
        if lower.isoformat() <= str(row.get("date") or "") < upper.isoformat()
    ]
    if not dated:
        return None
    first = date.fromisoformat(min(row["date"] for row in dated))
    for offset in range((upper - first).days):
        day = (first + timedelta(days=offset)).isoformat()
        if not tiktok_reporting_coverage(
            [account_id], day, day, dated
        )["coverage_complete"]:
            return date.fromisoformat(day)
    return None


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
    days = _dates(payload, now().astimezone(timezone.utc).date())
    observed_at = _iso(now())
    try:
        discovery = await refresh_tiktok_authorized_accounts(
            db, user_id, access_token, observed_at
        )
    except Exception:
        # Never erase previously discovered identities or their daily rows
        # because TikTok account enumeration was temporarily unavailable.
        discovery = {
            "status": "degraded",
            "reason": "tiktok_authorized_account_discovery_unavailable",
        }
    all_accounts = await _accounts(db, user_id)
    # Reserve two optional history days per account without ever exceeding the
    # provider request budget. Previous valid daily facts are only upserted.
    batch_size = max(1, MAX_REPORTING_CALLS_PER_SYNC // (len(days) + 2))
    accounts = all_accounts[:batch_size]
    accounts_deferred = len(all_accounts) - len(accounts)
    account_summaries = []
    error_items = (
        [{"code": discovery["reason"], "kind": "discovery"}]
        if discovery["status"] != "complete" else []
    )
    provider_calls = 0

    async with httpx.AsyncClient(timeout=30.0) as client:
        for account in accounts:
            saved = 0
            account_errors = []
            currency = str(account.get("currency") or "").strip().upper() or None
            fx_rate, fx_source = _fx_to_sar(currency)
            history_start = None
            legacy_repair = not bool(account.get("tiktok_backfill_next_date"))
            cursor_key = ("tiktok_evidence_repair_next_date" if legacy_repair
                          else "tiktok_backfill_next_date")
            complete_key = ("tiktok_evidence_repair_complete_at" if legacy_repair
                            else "tiktok_backfill_complete_at")
            if legacy_repair and not account.get(cursor_key) and not account.get(complete_key):
                history_start = await _legacy_evidence_repair_start(
                    db, user_id, account["ad_account_id"], min(days),
                    now().astimezone(timezone.utc).date(),
                )
            if account.get(cursor_key) and not account.get(complete_key):
                try:
                    history_start = date.fromisoformat(str(account[cursor_key]))
                except ValueError:
                    account_errors.append({
                        "ad_account_id": account["ad_account_id"],
                        "code": "tiktok_backfill_cursor_invalid",
                    })
                    error_items.append(account_errors[-1])
            historical_days = []
            if history_start and history_start < min(days):
                historical_days = [
                    history_start + timedelta(days=i) for i in range(2)
                    if history_start + timedelta(days=i) < min(days)
                ]
            next_history_date = history_start
            for day in [*historical_days, *days]:
                try:
                    if legacy_repair and day in historical_days:
                        existing = await db[TIKTOK_REPORTING_COLLECTION].find_one({
                            "user_id": user_id, "provider": TIKTOK_PROVIDER_ID,
                            "ad_account_id": account["ad_account_id"], "date": day.isoformat(),
                        }, {"_id": 0})
                        if existing and tiktok_reporting_coverage(
                            [account["ad_account_id"]], day.isoformat(),
                            day.isoformat(), [existing],
                        )["coverage_complete"]:
                            if next_history_date == day:
                                next_history_date = day + timedelta(days=1)
                            continue
                    row = await _fetch_day(client, access_token, account, day)
                    provider_calls += 1
                    spend_sar = (
                        round(row["spend_native"] * fx_rate, 2)
                        if fx_rate is not None
                        else None
                    )
                    source_proof = close_proof(
                        account_id=account["ad_account_id"],
                        business_date=day.isoformat(),
                        timezone=account.get("timezone"), currency=currency,
                        source_mode=TIKTOK_REPORTING_SOURCE_MODE,
                        observed_at=observed_at,
                        **row["close_evidence"],
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
                                "source_close_proof": source_proof,
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
                    if source_proof.get("complete") is not True or spend_sar is None:
                        item = {
                            "ad_account_id": account["ad_account_id"],
                            "date": day.isoformat(),
                            "code": "tiktok_reporting_source_evidence_incomplete",
                        }
                        account_errors.append(item)
                        error_items.append(item)
                    elif day in historical_days and next_history_date == day:
                        next_history_date = day + timedelta(days=1)
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
            account_patch = {
                "performance_rows_saved": saved,
                "has_data": saved > 0,
                "last_sync_at": observed_at if complete else account.get("last_sync_at"),
                "last_reporting_attempt_at": observed_at,
                "data_delay_minutes": 0 if complete else None,
                "health_score": 100 if complete else 70,
                "source_mode": TIKTOK_REPORTING_SOURCE_MODE,
                "last_observed_at": observed_at,
            }
            if history_start and next_history_date is not None:
                account_patch[cursor_key] = next_history_date.isoformat()
                if next_history_date >= min(days):
                    account_patch[complete_key] = observed_at
            await db.mezan_integration_accounts_v2.update_one(
                {
                    "user_id": user_id,
                    "provider": TIKTOK_PROVIDER_ID,
                    "external_account_id": account["ad_account_id"],
                },
                {
                    "$set": account_patch
                },
            )
            account_summaries.append(
                {
                    "ad_account_id": account["ad_account_id"],
                    "display_name": account.get("display_name"),
                    "currency": currency,
                    "timezone": account.get("timezone"),
                    "rows_saved": saved,
                    "errors": len(account_errors),
                    "complete": complete,
                    "backfill_pending": bool(
                        history_start and next_history_date < min(days)
                    ),
                }
            )

    rows_saved = sum(item["rows_saved"] for item in account_summaries)
    accounts_complete = sum(bool(item["complete"]) for item in account_summaries)
    backfill_pending_accounts = (
        sum(bool(item.get("backfill_pending")) for item in account_summaries)
        + sum(bool(
            (item.get("tiktok_backfill_next_date") and not item.get("tiktok_backfill_complete_at"))
            or (item.get("tiktok_evidence_repair_next_date")
                and not item.get("tiktok_evidence_repair_complete_at"))
        ) for item in all_accounts[len(accounts):])
    )
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
        "complete"
        if accounts_complete == len(account_summaries)
        and accounts_deferred == 0
        and backfill_pending_accounts == 0
        and discovery["status"] == "complete"
        else "partial"
    )
    hierarchy = None
    if payload.include_hierarchy and not accounts_deferred:
        from .tiktok_native_hierarchy import sync_tiktok_hierarchy
        hierarchy = await sync_tiktok_hierarchy(db, user_id, days, observed_at=observed_at)
        if hierarchy["status"] != "complete":
            sync_status = "partial"
            error_items.extend(hierarchy["errors"])
    await db.mezan_integrations_v2.update_one(
        {"user_id": user_id, "provider": TIKTOK_PROVIDER_ID},
        {
            "$set": {
                "connection_status": "connected",
                "connection_provenance": "api_connection",
                "source_mode": TIKTOK_REPORTING_SOURCE_MODE,
                "last_sync_at": observed_at,
                "data_delay_minutes": 0 if sync_status == "complete" else None,
                "data_quality": "good" if sync_status == "complete" else "degraded",
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
        "accounts_total": len(all_accounts),
        "accounts_deferred": accounts_deferred,
        "backfill_pending_accounts": backfill_pending_accounts,
        "discovery": discovery,
        "rows_saved": rows_saved,
        "errors_count": len(error_items),
        "items": account_summaries,
        "errors": error_items[:100],
        "provider_calls": provider_calls,
        "hierarchy": hierarchy,
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

