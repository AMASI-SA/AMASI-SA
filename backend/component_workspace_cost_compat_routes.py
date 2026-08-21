"""Component workspace with current costs, categories, and reusable groups.

The organization layer is deliberately metadata-only. It classifies the shared
resource catalog and stores reusable bundles without changing product bindings,
order snapshots, supplier invoices, accounting, Salla, or Qoyod.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Literal

from fastapi import APIRouter, Body, Depends, HTTPException
from pymongo import ASCENDING

from component_status_policy import component_is_active, component_status
from product_fulfillment_rules import PRODUCT_RESOURCE_BINDINGS
from product_option_cost_routes import BINDINGS, RESOURCES, _serialize, ensure_indexes
from product_v2_routes import PRODUCTS, _number, _text

COMPONENT_CATEGORIES = "mezan_component_categories_v2"
COMPONENT_GROUPS = "mezan_component_groups_v2"


def _current_cost(resource: dict[str, Any]) -> float | None:
    """Return the current visible/editable cost across all storage generations."""
    candidates = (
        resource.get("unit_cost"),
        resource.get("initial_unit_cost"),
        resource.get("current_cost"),
        resource.get("cost"),
        resource.get("price"),
        (resource.get("reference_cost") or {}).get("amount")
        if isinstance(resource.get("reference_cost"), dict)
        else None,
    )
    for value in candidates:
        parsed = _number(value)
        if parsed is not None:
            return parsed
    return None


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


def _resource_group_kind(resource: dict[str, Any]) -> Literal["service", "component"]:
    return "component" if bool(resource.get("track_inventory")) else "service"


def generated_group_name(resources: list[dict[str, Any]], resource_ids: list[str]) -> str:
    by_id = {_text(row.get("id")): row for row in resources}
    return " - ".join(
        _text(by_id[resource_id].get("name")) or resource_id
        for resource_id in resource_ids
        if resource_id in by_id
    )


def validate_group_members(
    resources: list[dict[str, Any]],
    *,
    resource_ids: list[str],
    category_id: str,
    group_kind: str,
) -> list[dict[str, Any]]:
    ids = _unique_ids(resource_ids)
    if len(ids) < 2:
        raise HTTPException(
            status_code=422,
            detail={"code": "component_group_requires_two_members"},
        )
    if group_kind not in {"service", "component"}:
        raise HTTPException(
            status_code=422,
            detail={"code": "invalid_component_group_kind"},
        )
    by_id = {_text(row.get("id")): row for row in resources}
    missing = [resource_id for resource_id in ids if resource_id not in by_id]
    if missing:
        raise HTTPException(
            status_code=422,
            detail={"code": "component_group_resource_missing", "resource_ids": missing},
        )
    selected = [by_id[resource_id] for resource_id in ids]
    inactive = [
        _text(row.get("id"))
        for row in selected
        if not component_is_active(row)
    ]
    if inactive:
        raise HTTPException(
            status_code=409,
            detail={"code": "component_inactive", "resource_ids": inactive},
        )
    wrong_category = [
        _text(row.get("id"))
        for row in selected
        if category_id not in _unique_ids(row.get("category_ids"))
    ]
    if wrong_category:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "component_group_category_mismatch",
                "resource_ids": wrong_category,
            },
        )
    wrong_kind = [
        _text(row.get("id"))
        for row in selected
        if _resource_group_kind(row) != group_kind
    ]
    if wrong_kind:
        raise HTTPException(
            status_code=422,
            detail={"code": "component_group_kind_mismatch", "resource_ids": wrong_kind},
        )
    return selected


async def _ensure_organization_indexes(db: Any) -> None:
    await db[COMPONENT_CATEGORIES].create_index(
        [("user_id", ASCENDING), ("normalized_name", ASCENDING)],
        unique=True,
        name="uq_component_category_name_v2",
    )
    await db[COMPONENT_GROUPS].create_index(
        [
            ("user_id", ASCENDING),
            ("category_id", ASCENDING),
            ("group_kind", ASCENDING),
            ("member_signature", ASCENDING),
        ],
        unique=True,
        name="uq_component_group_members_v2",
    )
    await db[RESOURCES].create_index(
        [("user_id", ASCENDING), ("category_ids", ASCENDING)],
        name="ix_cost_resource_categories_v2",
    )


def _public_category(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": _text(row.get("id")),
        "name": _text(row.get("name")),
        "status": _text(row.get("status")) or "active",
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


def _group_signature(resource_ids: list[str]) -> str:
    return "|".join(sorted(_unique_ids(resource_ids)))


def make_component_workspace_cost_compat_router(
    db: Any,
    current_user: Callable[..., Any],
) -> APIRouter:
    router = APIRouter(tags=["Mezan Component Organization"])

    async def organization_rows(user_id: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        categories = await db[COMPONENT_CATEGORIES].find(
            {"user_id": user_id}, {"_id": 0, "user_id": 0, "normalized_name": 0}
        ).sort("name", 1).to_list(length=500)
        groups = await db[COMPONENT_GROUPS].find(
            {"user_id": user_id}, {"_id": 0, "user_id": 0}
        ).sort("updated_at", -1).to_list(length=2000)
        return categories, groups

    @router.get("/components-v2/workspace")
    async def component_workspace(user: dict = Depends(current_user)) -> dict[str, Any]:
        await ensure_indexes(db)
        await _ensure_organization_indexes(db)
        user_id = str(user["id"])
        resources = await db[RESOURCES].find(
            {"user_id": user_id}, {"_id": 0}
        ).sort("name", 1).to_list(length=5000)
        bindings = await db[BINDINGS].find(
            {"user_id": user_id}, {"_id": 0}
        ).to_list(length=10000)
        product_bindings = await db[PRODUCT_RESOURCE_BINDINGS].find(
            {"user_id": user_id}, {"_id": 0}
        ).to_list(length=10000)
        products = await db[PRODUCTS].find(
            {"user_id": user_id, "archived": {"$ne": True}},
            {
                "_id": 0,
                "salla_product_id": 1,
                "mezan_product_id": 1,
                "name": 1,
                "sku": 1,
                "options": 1,
            },
        ).to_list(length=5000)
        categories, groups = await organization_rows(user_id)
        products_by_salla = {
            str(row.get("salla_product_id")): row for row in products
        }

        by_resource: dict[str, list[dict[str, Any]]] = {}
        for binding in bindings:
            if binding.get("mode") != "resource" or not binding.get("resource_id"):
                continue
            product = products_by_salla.get(str(binding.get("salla_product_id")))
            usage = {
                "id": binding.get("id"),
                "product_id": product.get("mezan_product_id") if product else binding.get("salla_product_id"),
                "product_name": product.get("name") if product else "منتج غير متوفر",
                "product_sku": product.get("sku") if product else None,
                "quantity": binding.get("quantity") or 1,
                "source": "option",
                "condition": {
                    "option_key": binding.get("option_id"),
                    "value_key": binding.get("value_id"),
                    "option_name": binding.get("option_name"),
                    "value_name": binding.get("value_name"),
                },
            }
            by_resource.setdefault(str(binding.get("resource_id")), []).append(usage)
        for binding in product_bindings:
            if not binding.get("resource_id"):
                continue
            product = products_by_salla.get(str(binding.get("salla_product_id")))
            usage = {
                "id": binding.get("id"),
                "product_id": product.get("mezan_product_id") if product else binding.get("mezan_product_id") or binding.get("salla_product_id"),
                "product_name": product.get("name") if product else binding.get("product_name") or "منتج غير متوفر",
                "product_sku": product.get("sku") if product else None,
                "quantity": binding.get("quantity") or 1,
                "source": "product",
                "condition": None,
            }
            by_resource.setdefault(str(binding.get("resource_id")), []).append(usage)

        component_rows = []
        for resource in resources:
            row = _serialize(resource) or {}
            amount = _current_cost(row)
            row.update({
                "status": component_status(row),
                "is_active": component_is_active(row),
                "track_inventory": bool(row.get("track_inventory")),
                "category_ids": _unique_ids(row.get("category_ids")),
                "editable_cost": amount,
                "reference_cost": {
                    "amount": amount,
                    "currency": "SAR",
                    "source": row.get("cost_source") or "mezan_cost_resource_v2",
                },
                "product_usages": by_resource.get(str(row.get("id")), []),
            })
            component_rows.append(row)

        resources_by_id = {_text(row.get("id")): row for row in component_rows}
        category_rows = []
        for raw in categories:
            row = _public_category(raw)
            row["resource_count"] = sum(
                row["id"] in resource.get("category_ids", [])
                for resource in component_rows
            )
            row["group_count"] = sum(
                _text(group.get("category_id")) == row["id"]
                for group in groups
            )
            category_rows.append(row)

        group_rows = []
        for group in groups:
            resource_ids = _unique_ids(group.get("resource_ids"))
            group_resources = [
                resources_by_id[resource_id]
                for resource_id in resource_ids
                if resource_id in resources_by_id
            ]
            group_rows.append({
                "id": _text(group.get("id")),
                "category_id": _text(group.get("category_id")),
                "group_kind": _text(group.get("group_kind")),
                "resource_ids": resource_ids,
                "name": generated_group_name(component_rows, resource_ids),
                "resources": [{
                    "id": _text(resource.get("id")),
                    "name": _text(resource.get("name")),
                    "code": _text(resource.get("code")) or None,
                    "track_inventory": bool(resource.get("track_inventory")),
                    "status": component_status(resource),
                } for resource in group_resources],
                "available_for_new_links": (
                    len(group_resources) == len(resource_ids)
                    and all(component_is_active(resource) for resource in group_resources)
                ),
                "status": _text(group.get("status")) or "active",
                "created_at": group.get("created_at"),
                "updated_at": group.get("updated_at"),
            })

        return {
            "components": component_rows,
            "categories": category_rows,
            "groups": group_rows,
            "products": [{
                "id": row.get("mezan_product_id"),
                "salla_id": row.get("salla_product_id"),
                "sku": row.get("sku"),
                "name": row.get("name"),
                "options": row.get("options") or [],
            } for row in products],
            "meta": {
                "mode": "production",
                "writes_enabled": True,
                "source": RESOURCES,
                "cost_contract": "current_editable_cost_v2",
                "organization_contract": "component_categories_groups_v1",
                "component_lifecycle_contract": "active_inactive_soft_stop_v1",
                "historical_orders_unchanged": True,
            },
        }

    @router.post("/components-v2/categories", status_code=201)
    async def create_category(
        payload: dict = Body(...),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        await _ensure_organization_indexes(db)
        user_id = str(user["id"])
        name = re.sub(r"\s+", " ", _text(payload.get("name"))).strip()
        if not name:
            raise HTTPException(status_code=422, detail={"code": "component_category_name_required"})
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
                raise HTTPException(status_code=409, detail={"code": "component_category_exists"}) from exc
            raise
        return {"ok": True, "category": _public_category(row)}

    @router.put("/components-v2/categories/{category_id}")
    async def update_category(
        category_id: str,
        payload: dict = Body(...),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        user_id = str(user["id"])
        name = re.sub(r"\s+", " ", _text(payload.get("name"))).strip()
        if not name:
            raise HTTPException(status_code=422, detail={"code": "component_category_name_required"})
        selector = {"user_id": user_id, "id": _text(category_id)}
        before = await db[COMPONENT_CATEGORIES].find_one(selector, {"_id": 0})
        if not before:
            raise HTTPException(status_code=404, detail={"code": "component_category_not_found"})
        now = datetime.now(timezone.utc)
        patch = {"name": name, "normalized_name": _normalized(name), "updated_at": now}
        try:
            await db[COMPONENT_CATEGORIES].update_one(selector, {"$set": patch})
        except Exception as exc:
            if "duplicate" in str(exc).lower():
                raise HTTPException(status_code=409, detail={"code": "component_category_exists"}) from exc
            raise
        return {"ok": True, "category": _public_category({**before, **patch})}

    @router.put("/components-v2/{resource_id}/categories")
    async def assign_resource_categories(
        resource_id: str,
        payload: dict = Body(...),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        user_id = str(user["id"])
        category_ids = _unique_ids(payload.get("category_ids"))
        resource = await db[RESOURCES].find_one(
            {"user_id": user_id, "id": _text(resource_id)}, {"_id": 0}
        )
        if not resource:
            raise HTTPException(status_code=404, detail={"code": "component_not_found"})
        categories = await db[COMPONENT_CATEGORIES].find(
            {"user_id": user_id, "id": {"$in": category_ids}}, {"_id": 0, "id": 1}
        ).to_list(length=500)
        found = {_text(row.get("id")) for row in categories}
        missing = [category_id for category_id in category_ids if category_id not in found]
        if missing:
            raise HTTPException(
                status_code=422,
                detail={"code": "component_category_not_found", "category_ids": missing},
            )
        protected = await db[COMPONENT_GROUPS].find_one(
            {
                "user_id": user_id,
                "resource_ids": _text(resource_id),
                "category_id": {"$nin": category_ids},
            },
            {"_id": 0, "id": 1, "category_id": 1},
        )
        if protected:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "component_category_used_by_group",
                    "group_id": protected.get("id"),
                    "category_id": protected.get("category_id"),
                },
            )
        await db[RESOURCES].update_one(
            {"user_id": user_id, "id": _text(resource_id)},
            {"$set": {"category_ids": category_ids}},
        )
        return {"ok": True, "resource_id": _text(resource_id), "category_ids": category_ids}

    async def save_group(
        *,
        payload: dict,
        user_id: str,
        group_id: str | None = None,
    ) -> dict[str, Any]:
        category_id = _text(payload.get("category_id"))
        group_kind = _text(payload.get("group_kind"))
        resource_ids = _unique_ids(payload.get("resource_ids"))
        category = await db[COMPONENT_CATEGORIES].find_one(
            {"user_id": user_id, "id": category_id}, {"_id": 0}
        )
        if not category:
            raise HTTPException(status_code=422, detail={"code": "component_category_not_found"})
        resources = await db[RESOURCES].find(
            {"user_id": user_id, "id": {"$in": resource_ids}}, {"_id": 0}
        ).to_list(length=500)
        validate_group_members(
            resources,
            resource_ids=resource_ids,
            category_id=category_id,
            group_kind=group_kind,
        )
        signature = _group_signature(resource_ids)
        duplicate_selector: dict[str, Any] = {
            "user_id": user_id,
            "category_id": category_id,
            "group_kind": group_kind,
            "member_signature": signature,
        }
        if group_id:
            duplicate_selector["id"] = {"$ne": group_id}
        duplicate = await db[COMPONENT_GROUPS].find_one(duplicate_selector, {"_id": 0, "id": 1})
        if duplicate:
            raise HTTPException(
                status_code=409,
                detail={"code": "component_group_exists", "group_id": duplicate.get("id")},
            )
        now = datetime.now(timezone.utc)
        patch = {
            "category_id": category_id,
            "group_kind": group_kind,
            "resource_ids": resource_ids,
            "member_signature": signature,
            "status": "active",
            "updated_at": now,
        }
        if group_id:
            selector = {"user_id": user_id, "id": group_id}
            before = await db[COMPONENT_GROUPS].find_one(selector, {"_id": 0})
            if not before:
                raise HTTPException(status_code=404, detail={"code": "component_group_not_found"})
            await db[COMPONENT_GROUPS].update_one(selector, {"$set": patch})
            saved = {**before, **patch}
        else:
            saved = {
                "id": f"cgv2_{uuid.uuid4().hex}",
                "user_id": user_id,
                "created_at": now,
                **patch,
            }
            await db[COMPONENT_GROUPS].insert_one(dict(saved))
        saved["name"] = generated_group_name(resources, resource_ids)
        return saved

    @router.post("/components-v2/groups", status_code=201)
    async def create_group(
        payload: dict = Body(...),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        saved = await save_group(payload=payload, user_id=str(user["id"]))
        return {"ok": True, "group": _serialize(saved)}

    @router.put("/components-v2/groups/{group_id}")
    async def update_group(
        group_id: str,
        payload: dict = Body(...),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        saved = await save_group(
            payload=payload,
            user_id=str(user["id"]),
            group_id=_text(group_id),
        )
        return {"ok": True, "group": _serialize(saved)}

    return router


__all__ = [
    "COMPONENT_CATEGORIES",
    "COMPONENT_GROUPS",
    "generated_group_name",
    "make_component_workspace_cost_compat_router",
    "validate_group_members",
]
