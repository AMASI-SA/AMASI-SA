"""Native read-only Google Ads spend reporting for the Dashboard.

The connector queries Google Ads with GAQL, stores daily and account-local
hourly analytical facts, and never mutates campaigns, accounting, Salla, or
Qoyod. OAuth access tokens may be refreshed operationally in their encrypted
credential collection.
"""
from __future__ import annotations

import os
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

import httpx

from .ads_platform_hourly import (
    ensure_platform_hourly_indexes,
    upsert_platform_hour,
)
from .google_oauth_security import (
    GOOGLE_CREDENTIALS_COLLECTION,
    GOOGLE_PROVIDER_IDS,
    GOOGLE_TOKEN_URL,
    decrypt_google_token,
    encrypt_google_token,
    google_oauth_configured,
)

GOOGLE_ADS_PROVIDER_ID = "google_ads"
GOOGLE_ADS_DAILY_COLLECTION = "mezan_google_ads_performance_daily_v2"
GOOGLE_ADS_REPORTING_SOURCE_MODE = "google_ads_reporting_v1"
GOOGLE_ADS_REPORTING_ENABLED_ENV = "GOOGLE_ADS_REPORTING_SYNC_ENABLED"
GOOGLE_ADS_USD_TO_SAR_RATE_ENV = "GOOGLE_ADS_USD_TO_SAR_RATE"
MAX_GOOGLE_ADS_ACCOUNTS = 50
MAX_GOOGLE_ADS_DAYS = 31


class GoogleAdsReportingError(RuntimeError):
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


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None = None) -> str:
    return (value or _utcnow()).astimezone(timezone.utc).isoformat()


def _parse_datetime(value: Any) -> datetime | None:
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


def google_ads_reporting_enabled() -> bool:
    raw = os.environ.get(GOOGLE_ADS_REPORTING_ENABLED_ENV, "true").strip().lower()
    return raw not in {"0", "false", "off", "no", "disabled"}


def google_ads_api_version() -> str:
    value = os.environ.get("GOOGLE_ADS_API_VERSION", "v25").strip()
    return value if value.startswith("v") else f"v{value}"


def _developer_token() -> str:
    value = os.environ.get("GOOGLE_ADS_DEVELOPER_TOKEN", "").strip()
    if not value:
        raise GoogleAdsReportingError(
            "google_ads_developer_token_missing",
            "Google Ads developer token is not configured.",
            status_code=503,
        )
    return value


def _fx_to_sar(currency: str | None) -> tuple[float | None, str]:
    normalized = str(currency or "").strip().upper()
    if normalized == "SAR":
        return 1.0, "implicit_sar"
    if normalized == "USD":
        try:
            rate = float(os.environ.get(GOOGLE_ADS_USD_TO_SAR_RATE_ENV, "3.75"))
        except ValueError:
            rate = 0.0
        if rate > 0:
            return rate, "configured_usd_peg"
    return None, "missing"


async def ensure_google_ads_reporting_indexes(db: Any) -> None:
    await db[GOOGLE_ADS_DAILY_COLLECTION].create_index(
        [("user_id", 1), ("ad_account_id", 1), ("date", 1)],
        unique=True,
        name="google_ads_daily_user_account_date_unique",
    )
    await db[GOOGLE_ADS_DAILY_COLLECTION].create_index(
        [("user_id", 1), ("date", -1)],
        name="google_ads_daily_user_date",
    )
    await ensure_platform_hourly_indexes(db)


