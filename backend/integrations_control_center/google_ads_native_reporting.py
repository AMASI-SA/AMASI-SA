"""Read-only Google Ads GAQL reporting for Mezan Integrations V2.

The module queries GoogleAdsService.SearchStream only. It writes source-only
performance facts to a dedicated V2 collection and never mutates campaigns,
budgets, accounting, Qoyod, or transitional daily costs.
"""
from __future__ import annotations

import os
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

import httpx
from pydantic import BaseModel, Field, model_validator

from .google_ads_account_selection import (
    GOOGLE_ADS_PROVIDER_ID,
    load_selected_google_ads_accounts,
)
from .google_oauth_security import (
    GOOGLE_CREDENTIALS_COLLECTION,
    GOOGLE_SCOPE_BY_PROVIDER,
    GOOGLE_TOKEN_URL,
    decrypt_google_token,
    encrypt_google_token,
    google_oauth_configured,
)

GOOGLE_ADS_REPORTING_COLLECTION = "mezan_google_ads_performance_daily_v2"
GOOGLE_ADS_REPORTING_SOURCE_MODE = "google_ads_gaql_reporting_v2"
GOOGLE_ADS_REPORTING_ENABLED_ENV = "GOOGLE_ADS_NATIVE_REPORTING_SYNC_ENABLED"
GOOGLE_ADS_USD_TO_SAR_RATE_ENV = "GOOGLE_ADS_USD_TO_SAR_RATE"
GOOGLE_ADS_API_VERSION_ENV = "GOOGLE_ADS_API_VERSION"
GOOGLE_ADS_LOGIN_CUSTOMER_ID_ENV = "GOOGLE_ADS_LOGIN_CUSTOMER_ID"
MAX_REPORTING_DAYS = 31
MAX_REPORTING_ACCOUNTS = 20
MAX_REPORTING_ROWS = 20_000
TOKEN_EXPIRY_SKEW = timedelta(minutes=2)


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


class GoogleAdsReportingSyncInput(BaseModel):
    days: int = Field(default=7, ge=1, le=MAX_REPORTING_DAYS)
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
                    f"Google Ads reporting supports at most {MAX_REPORTING_DAYS} days"
                )
        return self


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None = None) -> str:
    return (value or _utcnow()).astimezone(timezone.utc).isoformat()


