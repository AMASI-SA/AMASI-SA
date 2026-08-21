"""Product-level resources and fulfillment profiles for Product V2."""
from __future__ import annotations

import uuid
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from pymongo import ASCENDING

from component_status_policy import (
    active_component_selector,
    component_is_active,
    component_status,
    require_active_component,
)
from product_fulfillment_rules import (
    DEFAULT_LOW_STOCK_THRESHOLD,
    FROZEN_FULFILLMENT_TYPE,
    FROZEN_INVENTORY_POLICY,
    FULFILLMENT_TYPES,
    INVENTORY_POLICIES,
    PRODUCT_OPERATION_CHOICES_FROZEN,
    PRODUCT_OPERATION_PROFILES,
    PRODUCT_RESOURCE_BINDINGS,
    STOCKOUT_POLICIES,
    STOCKOUT_POLICY_CLOSE,
    inventory_policy_for_fulfillment,
    normalize_low_stock_threshold,
    normalize_inventory_policy,
    normalize_stockout_policy,
    normalize_fulfillment_type,
)
from product_option_cost_routes import AUDIT, BINDINGS, RESOURCES, _now, _serialize
from product_v2_routes import PRODUCTS, _number


class ProductOperationProfileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fulfillment_type: str
    inventory_policy: str
    stockout_policy: str = STOCKOUT_POLICY_CLOSE
    low_stock_threshold: int = Field(
        default=DEFAULT_LOW_STOCK_THRESHOLD,
        ge=0,
        le=100000,
    )

    @model_validator(mode="before")
    @classmethod
    def discard_legacy_product_warehouse(cls, value: Any) -> Any:
        if not isinstance(value, dict) or "warehouse_id" not in value:
            return value
        sanitized = dict(value)
        sanitized.pop("warehouse_id", None)
        return sanitized


class ProductResourceLinkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    quantity: float = Field(default=1.0, gt=0, le=100000)


async def ensure_product_fulfillment_indexes(db: Any) -> None:
    await db[PRODUCT_RESOURCE_BINDINGS].create_index(
        [
            ("user_id", ASCENDING),
            ("salla_product_id", ASCENDING),
            ("resource_id", ASCENDING),
        ],
        unique=True,
        name="uq_product_resource_binding_v2",
    )
    await db[PRODUCT_OPERATION_PROFILES].create_index(
        [("user_id", ASCENDING), ("salla_product_id", ASCENDING)],
        unique=True,
        name="uq_product_operation_profile_v2",
    )


async def _product(db: Any, user_id: str, product_id: str) -> dict[str, Any]:
    row = await db[PRODUCTS].find_one(
        {
            "user_id": user_id,
            "$or": [
                {"id": product_id},
                {"mezan_product_id": product_id},
                {"salla_product_id": product_id},
            ],
        },
        {"_id": 0},
    )
    if not row:
        raise HTTPException(
            status_code=404,
            detail={"code": "product_v2_not_found"},
        )
    return row


def _product_key(product: dict[str, Any]) -> str:
    value = product.get("salla_product_id")
    if value in (None, ""):
        value = product.get("mezan_product_id") or product.get("id")
    return str(value or "")