async def _refresh_access_token(
    db: Any,
    row: dict[str, Any],
    now: datetime,
) -> str:
    refresh_token = decrypt_google_token(row.get("refresh_token_ciphertext"))
    if not refresh_token:
        raise GoogleAdsReportingError(
            "google_ads_needs_reauth",
            "Google Ads OAuth refresh token is unavailable.",
            status_code=409,
        )
    client_id = os.environ.get("GOOGLE_OAUTH_CLIENT_ID", "").strip()
    client_secret = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        raise GoogleAdsReportingError(
            "google_ads_oauth_configuration_missing",
            "Google OAuth client configuration is incomplete.",
            status_code=503,
        )
    async with httpx.AsyncClient(timeout=25.0) as client:
        response = await client.post(
            GOOGLE_TOKEN_URL,
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
        )
    if response.status_code >= 400:
        raise GoogleAdsReportingError(
            "google_ads_needs_reauth",
            "Google rejected the saved OAuth refresh token.",
            status_code=409,
        )
    payload = response.json() or {}
    access_token = str(payload.get("access_token") or "").strip()
    if not access_token:
        raise GoogleAdsReportingError(
            "google_ads_token_refresh_missing_access_token",
            "Google token refresh returned no access token.",
            status_code=502,
            retryable=True,
        )
    expires_in = max(int(payload.get("expires_in") or 3600), 60)
    await db[GOOGLE_CREDENTIALS_COLLECTION].update_one(
        {"user_id": str(row.get("user_id") or "")},
        {
            "$set": {
                "access_token_ciphertext": encrypt_google_token(access_token),
                "expires_at": now + timedelta(seconds=expires_in),
                "updated_at": now,
            }
        },
    )
    return access_token


async def _credential(db: Any, user_id: str, now: datetime) -> str:
    row = await db[GOOGLE_CREDENTIALS_COLLECTION].find_one(
        {"user_id": user_id},
        {
            "_id": 0,
            "user_id": 1,
            "access_token_ciphertext": 1,
            "refresh_token_ciphertext": 1,
            "expires_at": 1,
            "scope": 1,
        },
    )
    scopes = set((row or {}).get("scope") or [])
    if "https://www.googleapis.com/auth/adwords" not in scopes:
        raise GoogleAdsReportingError(
            "google_ads_scope_missing",
            "Google Ads OAuth permission was not granted.",
            status_code=409,
        )
    expiry = _parse_datetime((row or {}).get("expires_at"))
    access_token = decrypt_google_token((row or {}).get("access_token_ciphertext"))
    if access_token and expiry and expiry > now + timedelta(minutes=2):
        return access_token
    if not row:
        raise GoogleAdsReportingError(
            "google_ads_oauth_credential_missing",
            "No Google OAuth credential is available.",
            status_code=409,
        )
    return await _refresh_access_token(db, row, now)


async def _to_list(cursor: Any, length: int) -> list[dict[str, Any]]:
    if hasattr(cursor, "to_list"):
        return await cursor.to_list(length=length)
    return [row async for row in cursor]


async def _accounts(db: Any, user_id: str) -> list[dict[str, Any]]:
    cursor = db.mezan_integration_accounts_v2.find(
        {
            "user_id": user_id,
            "provider": GOOGLE_ADS_PROVIDER_ID,
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
            "mezan_selected": 1,
            "login_customer_id": 1,
        },
    )
    rows = await _to_list(cursor, MAX_GOOGLE_ADS_ACCOUNTS + 1)
    selected = [row for row in rows if row.get("mezan_selected") is True]
    source = selected or rows
    output: list[dict[str, Any]] = []
    for row in source:
        account_id = str(
            row.get("ad_account_id") or row.get("external_account_id") or ""
        ).replace("-", "").strip()
        if not account_id:
            continue
        output.append({**row, "ad_account_id": account_id})
    if not output:
        raise GoogleAdsReportingError(
            "google_ads_accounts_missing",
            "No connected Google Ads account was found.",
            status_code=409,
        )
    if len(output) > MAX_GOOGLE_ADS_ACCOUNTS:
        raise GoogleAdsReportingError(
            "google_ads_account_limit_exceeded",
            f"At most {MAX_GOOGLE_ADS_ACCOUNTS} Google Ads accounts may sync.",
            status_code=409,
        )
    return output


def _error_from_response(response: httpx.Response) -> GoogleAdsReportingError:
    payload: Any = {}
    try:
        payload = response.json()
    except Exception:  # noqa: BLE001
        payload = {}
    text = str(payload)[:500]
    if response.status_code in {401, 403}:
        return GoogleAdsReportingError(
            "google_ads_needs_reauth",
            "Google Ads rejected the saved credential or account access.",
            status_code=409,
            result={"provider_status": response.status_code, "provider_error": text},
        )
    if response.status_code == 429:
        return GoogleAdsReportingError(
            "google_ads_rate_limited",
            "Google Ads temporarily rate-limited reporting requests.",
            status_code=429,
            retryable=True,
        )
    return GoogleAdsReportingError(
        f"google_ads_reporting_http_{response.status_code}",
        "Google Ads reporting returned an API error.",
        status_code=502,
        retryable=response.status_code >= 500,
        result={"provider_status": response.status_code, "provider_error": text},
    )


