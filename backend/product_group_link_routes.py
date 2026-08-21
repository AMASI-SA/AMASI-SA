"""Product-level reusable component/service group linking.

A group link expands to the group's unique resources in the existing canonical
product resource binding collection, so cost snapshots and fulfillment keep
using individual resources. The separate group binding records preserve UI
intent and allow safe group removal without deleting manual or other-group
links. Historical order snapshots are never rewritten.
"""
from __future__ import annotations

import uuid
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from pymongo import ASCENDING

from component_workspace_cost_compat_routes import (
    COMPONENT_CATEGORIES,
    COMPONENT_GROUPS,
    generated_group_name,
)
from component_status_policy import component_is_active, require_active_component
from product_cost_revision import bump_product_cost_revision
from product_fulfillment_routes import (
    ProductResourceLinkRequest,
    _operations_view as _base_operations_view,
    _product,
    _product_key,
    ensure_product_fulfillment_indexes,
)
from product_fulfillment_rules import PRODUCT_RESOURCE_BINDINGS
from product_option_cost_routes import AUDIT, BINDINGS, RESOURCES, _now, _serialize
from product_v2_routes import _number, _text

PRODUCT_GROUP_BINDINGS = "mezan_product_group_bindings_v2"


def _unique_ids(values: Any) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()
    for value in values if isinstance(values, list) else []:
        item_id = _text(value)
        if item_id and item_id not in seen:
            seen.add(item_id)
            output.append(item_id)
    return output


class ProductGroupLinkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    group_ids: list[str] = Field(min_length=1, max_length=100)

    @field_validator("group_ids")
    @classmethod
    def normalize_group_ids(cls, values: list[str]) -> list[str]:
        result = _unique_ids(values)
        if not result:
            raise ValueError("product_group_required")
        return result


async def ensure_product_group_indexes(db: Any) -> None:
    await db[PRODUCT_GROUP_BINDINGS].create_index(
        [
            ("user_id", ASCENDING),
            ("salla_product_id", ASCENDING),
            ("group_id", ASCENDING),
        ],
        unique=True,
        name="uq_product_group_binding_v2",
    )
    await db[PRODUCT_GROUP_BINDINGS].create_index(
        [("user_id", ASCENDING), ("group_id", ASCENDING)],
        name="ix_product_group_usage_v2",
    )


def _manual_link_value(binding: dict[str, Any] | None) -> bool:
    if not binding:
        return False
    # Existing rows predate group provenance and are therefore manual links.
    return binding.get("manual_link", True) is not False


async def _extended_operations_view(
    db: Any,
    *,
    user_id: str,
    product: dict[str, Any],
) -> dict[str, Any]:
    view = await _base_operations_view(
        db,
        user_id=user_id,
        product=product,
    )
    salla_id = _product_key(product)
    categories = await db[COMPONENT_CATEGORIES].find(
        {"user_id": user_id, "status": {"$ne": "inactive"}},
        {"_id": 0, "user_id": 0, "normalized_name": 0},
    ).sort("name", 1).to_list(length=500)
    groups = await db[COMPONENT_GROUPS].find(
        {"user_id": user_id, "status": {"$ne": "inactive"}},
        {"_id": 0, "user_id": 0},
    ).sort("updated_at", -1).to_list(length=2000)
    group_bindings = await db[PRODUCT_GROUP_BINDINGS].find(
        {"user_id": user_id, "salla_product_id": salla_id},
        {"_id": 0},
    ).sort("created_at", 1).to_list(length=1000)
    linked_group_ids = {
        _text(row.get("group_id")) for row in group_bindings
        if _text(row.get("group_id"))
    }
    resource_rows = view.get("resources") or []
    resources_by_id = {
        _text(row.get("id")): row for row in resource_rows
        if _text(row.get("id"))
    }
    product_links_by_resource = {
        _text(row.get("resource_id")): row
        for row in view.get("product_links") or []
        if _text(row.get("resource_id"))
    }
    for row in resource_rows:
        resource_id = _text(row.get("id"))
        binding = product_links_by_resource.get(resource_id)
        group_ids = _unique_ids((binding or {}).get("group_ids"))
        row["category_ids"] = _unique_ids(row.get("category_ids"))
        row["group_ids"] = group_ids
        row["manual_link"] = _manual_link_value(binding)
        row["group_managed"] = bool(group_ids)

    group_rows: list[dict[str, Any]] = []
    for raw in groups:
        resource_ids = _unique_ids(raw.get("resource_ids"))
        group_resources = [
            resources_by_id[resource_id]
            for resource_id in resource_ids
            if resource_id in resources_by_id
        ]
        group_id = _text(raw.get("id"))
        all_members_active = (
            len(group_resources) == len(resource_ids)
            and all(component_is_active(resource) for resource in group_resources)
        )
        group_rows.append({
            "id": group_id,
            "category_id": _text(raw.get("category_id")),
            "group_kind": _text(raw.get("group_kind")),
            "resource_ids": resource_ids,
            "name": generated_group_name(resource_rows, resource_ids),
            "resources": [{
                "id": _text(resource.get("id")),
                "name": _text(resource.get("name")),
                "code": _text(resource.get("code")) or None,
                "track_inventory": bool(resource.get("track_inventory")),
            } for resource in group_resources],
            "linked_to_product": group_id in linked_group_ids,
            "available_for_product_link": all_members_active,
            "status": _text(raw.get("status")) or "active",
        })

    view.update({
        "categories": [{
            "id": _text(row.get("id")),
            "name": _text(row.get("name")),
            "status": _text(row.get("status")) or "active",
        } for row in categories],
        "groups": group_rows,
        "product_group_links": [
            _serialize(row) for row in group_bindings
        ],
    })
    view.setdefault("rules", {}).update({
        "group_picker_requires_category": True,
        "group_resources_are_deduplicated": True,
        "group_unlink_preserves_manual_and_other_group_links": True,
        "historical_order_snapshots_unchanged": True,
    })
    return view


