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
)


# Where the frontend wants to land after the callback completes
# (success or failure). Computed from REACT_APP_BACKEND_URL minus the
# trailing /api so it points at the SPA. Override via SALLA_RETURN_URL
# if you ever want the redirect to go to a different host.
def _frontend_origin() -> str:
    explicit = os.environ.get("SALLA_RETURN_URL", "").strip()
    if explicit:
        return explicit.rstrip("/")
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
                    "Salla OAuth ليس مُعدّاً بعد. الرجاء إضافة SALLA_CLIENT_ID و "
                    "SALLA_CLIENT_SECRET في backend/.env من Salla Partners → My Apps."
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
        frontend = _frontend_origin()
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

    api_router.include_router(router)


async def ensure_salla_indexes(db) -> None:
    """Idempotent index creation called once at app startup."""
    await db.salla_integrations.create_index("user_id", unique=True)
    await db.salla_oauth_states.create_index("state", unique=True)
    # Auto-expire stale OAuth states after 10 min so the collection
    # doesn't grow forever.
    await db.salla_oauth_states.create_index("expires_at", expireAfterSeconds=0)
