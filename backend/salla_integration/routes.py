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

import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request, Query
from fastapi.responses import RedirectResponse, HTMLResponse

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
    ensure_sync_indexes,
    run_orders_sync,
    run_products_sync,
)


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
        return {
            "authorize_url": f"{SALLA_AUTHORIZE_URL}?{urlencode(params)}",
            "redirect_uri": redirect_uri,
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
    @router.post("/sync/orders")
    async def sync_orders(payload: Optional[dict] = None, user: dict = Depends(current_user)):
        payload = payload or {}
        try:
            result = await run_orders_sync(
                db, user["id"],
                from_date=payload.get("from_date"),
                to_date=payload.get("to_date"),
                updated_since_hours=payload.get("updated_since_hours"),
            )
        except SallaError as e:
            raise HTTPException(
                status_code=e.status_code if e.status_code != 200 else 400,
                detail={"message": str(e), "needs_reauth": e.needs_reauth},
            )
        return {"ok": True, **result}

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
    # Phase 2 — sync indexes
    await ensure_sync_indexes(db)
    # Warm up the credentials cache from DB so the very first OAuth
    # attempt after a restart already sees the UI-saved values.
    cfg = await get_oauth_config(db) or {}
    update_credentials_cache(cfg.get("client_id") or "", cfg.get("client_secret") or "")
