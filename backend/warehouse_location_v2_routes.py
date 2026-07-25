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


class LocationPlacementV2(BaseModel):
    """A physical placement is valid only after scanning the target location."""

    model_config = ConfigDict(extra="forbid")
    scanned_barcode: str = Field(min_length=1, max_length=120)
    product_id: str = Field(min_length=1, max_length=120)
    product_name: str | None = Field(default=None, max_length=240)
    sku: str | None = Field(default=None, max_length=120)
    quantity: int = Field(ge=1, le=100000)
    order_number: str | None = Field(default=None, max_length=80)


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
    await db[LOCATIONS].create_index(
        [("user_id", 1), ("barcode_value", 1)], unique=True, sparse=True
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
        locations = []
        for row in generated:
            barcode_value = row["code"]
            locations.append({
                **row, "id": str(uuid.uuid4()), "user_id": user_id,
                "warehouse_id": payload.warehouse_id, "warehouse_code": warehouse["code"],
                "cabinet_id": cabinet_id, "cabinet_code": cabinet_code,
                "barcode_value": barcode_value, "barcode_symbology": "CODE39",
                "qr_value": f"MEZAN-LOCATION:{barcode_value}", "occupancy": None,
                "created_at": now, "updated_at": now,
            })
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

    @router.get("/locations/scan/{barcode}")
    async def scan_location(barcode: str, user: dict = Depends(current_user)) -> dict[str, Any]:
        actor = _require_manager(user)
        user_id = _merchant_user_id(actor)
        normalized = _text(barcode).upper()
        location = await db[LOCATIONS].find_one(
            {"user_id": user_id, "barcode_value": normalized}, {"_id": 0}
        )
        if not location:
            raise HTTPException(status_code=404, detail={"code": "location_barcode_not_found"})
        return {"ok": True, "scan_verified": True, "location": location}

    @router.post("/locations/{location_id}/place", status_code=201)
    async def place_product(
        location_id: str,
        payload: LocationPlacementV2,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        actor = _require_manager(user)
        user_id = _merchant_user_id(actor)
        location = await db[LOCATIONS].find_one(
            {"id": location_id, "user_id": user_id}, {"_id": 0}
        )
        if not location:
            raise HTTPException(status_code=404, detail={"code": "location_not_found"})
        expected_barcode = _text(location.get("barcode_value") or location.get("code")).upper()
        scanned_barcode = _text(payload.scanned_barcode).upper()
        if scanned_barcode != expected_barcode:
            await db[EVENTS].insert_one({
                "id": str(uuid.uuid4()), "user_id": user_id,
                "event_type": "placement_scan_rejected", "location_id": location_id,
                "expected_barcode": expected_barcode, "scanned_barcode": scanned_barcode,
                "product_id": payload.product_id, "actor_id": _text(actor.get("id")),
                "occurred_at": _now(),
            })
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "location_barcode_mismatch",
                    "message": "الباركود المصوّر لا يطابق الخانة المطلوبة؛ لم يتم وضع المنتج.",
                    "expected_location_code": expected_barcode,
                },
            )
        if location.get("state") == "disabled":
            raise HTTPException(status_code=409, detail={"code": "location_disabled"})

        occupancy = location.get("occupancy") or {"items": [], "total_quantity": 0}
        items = list(occupancy.get("items") or [])
        existing = next((item for item in items if item.get("product_id") == payload.product_id), None)
        if existing:
            existing["quantity"] = int(existing.get("quantity") or 0) + payload.quantity
            existing["updated_at"] = _now()
        else:
            items.append({
                "product_id": payload.product_id,
                "product_name": payload.product_name,
                "sku": payload.sku,
                "quantity": payload.quantity,
                "order_number": payload.order_number,
                "placed_at": _now(),
                "placed_by": _text(actor.get("id")),
            })
        total_quantity = sum(int(item.get("quantity") or 0) for item in items)
        max_items = location.get("max_items")
        if max_items is not None and total_quantity > int(max_items):
            raise HTTPException(
                status_code=409,
                detail={"code": "location_capacity_exceeded", "max_items": int(max_items)},
            )
        now = _now()
        next_occupancy = {"items": items, "total_quantity": total_quantity}
        await db[LOCATIONS].update_one(
            {"id": location_id, "user_id": user_id},
            {"$set": {
                "occupancy": next_occupancy,
                "state": "occupied",
                "last_verified_scan": scanned_barcode,
                "last_scan_verified_at": now,
                "updated_at": now,
            }},
        )
        await db[EVENTS].insert_one({
            "id": str(uuid.uuid4()), "user_id": user_id,
            "event_type": "product_placed_after_scan", "location_id": location_id,
            "location_code": expected_barcode, "scan_verified": True,
            "product_id": payload.product_id, "quantity": payload.quantity,
            "order_number": payload.order_number, "actor_id": _text(actor.get("id")),
            "occurred_at": now,
        })
        return {
            "ok": True,
            "scan_verified": True,
            "location_id": location_id,
            "location_code": expected_barcode,
            "state": "occupied",
            "occupancy": next_occupancy,
        }

    return router
