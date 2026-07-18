"""SallaClient — thin async wrapper around Salla Merchant API.

Responsibilities
    1. Persist + read encrypted tokens from `salla_integrations` collection.
    2. Auto-refresh access_token before/on expiry (with safety margin) +
       a per-user asyncio.Lock so two concurrent requests never race on
       the same refresh_token (refresh tokens may rotate on each use,
       so a race causes one of the two to receive invalid_grant).
    3. Single retry-on-401 in case the access token expired mid-flight.
"""
from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx

from .crypto import decrypt_token, encrypt_token


SALLA_AUTH_BASE = os.environ.get("SALLA_AUTH_BASE", "https://accounts.salla.sa").rstrip("/")
SALLA_API_BASE = os.environ.get("SALLA_API_BASE", "https://api.salla.dev/admin/v2").rstrip("/")
SALLA_TOKEN_URL = f"{SALLA_AUTH_BASE}/oauth2/token"
SALLA_AUTHORIZE_URL = f"{SALLA_AUTH_BASE}/oauth2/auth"

# Safety margin: refresh tokens X seconds before Salla's own expiry to
# avoid the narrow window where the token has *just* expired and any
# in-flight request will get 401.
EXPIRY_SAFETY_MARGIN_SEC = 120

# Required scopes — official Salla format per docs.salla.dev.
#
# IMPORTANT (Iter-291): every scope listed here MUST
#   (a) be in the EXACT format Salla accepts in its OAuth `scope`
#       parameter (per /421118m0 + /421413m0 in the partners docs), AND
#   (b) be enabled in the Salla Partners Portal for this App,
# otherwise Salla returns `invalid_scope` at /oauth2/auth.
#
# Per Salla's official docs, write capability uses the `*.read_write`
# suffix — NOT a separate `.write` token. Confirmed examples in the
# docs and App Events page:
#     offline_access
#     settings.read
#     orders.read_write
#     webhooks.read_write
# Using `orders.write` / `webhooks.write` as standalone scopes is
# unofficial and historically causes `invalid_scope`.
#
# Customer details (name/phone/email/address) come embedded inside the
# order payload — we do NOT need a separate `customers.read` scope for
# the Salla→Qoyod invoice pipeline. Same for payments / shipping /
# taxes / branches: all available inside the order payload.
#
# Operators can override the list via the SALLA_OAUTH_SCOPES env var
# (space-separated) without a code change — useful when Salla rolls out
# new scope names or when the App's enabled permissions change.
_DEFAULT_SCOPES_FALLBACK = (
    "offline_access "
    "settings.read "
    "orders.read_write "
    "shipping.read_write "
    "webhooks.read_write"
)
DEFAULT_SCOPES = (os.environ.get("SALLA_OAUTH_SCOPES") or _DEFAULT_SCOPES_FALLBACK).strip()


class SallaError(Exception):
    """Raised for any Salla-side failure. Frontend renders the .args[0]
    as a toast and may show a 'reconnect' CTA when .needs_reauth is True."""

    def __init__(self, message: str, *, needs_reauth: bool = False, status_code: int = 500):
        super().__init__(message)
        self.needs_reauth = needs_reauth
        self.status_code = status_code


# ── per-user refresh locks ─────────────────────────────────────────────
# Module-level so two requests for the SAME user serialize, but two
# requests for DIFFERENT users still run concurrently. Cleared lazily —
# we never need to garbage-collect because the key set is bounded by
# the user count, and a stale lock is harmless (it just gets re-acquired).
_REFRESH_LOCKS: dict[str, asyncio.Lock] = {}


