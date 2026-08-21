"""Mobile categories/resources/groups catalog for Mezan shared components.

ADR-001 mapping: additive route surface (1), backward-compatible contracts (7),
canonical component resources remain the SSOT (9), and every collection/query is
tenant-scoped by ``user_id`` (11).
"""
from __future__ import annotations

import hashlib
import uuid
from typing import Any, Callable

from fastapi import APIRouter, Body, Depends, HTTPException

from product_option_cost_routes import RESOURCES
from product_v2_routes import _text

CATEGORIES = "mezan_resource_categories_v1"
GROUPS = "mezan_resource_groups_v1"


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _normalized(value: Any) -> str:
    return " ".join(_text(value).split()).casefold()


def _category_id(name: str) -> str:
    return hashlib.sha1(name.casefold().encode("utf-8")).hexdigest()[:20]


async def _ensure_indexes(db: Any) -> None:
    await db[CATEGORIES].create_index([("user_id", 1), ("normalized_name", 1)], unique=True)
    await db[GROUPS].create_index([("user_id", 1), ("id", 1)], unique=True)
    await db[GROUPS].create_index([("user_id", 1), ("category", 1), ("kind", 1), ("normalized_name", 1)], unique=True)


def _resource_kind(row: dict[str, Any]) -> str:
    return "stock_component" if row.get("track_inventory") else "service"


def _serialize_resource(row: dict[str, Any]) -> dict[str, Any]:
    amount = row.get("unit_cost")
    if amount is None:
        amount = row.get("initial_unit_cost")
    return {
        "id": str(row.get("id") or ""),
        "name": _text(row.get("name")),
        "code": _text(row.get("code")),
        "kind": _resource_kind(row),
        "unit": _text(row.get("unit")) or ("piece" if row.get("track_inventory") else "job"),
        "category": _text(row.get("category")) or "other",
        "description": _text(row.get("description")),
        "requires_preparation": row.get("requires_preparation") is True,
        "unit_cost": amount,
        "track_inventory": bool(row.get("track_inventory")),
    }


