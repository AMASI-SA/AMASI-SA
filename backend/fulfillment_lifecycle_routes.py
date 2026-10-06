"""Shared Web/mobile PR1 contract. Commercial capabilities are always disabled."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal

import fulfillment_lifecycle as service
from fulfillment_v2_routes import _actor_context


class ControlRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    idempotency_key: str = Field(min_length=8, max_length=180)
    expected_revision: int = Field(ge=0, strict=True)
    expected_generation: str = Field(min_length=64, max_length=64)
    reason: str = Field(min_length=3, max_length=1000)


class HoldRequest(ControlRequest):
    scope: Literal["order", "item", "piece"] = "order"
    target_id: str | None = Field(default=None, max_length=180)
    stop_type: Literal["cancel", "edit", "note", "employee"] = "note"


def make_fulfillment_lifecycle_router(db, current_user):
    router = APIRouter(prefix="/order-change-controls-v1", tags=["Order Change Controls"])

    async def actor(user):
        context = await _actor_context(db, user)
        if not (context.get("is_owner") or
                {service.MANAGE, service.SELF_STOP} & set(context.get("permissions", set()))):
            raise HTTPException(403, detail={"code": "fulfillment_stop_permission_required"})
        return {**context, "actor_name": str(user.get("name") or "")}

    @router.get("/orders/{order_number}/capabilities")
    async def capabilities(order_number: str, user=Depends(current_user)):
        context = await actor(user)
        return await service.capabilities(db, user_id=context["merchant_id"], order_number=order_number, context=context)

    @router.post("/orders/{order_number}/holds")
    async def hold(order_number: str, payload: HoldRequest, user=Depends(current_user)):
        context = await actor(user)
        return await service.create_hold(db, user_id=context["merchant_id"], order_number=order_number,
                                         context=context, payload=payload.model_dump())

    @router.post("/holds/{hold_id}/resume")
    async def resume(hold_id: str, payload: ControlRequest, user=Depends(current_user)):
        context = await actor(user)
        return await service.resume_hold(db, user_id=context["merchant_id"], hold_id=hold_id,
                                         context=context, payload=payload.model_dump())

    @router.get("/orders/{order_number}/audit")
    async def audit(order_number: str, user=Depends(current_user)):
        context = await actor(user)
        if not (context.get("is_owner") or service.MANAGE in context.get("permissions", set())):
            raise HTTPException(403, detail={"code": "fulfillment_stop_manage_permission_required"})
        rows = await db[service.AUDIT].find({"user_id": context["merchant_id"], "order_number": order_number},
                                          {"_id": 0}).sort("occurred_at", -1).limit(100).to_list(100)
        return {"events": rows, "limit": 100}
    return router
