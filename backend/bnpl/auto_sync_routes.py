"""HTTP routes for BNPL auto-sync (status + manual trigger)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request

from .auto_sync_service import (
    get_auto_sync_status,
    run_auto_sync_for_user,
    SYNC_INTERVAL_SECONDS,
)


def attach_bnpl_auto_sync_routes(parent_router, *, db, get_current_user):
    """Mount auto-sync routes onto `parent_router`."""
    router = APIRouter(prefix="/bnpl/auto-sync", tags=["BNPL Auto-Sync"])

    @router.get("/status")
    async def auto_sync_status(user: dict = Depends(get_current_user)):
        try:
            payload = await get_auto_sync_status(db, user["id"])
            payload["interval_seconds"] = SYNC_INTERVAL_SECONDS
            return {"success": True, **payload}
        except Exception as e:  # noqa: BLE001
            return {"success": False, "error": f"{type(e).__name__}: {e}"}

    @router.post("/run-now")
    async def run_now(user: dict = Depends(get_current_user)):
        """Trigger an immediate incremental sync for the current user.
        Wrapped in try/except so we always return JSON (never raw HTML
        traceback that Cloudflare would convert to a 524 parse error)."""
        try:
            return {"success": True, "run": await run_auto_sync_for_user(db, user["id"])}
        except Exception as e:  # noqa: BLE001
            return {"success": False, "error": f"{type(e).__name__}: {e}"}

    parent_router.include_router(router)
