"""Shared primitives for the native, read-only Snapchat V2 data plane."""
from __future__ import annotations

import asyncio
import math
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, Field

from .snapchat_oauth_security import (
    SNAPCHAT_CREDENTIALS_COLLECTION,
    SNAPCHAT_PROVIDER_ID,
    SNAPCHAT_REQUESTED_SCOPES,
    SNAPCHAT_TOKEN_URL,
    decrypt_snapchat_token,
    encrypt_snapchat_token,
)

SNAPCHAT_API_BASE = "https://adsapi.snapchat.com/v1"
BUSINESS_TIMEZONE = "Asia/Riyadh"
MAX_SYNC_DAYS = 62
MAX_SYNC_ACCOUNTS = 20
MAX_PROVIDER_CALLS = 250
MAX_PAGES = 10
SNAPCHAT_PROVIDER_REQUESTS_PER_SECOND = 6.0
SNAPCHAT_PROVIDER_MIN_REQUEST_INTERVAL_SECONDS = (
    1.0 / SNAPCHAT_PROVIDER_REQUESTS_PER_SECOND
)
SNAPCHAT_HTTP_429_MAX_RETRIES = 2
SNAPCHAT_HTTP_429_MAX_RETRY_AFTER_SECONDS = 8.0
MAX_ENTITY_ROWS_PER_TYPE = 5000
DEFAULT_USD_TO_SAR = 3.75
ATTRIBUTION_MODEL = "swipe_28d_view_1d_conversion_time"
SNAPCHAT_NATIVE_SYNC_ENABLED_ENV = "MEZAN_SNAPCHAT_NATIVE_SYNC_V2_ENABLED"
SNAPCHAT_NATIVE_SYNC_SOURCE_MODE = "snapchat_marketing_native_sync_v2"
SNAPCHAT_ENTITY_COLLECTION = "mezan_snapchat_entities_v2"
SNAPCHAT_PERFORMANCE_COLLECTION = "mezan_snapchat_performance_daily_v2"
SNAPCHAT_NATIVE_SYNC_LOCK_TTL = timedelta(hours=4)
SNAPCHAT_NATIVE_SYNC_IDEMPOTENCY_WINDOW = timedelta(minutes=5)
NATIVE_RESPONSE_KEYS = (
    "run_id", "provider", "status", "date_from", "date_to",
    "accounts_attempted", "accounts_complete", "rows_saved", "errors_count",
    "source_only", "accounting_write_reached", "qoyod_write_reached",
)


class SnapchatNativeSyncInput(BaseModel):
    days: int = Field(default=30, ge=1, le=MAX_SYNC_DAYS)
    from_date: str | None = None
    to_date: str | None = None
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=128)


class SnapchatNativeSyncError(Exception):
    def __init__(self, code: str, message: str, *, status_code: int,
                 retryable: bool = False, result: dict[str, Any] | None = None) -> None:
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


def _collection(db: Any, name: str) -> Any:
    try:
        return db[name]
    except (KeyError, TypeError, AttributeError):
        return getattr(db, name)


def _parse_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _timezone(value: Any):
    name = str(value or "").strip() or "UTC"
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:  # noqa: BLE001
        return timezone.utc


def _as_number(value: Any) -> int | float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(parsed):
        return None
    return int(parsed) if parsed.is_integer() else parsed


