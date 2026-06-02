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

# Required scopes — keep the union of read+write for orders, webhooks
# (used in Phase 2), and offline_access (required to receive a
# refresh_token from Salla, per the OAuth2 spec).
DEFAULT_SCOPES = "offline_access orders.read orders.write webhooks.read webhooks.write customers.read settings.read"


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


def is_configured() -> bool:
    """The /api/salla/auth/login route surfaces a 400 with a helpful
    message when the merchant has not yet pasted Client ID / Secret
    into backend/.env. We never want to attempt an OAuth flow with an
    empty client_id (Salla returns a confusing error)."""
    return bool(
        (os.environ.get("SALLA_CLIENT_ID") or "").strip()
        and (os.environ.get("SALLA_CLIENT_SECRET") or "").strip(),
    )


def get_client_id() -> str:
    cid = (os.environ.get("SALLA_CLIENT_ID") or "").strip()
    if not cid:
        raise SallaError("SALLA_CLIENT_ID is not configured. Add it to backend/.env.", status_code=503)
    return cid


def get_client_secret() -> str:
    cs = (os.environ.get("SALLA_CLIENT_SECRET") or "").strip()
    if not cs:
        raise SallaError("SALLA_CLIENT_SECRET is not configured. Add it to backend/.env.", status_code=503)
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
async def ensure_fresh_access_token(db, user_id: str) -> str:
    """Return a valid, non-expired access_token for the user. Refreshes
    automatically if the stored token has expired (or will within the
    safety margin). Raises SallaError(needs_reauth=True) if the refresh
    token is also dead."""
    async with _lock_for(user_id):
        # Re-fetch INSIDE the lock so a concurrent request that already
        # refreshed our token is visible.
        doc = await get_integration(db, user_id)
        if not doc:
            raise SallaError("Salla store is not connected.", needs_reauth=True, status_code=404)
        if doc.get("status") == "needs_reauth":
            raise SallaError("Salla connection expired. Please reconnect.", needs_reauth=True, status_code=401)

        expires_at = _as_utc(doc.get("expires_at"))
        if isinstance(expires_at, datetime) and expires_at > _now():
            # Token is fresh enough → just decrypt and return.
            return decrypt_token(doc.get("access_token_encrypted") or b"")

        # Expired (or close to it) → refresh.
        try:
            refresh_token = decrypt_token(doc.get("refresh_token_encrypted") or b"")
        except ValueError as e:
            await mark_needs_reauth(db, user_id, f"refresh-token decrypt failed: {e}")
            raise SallaError("Saved Salla credentials are corrupted.", needs_reauth=True, status_code=401)
        if not refresh_token:
            await mark_needs_reauth(db, user_id, "no refresh_token on file (offline_access not granted)")
            raise SallaError("No refresh_token on file. Reconnect Salla.", needs_reauth=True, status_code=401)

        try:
            new_payload = await refresh_with_token(refresh_token)
        except SallaError as e:
            if e.needs_reauth:
                await mark_needs_reauth(db, user_id, str(e))
            raise
        await store_token_response(db, user_id, new_payload)
        return new_payload["access_token"]


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
    token = await ensure_fresh_access_token(db, user_id)

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
        token = await ensure_fresh_access_token(db, user_id)
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
    }
