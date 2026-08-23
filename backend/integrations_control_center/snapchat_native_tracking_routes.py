"""Owner-only routes and card action for Snapchat tracking diagnostics."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException, Query

from .snapchat_native_data_common import SnapchatNativeSyncError
from .snapchat_order_source_audit import build_snapchat_order_source_audit
from .snapchat_native_data_sync import snapchat_native_sync_enabled
from .snapchat_native_tracking_diagnostics import (
    SnapchatTrackingDiagnosticsInput,
    execute_snapchat_tracking_diagnostics,
)
from .snapchat_native_tracking_models import SnapchatTrackingDiagnosticsResponse
from .snapchat_oauth_security import SNAPCHAT_PROVIDER_ID, snapchat_oauth_configured
from .snapchat_tracking_error_details import (
    install_snapchat_tracking_error_detail_persistence,
)


def install_snapchat_native_tracking_actions() -> None:
    """Expose tracking diagnostics only for a proven native API connection."""
    from . import service as service_module

    original_actions = service_module._actions
    if getattr(original_actions, "_mezan_snapchat_native_tracking_actions", False):
        return

    def wrapped_actions(definition: Any, snapshot: dict) -> dict:
        actions = original_actions(definition, snapshot)
        if definition.provider != SNAPCHAT_PROVIDER_ID:
            return actions
        connected = bool(
            snapshot.get("connection_status") == "connected"
            and snapshot.get("connection_provenance") == "api_connection"
            and snapshot.get("accounts")
        )
        configured = snapchat_oauth_configured()
        runtime_enabled = snapchat_native_sync_enabled()
        enabled = connected and configured and runtime_enabled
        if enabled:
            reason = None
        elif not connected:
            reason = "اربط Snapchat Marketing API واكتشف حسابًا إعلانيًا واحدًا على الأقل."
        elif not configured:
            reason = "إعدادات Snapchat Marketing API في Backend غير مكتملة."
        else:
            reason = "تشخيص تتبع Snapchat متوقف بمفتاح الأمان التشغيلي."
        actions["tracking_diagnostics"] = {
            "enabled": enabled,
            "reason": reason,
            "href": None,
        }
        return actions

    wrapped_actions._mezan_snapchat_native_tracking_actions = True  # type: ignore[attr-defined]
    service_module._actions = wrapped_actions


def _failure_detail(
    exc: SnapchatNativeSyncError,
    payload: SnapchatTrackingDiagnosticsInput,
) -> dict[str, Any]:
    result = exc.result or {}
    return {
        "run_id": getattr(exc, "run_id", None),
        "provider": SNAPCHAT_PROVIDER_ID,
        "status": "failed",
        "date_from": result.get("date_from"),
        "date_to": result.get("date_to"),
        "accounts_attempted": int(result.get("accounts_attempted") or 0),
        "accounts_complete": int(result.get("accounts_complete") or 0),
        "pixels_found": int(result.get("pixels_found") or 0),
        "pixels_complete": int(result.get("pixels_complete") or 0),
        "domains_observed": int(result.get("domains_observed") or 0),
        "diagnostics_saved": int(result.get("diagnostics_saved") or 0),
        "recommendations_count": int(result.get("recommendations_count") or 0),
        "errors_count": int(result.get("errors_count") or 1),
        "source_only": True,
        "provider_write_reached": False,
        "event_write_reached": False,
        "accounting_write_reached": False,
        "qoyod_write_reached": False,
        "code": exc.code,
        "message": exc.message,
        "retryable": exc.retryable,
        "requested_days": payload.days,
    }


async def _mark_needs_reauth(db: Any, user_id: str) -> None:
    checked_at = datetime.now(timezone.utc).isoformat()
    await db.mezan_integrations_v2.update_one(
        {"user_id": user_id, "provider": SNAPCHAT_PROVIDER_ID},
        {"$set": {
            "connection_status": "needs_reauth",
            "connection_provenance": "api_connection",
            "checked_at": checked_at,
            "updated_at": checked_at,
        }},
        upsert=True,
    )
    await db.mezan_integration_accounts_v2.update_many(
        {"user_id": user_id, "provider": SNAPCHAT_PROVIDER_ID},
        {"$set": {
            "connection_status": "needs_reauth",
            "last_observed_at": checked_at,
        }},
    )


def attach_snapchat_native_tracking_routes(
    router: APIRouter,
    db: Any,
    current_user: Callable,
    require_owner: Callable[[Any], dict],
) -> None:
    install_snapchat_tracking_error_detail_persistence()
    install_snapchat_native_tracking_actions()

    # The new reporting plane is attached beside the existing native routes.
    # It remains shadow-only and does not change any current Dashboard reader.
    from snapchat_v2.routes import attach_snapchat_v2_routes

    attach_snapchat_v2_routes(router, db, current_user, require_owner)

    @router.get(
        f"/{SNAPCHAT_PROVIDER_ID}/order-source-audit",
        name="get_snapchat_order_source_audit",
    )
    async def get_snapchat_order_source_audit(
        account_id: str | None = Query(default=None, max_length=120),
        from_date: str | None = Query(default=None),
        to_date: str | None = Query(default=None),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        owner = require_owner(user)
        try:
            return await build_snapchat_order_source_audit(
                db,
                str(owner["id"]),
                account_id=account_id,
                from_date=from_date,
                to_date=to_date,
            )
        except SnapchatNativeSyncError as exc:
            raise HTTPException(
                status_code=exc.status_code,
                detail={
                    "code": exc.code,
                    "message": exc.message,
                    "retryable": exc.retryable,
                    "source_only": True,
                    "provider_write_reached": False,
                    "campaign_write_reached": False,
                    "accounting_write_reached": False,
                    "qoyod_write_reached": False,
                },
            ) from exc

    @router.post(
        f"/{SNAPCHAT_PROVIDER_ID}/tracking-diagnostics",
        response_model=SnapchatTrackingDiagnosticsResponse,
        name="diagnose_snapchat_native_tracking",
    )
    async def diagnose_snapchat_native_tracking(
        payload: SnapchatTrackingDiagnosticsInput,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        owner = require_owner(user)
        owner_id = str(owner["id"])
        try:
            return await execute_snapchat_tracking_diagnostics(
                db,
                owner_id,
                payload,
            )
        except SnapchatNativeSyncError as exc:
            if exc.code == "snapchat_needs_reauth":
                await _mark_needs_reauth(db, owner_id)
            raise HTTPException(
                status_code=exc.status_code,
                detail=_failure_detail(exc, payload),
            ) from exc


__all__ = [
    "attach_snapchat_native_tracking_routes",
    "install_snapchat_native_tracking_actions",
]
