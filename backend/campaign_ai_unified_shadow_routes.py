"""Owner-only read endpoint for the Campaign AI Unified V2 Shadow proof."""
from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter, Depends, Query

from campaign_ai_unified_shadow import build_campaign_ai_unified_shadow


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
        return await build_campaign_ai_unified_shadow(
            db,
            user_id,
            days=days,
        )


__all__ = ["attach_campaign_ai_unified_shadow_routes"]
