"""Owner-only cached reads and explicit durable monitoring/comparison requests."""
from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter, Depends, Query

from campaign_ai_shadow_jobs import read_status, request_refresh


def attach_campaign_ai_unified_shadow_routes(
    router: APIRouter,
    db: Any,
    current_user: Callable,
    require_owner: Callable[[Any], dict],
) -> None:
    @router.get("/ai-monitor/unified-shadow")
    async def campaign_ai_unified_shadow(
        days: int = Query(default=1, ge=1, le=14),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        owner = require_owner(user)
        user_id = str(owner.get("id") or owner.get("_id") or "")
        return await read_status(db, user_id, days=days)

    @router.post("/ai-monitor/unified-shadow/refresh", status_code=202)
    async def refresh_campaign_ai_unified_shadow(
        days: int = Query(default=1, ge=1, le=14),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        owner = require_owner(user)
        user_id = str(owner.get("id") or owner.get("_id") or "")
        return await request_refresh(db, user_id, days=days)

    @router.get("/ai-monitor/manual")
    async def manual_campaign_ai_status(user: dict = Depends(current_user)) -> dict[str, Any]:
        owner = require_owner(user)
        user_id = str(owner.get("id") or owner.get("_id") or "")
        return await read_status(db, user_id, days=3, kind="monitor")

    @router.post("/ai-monitor/manual/refresh", status_code=202)
    async def refresh_manual_campaign_ai(user: dict = Depends(current_user)) -> dict[str, Any]:
        owner = require_owner(user)
        user_id = str(owner.get("id") or owner.get("_id") or "")
        return await request_refresh(db, user_id, days=3, kind="monitor")


__all__ = ["attach_campaign_ai_unified_shadow_routes"]
