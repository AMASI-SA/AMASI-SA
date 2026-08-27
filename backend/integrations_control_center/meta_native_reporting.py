"""Native read-only Meta Ads reporting for Mezan Integrations V2.

The module reads only the encrypted Meta OAuth credential and owner-selected
ad accounts. It stores provider evidence in an isolated V2 analytical collection
and never writes campaigns, legacy ad tables, accounting, or Qoyod.
"""
from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

import httpx
from fastapi import HTTPException
from pydantic import BaseModel, Field, model_validator

from .meta_account_selection import (
    MAX_META_SELECTED_ACCOUNTS,
    load_selected_meta_accounts,
)
from .meta_campaign_reporting import (
    MetaCampaignReportingError,
    ensure_meta_campaign_reporting_indexes,
    fetch_meta_campaign_catalog,
    sync_meta_campaign_day,
)
from .meta_oauth_security import (
    META_CREDENTIALS_COLLECTION,
    META_PROVIDER_ID,
    decrypt_meta_token,
    meta_appsecret_proof,
    meta_graph_base,
    meta_oauth_configured,
)

META_REPORTING_COLLECTION = "mezan_meta_performance_daily_v2"
META_REPORTING_SOURCE_MODE = "meta_marketing_reporting_v2"
META_REPORTING_ENABLED_ENV = "META_NATIVE_REPORTING_SYNC_ENABLED"
META_USD_TO_SAR_RATE_ENV = "META_USD_TO_SAR_RATE"
MAX_META_REPORTING_DAYS = 31

PURCHASE_ACTION_PRIORITY = (
    "omni_purchase",
    "purchase",
    "offsite_conversion.fb_pixel_purchase",
    "onsite_conversion.purchase",
    "mobile_app_purchase",
)


class MetaReportingError(RuntimeError):
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


class MetaReportingSyncInput(BaseModel):
    days: int = Field(default=7, ge=1, le=MAX_META_REPORTING_DAYS)
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
            if (end - start).days + 1 > MAX_META_REPORTING_DAYS:
                raise ValueError(
                    f"Meta reporting supports at most {MAX_META_REPORTING_DAYS} days"
                )
        return self


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None = None) -> str:
    return (value or _utcnow()).astimezone(timezone.utc).isoformat()


def _parse_datetime(value: Any) -> datetime | None:
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


