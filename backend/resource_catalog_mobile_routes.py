"""Backward-compatible mobile adapter for canonical Mezan component organization.

The canonical SSOT is the same one used by Mezan Web:
- ``mezan_cost_resources_v2`` resources with ``category_ids``
- ``mezan_component_categories_v2`` categories
- ``mezan_component_groups_v2`` reusable groups

This adapter keeps the legacy ``/resource-catalog-v1`` response shape for older
AMASI app builds, but it never reads or writes a parallel category/group store.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from fastapi import APIRouter, Body, Depends, HTTPException

from component_workspace_cost_compat_routes import (
    COMPONENT_CATEGORIES,
    COMPONENT_GROUPS,
    generated_group_name,
    validate_group_members,
)
from component_status_policy import active_component_selector, component_status
from product_option_cost_routes import RESOURCES
from product_v2_routes import _text


def _normalized(value: Any) -> str:
    return re.sub(r"\s+", " ", _text(value)).strip().casefold()


def _unique_ids(values: Any) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values if isinstance(values, list) else []:
        item_id = _text(value)
        if item_id and item_id not in seen:
            seen.add(item_id)
            output.append(item_id)
    return output


def _resource_kind(row: dict[str, Any]) -> str:
    return "stock_component" if row.get("track_inventory") else "service"


def _group_kind(kind: str) -> str:
    return "component" if kind == "stock_component" else "service"


def _legacy_kind(group_kind: str) -> str:
    return "stock_component" if group_kind == "component" else "service"


def _serialize_resource(row: dict[str, Any], category_names: dict[str, str]) -> dict[str, Any]:
    amount = row.get("unit_cost")
    if amount is None:
        amount = row.get("initial_unit_cost")
    category_ids = _unique_ids(row.get("category_ids"))
    first_category = next((category_names.get(category_id) for category_id in category_ids if category_names.get(category_id)), "")
    return {
        "id": str(row.get("id") or ""),
        "name": _text(row.get("name")),
        "code": _text(row.get("code")),
        "kind": _resource_kind(row),
        "unit": _text(row.get("unit")) or ("piece" if row.get("track_inventory") else "job"),
        "category": first_category,
        "category_ids": category_ids,
        "description": _text(row.get("description")),
        "requires_preparation": row.get("requires_preparation") is True,
        "unit_cost": amount,
        "track_inventory": bool(row.get("track_inventory")),
        "status": component_status(row),
    }


async def _category_by_name(db: Any, user_id: str, name: str) -> dict[str, Any] | None:
    normalized = _normalized(name)
    rows = await db[COMPONENT_CATEGORIES].find(
        {"user_id": user_id, "status": {"$ne": "inactive"}},
        {"_id": 0},
    ).to_list(length=500)
    return next((row for row in rows if _normalized(row.get("name")) == normalized), None)


def make_resource_catalog_mobile_router(db: Any, current_user: Callable[..., Any]) -> APIRouter:
    router = APIRouter(prefix="/resource-catalog-v1", tags=["Mezan Mobile Resource Catalog Compatibility"])

    @router.get("/mobile")
    async def mobile_catalog(user: dict = Depends(current_user)) -> dict[str, Any]:
        user_id = str(user["id"])
        category_docs = await db[COMPONENT_CATEGORIES].find(
            {"user_id": user_id, "status": {"$ne": "inactive"}},
            {"_id": 0, "id": 1, "name": 1, "status": 1},
        ).sort("name", 1).to_list(length=500)
        category_names = {
            _text(row.get("id")): _text(row.get("name"))
            for row in category_docs
            if _text(row.get("id")) and _text(row.get("name"))
        }

        resources = await db[RESOURCES].find(
            {"user_id": user_id, **active_component_selector()},
            {
                "_id": 0,
                "id": 1,
                "name": 1,
                "code": 1,
                "kind": 1,
                "unit": 1,
                "category_ids": 1,
                "description": 1,
                "requires_preparation": 1,
                "unit_cost": 1,
                "initial_unit_cost": 1,
                "track_inventory": 1,
                "status": 1,
            },
        ).sort("name", 1).to_list(length=5000)
        serialized_resources = [_serialize_resource(row, category_names) for row in resources]

        categories = []
        for category in category_docs:
            category_id = _text(category.get("id"))
            assigned = [row for row in serialized_resources if category_id in row.get("category_ids", [])]
            categories.append({
                "id": category_id,
                "name": _text(category.get("name")),
                "resources_count": len(assigned),
                "services_count": sum(row["kind"] == "service" for row in assigned),
                "components_count": sum(row["kind"] == "stock_component" for row in assigned),
            })

        groups = await db[COMPONENT_GROUPS].find(
            {"user_id": user_id, "status": {"$ne": "inactive"}},
            {"_id": 0},
        ).sort("updated_at", -1).to_list(length=2000)
        resources_by_id = {row["id"]: row for row in serialized_resources}
        clean_groups = []
        for row in groups:
            resource_ids = _unique_ids(row.get("resource_ids"))
            group_resources = [resources_by_id[resource_id] for resource_id in resource_ids if resource_id in resources_by_id]
            category_id = _text(row.get("category_id"))
            clean_groups.append({
                "id": _text(row.get("id")),
                "name": generated_group_name(group_resources, resource_ids),
                "category": category_names.get(category_id, ""),
                "category_id": category_id,
                "kind": _legacy_kind(_text(row.get("group_kind"))),
                "resource_ids": resource_ids,
                "updated_at": row.get("updated_at"),
            })

        return {
            "ok": True,
            "categories": categories,
            "resources": serialized_resources,
            "groups": clean_groups,
            "meta": {
                "source": "canonical_components_v2",
                "compatibility_adapter": True,
                "parallel_store_writes": False,
            },
        }

    @router.post("/categories")
    async def create_category(payload: dict = Body(...), user: dict = Depends(current_user)) -> dict[str, Any]:
        user_id = str(user["id"])
        name = re.sub(r"\s+", " ", _text(payload.get("name"))).strip()
        if not name:
            raise HTTPException(status_code=422, detail={"code": "component_category_name_required", "message": "اسم التصنيف مطلوب."})
        existing = await _category_by_name(db, user_id, name)
        if existing:
            raise HTTPException(status_code=409, detail={"code": "component_category_exists", "message": "يوجد تصنيف بالاسم نفسه."})
        now = datetime.now(timezone.utc)
        row = {
            "id": f"ccv2_{uuid.uuid4().hex}",
            "user_id": user_id,
            "name": name,
            "normalized_name": _normalized(name),
            "status": "active",
            "created_at": now,
            "updated_at": now,
        }
        try:
            await db[COMPONENT_CATEGORIES].insert_one(dict(row))
        except Exception as exc:
            if "duplicate" in str(exc).lower():
                raise HTTPException(status_code=409, detail={"code": "component_category_exists", "message": "يوجد تصنيف بالاسم نفسه."}) from exc
            raise
        return {"ok": True, "category": {"id": row["id"], "name": name}}

    async def save_compat_group(user_id: str, payload: dict, group_id: str = "") -> dict[str, Any]:
        category_name = re.sub(r"\s+", " ", _text(payload.get("category"))).strip()
        kind = _text(payload.get("kind"))
        resource_ids = _unique_ids(payload.get("resource_ids"))
        category = await _category_by_name(db, user_id, category_name)
        if not category:
            raise HTTPException(status_code=422, detail={"code": "component_category_not_found", "message": "التصنيف المحدد غير موجود."})
        if kind not in {"service", "stock_component"}:
            raise HTTPException(status_code=422, detail={"code": "invalid_component_group_kind", "message": "نوع المجموعة غير صحيح."})
        rows = await db[RESOURCES].find(
            {"user_id": user_id, "id": {"$in": resource_ids}},
            {"_id": 0},
        ).to_list(length=500)
        canonical_kind = _group_kind(kind)
        validate_group_members(
            rows,
            resource_ids=resource_ids,
            category_id=_text(category.get("id")),
            group_kind=canonical_kind,
        )
        signature = "|".join(sorted(resource_ids))
        duplicate_selector: dict[str, Any] = {
            "user_id": user_id,
            "category_id": _text(category.get("id")),
            "group_kind": canonical_kind,
            "member_signature": signature,
        }
        if group_id:
            duplicate_selector["id"] = {"$ne": group_id}
        duplicate = await db[COMPONENT_GROUPS].find_one(duplicate_selector, {"_id": 0, "id": 1})
        if duplicate:
            raise HTTPException(status_code=409, detail={"code": "component_group_exists", "message": "يوجد قروب بنفس العناصر."})
        now = datetime.now(timezone.utc)
        patch = {
            "category_id": _text(category.get("id")),
            "group_kind": canonical_kind,
            "resource_ids": resource_ids,
            "member_signature": signature,
            "status": "active",
            "updated_at": now,
        }
        if group_id:
            selector = {"user_id": user_id, "id": group_id}
            current = await db[COMPONENT_GROUPS].find_one(selector, {"_id": 0})
            if not current:
                raise HTTPException(status_code=404, detail={"code": "component_group_not_found", "message": "القروب غير موجود."})
            await db[COMPONENT_GROUPS].update_one(selector, {"$set": patch})
            saved = {**current, **patch}
        else:
            saved = {
                "id": f"cgv2_{uuid.uuid4().hex}",
                "user_id": user_id,
                "created_at": now,
                **patch,
            }
            await db[COMPONENT_GROUPS].insert_one(dict(saved))
        saved["name"] = generated_group_name(rows, resource_ids)
        return saved

    @router.post("/groups")
    async def create_group(payload: dict = Body(...), user: dict = Depends(current_user)) -> dict[str, Any]:
        saved = await save_compat_group(str(user["id"]), payload)
        return {"ok": True, "group": saved}

    @router.put("/groups/{group_id}")
    async def update_group(group_id: str, payload: dict = Body(...), user: dict = Depends(current_user)) -> dict[str, Any]:
        saved = await save_compat_group(str(user["id"]), payload, _text(group_id))
        return {"ok": True, "group": saved}

    return router
