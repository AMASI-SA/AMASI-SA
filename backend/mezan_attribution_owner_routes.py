"""Owner-only Production-safe controls for Mezan attribution ledger refresh.

These routes intentionally operate only on Mezan's internal Mongo database.
They never call Salla, ad providers, Qoyod, product catalogs, pricing, or stock
writes. Backfill is idempotent because the attribution ledger upserts by
``(user_id, order_key)``.
"""
from __future__ import annotations

from typing import Any, Callable, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field

from mezan_attribution_order_ledger import LEDGER_COLLECTION
from mezan_attribution_profit_bridge import (
    build_attribution_profit_bridge,
    refresh_existing_orders_to_ledger,
)
from warehouse_location_routes import _merchant_user_id, _text


class AttributionBackfillRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirmation: Literal["تشغيل"]
    limit: int = Field(default=5000, ge=1, le=50_000)


def require_owner(user: Any) -> dict[str, Any]:
    if not isinstance(user, dict):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "owner_required"},
        )
    role = _text(user.get("role")).casefold()
    if role == "owner" or user.get("is_owner") is True:
        return user
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={
            "code": "owner_required",
            "message": "تحديث دفتر الإسناد متاح لمالك المتجر فقط.",
        },
    )


async def build_backfill_preview(db: Any, *, user_id: str) -> dict[str, Any]:
    """Return counts only; never writes or calls external systems."""
    unified = int(await db.unified_orders.count_documents({"user_id": user_id}))
    ledger = int(await db[LEDGER_COLLECTION].count_documents({"user_id": user_id}))
    return {
        "unified_orders": unified,
        "ledger_rows": ledger,
        "estimated_missing_rows": max(0, unified - ledger),
        "external_writes": False,
        "read_only": True,
    }


def make_mezan_attribution_owner_router(
    db: Any,
    current_user: Callable[..., Any],
) -> APIRouter:
    router = APIRouter(
        prefix="/mezan-attribution-v1",
        tags=["Mezan Attribution Owner Controls"],
    )

    @router.get("/backfill-preview")
    async def backfill_preview(user: dict = Depends(current_user)) -> dict[str, Any]:
        actor = require_owner(user)
        user_id = _merchant_user_id(actor)
        preview = await build_backfill_preview(db, user_id=user_id)
        return {"ok": True, "user_id": user_id, **preview}

    @router.post("/backfill")
    async def run_backfill(
        payload: AttributionBackfillRequest,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        actor = require_owner(user)
        user_id = _merchant_user_id(actor)
        result = await refresh_existing_orders_to_ledger(
            db,
            user_id,
            limit=payload.limit,
        )
        return {
            "ok": True,
            "user_id": user_id,
            "backfill": result,
            "external_writes": False,
        }

    @router.get("/report")
    async def attribution_profit_report(
        from_date: str = Query(..., pattern=r"^\d{4}-\d{2}-\d{2}$"),
        to_date: str = Query(..., pattern=r"^\d{4}-\d{2}-\d{2}$"),
        limit: int = Query(default=20_000, ge=1, le=50_000),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        actor = require_owner(user)
        user_id = _merchant_user_id(actor)
        report = await build_attribution_profit_bridge(
            db,
            user_id,
            from_date=from_date,
            to_date=to_date,
            limit=limit,
        )
        return {
            "ok": True,
            "user_id": user_id,
            "report": report,
            "external_writes": False,
        }

    return router


__all__ = [
    "AttributionBackfillRequest",
    "build_backfill_preview",
    "make_mezan_attribution_owner_router",
    "require_owner",
]