def google_ads_reporting_enabled() -> bool:
    return os.environ.get(GOOGLE_ADS_REPORTING_ENABLED_ENV, "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def google_ads_reporting_missing_configuration() -> list[str]:
    missing: list[str] = []
    if not google_oauth_configured():
        missing.append("GOOGLE_OAUTH")
    if not os.environ.get("GOOGLE_ADS_DEVELOPER_TOKEN", "").strip():
        missing.append("GOOGLE_ADS_DEVELOPER_TOKEN")
    return missing


def _range(payload: GoogleAdsReportingSyncInput, today: date) -> tuple[date, date]:
    if payload.from_date and payload.to_date:
        return date.fromisoformat(payload.from_date), date.fromisoformat(payload.to_date)
    end = today
    return end - timedelta(days=payload.days - 1), end


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


def _clean_customer_id(value: Any) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def _as_datetime(value: Any) -> datetime | None:
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


def _api_version() -> str:
    value = os.environ.get(GOOGLE_ADS_API_VERSION_ENV, "v25").strip()
    return value if value.startswith("v") else f"v{value}"


def _headers(access_token: str) -> dict[str, str]:
    headers = {
        "Authorization": f"Bearer {access_token}",
        "developer-token": os.environ.get("GOOGLE_ADS_DEVELOPER_TOKEN", "").strip(),
        "Content-Type": "application/json",
    }
    login_customer_id = _clean_customer_id(
        os.environ.get(GOOGLE_ADS_LOGIN_CUSTOMER_ID_ENV, "")
    )
    if login_customer_id:
        headers["login-customer-id"] = login_customer_id
    return headers


def _gaql(start: date, end: date) -> str:
    return " ".join(
        [
            "SELECT",
            "segments.date,",
            "customer.id,",
            "customer.descriptive_name,",
            "customer.currency_code,",
            "customer.time_zone,",
            "campaign.id,",
            "campaign.name,",
            "campaign.status,",
            "metrics.cost_micros,",
            "metrics.impressions,",
            "metrics.clicks,",
            "metrics.conversions,",
            "metrics.conversions_value",
            "FROM campaign",
            f"WHERE segments.date BETWEEN '{start.isoformat()}' AND '{end.isoformat()}'",
            "AND campaign.status != 'REMOVED'",
            "ORDER BY segments.date, campaign.id",
        ]
    )


def _response_error(response: httpx.Response) -> GoogleAdsReportingError:
    request_id = response.headers.get("request-id")
    code = f"google_ads_http_{response.status_code}"
    message = "Google Ads reporting returned an HTTP error."
    retryable = response.status_code in {429, 500, 502, 503, 504}
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    text = str(payload)[:1500]
    if response.status_code == 401:
        code = "google_ads_needs_reauth"
        message = "Google rejected the saved OAuth access token."
    elif response.status_code == 403:
        if "DEVELOPER_TOKEN" in text.upper():
            code = "google_ads_developer_token_rejected"
            message = "Google Ads rejected the configured developer token."
        else:
            code = "google_ads_permission_denied"
            message = "Google Ads denied access to the selected customer."
    elif response.status_code == 429:
        code = "google_ads_rate_limited"
        message = "Google Ads temporarily rate-limited reporting requests."
    return GoogleAdsReportingError(
        code,
        message,
        status_code=409 if code == "google_ads_needs_reauth" else 502,
        retryable=retryable,
        result={"request_id": request_id},
    )


async def ensure_google_ads_reporting_indexes(db: Any) -> None:
    await db[GOOGLE_ADS_REPORTING_COLLECTION].create_index(
        [
            ("user_id", 1),
            ("ad_account_id", 1),
            ("date", 1),
            ("campaign_id", 1),
        ],
        unique=True,
        name="google_ads_performance_user_account_date_campaign_unique",
    )
    await db[GOOGLE_ADS_REPORTING_COLLECTION].create_index(
        [("user_id", 1), ("date", -1)],
        name="google_ads_performance_user_date",
    )


async def _credential(
    db: Any,
    user_id: str,
    *,
    now: Callable[[], datetime] = _utcnow,
) -> str:
    row = await db[GOOGLE_CREDENTIALS_COLLECTION].find_one(
        {"user_id": user_id},
        {
            "_id": 0,
            "access_token_ciphertext": 1,
            "refresh_token_ciphertext": 1,
            "expires_at": 1,
            "scope": 1,
        },
    )
    if not row:
        raise GoogleAdsReportingError(
            "google_ads_oauth_credential_missing",
            "No Google OAuth credential is available.",
            status_code=409,
        )
    scopes = set(row.get("scope") or [])
    if GOOGLE_SCOPE_BY_PROVIDER[GOOGLE_ADS_PROVIDER_ID] not in scopes:
        raise GoogleAdsReportingError(
            "google_ads_scope_missing",
            "The Google Ads OAuth scope was not granted.",
            status_code=409,
        )
    access_token = decrypt_google_token(row.get("access_token_ciphertext"))
    expires_at = _as_datetime(row.get("expires_at"))
    if access_token and expires_at and expires_at > now() + TOKEN_EXPIRY_SKEW:
        return access_token

    refresh_token = decrypt_google_token(row.get("refresh_token_ciphertext"))
    if not refresh_token:
        raise GoogleAdsReportingError(
            "google_ads_refresh_token_missing",
            "Reconnect Google to obtain an offline refresh token.",
            status_code=409,
        )
    async with httpx.AsyncClient(timeout=25.0) as client:
        response = await client.post(
            GOOGLE_TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "client_id": os.environ.get("GOOGLE_OAUTH_CLIENT_ID", ""),
                "client_secret": os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET", ""),
                "refresh_token": refresh_token,
            },
        )
    if response.status_code >= 400:
        raise GoogleAdsReportingError(
            "google_ads_needs_reauth",
            "Google rejected the saved refresh token.",
            status_code=409,
        )
    payload = response.json() or {}
    access_token = str(payload.get("access_token") or "").strip()
    if not access_token:
        raise GoogleAdsReportingError(
            "google_ads_token_refresh_missing_access_token",
            "Google returned no usable access token.",
            status_code=502,
            retryable=True,
        )
    expires_in = max(int(payload.get("expires_in") or 3600), 60)
    await db[GOOGLE_CREDENTIALS_COLLECTION].update_one(
        {"user_id": user_id},
        {
            "$set": {
                "access_token_ciphertext": encrypt_google_token(access_token),
                "expires_at": now() + timedelta(seconds=expires_in),
                "updated_at": now(),
            }
        },
    )
    return access_token


