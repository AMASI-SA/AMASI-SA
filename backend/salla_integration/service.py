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
import logging
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

# Do not wait for the last seconds of a 14-day access token.  A small
# background maintenance pass refreshes connected stores one day early, so a
# temporarily quiet store does not discover an expired token in the middle of
# an order/shipping workflow.
PROACTIVE_REFRESH_BEFORE_SEC = 24 * 60 * 60
TOKEN_MAINTENANCE_INTERVAL_SEC = 6 * 60 * 60

log = logging.getLogger("salla.oauth")

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
_REVISION_NOT_PROVIDED = object()


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
        # A revision changes whenever Salla authorizes or refreshes tokens.
        # It is deliberately opaque; refresh code uses it as a compare-and-
        # swap guard so a delayed refresh response can never overwrite a newer
        # app.store.authorize/app.updated token.
        "token_revision": uuid.uuid4().hex,
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


async def mark_needs_reauth(
    db,
    user_id: str,
    reason: str,
    *,
    expected_revision: Any = _REVISION_NOT_PROVIDED,
) -> bool:
    query: dict[str, Any] = {"user_id": user_id}
    if expected_revision is None:
        query["$or"] = [
            {"token_revision": {"$exists": False}},
            {"token_revision": None},
        ]
    elif expected_revision is not _REVISION_NOT_PROVIDED:
        query["token_revision"] = expected_revision
    result = await db.salla_integrations.update_one(
        query,
        {"$set": {
            "status": "needs_reauth",
            "last_error": (reason or "")[:500],
            "last_error_at": _now(),
            "updated_at": _now(),
        }},
    )
    # A false value means a newer authorization won the race and was
    # intentionally left connected.
    return result.modified_count == 1


def _decrypt_access(doc: dict) -> str:
    try:
        return decrypt_token(doc.get("access_token_encrypted") or b"")
    except ValueError as exc:
        raise SallaError(
            "Saved Salla access token is corrupted.",
            needs_reauth=True,
            status_code=401,
        ) from exc


def _revision_changed(initial: Optional[str], current: Optional[str]) -> bool:
    # Legacy rows have no revision.  The first successful refresh/authorize
    # adds one, which is therefore also a detectable change.
    return current is not None and current != initial


def _is_scope_401(response: httpx.Response) -> bool:
    """Return True when Salla's 401 means a missing app permission.

    Salla uses 401 both for an invalid token *and* for a valid token that lacks
    the endpoint scope.  Refreshing on the latter is harmful: it needlessly
    rotates a single-use refresh token and the second 401 used to mark the
    whole integration as disconnected.
    """
    if response.status_code != 401:
        return False
    fragments: list[str] = []
    try:
        body = response.json()
        fragments.append(str(body))
        if isinstance(body, dict):
            error = body.get("error")
            if isinstance(error, dict):
                fragments.extend(str(v) for v in error.values())
            fragments.extend(str(v) for v in body.values())
    except Exception:
        pass
    fragments.append(response.text or "")
    haystack = " ".join(fragments).lower()
    return any(marker in haystack for marker in (
        "scope",
        "permission",
        "not allowed",
        "access to one of those",
        "صلاحية",
        "الصلاحيات",
    ))


def _scope_error_message(response: httpx.Response, path: str) -> str:
    detail = ""
    try:
        body = response.json()
        error = body.get("error") if isinstance(body, dict) else None
        if isinstance(error, dict):
            detail = str(error.get("message") or error.get("code") or "")
        elif isinstance(body, dict):
            detail = str(body.get("message") or body.get("error_description") or "")
    except Exception:
        detail = response.text[:240]
    suffix = f": {detail}" if detail else ""
    return f"Salla permission is missing for {path}{suffix}"


