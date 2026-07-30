"""Owner-only native Snapchat data-plane route and card action wiring."""
from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException

from .models import SnapchatAnalyticsSyncResponse
from .snapchat_native_async_routes import (
    attach_snapchat_native_async_routes,
)
from .snapchat_native_data_sync import (
    SnapchatNativeSyncError,
    SnapchatNativeSyncInput,
    execute_snapchat_native_sync,
    snapchat_native_sync_enabled,
)
from .snapchat_native_selected_reads import (
    attach_snapchat_native_selected_read_routes,
)
from .snapchat_oauth_security import (
    SNAPCHAT_PROVIDER_ID,
    snapchat_oauth_configured,
)


def install_snapchat_native_data_actions() -> None:
    """Enable the bounded sync action only for a proven native API connection."""
    from . import service as service_module

    original_actions = service_module._actions
    if getattr(original_actions, "_mezan_snapchat_native_data_actions", False):
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
        runtime_enabled = snapchat_native_sync_enabled()
        configured = snapchat_oauth_configured()
        enabled = connected and runtime_enabled and configured
        if enabled:
            reason = None
        elif not connected:
            reason = (
                "اربط Snapchat Marketing API واكتشف حسابًا "
                "إعلانيًا واحدًا على الأقل."
            )
        elif not configured:
            reason = (
                "إعدادات Snapchat Marketing API في Backend غير مكتملة."
            )
        else:
            reason = (
                "مزامنة Snapchat الأصلية متوقفة بمفتاح الأمان التشغيلي."
            )
        actions["sync_data"] = {
            "enabled": enabled,
            "reason": reason,
            "href": None,
        }
        return actions

    wrapped_actions._mezan_snapchat_native_data_actions = True  # type: ignore[attr-defined]
    service_module._actions = wrapped_actions


def _failure_detail(
    exc: SnapchatNativeSyncError,
    payload: SnapchatNativeSyncInput,
) -> dict[str, Any]:
    result = exc.result or {}
    return {
        "run_id": getattr(exc, "run_id", None),
        "provider": SNAPCHAT_PROVIDER_ID,
        "status": "failed",
        "date_from": result.get("date_from") or payload.from_date,
        "date_to": result.get("date_to") or payload.to_date,
        "accounts_attempted": int(result.get("accounts_synced") or 0),
        "accounts_complete": int(result.get("accounts_complete") or 0),
        "rows_saved": int(result.get("rows_saved") or 0),
        "errors_count": int(result.get("errors_count") or 1),
        "source_only": True,
        "accounting_write_reached": False,
        "qoyod_write_reached": False,
        "code": exc.code,
        "message": exc.message,
        "retryable": exc.retryable,
    }


def attach_snapchat_native_data_routes(
    router: APIRouter,
    db: Any,
    current_user: Callable,
    require_owner: Callable[[Any], dict],
) -> None:
    install_snapchat_native_data_actions()
    attach_snapchat_native_async_routes(
        router, db, current_user, require_owner
    )
    attach_snapchat_native_selected_read_routes(
        router, db, current_user, require_owner
    )

    @router.post(
        f"/{SNAPCHAT_PROVIDER_ID}/sync",
        response_model=SnapchatAnalyticsSyncResponse,
        name="sync_snapchat_native_data",
    )
    async def sync_snapchat_native_data(
        payload: SnapchatNativeSyncInput,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        owner = require_owner(user)
        try:
            return await execute_snapchat_native_sync(
                db,
                str(owner["id"]),
                payload,
            )
        except SnapchatNativeSyncError as exc:
            raise HTTPException(
                status_code=exc.status_code,
                detail=_failure_detail(exc, payload),
            ) from exc


__all__ = [
    "attach_snapchat_native_data_routes",
    "install_snapchat_native_data_actions",
]
