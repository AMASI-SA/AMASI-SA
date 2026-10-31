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

from .ad_daily_close_proof import StreamRows, close_proof, google_evidence

from .ads_platform_hourly import (
    ensure_platform_hourly_indexes,
    upsert_platform_hour,
)
from .google_credential_refresh_cas import (
    CREDENTIAL_REFRESH_PROJECTION,
    GoogleRefreshPersistenceError,
    persist_refreshed_google_credential,
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
    try:
        await persist_refreshed_google_credential(
            db, user_id=str(row.get("user_id") or ""), previous=row,
            values={"access_token_ciphertext": encrypt_google_token(access_token),
                    "expires_at": now + timedelta(seconds=expires_in), "updated_at": now})
    except GoogleRefreshPersistenceError as exc:
        raise GoogleAdsReportingError(exc.code,
            "لم يتأكد حفظ جلسة Google الحالية؛ حدّث الاتصال دون إعادة إرسال العملية.",
            status_code=exc.status_code, retryable=False) from None
    return access_token


async def _credential(db: Any, user_id: str, now: datetime) -> str:
    row = await db[GOOGLE_CREDENTIALS_COLLECTION].find_one(
        {"user_id": user_id},
        CREDENTIAL_REFRESH_PROJECTION,
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


GOOGLE_BOUND_READ_TIMEOUT_SECONDS = 90.0
GOOGLE_BOUND_READ_CLEANUP_SECONDS = 2.0


class GoogleBoundReadError(RuntimeError):
    """A fixed safe code only; never attach credential or driver details."""

    def __init__(self, code="google_bound_read_unproved"):
        self.code = code
        super().__init__(code)


def google_bound_read_session(db, user_id, *, ads_account, merchant_id, clock=None):
    """Opaque same-credential read session; NOT tenant/account authorization.

    The owner route must independently prove/recheck membership and complete
    native Ads-to-Merchant links. Keep its result inside this async context
    until the final credential fence succeeds. Existing refresh callers and
    writers are unchanged. No token, ciphertext, headers or raw client escapes.
    """
    import asyncio
    from contextlib import asynccontextmanager
    from copy import deepcopy
    import hmac
    import re
    import uuid

    from bson import BSON
    from pymongo import ReadPreference
    from pymongo.read_concern import ReadConcern
    from .google_ads_client import GoogleAdsClient

    clock = clock or _utcnow

    def fail(code="google_bound_read_unproved"):
        raise GoogleBoundReadError(code) from None

    def timestamp():
        value = clock()
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            fail()
        return value.astimezone(timezone.utc)

    @asynccontextmanager
    async def opened():
        token, snapshot, source_bytes, ads, merchant = "", {}, b"", None, None
        closed = False

        async def cleanup():
            nonlocal token, source_bytes, ads, merchant, closed
            closed = True
            snapshot.clear()
            token = ""
            source_bytes = b""
            clients = tuple(client for client in (ads, merchant) if client is not None)
            ads = merchant = None

            async def close_one(client):
                try:
                    async with asyncio.timeout(GOOGLE_BOUND_READ_CLEANUP_SECONDS):
                        close = getattr(client, "close", None) or client.aclose
                        await close()
                except Exception:
                    pass

            # The outer deadline may already have cancelled the body. Cleanup
            # owns a separate finite deadline, and gather awaits both closes.
            # External cancellation propagates through gather to both clients.
            await asyncio.gather(*(close_one(client) for client in clients))

        def exact_snapshot(row):
            # BSON retains scalar types and absent/null fields. Sorting keys
            # avoids treating harmless field order as a different credential.
            return BSON.encode({key: row[key] for key in sorted(row)})

        try:
            if (not isinstance(user_id, str) or not user_id or len(user_id) > 128
                or not isinstance(ads_account, dict) or not isinstance(merchant_id, str)
                or not re.fullmatch(r"[1-9][0-9]{0,29}", merchant_id)):
                fail()
            account = deepcopy(ads_account)
            customer = account.get("ad_account_id") or account.get("external_account_id")
            if (not isinstance(customer, str) or not re.fullmatch(r"[1-9][0-9]{0,29}", customer)
                or any(account.get(key) not in (None, customer) for key in ("ad_account_id", "external_account_id"))):
                fail()
            # Refresh remains inside the existing CAS-protected boundary.
            token = await _credential(db, user_id, timestamp())
            collection = db[GOOGLE_CREDENTIALS_COLLECTION].with_options(
                read_concern=ReadConcern("majority"), read_preference=ReadPreference.PRIMARY)
            rows = await collection.find({"user_id": user_id}, CREDENTIAL_REFRESH_PROJECTION).limit(2).to_list(length=2)
            if len(rows) != 1 or rows[0].get("user_id") != user_id:
                fail()
            snapshot = deepcopy(rows[0])
            current_token = decrypt_google_token(snapshot.get("access_token_ciphertext"))
            if (not isinstance(token, str) or not token or not isinstance(current_token, str)
                or not hmac.compare_digest(token.encode(), current_token.encode())):
                fail("google_bound_read_credential_changed")
            current_token = ""
            scopes = snapshot.get("scope")
            if (not isinstance(scopes, list) or any(not isinstance(s, str) for s in scopes)
                or not {"https://www.googleapis.com/auth/adwords", "https://www.googleapis.com/auth/content"} <= set(scopes)
                or not isinstance(snapshot.get("google_subject"), str) or not snapshot["google_subject"]
                or snapshot.get("token_type") != "Bearer"):
                fail("google_bound_read_capability_unproved")
            expiry = _parse_datetime(snapshot.get("expires_at"))
            if expiry is None or expiry <= timestamp() + timedelta(minutes=2):
                fail("google_bound_read_expiry_unproved")
            source_bytes = exact_snapshot(snapshot)
            evidence_id = "google-bound-read-" + uuid.uuid4().hex

            async def current():
                if closed or expiry <= timestamp() + timedelta(seconds=45):
                    fail("google_bound_read_expiry_unproved")
                try:
                    matched = await collection.find({"user_id": user_id}, CREDENTIAL_REFRESH_PROJECTION).limit(2).to_list(length=2)
                    if len(matched) != 1 or not hmac.compare_digest(exact_snapshot(matched[0]), source_bytes):
                        fail("google_bound_read_credential_changed")
                except GoogleBoundReadError:
                    raise
                except Exception:
                    fail()

            await current()
            ads = GoogleAdsClient(token, account)
            merchant = httpx.AsyncClient(headers={"Authorization": "Bearer " + token},
                timeout=12.0, follow_redirects=False, max_redirects=0,
                limits=httpx.Limits(max_connections=1, max_keepalive_connections=1))
            merchant_url = "https://merchantapi.googleapis.com/products/v1/accounts/" + merchant_id + "/products"

            class SafeResponse:
                __slots__ = ("__response",)

                def __init__(self, response):
                    self.__response = response

                def __repr__(self):
                    return "<GoogleBoundReadResponse>"

                @property
                def status_code(self):
                    return self.__response.status_code

                @property
                def headers(self):
                    return {key: self.__response.headers[key] for key in
                        ("content-type", "content-encoding") if key in self.__response.headers}

                async def aiter_bytes(self, chunk_size=8192):
                    try:
                        async for chunk in self.__response.aiter_bytes(chunk_size=chunk_size):
                            yield chunk
                    except Exception:
                        fail("google_bound_read_provider_unavailable")

            class ReadSession:
                __slots__ = ()

                def __repr__(self):
                    return "<GoogleBoundReadSession>"

                @property
                def evidence_id(self):
                    return evidence_id

                async def check_current(self):
                    await current()

                async def account_metadata(self):
                    try:
                        await current()
                        result = await ads.account_metadata()
                        await current()
                        return result
                    except GoogleBoundReadError:
                        raise
                    except Exception:
                        fail("google_bound_read_provider_unavailable")

                async def search(self, query_text, *, max_rows=2000):
                    try:
                        await current()
                        result = await ads.search(query_text, max_rows=max_rows)
                        await current()
                        return result
                    except GoogleBoundReadError:
                        raise
                    except Exception:
                        fail("google_bound_read_provider_unavailable")

                @asynccontextmanager
                async def stream(self, method, url, *, params, headers, follow_redirects):
                    if (method != "GET" or url != merchant_url or follow_redirects is not False
                        or headers != {"Accept-Encoding": "identity"} or not isinstance(params, dict)
                        or set(params) - {"pageSize", "pageToken"}
                        or type(params.get("pageSize")) is not int or not 1 <= params["pageSize"] <= 1000
                        or "pageToken" in params and (not isinstance(params["pageToken"], str) or not 1 <= len(params["pageToken"]) <= 4096)):
                        fail("google_bound_read_request_refused")
                    try:
                        await current()
                        async with merchant.stream("GET", merchant_url, params=dict(params),
                                headers={"Accept-Encoding": "identity"}, follow_redirects=False) as response:
                            yield SafeResponse(response)
                        await current()
                    except GoogleBoundReadError:
                        raise
                    except Exception:
                        fail("google_bound_read_provider_unavailable")

                @asynccontextmanager
                async def stream_merchant_data_sources(self, *, page_token=None):
                    # DataSources v1 list only: the bound account, method and
                    # minimal non-sensitive fields mask cannot be overridden.
                    from .google_merchant_data_sources_proof import data_source_request, DataSourceProofError
                    try:
                        url, params = data_source_request(merchant_id, page_token)
                    except DataSourceProofError:
                        fail("google_bound_read_request_refused")
                    try:
                        await current()
                        async with merchant.stream("GET", url, params=params,
                                headers={"Accept-Encoding": "identity"}, follow_redirects=False) as response:
                            yield SafeResponse(response)
                        await current()
                    except GoogleBoundReadError:
                        raise
                    except Exception:
                        fail("google_bound_read_provider_unavailable")

        except asyncio.CancelledError:
            await cleanup()
            raise
        except GoogleBoundReadError:
            await cleanup()
            raise
        except Exception:
            await cleanup()
            raise GoogleBoundReadError() from None
        try:
            yield ReadSession()
            await current()
        finally:
            # Cleanup must not leak transport exception repr or replace the
            # result with a raw credential-bearing driver exception.
            await cleanup()

    @asynccontextmanager
    async def bounded():
        try:
            async with asyncio.timeout(GOOGLE_BOUND_READ_TIMEOUT_SECONDS):
                async with opened() as session:
                    yield session
        except TimeoutError:
            raise GoogleBoundReadError("google_bound_read_timeout") from None

    return bounded()


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
    complete_response = isinstance(payload, list) and bool(payload)
    for chunk in chunks:
        if (not isinstance(chunk, dict) or chunk.get("error") or chunk.get("nextPageToken")
                or not isinstance(chunk.get("results"), list)
                or any(not isinstance(row, dict) for row in chunk.get("results", []))):
            complete_response = False
        if isinstance(chunk, dict):
            results.extend(
                row for row in (chunk.get("results") or []) if isinstance(row, dict)
            )
    return StreamRows(results, complete_response)


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
        "provider_identity_proven": (getattr(rows, "complete_response", False) is True
            and len(rows) == 1 and str(customer.get("id") or "") == str(account["ad_account_id"])
            and bool(customer.get("currencyCode")) and bool(customer.get("timeZone"))),
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
                                "source_close_proof": close_proof(
                                    account_id=account_id, business_date=day.isoformat(),
                                    timezone=account_timezone, currency=currency,
                                    source_mode=GOOGLE_ADS_REPORTING_SOURCE_MODE, observed_at=observed_at,
                                    **google_evidence(rows, metadata, account_id, day, start, end)),
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
    # Report availability is not OAuth/account ownership authority. A failed
    # report must not revoke management, and partial/successful reads must not
    # restore a revoked connection or create an unverified connection record.
    await db.mezan_integrations_v2.update_one(
        {"user_id": user_id, "provider": GOOGLE_ADS_PROVIDER_ID},
        {
            "$set": {
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
        upsert=False,
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