async def _search_stream(
    client: httpx.AsyncClient,
    *,
    access_token: str,
    account: dict[str, Any],
    query: str,
) -> list[dict[str, Any]]:
    account_id = str(account["ad_account_id"])
    headers = {
        "Authorization": f"Bearer {access_token}",
        "developer-token": _developer_token(),
        "Content-Type": "application/json",
    }
    login_customer_id = str(account.get("login_customer_id") or "").replace("-", "").strip()
    if login_customer_id:
        headers["login-customer-id"] = login_customer_id
    response = await client.post(
        (
            f"https://googleads.googleapis.com/{google_ads_api_version()}"
            f"/customers/{account_id}/googleAds:searchStream"
        ),
        headers=headers,
        json={"query": query},
    )
    if response.status_code >= 400:
        raise _error_from_response(response)
    payload = response.json() or []
    chunks = payload if isinstance(payload, list) else [payload]
    results: list[dict[str, Any]] = []
    for chunk in chunks:
        if isinstance(chunk, dict):
            results.extend(
                row for row in (chunk.get("results") or []) if isinstance(row, dict)
            )
    return results


async def _account_metadata(
    client: httpx.AsyncClient,
    *,
    access_token: str,
    account: dict[str, Any],
) -> dict[str, Any]:
    rows = await _search_stream(
        client,
        access_token=access_token,
        account=account,
        query=(
            "SELECT customer.id, customer.descriptive_name, "
            "customer.currency_code, customer.time_zone, customer.manager "
            "FROM customer LIMIT 1"
        ),
    )
    customer = (rows[0].get("customer") if rows else {}) or {}
    return {
        "display_name": customer.get("descriptiveName") or account.get("display_name"),
        "currency": customer.get("currencyCode") or account.get("currency"),
        "timezone": customer.get("timeZone") or account.get("timezone") or "Asia/Riyadh",
        "manager": customer.get("manager") is True,
    }


def _days(start: date, end: date) -> list[date]:
    return [start + timedelta(days=offset) for offset in range((end - start).days + 1)]


