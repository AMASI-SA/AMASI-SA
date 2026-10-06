"""Operational EDIT_OPTIONS evidence application; commercial fields are forbidden."""
from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from fulfillment_v2_routes import _actor_context
import salla_edit_application as service

class UnitRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    order_item_id: str = Field(min_length=1, max_length=180)
    unit_index: int = Field(ge=1, strict=True)
    generation: int = Field(ge=0, strict=True)
    revision: int = Field(ge=0, strict=True)
    change_id: str | None

class ApplyEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_id: str = Field(min_length=1, max_length=180)
    employee_id: str = Field(min_length=1, max_length=180)
    reason: str = Field(min_length=3, max_length=1000)
    idempotency_key: str = Field(min_length=8, max_length=180)
    expected_revision: int = Field(ge=0, strict=True)
    expected_generation: str = Field(min_length=64, max_length=64)
    units: list[UnitRef] = Field(min_length=1, max_length=250)


def make_salla_edit_router(db, current_user):
    router = APIRouter(prefix="/order-change-edit-v1", tags=["Salla EDIT fulfillment"])
    async def actor(user):
        return {**await _actor_context(db, user), "actor_name": str(user.get("name") or "")}
    @router.get("/orders/{order_number}/pending")
    async def pending(order_number: str, user=Depends(current_user)):
        context = await actor(user)
        return await service.pending_edits(db, user_id=context["merchant_id"], order_number=order_number, context=context)
    @router.post("/orders/{order_number}/apply")
    async def apply(order_number: str, payload: ApplyEdit, user=Depends(current_user)):
        context = await actor(user)
        return await service.apply_edit(db, user_id=context["merchant_id"], order_number=order_number,
                                        context=context, payload=payload.model_dump())
    return router