async def _fetch_rows(
    client: httpx.AsyncClient,
    *,
    access_token: str,
    customer_id: str,
    start: date,
    end: date,
) -> tuple[list[dict[str, Any]], str | None]:
    url = (
        f"https://googleads.googleapis.com/{_api_version()}/customers/"
        f"{customer_id}/googleAds:searchStream"
    )
    response = await client.post(
        url,
        headers=_headers(access_token),
        json={"query": _gaql(start, end)},
    )
    if response.status_code >= 400:
        raise _response_error(response)
    try:
        payload = response.json()
    except ValueError as exc:
        raise GoogleAdsReportingError(
            "google_ads_invalid_json",
            "Google Ads reporting returned invalid JSON.",
            status_code=502,
            retryable=True,
        ) from exc
    batches = payload if isinstance(payload, list) else [payload]
    rows: list[dict[str, Any]] = []
    for batch in batches:
        if not isinstance(batch, dict):
            continue
        for row in batch.get("results") or []:
            if isinstance(row, dict):
                rows.append(row)
                if len(rows) > MAX_REPORTING_ROWS:
                    raise GoogleAdsReportingError(
                        "google_ads_reporting_row_limit_exceeded",
                        "Google Ads returned more rows than the safe run limit.",
                        status_code=409,
                    )
    return rows, response.headers.get("request-id")


def _normalized_row(
    raw: dict[str, Any],
    *,
    fallback_customer_id: str,
    observed_at: str,
    request_id: str | None,
) -> dict[str, Any] | None:
    segments = raw.get("segments") or {}
    customer = raw.get("customer") or {}
    campaign = raw.get("campaign") or {}
    metrics = raw.get("metrics") or {}
    day = str(segments.get("date") or "").strip()
    campaign_id = str(campaign.get("id") or "").strip()
    if not day or not campaign_id:
        return None
    customer_id = _clean_customer_id(customer.get("id") or fallback_customer_id)
    currency = str(customer.get("currencyCode") or "").strip().upper() or None
    fx_rate, fx_source = _fx_to_sar(currency)
    try:
        spend_native = float(metrics.get("costMicros") or 0) / 1_000_000
    except (TypeError, ValueError):
        spend_native = 0.0
    try:
        conversions = float(metrics.get("conversions") or 0)
    except (TypeError, ValueError):
        conversions = 0.0
    try:
        conversion_value_native = float(metrics.get("conversionsValue") or 0)
    except (TypeError, ValueError):
        conversion_value_native = 0.0
    spend_sar = round(spend_native * fx_rate, 2) if fx_rate is not None else None
    conversion_value_sar = (
        round(conversion_value_native * fx_rate, 2)
        if fx_rate is not None
        else None
    )
    return {
        "provider": GOOGLE_ADS_PROVIDER_ID,
        "ad_account_id": customer_id,
        "display_name": customer.get("descriptiveName") or f"Google Ads {customer_id}",
        "account_timezone": customer.get("timeZone"),
        "currency_native": currency,
        "date": day,
        "campaign_id": campaign_id,
        "campaign_name": campaign.get("name") or campaign_id,
        "campaign_status": campaign.get("status"),
        "spend_native": round(spend_native, 6),
        "fx_rate_to_sar": fx_rate,
        "fx_source": fx_source,
        "spend_sar": spend_sar,
        "impressions": int(float(metrics.get("impressions") or 0)),
        "clicks": int(float(metrics.get("clicks") or 0)),
        "conversions": conversions,
        "purchase_value_native": round(conversion_value_native, 6),
        "purchase_value_sar": conversion_value_sar,
        "request_id": request_id,
        "source_mode": GOOGLE_ADS_REPORTING_SOURCE_MODE,
        "source_only": True,
        "accounting_eligible": False,
        "observed_at": observed_at,
        "updated_at": observed_at,
    }


