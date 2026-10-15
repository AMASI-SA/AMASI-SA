"""FastAPI router for the Salla integration.

Public routes (all prefixed with /api/salla — attached at app startup):
    GET  /status                     Integration state for current user (no secrets).
    GET  /oauth/login                Build authorize URL + 302 to Salla.
    GET  /oauth/callback             Salla redirects here with ?code + ?state.
    POST /test-connection            Calls /store/info; refreshes token if stale.
    GET  /store-info                 Cached store info from DB (no network call).
    POST /refresh-store-info         Re-fetch /store/info from Salla and update DB.
    POST /disconnect                 Delete the integration record (revoke locally).

State handling
    OAuth requires a CSRF-resistant `state` parameter that survives the
    Salla redirect round-trip. We store it in a short-lived (5-minute)
    `salla_oauth_states` collection keyed by random string + user_id so
    the callback can validate both halves: who started this flow + that
    the state value wasn't tampered with.

Phase 1 NEVER touches: unified_orders, analyses, snapchat_*, meta_*,
make.com webhooks. The integration is entirely additive.
"""
from __future__ import annotations

import asyncio
import logging
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request, Query
from fastapi.responses import RedirectResponse, HTMLResponse
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from customer_identity import CUSTOMER_IDENTITY_COLLECTION

from .service import (
    DEFAULT_SCOPES,
    SallaError,
    SallaError as _SE,  # alias used in handlers
    SALLA_AUTHORIZE_URL,
    build_redirect_uri,
    call_salla,
    disconnect_integration,
    ensure_fresh_access_token,
    exchange_code,
    fetch_store_info,
    get_client_id,
    get_integration,
    integration_to_public,
    is_configured,
    store_token_response,
    update_credentials_cache,
)
from .config_store import (
    delete_config as delete_oauth_config,
    get_config as get_oauth_config,
    save_config as save_oauth_config,
)
from .sync import (
    compute_sources_comparison,
    create_sync_log,
    ensure_sync_indexes,
    run_orders_sync,
    run_products_sync,
)
from .abandoned_carts import (
    ABANDONED_CART_COLLECTION,
    ABANDONED_CART_EVENT_COLLECTION,
    ABANDONED_CART_EVENTS,
    ABANDONED_CART_SCHEMA_VERSION,
    ensure_abandoned_cart_indexes,
    split_scopes,
)


HISTORICAL_ABANDONED_CART_IMPORT_ENABLED = False
HISTORICAL_ABANDONED_CART_STOP_REASON = (
    "historical_import_disabled_live_webhooks_only"
)


logger = logging.getLogger(__name__)
_MANUAL_ORDER_SYNC_TASKS: dict[str, asyncio.Task] = {}
_ORDER_SYNC_LEASE_SECONDS = 30 * 60