# ── Auto-refresh wrapper ───────────────────────────────────────────────
async def ensure_fresh_access_token(
    db,
    user_id: str,
    *,
    force_refresh: bool = False,
    recover_needs_reauth: bool = False,
    rejected_access_token: Optional[str] = None,
    minimum_validity_sec: int = 0,
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

        initial_revision = initial_doc.get("token_revision")
        initial_access = _decrypt_access(initial_doc)
        initial_expires_at = _as_utc(initial_doc.get("expires_at"))

        # Fast path: most calls never touch the distributed lease.  If the
        # caller's rejected token is already different from the DB token,
        # another request refreshed it while this request was in flight.
        if rejected_access_token and initial_access != rejected_access_token:
            return initial_access
        if (
            not force_refresh
            and initial_doc.get("status") == "connected"
            and isinstance(initial_expires_at, datetime)
            and initial_expires_at
            > _now() + timedelta(seconds=max(0, int(minimum_validity_sec or 0)))
        ):
            return initial_access

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

            refresh_completed = (
                current.get("status") == "connected"
                and _revision_changed(
                    initial_revision,
                    current.get("token_revision"),
                )
            )

            if refresh_completed:
                return _decrypt_access(current)

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

            another_worker_refreshed = (
                _revision_changed(
                    initial_revision,
                    doc.get("token_revision"),
                )
            )
            if another_worker_refreshed and doc.get("status") == "connected":
                return _decrypt_access(doc)

            current_access = _decrypt_access(doc)
            if rejected_access_token and current_access != rejected_access_token:
                return current_access

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
                and expires_at
                > _now() + timedelta(seconds=max(0, int(minimum_validity_sec or 0)))
            ):
                return current_access

            try:
                refresh_token = decrypt_token(
                    doc.get("refresh_token_encrypted") or b""
                )
            except ValueError as exc:
                await mark_needs_reauth(
                    db,
                    user_id,
                    f"refresh-token decrypt failed: {exc}",
                    expected_revision=doc.get("token_revision"),
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
                    expected_revision=doc.get("token_revision"),
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
                    await mark_needs_reauth(
                        db,
                        user_id,
                        str(exc),
                        expected_revision=doc.get("token_revision"),
                    )
                raise

            # Salla refresh tokens rotate and are single-use.  A refresh
            # response without the replacement token cannot be safely reused.
            if not new_payload.get("refresh_token"):
                await mark_needs_reauth(
                    db,
                    user_id,
                    "Salla refresh response omitted the rotated refresh_token",
                    expected_revision=doc.get("token_revision"),
                )
                raise SallaError(
                    "Salla refresh response did not include refresh_token.",
                    needs_reauth=True,
                    status_code=502,
                )

            access = new_payload.get("access_token")
            if not access:
                raise SallaError(
                    "Salla refresh response missing access_token",
                    status_code=502,
                )

            update = {
                "access_token_encrypted": encrypt_token(access),
                "refresh_token_encrypted": encrypt_token(
                    new_payload["refresh_token"]
                ),
                "scope": new_payload.get("scope") or doc.get("scope") or DEFAULT_SCOPES,
                "token_type": new_payload.get("token_type", "Bearer"),
                "expires_at": _expires_at(int(new_payload.get("expires_in") or 0)),
                "expires_in_seconds": int(new_payload.get("expires_in") or 0),
                "last_refreshed_at": _now(),
                "status": "connected",
                "last_error": None,
                "last_error_at": None,
                "token_revision": uuid.uuid4().hex,
                "updated_at": _now(),
            }
            revision_query: dict[str, Any]
            if doc.get("token_revision") is None:
                revision_query = {
                    "$or": [
                        {"token_revision": {"$exists": False}},
                        {"token_revision": None},
                    ]
                }
            else:
                revision_query = {"token_revision": doc["token_revision"]}

            # Compare-and-swap: if app.store.authorize/app.updated delivered
            # newer credentials while the HTTP refresh was in flight, do not
            # overwrite them with this now-stale response.
            result = await db.salla_integrations.update_one(
                {
                    "user_id": user_id,
                    "refresh_lock_owner": lease_owner,
                    **revision_query,
                },
                {"$set": update},
            )
            if result.modified_count == 1:
                return access

            latest = await get_integration(db, user_id)
            if latest and latest.get("status") == "connected":
                return _decrypt_access(latest)
            raise SallaError(
                "Salla credentials changed during refresh. Retry shortly.",
                status_code=503,
            )

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

    async def _access_token_is_still_valid(tok: str) -> Optional[bool]:
        """Probe a low-risk endpoint to classify an ambiguous Salla 401.

        Salla may return 401 for a valid token that lacks one endpoint's
        permission.  Only a 401 from /store/info proves the token itself is
        rejected.  Network/server errors remain inconclusive and must never
        disconnect the merchant integration.
        """
        if path.rstrip("/") == "/store/info":
            return False
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
                probe = await client.get(
                    f"{SALLA_API_BASE}/store/info",
                    headers={"Authorization": f"Bearer {tok}"},
                )
        except (httpx.TimeoutException, httpx.NetworkError):
            return None
        if 200 <= probe.status_code < 300:
            return True
        if probe.status_code == 401:
            return False
        return None

    resp = await _do_request(token)
    if resp.status_code == 401:
        if _is_scope_401(resp):
            raise SallaError(
                _scope_error_message(resp, path),
                needs_reauth=False,
                status_code=403,
            )
        # Do not rotate Salla's single-use refresh token merely because one
        # endpoint returned an ambiguous 401.  First prove whether the access
        # token itself is rejected using the low-risk store-info endpoint.
        first_probe = await _access_token_is_still_valid(token)
        if first_probe is True:
            raise SallaError(
                _scope_error_message(resp, path),
                needs_reauth=False,
                status_code=403,
            )
        if first_probe is None:
            raise SallaError(
                "Could not verify the Salla token because Salla is temporarily unavailable. Retry shortly.",
                needs_reauth=False,
                status_code=503,
            )
        # Race: token just expired between our pre-check and the request.
        # Refresh once and retry.
        token = await ensure_fresh_access_token(
            db,
            user_id,
            force_refresh=True,
            recover_needs_reauth=True,
            rejected_access_token=token,
        )
        resp = await _do_request(token)

    if resp.status_code >= 400:
        if resp.status_code == 401:
            if _is_scope_401(resp):
                raise SallaError(
                    _scope_error_message(resp, path),
                    needs_reauth=False,
                    status_code=403,
                )
            latest = await get_integration(db, user_id)
            latest_access = _decrypt_access(latest) if latest else ""
            if latest_access and latest_access != token:
                resp = await _do_request(latest_access)
                if resp.status_code < 400:
                    return resp.json()
                if _is_scope_401(resp):
                    raise SallaError(
                        _scope_error_message(resp, path),
                        needs_reauth=False,
                        status_code=403,
                    )
                token = latest_access

            # A valid token plus a 401 on this specific endpoint means a
            # missing/awaiting scope (for example products.read), not an
            # expired OAuth session.  Keep orders, Qoyod and all other Salla
            # workflows connected.
            probe_result = await _access_token_is_still_valid(token)
            if probe_result is True:
                raise SallaError(
                    _scope_error_message(resp, path),
                    needs_reauth=False,
                    status_code=403,
                )
            if probe_result is None:
                raise SallaError(
                    "Could not verify the Salla token because Salla is temporarily unavailable. Retry shortly.",
                    needs_reauth=False,
                    status_code=503,
                )
            await mark_needs_reauth(
                db,
                user_id,
                "Salla API 401 even after safe refresh",
                expected_revision=(latest or {}).get("token_revision"),
            )
            raise SallaError("Salla rejected our token. Reconnect.", needs_reauth=True, status_code=401)
        raise SallaError(
            f"Salla {method} {path} → {resp.status_code}: {resp.text[:200]}",
            status_code=resp.status_code,
        )
    return resp.json()


async def refresh_expiring_integrations_once(db) -> dict[str, int]:
    """Refresh connected stores before their access tokens expire.

    Safe to run in every application worker: `ensure_fresh_access_token`
    serializes the actual single-use refresh through Mongo and all other
    workers observe the new token revision.
    """
    stats = {"checked": 0, "refreshed": 0, "failed": 0}
    cutoff = _now() + timedelta(seconds=PROACTIVE_REFRESH_BEFORE_SEC)
    cursor = db.salla_integrations.find(
        {
            "status": "connected",
            "expires_at": {"$lte": cutoff},
        },
        {"_id": 0, "user_id": 1, "token_revision": 1},
    )
    async for row in cursor:
        user_id = str(row.get("user_id") or "").strip()
        if not user_id:
            continue
        stats["checked"] += 1
        before = row.get("token_revision")
        try:
            await ensure_fresh_access_token(
                db,
                user_id,
                recover_needs_reauth=False,
                minimum_validity_sec=PROACTIVE_REFRESH_BEFORE_SEC,
            )
            current = await get_integration(db, user_id)
            if current and current.get("token_revision") != before:
                stats["refreshed"] += 1
        except Exception as exc:  # noqa: BLE001 — one store must not stop loop
            stats["failed"] += 1
            log.warning(
                "salla.oauth.proactive_refresh_failed user_id=%s error=%s",
                user_id,
                str(exc)[:300],
            )
    return stats


async def salla_token_maintenance_loop(db) -> None:
    """Long-running, non-blocking OAuth maintenance task."""
    # Let indexes/config caches finish warming before the first pass.
    await asyncio.sleep(60)
    while True:
        try:
            stats = await refresh_expiring_integrations_once(db)
            log.info("salla.oauth.maintenance %s", stats)
        except Exception as exc:  # noqa: BLE001 — retry next interval
            log.exception("salla.oauth.maintenance_failed: %s", exc)
        await asyncio.sleep(TOKEN_MAINTENANCE_INTERVAL_SEC)


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
    scope_text = str(doc.get("scope") or "")
    automatic_refresh_ready = bool(
        doc.get("refresh_token_encrypted")
        and "offline_access" in scope_text.split()
    )
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
        "automatic_refresh_ready": automatic_refresh_ready,
        "automatic_refresh_before_seconds": PROACTIVE_REFRESH_BEFORE_SEC,
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