def _lock_for(user_id: str) -> asyncio.Lock:
    lock = _REFRESH_LOCKS.get(user_id)
    if lock is None:
        lock = asyncio.Lock()
        _REFRESH_LOCKS[user_id] = lock
    return lock


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(dt: Optional[datetime]) -> Optional[datetime]:
    """Mongo returns naive datetimes (BSON has no TZ). We always store
    UTC, so a naive value from Mongo IS UTC — just attach the tzinfo."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


# In-process cache for OAuth credentials. Populated at startup from
# `salla_oauth_config` collection (DB) with `.env` as fallback. Refreshed
# whenever the user saves new values via the Settings UI.
_CREDS_CACHE: dict = {"client_id": "", "client_secret": "", "loaded": False}


def _env(key: str) -> str:
    return (os.environ.get(key) or "").strip()


def update_credentials_cache(client_id: str, client_secret: str) -> None:
    """Called by routes.py whenever the user updates config via the UI,
    and once at startup with DB-resolved values."""
    _CREDS_CACHE["client_id"] = (client_id or "").strip()
    _CREDS_CACHE["client_secret"] = (client_secret or "").strip()
    _CREDS_CACHE["loaded"] = True


def _client_id() -> str:
    if _CREDS_CACHE["loaded"] and _CREDS_CACHE["client_id"]:
        return _CREDS_CACHE["client_id"]
    return _env("SALLA_CLIENT_ID")


def _client_secret() -> str:
    if _CREDS_CACHE["loaded"] and _CREDS_CACHE["client_secret"]:
        return _CREDS_CACHE["client_secret"]
    return _env("SALLA_CLIENT_SECRET")


def is_configured() -> bool:
    """True when both Client ID + Client Secret have been provided
    (UI or .env). Surfaces a clear error to the merchant before any
    OAuth attempt is made."""
    return bool(_client_id() and _client_secret())


def get_client_id() -> str:
    cid = _client_id()
    if not cid:
        raise SallaError("SALLA_CLIENT_ID is not configured. Add it via Settings → Salla or in backend/.env.", status_code=503)
    return cid


def get_client_secret() -> str:
    cs = _client_secret()
    if not cs:
        raise SallaError("SALLA_CLIENT_SECRET is not configured. Add it via Settings → Salla or in backend/.env.", status_code=503)
    return cs


def build_redirect_uri(public_base_url: str) -> str:
    """Compute the callback URL the OAuth flow will redirect to. We always
    use the backend's public origin — this MUST match the URI registered
    in Salla Partners → App → Configurations.

    `public_base_url` should be the REACT_APP_BACKEND_URL value (kicked
    in from the request's X-Forwarded-Proto/Host or .env)."""
    return f"{public_base_url.rstrip('/')}/api/salla/oauth/callback"


# ── HTTP helpers ───────────────────────────────────────────────────────
async def _post_token(payload: dict) -> dict:
    """POST to /oauth2/token. Raises SallaError with a friendly message
    on any non-2xx — the caller can decide whether to mark the
    integration as needs_reauth."""
    async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
        resp = await client.post(
            SALLA_TOKEN_URL,
            data=payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    if resp.status_code >= 400:
        # Salla returns {"error": "...", "error_description": "..."}
        body: dict[str, Any] = {}
        try:
            body = resp.json()
        except Exception:
            body = {"raw": resp.text[:300]}
        err = body.get("error") or "salla_error"
        desc = body.get("error_description") or body.get("message") or resp.text[:200]
        # invalid_grant on refresh = refresh_token revoked/expired → needs reauth
        needs_reauth = err in ("invalid_grant", "invalid_token", "unauthorized")
        raise SallaError(
            f"Salla token endpoint returned {resp.status_code} ({err}): {desc}",
            needs_reauth=needs_reauth,
            status_code=resp.status_code,
        )
    return resp.json()


async def exchange_code(code: str, redirect_uri: str) -> dict:
    return await _post_token({
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": get_client_id(),
        "client_secret": get_client_secret(),
    })


async def refresh_with_token(refresh_token: str) -> dict:
    return await _post_token({
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": get_client_id(),
        "client_secret": get_client_secret(),
    })


async def fetch_store_info(access_token: str) -> dict:
    async with httpx.AsyncClient(timeout=httpx.Timeout(15.0)) as client:
        resp = await client.get(
            f"{SALLA_API_BASE}/store/info",
            headers={"Authorization": f"Bearer {access_token}"},
        )
    if resp.status_code == 401:
        raise SallaError("Salla access token rejected (401).", needs_reauth=True, status_code=401)
    if resp.status_code >= 400:
        raise SallaError(
            f"GET /store/info → {resp.status_code}: {resp.text[:200]}",
            status_code=resp.status_code,
        )
    return resp.json()


# ── Persistence layer ──────────────────────────────────────────────────
async def get_integration(db, user_id: str) -> Optional[dict]:
    return await db.salla_integrations.find_one({"user_id": user_id}, {"_id": 0})


async def upsert_integration(db, user_id: str, payload: dict) -> None:
    payload = dict(payload)  # don't mutate caller's dict
    payload["user_id"] = user_id
    payload["updated_at"] = _now()
    # `created_at` must live ONLY in $setOnInsert — putting it in both
    # branches makes Mongo throw "Updating the path 'created_at' would
    # create a conflict".
    payload.pop("created_at", None)
    await db.salla_integrations.update_one(
        {"user_id": user_id},
        {"$set": payload, "$setOnInsert": {"created_at": _now()}},
        upsert=True,
    )


async def disconnect_integration(db, user_id: str) -> int:
    """Remove the integration document. Safe to call repeatedly."""
    res = await db.salla_integrations.delete_one({"user_id": user_id})
    return res.deleted_count


def _expires_at(expires_in_sec: int) -> datetime:
    return _now() + timedelta(seconds=max(0, int(expires_in_sec or 0)) - EXPIRY_SAFETY_MARGIN_SEC)


async def store_token_response(
    db,
    user_id: str,
    token_payload: dict,
    *,
    store_info: Optional[dict] = None,
) -> dict:
    """Persist the result of an exchange_code() or refresh_with_token().

    `token_payload` is the JSON dict returned by Salla's /oauth2/token.
    `store_info` is optional — only the initial connect call passes it
    (refresh calls don't need to re-fetch store metadata).
    """
    access = token_payload.get("access_token")
    refresh = token_payload.get("refresh_token")
    expires_in = token_payload.get("expires_in") or 0
    scope = token_payload.get("scope") or DEFAULT_SCOPES
    if not access:
        raise SallaError("Salla response missing access_token", status_code=502)

    update: dict = {
        "access_token_encrypted": encrypt_token(access),
        "scope": scope,
        "token_type": token_payload.get("token_type", "Bearer"),
        "expires_at": _expires_at(int(expires_in)),
        "expires_in_seconds": int(expires_in),
        "last_refreshed_at": _now(),
        "status": "connected",
        "last_error": None,
        "last_error_at": None,
    }
    # Refresh tokens may ROTATE on each use → store the new one if Salla
    # returned a fresh one. If absent, keep whatever's already in DB.
    if refresh:
        update["refresh_token_encrypted"] = encrypt_token(refresh)
    if store_info:
        data = store_info.get("data") or store_info
        update["store_id"] = data.get("id")
        update["store_name"] = data.get("name")
        update["store_domain"] = data.get("domain")
        update["store_email"] = data.get("email")
        update["store_plan"] = data.get("plan")
        update["store_status"] = data.get("status")

    await upsert_integration(db, user_id, update)
    return update


async def mark_needs_reauth(db, user_id: str, reason: str) -> None:
    await db.salla_integrations.update_one(
        {"user_id": user_id},
        {"$set": {
            "status": "needs_reauth",
            "last_error": (reason or "")[:500],
            "last_error_at": _now(),
            "updated_at": _now(),
        }},
    )


# ── Auto-refresh wrapper ───────────────────────────────────────────────
async def ensure_fresh_access_token(
    db,
    user_id: str,
    *,
    force_refresh: bool = False,
    recover_needs_reauth: bool = False,
) -> str:
    """Return a valid access token.

    Refresh tokens in Salla are single-use. The Mongo lease below protects
    refresh across all backend workers/processes, while the asyncio lock
    protects concurrent calls inside the same process.
    """
    async with _lock_for(user_id):
        initial_doc = await get_integration(db, user_id)
        if not initial_doc:
            raise SallaError(
                "Salla store is not connected.",
                needs_reauth=True,
                status_code=404,
            )

        initial_refreshed_at = _as_utc(initial_doc.get("last_refreshed_at"))
        lease_owner = uuid.uuid4().hex
        lease_seconds = 45

        # Acquire a distributed Mongo lease. Only one backend worker may use
        # the single-use Salla refresh token.
        acquired = False
        for _ in range(120):
            now = _now()
            lease_until = now + timedelta(seconds=lease_seconds)

            result = await db.salla_integrations.update_one(
                {
                    "user_id": user_id,
                    "$or": [
                        {"refresh_lock_until": {"$exists": False}},
                        {"refresh_lock_until": None},
                        {"refresh_lock_until": {"$lte": now}},
                    ],
                },
                {
                    "$set": {
                        "refresh_lock_owner": lease_owner,
                        "refresh_lock_until": lease_until,
                    }
                },
            )

            if result.modified_count == 1:
                acquired = True
                break

            # Another worker is refreshing. Wait, then use the token it saved.
            await asyncio.sleep(0.25)
            current = await get_integration(db, user_id)
            if not current:
                raise SallaError(
                    "Salla store is not connected.",
                    needs_reauth=True,
                    status_code=404,
                )

            current_refreshed_at = _as_utc(current.get("last_refreshed_at"))
            refresh_completed = (
                current.get("status") == "connected"
                and current_refreshed_at is not None
                and (
                    initial_refreshed_at is None
                    or current_refreshed_at > initial_refreshed_at
                )
            )

            if refresh_completed:
                return decrypt_token(
                    current.get("access_token_encrypted") or b""
                )

        if not acquired:
            raise SallaError(
                "Salla token refresh is busy. Retry shortly.",
                status_code=503,
            )

        try:
            # Re-fetch after obtaining the lease. Another worker may have
            # completed a refresh just before this worker acquired it.
            doc = await get_integration(db, user_id)
            if not doc:
                raise SallaError(
                    "Salla store is not connected.",
                    needs_reauth=True,
                    status_code=404,
                )

            refreshed_at = _as_utc(doc.get("last_refreshed_at"))
            another_worker_refreshed = (
                refreshed_at is not None
                and initial_refreshed_at is not None
                and refreshed_at > initial_refreshed_at
            )
            if another_worker_refreshed and doc.get("status") == "connected":
                return decrypt_token(
                    doc.get("access_token_encrypted") or b""
                )

            status_needs_reauth = doc.get("status") == "needs_reauth"
            if status_needs_reauth and not recover_needs_reauth:
                raise SallaError(
                    "Salla connection expired. Please reconnect.",
                    needs_reauth=True,
                    status_code=401,
                )

            must_refresh = force_refresh or status_needs_reauth
            expires_at = _as_utc(doc.get("expires_at"))

            if (
                not must_refresh
                and isinstance(expires_at, datetime)
                and expires_at > _now()
            ):
                return decrypt_token(
                    doc.get("access_token_encrypted") or b""
                )

            try:
                refresh_token = decrypt_token(
                    doc.get("refresh_token_encrypted") or b""
                )
            except ValueError as exc:
                await mark_needs_reauth(
                    db,
                    user_id,
                    f"refresh-token decrypt failed: {exc}",
                )
                raise SallaError(
                    "Saved Salla credentials are corrupted.",
                    needs_reauth=True,
                    status_code=401,
                ) from exc

            if not refresh_token:
                await mark_needs_reauth(
                    db,
                    user_id,
                    "no refresh_token on file (offline_access not granted)",
                )
                raise SallaError(
                    "No refresh_token on file. Reconnect Salla.",
                    needs_reauth=True,
                    status_code=401,
                )

            try:
                new_payload = await refresh_with_token(refresh_token)
            except SallaError as exc:
                if exc.needs_reauth:
                    await mark_needs_reauth(db, user_id, str(exc))
                raise

            # Saves both the new access token and the newly rotated,
            # single-use refresh token before releasing the Mongo lease.
            await store_token_response(db, user_id, new_payload)
            return new_payload["access_token"]

        finally:
            await db.salla_integrations.update_one(
                {
                    "user_id": user_id,
                    "refresh_lock_owner": lease_owner,
                },
                {
                    "$unset": {
                        "refresh_lock_owner": "",
                        "refresh_lock_until": "",
                    }
                },
            )


async def call_salla(
    db,
    user_id: str,
    method: str,
    path: str,
    *,
    params: Optional[dict] = None,
    json: Optional[dict] = None,
) -> dict:
    """Authenticated request to Salla Merchant API with auto-refresh
    + single retry on 401. `path` should start with '/' (e.g. '/orders')
    and is appended to SALLA_API_BASE."""
    token = await ensure_fresh_access_token(
        db,
        user_id,
        recover_needs_reauth=True,
    )

    async def _do_request(tok: str) -> httpx.Response:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
            return await client.request(
                method,
                f"{SALLA_API_BASE}{path}",
                headers={"Authorization": f"Bearer {tok}"},
                params=params,
                json=json,
            )

    resp = await _do_request(token)
    if resp.status_code == 401:
        # Race: token just expired between our pre-check and the request.
        # Refresh once and retry.
        token = await ensure_fresh_access_token(
            db,
            user_id,
            force_refresh=True,
            recover_needs_reauth=True,
        )
        resp = await _do_request(token)

    if resp.status_code >= 400:
        if resp.status_code == 401:
            await mark_needs_reauth(db, user_id, "Salla API 401 even after refresh")
            raise SallaError("Salla rejected our token. Reconnect.", needs_reauth=True, status_code=401)
        raise SallaError(
            f"Salla {method} {path} → {resp.status_code}: {resp.text[:200]}",
            status_code=resp.status_code,
        )
    return resp.json()


# ── Public-facing serializer ───────────────────────────────────────────
def integration_to_public(doc: Optional[dict]) -> dict:
    """Strip secrets + format for the frontend. Never returns tokens."""
    if not doc:
        return {
            "connected": False,
            "configured": is_configured(),
            "status": "not_connected",
        }
    expires_at = _as_utc(doc.get("expires_at"))
    last_refresh = _as_utc(doc.get("last_refreshed_at"))
    last_err_at = _as_utc(doc.get("last_error_at"))
    created_at = _as_utc(doc.get("created_at"))
    return {
        "connected": doc.get("status") == "connected",
        "configured": is_configured(),
        "status": doc.get("status") or "unknown",
        "store_id": doc.get("store_id"),
        "store_name": doc.get("store_name"),
        "store_domain": doc.get("store_domain"),
        "store_plan": doc.get("store_plan"),
        "store_status": doc.get("store_status"),
        "scope": doc.get("scope"),
        "expires_at": expires_at.isoformat() if expires_at else None,
        "last_refreshed_at": last_refresh.isoformat() if last_refresh else None,
        "last_error": doc.get("last_error"),
        "last_error_at": last_err_at.isoformat() if last_err_at else None,
        "created_at": created_at.isoformat() if created_at else None,
        # Iter-292 — surfaces whether this install came from Easy Mode
        # (Salla App Store webhook) or Custom Mode (browser OAuth flow).
        # Useful for support: "how was the merchant connected?".
        "install_mode": doc.get("install_mode") or "custom",
        "easy_mode_owner_email": doc.get("easy_mode_owner_email"),
    }