async def _acquire_order_sync_lease(db, user_id: str) -> tuple[Optional[str], Optional[dict]]:
    now = datetime.now(timezone.utc)
    lease_id = f"salla-orders:{user_id}"
    lease_token = secrets.token_urlsafe(18)
    try:
        lease = await db.salla_sync_leases.find_one_and_update(
            {
                "_id": lease_id,
                "$or": [
                    {"lease_until": {"$lte": now}},
                    {"lease_until": {"$exists": False}},
                ],
            },
            {"$set": {
                "user_id": str(user_id),
                "kind": "orders",
                "lease_token": lease_token,
                "lease_until": now + timedelta(seconds=_ORDER_SYNC_LEASE_SECONDS),
                "acquired_at": now,
            }},
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
    except DuplicateKeyError:
        lease = None
    if lease and lease.get("lease_token") == lease_token:
        return lease_token, None
    active = await db.salla_sync_leases.find_one(
        {"_id": lease_id}, {"_id": 0, "log_id": 1, "lease_until": 1}
    )
    return None, active


async def _release_order_sync_lease(db, user_id: str, lease_token: str) -> None:
    await db.salla_sync_leases.update_one(
        {
            "_id": f"salla-orders:{user_id}",
            "lease_token": lease_token,
        },
        {"$set": {"lease_until": datetime.now(timezone.utc)}, "$unset": {"log_id": ""}},
    )


async def close_running_historical_cart_imports(
    db,
    user_id: str,
    *,
    now: Optional[datetime] = None,
) -> int:
    """Close legacy backfills after switching the tenant to live webhooks only.

    A background import can be killed between page requests while its durable
    log remains ``running``.  The Salla settings page used that flag to disable
    its own action forever.  Historical cart ingestion is intentionally
    disabled now, so terminalise every orphaned run without touching saved
    carts or the live webhook collections.
    """
    ended_at = now or datetime.now(timezone.utc)
    result = await db.salla_sync_logs.update_many(
        {
            "user_id": user_id,
            "kind": "abandoned_carts",
            "status": "running",
        },
        {
            "$set": {
                "status": "cancelled",
                "stopped_reason": HISTORICAL_ABANDONED_CART_STOP_REASON,
                "ended_at": ended_at,
                "updated_at": ended_at,
                "last_error": None,
            }
        },
    )
    return int(getattr(result, "modified_count", 0) or 0)


# Where the frontend wants to land after the callback completes
# (success or failure).
#
# Iter-177 — derive the redirect target from the actual request
# headers (X-Forwarded-Host stripped by the K8s ingress). This way
# the callback redirect always returns the merchant to the same
# domain they came from (e.g. mezansalla.com in production,
# *.preview.emergentagent.com in preview, localhost in dev) —
# without needing per-environment FRONTEND_URL env vars.
#
# An explicit override via SALLA_RETURN_URL is still honored for
# the rare case where the OAuth flow should land on a different
# host (e.g. a marketing landing page).
def _frontend_origin(request: Optional[Request] = None) -> str:
    explicit = os.environ.get("SALLA_RETURN_URL", "").strip()
    if explicit:
        return explicit.rstrip("/")
    if request is not None:
        try:
            return _public_base_url(request).rstrip("/")
        except Exception:  # noqa: BLE001 — fall through to env fallback
            pass
    raw = (os.environ.get("FRONTEND_URL") or "http://localhost:3000").strip().rstrip("/")
    return raw


def _public_base_url(request: Request) -> str:
    """Best-effort discovery of the public origin we're served under.

    Behind the K8s ingress we get X-Forwarded-Proto/Host. We trust those
    because they're stripped of any client-supplied values by the ingress.
    Falls back to request.base_url for local dev."""
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
    return f"{proto}://{host}"


def attach_salla_routes(api_router: APIRouter, db) -> None:
    # Importing here keeps the existing server.py bootstrap code unchanged.
    from server import current_user  # type: ignore  # circular by design

    router = APIRouter(prefix="/salla", tags=["salla"])

    async def run_orders_sync_background(
        *,
        user_id: str,
        lease_token: str,
        log_id: str,
        from_date: Optional[str],
        to_date: Optional[str],
        updated_since_hours: Optional[int],
        recover_marketing_attribution: bool,
    ) -> None:
        task_key = str(user_id)
        try:
            await run_orders_sync(
                db,
                task_key,
                from_date=from_date,
                to_date=to_date,
                updated_since_hours=updated_since_hours,
                log_id=log_id,
                recover_marketing_attribution=recover_marketing_attribution,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "Background Salla order sync failed user_id=%s log_id=%s",
                task_key,
                log_id,
            )
        finally:
            try:
                await _release_order_sync_lease(db, task_key, lease_token)
            finally:
                current = _MANUAL_ORDER_SYNC_TASKS.get(task_key)
                if current is asyncio.current_task():
                    _MANUAL_ORDER_SYNC_TASKS.pop(task_key, None)

    # ── 0. Easy Mode webhook (PUBLIC — gated by HMAC) ─────────────────
    # Iter-292 — Salla Easy Mode delivers tokens via webhook instead of
    # a redirect-based OAuth code exchange. This route is INTENTIONALLY
    # mounted before the auth-gated routes so its public nature is
    # obvious during code review.
    #
    # Security invariants (see easy_mode_webhook.py for full doc):
    #   • Returns 503 if SALLA_WEBHOOK_SECRET is unset (NEVER silently
    #     accepts unsigned webhooks).
    #   • Verifies HMAC-SHA256 over the RAW body with constant-time
    #     comparison BEFORE any JSON parsing.
    #   • NO token is persisted if verification fails.
    from .easy_mode_webhook import (  # noqa: E402 — local import keeps module load order safe
        SIGNATURE_HEADER,
        STRATEGY_HEADER,
        STRATEGY_SIGNATURE,
        STRATEGY_TOKEN,
        _extract_provided_token,
        dispatch_event,
        get_webhook_secret,
        resolve_strategy,
        verify_signature,
        verify_token,
    )

    @router.post("/webhooks/app")
    async def salla_app_webhook(request: Request):
        # 1. Secret must be configured. Refusing 503 (vs. 401) makes the
        #    operational error distinguishable in monitoring from a bad
        #    signature attack.
        secret = get_webhook_secret()
        if not secret:
            import logging
            logging.getLogger("salla.easy_mode").error(
                "easy_mode.webhook_received_but_secret_unset path=%s",
                request.url.path,
            )
            raise HTTPException(
                status_code=503,
                detail={"code": "SALLA_WEBHOOK_SECRET_NOT_CONFIGURED",
                        "message": "Set SALLA_WEBHOOK_SECRET in backend/.env then restart backend."},
            )

        # 2. Read the RAW body for HMAC verification. Doing this before
        #    JSON parsing is critical — once parsed, key-order /
        #    whitespace differences would change the hash.
        raw_body = await request.body()

        # rev30 — Detect Salla's Webhook Security Strategy header. The
        # Partners Portal setting decides which credential Salla will
        # send on every request: HMAC-SHA256 in Signature mode, OR the
        # exact webhook secret value in Token mode. We honour whichever
        # the header advertises; defaults to Signature.
        strategy = resolve_strategy(request.headers)
        import logging as _logging
        _log = _logging.getLogger("salla.easy_mode")
        if strategy == STRATEGY_TOKEN:
            provided_token = _extract_provided_token(request.headers)
            verified = verify_token(provided_token, secret)
            # Safe log — presence flag only, NEVER the token value.
            _log.info(
                "easy_mode.webhook_received strategy=%s token_present=%s "
                "verified=%s ip=%s body_len=%d",
                strategy, bool(provided_token), verified,
                getattr(request.client, "host", None), len(raw_body),
            )
            if not verified:
                raise HTTPException(
                    status_code=401,
                    detail={"code": "INVALID_TOKEN",
                            "message": "Salla Token strategy verification failed."},
                )
        else:
            # Signature strategy — HMAC-SHA256 over the raw body.
            provided_sig = request.headers.get(SIGNATURE_HEADER) or ""
            verified = verify_signature(raw_body, provided_sig, secret)
            _log.info(
                "easy_mode.webhook_received strategy=%s sig_present=%s "
                "verified=%s ip=%s body_len=%d",
                strategy, bool(provided_sig), verified,
                getattr(request.client, "host", None), len(raw_body),
            )
            if not verified:
                _log.warning(
                    "easy_mode.invalid_signature ip=%s sig_present=%s body_len=%d",
                    getattr(request.client, "host", None),
                    bool(provided_sig),
                    len(raw_body),
                )
                raise HTTPException(
                    status_code=401,
                    detail={"code": "INVALID_SIGNATURE",
                            "message": "x-salla-signature missing or does not match."},
                )

        # 3. Now safe to parse JSON. A malformed body after a valid HMAC
        #    is essentially impossible (signature would be wrong) — but
        #    we still guard against it to avoid 500s in Salla retry loops.
        import json
        try:
            body = json.loads(raw_body.decode("utf-8") or "{}")
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            raise HTTPException(
                status_code=400,
                detail={"code": "INVALID_JSON", "message": str(e)},
            )

        # 4. Dispatch. Unknown events return 200 (Salla will keep
        #    retrying on non-2xx — so we MUST 200 anything we don't
        #    care about to avoid retry storms).
        result = await dispatch_event(db, body)
        _log.info(
            "easy_mode.dispatched strategy=%s event=%s stored=%s http_status=200",
            strategy,
            (body or {}).get("event") or "<none>",
            (result or {}).get("stored"),
        )
        return result

    # ── 1. Status (always safe — never returns tokens) ────────────────
    @router.get("/status")
    async def status(user: dict = Depends(current_user)):
        doc = await get_integration(db, user["id"])
        return integration_to_public(doc)

    # ── 2. Begin OAuth flow ───────────────────────────────────────────
    @router.get("/oauth/login")
    async def oauth_login(request: Request, user: dict = Depends(current_user)):
        if not is_configured():
            raise HTTPException(
                status_code=503,
                detail=(
                    "Salla OAuth ليس مُعدّاً بعد. الرجاء إضافة Client ID و Client Secret "
                    "من صفحة الإعدادات → ربط متجر سلة (أو في backend/.env)."
                ),
            )
        state = secrets.token_urlsafe(32)
        now = datetime.now(timezone.utc)
        # Store state with TTL so the callback can validate it.
        await db.salla_oauth_states.insert_one({
            "state": state,
            "user_id": user["id"],
            "created_at": now,
            "expires_at": now + timedelta(minutes=10),
        })
        redirect_uri = build_redirect_uri(_public_base_url(request))
        params = {
            "response_type": "code",
            "client_id": get_client_id(),
            "redirect_uri": redirect_uri,
            "scope": DEFAULT_SCOPES,
            "state": state,
        }
        authorize_url = f"{SALLA_AUTHORIZE_URL}?{urlencode(params)}"
        # Iter-291 — Log the *exact* OAuth URL we send the merchant to.
        # No secrets are in the URL (client_secret is only used at the
        # /oauth2/token step, never in the authorize redirect). The
        # `state` is a one-shot CSRF token, so logging it is harmless.
        # This makes Salla `invalid_scope` failures debuggable without
        # asking the merchant to copy the URL from their browser bar.
        try:
            import logging
            logging.getLogger("salla.oauth").info(
                "salla.oauth.authorize_url user_id=%s client_id=%s redirect_uri=%s scope=%r state=%s url=%s",
                user.get("id"),
                params["client_id"],
                redirect_uri,
                DEFAULT_SCOPES,
                state[:8] + "…",
                authorize_url,
            )
        except Exception:  # noqa: BLE001 — logging must never break OAuth
            pass
        return {
            "authorize_url": authorize_url,
            "redirect_uri": redirect_uri,
            # Iter-291 — surface scope explicitly so the frontend can show
            # "we will request these permissions" and so curl/QA can
            # verify what's being requested without parsing the URL.
            "scope": DEFAULT_SCOPES,
            "scope_list": DEFAULT_SCOPES.split(),
        }

    # ── 2b. Debug: what scopes will we request? (no state, no redirect)
    # Iter-291 — Lets the merchant / support engineer verify the exact
    # scope string Mezan sends to Salla, without triggering the OAuth
    # flow. Critical when chasing `invalid_scope` errors: if a scope is
    # not enabled in the Salla Partners Portal, Salla rejects the whole
    # request — this endpoint lets you compare side-by-side.
    @router.get("/oauth/scopes")
    async def oauth_scopes(user: dict = Depends(current_user)):  # noqa: ARG001 — auth-gated
        return {
            "scope": DEFAULT_SCOPES,
            "scope_list": DEFAULT_SCOPES.split(),
            "source": (
                "env:SALLA_OAUTH_SCOPES"
                if os.environ.get("SALLA_OAUTH_SCOPES")
                else "env:SALLA_CARTS_READ_APPROVED"
                if "carts.read" in DEFAULT_SCOPES.split()
                else "code_default"
            ),
            "note": (
                "Every scope above MUST be enabled in your Salla Partners "
                "Portal App. Any unenabled scope will cause `invalid_scope` "
                "during /oauth2/auth."
            ),
        }

    # ── 3. OAuth callback ─────────────────────────────────────────────
    # Salla redirects the browser here after the merchant consents. We
    # exchange the code, fetch store_info, persist + redirect the user
    # back to the frontend success/error page.
    @router.get("/oauth/callback")
    async def oauth_callback(
        request: Request,
        code: Optional[str] = Query(default=None),
        state: Optional[str] = Query(default=None),
        error: Optional[str] = Query(default=None),
        error_description: Optional[str] = Query(default=None),
    ):
        frontend = _frontend_origin(request)
        # 3a. Did Salla bounce us back with an error?
        if error:
            return RedirectResponse(
                url=f"{frontend}/settings/salla?status=error&reason={error}",
                status_code=302,
            )
        if not code or not state:
            return RedirectResponse(
                url=f"{frontend}/settings/salla?status=error&reason=missing_code_or_state",
                status_code=302,
            )

        # 3b. Validate state — look up which user started this flow.
        state_doc = await db.salla_oauth_states.find_one({"state": state})
        if not state_doc:
            return RedirectResponse(
                url=f"{frontend}/settings/salla?status=error&reason=invalid_state",
                status_code=302,
            )
        await db.salla_oauth_states.delete_one({"state": state})
        if isinstance(state_doc.get("expires_at"), datetime):
            if state_doc["expires_at"] < datetime.now(timezone.utc):
                return RedirectResponse(
                    url=f"{frontend}/settings/salla?status=error&reason=state_expired",
                    status_code=302,
                )
        user_id = state_doc["user_id"]
        redirect_uri = build_redirect_uri(_public_base_url(request))

        # 3c. Exchange the code for tokens.
        try:
            token_payload = await exchange_code(code, redirect_uri)
        except SallaError as e:
            return RedirectResponse(
                url=f"{frontend}/settings/salla?status=error&reason=exchange_failed&detail={(str(e))[:200]}",
                status_code=302,
            )

        # 3d. Pull /store/info immediately so we know which store this is.
        try:
            store_info = await fetch_store_info(token_payload["access_token"])
        except SallaError as e:
            # Token works but /store/info failed — still save the tokens so
            # the merchant doesn't have to re-OAuth, but mark the issue.
            await store_token_response(db, user_id, token_payload, store_info=None)
            return RedirectResponse(
                url=f"{frontend}/settings/salla?status=warn&reason=store_info_failed&detail={(str(e))[:200]}",
                status_code=302,
            )

        await store_token_response(db, user_id, token_payload, store_info=store_info)
        return RedirectResponse(
            url=f"{frontend}/settings/salla?status=connected",
            status_code=302,
        )

    # ── 4. Test connection (live API call) ────────────────────────────
    @router.post("/test-connection")
    async def test_connection(user: dict = Depends(current_user)):
        try:
            access = await ensure_fresh_access_token(db, user["id"])
            info = await fetch_store_info(access)
        except SallaError as e:
            raise HTTPException(
                status_code=e.status_code if e.status_code != 200 else 400,
                detail={"message": str(e), "needs_reauth": e.needs_reauth},
            )
        # Persist the freshly-fetched store info for the next /status read.
        data = info.get("data") or info
        await db.salla_integrations.update_one(
            {"user_id": user["id"]},
            {"$set": {
                "store_id": data.get("id"),
                "store_name": data.get("name"),
                "store_domain": data.get("domain"),
                "store_email": data.get("email"),
                "store_plan": data.get("plan"),
                "store_status": data.get("status"),
                "updated_at": datetime.now(timezone.utc),
            }},
        )
        return {
            "ok": True,
            "store": {
                "id": data.get("id"),
                "name": data.get("name"),
                "domain": data.get("domain"),
                "email": data.get("email"),
                "plan": data.get("plan"),
                "status": data.get("status"),
            },
        }

    # ── 5. Refresh cached store info from Salla ───────────────────────
    @router.post("/refresh-store-info")
    async def refresh_store_info(user: dict = Depends(current_user)):
        try:
            access = await ensure_fresh_access_token(db, user["id"])
            info = await fetch_store_info(access)
        except SallaError as e:
            raise HTTPException(
                status_code=e.status_code if e.status_code != 200 else 400,
                detail={"message": str(e), "needs_reauth": e.needs_reauth},
            )
        data = info.get("data") or info
        await db.salla_integrations.update_one(
            {"user_id": user["id"]},
            {"$set": {
                "store_id": data.get("id"),
                "store_name": data.get("name"),
                "store_domain": data.get("domain"),
                "store_email": data.get("email"),
                "store_plan": data.get("plan"),
                "store_status": data.get("status"),
                "updated_at": datetime.now(timezone.utc),
            }},
        )
        return {"ok": True, "store": data}

    # ── 6. Disconnect (revoke locally) ────────────────────────────────
    # NOTE: Phase-2 should also DELETE webhooks on Salla side before
    # removing the row. For now the merchant can manage that manually.
    @router.post("/disconnect")
    async def disconnect(user: dict = Depends(current_user)):
        n = await disconnect_integration(db, user["id"])
        return {"ok": True, "removed": n}

    # ── 7. OAuth credentials (UI-managed Client ID / Secret) ──────────
    @router.get("/config")
    async def get_config(user: dict = Depends(current_user)):
        cfg = await get_oauth_config(db) or {}
        # Iter-2026-02.rev23 — Easy Mode install URL.
        # SALLA_APP_ID is read from env ONLY (never from UI). If present,
        # we expose the exact Salla App Store install URL — the frontend
        # opens it directly, no OAuth redirect involved.
        # If unset, install_url=None + install_url_error tells the UI to
        # show `SALLA_APP_ID_NOT_CONFIGURED` — no partial URL is ever
        # returned.
        salla_app_id = (os.environ.get("SALLA_APP_ID") or "").strip()
        install_url = (
            f"https://s.salla.sa/apps/install/{salla_app_id}"
            if salla_app_id else None
        )
        # webhook secret presence (boolean only — never expose the value).
        from .easy_mode_webhook import get_webhook_secret
        webhook_secret_configured = bool(get_webhook_secret())

        # Never echo the raw secret back. We just say whether it exists.
        return {
            "client_id": cfg.get("client_id") or "",
            "redirect_uri": cfg.get("redirect_uri") or "",
            "has_client_secret": bool(cfg.get("has_client_secret")),
            "updated_at": (cfg.get("updated_at").isoformat()
                           if hasattr(cfg.get("updated_at"), "isoformat") else None),
            "configured": is_configured(),
            "env_client_id_present": bool(os.environ.get("SALLA_CLIENT_ID")),
            "env_client_secret_present": bool(os.environ.get("SALLA_CLIENT_SECRET")),
            # Easy Mode surface (rev23):
            "install_mode": "easy_mode",
            "install_url": install_url,
            "install_url_error": (
                None if install_url else "SALLA_APP_ID_NOT_CONFIGURED"),
            "salla_app_id_present": bool(salla_app_id),
            "webhook_secret_configured": webhook_secret_configured,
            "webhook_path": "/api/salla/webhooks/app",
        }

    @router.put("/config")
    async def put_config(payload: dict, user: dict = Depends(current_user)):
        client_id = (payload.get("client_id") or "").strip()
        client_secret = (payload.get("client_secret") or "").strip() or None
        redirect_uri = (payload.get("redirect_uri") or "").strip()
        if not client_id:
            raise HTTPException(status_code=400, detail="Client ID مطلوب.")
        await save_oauth_config(
            db, client_id=client_id, client_secret=client_secret, redirect_uri=redirect_uri,
        )
        # Reload in-process cache so the very next OAuth call sees the new values.
        fresh = await get_oauth_config(db) or {}
        update_credentials_cache(
            fresh.get("client_id") or "",
            fresh.get("client_secret") or "",
        )
        return {"ok": True, "configured": is_configured()}

    @router.delete("/config")
    async def delete_config(user: dict = Depends(current_user)):
        n = await delete_oauth_config(db)
        update_credentials_cache("", "")
        return {"ok": True, "removed": n}

    # ── 7b. Full reset — wipe OAuth config + tokens + pending states ──
    # Convenience endpoint for the merchant to "start over" with one
    # click. Idempotent: safe to call even when nothing is saved.
    @router.post("/reset")
    async def reset_salla(user: dict = Depends(current_user)):
        # 1. Disconnect (removes the connected merchant row + tokens)
        tokens_removed = await disconnect_integration(db, user["id"])
        # 2. Wipe OAuth credentials (Client ID / Secret)
        config_removed = await delete_oauth_config(db)
        # 3. Clear any pending OAuth state rows belonging to this user.
        #    These are CSRF guards created by /oauth/login; they auto-
        #    expire after 10 min but we wipe them now for a clean slate.
        states_res = await db.salla_oauth_states.delete_many({"user_id": user["id"]})
        # 4. Refresh the in-process credentials cache so the very next
        #    /status call reports configured=false.
        update_credentials_cache("", "")
        return {
            "ok": True,
            "tokens_removed": tokens_removed,
            "config_removed": config_removed,
            "pending_states_removed": states_res.deleted_count,
        }

    # ── 8. Manual sync — orders / products ────────────────────────────
    @router.post("/sync/orders", status_code=202)
    async def sync_orders(payload: Optional[dict] = None, user: dict = Depends(current_user)):
        payload = payload or {}
        user_id = str(user["id"])
        now = datetime.now(timezone.utc)
        await db.salla_sync_logs.update_many(
            {
                "user_id": user_id,
                "kind": "orders",
                "status": "running",
                "started_at": {"$lt": now - timedelta(minutes=30)},
            },
            {"$set": {
                "status": "interrupted",
                "ended_at": now,
                "last_error": "stale_running_sync_recovered",
            }},
        )

        lease_token, active = await _acquire_order_sync_lease(db, user_id)
        if not lease_token:
            return {
                "ok": True,
                "accepted": True,
                "already_running": True,
                "log_id": (active or {}).get("log_id"),
            }

        try:
            log_id = await create_sync_log(db, user_id, "orders")
            await db.salla_sync_leases.update_one(
                {
                    "_id": f"salla-orders:{user_id}",
                    "lease_token": lease_token,
                },
                {"$set": {"log_id": log_id}},
            )
        except Exception:
            await _release_order_sync_lease(db, user_id, lease_token)
            raise
        try:
            task = asyncio.create_task(
                run_orders_sync_background(
                    user_id=user_id,
                    lease_token=lease_token,
                    log_id=log_id,
                    from_date=payload.get("from_date"),
                    to_date=payload.get("to_date"),
                    updated_since_hours=payload.get("updated_since_hours"),
                    recover_marketing_attribution=(
                        payload.get("recover_marketing_attribution") is True
                    ),
                ),
                name=f"salla-manual-order-sync:{user_id}",
            )
            _MANUAL_ORDER_SYNC_TASKS[user_id] = task
        except Exception:
            await db.salla_sync_logs.update_one(
                {"id": log_id},
                {"$set": {
                    "status": "failed",
                    "ended_at": datetime.now(timezone.utc),
                    "last_error": "failed_to_schedule_background_sync",
                }},
            )
            await _release_order_sync_lease(db, user_id, lease_token)
            raise
        return {"ok": True, "accepted": True, "log_id": log_id}

    @router.post("/sync/products")
    async def sync_products(user: dict = Depends(current_user)):
        try:
            result = await run_products_sync(db, user["id"])
        except SallaError as e:
            raise HTTPException(
                status_code=e.status_code if e.status_code != 200 else 400,
                detail={"message": str(e), "needs_reauth": e.needs_reauth},
            )
        return {"ok": True, **result}

    def _public_cart_sync_log(row: Optional[dict]) -> Optional[dict]:
        if not row:
            return None
        allowed = (
            "id",
            "kind",
            "status",
            "created",
            "updated",
            "errors_count",
            "pages_fetched",
            "rows_seen",
            "rows_saved",
            "stopped_reason",
            "started_at",
            "ended_at",
            "updated_at",
            "last_error",
            "provider_write_reached",
            "pii_stored",
            "plaintext_pii_stored",
            "schema_version",
            "identity_linked",
            "attributed",
            "order_linked",
            "private_context_encrypted",
            "customer_orders_linked",
        )
        public = {key: row.get(key) for key in allowed if key in row}
        for key in ("started_at", "ended_at", "updated_at"):
            value = public.get(key)
            if hasattr(value, "isoformat"):
                public[key] = value.isoformat()
        return public

    @router.get("/abandoned-carts/status")
    async def abandoned_carts_status(user: dict = Depends(current_user)):
        """Privacy-safe progress and coverage summary for the current tenant."""
        user_id = user["id"]
        closed_historical_runs = await close_running_historical_cart_imports(
            db, user_id
        )
        integration = await get_integration(db, user_id)
        scope_granted = bool(
            integration
            and "carts.read" in split_scopes(integration.get("scope"))
        )
        carts = getattr(db, ABANDONED_CART_COLLECTION)
        events = getattr(db, ABANDONED_CART_EVENT_COLLECTION)
        identities = getattr(db, CUSTOMER_IDENTITY_COLLECTION)
        total_carts = await carts.count_documents({"user_id": user_id})
        purchased_carts = await carts.count_documents(
            {"user_id": user_id, "purchased": True}
        )
        webhook_events = await events.count_documents({"user_id": user_id})
        identity_linked_carts = await carts.count_documents(
            {
                "user_id": user_id,
                "customer_identity_id": {"$exists": True, "$ne": None},
            }
        )
        attributed_carts = await carts.count_documents(
            {
                "user_id": user_id,
                "$or": [
                    {"attribution.platform": {"$exists": True, "$ne": None}},
                    {"attribution.campaign_id": {"$exists": True, "$ne": None}},
                    {"attribution.utm_source": {"$exists": True, "$ne": None}},
                ],
            }
        )
        order_linked_carts = await carts.count_documents(
            {
                "user_id": user_id,
                "$or": [
                    {"order_id": {"$exists": True, "$ne": None}},
                    {"order_number": {"$exists": True, "$ne": None}},
                ],
            }
        )
        encrypted_private_carts = await carts.count_documents(
            {"user_id": user_id, "private_cart_context_encrypted": True}
        )
        customer_identities = await identities.count_documents({"user_id": user_id})
        customer_memory_orders = await db.unified_orders.count_documents(
            {
                "user_id": user_id,
                "customer_identity_id": {"$exists": True, "$ne": None},
            }
        )
        event_counts = {
            event_name: await events.count_documents(
                {"user_id": user_id, "event": event_name}
            )
            for event_name in sorted(ABANDONED_CART_EVENTS)
        }
        latest_cart = await carts.find_one(
            {"user_id": user_id},
            {"_id": 0, "updated_at": 1, "cart_updated_at": 1},
            sort=[("updated_at", -1)],
        )
        latest_event = await events.find_one(
            {"user_id": user_id},
            {"_id": 0, "last_received_at": 1},
            sort=[("last_received_at", -1)],
        )
        latest_sync = await db.salla_sync_logs.find_one(
            {"user_id": user_id, "kind": "abandoned_carts"},
            {"_id": 0},
            sort=[("started_at", -1)],
        )

        def iso(value):
            return value.isoformat() if hasattr(value, "isoformat") else value

        return {
            "ok": True,
            "scope_granted": scope_granted,
            "total_carts": total_carts,
            "active_carts": max(total_carts - purchased_carts, 0),
            "purchased_carts": purchased_carts,
            "webhook_events": webhook_events,
            "schema_version": ABANDONED_CART_SCHEMA_VERSION,
            "customer_identities": customer_identities,
            "customer_memory_orders": customer_memory_orders,
            "identity_linked_carts": identity_linked_carts,
            "attributed_carts": attributed_carts,
            "order_linked_carts": order_linked_carts,
            "encrypted_private_carts": encrypted_private_carts,
            "event_counts": event_counts,
            "latest_cart_at": iso(
                (latest_cart or {}).get("cart_updated_at")
                or (latest_cart or {}).get("updated_at")
            ),
            "latest_event_at": iso((latest_event or {}).get("last_received_at")),
            "last_sync": _public_cart_sync_log(latest_sync),
            "import_running": False,
            "historical_import_enabled": (
                HISTORICAL_ABANDONED_CART_IMPORT_ENABLED
            ),
            "live_webhooks_only": True,
            "closed_historical_runs": closed_historical_runs,
            "provider_write_reached": False,
            "pii_stored": False,
            "plaintext_pii_stored": False,
            "private_data_encrypted_at_rest": True,
        }

    @router.post("/sync/abandoned-carts", status_code=410)
    async def sync_abandoned_carts(user: dict = Depends(current_user)):
        """Reject historical imports; new carts arrive through live webhooks."""
        user_id = user["id"]
        await close_running_historical_cart_imports(db, user_id)
        raise HTTPException(
            status_code=410,
            detail={
                "code": "historical_abandoned_cart_import_disabled",
                "message": (
                    "تم إيقاف الاستيراد التاريخي. تصل السلات الجديدة "
                    "وتحديثاتها مباشرة عبر Webhook."
                ),
                "live_webhooks_only": True,
            },
        )

    # ── 9. Sync logs ──────────────────────────────────────────────────
    @router.get("/sync/logs")
    async def list_sync_logs(
        user: dict = Depends(current_user),
        limit: int = Query(default=50, ge=1, le=200),
        kind: Optional[str] = Query(default=None),
    ):
        q: dict = {"user_id": user["id"]}
        if kind:
            q["kind"] = kind
        cursor = db.salla_sync_logs.find(q, {"_id": 0}).sort("started_at", -1).limit(limit)
        logs = []
        async for row in cursor:
            for k in ("started_at", "ended_at"):
                v = row.get(k)
                if hasattr(v, "isoformat"):
                    row[k] = v.isoformat()
            logs.append(row)
        return {"logs": logs}

    # ── 10. Sources comparison (salla_direct vs make vs excel) ────────
    @router.get("/sources-comparison")
    async def sources_comparison(
        user: dict = Depends(current_user),
        from_date: Optional[str] = Query(default=None),
        to_date: Optional[str] = Query(default=None),
    ):
        data = await compute_sources_comparison(db, user["id"], from_date=from_date, to_date=to_date)
        return data

    api_router.include_router(router)


async def ensure_salla_indexes(db) -> None:
    """Idempotent index creation called once at app startup."""
    await db.salla_integrations.create_index("user_id", unique=True)
    await db.salla_oauth_states.create_index("state", unique=True)
    # Auto-expire stale OAuth states after 10 min so the collection
    # doesn't grow forever.
    await db.salla_oauth_states.create_index("expires_at", expireAfterSeconds=0)
    await ensure_abandoned_cart_indexes(db)
    # Phase 2 — sync indexes
    await ensure_sync_indexes(db)
    # Warm up the credentials cache from DB so the very first OAuth
    # attempt after a restart already sees the UI-saved values.
    cfg = await get_oauth_config(db) or {}
    update_credentials_cache(cfg.get("client_id") or "", cfg.get("client_secret") or "")