async def run_google_ads_reporting_sync(
    db: Any,
    user_id: str,
    *,
    date_from: str,
    date_to: str,
    now: Callable[[], datetime] = _utcnow,
) -> dict[str, Any]:
    if GOOGLE_ADS_PROVIDER_ID not in GOOGLE_PROVIDER_IDS or not google_oauth_configured():
        raise GoogleAdsReportingError(
            "google_ads_oauth_not_configured",
            "Google OAuth is not configured.",
            status_code=503,
        )
    if not google_ads_reporting_enabled():
        raise GoogleAdsReportingError(
            "google_ads_reporting_disabled",
            "Google Ads reporting is disabled.",
            status_code=503,
        )
    start = date.fromisoformat(date_from)
    end = date.fromisoformat(date_to)
    if end < start or (end - start).days + 1 > MAX_GOOGLE_ADS_DAYS:
        raise GoogleAdsReportingError(
            "google_ads_invalid_date_range",
            f"Google Ads reporting supports at most {MAX_GOOGLE_ADS_DAYS} days.",
            status_code=422,
        )

    now_value = now().astimezone(timezone.utc)
    observed_at = _iso(now_value)
    await ensure_google_ads_reporting_indexes(db)
    access_token = await _credential(db, user_id, now_value)
    accounts = await _accounts(db, user_id)
    requested_days = _days(start, end)
    account_summaries: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    provider_calls = 0

    async with httpx.AsyncClient(timeout=40.0) as client:
        for account in accounts:
            account_id = str(account["ad_account_id"])
            try:
                metadata = await _account_metadata(
                    client,
                    access_token=access_token,
                    account=account,
                )
                provider_calls += 1
                if metadata["manager"]:
                    raise GoogleAdsReportingError(
                        "google_ads_manager_account_requires_client_selection",
                        "Select a non-manager Google Ads client account for reporting.",
                        status_code=409,
                    )
                query = (
                    "SELECT customer.id, customer.currency_code, customer.time_zone, "
                    "segments.date, segments.hour, metrics.cost_micros, "
                    "metrics.impressions, metrics.clicks, metrics.conversions, "
                    "metrics.conversions_value FROM customer "
                    f"WHERE segments.date BETWEEN '{start.isoformat()}' "
                    f"AND '{end.isoformat()}'"
                )
                rows = await _search_stream(
                    client,
                    access_token=access_token,
                    account=account,
                    query=query,
                )
                provider_calls += 1
                currency = str(metadata.get("currency") or "").upper() or None
                account_timezone = str(metadata.get("timezone") or "Asia/Riyadh")
                fx_rate, fx_source = _fx_to_sar(currency)
                hourly: dict[tuple[str, int], dict[str, float]] = defaultdict(
                    lambda: {
                        "spend_native": 0.0,
                        "impressions": 0.0,
                        "clicks": 0.0,
                        "conversions": 0.0,
                        "conversion_value_native": 0.0,
                    }
                )
                for row in rows:
                    segments = row.get("segments") if isinstance(row.get("segments"), dict) else {}
                    metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
                    day_text = str(segments.get("date") or "")
                    try:
                        hour_index = int(segments.get("hour"))
                    except (TypeError, ValueError):
                        continue
                    if day_text < start.isoformat() or day_text > end.isoformat() or not 0 <= hour_index <= 23:
                        continue
                    bucket = hourly[(day_text, hour_index)]
                    bucket["spend_native"] += float(metrics.get("costMicros") or 0) / 1_000_000
                    bucket["impressions"] += float(metrics.get("impressions") or 0)
                    bucket["clicks"] += float(metrics.get("clicks") or 0)
                    bucket["conversions"] += float(metrics.get("conversions") or 0)
                    bucket["conversion_value_native"] += float(metrics.get("conversionsValue") or 0)

                rows_saved = 0
                for day in requested_days:
                    daily_native = 0.0
                    daily_impressions = 0
                    daily_clicks = 0
                    daily_conversions = 0.0
                    daily_conversion_value = 0.0
                    for hour_index in range(24):
                        bucket = hourly[(day.isoformat(), hour_index)]
                        spend_native = float(bucket["spend_native"])
                        spend_sar = (
                            round(spend_native * fx_rate, 2)
                            if fx_rate is not None
                            else None
                        )
                        await upsert_platform_hour(
                            db,
                            user_id=user_id,
                            provider="google",
                            ad_account_id=account_id,
                            display_name=metadata.get("display_name"),
                            day=day,
                            hour_index=hour_index,
                            account_timezone=account_timezone,
                            currency_native=currency,
                            spend_native=spend_native,
                            fx_rate_to_sar=fx_rate,
                            spend_sar=spend_sar,
                            impressions=int(bucket["impressions"]),
                            clicks=int(bucket["clicks"]),
                            conversions=float(bucket["conversions"]),
                            source_mode=GOOGLE_ADS_REPORTING_SOURCE_MODE,
                            observed_at=observed_at,
                        )
                        daily_native += spend_native
                        daily_impressions += int(bucket["impressions"])
                        daily_clicks += int(bucket["clicks"])
                        daily_conversions += float(bucket["conversions"])
                        daily_conversion_value += float(bucket["conversion_value_native"])
                    daily_sar = (
                        round(daily_native * fx_rate, 2)
                        if fx_rate is not None
                        else None
                    )
                    conversion_value_sar = (
                        round(daily_conversion_value * fx_rate, 2)
                        if fx_rate is not None
                        else None
                    )
                    await db[GOOGLE_ADS_DAILY_COLLECTION].update_one(
                        {
                            "user_id": user_id,
                            "provider": GOOGLE_ADS_PROVIDER_ID,
                            "ad_account_id": account_id,
                            "date": day.isoformat(),
                        },
                        {
                            "$set": {
                                "user_id": user_id,
                                "provider": GOOGLE_ADS_PROVIDER_ID,
                                "ad_account_id": account_id,
                                "display_name": metadata.get("display_name"),
                                "date": day.isoformat(),
                                "account_timezone": account_timezone,
                                "currency_native": currency,
                                "spend_native": round(daily_native, 6),
                                "fx_rate_to_sar": fx_rate,
                                "fx_source": fx_source,
                                "spend_sar": daily_sar,
                                "impressions": daily_impressions,
                                "clicks": daily_clicks,
                                "conversions": round(daily_conversions, 6),
                                "conversion_value_native": round(daily_conversion_value, 6),
                                "conversion_value_sar": conversion_value_sar,
                                "source_mode": GOOGLE_ADS_REPORTING_SOURCE_MODE,
                                "source_only": True,
                                "accounting_eligible": False,
                                "provider_write_reached": False,
                                "campaign_write_reached": False,
                                "accounting_write_reached": False,
                                "qoyod_write_reached": False,
                                "observed_at": observed_at,
                                "updated_at": observed_at,
                            },
                            "$setOnInsert": {"created_at": observed_at},
                        },
                        upsert=True,
                    )
                    rows_saved += 1
                await db.mezan_integration_accounts_v2.update_one(
                    {
                        "user_id": user_id,
                        "provider": GOOGLE_ADS_PROVIDER_ID,
                        "$or": [
                            {"external_account_id": account_id},
                            {"ad_account_id": account_id},
                        ],
                    },
                    {
                        "$set": {
                            "display_name": metadata.get("display_name"),
                            "currency": currency,
                            "timezone": account_timezone,
                            "has_data": True,
                            "last_sync_at": observed_at,
                            "last_observed_at": observed_at,
                            "data_delay_minutes": 0,
                            "health_score": 100,
                            "performance_rows_saved": rows_saved,
                            "source_mode": GOOGLE_ADS_REPORTING_SOURCE_MODE,
                        }
                    },
                )
                account_summaries.append(
                    {
                        "ad_account_id": account_id,
                        "rows_saved": rows_saved,
                        "complete": True,
                    }
                )
            except GoogleAdsReportingError as exc:
                errors.append(
                    {
                        "ad_account_id": account_id,
                        "code": exc.code,
                        "message": exc.message,
                        "retryable": exc.retryable,
                    }
                )
                account_summaries.append(
                    {
                        "ad_account_id": account_id,
                        "rows_saved": 0,
                        "complete": False,
                    }
                )

    rows_saved = sum(int(item["rows_saved"]) for item in account_summaries)
    accounts_complete = sum(bool(item["complete"]) for item in account_summaries)
    status = "complete" if accounts_complete == len(account_summaries) else "partial"
    if rows_saved == 0 and errors:
        status = "failed"
    await db.mezan_integrations_v2.update_one(
        {"user_id": user_id, "provider": GOOGLE_ADS_PROVIDER_ID},
        {
            "$set": {
                "connection_status": "connected" if status != "failed" else "error",
                "connection_provenance": "api_connection",
                "has_data": rows_saved > 0,
                "last_sync_at": observed_at if rows_saved > 0 else None,
                "data_delay_minutes": 0 if status == "complete" else None,
                "data_quality": "complete" if status == "complete" else "partial" if rows_saved else "unavailable",
                "health_score": 100 if status == "complete" else 80 if rows_saved else 40,
                "source_mode": GOOGLE_ADS_REPORTING_SOURCE_MODE,
                "checked_at": observed_at,
                "updated_at": observed_at,
            }
        },
        upsert=True,
    )
    return {
        "provider": GOOGLE_ADS_PROVIDER_ID,
        "status": status,
        "date_from": start.isoformat(),
        "date_to": end.isoformat(),
        "accounts_attempted": len(account_summaries),
        "accounts_complete": accounts_complete,
        "rows_saved": rows_saved,
        "errors_count": len(errors),
        "provider_calls": provider_calls,
        "error_samples": errors[:10],
        "source_only": True,
        "provider_write_reached": False,
        "campaign_write_reached": False,
        "accounting_write_reached": False,
        "qoyod_write_reached": False,
    }


__all__ = [
    "GOOGLE_ADS_DAILY_COLLECTION",
    "GOOGLE_ADS_PROVIDER_ID",
    "GOOGLE_ADS_REPORTING_SOURCE_MODE",
    "GoogleAdsReportingError",
    "ensure_google_ads_reporting_indexes",
    "google_ads_reporting_enabled",
    "run_google_ads_reporting_sync",
]