async def run_google_ads_reporting_sync(
    db: Any,
    user_id: str,
    payload: GoogleAdsReportingSyncInput,
    *,
    now: Callable[[], datetime] = _utcnow,
) -> dict[str, Any]:
    missing = google_ads_reporting_missing_configuration()
    if missing:
        raise GoogleAdsReportingError(
            "google_ads_reporting_not_configured",
            "Google Ads reporting configuration is incomplete.",
            status_code=503,
            result={"missing": missing},
        )
    if not google_ads_reporting_enabled():
        raise GoogleAdsReportingError(
            "google_ads_reporting_disabled",
            "Google Ads native reporting is disabled by the safety flag.",
            status_code=503,
        )

    await ensure_google_ads_reporting_indexes(db)
    access_token = await _credential(db, user_id, now=now)
    accounts = await load_selected_google_ads_accounts(db, user_id)
    if len(accounts) > MAX_REPORTING_ACCOUNTS:
        raise GoogleAdsReportingError(
            "google_ads_reporting_account_limit_exceeded",
            f"At most {MAX_REPORTING_ACCOUNTS} Google Ads accounts may sync per run.",
            status_code=409,
        )
    start, end = _range(payload, now().astimezone(timezone.utc).date())
    observed_at = _iso(now())
    summaries: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    provider_calls = 0

    async with httpx.AsyncClient(timeout=60.0) as client:
        for account in accounts:
            account_id = _clean_customer_id(
                account.get("ad_account_id") or account.get("external_account_id")
            )
            saved = 0
            account_error: dict[str, Any] | None = None
            latest_currency = account.get("currency")
            latest_timezone = account.get("timezone")
            latest_name = account.get("display_name")
            try:
                rows, request_id = await _fetch_rows(
                    client,
                    access_token=access_token,
                    customer_id=account_id,
                    start=start,
                    end=end,
                )
                provider_calls += 1
                for raw in rows:
                    normalized = _normalized_row(
                        raw,
                        fallback_customer_id=account_id,
                        observed_at=observed_at,
                        request_id=request_id,
                    )
                    if not normalized:
                        continue
                    latest_currency = normalized.get("currency_native") or latest_currency
                    latest_timezone = normalized.get("account_timezone") or latest_timezone
                    latest_name = normalized.get("display_name") or latest_name
                    await db[GOOGLE_ADS_REPORTING_COLLECTION].update_one(
                        {
                            "user_id": user_id,
                            "provider": GOOGLE_ADS_PROVIDER_ID,
                            "ad_account_id": normalized["ad_account_id"],
                            "date": normalized["date"],
                            "campaign_id": normalized["campaign_id"],
                        },
                        {
                            "$set": {"user_id": user_id, **normalized},
                            "$setOnInsert": {"created_at": observed_at},
                        },
                        upsert=True,
                    )
                    saved += 1
            except GoogleAdsReportingError as exc:
                provider_calls += 1
                if exc.code == "google_ads_needs_reauth":
                    raise
                account_error = {
                    "ad_account_id": account_id,
                    "code": exc.code,
                    "retryable": exc.retryable,
                }
                errors.append(account_error)

            complete = account_error is None
            await db.mezan_integration_accounts_v2.update_one(
                {
                    "user_id": user_id,
                    "provider": GOOGLE_ADS_PROVIDER_ID,
                    "external_account_id": account_id,
                },
                {
                    "$set": {
                        "display_name": latest_name or f"Google Ads {account_id}",
                        "currency": latest_currency,
                        "timezone": latest_timezone,
                        "performance_rows_saved": saved,
                        "has_data": saved > 0,
                        "last_sync_at": observed_at if complete else account.get("last_sync_at"),
                        "data_delay_minutes": 0 if complete else None,
                        "health_score": 100 if complete else 70,
                        "source_mode": GOOGLE_ADS_REPORTING_SOURCE_MODE,
                        "last_observed_at": observed_at,
                    }
                },
            )
            summaries.append(
                {
                    "ad_account_id": account_id,
                    "display_name": latest_name,
                    "currency": latest_currency,
                    "timezone": latest_timezone,
                    "rows_saved": saved,
                    "complete": complete,
                    "error": account_error,
                }
            )

    rows_saved = sum(item["rows_saved"] for item in summaries)
    accounts_complete = sum(bool(item["complete"]) for item in summaries)
    status = "complete" if accounts_complete == len(summaries) else "partial"
    await db.mezan_integrations_v2.update_one(
        {"user_id": user_id, "provider": GOOGLE_ADS_PROVIDER_ID},
        {
            "$set": {
                "connection_status": "connected",
                "connection_provenance": "api_connection",
                "source_mode": GOOGLE_ADS_REPORTING_SOURCE_MODE,
                "last_sync_at": observed_at if accounts_complete else None,
                "data_delay_minutes": 0 if accounts_complete else None,
                "data_quality": "good" if accounts_complete else "partial",
                "has_data": rows_saved > 0,
                "updated_at": observed_at,
            }
        },
        upsert=True,
    )
    return {
        "provider": GOOGLE_ADS_PROVIDER_ID,
        "status": status,
        "from_date": start.isoformat(),
        "to_date": end.isoformat(),
        "accounts_attempted": len(summaries),
        "accounts_complete": accounts_complete,
        "rows_saved": rows_saved,
        "provider_calls": provider_calls,
        "errors_count": len(errors),
        "accounts": summaries,
        "errors": errors,
        "source_mode": GOOGLE_ADS_REPORTING_SOURCE_MODE,
        "source_only": True,
        "accounting_eligible": False,
        "provider_write_reached": False,
        "campaign_write_reached": False,
        "accounting_write_reached": False,
        "qoyod_write_reached": False,
    }


__all__ = [
    "GOOGLE_ADS_PROVIDER_ID",
    "GOOGLE_ADS_REPORTING_COLLECTION",
    "GOOGLE_ADS_REPORTING_ENABLED_ENV",
    "GOOGLE_ADS_REPORTING_SOURCE_MODE",
    "GoogleAdsReportingError",
    "GoogleAdsReportingSyncInput",
    "ensure_google_ads_reporting_indexes",
    "google_ads_reporting_enabled",
    "google_ads_reporting_missing_configuration",
    "run_google_ads_reporting_sync",
]
