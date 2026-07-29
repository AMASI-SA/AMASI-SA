"""Owner-only Google OAuth routes for Apps & Integrations V2."""
from __future__ import annotations

import hashlib
import hmac
import os
import uuid
from typing import Any, Callable
from urllib.parse import parse_qs, urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse

from .google_discovery import _discover_google_accounts, _userinfo
from .google_oauth_security import (
    GOOGLE_PROVIDER_IDS,
    GOOGLE_SOURCE_MODE,
    _callback_redirect,
    _consume_state,
    _exchange_code,
    _iso,
    _redirect_uri,
    _state_secret,
    ensure_google_connection_indexes,
    google_oauth_configured,
    start_google_connection,
)
from .google_projection import _persist_google_projection

GOOGLE_BINDING_COOKIE = "mezan_google_oauth_binding"
GOOGLE_BINDING_MAX_AGE = 15 * 60
GOOGLE_CALLBACK_PATH = "/api/integrations-v2/google/callback"


def _browser_binding(state_token: str) -> str:
    return hmac.new(
        _state_secret().encode("utf-8"),
        f"google-browser:{state_token}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _state_from_authorization_url(value: str) -> str:
    try:
        rows = parse_qs(urlsplit(value).query).get("state") or []
        return str(rows[0]).strip() if rows else ""
    except Exception:  # noqa: BLE001
        return ""


def _secure_cookie() -> bool:
    return _redirect_uri().lower().startswith("https://")


def _clear_binding_cookie(response: RedirectResponse) -> None:
    response.delete_cookie(
        GOOGLE_BINDING_COOKIE,
        path=GOOGLE_CALLBACK_PATH,
        secure=_secure_cookie(),
        httponly=True,
        samesite="lax",
    )


async def handle_google_callback(
    db: Any,
    *,
    code: str | None,
    state_token: str | None,
    provider_error: str | None,
    browser_binding: str | None,
) -> RedirectResponse:
    if provider_error:
        return RedirectResponse(
            _callback_redirect(outcome="error", code=provider_error), status_code=302
        )
    if not code or not state_token:
        return RedirectResponse(
            _callback_redirect(outcome="error", code="missing_code_or_state"),
            status_code=302,
        )
    expected_binding = _browser_binding(state_token)
    if not browser_binding or not hmac.compare_digest(
        expected_binding, str(browser_binding)
    ):
        return RedirectResponse(
            _callback_redirect(outcome="error", code="browser_binding_mismatch"),
            status_code=302,
        )
    try:
        await ensure_google_connection_indexes(db)
        state_payload = await _consume_state(db, state_token)
        token_payload = await _exchange_code(code)
        access_token = str(token_payload["access_token"])
        identity = await _userinfo(access_token)
        scope_text = str(token_payload.get("scope") or "")
        granted_scopes = {scope for scope in scope_text.split() if scope}
        # Fail closed when Google does not report granted scopes. Granular
        # consent allows the user to omit individual services, so requested
        # scopes must never be treated as granted without evidence.
        accounts, errors = await _discover_google_accounts(
            access_token, granted_scopes
        )
        await _persist_google_projection(
            db,
            user_id=str(state_payload["user_id"]),
            token_payload=token_payload,
            identity=identity,
            granted_scopes=granted_scopes,
            accounts=accounts,
            errors=errors,
        )
    except ValueError as exc:
        return RedirectResponse(
            _callback_redirect(outcome="error", code=str(exc)), status_code=302
        )
    except Exception:  # noqa: BLE001
        return RedirectResponse(
            _callback_redirect(outcome="error", code="google_oauth_failed"),
            status_code=302,
        )
    return RedirectResponse(_callback_redirect(outcome="connected"), status_code=302)


async def test_google_connection(db: Any, user_id: str, provider: str) -> dict[str, Any]:
    if provider not in GOOGLE_PROVIDER_IDS:
        raise HTTPException(status_code=404, detail={"code": "unknown_google_provider"})
    now_iso = _iso()
    run_id = str(uuid.uuid4())
    integration = await db.mezan_integrations_v2.find_one(
        {"user_id": user_id, "provider": provider},
        {"_id": 0, "connection_status": 1, "connection_provenance": 1, "data_quality": 1},
    )
    account_count = await db.mezan_integration_accounts_v2.count_documents(
        {"user_id": user_id, "provider": provider, "connection_status": "connected"}
    )
    connected = bool(
        integration
        and integration.get("connection_status") == "connected"
        and integration.get("connection_provenance") == "api_connection"
    )
    run_status = "passed" if connected else "not_connected"
    health = {
        "status": "healthy" if connected and account_count else "degraded" if connected else "not_available",
        "score": 100 if connected and account_count else 75 if connected else None,
        "checked_at": now_iso,
        "data_quality": (integration or {}).get("data_quality") or "unavailable",
    }
    await db.mezan_integration_health_v2.insert_one(
        {
            "user_id": user_id,
            "provider": provider,
            "health_status": health["status"],
            "health_score": health["score"],
            "data_quality": health["data_quality"],
            "connection_status": "connected" if connected else "not_connected",
            "connection_provenance": "api_connection" if connected else "disconnected",
            "data_delay_minutes": 0 if connected and account_count else None,
            "checked_at": now_iso,
            "source_mode": GOOGLE_SOURCE_MODE,
            "run_id": run_id,
        }
    )
    message = (
        "Google OAuth connection evidence and discovered accounts are present."
        if connected and account_count
        else "Google OAuth is connected, but no account was discovered for this service."
        if connected
        else "No verified Google OAuth connection exists for this service."
    )
    await db.mezan_integration_sync_runs_v2.insert_one(
        {
            "run_id": run_id,
            "user_id": user_id,
            "provider": provider,
            "run_type": "local_connection_test",
            "status": run_status,
            "started_at": now_iso,
            "finished_at": now_iso,
            "source_mode": GOOGLE_SOURCE_MODE,
            "summary": {
                "message": message,
                "account_count": int(account_count),
                "connection_status": "connected" if connected else "not_connected",
            },
            "error": None,
        }
    )
    return {
        "provider": provider,
        "run_id": run_id,
        "status": run_status,
        "health": health,
        "message": message,
    }


def install_google_connection_actions() -> None:
    from . import service as service_module

    original_actions = service_module._actions
    if getattr(original_actions, "_mezan_google_connection_actions", False):
        return

    def wrapped_actions(definition: Any, snapshot: dict) -> dict:
        actions = original_actions(definition, snapshot)
        is_google = definition.provider in GOOGLE_PROVIDER_IDS
        configured = google_oauth_configured()
        actions["connect"] = {
            "enabled": bool(is_google and configured),
            "reason": (
                None
                if is_google and configured
                else "يجب ضبط إعدادات Google OAuth في Backend أولًا."
                if is_google
                else "هذا التطبيق لا يستخدم بوابة ربط Google."
            ),
            "href": None,
        }
        if is_google:
            actions["reconnect"] = dict(actions["connect"])
            actions["test_connection"] = {
                "enabled": snapshot.get("connection_status") == "connected",
                "reason": (
                    None
                    if snapshot.get("connection_status") == "connected"
                    else "اربط حساب Google أولًا قبل تشغيل الفحص المحلي."
                ),
                "href": None,
            }
        return actions

    wrapped_actions._mezan_google_connection_actions = True  # type: ignore[attr-defined]
    service_module._actions = wrapped_actions


def attach_google_connection_routes(
    router: APIRouter,
    db: Any,
    current_user: Callable,
    require_owner: Callable[[Any], dict],
) -> None:
    install_google_connection_actions()

    @router.post("/google/connect/start")
    async def google_connect_start(
        response: Response,
        user: dict = Depends(current_user),
    ) -> dict:
        owner = require_owner(user)
        if not os.environ.get("FRONTEND_URL", "").strip():
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "google_oauth_frontend_url_missing",
                    "message": "يجب ضبط FRONTEND_URL في بيئة Backend أولًا.",
                    "missing": ["FRONTEND_URL"],
                },
            )
        result = await start_google_connection(db, str(owner["id"]))
        state_token = _state_from_authorization_url(result["authorization_url"])
        if not state_token:
            raise HTTPException(
                status_code=500,
                detail={
                    "code": "google_oauth_state_generation_failed",
                    "message": "تعذر إنشاء جلسة ربط Google الآمنة.",
                },
            )
        response.set_cookie(
            GOOGLE_BINDING_COOKIE,
            _browser_binding(state_token),
            max_age=GOOGLE_BINDING_MAX_AGE,
            secure=_secure_cookie(),
            httponly=True,
            samesite="lax",
            path=GOOGLE_CALLBACK_PATH,
        )
        return result

    @router.get("/google/callback", include_in_schema=False)
    async def google_callback(request: Request) -> RedirectResponse:
        response = await handle_google_callback(
            db,
            code=request.query_params.get("code"),
            state_token=request.query_params.get("state"),
            provider_error=request.query_params.get("error"),
            browser_binding=request.cookies.get(GOOGLE_BINDING_COOKIE),
        )
        _clear_binding_cookie(response)
        return response

    def make_google_local_test(provider_id: str) -> Callable:
        async def google_local_test(user: dict = Depends(current_user)) -> dict:
            owner = require_owner(user)
            return await test_google_connection(
                db, str(owner["id"]), provider_id
            )

        return google_local_test

    for provider_id in sorted(GOOGLE_PROVIDER_IDS):
        router.add_api_route(
            f"/{provider_id}/test-connection",
            make_google_local_test(provider_id),
            methods=["POST"],
            name=f"test_{provider_id}_connection",
        )
