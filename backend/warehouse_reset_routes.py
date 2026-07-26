"""Owner-only reset endpoint for Mezan OS warehouse test data.

This endpoint is intentionally explicit and scoped to the current merchant. It
removes branches, sections, cabinets, locations, warehouse events, and their
numbering counters so the merchant can restart the warehouse setup from 1.
"""
from __future__ import annotations

import re
from typing import Any, Callable, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict

from warehouse_location_routes import CABINETS, EVENTS, LOCATIONS, WAREHOUSES, _merchant_user_id, _text
from warehouse_location_v2_routes import COUNTERS
from warehouse_room_routes import ROOMS


class WarehouseResetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmation: Literal["حذف"]


def _require_owner(user: Any) -> dict[str, Any]:
    if not isinstance(user, dict):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail={"code": "owner_required"})
    role = _text(user.get("role")).casefold()
    if role == "owner" or user.get("is_owner") is True:
        return user
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={"code": "owner_required", "message": "إعادة تهيئة الفروع والمخازن متاحة لمالك المتجر فقط."},
    )


def _counter_scope_filter(user_id: str) -> dict[str, Any]:
    escaped = re.escape(user_id)
    return {
        "$or": [
            {"user_id": user_id},
            {"key": {"$regex": rf"(^|:){escaped}(:|$)"}},
        ]
    }


async def reset_warehouse_data(db: Any, *, user_id: str) -> dict[str, int]:
    """Delete only the current merchant's warehouse workspace and counters."""

    deleted: dict[str, int] = {}
    for key, collection, query in [
        ("locations", LOCATIONS, {"user_id": user_id}),
        ("cabinets", CABINETS, {"user_id": user_id}),
        ("sections", ROOMS, {"user_id": user_id}),
        ("branches", WAREHOUSES, {"user_id": user_id}),
        ("events", EVENTS, {"user_id": user_id}),
        ("counters", COUNTERS, _counter_scope_filter(user_id)),
    ]:
        result = await db[collection].delete_many(query)
        deleted[key] = int(getattr(result, "deleted_count", 0) or 0)
    return deleted


def make_warehouse_reset_router(db: Any, current_user: Callable[..., Any]) -> APIRouter:
    router = APIRouter(prefix="/warehouse-locations-v2", tags=["Mezan OS V2 Warehouse Reset"])

    @router.post("/reset", status_code=status.HTTP_200_OK)
    async def reset_workspace(
        payload: WarehouseResetRequest,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        del payload  # Pydantic already verified the exact confirmation word.
        actor = _require_owner(user)
        user_id = _merchant_user_id(actor)
        deleted = await reset_warehouse_data(db, user_id=user_id)
        return {
            "ok": True,
            "reset": True,
            "deleted": deleted,
            "message": "تم حذف بيانات الفروع والأقسام والدواليب والخانات وإعادة الترقيم من 1.",
        }

    return router
