"""Optional room/section layer for Mezan OS V2 warehouse buildings.

A building may contain cabinets directly, rooms only, or both. Existing
warehouse and cabinet records remain valid; room_id is additive and optional.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from warehouse_location_routes import (
    CABINETS,
    EVENTS,
    LOCATIONS,
    WAREHOUSES,
    LocationPurpose,
    _merchant_user_id,
    _require_manager,
    _text,
    generate_location_rows,
    CabinetGenerate,
)

ROOMS = "warehouse_rooms"
COUNTERS = "warehouse_location_counters"

RoomType = Literal[
    "storage",
    "installation_engraving",
    "shipping_labeling",
    "packing",
    "returns",
    "raw_materials",
    "ready_to_ship",
    "worker_housing",
    "office",
    "other",
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class RoomCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    warehouse_id: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=120)
    room_type: RoomType = "storage"
    allows_cabinets: bool = True
    notes: str | None = Field(default=None, max_length=500)


class RoomCabinetCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cabinet_name: str | None = Field(default=None, max_length=120)
    length: int = Field(ge=1, le=100)
    width: int = Field(ge=1, le=100)
    purpose: LocationPurpose = "permanent_storage"
    max_items_per_location: int | None = Field(default=None, ge=1, le=100000)


async def _next_number(db: Any, key: str) -> int:
    row = await db[COUNTERS].find_one_and_update(
        {"key": key},
        {"$inc": {"value": 1}, "$setOnInsert": {"created_at": _now()}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return int(row["value"])


async def ensure_room_indexes(db: Any) -> None:
    await db[ROOMS].create_index(
        [("user_id", 1), ("warehouse_id", 1), ("room_number", 1)],
        unique=True,
    )
    await db[CABINETS].create_index(
        [("user_id", 1), ("warehouse_id", 1), ("room_id", 1), ("cabinet_number", 1)],
        unique=True,
        partialFilterExpression={"room_id": {"$exists": True}},
        name="uq_room_cabinet_number_v2",
    )


def make_warehouse_room_router(db: Any, current_user: Callable[..., Any]) -> APIRouter:
    router = APIRouter(prefix="/warehouse-locations-v2", tags=["Mezan OS V2 Warehouse Rooms"])

    @router.post("/rooms", status_code=status.HTTP_201_CREATED)
    async def create_room(payload: RoomCreate, user: dict = Depends(current_user)) -> dict[str, Any]:
        actor = _require_manager(user)
        user_id = _merchant_user_id(actor)
        warehouse = await db[WAREHOUSES].find_one(
            {"id": payload.warehouse_id, "user_id": user_id, "status": "active"},
            {"_id": 0},
        )
        if not warehouse:
            raise HTTPException(status_code=404, detail={"code": "warehouse_not_found"})

        await ensure_room_indexes(db)
        number = await _next_number(db, f"room:{user_id}:{payload.warehouse_id}")
        now = _now()
        room = {
            "id": str(uuid.uuid4()),
            "user_id": user_id,
            "warehouse_id": payload.warehouse_id,
            "warehouse_code": warehouse.get("code"),
            "room_number": number,
            "code": f"R{number:02d}",
            "name": payload.name.strip(),
            "room_type": payload.room_type,
            "allows_cabinets": payload.allows_cabinets,
            "notes": _text(payload.notes) or None,
            "status": "active",
            "created_at": now,
            "updated_at": now,
            "created_by": _text(actor.get("id")),
        }
        try:
            await db[ROOMS].insert_one(room)
        except DuplicateKeyError as exc:
            raise HTTPException(status_code=409, detail={"code": "room_number_conflict"}) from exc
        room.pop("_id", None)
        return room

    @router.get("/warehouses/{warehouse_id}/rooms")
    async def list_rooms(warehouse_id: str, user: dict = Depends(current_user)) -> dict[str, Any]:
        actor = _require_manager(user)
        user_id = _merchant_user_id(actor)
        rooms = await db[ROOMS].find(
            {"warehouse_id": warehouse_id, "user_id": user_id, "status": "active"},
            {"_id": 0},
        ).sort("room_number", 1).to_list(length=500)
        room_ids = [room["id"] for room in rooms]
        cabinets = []
        if room_ids:
            cabinets = await db[CABINETS].find(
                {"user_id": user_id, "room_id": {"$in": room_ids}, "status": "active"},
                {"_id": 0},
            ).sort([("room_id", 1), ("cabinet_number", 1)]).to_list(length=2000)
        grouped = {room_id: [] for room_id in room_ids}
        for cabinet in cabinets:
            grouped.setdefault(cabinet.get("room_id"), []).append(cabinet)
        for room in rooms:
            room["cabinets"] = grouped.get(room["id"], [])
            room["cabinet_count"] = len(room["cabinets"])
        return {"items": rooms, "total": len(rooms)}

    @router.post("/rooms/{room_id}/cabinets", status_code=status.HTTP_201_CREATED)
    async def create_room_cabinet(
        room_id: str,
        payload: RoomCabinetCreate,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        actor = _require_manager(user)
        user_id = _merchant_user_id(actor)
        await ensure_room_indexes(db)
        room = await db[ROOMS].find_one(
            {"id": room_id, "user_id": user_id, "status": "active"},
            {"_id": 0},
        )
        if not room:
            raise HTTPException(status_code=404, detail={"code": "room_not_found"})
        if not room.get("allows_cabinets", True):
            raise HTTPException(
                status_code=409,
                detail={"code": "room_does_not_allow_cabinets", "message": "هذه الغرفة تشغيلية ولا تسمح بإضافة دواليب."},
            )
        warehouse = await db[WAREHOUSES].find_one(
            {"id": room["warehouse_id"], "user_id": user_id, "status": "active"},
            {"_id": 0},
        )
        if not warehouse:
            raise HTTPException(status_code=404, detail={"code": "warehouse_not_found"})

        number = await _next_number(db, f"room-cabinet:{user_id}:{room_id}")
        room_code = room.get("code") or f"R{int(room.get('room_number') or 0):02d}"
        cabinet_code = f"{room_code}-C{number:02d}"
        generated_payload = CabinetGenerate(
            warehouse_id=room["warehouse_id"],
            cabinet_code=cabinet_code,
            cabinet_name=payload.cabinet_name,
            length=payload.length,
            width=payload.width,
            purpose=payload.purpose,
            max_items_per_location=payload.max_items_per_location,
        )
        generated = generate_location_rows(generated_payload, warehouse_code=warehouse["code"])
        cabinet_id = str(uuid.uuid4())
        now = _now()
        cabinet = {
            "id": cabinet_id,
            "user_id": user_id,
            "warehouse_id": room["warehouse_id"],
            "warehouse_code": warehouse["code"],
            "room_id": room_id,
            "room_number": room.get("room_number"),
            "room_code": room_code,
            "code": cabinet_code,
            "cabinet_number": number,
            "name": (payload.cabinet_name or f"دولاب {number}").strip(),
            "length": payload.length,
            "width": payload.width,
            "purpose": payload.purpose,
            "total_locations": len(generated),
            "status": "active",
            "created_at": now,
            "created_by": _text(actor.get("id")),
        }
        locations = []
        for row in generated:
            barcode_value = row["code"]
            locations.append({
                **row,
                "id": str(uuid.uuid4()),
                "user_id": user_id,
                "warehouse_id": room["warehouse_id"],
                "warehouse_code": warehouse["code"],
                "room_id": room_id,
                "room_number": room.get("room_number"),
                "room_code": room_code,
                "cabinet_id": cabinet_id,
                "cabinet_code": cabinet_code,
                "barcode_value": barcode_value,
                "barcode_symbology": "CODE39",
                "qr_value": f"MEZAN-LOCATION:{barcode_value}",
                "occupancy": None,
                "created_at": now,
                "updated_at": now,
            })
        try:
            await db[CABINETS].insert_one(cabinet)
            await db[LOCATIONS].insert_many(locations, ordered=True)
        except DuplicateKeyError as exc:
            await db[CABINETS].delete_one({"id": cabinet_id, "user_id": user_id})
            await db[LOCATIONS].delete_many({"cabinet_id": cabinet_id, "user_id": user_id})
            raise HTTPException(status_code=409, detail={"code": "room_cabinet_number_conflict"}) from exc

        await db[EVENTS].insert_one({
            "id": str(uuid.uuid4()),
            "user_id": user_id,
            "event_type": "room_cabinet_generated",
            "warehouse_id": room["warehouse_id"],
            "room_id": room_id,
            "cabinet_id": cabinet_id,
            "locations_created": len(locations),
            "actor_id": _text(actor.get("id")),
            "occurred_at": now,
        })
        cabinet.pop("_id", None)
        return {"cabinet": cabinet, "locations_created": len(locations)}

    return router
