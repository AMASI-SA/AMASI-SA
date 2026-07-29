"""Owner-only TikTok Marketing API routes for Apps & Integrations V2."""
from __future__ import annotations

import hashlib
import hmac
import os
import uuid
from typing import Any, Callable
from urllib.parse import parse_qs, urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import RedirectResponse

from .tiktok_discovery import discover_tiktok_advertisers
from .tiktok_oauth_security import (
    TIKTOK_PROVIDER_ID,
    TIKTOK_SOURCE_MODE,
    _callback_redirect,
    _consume_state,
    _exchange_code,
    _iso,
    _redirect_uri,
    _state_secret,
    ensure_tiktok_connection_indexes,
    start_tiktok_connection,
    tiktok_oauth_configured,
)
from .tiktok_projection import persist_tiktok_projection

TIKTOK_BINDING_COOKIE = "mezan_tiktok_oauth_binding"
TIKTOK_BINDING_MAX_AGE = 15 * 60
TIKTOK_CALLBACK_PATH = "/api/integrations-v2/tiktok/callback"


def _browser_binding(state_token: str) -> str:
    return hmac.new(
        _state_secret().encode("utf-8"),
        f"tiktok-browser:{state_token}".encode("utf-8"),
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
        TIKTOK_BINDING_COOKIE,
        path=TIKTOK_CALLBACK_PATH,
        secure=_secure_cookie(),
        httponly=True,
        samesite="lax",
    )