def meta_reporting_enabled() -> bool:
    return os.environ.get(META_REPORTING_ENABLED_ENV, "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _dates(payload: MetaReportingSyncInput, today: date) -> list[date]:
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
            rate = float(os.environ.get(META_USD_TO_SAR_RATE_ENV, "3.75"))
        except ValueError:
            rate = 0.0
        if rate > 0:
            return rate, "configured_usd_peg"
    return None, "missing"


def _graph_error(response: httpx.Response, operation: str) -> MetaReportingError:
    payload: dict[str, Any] = {}
    try:
        parsed = response.json()
        payload = parsed if isinstance(parsed, dict) else {}
    except Exception:  # noqa: BLE001
        payload = {}
    error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
    provider_code = int(error.get("code") or 0)
    provider_subcode = int(error.get("error_subcode") or 0)
    if response.status_code in {401, 403} or provider_code in {102, 190}:
        return MetaReportingError(
            "meta_needs_reauth",
            "Meta rejected or expired the saved access token.",
            status_code=409,
            retryable=False,
            result={"provider_code": provider_code, "provider_subcode": provider_subcode},
        )
    if response.status_code == 429 or provider_code in {4, 17, 32, 613}:
        return MetaReportingError(
            "meta_rate_limited",
            "Meta temporarily rate-limited reporting requests.",
            status_code=429,
            retryable=True,
            result={"provider_code": provider_code, "provider_subcode": provider_subcode},
        )
    return MetaReportingError(
        f"{operation}_http_{response.status_code}",
        "Meta reporting returned an API error.",
        status_code=502,
        retryable=response.status_code >= 500 or provider_code in {1, 2},
        result={"provider_code": provider_code, "provider_subcode": provider_subcode},
    )


def _action_value(rows: Any) -> tuple[float, str | None]:
    if not isinstance(rows, list):
        return 0.0, None
    by_type = {
        str(item.get("action_type") or ""): float(item.get("value") or 0)
        for item in rows
        if isinstance(item, dict)
    }
    for action_type in PURCHASE_ACTION_PRIORITY:
        if action_type in by_type:
            return by_type[action_type], action_type
    return 0.0, None


async def ensure_meta_reporting_indexes(db: Any) -> None:
    await db[META_REPORTING_COLLECTION].create_index(
        [("user_id", 1), ("ad_account_id", 1), ("date", 1)],
        unique=True,
        name="meta_performance_user_account_date_unique",
    )
    await db[META_REPORTING_COLLECTION].create_index(
        [("user_id", 1), ("date", -1)],
        name="meta_performance_user_date",
    )


async def _credential(db: Any, user_id: str, now: datetime) -> str:
    row = await db[META_CREDENTIALS_COLLECTION].find_one(
        {"user_id": user_id, "provider": META_PROVIDER_ID},
        {
            "_id": 0,
            "access_token_ciphertext": 1,
            "access_token_expires_at": 1,
        },
    )
    expiry = _parse_datetime((row or {}).get("access_token_expires_at"))
    if expiry and expiry <= now.astimezone(timezone.utc):
        raise MetaReportingError(
            "meta_needs_reauth",
            "The saved Meta access token has expired.",
            status_code=409,
        )
    token = decrypt_meta_token((row or {}).get("access_token_ciphertext"))
    if not token:
        raise MetaReportingError(
            "meta_oauth_credential_missing",
            "No native Meta OAuth credential is available.",
            status_code=409,
        )
    return token


async def _accounts(db: Any, user_id: str) -> list[dict[str, Any]]:
    try:
        accounts = await load_selected_meta_accounts(db, user_id)
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        raise MetaReportingError(
            str(detail.get("code") or "meta_accounts_not_selected"),
            str(detail.get("message") or "Select at least one Meta ad account."),
            status_code=exc.status_code,
        ) from exc
    if len(accounts) > MAX_META_SELECTED_ACCOUNTS:
        raise MetaReportingError(
            "meta_account_limit_exceeded",
            f"At most {MAX_META_SELECTED_ACCOUNTS} Meta accounts may sync per run.",
            status_code=409,
        )
    return accounts


async def _fetch_day(
    client: httpx.AsyncClient,
    access_token: str,
    account: dict[str, Any],
    day: date,
) -> dict[str, Any]:
    account_id = str(account["ad_account_id"])
    response = await client.get(
        f"{meta_graph_base()}/{account_id}/insights",
        params={
            "access_token": access_token,
            "appsecret_proof": meta_appsecret_proof(access_token),
            "fields": (
                "spend,impressions,clicks,actions,action_values,"
                "account_currency,date_start,date_stop"
            ),
            "time_range": json.dumps(
                {"since": day.isoformat(), "until": day.isoformat()},
                separators=(",", ":"),
            ),
            "time_increment": 1,
            "level": "account",
            "use_account_attribution_setting": "true",
            "use_unified_attribution_setting": "true",
            "limit": 100,
        },
    )
    if response.status_code >= 400:
        raise _graph_error(response, "meta_insights")
    payload = response.json() or {}
    if not isinstance(payload, dict):
        raise MetaReportingError(
            "meta_insights_invalid_payload",
            "Meta reporting returned an invalid response.",
            status_code=502,
            retryable=True,
        )
    rows = payload.get("data") or []
    if not isinstance(rows, list):
        raise MetaReportingError(
            "meta_insights_invalid_rows",
            "Meta reporting returned invalid insight rows.",
            status_code=502,
            retryable=True,
        )
    if not rows:
        return {
            "spend_native": 0.0,
            "impressions": 0,
            "clicks": 0,
            "purchases": 0.0,
            "purchase_value_native": 0.0,
            "purchase_action_type": None,
            "purchase_value_action_type": None,
            "currency": account.get("currency"),
            "date_start": day.isoformat(),
            "date_stop": day.isoformat(),
            "empty": True,
        }
    row = rows[0] if isinstance(rows[0], dict) else {}
    purchases, purchase_action_type = _action_value(row.get("actions"))
    purchase_value, purchase_value_action_type = _action_value(row.get("action_values"))
    return {
        "spend_native": float(row.get("spend") or 0),
        "impressions": int(float(row.get("impressions") or 0)),
        "clicks": int(float(row.get("clicks") or 0)),
        "purchases": purchases,
        "purchase_value_native": purchase_value,
        "purchase_action_type": purchase_action_type,
        "purchase_value_action_type": purchase_value_action_type,
        "currency": row.get("account_currency") or account.get("currency"),
        "date_start": row.get("date_start") or day.isoformat(),
        "date_stop": row.get("date_stop") or day.isoformat(),
        "empty": False,
    }


async def run_meta_reporting_sync(
    db: Any,
    user_id: str,
    payload: MetaReportingSyncInput,
    *,
    now: Callable[[], datetime] = _utcnow,
) -> dict[str, Any]:
    if not meta_oauth_configured():
        raise MetaReportingError(
            "meta_oauth_not_configured",
            "Meta Business OAuth configuration is incomplete.",
            status_code=503,
        )
    if not meta_reporting_enabled():
        raise MetaReportingError(
            "meta_reporting_disabled",
            "Meta native reporting is disabled by the operational safety flag.",
            status_code=503,
        )

    now_value = now().astimezone(timezone.utc)
    await ensure_meta_reporting_indexes(db)
    await ensure_meta_campaign_reporting_indexes(db)
    access_token = await _credential(db, user_id, now_value)
    accounts = await _accounts(db, user_id)
    days = _dates(payload, now_value.date())
    observed_at = _iso(now_value)
    account_summaries: list[dict[str, Any]] = []
    error_items: list[dict[str, Any]] = []
    provider_calls = 0

    async with httpx.AsyncClient(timeout=35.0) as client:
        for account in accounts:
            saved = 0
            campaign_saved = 0
            account_errors: list[dict[str, Any]] = []
            campaign_catalog: dict[str, dict[str, Any]] = {}
            try:
                catalog_result = await fetch_meta_campaign_catalog(
                    client, access_token, account
                )
                provider_calls += int(catalog_result.get("provider_calls") or 0)
                campaign_catalog = catalog_result.get("campaigns") or {}
            except MetaCampaignReportingError as exc:
                provider_calls += int(exc.provider_calls or 0)
                if exc.code == "meta_needs_reauth":
                    raise MetaReportingError(
                        exc.code, exc.message, status_code=exc.status_code
                    ) from exc
                item = {
                    "ad_account_id": account["ad_account_id"],
                    "kind": "campaign_catalog",
                    "code": exc.code,
                }
                account_errors.append(item)
                error_items.append(item)
            for day in days:
                try:
                    row = await _fetch_day(client, access_token, account, day)
                    provider_calls += 1
                    currency = str(row.get("currency") or account.get("currency") or "").strip().upper() or None
                    fx_rate, fx_source = _fx_to_sar(currency)
                    spend_sar = round(row["spend_native"] * fx_rate, 2) if fx_rate is not None else None
                    purchase_value_sar = (
                        round(row["purchase_value_native"] * fx_rate, 2)
                        if fx_rate is not None
                        else None
                    )
                    await db[META_REPORTING_COLLECTION].update_one(
                        {
                            "user_id": user_id,
                            "provider": META_PROVIDER_ID,
                            "ad_account_id": account["ad_account_id"],
                            "date": day.isoformat(),
                        },
                        {
                            "$set": {
                                "user_id": user_id,
                                "provider": META_PROVIDER_ID,
                                "ad_account_id": account["ad_account_id"],
                                "display_name": account.get("display_name"),
                                "business_id": account.get("business_id"),
                                "business_name": account.get("business_name"),
                                "date": day.isoformat(),
                                "date_start": row["date_start"],
                                "date_stop": row["date_stop"],
                                "account_timezone": account.get("timezone"),
                                "currency_native": currency,
                                "spend_native": row["spend_native"],
                                "fx_rate_to_sar": fx_rate,
                                "fx_source": fx_source,
                                "spend_sar": spend_sar,
                                "impressions": row["impressions"],
                                "clicks": row["clicks"],
                                "purchases": row["purchases"],
                                "purchase_value_native": row["purchase_value_native"],
                                "purchase_value_sar": purchase_value_sar,
                                "purchase_action_type": row["purchase_action_type"],
                                "purchase_value_action_type": row["purchase_value_action_type"],
                                "attribution_mode": "account_setting+unified",
                                "empty_provider_row": row["empty"],
                                "source_mode": META_REPORTING_SOURCE_MODE,
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
                    try:
                        campaign_result = await sync_meta_campaign_day(
                            db,
                            user_id,
                            client,
                            access_token,
                            account,
                            day,
                            campaign_catalog=campaign_catalog,
                            observed_at=observed_at,
                        )
                        provider_calls += int(
                            campaign_result.get("provider_calls") or 0
                        )
                        campaign_saved += int(
                            campaign_result.get("rows_saved") or 0
                        )
                    except MetaCampaignReportingError as exc:
                        provider_calls += int(exc.provider_calls or 0)
                        if exc.code == "meta_needs_reauth":
                            raise MetaReportingError(
                                exc.code,
                                exc.message,
                                status_code=exc.status_code,
                            ) from exc
                        item = {
                            "ad_account_id": account["ad_account_id"],
                            "date": day.isoformat(),
                            "kind": "campaign_insights",
                            "code": exc.code,
                        }
                        account_errors.append(item)
                        error_items.append(item)
                except MetaReportingError as exc:
                    provider_calls += 1
                    if exc.code == "meta_needs_reauth":
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
                    "provider": META_PROVIDER_ID,
                    "external_account_id": account["ad_account_id"],
                },
                {
                    "$set": {
                        "performance_rows_saved": saved,
                        "has_data": saved > 0,
                        "last_sync_at": observed_at if complete else account.get("last_sync_at"),
                        "data_delay_minutes": 0 if complete else None,
                        "health_score": 100 if complete else 70,
                        "source_mode": META_REPORTING_SOURCE_MODE,
                        "last_observed_at": observed_at,
                    }
                },
            )
            account_summaries.append(
                {
                    "ad_account_id": account["ad_account_id"],
                    "display_name": account.get("display_name"),
                    "currency": account.get("currency"),
                    "timezone": account.get("timezone"),
                    "rows_saved": saved,
                    "campaign_rows_saved": campaign_saved,
                    "errors": len(account_errors),
                    "complete": complete,
                }
            )

    rows_saved = sum(item["rows_saved"] for item in account_summaries)
    campaign_rows_saved = sum(
        item.get("campaign_rows_saved", 0) for item in account_summaries
    )
    accounts_complete = sum(bool(item["complete"]) for item in account_summaries)
    if rows_saved == 0:
        raise MetaReportingError(
            "meta_reporting_no_rows",
            "Meta returned no usable reporting rows.",
            status_code=502,
            retryable=True,
            result={
                "accounts_attempted": len(account_summaries),
                "accounts_complete": accounts_complete,
                "rows_saved": 0,
                "errors_count": len(error_items) or 1,
            },
        )

    sync_status = "complete" if accounts_complete == len(account_summaries) else "partial"
    await db.mezan_integrations_v2.update_one(
        {"user_id": user_id, "provider": META_PROVIDER_ID},
        {
            "$set": {
                "connection_status": "connected",
                "connection_provenance": "api_connection",
                "source_mode": META_REPORTING_SOURCE_MODE,
                "last_sync_at": observed_at,
                "data_delay_minutes": 0 if sync_status == "complete" else None,
                "data_quality": "complete" if sync_status == "complete" else "partial",
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
            "provider": META_PROVIDER_ID,
            "health_status": "healthy" if sync_status == "complete" else "degraded",
            "health_score": 100 if sync_status == "complete" else 70,
            "data_quality": "complete" if sync_status == "complete" else "partial",
            "connection_status": "connected",
            "connection_provenance": "api_connection",
            "data_delay_minutes": 0 if sync_status == "complete" else None,
            "checked_at": observed_at,
            "source_mode": META_REPORTING_SOURCE_MODE,
        }
    )
    return {
        "provider": META_PROVIDER_ID,
        "status": sync_status,
        "date_from": days[0].isoformat(),
        "date_to": days[-1].isoformat(),
        "accounts_attempted": len(account_summaries),
        "accounts_complete": accounts_complete,
        "rows_saved": rows_saved,
        "campaign_rows_saved": campaign_rows_saved,
        "errors_count": len(error_items),
        "provider_calls": provider_calls,
        "items": account_summaries,
        "errors": error_items[:100],
        "source_only": True,
        "provider_write_reached": False,
        "campaign_write_reached": False,
        "accounting_write_reached": False,
        "qoyod_write_reached": False,
        "fetched_at": observed_at,
    }


__all__ = [
    "MAX_META_REPORTING_DAYS",
    "META_REPORTING_COLLECTION",
    "META_REPORTING_ENABLED_ENV",
    "META_REPORTING_SOURCE_MODE",
    "MetaReportingError",
    "MetaReportingSyncInput",
    "ensure_meta_reporting_indexes",
    "meta_reporting_enabled",
    "run_meta_reporting_sync",
]
