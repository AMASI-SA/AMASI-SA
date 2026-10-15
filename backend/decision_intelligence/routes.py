"""Owner-only, read-only routes for Decision Intelligence Phase 5."""
from __future__ import annotations

from datetime import date
from typing import Any, Callable

from fastapi import APIRouter, Depends, Query

from .phase5 import run_phase5_shadow


def attach_decision_intelligence_phase5_routes(
    router: APIRouter,
    db: Any,
    current_user: Callable,
    require_owner: Callable[[Any], dict],
) -> None:
    @router.get("/decision-intelligence/phase5/shadow")
    async def decision_intelligence_phase5_shadow(
        date_from: date = Query(...),
        date_to: date = Query(...),
        provider: str = Query(default="snapchat_ads", min_length=1, max_length=80),
        max_freshness_hours: float = Query(default=36.0, ge=1.0, le=168.0),
        max_candidates: int = Query(default=25, ge=1, le=100),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        owner = require_owner(user)
        user_id = str(owner.get("id") or owner.get("_id") or "")
        return await run_phase5_shadow(
            db,
            user_id,
            provider=provider,
            date_from=date_from,
            date_to=date_to,
            max_freshness_hours=max_freshness_hours,
            max_candidates=max_candidates,
        )


__all__ = ["attach_decision_intelligence_phase5_routes"]
