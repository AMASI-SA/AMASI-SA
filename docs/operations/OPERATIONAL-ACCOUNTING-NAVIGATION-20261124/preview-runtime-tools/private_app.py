"""Current source routers, existing browser authentication, isolated copied DB."""
from __future__ import annotations
from contextlib import asynccontextmanager
import json

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request

from private_common import MONGO_URL, SOURCE_SHA, STATE_COLLECTION, RuntimeBlocked, owner_is_valid, verify_database
from private_http import AUTH_METHODS, FEE_PATHS, GET_SUFFIXES, GET_TEMPLATES, PREFIX, authenticated_owner, bounded_body, browser_login_payload, cookie_has_nonbrowser_envelope, denied, relay_auth, ReadOnlyGate


def create_app(config):
    from private_guard import require_installed
    require_installed()
    from motor.motor_asyncio import AsyncIOMotorClient
    from review_acceptance_config_guard import AcceptanceConfigDatabase
    from runtime_mongo import main_client_options
    from mongo_observability import mongo_metrics
    from browser_security import BrowserSecurityMiddleware
    from operational_balance_routes import make_operational_balance_router

    client = AsyncIOMotorClient(MONGO_URL, **main_client_options(event_listener=mongo_metrics), w="majority", journal=True)
    db = AcceptanceConfigDatabase(client[config["database_name"]])
    owner = config["expected_owner_id"]

    @asynccontextmanager
    async def lifespan(app):
        try:
            _, manifest = await verify_database(client, config)
            app.state.source_revision = manifest["state"]["revision"]
            yield
        finally:
            client.close()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    async def current_user(request: Request):
        if "authorization" in request.headers or not request.cookies.get("access_token"):
            raise HTTPException(401, detail={"code": "preview_cookie_browser_session_required"})
        if cookie_has_nonbrowser_envelope(request.cookies["access_token"], "access"):
            raise HTTPException(403, detail={"code": "preview_native_cookie_not_supported"})
        response = await relay_auth("GET", "/api/auth/me", request.scope["headers"])
        if not authenticated_owner(response, owner):
            code = response.status_code if response.status_code in {401, 403, 503} else 403
            raise HTTPException(code, detail={"code": "preview_existing_owner_session_required"})
        actor = await db["users"].find_one({"id": owner}, {"_id": 0, "id": 1, "role": 1, "disabled": 1, "is_active": 1, "deleted_at": 1})
        if not owner_is_valid(actor, owner):
            raise HTTPException(403, detail={"code": "preview_copied_owner_invalid"})
        state = await db[STATE_COLLECTION].find_one({"_id": owner, "owner_id": owner}, {"revision": 1})
        if not state or state.get("revision") != request.app.state.source_revision:
            raise HTTPException(503, detail={"code": "preview_copied_state_changed"})
        # Product scope() reloads the canonical actor and its permissions.
        return {"id": owner}

    async def auth_endpoint(request: Request):
        # A normal password login can replace an invalid/old native cookie; it
        # still goes through the untouched upstream authentication policy.
        checked_cookies = () if request.url.path == "/api/auth/login" else (("access_token", "access"), ("refresh_token", "refresh"))
        for cookie_name, kind in checked_cookies:
            token = request.cookies.get(cookie_name)
            if token and cookie_has_nonbrowser_envelope(token, kind):
                return denied("preview_native_cookie_not_supported")
        try:
            body = await bounded_body(request)
        except ValueError:
            return denied("preview_auth_request_too_large", 413)
        except TimeoutError:
            return denied("preview_auth_request_timeout", 408)
        if request.url.path == "/api/auth/login":
            if not browser_login_payload(body):
                return denied("preview_cookie_browser_session_required")
        elif request.method == "POST":
            try:
                if body and json.loads(body) != {}:
                    return denied("preview_browser_auth_body_required", 400)
            except ValueError:
                return denied("preview_browser_auth_body_required", 400)
        response = await relay_auth(request.method, request.url.path, request.scope["headers"], body)
        if request.url.path == "/api/auth/me" and response.status_code == 200 and not authenticated_owner(response, owner):
            return denied("preview_expected_owner_required")
        if request.url.path == "/api/auth/login" and response.status_code == 200:
            try:
                payload = json.loads(response.body)
            except ValueError:
                return denied("preview_auth_response_invalid", 502)
            if isinstance(payload, dict) and payload.get("id") is not None and (payload.get("id") != owner or payload.get("role") != "owner"):
                return denied("preview_expected_owner_required")
        return response

    for path, method in AUTH_METHODS.items():
        app.add_api_route(path, auth_endpoint, methods=[method], include_in_schema=False)

    router = make_operational_balance_router(db, current_user)
    permitted_get = {"/operational-balances" + suffix for suffix in GET_SUFFIXES | GET_TEMPLATES}
    permitted_delete = "/operational-balances/workspace/read-demand/{key}"
    router.routes[:] = [route for route in router.routes if
                       route.path in permitted_get and route.methods == {"GET"}
                       or route.path == permitted_delete and route.methods == {"DELETE"}]
    if not permitted_get | {permitted_delete} <= {r.path for r in router.routes}:
        raise RuntimeBlocked("candidate_required_routes_missing")
    app.include_router(router, prefix="/api")

    # Preserve the source setup read handlers and their original permission rules.
    from accounting_onboarding_setup_routes import install_setup_contract_routes
    from accounting_write_control import fresh_actor
    from accounting_module_contract import accounting_owner_id, require_accounting_permission
    from accounting_financial_accounts import PERMISSIONS

    async def actor_for(user, *permissions):
        actor = await fresh_actor(db, user)
        for permission in ("opening_view", *permissions):
            require_accounting_permission(actor, PERMISSIONS[permission])
        if not owner_is_valid(actor, owner) or accounting_owner_id(actor) != owner:
            raise HTTPException(403, detail={"code": "preview_expected_owner_required"})
        return actor, owner

    fees = APIRouter()
    install_setup_contract_routes(fees, db, current_user, actor_for, "/accounting-module/onboarding")
    fees.routes[:] = [route for route in fees.routes if "/api" + route.path in FEE_PATHS and route.methods == {"GET"}]
    if {"/api" + route.path for route in fees.routes} != FEE_PATHS:
        raise RuntimeBlocked("candidate_required_fee_routes_missing")
    app.include_router(fees, prefix="/api")
    app.add_middleware(ReadOnlyGate, origin=config["preview_origin"])
    app.add_middleware(BrowserSecurityMiddleware, trusted_origins=[config["preview_origin"]])
    return app
