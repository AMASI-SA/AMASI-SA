"""Owner-gated Mezan endpoint for confirmed assisted Salla order creation.

No direct model/provider access. Requires a pre-existing trusted, fresh Mezan
quote and Chatwoot customer confirmation record. The required proof-writer
integration is a separate task; without it this endpoint cannot create orders.
"""

from __future__ import annotations

import os
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from salla_integration.service import call_salla

from .payload import PendingOrder
from .service import CreationDenied, create_confirmed_salla_order


class AssistedCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent_id: str = Field(min_length=8, max_length=100, pattern=r"^[a-zA-Z0-9_-]+$")
    inbox_id: str = Field(min_length=1, max_length=80)
    conversation_id: str = Field(min_length=1, max_length=80)
    order: PendingOrder


def make_assisted_salla_order_router(
    db: Any, current_user: Callable,
) -> APIRouter:
    router = APIRouter(prefix="/orders-v2/assisted-salla", tags=["assisted-orders"])

    @router.post("/create-confirmed")
    async def create_confirmed(
        request: AssistedCreateRequest,
        user: dict = Depends(current_user),
    ) -> dict:
        # This owner-only preview does not grant chat agents owner credentials.
        # Later trusted service-to-service access must go through Action Gateway.
        role = str((user or {}).get("role") or "").strip().lower()
        if not user or (role != "owner" and user.get("is_owner") is not True):
            raise HTTPException(status_code=403, detail={"code": "owner_only"})
        try:
            return await create_confirmed_salla_order(
                db,
                user_id=str(user["id"]),
                actor_id=str(user["id"]),
                intent_id=request.intent_id,
                inbox_id=request.inbox_id,
                conversation_id=request.conversation_id,
                order=request.order,
                post_salla=call_salla,
                enabled=(
                    os.environ.get("MEZAN_ASSISTED_SALLA_ORDER_POST_ENABLED", "0") == "1"
                ),
            )
        except CreationDenied as exc:
            raise HTTPException(
                status_code=exc.status_code, detail={"code": exc.code}
            ) from exc

    return router