async def _operations_view(
    db: Any,
    *,
    user_id: str,
    product: dict[str, Any],
) -> dict[str, Any]:
    salla_id = _product_key(product)
    profile = await db[PRODUCT_OPERATION_PROFILES].find_one(
        {"user_id": user_id, "salla_product_id": salla_id},
        {"_id": 0},
    )
    product_links = await db[PRODUCT_RESOURCE_BINDINGS].find(
        {"user_id": user_id, "salla_product_id": salla_id},
        {"_id": 0},
    ).sort("created_at", 1).to_list(length=1000)
    linked_resource_ids = {
        str(row.get("resource_id"))
        for row in product_links
        if row.get("resource_id")
    }
    option_links = await db[BINDINGS].find(
        {
            "user_id": user_id,
            "salla_product_id": salla_id,
            "mode": "resource",
            "resource_id": {"$nin": [None, ""]},
        },
        {"_id": 0},
    ).to_list(length=5000)
    preserved_resource_ids = linked_resource_ids | {
        str(row.get("resource_id"))
        for row in option_links
        if row.get("mode") == "resource" and row.get("resource_id")
    }
    resources = await db[RESOURCES].find(
        {
            "user_id": user_id,
            "$or": [
                active_component_selector(),
                {"id": {"$in": sorted(preserved_resource_ids)}},
            ],
        },
        {"_id": 0},
    ).sort("name", 1).to_list(length=2000)
    product_by_resource = {
        str(row.get("resource_id")): row for row in product_links
    }
    option_by_resource: dict[str, list[dict[str, Any]]] = {}
    for row in option_links:
        option_by_resource.setdefault(str(row.get("resource_id")), []).append({
            "binding_id": row.get("id"),
            "option_id": row.get("option_id"),
            "option_name": row.get("option_name"),
            "value_id": row.get("value_id"),
            "value_name": row.get("value_name"),
        })

    resource_rows = []
    for raw in resources:
        row = _serialize(raw) or {}
        resource_id = str(row.get("id"))
        product_link = product_by_resource.get(resource_id)
        option_conflicts = option_by_resource.get(resource_id, [])
        row.update({
            "status": component_status(row),
            "is_active": component_is_active(row),
            "linked_to_product": bool(product_link),
            "product_quantity": (
                _number(product_link.get("quantity"))
                if product_link
                else None
            ),
            "option_links": option_conflicts,
            "available_for_product_link": (
                component_is_active(row) and not bool(option_conflicts)
            ),
            "available_for_option_link": (
                component_is_active(row) and not bool(product_link)
            ),
        })
        resource_rows.append(row)

    serialized_profile = _serialize(profile) or {
        "fulfillment_type": None,
        "inventory_policy": None,
        "configured": False,
    }
    if (
        serialized_profile.get("fulfillment_type") in FULFILLMENT_TYPES
        and serialized_profile.get("inventory_policy") not in INVENTORY_POLICIES
    ):
        serialized_profile["inventory_policy"] = (
            inventory_policy_for_fulfillment(
                serialized_profile["fulfillment_type"]
            )["mode"]
        )
        serialized_profile["inventory_policy_inferred_from_legacy"] = True
    if serialized_profile.get("stockout_policy") not in STOCKOUT_POLICIES:
        serialized_profile["stockout_policy"] = STOCKOUT_POLICY_CLOSE
        serialized_profile["stockout_policy_inferred_from_legacy"] = True
    try:
        serialized_profile["low_stock_threshold"] = (
            normalize_low_stock_threshold(
                serialized_profile.get(
                    "low_stock_threshold",
                    DEFAULT_LOW_STOCK_THRESHOLD,
                )
            )
        )
    except ValueError:
        serialized_profile["low_stock_threshold"] = (
            DEFAULT_LOW_STOCK_THRESHOLD
        )
    # Legacy product-level warehouse values are intentionally hidden and
    # ignored. Warehouse resolution belongs to inventory/order-item routing.
    serialized_profile.pop("warehouse_id", None)
    if PRODUCT_OPERATION_CHOICES_FROZEN:
        serialized_profile.update({
            "fulfillment_type": FROZEN_FULFILLMENT_TYPE,
            "inventory_policy": FROZEN_INVENTORY_POLICY,
            "stockout_policy": STOCKOUT_POLICY_CLOSE,
            "low_stock_threshold": DEFAULT_LOW_STOCK_THRESHOLD,
            "configured": True,
            "operation_choices_frozen": True,
        })

    return {
        "product": {
            "mezan_product_id": (
                product.get("mezan_product_id") or product.get("id")
            ),
            "salla_product_id": product.get("salla_product_id"),
            "name": product.get("name"),
            "sku": product.get("sku"),
        },
        "profile": serialized_profile,
        "product_links": [
            _serialize(row) for row in product_links
        ],
        "resources": resource_rows,
        "rules": {
            "same_resource_cannot_link_at_product_and_option": True,
            "component_alone_requires_preparation": False,
            "service_can_force_preparation": True,
            "default_when_unconfigured": "requires_preparation",
            "operation_choices_frozen": (
                PRODUCT_OPERATION_CHOICES_FROZEN
            ),
            "inventory_and_preparation_are_independent": True,
            "stockout_policy_applies_to_tracked_products_only": True,
            "product_service_applies_to_every_order": True,
            "warehouse_resolution": [
                "inventory_location",
            ],
            "employee_assignment_role": "visibility_and_permissions_only",
        },
    }


