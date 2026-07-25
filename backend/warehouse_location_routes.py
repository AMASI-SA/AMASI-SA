"""Warehouse Location Engine — Sprint 2.1.

Creates deterministic warehouse/cabinet/bin locations from a compact structure
and keeps occupied locations immutable. All records are tenant-scoped.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from pymongo.errors import DuplicateKeyError

WAREHOUSES = "warehouse_locations_warehouses"
CABINETS = "warehouse_locations_cabinets"
LOCATIONS = "warehouse_locations"
EVENTS = "warehouse_location_events"

LocationPurpose = Literal[
    "permanent_storage",
    "temporary_staging",
    "returns",
    "damaged",
    "reserved",
]
LocationState = Literal[
    "empty",
    "reserved",
    "occupied",
    "partial_order",
    "ready_to_ship",
    "disabled",
]

_CODE_RE = re.compile(r"^[A-Z0-9][A-Z0-9_-]{0,19}$")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text(value: Any) -> str:
    return str(value or "").strip()


def _normalized_code(value: str) -> str:
    return _text(value).upper().replace(" ", "-")


def _can_manage(user: Any) -> bool:
    if not isinstance(user, dict):
        return False
    role = _text(user.get("role")).casefold()
    if role == "owner" or user.get("is_owner") is True:
        return True
    if "warehouse.manage" in set(user.get("denied_permissions") or []):
        return False
    return role in {"admin", "operations", "warehouse"} or (
        "warehouse.manage" in set(user.get("extra_permissions") or [])
    )


def _require_manager(user: Any) -> dict[str, Any]:
    if not _can_manage(user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "warehouse_permission_required", "message": "تحتاج صلاحية إدارة المستودع."},
        )
    return user


def _merchant_user_id(user: dict[str, Any]) -> str:
    if _text(user.get("role")).casefold() == "owner" or user.get("is_owner") is True:
        return _text(user.get("id"))
    owner_id = _text(user.get("created_by"))
    if not owner_id:
        raise HTTPException(
            status_code=409,
            detail={"code": "employee_store_not_linked", "message": "حساب الموظف غير مربوط بمالك المتجر."},
        )
    return owner_id


class WarehouseCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    code: str = Field(min_length=1, max_length=20)
    is_primary: bool = False

    @field_validator("code")
    @classmethod
    def validate_code(cls, value: str) -> str:
        value = _normalized_code(value)
        if not _CODE_RE.fullmatch(value):
            raise ValueError("warehouse code must contain A-Z, 0-9, _ or -")
        return value


class CabinetGenerate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    warehouse_id: str = Field(min_length=1, max_length=80)
    cabinet_code: str = Field(min_length=1, max_length=20)
    cabinet_name: str | None = Field(default=None, max_length=120)
    columns: int = Field(ge=1, le=100)
    rows: int = Field(ge=1, le=100)
    bins_per_row: int = Field(ge=1, le=100)
    purpose: LocationPurpose = "temporary_staging"
    max_items_per_bin: int | None = Field(default=None, ge=1, le=100000)

    @field_validator("cabinet_code")
    @classmethod
    def validate_code(cls, value: str) -> str:
        value = _normalized_code(value)
        if not _CODE_RE.fullmatch(value):
            raise ValueError("cabinet code must contain A-Z, 0-9, _ or -")
        return value


def location_code(cabinet_code: str, column: int, row: int, bin_number: int) -> str:
    """Stable human-readable code, e.g. A05-C01-R03-B04."""
    return f"{_normalized_code(cabinet_code)}-C{column:02d}-R{row:02d}-B{bin_number:02d}"


def generate_location_rows(payload: CabinetGenerate) -> list[dict[str, Any]]:
    total = payload.columns * payload.rows * payload.bins_per_row
    if total > 5000:
        raise ValueError("cabinet_location_limit_exceeded")
    rows: list[dict[str, Any]] = []
    for column in range(1, payload.columns + 1):
        for row in range(1, payload.rows + 1):
            for bin_number in range(1, payload.bins_per_row + 1):
                rows.append({
                    "code": location_code(payload.cabinet_code, column, row, bin_number),
                    "column": column,
                    "row": row,
                    "bin": bin_number,
                    "purpose": payload.purpose,
                    "state": "empty",
                    "max_items": payload.max_items_per_bin,
                })
    return rows


async def ensure_warehouse_location_indexes(db: Any) -> None:
    await db[WAREHOUSES].create_index([("user_id", 1), ("code", 1)], unique=True)
    await db[CABINETS].create_index([("user_id", 1), ("warehouse_id", 1), ("code", 1)], unique=True)
    await db[LOCATIONS].create_index([("user_id", 1), ("code", 1)], unique=True)
    await db[LOCATIONS].create_index([("user_id", 1), ("warehouse_id", 1), ("state", 1)])
    await db[EVENTS].create_index([("user_id", 1), ("occurred_at", -1)])


def make_warehouse_location_router(db: Any, current_user: Callable[..., Any]) -> APIRouter:
    router = APIRouter(prefix="/warehouse-locations", tags=["Warehouse Locations"])

    @router.post("/warehouses", status_code=201)
    async def create_warehouse(payload: WarehouseCreate, user: dict = Depends(current_user)) -> dict[str, Any]:
        actor = _require_manager(user)
        user_id = _merchant_user_id(actor)
        await ensure_warehouse_location_indexes(db)
        warehouse_id = str(uuid.uuid4())
        now = _now()
        doc = {
            "id": warehouse_id,
            "user_id": user_id,
            "name": payload.name.strip(),
            "code": payload.code,
            "is_primary": payload.is_primary,
            "status": "active",
            "created_at": now,
            "updated_at": now,
            "created_by": _text(actor.get("id")),
        }
        try:
            if payload.is_primary:
                await db[WAREHOUSES].update_many({"user_id": user_id}, {"$set": {"is_primary": False, "updated_at": now}})
            await db[WAREHOUSES].insert_one(doc)
        except DuplicateKeyError as exc:
            raise HTTPException(status_code=409, detail={"code": "warehouse_code_exists"}) from exc
        doc.pop("_id", None)
        return doc

    @router.get("/warehouses")
    async def list_warehouses(user: dict = Depends(current_user)) -> dict[str, Any]:
        actor = _require_manager(user)
        user_id = _merchant_user_id(actor)
        rows = await db[WAREHOUSES].find({"user_id": user_id}, {"_id": 0}).sort("created_at", 1).to_list(length=500)
        return {"items": rows, "total": len(rows)}

    @router.post("/cabinets/preview")
    async def preview_cabinet(payload: CabinetGenerate, user: dict = Depends(current_user)) -> dict[str, Any]:
        _require_manager(user)
        try:
            rows = generate_location_rows(payload)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail={"code": str(exc), "max_locations": 5000}) from exc
        return {
            "total_locations": len(rows),
            "first_code": rows[0]["code"],
            "last_code": rows[-1]["code"],
            "sample": [row["code"] for row in rows[:8]],
        }

    @router.post("/cabinets/generate", status_code=201)
    async def generate_cabinet(payload: CabinetGenerate, user: dict = Depends(current_user)) -> dict[str, Any]:
        actor = _require_manager(user)
        user_id = _merchant_user_id(actor)
        await ensure_warehouse_location_indexes(db)
        warehouse = await db[WAREHOUSES].find_one(
            {"id": payload.warehouse_id, "user_id": user_id, "status": "active"}, {"_id": 0}
        )
        if not warehouse:
            raise HTTPException(status_code=404, detail={"code": "warehouse_not_found"})
        try:
            generated = generate_location_rows(payload)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail={"code": str(exc), "max_locations": 5000}) from exc

        cabinet_id = str(uuid.uuid4())
        now = _now()
        cabinet = {
            "id": cabinet_id,
            "user_id": user_id,
            "warehouse_id": payload.warehouse_id,
            "code": payload.cabinet_code,
            "name": (payload.cabinet_name or payload.cabinet_code).strip(),
            "columns": payload.columns,
            "rows": payload.rows,
            "bins_per_row": payload.bins_per_row,
            "purpose": payload.purpose,
            "total_locations": len(generated),
            "status": "active",
            "created_at": now,
            "created_by": _text(actor.get("id")),
        }
        locations = [{
            **row,
            "id": str(uuid.uuid4()),
            "user_id": user_id,
            "warehouse_id": payload.warehouse_id,
            "cabinet_id": cabinet_id,
            "cabinet_code": payload.cabinet_code,
            "qr_value": f"MEZAN-LOCATION:{row['code']}",
            "occupancy": None,
            "created_at": now,
            "updated_at": now,
        } for row in generated]
        try:
            await db[CABINETS].insert_one(cabinet)
            await db[LOCATIONS].insert_many(locations, ordered=True)
        except DuplicateKeyError as exc:
            await db[CABINETS].delete_one({"id": cabinet_id, "user_id": user_id})
            await db[LOCATIONS].delete_many({"cabinet_id": cabinet_id, "user_id": user_id})
            raise HTTPException(status_code=409, detail={"code": "cabinet_or_location_code_exists"}) from exc

        await db[EVENTS].insert_one({
            "id": str(uuid.uuid4()), "user_id": user_id, "event_type": "cabinet_generated",
            "warehouse_id": payload.warehouse_id, "cabinet_id": cabinet_id,
            "locations_created": len(locations), "actor_id": _text(actor.get("id")), "occurred_at": now,
        })
        cabinet.pop("_id", None)
        return {"cabinet": cabinet, "locations_created": len(locations)}

    @router.get("/locations")
    async def list_locations(
        warehouse_id: str | None = None,
        cabinet_id: str | None = None,
        state_filter: LocationState | None = Query(default=None, alias="state"),
        limit: int = Query(default=250, ge=1, le=1000),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        actor = _require_manager(user)
        user_id = _merchant_user_id(actor)
        query: dict[str, Any] = {"user_id": user_id}
        if warehouse_id:
            query["warehouse_id"] = warehouse_id
        if cabinet_id:
            query["cabinet_id"] = cabinet_id
        if state_filter:
            query["state"] = state_filter
        items = await db[LOCATIONS].find(query, {"_id": 0}).sort([("cabinet_code", 1), ("column", 1), ("row", 1), ("bin", 1)]).to_list(length=limit)
        totals_pipeline = [
            {"$match": query},
            {"$group": {"_id": "$state", "count": {"$sum": 1}}},
        ]
        grouped = await db[LOCATIONS].aggregate(totals_pipeline).to_list(length=20)
        return {"items": items, "returned": len(items), "counts_by_state": {row["_id"]: row["count"] for row in grouped}}

    @router.post("/locations/{location_id}/disable")
    async def disable_location(location_id: str, user: dict = Depends(current_user)) -> dict[str, Any]:
        actor = _require_manager(user)
        user_id = _merchant_user_id(actor)
        location = await db[LOCATIONS].find_one({"id": location_id, "user_id": user_id}, {"_id": 0})
        if not location:
            raise HTTPException(status_code=404, detail={"code": "location_not_found"})
        if location.get("state") != "empty" or location.get("occupancy"):
            raise HTTPException(status_code=409, detail={"code": "occupied_location_cannot_be_disabled", "message": "يجب تفريغ الخانة قبل تعطيلها."})
        now = _now()
        await db[LOCATIONS].update_one(
            {"id": location_id, "user_id": user_id, "state": "empty"},
            {"$set": {"state": "disabled", "updated_at": now, "updated_by": _text(actor.get("id"))}},
        )
        return {"ok": True, "location_id": location_id, "state": "disabled"}

    return router
