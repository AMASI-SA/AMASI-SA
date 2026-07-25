"""Warehouse Location Engine — Sprint 2.1.

A merchant creates a warehouse once with its geographic address, then creates
any number of cabinets under it. Each cabinet is entered as length × width;
Mezan generates numbered locations and stable QR payloads automatically.
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
    country: str = Field(min_length=1, max_length=80)
    city: str = Field(min_length=1, max_length=80)
    district: str = Field(min_length=1, max_length=120)
    street: str = Field(min_length=1, max_length=180)
    warehouse_number: str = Field(min_length=1, max_length=40)
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
    length: int = Field(ge=1, le=100, description="Vertical grid size")
    width: int = Field(ge=1, le=100, description="Horizontal grid size")
    purpose: LocationPurpose = "temporary_staging"
    max_items_per_location: int | None = Field(default=None, ge=1, le=100000)

    @field_validator("cabinet_code")
    @classmethod
    def validate_code(cls, value: str) -> str:
        value = _normalized_code(value)
        if not _CODE_RE.fullmatch(value):
            raise ValueError("cabinet code must contain A-Z, 0-9, _ or -")
        return value


def location_code(warehouse_code: str, cabinet_code: str, number: int) -> str:
    """Stable compact code, e.g. WH01-A05-024."""
    return f"{_normalized_code(warehouse_code)}-{_normalized_code(cabinet_code)}-{number:03d}"


def generate_location_rows(payload: CabinetGenerate, *, warehouse_code: str = "WH") -> list[dict[str, Any]]:
    total = payload.length * payload.width
    if total > 5000:
        raise ValueError("cabinet_location_limit_exceeded")

    locations: list[dict[str, Any]] = []
    number = 0
    for row in range(1, payload.length + 1):
        for column in range(1, payload.width + 1):
            number += 1
            locations.append({
                "code": location_code(warehouse_code, payload.cabinet_code, number),
                "number": number,
                "row": row,
                "column": column,
                "grid_y": row,
                "grid_x": column,
                "purpose": payload.purpose,
                "state": "empty",
                "max_items": payload.max_items_per_location,
            })
    return locations


async def ensure_warehouse_location_indexes(db: Any) -> None:
    await db[WAREHOUSES].create_index([("user_id", 1), ("code", 1)], unique=True)
    await db[CABINETS].create_index([("user_id", 1), ("warehouse_id", 1), ("code", 1)], unique=True)
    await db[LOCATIONS].create_index([("user_id", 1), ("code", 1)], unique=True)
    await db[LOCATIONS].create_index([("user_id", 1), ("warehouse_id", 1), ("state", 1)])
    await db[LOCATIONS].create_index([("user_id", 1), ("cabinet_id", 1), ("number", 1)], unique=True)
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
            "country": payload.country.strip(),
            "city": payload.city.strip(),
            "district": payload.district.strip(),
            "street": payload.street.strip(),
            "warehouse_number": payload.warehouse_number.strip(),
            "address_label": " > ".join([
                payload.country.strip(), payload.city.strip(), payload.district.strip(),
                payload.street.strip(), payload.warehouse_number.strip(),
            ]),
            "is_primary": payload.is_primary,
            "status": "active",
            "created_at": now,
            "updated_at": now,
            "created_by": _text(actor.get("id")),
        }
        try:
            if payload.is_primary:
                await db[WAREHOUSES].update_many(
                    {"user_id": user_id}, {"$set": {"is_primary": False, "updated_at": now}}
                )
            await db[WAREHOUSES].insert_one(doc)
        except DuplicateKeyError as exc:
            raise HTTPException(status_code=409, detail={"code": "warehouse_code_exists"}) from exc
        doc.pop("_id", None)
        return doc

    @router.get("/warehouses")
    async def list_warehouses(user: dict = Depends(current_user)) -> dict[str, Any]:
        actor = _require_manager(user)
        user_id = _merchant_user_id(actor)
        rows = await db[WAREHOUSES].find(
            {"user_id": user_id}, {"_id": 0}
        ).sort("created_at", 1).to_list(length=500)
        return {"items": rows, "total": len(rows)}

    @router.get("/warehouses/{warehouse_id}")
    async def get_warehouse(warehouse_id: str, user: dict = Depends(current_user)) -> dict[str, Any]:
        actor = _require_manager(user)
        user_id = _merchant_user_id(actor)
        warehouse = await db[WAREHOUSES].find_one(
            {"id": warehouse_id, "user_id": user_id}, {"_id": 0}
        )
        if not warehouse:
            raise HTTPException(status_code=404, detail={"code": "warehouse_not_found"})
        cabinets = await db[CABINETS].find(
            {"warehouse_id": warehouse_id, "user_id": user_id}, {"_id": 0}
        ).sort("created_at", 1).to_list(length=1000)
        return {"warehouse": warehouse, "cabinets": cabinets, "cabinet_count": len(cabinets)}

    @router.post("/cabinets/preview")
    async def preview_cabinet(payload: CabinetGenerate, user: dict = Depends(current_user)) -> dict[str, Any]:
        actor = _require_manager(user)
        user_id = _merchant_user_id(actor)
        warehouse = await db[WAREHOUSES].find_one(
            {"id": payload.warehouse_id, "user_id": user_id, "status": "active"}, {"_id": 0}
        )
        if not warehouse:
            raise HTTPException(status_code=404, detail={"code": "warehouse_not_found"})
        try:
            rows = generate_location_rows(payload, warehouse_code=warehouse["code"])
        except ValueError as exc:
            raise HTTPException(status_code=422, detail={"code": str(exc), "max_locations": 5000}) from exc
        return {
            "length": payload.length,
            "width": payload.width,
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
            generated = generate_location_rows(payload, warehouse_code=warehouse["code"])
        except ValueError as exc:
            raise HTTPException(status_code=422, detail={"code": str(exc), "max_locations": 5000}) from exc

        cabinet_id = str(uuid.uuid4())
        now = _now()
        cabinet = {
            "id": cabinet_id,
            "user_id": user_id,
            "warehouse_id": payload.warehouse_id,
            "warehouse_code": warehouse["code"],
            "code": payload.cabinet_code,
            "name": (payload.cabinet_name or payload.cabinet_code).strip(),
            "length": payload.length,
            "width": payload.width,
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
            "warehouse_code": warehouse["code"],
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

    @router.get("/cabinets")
    async def list_cabinets(
        warehouse_id: str = Query(min_length=1),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        actor = _require_manager(user)
        user_id = _merchant_user_id(actor)
        items = await db[CABINETS].find(
            {"user_id": user_id, "warehouse_id": warehouse_id}, {"_id": 0}
        ).sort("created_at", 1).to_list(length=1000)
        return {"items": items, "total": len(items)}

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
        items = await db[LOCATIONS].find(query, {"_id": 0}).sort(
            [("warehouse_code", 1), ("cabinet_code", 1), ("number", 1)]
        ).to_list(length=limit)
        grouped = await db[LOCATIONS].aggregate([
            {"$match": query},
            {"$group": {"_id": "$state", "count": {"$sum": 1}}},
        ]).to_list(length=20)
        return {
            "items": items,
            "returned": len(items),
            "counts_by_state": {row["_id"]: row["count"] for row in grouped},
        }

    @router.post("/locations/{location_id}/disable")
    async def disable_location(location_id: str, user: dict = Depends(current_user)) -> dict[str, Any]:
        actor = _require_manager(user)
        user_id = _merchant_user_id(actor)
        location = await db[LOCATIONS].find_one({"id": location_id, "user_id": user_id}, {"_id": 0})
        if not location:
            raise HTTPException(status_code=404, detail={"code": "location_not_found"})
        if location.get("state") != "empty" or location.get("occupancy"):
            raise HTTPException(
                status_code=409,
                detail={"code": "occupied_location_cannot_be_disabled", "message": "يجب تفريغ الخانة قبل تعطيلها."},
            )
        now = _now()
        await db[LOCATIONS].update_one(
            {"id": location_id, "user_id": user_id, "state": "empty"},
            {"$set": {"state": "disabled", "updated_at": now, "updated_by": _text(actor.get("id"))}},
        )
        return {"ok": True, "location_id": location_id, "state": "disabled"}

    return router