async def handle_tiktok_callback(
    db: Any,
    *,
    auth_code: str | None,
    state_token: str | None,
    provider_error: str | None,
    browser_binding: str | None,
) -> RedirectResponse:
    if provider_error:
        return RedirectResponse(
            _callback_redirect(outcome="error", code=provider_error), status_code=302
        )
    if not auth_code or not state_token:
        return RedirectResponse(
            _callback_redirect(outcome="error", code="missing_auth_code_or_state"),
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
        await ensure_tiktok_connection_indexes(db)
        state_payload = await _consume_state(db, state_token)
        token_payload = await _exchange_code(auth_code)
        access_token = str(token_payload["access_token"])
        advertiser_ids = [
            str(item).strip()
            for item in (token_payload.get("advertiser_ids") or [])
            if str(item).strip()
        ]
        advertisers: list[dict[str, Any]] = []
        discovery_error = None
        try:
            advertisers = await discover_tiktok_advertisers(
                access_token, advertiser_ids
            )
        except Exception as exc:  # noqa: BLE001
            discovery_error = str(exc)
        await persist_tiktok_projection(
            db,
            user_id=str(state_payload["user_id"]),
            token_payload=token_payload,
            advertisers=advertisers,
            provider_error=discovery_error,
        )
    except ValueError as exc:
        return RedirectResponse(
            _callback_redirect(outcome="error", code=str(exc)), status_code=302
        )
    except Exception:  # noqa: BLE001
        return RedirectResponse(
            _callback_redirect(outcome="error", code="tiktok_oauth_failed"),
            status_code=302,
        )
    return RedirectResponse(_callback_redirect(outcome="connected"), status_code=302)


async def test_tiktok_connection(db: Any, user_id: str) -> dict[str, Any]:
    now_iso = _iso()
    run_id = str(uuid.uuid4())
    integration = await db.mezan_integrations_v2.find_one(
        {"user_id": user_id, "provider": TIKTOK_PROVIDER_ID},
        {
            "_id": 0,
            "connection_status": 1,
            "connection_provenance": 1,
            "data_quality": 1,
        },
    )
    account_count = await db.mezan_integration_accounts_v2.count_documents(
        {
            "user_id": user_id,
            "provider": TIKTOK_PROVIDER_ID,
            "connection_status": "connected",
        }
    )
    connected = bool(
        integration
        and integration.get("connection_status") == "connected"
        and integration.get("connection_provenance") == "api_connection"
    )
    run_status = "passed" if connected and account_count else "partial" if connected else "not_connected"
    health = {
        "status": "healthy" if connected and account_count else "degraded" if connected else "not_available",
        "score": 100 if connected and account_count else 75 if connected else None,
        "checked_at": now_iso,
        "data_quality": (integration or {}).get("data_quality") or "unavailable",
    }
    await db.mezan_integration_health_v2.insert_one(
        {
            "user_id": user_id,
            "provider": TIKTOK_PROVIDER_ID,
            "health_status": health["status"],
            "health_score": health["score"],
            "data_quality": health["data_quality"],
            "connection_status": "connected" if connected else "not_connected",
            "connection_provenance": "api_connection" if connected else "disconnected",
            "data_delay_minutes": 0 if connected and account_count else None,
            "checked_at": now_iso,
            "source_mode": TIKTOK_SOURCE_MODE,
            "run_id": run_id,
        }
    )
    message = (
        "TikTok Marketing API connection and advertiser accounts are present."
        if connected and account_count
        else "TikTok OAuth is connected, but no advertiser account was discovered."
        if connected
        else "No verified TikTok Marketing API connection exists."
    )
    await db.mezan_integration_sync_runs_v2.insert_one(
        {
            "run_id": run_id,
            "user_id": user_id,
            "provider": TIKTOK_PROVIDER_ID,
            "run_type": "local_connection_test",
            "status": run_status,
            "started_at": now_iso,
            "finished_at": now_iso,
            "source_mode": TIKTOK_SOURCE_MODE,
            "summary": {
                "message": message,
                "account_count": int(account_count),
                "make_data_used": False,
                "legacy_collection_read": False,
            },
            "error": None,
        }
    )
    return {
        "provider": TIKTOK_PROVIDER_ID,
        "run_id": run_id,
        "status": run_status,
        "health": health,
        "message": message,
    }


def install_tiktok_connection_actions() -> None:
    from . import service as service_module

    original_actions = service_module._actions
    if getattr(original_actions, "_mezan_tiktok_connection_actions", False):
        return

    def wrapped_actions(definition: Any, snapshot: dict) -> dict:
        actions = original_actions(definition, snapshot)
        if definition.provider != TIKTOK_PROVIDER_ID:
            return actions
        configured = tiktok_oauth_configured()
        connect_action = {
            "enabled": configured,
            "reason": (
                None
                if configured
                else "يجب ضبط إعدادات TikTok Marketing API في Backend أولًا."
            ),
            "href": None,
        }
        actions["connect"] = connect_action
        actions["reconnect"] = dict(connect_action)
        actions["test_connection"] = {
            "enabled": snapshot.get("connection_status") == "connected",
            "reason": (
                None
                if snapshot.get("connection_status") == "connected"
                else "اربط TikTok Marketing API أولًا قبل تشغيل الفحص المحلي."
            ),
            "href": None,
        }
        return actions

    wrapped_actions._mezan_tiktok_connection_actions = True  # type: ignore[attr-defined]
    service_module._actions = wrapped_actions


def install_tiktok_stale_error_filter() -> None:
    from . import service as service_module
    from .google_error_resolution import _as_utc

    service_class = service_module.IntegrationsControlCenterService
    original = service_class._v2_snapshot
    if getattr(original, "_mezan_tiktok_stale_error_filter", False):
        return

    async def wrapped_v2_snapshot(self: Any, user_id: str, definition: Any):
        result = await original(self, user_id, definition)
        if not result:
            return result
        snapshot, health = result
        latest_error = snapshot.get("latest_error")
        if (
            snapshot.get("provider") == TIKTOK_PROVIDER_ID
            and snapshot.get("has_data")
            and isinstance(latest_error, dict)
            and str(latest_error.get("code") or "").startswith("tiktok_discovery_")
        ):
            error_at = _as_utc(latest_error.get("occurred_at"))
            success_at = _as_utc(snapshot.get("last_sync_at"))
            if error_at and success_at and error_at <= success_at:
                snapshot = dict(snapshot)
                snapshot["latest_error"] = None
        return snapshot, health

    wrapped_v2_snapshot._mezan_tiktok_stale_error_filter = True  # type: ignore[attr-defined]
    service_class._v2_snapshot = wrapped_v2_snapshot


def attach_tiktok_connection_routes(
    router: APIRouter,
    db: Any,
    current_user: Callable,
    require_owner: Callable[[Any], dict],
) -> None:
    install_tiktok_connection_actions()
    install_tiktok_stale_error_filter()

    @router.post("/tiktok/connect/start")
    async def tiktok_connect_start(
        response: Response,
        user: dict = Depends(current_user),
    ) -> dict:
        owner = require_owner(user)
        if not os.environ.get("FRONTEND_URL", "").strip():
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "tiktok_oauth_frontend_url_missing",
                    "message": "يجب ضبط FRONTEND_URL في بيئة Backend أولًا.",
                    "missing": ["FRONTEND_URL"],
                },
            )
        result = await start_tiktok_connection(db, str(owner["id"]))
        state_token = _state_from_authorization_url(result["authorization_url"])
        if not state_token:
            raise HTTPException(
                status_code=500,
                detail={
                    "code": "tiktok_oauth_state_generation_failed",
                    "message": "تعذر إنشاء جلسة ربط TikTok الآمنة.",
                },
            )
        response.set_cookie(
            TIKTOK_BINDING_COOKIE,
            _browser_binding(state_token),
            max_age=TIKTOK_BINDING_MAX_AGE,
            secure=_secure_cookie(),
            httponly=True,
            samesite="lax",
            path=TIKTOK_CALLBACK_PATH,
        )
        return result

    @router.get("/tiktok/callback", include_in_schema=False)
    async def tiktok_callback(request: Request) -> RedirectResponse:
        response = await handle_tiktok_callback(
            db,
            auth_code=(
                request.query_params.get("auth_code")
                or request.query_params.get("code")
            ),
            state_token=request.query_params.get("state"),
            provider_error=(
                request.query_params.get("error")
                or request.query_params.get("error_description")
            ),
            browser_binding=request.cookies.get(TIKTOK_BINDING_COOKIE),
        )
        _clear_binding_cookie(response)
        return response

    @router.post(
        f"/{TIKTOK_PROVIDER_ID}/test-connection",
        name="test_tiktok_ads_connection",
    )
    async def tiktok_local_test(user: dict = Depends(current_user)) -> dict:
        owner = require_owner(user)
        return await test_tiktok_connection(db, str(owner["id"]))