async def _group_rows(
    db: Any,
    *,
    user_id: str,
    group_ids: list[str],
) -> list[dict[str, Any]]:
    groups = await db[COMPONENT_GROUPS].find(
        {
            "user_id": user_id,
            "id": {"$in": group_ids},
            "status": {"$ne": "inactive"},
        },
        {"_id": 0},
    ).to_list(length=100)
    by_id = {_text(row.get("id")): row for row in groups}
    missing = [group_id for group_id in group_ids if group_id not in by_id]
    if missing:
        raise HTTPException(
            status_code=422,
            detail={"code": "component_group_not_found", "group_ids": missing},
        )
    return [by_id[group_id] for group_id in group_ids]


async def _validate_no_option_conflicts(
    db: Any,
    *,
    user_id: str,
    salla_id: str,
    resource_ids: list[str],
) -> None:
    conflicts = await db[BINDINGS].find(
        {
            "user_id": user_id,
            "salla_product_id": salla_id,
            "mode": "resource",
            "resource_id": {"$in": resource_ids},
        },
        {
            "_id": 0,
            "resource_id": 1,
            "option_id": 1,
            "option_name": 1,
            "value_id": 1,
            "value_name": 1,
        },
    ).to_list(length=500)
    if conflicts:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "group_resource_already_linked_to_option",
                "conflicts": conflicts,
            },
        )


