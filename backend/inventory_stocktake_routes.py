"""Authenticated native inventory count and variance endpoints."""
from typing import Literal
from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
import inventory_stocktake_service as service


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, str_strip_whitespace=True)


class Command(Input):
    request_id: str = Field(min_length=8, max_length=120, pattern=r"^[A-Za-z0-9_.:-]+$")


class CreateCount(Command):
    title: str = Field(min_length=1, max_length=160)
    warehouse_ids: list[str] = Field(default_factory=list, max_length=500)
    location_ids: list[str] = Field(default_factory=list, max_length=500)
    row_ids: list[str] = Field(default_factory=list, max_length=500)


class CountLine(Input):
    id: str = Field(min_length=1, max_length=160)
    counted_quantity: str | None = Field(None, max_length=40)
    unit: str = Field(min_length=1, max_length=80)
    reason: str = Field("", max_length=500)


class SaveCount(Command):
    expected_revision: int = Field(ge=1, strict=True)
    title: str = Field(min_length=1, max_length=160)
    lines: list[CountLine] = Field(max_length=500)


class CountAction(Command):
    expected_revision: int = Field(ge=1, strict=True)
    action: Literal["submit", "approve", "reject", "return", "refresh_snapshot"]
    reason: str = Field("", max_length=1000)
    inventory_account_id: str | None = Field(None, max_length=160)
    variance_account_code: str | None = Field(None, max_length=160)


class PostCount(Input):
    operation_id: str = Field(min_length=8, max_length=160)
    expected_revision: int = Field(ge=1, strict=True)


def make_inventory_stocktake_router(db, current_user):
    router = APIRouter(prefix="/inventory-v2/stocktakes", tags=["native-inventory-counts"])

    @router.get("/context")
    async def context(user=Depends(current_user)):
        return await service.context_for(db, user)

    @router.get("/movements")
    async def movements(user=Depends(current_user)):
        return await service.movements(db, user)

    @router.get("")
    @router.get("/", include_in_schema=False)
    async def counts(user=Depends(current_user)):
        return await service.list_counts(db, user)

    @router.post("")
    @router.post("/", include_in_schema=False)
    async def create(payload: CreateCount, user=Depends(current_user)):
        return await service.create_count(db, user, payload.model_dump())

    @router.get("/{count_id}")
    async def detail(count_id: str, user=Depends(current_user)):
        return await service.get_count(db, user, count_id)

    @router.put("/{count_id}")
    async def save(count_id: str, payload: SaveCount, user=Depends(current_user)):
        return await service.save_count(db, user, count_id, payload.model_dump())

    @router.post("/{count_id}/actions")
    async def action(count_id: str, payload: CountAction, user=Depends(current_user)):
        return await service.act_on_count(db, user, count_id, payload.model_dump())

    @router.post("/{count_id}/post")
    async def post(count_id: str, payload: PostCount, user=Depends(current_user)):
        return await service.post_count(db, user, count_id, payload.model_dump())

    return router
