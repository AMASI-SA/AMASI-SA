"""Dynamic branch sections for Mezan OS V2.

A branch may contain cabinets directly and/or any number of dynamic sections.
Each section owns a set of capabilities instead of one fixed room type, so the
same section may combine storage, assembly, engraving and a production line.
Existing room records remain readable and compatible.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
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

SectionCapability = Literal[
    "cabinets",
    "workstations",
    "assembly",
    "engraving",
    "packing",
    "shipping_labeling",
    "quality_control",
    "waiting_areas",
    "equipment",
    "production_line",
    "office",
    "worker_housing",
    "returns",
]

CAPABILITY_LABELS = {
    "cabinets": "دواليب وخانات",
    "workstations": "محطات عمل",
    "assembly": "تركيب",
    "engraving": "نحت ونقش",
    "packing": "تغليف",
    "shipping_labeling": "شحن وعنونة",
    "quality_control": "فحص جودة",
    "waiting_areas": "مناطق انتظار",
    "equipment": "أجهزة ومعدات",
    "production_line": "خط إنتاج",
    "office": "إدارة ومكاتب",
    "worker_housing": "سكن عمال",
    "returns": "مرتجعات",
}

LEGACY_TYPE_CAPABILITIES = {
    "storage": ["cabinets"],
    "installation_engraving": ["cabinets", "workstations", "assembly", "engraving", "quality_control"],
    "shipping_labeling": ["cabinets", "workstations", "packing", "shipping_labeling", "waiting_areas"],
    "packing": ["cabinets", "workstations", "packing"],
    "returns": ["cabinets", "returns", "quality_control"],
    "raw_materials": ["cabinets"],
    "ready_to_ship": ["cabinets", "waiting_areas", "shipping_labeling"],
    "worker_housing": ["worker_housing"],
    "office": ["office"],
    "other": [],
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_capabilities(values: list[str] | None) -> list[str]:
    allowed = set(CAPABILITY_LABELS)
    return sorted({str(value).strip() for value in (values or []) if str(value).strip() in allowed})


def _hydrate_section(row: dict[str, Any]) -> dict[str, Any]:
    capabilities = row.get("capabilities")
    if capabilities is None:
        capabilities = LEGACY_TYPE_CAPABILITIES.get(str(row.get("room_type") or "other"), [])
    row["capabilities"] = _normalize_capabilities(capabilities)
    row["allows_cabinets"] = "cabinets" in row["capabilities"]
    row["section_number"] = int(row.get("section_number") or row.get("room_number") or 0)
    row["room_number"] = row["section_number"]  # compatibility with current frontend/data
    row["entity_type"] = "section"
    return row


class SectionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    warehouse_id: str = Field(min_length=1, max_length=80)
    name: str | None = Field(default=None, max_length=120)
    capabilities: list[SectionCapability] = Field(default_factory=list)
    notes: str | None = Field(default=None, max_length=500)

    @field_validator("capabilities")
    @classmethod
    def unique_capabilities(cls, value: list[SectionCapability]) -> list[SectionCapability]:
        return list(dict.fromkeys(value))


class SectionBulkCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    warehouse_id: str = Field(min_length=1, max_length=80)
    count: int = Field(ge=1, le=50)
    default_capabilities: list[SectionCapability] = Field(default_factory=list)


class SectionCabinetCreate(BaseModel):
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


async def _get_branch(db: Any, *, warehouse_id: str, user_id: str) -> dict[str, Any]:
    branch = await db[WAREHOUSES].find_one(
        {"id": warehouse_id, "user_id": user_id, "status": "active"},
        {"_id": 0},
    )
    if not branch:
        raise HTTPException(status_code=404, detail={"code": "branch_not_found"})
    return branch


async def ensure_section_indexes(db: Any) -> None:
    await db[ROOMS].create_index(
        [("user_id", 1), ("warehouse_id", 1), ("room_number", 1)],
        unique=True,
        name="uq_branch_section_number",
    )
    await db[CABINETS].create_index(
        [("user_id", 1), ("warehouse_id", 1), ("room_id", 1), ("cabinet_number", 1)],
        unique=True,
        partialFilterExpression={"room_id": {"$exists": True}},
        name="uq_room_cabinet_number_v2",
    )


async def _create_section_doc(
    db: Any,
    *,
    branch: dict[str, Any],
    user_id: str,
    actor_id: str,
    name: str | None,
    capabilities: list[str],
    notes: str | None = None,
) -> dict[str, Any]:
    number = await _next_number(db, f"section:{user_id}:{branch['id']}")
    now = _now()
    normalized = _normalize_capabilities(capabilities)
    section = {
        "id": str(uuid.uuid4()),
        "user_id": user_id,
        "warehouse_id": branch["id"],
        "warehouse_code": branch.get("code"),
        "room_number": number,
        "section_number": number,
        "code": f"S{number:02d}",
        "name": (_text(name) or f"قسم {number}"),
        "capabilities": normalized,
        "allows_cabinets": "cabinets" in normalized,
        "room_type": "dynamic",
        "notes": _text(notes) or None,
        "status": "active",
        "created_at": now,
        "updated_at": now,
        "created_by": actor_id,
        "entity_type": "section",
    }
    await db[ROOMS].insert_one(section)
    section.pop("_id", None)
    return section


def make_warehouse_room_router(db: Any, current_user: Callable[..., Any]) -> APIRouter:
    router = APIRouter(prefix="/warehouse-locations-v2", tags=["Mezan OS V2 Branch Sections"])

    @router.get("/section-capabilities")
    async def section_capabilities(user: dict = Depends(current_user)) -> dict[str, Any]:
        _require_manager(user)
        return {
            "items": [{"value": value, "label": label} for value, label in CAPABILITY_LABELS.items()],
            "recommended": {
                "storage": ["cabinets"],
                "assembly": ["cabinets", "workstations", "assembly", "quality_control"],
                "engraving": ["cabinets", "workstations", "engraving", "quality_control"],
                "shipping": ["cabinets", "workstations", "packing", "shipping_labeling", "waiting_areas"],
                "production": ["cabinets", "workstations", "equipment", "production_line", "quality_control"],
                "administration": ["office", "cabinets"],
            },
        }

    @router.post("/sections", status_code=status.HTTP_201_CREATED)
    @router.post("/rooms", status_code=status.HTTP_201_CREATED, include_in_schema=False)
    async def create_section(payload: SectionCreate, user: dict = Depends(current_user)) -> dict[str, Any]:
        actor = _require_manager(user)
        user_id = _merchant_user_id(actor)
        branch = await _get_branch(db, warehouse_id=payload.warehouse_id, user_id=user_id)
        await ensure_section_indexes(db)
        try:
            return await _create_section_doc(
                db,
                branch=branch,
                user_id=user_id,
                actor_id=_text(actor.get("id")),
                name=payload.name,
                capabilities=list(payload.capabilities),
                notes=payload.notes,
            )
        except DuplicateKeyError as exc:
            raise HTTPException(status_code=409, detail={"code": "section_number_conflict"}) from exc

    @router.post("/sections/bulk", status_code=status.HTTP_201_CREATED)
    async def create_sections_bulk(payload: SectionBulkCreate, user: dict = Depends(current_user)) -> dict[str, Any]:
        actor = _require_manager(user)
        user_id = _merchant_user_id(actor)
        branch = await _get_branch(db, warehouse_id=payload.warehouse_id, user_id=user_id)
        await ensure_section_indexes(db)
        created = []
        for _ in range(payload.count):
            created.append(await _create_section_doc(
                db,
                branch=branch,
                user_id=user_id,
                actor_id=_text(actor.get("id")),
                name=None,
                capabilities=list(payload.default_capabilities),
            ))
        return {"items": created, "created_count": len(created)}

    @router.get("/warehouses/{warehouse_id}/sections")
    @router.get("/warehouses/{warehouse_id}/rooms", include_in_schema=False)
    async def list_sections(warehouse_id: str, user: dict = Depends(current_user)) -> dict[str, Any]:
        actor = _require_manager(user)
        user_id = _merchant_user_id(actor)
        rows = await db[ROOMS].find(
            {"warehouse_id": warehouse_id, "user_id": user_id, "status": "active"},
            {"_id": 0},
        ).sort("room_number", 1).to_list(length=500)
        sections = [_hydrate_section(row) for row in rows]
        section_ids = [section["id"] for section in sections]
        cabinets = []
        if section_ids:
            cabinets = await db[CABINETS].find(
                {"user_id": user_id, "room_id": {"$in": section_ids}, "status": "active"},
                {"_id": 0},
            ).sort([("room_id", 1), ("cabinet_number", 1)]).to_list(length=2000)
        grouped = {section_id: [] for section_id in section_ids}
        for cabinet in cabinets:
            grouped.setdefault(cabinet.get("room_id"), []).append(cabinet)
        for section in sections:
            section["cabinets"] = grouped.get(section["id"], [])
            section["cabinet_count"] = len(section["cabinets"])
        return {"items": sections, "total": len(sections)}

    @router.post("/sections/{section_id}/cabinets", status_code=status.HTTP_201_CREATED)
    @router.post("/rooms/{section_id}/cabinets", status_code=status.HTTP_201_CREATED, include_in_schema=False)
    async def create_section_cabinet(
        section_id: str,
        payload: SectionCabinetCreate,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        actor = _require_manager(user)
        user_id = _merchant_user_id(actor)
        await ensure_section_indexes(db)
        raw_section = await db[ROOMS].find_one(
            {"id": section_id, "user_id": user_id, "status": "active"},
            {"_id": 0},
        )
        if not raw_section:
            raise HTTPException(status_code=404, detail={"code": "section_not_found"})
        section = _hydrate_section(raw_section)
        if "cabinets" not in section["capabilities"]:
            raise HTTPException(
                status_code=409,
                detail={"code": "section_missing_cabinet_capability", "message": "فعّل قدرة الدواليب والخانات لهذا القسم أولًا."},
            )
        branch = await _get_branch(db, warehouse_id=section["warehouse_id"], user_id=user_id)

        number = await _next_number(db, f"section-cabinet:{user_id}:{section_id}")
        section_code = section.get("code") or f"S{int(section.get('section_number') or 0):02d}"
        cabinet_code = f"{section_code}-C{number:02d}"
        generated_payload = CabinetGenerate(
            warehouse_id=section["warehouse_id"],
            cabinet_code=cabinet_code,
            cabinet_name=payload.cabinet_name,
            length=payload.length,
            width=payload.width,
            purpose=payload.purpose,
            max_items_per_location=payload.max_items_per_location,
        )
        generated = generate_location_rows(generated_payload, warehouse_code=branch["code"])
        cabinet_id = str(uuid.uuid4())
        now = _now()
        cabinet = {
            "id": cabinet_id,
            "user_id": user_id,
            "warehouse_id": section["warehouse_id"],
            "warehouse_code": branch["code"],
            "room_id": section_id,
            "section_id": section_id,
            "room_number": section["section_number"],
            "section_number": section["section_number"],
            "room_code": section_code,
            "section_code": section_code,
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
                "warehouse_id": section["warehouse_id"],
                "warehouse_code": branch["code"],
                "room_id": section_id,
                "section_id": section_id,
                "room_number": section["section_number"],
                "section_number": section["section_number"],
                "room_code": section_code,
                "section_code": section_code,
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
            raise HTTPException(status_code=409, detail={"code": "section_cabinet_number_conflict"}) from exc

        await db[EVENTS].insert_one({
            "id": str(uuid.uuid4()),
            "user_id": user_id,
            "event_type": "section_cabinet_generated",
            "warehouse_id": section["warehouse_id"],
            "section_id": section_id,
            "cabinet_id": cabinet_id,
            "locations_created": len(locations),
            "actor_id": _text(actor.get("id")),
            "occurred_at": now,
        })
        cabinet.pop("_id", None)
        return {"cabinet": cabinet, "locations_created": len(locations)}

    return router