def _safe_next_url(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = urlsplit(text)
    except Exception:  # noqa: BLE001
        return None
    if parsed.scheme != "https" or parsed.hostname != "adsapi.snapchat.com":
        return None
    return text if parsed.path.startswith("/v1/") else None


def _parse_date(value: str, field_name: str) -> date:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise SnapchatNativeSyncError(
            "invalid_date", f"{field_name} must use YYYY-MM-DD.", status_code=400
        ) from exc


def enumerate_native_sync_dates(payload: SnapchatNativeSyncInput, *, today: date) -> list[date]:
    has_from, has_to = bool(payload.from_date), bool(payload.to_date)
    if has_from != has_to:
        raise SnapchatNativeSyncError(
            "date_range_incomplete", "from_date and to_date must be supplied together.",
            status_code=400,
        )
    if has_from:
        start = _parse_date(payload.from_date or "", "from_date")
        end = _parse_date(payload.to_date or "", "to_date")
        if end < start:
            raise SnapchatNativeSyncError(
                "invalid_date_range", "to_date must be on or after from_date.", status_code=400
            )
        days = (end - start).days + 1
        if days > MAX_SYNC_DAYS:
            raise SnapchatNativeSyncError(
                "date_range_too_wide", f"Date range cannot exceed {MAX_SYNC_DAYS} days.",
                status_code=400,
            )
        return [start + timedelta(days=offset) for offset in range(days)]
    start = today - timedelta(days=payload.days - 1)
    return [start + timedelta(days=offset) for offset in range(payload.days)]


def snapchat_native_sync_enabled() -> bool:
    value = str(os.environ.get(SNAPCHAT_NATIVE_SYNC_ENABLED_ENV, "true")).strip().lower()
    return value in {"1", "true", "on", "yes", "enabled"}


async def ensure_snapchat_native_sync_indexes(db: Any) -> None:
    entities = _collection(db, SNAPCHAT_ENTITY_COLLECTION)
    performance = _collection(db, SNAPCHAT_PERFORMANCE_COLLECTION)
    await entities.create_index(
        [("user_id", 1), ("ad_account_id", 1), ("entity_type", 1), ("external_id", 1)],
        unique=True, name="mezan_snapchat_entities_v2_identity_unique",
    )
    await entities.create_index(
        [("user_id", 1), ("entity_type", 1), ("updated_at", -1)],
        name="mezan_snapchat_entities_v2_type_latest",
    )
    await performance.create_index(
        [("user_id", 1), ("ad_account_id", 1), ("entity_type", 1),
         ("external_id", 1), ("date", 1), ("attribution_model", 1)],
        unique=True, name="mezan_snapchat_performance_v2_identity_unique",
    )
    await performance.create_index(
        [("user_id", 1), ("date", -1), ("entity_type", 1)],
        name="mezan_snapchat_performance_v2_date",
    )



def _safe_provider_text(value: Any, *, limit: int) -> str:
    text = " ".join(str(value or "").split()).strip()
    if not text:
        return ""
    lowered = text.lower()
    if any(
        secret in lowered
        for secret in (
            "authorization",
            "bearer ",
            "access_token",
            "refresh_token",
            "client_secret",
        )
    ):
        return ""
    return text[:limit]


def _safe_provider_error_detail(payload: Any) -> dict[str, str]:
    """Extract only allow-listed, bounded Snapchat error metadata."""
    if not isinstance(payload, dict):
        return {}
    candidates: list[dict[str, Any]] = [payload]
    nested_error = payload.get("error")
    if isinstance(nested_error, dict):
        candidates.append(nested_error)
    nested_errors = payload.get("errors")
    if isinstance(nested_errors, list):
        candidates.extend(
            item for item in nested_errors[:3] if isinstance(item, dict)
        )
    code = ""
    message = ""
    for item in candidates:
        if not code:
            for key in ("error_code", "code", "request_status", "status"):
                code = _safe_provider_text(item.get(key), limit=80)
                if code:
                    break
        if not message:
            for key in (
                "error_message",
                "debug_message",
                "message",
                "description",
            ):
                message = _safe_provider_text(item.get(key), limit=240)
                if message:
                    break
        if code and message:
            break
    return {
        key: value
        for key, value in (
            ("provider_error_code", code),
            ("provider_error_message", message),
        )
        if value
    }

@dataclass
class SnapchatSyncContext:
    db: Any
    user_id: str
    now: Callable[[], datetime] = _utcnow
    provider_calls: int = 0
    usd_rate_cache: float | None = None
    failure_stage_observer: Callable[[str], None] | None = None
    defer_financial_fact_writes: bool = False
    deferred_financial_fact_writes: list[dict[str, Any]] = field(
        default_factory=list,
        repr=False,
    )
    _provider_request_lock: asyncio.Lock | None = field(
        default=None,
        init=False,
        repr=False,
    )
    _provider_request_last_started: float | None = field(
        default=None,
        init=False,
        repr=False,
    )

    def now_iso(self) -> str:
        return _iso(self.now())

    def observe_failure_stage(self, stage: str) -> None:
        if self.failure_stage_observer is not None:
            self.failure_stage_observer(stage)

    async def _provider_get(
        self,
        client: httpx.AsyncClient,
        url: str,
        *,
        headers: dict[str, str],
        params: dict[str, Any] | None,
    ) -> httpx.Response:
        self.observe_failure_stage("provider_refresh")
        self.provider_calls += 1
        if self.provider_calls > MAX_PROVIDER_CALLS:
            raise SnapchatNativeSyncError(
                "snapchat_provider_call_budget_exceeded",
                f"Snapchat sync exceeded the {MAX_PROVIDER_CALLS} call budget.",
                status_code=400,
            )
        if self._provider_request_lock is None:
            self._provider_request_lock = asyncio.Lock()
        async with self._provider_request_lock:
            loop = asyncio.get_running_loop()
            if self._provider_request_last_started is not None:
                delay = SNAPCHAT_PROVIDER_MIN_REQUEST_INTERVAL_SECONDS - (
                    loop.time() - self._provider_request_last_started
                )
                if delay > 0:
                    await asyncio.sleep(delay)
            self._provider_request_last_started = loop.time()
        try:
            return await client.get(url, headers=headers, params=params)
        except httpx.HTTPError as exc:
            raise SnapchatNativeSyncError(
                "snapchat_provider_network_error",
                "Snapchat provider request failed.",
                status_code=502,
                retryable=True,
            ) from exc

    @staticmethod
    def _http_429_retry_delay(response: httpx.Response, attempt: int) -> float:
        raw = str(response.headers.get("Retry-After") or "").strip()
        try:
            parsed = float(raw)
        except (TypeError, ValueError, OverflowError):
            parsed = float(2 ** attempt)
        if not math.isfinite(parsed):
            parsed = float(2 ** attempt)
        return min(
            max(parsed, SNAPCHAT_PROVIDER_MIN_REQUEST_INTERVAL_SECONDS),
            SNAPCHAT_HTTP_429_MAX_RETRY_AFTER_SECONDS,
        )

    async def get_json(self, client: httpx.AsyncClient, url: str, *,
                       headers: dict[str, str], params: dict[str, Any] | None = None) -> dict:
        request_headers = headers
        response = await self._provider_get(
            client,
            url,
            headers=request_headers,
            params=params,
        )
        if response.status_code == 401:
            fresh_access = await self.access_token(force_refresh=True)
            retry_headers = dict(headers)
            retry_headers["Authorization"] = f"Bearer {fresh_access}"
            request_headers = retry_headers
            response = await self._provider_get(
                client,
                url,
                headers=request_headers,
                params=params,
            )

        for retry_index in range(SNAPCHAT_HTTP_429_MAX_RETRIES):
            if response.status_code != 429:
                break
            await asyncio.sleep(
                self._http_429_retry_delay(response, retry_index)
            )
            response = await self._provider_get(
                client,
                url,
                headers=request_headers,
                params=params,
            )

        if response.status_code == 401:
            raise SnapchatNativeSyncError(
                "snapchat_needs_reauth",
                "Snapchat authorization must be renewed.",
                status_code=409,
                result={"needs_reauth": True},
            )

        if response.status_code >= 400:
            try:
                provider_payload = response.json() or {}
            except (TypeError, ValueError):
                provider_payload = {}
            detail = _safe_provider_error_detail(provider_payload)
            provider_code = detail.get("provider_error_code", "")
            provider_message = detail.get("provider_error_message", "")
            safe_suffix = ": ".join(
                part for part in (provider_code, provider_message) if part
            )
            message = "Snapchat rejected a read-only data request."
            if safe_suffix:
                message = f"{message} Provider: {safe_suffix}"
            raise SnapchatNativeSyncError(
                f"snapchat_provider_http_{response.status_code}",
                message,
                status_code=502,
                retryable=(
                    response.status_code == 429
                    or response.status_code >= 500
                ),
                result=detail,
            )
        try:
            payload = response.json() or {}
        except (TypeError, ValueError) as exc:
            raise SnapchatNativeSyncError(
                "snapchat_provider_invalid_json", "Snapchat returned an invalid response.",
                status_code=502, retryable=True,
            ) from exc
        if not isinstance(payload, dict):
            raise SnapchatNativeSyncError(
                "snapchat_provider_invalid_payload", "Snapchat returned an invalid response.",
                status_code=502, retryable=True,
            )
        request_status = str(payload.get("request_status") or "").upper()
        if "FAIL" in request_status or "ERROR" in request_status:
            raise SnapchatNativeSyncError(
                "snapchat_provider_request_failed",
                "Snapchat reported a failed read-only request.", status_code=502, retryable=True,
            )
        return payload

    async def access_token(self, *, force_refresh: bool = False) -> str:
        self.observe_failure_stage("credential_decrypt_or_refresh")
        credentials = await _collection(self.db, SNAPCHAT_CREDENTIALS_COLLECTION).find_one(
            {"user_id": self.user_id, "provider": SNAPCHAT_PROVIDER_ID},
            {"_id": 0, "access_token_ciphertext": 1, "refresh_token_ciphertext": 1,
             "access_token_expires_at": 1, "scope": 1},
        )
        if not credentials:
            raise SnapchatNativeSyncError(
                "snapchat_not_connected", "Snapchat is not connected through Mezan V2.",
                status_code=409,
            )
        try:
            access = decrypt_snapchat_token(credentials.get("access_token_ciphertext"))
            refresh = decrypt_snapchat_token(credentials.get("refresh_token_ciphertext"))
        except ValueError as exc:
            raise SnapchatNativeSyncError(
                "snapchat_credential_decryption_failed",
                "Snapchat credentials could not be decrypted.", status_code=500,
            ) from exc
        if not refresh:
            raise SnapchatNativeSyncError(
                "snapchat_needs_reauth", "Snapchat authorization must be renewed.",
                status_code=409, result={"needs_reauth": True},
            )
        expires_at = credentials.get("access_token_expires_at")
        if isinstance(expires_at, str):
            expires_at = _parse_datetime(expires_at)
        if (
            not force_refresh
            and access
            and isinstance(expires_at, datetime)
            and expires_at.astimezone(timezone.utc)
            > self.now().astimezone(timezone.utc) + timedelta(seconds=120)
        ):
            return access

        client_id = os.environ.get("SNAPCHAT_MARKETING_CLIENT_ID", "").strip()
        client_secret = os.environ.get("SNAPCHAT_MARKETING_CLIENT_SECRET", "").strip()
        if not client_id or not client_secret:
            raise SnapchatNativeSyncError(
                "snapchat_oauth_not_configured",
                "Snapchat platform OAuth settings are incomplete.", status_code=503,
            )
        data = {"refresh_token": refresh, "client_id": client_id,
                "client_secret": client_secret, "grant_type": "refresh_token"}
        try:
            async with httpx.AsyncClient(timeout=25.0) as client:
                response = await client.post(SNAPCHAT_TOKEN_URL, data=data)
                if response.status_code == 400 and "invalid_client" in (response.text or "").lower():
                    response = await client.post(
                        SNAPCHAT_TOKEN_URL,
                        data={"refresh_token": refresh, "grant_type": "refresh_token"},
                        auth=(client_id, client_secret),
                    )
        except httpx.HTTPError as exc:
            raise SnapchatNativeSyncError(
                "snapchat_token_refresh_failed", "Snapchat token refresh failed.",
                status_code=502, retryable=True,
            ) from exc
        if response.status_code >= 400:
            try:
                error_payload = response.json() or {}
            except (TypeError, ValueError):
                error_payload = {}

            oauth_error = str(
                error_payload.get("error") or ""
            ).strip().lower()
            oauth_description = str(
                error_payload.get("error_description") or ""
            ).strip().lower()
            combined_error = (
                f"{oauth_error} {oauth_description} "
                f"{(response.text or '').lower()}"
            )

            if "invalid_grant" in combined_error:
                latest = await _collection(
                    self.db, SNAPCHAT_CREDENTIALS_COLLECTION
                ).find_one(
                    {
                        "user_id": self.user_id,
                        "provider": SNAPCHAT_PROVIDER_ID,
                    },
                    {"_id": 0, "refresh_token_ciphertext": 1},
                )

                try:
                    latest_refresh = decrypt_snapchat_token(
                        (latest or {}).get("refresh_token_ciphertext")
                    )
                except ValueError:
                    latest_refresh = ""

                if latest_refresh and latest_refresh != refresh:
                    refresh = latest_refresh
                    data["refresh_token"] = refresh
                    try:
                        async with httpx.AsyncClient(timeout=25.0) as client:
                            response = await client.post(
                                SNAPCHAT_TOKEN_URL, data=data
                            )
                    except httpx.HTTPError as exc:
                        raise SnapchatNativeSyncError(
                            "snapchat_token_refresh_failed",
                            "Snapchat token refresh failed.",
                            status_code=502,
                            retryable=True,
                        ) from exc

                if response.status_code >= 400:
                    raise SnapchatNativeSyncError(
                        "snapchat_needs_reauth",
                        "Snapchat authorization must be renewed.",
                        status_code=409,
                        result={"needs_reauth": True},
                    )

            elif "invalid_client" in combined_error:
                raise SnapchatNativeSyncError(
                    "snapchat_oauth_not_configured",
                    "Snapchat OAuth client credentials were rejected.",
                    status_code=503,
                )

            else:
                raise SnapchatNativeSyncError(
                    "snapchat_token_refresh_rejected",
                    "Snapchat temporarily rejected the token refresh.",
                    status_code=502,
                    retryable=(
                        response.status_code >= 500
                        or response.status_code == 429
                    ),
                )
        try:
            payload = response.json() or {}
        except (TypeError, ValueError) as exc:
            raise SnapchatNativeSyncError(
                "snapchat_token_refresh_invalid_json", "Snapchat token response was invalid.",
                status_code=502, retryable=True,
            ) from exc
        access = str(payload.get("access_token") or "").strip()
        if not access:
            raise SnapchatNativeSyncError(
                "snapchat_token_missing", "Snapchat token response was incomplete.",
                status_code=502, retryable=True,
            )
        refresh = str(payload.get("refresh_token") or refresh).strip()
        expires_in = int(payload.get("expires_in") or 3600)
        now = self.now().astimezone(timezone.utc)
        await _collection(self.db, SNAPCHAT_CREDENTIALS_COLLECTION).update_one(
            {"user_id": self.user_id, "provider": SNAPCHAT_PROVIDER_ID},
            {"$set": {
                "access_token_ciphertext": encrypt_snapchat_token(access),
                "refresh_token_ciphertext": encrypt_snapchat_token(refresh),
                "access_token_expires_at": now + timedelta(seconds=expires_in),
                "scope": payload.get("scope") or credentials.get("scope")
                         or list(SNAPCHAT_REQUESTED_SCOPES),
                "last_refresh_success_at": now,
                "updated_at": now,
            }},
        )
        return access

    async def to_sar(self, value: float | None, currency: str) -> float | None:
        if value is None:
            return None
        normalized = str(currency or "").strip().upper()
        if normalized in {"SAR", "ر.س"}:
            return round(value, 2)
        if normalized != "USD":
            return None
        if self.usd_rate_cache is None:
            row = None
            try:
                row = await _collection(self.db, "ads_currency_settings").find_one(
                    {"user_id": self.user_id}, {"_id": 0, "usd_to_sar_rate": 1}
                )
            except Exception:  # noqa: BLE001
                row = None
            try:
                rate = float(row.get("usd_to_sar_rate") if row else DEFAULT_USD_TO_SAR)
            except (TypeError, ValueError, OverflowError) as exc:
                raise SnapchatNativeSyncError(
                    "snapchat_usd_rate_unverified",
                    "The configured USD to SAR rate is invalid.", status_code=409,
                ) from exc
            if not math.isfinite(rate) or rate <= 0:
                raise SnapchatNativeSyncError(
                    "snapchat_usd_rate_unverified",
                    "The configured USD to SAR rate is invalid.", status_code=409,
                )
            self.usd_rate_cache = rate
        return round(value * self.usd_rate_cache, 2)