def make_resource_catalog_mobile_router(db: Any, current_user: Callable[..., Any]) -> APIRouter:
    router = APIRouter(prefix="/resource-catalog-v1", tags=["Mezan Mobile Resource Catalog"])

    @router.get("/mobile")
    async def mobile_catalog(user: dict = Depends(current_user)) -> dict[str, Any]:
        await _ensure_indexes(db)
        user_id = str(user["id"])
        resources = await db[RESOURCES].find({"user_id": user_id}, {"_id": 0}).sort("name", 1).to_list(length=2000)
        category_docs = await db[CATEGORIES].find({"user_id": user_id}, {"_id": 0}).sort("name", 1).to_list(length=1000)
        groups = await db[GROUPS].find({"user_id": user_id}, {"_id": 0}).sort("name", 1).to_list(length=2000)

        names: dict[str, str] = {}
        for row in category_docs:
            name = _text(row.get("name"))
            if name:
                names[_normalized(name)] = name
        for row in resources:
            name = _text(row.get("category")) or "other"
            names.setdefault(_normalized(name), name)

        serialized_resources = [_serialize_resource(row) for row in resources]
        categories = []
        for _, name in sorted(names.items(), key=lambda item: item[1].casefold()):
            rows = [row for row in serialized_resources if _normalized(row.get("category")) == _normalized(name)]
            categories.append({
                "id": _category_id(name),
                "name": name,
                "resources_count": len(rows),
                "services_count": sum(1 for row in rows if row["kind"] == "service"),
                "components_count": sum(1 for row in rows if row["kind"] == "stock_component"),
            })

        clean_groups = [{
            "id": str(row.get("id") or ""),
            "name": _text(row.get("name")),
            "category": _text(row.get("category")),
            "kind": _text(row.get("kind")),
            "resource_ids": [str(value) for value in (row.get("resource_ids") or []) if value],
            "updated_at": row.get("updated_at"),
        } for row in groups]
        return {"ok": True, "categories": categories, "resources": serialized_resources, "groups": clean_groups}

    @router.post("/categories")
    async def create_category(payload: dict = Body(...), user: dict = Depends(current_user)) -> dict[str, Any]:
        await _ensure_indexes(db)
        user_id = str(user["id"])
        name = " ".join(_text(payload.get("name")).split())
        normalized_name = _normalized(name)
        if not name:
            raise HTTPException(status_code=422, detail={"code": "category_name_required", "message": "اسم التصنيف مطلوب."})
        existing_resource = await db[RESOURCES].find_one({"user_id": user_id, "category": name}, {"_id": 1})
        existing_doc = await db[CATEGORIES].find_one({"user_id": user_id, "normalized_name": normalized_name}, {"_id": 1})
        if existing_resource or existing_doc:
            raise HTTPException(status_code=409, detail={"code": "category_exists", "message": "التصنيف موجود مسبقًا."})
        now = _now_iso()
        row = {"id": uuid.uuid4().hex, "user_id": user_id, "name": name, "normalized_name": normalized_name, "created_at": now, "updated_at": now}
        try:
            await db[CATEGORIES].insert_one(row)
        except Exception as exc:
            if "duplicate" in str(exc).lower():
                raise HTTPException(status_code=409, detail={"code": "category_exists", "message": "التصنيف موجود مسبقًا."}) from exc
            raise
        return {"ok": True, "category": {"id": _category_id(name), "name": name}}

    async def validate_group(user_id: str, payload: dict, current_id: str = "") -> tuple[str, str, str, list[str]]:
        name = " ".join(_text(payload.get("name")).split())
        category = " ".join(_text(payload.get("category")).split())
        kind = _text(payload.get("kind"))
        resource_ids = list(dict.fromkeys(str(value) for value in (payload.get("resource_ids") or []) if value))
        if not name or not category:
            raise HTTPException(status_code=422, detail={"code": "invalid_group", "message": "اسم المجموعة والتصنيف مطلوبان."})
        if kind not in {"service", "stock_component"}:
            raise HTTPException(status_code=422, detail={"code": "invalid_group_kind", "message": "اختر مجموعة خدمات أو مجموعة مكونات."})
        if not resource_ids:
            raise HTTPException(status_code=422, detail={"code": "group_items_required", "message": "اختر خدمة أو مكونًا واحدًا على الأقل للمجموعة."})
        rows = await db[RESOURCES].find({"user_id": user_id, "id": {"$in": resource_ids}}, {"_id": 0, "id": 1, "category": 1, "track_inventory": 1}).to_list(length=len(resource_ids))
        if len(rows) != len(resource_ids):
            raise HTTPException(status_code=422, detail={"code": "group_resource_missing", "message": "يوجد عنصر غير متوفر ضمن المجموعة."})
        for row in rows:
            if _normalized(row.get("category")) != _normalized(category) or _resource_kind(row) != kind:
                raise HTTPException(status_code=422, detail={"code": "group_resource_scope_mismatch", "message": "كل عناصر المجموعة يجب أن تكون من نفس التصنيف ونفس النوع."})
        duplicate = await db[GROUPS].find_one({
            "user_id": user_id,
            "category": category,
            "kind": kind,
            "normalized_name": _normalized(name),
            **({"id": {"$ne": current_id}} if current_id else {}),
        }, {"_id": 1})
        if duplicate:
            raise HTTPException(status_code=409, detail={"code": "group_exists", "message": "يوجد مجموعة بنفس الاسم والنوع داخل التصنيف."})
        return name, category, kind, resource_ids

    @router.post("/groups")
    async def create_group(payload: dict = Body(...), user: dict = Depends(current_user)) -> dict[str, Any]:
        await _ensure_indexes(db)
        user_id = str(user["id"])
        name, category, kind, resource_ids = await validate_group(user_id, payload)
        now = _now_iso()
        row = {"id": uuid.uuid4().hex, "user_id": user_id, "name": name, "normalized_name": _normalized(name), "category": category, "kind": kind, "resource_ids": resource_ids, "created_at": now, "updated_at": now}
        await db[GROUPS].insert_one(row)
        return {"ok": True, "group": {key: row[key] for key in ("id", "name", "category", "kind", "resource_ids")}}

    @router.put("/groups/{group_id}")
    async def update_group(group_id: str, payload: dict = Body(...), user: dict = Depends(current_user)) -> dict[str, Any]:
        await _ensure_indexes(db)
        user_id = str(user["id"])
        current = await db[GROUPS].find_one({"user_id": user_id, "id": group_id}, {"_id": 0})
        if not current:
            raise HTTPException(status_code=404, detail={"code": "group_not_found", "message": "المجموعة غير موجودة."})
        merged = {**current, **payload}
        name, category, kind, resource_ids = await validate_group(user_id, merged, group_id)
        patch = {"name": name, "normalized_name": _normalized(name), "category": category, "kind": kind, "resource_ids": resource_ids, "updated_at": _now_iso()}
        await db[GROUPS].update_one({"user_id": user_id, "id": group_id}, {"$set": patch})
        return {"ok": True, "group": {"id": group_id, **patch}}

    return router