def make_product_fulfillment_router(
    db: Any,
    current_user: Callable[..., Any],
) -> APIRouter:
    router = APIRouter(
        prefix="/products-v2",
        tags=["Product V2 Fulfillment"],
    )

    @router.get("/{product_id}/operations")
    async def get_product_operations(
        product_id: str,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        await ensure_product_fulfillment_indexes(db)
        user_id = str(user["id"])
        product = await _product(db, user_id, product_id)
        return await _operations_view(
            db,
            user_id=user_id,
            product=product,
        )

    @router.put("/{product_id}/operations/profile")
    async def save_product_operation_profile(
        product_id: str,
        payload: ProductOperationProfileRequest,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        await ensure_product_fulfillment_indexes(db)
        user_id = str(user["id"])
        product = await _product(db, user_id, product_id)
        if PRODUCT_OPERATION_CHOICES_FROZEN:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "product_operation_choices_frozen",
                    "message": (
                        "خيارات تشغيل المنتج مجمّدة مؤقتًا؛ "
                        "جميع المنتجات تحتاج تجهيزًا."
                    ),
                },
            )
        try:
            fulfillment_type = normalize_fulfillment_type(
                payload.fulfillment_type
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "invalid_fulfillment_type",
                    "allowed": sorted(FULFILLMENT_TYPES),
                },
            ) from exc
        try:
            stockout_policy = normalize_stockout_policy(
                payload.stockout_policy
            )
            low_stock_threshold = normalize_low_stock_threshold(
                payload.low_stock_threshold
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": str(exc),
                    "allowed_stockout_policies": sorted(
                        STOCKOUT_POLICIES
                    ),
                },
            ) from exc
        try:
            inventory_policy = normalize_inventory_policy(
                payload.inventory_policy
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "invalid_inventory_policy",
                    "allowed": sorted(INVENTORY_POLICIES),
                },
            ) from exc
        salla_id = _product_key(product)
        selector = {
            "user_id": user_id,
            "salla_product_id": salla_id,
        }
        before = await db[PRODUCT_OPERATION_PROFILES].find_one(
            selector,
            {"_id": 0},
        )
        now = _now()
        patch = {
            **selector,
            "mezan_product_id": (
                product.get("mezan_product_id") or product.get("id")
            ),
            "fulfillment_type": fulfillment_type,
            "inventory_policy": inventory_policy,
            "stockout_policy": stockout_policy,
            "low_stock_threshold": low_stock_threshold,
            "configured": True,
            "updated_at": now,
            "updated_by": str(user.get("id") or ""),
        }
        await db[PRODUCT_OPERATION_PROFILES].update_one(
            selector,
            {
                "$set": patch,
                "$unset": {"warehouse_id": ""},
                "$setOnInsert": {
                    "id": uuid.uuid4().hex,
                    "created_at": now,
                },
            },
            upsert=True,
        )
        await db[AUDIT].insert_one({
            "id": uuid.uuid4().hex,
            "user_id": user_id,
            "event_type": "product_operation_profile_saved",
            "salla_product_id": salla_id,
            "before": before,
            "after": patch,
            "created_at": now,
        })
        return {
            "ok": True,
            **(
                await _operations_view(
                    db,
                    user_id=user_id,
                    product=product,
                )
            ),
        }

    @router.put("/{product_id}/resource-links/{resource_id}")
    async def link_resource_to_product(
        product_id: str,
        resource_id: str,
        payload: ProductResourceLinkRequest,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        await ensure_product_fulfillment_indexes(db)
        user_id = str(user["id"])
        product = await _product(db, user_id, product_id)
        resource = await db[RESOURCES].find_one(
            {"user_id": user_id, "id": resource_id},
            {"_id": 0},
        )
        require_active_component(resource)
        salla_id = _product_key(product)
        option_conflict = await db[BINDINGS].find_one(
            {
                "user_id": user_id,
                "salla_product_id": salla_id,
                "mode": "resource",
                "resource_id": resource_id,
            },
            {
                "_id": 0,
                "option_id": 1,
                "option_name": 1,
                "value_id": 1,
                "value_name": 1,
            },
        )
        if option_conflict:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "resource_already_linked_to_option",
                    "option": option_conflict,
                },
            )
        now = _now()
        selector = {
            "user_id": user_id,
            "salla_product_id": salla_id,
            "resource_id": resource_id,
        }
        patch = {
            **selector,
            "mezan_product_id": (
                product.get("mezan_product_id") or product.get("id")
            ),
            "product_name": product.get("name"),
            "resource_name": resource.get("name"),
            "quantity": float(payload.quantity),
            "updated_at": now,
        }
        await db[PRODUCT_RESOURCE_BINDINGS].update_one(
            selector,
            {
                "$set": patch,
                "$setOnInsert": {
                    "id": uuid.uuid4().hex,
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
            "quantity": float(payload.quantity),
            "created_at": now,
        })
        return {
            "ok": True,
            **(
                await _operations_view(
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
        await ensure_product_fulfillment_indexes(db)
        user_id = str(user["id"])
        product = await _product(db, user_id, product_id)
        result = await db[PRODUCT_RESOURCE_BINDINGS].delete_one({
            "user_id": user_id,
            "salla_product_id": _product_key(product),
            "resource_id": resource_id,
        })
        if not result.deleted_count:
            raise HTTPException(
                status_code=404,
                detail={"code": "product_resource_link_not_found"},
            )
        await db[AUDIT].insert_one({
            "id": uuid.uuid4().hex,
            "user_id": user_id,
            "event_type": "product_resource_link_deleted",
            "salla_product_id": _product_key(product),
            "resource_id": resource_id,
            "created_at": _now(),
        })
        return {
            "ok": True,
            **(
                await _operations_view(
                    db,
                    user_id=user_id,
                    product=product,
                )
            ),
        }

    return router


__all__ = [
    "ensure_product_fulfillment_indexes",
    "make_product_fulfillment_router",
]
