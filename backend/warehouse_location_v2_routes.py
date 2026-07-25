"""Mezan OS V2 warehouse APIs with server-owned automatic numbering."""
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
    ensure_warehouse_location_indexes,
    generate_location_rows,
    CabinetGenerate,
)

COUNTERS = "warehouse_location_counters"

COUNTRY_CITIES = {
    "السعودية": ["الرياض", "جدة", "مكة المكرمة", "المدينة المنورة", "الدمام", "الخبر", "الطائف"],
    "الإمارات": ["دبي", "أبوظبي", "الشارقة", "عجمان", "رأس الخيمة", "الفجيرة", "أم القيوين"],
    "قطر": ["الدوحة", "الريان", "الوكرة", "الخــور", "لوسيل", "أم صلال"],
}
CITY_CODES = {
    "الرياض": "RUH", "جدة": "JED", "مكة المكرمة": "MKK", "المدينة المنورة": "MED",
    "الدمام": "DMM", "الخبر": "KHB", "الطائف": "TAF",
    "دبي": "DXB", "أبوظبي": "AUH", "الشارقة": "SHJ", "عجمان": "AJM",
    "رأس الخيمة": "RAK", "الفجيرة": "FUJ", "أم القيوين": "UAQ",
    "الدوحة": "DOH", "الريان": "RYN", "الوكرة": "WAK", "الخــور": "KHO",
    "لوسيل": "LUS", "أم صلال": "UMS",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class WarehouseCreateV2(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    country: Literal["السعودية", "الإمارات", "قطر"] = "السعودية"
    city: str = "الرياض"
    district: str = Field(min_length=1, max_length=120)
    street: str = Field(min_length=1, max_length=180)
    is_primary: bool = False


class CabinetCreateV2(BaseModel):
    model_config = ConfigDict(extra="forbid")
    warehouse_id: str = Field(min_length=1, max_length=80)
    cabinet_name: str | None = Field(default=None, max_length=120)
    length: int = Field(ge=1, le=100)
    width: int = Field(ge=1, le=100)
    purpose: LocationPurpose = "temporary_staging"
    max_items_per_location: int | None = Field(default=None, ge=1, le=100000)


async def _next_number(db: Any, *, key: str) -> int:
    row = await db[COUNTERS].find_one_and_update(
        {"key": key},
        {"$inc": {"value": 1}, "$setOnInsert": {"created_at": _now()}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return int(row["value"])


async def ensure_v2_indexes(db: Any) -> None:
    await ensure_warehouse_location_indexes(db)
    await db[COUNTERS].create_index("key", unique=True)
    await db[WAREHOUSES].create_index(
        [("user_id", 1), ("country", 1), ("city", 1), ("warehouse_number_int", 1)],
        unique=True,
        sparse=True,
    )
    await db[CABINETS].create_index(
        [("user_id", 1), ("warehouse_id", 1), ("cabinet_number", 1)],
        unique=True,
        sparse=True,
    )


def make_warehouse_location_v2_router(db: Any, current_user: Callable[..., Any]) -> APIRouter:
    router = APIRouter(prefix="/warehouse-locations-v2", tags=["Mezan OS V2 Warehouses"])

    @router.get("/catalog")
    async def catalog(user: dict = Depends(current_user)) -> dict[str, Any]:
        _require_manager(user)
        return {
            "countries": [
                {"name": country, "cities": cities, "default_city": cities[0]}
                for country, cities in COUNTRY_CITIES.items()
            ],
            "default_country": "السعودية",
            "default_city": "الرياض",
        }

    @router.post("/warehouses", status_code=201)
    async def create_warehouse(payload: WarehouseCreateV2, user: dict = Depends(current_user)) -> dict[str, Any]:
        actor = _require_manager(user)
        user_id = _merchant_user_id(actor)
        if payload.city not in COUNTRY_CITIES[payload.country]:
            raise HTTPException(status_code=422, detail={"code": "city_not_in_country"})
        await ensure_v2_indexes(db)
        number = await _next_number(db, key=f"warehouse:{user_id}:{payload.country}:{payload.city}")
        city_code = CITY_CODES.get(payload.city, "CITY")
        code = f"{city_code}-WH{number:03d}"
        now = _now()
        doc = {
            "id": str(uuid.uuid4()), "user_id": user_id,
            "name": payload.name.strip(), "code": code,
            "country": payload.country, "city": payload.city,
            "district": payload.district.strip(), "street": payload.street.strip(),
            "warehouse_number": str(number), "warehouse_number_int": number,
            "address_label": " > ".join([payload.country, payload.city, payload.district.strip(), payload.street.strip(), str(number)]),
            "is_primary": payload.is_primary, "status": "active",
            "created_at": now, "updated_at": now, "created_by": _text(actor.get("id")),
        }
        try:
            if payload.is_primary:
                await db[WAREHOUSES].update_many({"user_id": user_id}, {"$set": {"is_primary": False, "updated_at": now}})
            await db[WAREHOUSES].insert_one(doc)
        except DuplicateKeyError as exc:
            raise HTTPException(status_code=409, detail={"code": "warehouse_number_conflict"}) from exc
        doc.pop("_id", None)
        return doc

    @router.post("/cabinets", status_code=201)
    async def create_cabinet(payload: CabinetCreateV2, user: dict = Depends(current_user)) -> dict[str, Any]:
        actor = _require_manager(user)
        user_id = _merchant_user_id(actor)
        await ensure_v2_indexes(db)
        warehouse = await db[WAREHOUSES].find_one({"id": payload.warehouse_id, "user_id": user_id, "status": "active"}, {"_id": 0})
        if not warehouse:
            raise HTTPException(status_code=404, detail={"code": "warehouse_not_found"})
        number = await _next_number(db, key=f"cabinet:{user_id}:{payload.warehouse_id}")
        cabinet_code = str(number)
        generated_payload = CabinetGenerate(
            warehouse_id=payload.warehouse_id,
            cabinet_code=cabinet_code,
            cabinet_name=payload.cabinet_name,
            length=payload.length,
            width=payload.width,
            purpose=payload.purpose,
            max_items_per_location=payload.max_items_per_location,
        )
        generated = generate_location_rows(generated_payload, warehouse_code=warehouse["code"])
        now = _now()
        cabinet_id = str(uuid.uuid4())
        cabinet = {
            "id": cabinet_id, "user_id": user_id, "warehouse_id": payload.warehouse_id,
            "warehouse_code": warehouse["code"], "code": cabinet_code, "cabinet_number": number,
            "name": (payload.cabinet_name or f"دولاب {number}").strip(),
            "length": payload.length, "width": payload.width, "purpose": payload.purpose,
            "total_locations": len(generated), "status": "active", "created_at": now,
            "created_by": _text(actor.get("id")),
        }
        locations = [{
            **row, "id": str(uuid.uuid4()), "user_id": user_id,
            "warehouse_id": payload.warehouse_id, "warehouse_code": warehouse["code"],
            "cabinet_id": cabinet_id, "cabinet_code": cabinet_code,
            "qr_value": f"MEZAN-LOCATION:{row['code']}", "occupancy": None,
            "created_at": now, "updated_at": now,
        } for row in generated]
        try:
            await db[CABINETS].insert_one(cabinet)
            await db[LOCATIONS].insert_many(locations, ordered=True)
        except DuplicateKeyError as exc:
            await db[CABINETS].delete_one({"id": cabinet_id, "user_id": user_id})
            await db[LOCATIONS].delete_many({"cabinet_id": cabinet_id, "user_id": user_id})
            raise HTTPException(status_code=409, detail={"code": "cabinet_number_conflict"}) from exc
        await db[EVENTS].insert_one({
            "id": str(uuid.uuid4()), "user_id": user_id, "event_type": "cabinet_generated",
            "warehouse_id": payload.warehouse_id, "cabinet_id": cabinet_id,
            "locations_created": len(locations), "actor_id": _text(actor.get("id")), "occurred_at": now,
        })
        cabinet.pop("_id", None)
        return {"cabinet": cabinet, "locations_created": len(locations)}

    return router