def make_product_group_link_router(
    db: Any,
    current_user: Callable[..., Any],
) -> APIRouter:
    router = APIRouter(prefix="/products-v2", tags=["Product V2 Group Links"])

    @router.get("/{product_id}/operations")
    async def get_product_operations(
        product_id: str,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        await ensure_product_fulfillment_indexes(db)
        await ensure_product_group_indexes(db)
        user_id = str(user["id"])
        product = await _product(db, user_id, product_id)
        return await _extended_operations_view(
            db,
            user_id=user_id,
            product=product,
        )

    @router.put("/{product_id}/resource-links/{resource_id}")
    async def link_resource_to_product(
        product_id: str,
        resource_id: str,
        payload: ProductResourceLinkRequest,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        await ensure_product_fulfillment_indexes(db)
        await ensure_product_group_indexes(db)
        user_id = str(user["id"])
        product = await _product(db, user_id, product_id)
        resource_id = _text(resource_id)
        resource = await db[RESOURCES].find_one(
            {"user_id": user_id, "id": resource_id},
            {"_id": 0},
        )
        require_active_component(resource)
        salla_id = _product_key(product)
        await _validate_no_option_conflicts(
            db,
            user_id=user_id,
            salla_id=salla_id,
            resource_ids=[resource_id],
        )
        now = _now()
        selector = {
            "user_id": user_id,
            "salla_product_id": salla_id,
            "resource_id": resource_id,
        }
        await db[PRODUCT_RESOURCE_BINDINGS].update_one(
            selector,
            {
                "$set": {
                    "mezan_product_id": (
                        product.get("mezan_product_id") or product.get("id")
                    ),
                    "product_name": product.get("name"),
                    "resource_name": resource.get("name"),
                    "quantity": float(payload.quantity),
                    "manual_link": True,
                    "updated_at": now,
                },
                "$setOnInsert": {
                    "id": uuid.uuid4().hex,
                    "group_ids": [],
                    "created_at": now,
                },
            },
            upsert=True,
        )
        await db[AUDIT].insert_one({
            "id": uuid.uuid4().hex,
            "user_id": user_id,
            "event_type": "product_resource_link_saved",
            "salla_product_id": salla_id,
            "resource_id": resource_id,
            "link_source": "manual",
            "quantity": float(payload.quantity),
            "created_at": now,
        })
        await bump_product_cost_revision(db, user_id)
        return {
            "ok": True,
            **(
                await _extended_operations_view(
                    db,
                    user_id=user_id,
                    product=product,
                )
            ),
        }

    @router.delete("/{product_id}/resource-links/{resource_id}")
    async def unlink_resource_from_product(
        product_id: str,
        resource_id: str,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        await ensure_product_group_indexes(db)
        user_id = str(user["id"])
        product = await _product(db, user_id, product_id)
        selector = {
            "user_id": user_id,
            "salla_product_id": _product_key(product),
            "resource_id": _text(resource_id),
        }
        binding = await db[PRODUCT_RESOURCE_BINDINGS].find_one(selector, {"_id": 0})
        if not binding:
            raise HTTPException(
                status_code=404,
                detail={"code": "product_resource_link_not_found"},
            )
        group_ids = _unique_ids(binding.get("group_ids"))
        manual_link = _manual_link_value(binding)
        if not manual_link and group_ids:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "resource_link_managed_by_group",
                    "group_ids": group_ids,
                },
            )
        if group_ids:
            await db[PRODUCT_RESOURCE_BINDINGS].update_one(
                selector,
                {"$set": {"manual_link": False, "updated_at": _now()}},
            )
        else:
            await db[PRODUCT_RESOURCE_BINDINGS].delete_one(selector)
        await db[AUDIT].insert_one({
            "id": uuid.uuid4().hex,
            "user_id": user_id,
            "event_type": "product_resource_link_deleted",
            "salla_product_id": _product_key(product),
            "resource_id": _text(resource_id),
            "preserved_by_groups": group_ids,
            "created_at": _now(),
        })
        await bump_product_cost_revision(db, user_id)
        return {
            "ok": True,
            **(
                await _extended_operations_view(
                    db,
                    user_id=user_id,
                    product=product,
                )
            ),
        }

    @router.post("/{product_id}/group-links")
    async def link_groups_to_product(
        product_id: str,
        payload: ProductGroupLinkRequest,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        await ensure_product_fulfillment_indexes(db)
        await ensure_product_group_indexes(db)
        user_id = str(user["id"])
        product = await _product(db, user_id, product_id)
        salla_id = _product_key(product)
        group_ids = _unique_ids(payload.group_ids)
        groups = await _group_rows(
            db,
            user_id=user_id,
            group_ids=group_ids,
        )
        resource_group_ids: dict[str, list[str]] = {}
        for group in groups:
            group_id = _text(group.get("id"))
            for resource_id in _unique_ids(group.get("resource_ids")):
                resource_group_ids.setdefault(resource_id, []).append(group_id)
        resource_ids = list(resource_group_ids)
        resources = await db[RESOURCES].find(
            {"user_id": user_id, "id": {"$in": resource_ids}},
            {"_id": 0},
        ).to_list(length=1000)
        resources_by_id = {_text(row.get("id")): row for row in resources}
        missing_resources = [
            resource_id for resource_id in resource_ids
            if resource_id not in resources_by_id
        ]
        if missing_resources:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "component_group_resource_missing",
                    "resource_ids": missing_resources,
                },
            )
        inactive_resources = [
            resource_id
            for resource_id, resource in resources_by_id.items()
            if not component_is_active(resource)
        ]
        if inactive_resources:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "component_inactive",
                    "resource_ids": inactive_resources,
                },
            )
        await _validate_no_option_conflicts(
            db,
            user_id=user_id,
            salla_id=salla_id,
            resource_ids=resource_ids,
        )
        now = _now()
        for group in groups:
            group_id = _text(group.get("id"))
            await db[PRODUCT_GROUP_BINDINGS].update_one(
                {
                    "user_id": user_id,
                    "salla_product_id": salla_id,
                    "group_id": group_id,
                },
                {
                    "$set": {
                        "mezan_product_id": (
                            product.get("mezan_product_id") or product.get("id")
                        ),
                        "product_name": product.get("name"),
                        "category_id": _text(group.get("category_id")),
                        "group_kind": _text(group.get("group_kind")),
                        "resource_ids": _unique_ids(group.get("resource_ids")),
                        "updated_at": now,
                    },
                    "$setOnInsert": {
                        "id": uuid.uuid4().hex,
                        "created_at": now,
                    },
                },
                upsert=True,
            )
        for resource_id, source_group_ids in resource_group_ids.items():
            resource = resources_by_id[resource_id]
            await db[PRODUCT_RESOURCE_BINDINGS].update_one(
                {
                    "user_id": user_id,
                    "salla_product_id": salla_id,
                    "resource_id": resource_id,
                },
                {
                    "$set": {
                        "mezan_product_id": (
                            product.get("mezan_product_id") or product.get("id")
                        ),
                        "product_name": product.get("name"),
                        "resource_name": resource.get("name"),
                        "updated_at": now,
                    },
                    "$addToSet": {
                        "group_ids": {"$each": source_group_ids},
                    },
                    "$setOnInsert": {
                        "id": uuid.uuid4().hex,
                        "quantity": 1.0,
                        "manual_link": False,
                        "created_at": now,
                    },
                },
                upsert=True,
            )
        await db[AUDIT].insert_one({
            "id": uuid.uuid4().hex,
            "user_id": user_id,
            "event_type": "product_groups_linked",
            "salla_product_id": salla_id,
            "group_ids": group_ids,
            "resource_ids": resource_ids,
            "created_at": now,
        })
        await bump_product_cost_revision(db, user_id)
        return {
            "ok": True,
            **(
                await _extended_operations_view(
                    db,
                    user_id=user_id,
                    product=product,
                )
            ),
        }

    @router.delete("/{product_id}/group-links/{group_id}")
    async def unlink_group_from_product(
        product_id: str,
        group_id: str,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        await ensure_product_group_indexes(db)
        user_id = str(user["id"])
        product = await _product(db, user_id, product_id)
        salla_id = _product_key(product)
        group_id = _text(group_id)
        group_binding_selector = {
            "user_id": user_id,
            "salla_product_id": salla_id,
            "group_id": group_id,
        }
        group_binding = await db[PRODUCT_GROUP_BINDINGS].find_one(
            group_binding_selector,
            {"_id": 0},
        )
        if not group_binding:
            raise HTTPException(
                status_code=404,
                detail={"code": "product_group_link_not_found"},
            )
        await db[PRODUCT_GROUP_BINDINGS].delete_one(group_binding_selector)
        for resource_id in _unique_ids(group_binding.get("resource_ids")):
            selector = {
                "user_id": user_id,
                "salla_product_id": salla_id,
                "resource_id": resource_id,
            }
            binding = await db[PRODUCT_RESOURCE_BINDINGS].find_one(selector, {"_id": 0})
            if not binding:
                continue
            remaining_group_ids = [
                value for value in _unique_ids(binding.get("group_ids"))
                if value != group_id
            ]
            if remaining_group_ids or _manual_link_value(binding):
                await db[PRODUCT_RESOURCE_BINDINGS].update_one(
                    selector,
                    {
                        "$set": {
                            "group_ids": remaining_group_ids,
                            "updated_at": _now(),
                        },
                    },
                )
            else:
                await db[PRODUCT_RESOURCE_BINDINGS].delete_one(selector)
        await db[AUDIT].insert_one({
            "id": uuid.uuid4().hex,
            "user_id": user_id,
            "event_type": "product_group_unlinked",
            "salla_product_id": salla_id,
            "group_id": group_id,
            "created_at": _now(),
        })
        await bump_product_cost_revision(db, user_id)
        return {
            "ok": True,
            **(
                await _extended_operations_view(
                    db,
                    user_id=user_id,
                    product=product,
                )
            ),
        }

    return router


__all__ = [
    "PRODUCT_GROUP_BINDINGS",
    "ProductGroupLinkRequest",
    "_manual_link_value",
    "_unique_ids",
    "ensure_product_group_indexes",
    "make_product_group_link_router",
]
