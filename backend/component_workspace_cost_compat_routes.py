"""Compatibility workspace for component costs.

Older component rows may store their editable amount in different fields.  The
frontend contract must always receive ``reference_cost.amount`` so the edit
modal opens with the current value instead of an empty input.
"""
from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter, Depends

from product_option_cost_routes import BINDINGS, RESOURCES, _serialize, ensure_indexes
from product_v2_routes import PRODUCTS, _number


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


def make_component_workspace_cost_compat_router(db: Any, current_user: Callable[..., Any]) -> APIRouter:
    router = APIRouter(tags=["Mezan Component Cost Compatibility"])

    @router.get("/components-v2/workspace")
    async def component_workspace(user: dict = Depends(current_user)) -> dict[str, Any]:
        await ensure_indexes(db)
        user_id = str(user["id"])
        resources = await db[RESOURCES].find(
            {"user_id": user_id}, {"_id": 0}
        ).sort("name", 1).to_list(length=1000)
        bindings = await db[BINDINGS].find(
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
                "product_id": (
                    product.get("mezan_product_id")
                    if product
                    else binding.get("salla_product_id")
                ),
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

        component_rows = []
        for resource in resources:
            row = _serialize(resource) or {}
            amount = _current_cost(row)
            row.update({
                "track_inventory": bool(row.get("track_inventory")),
                "editable_cost": amount,
                "reference_cost": {
                    "amount": amount,
                    "currency": "SAR",
                    "source": row.get("cost_source") or "mezan_cost_resource_v2",
                },
                "product_usages": by_resource.get(str(row.get("id")), []),
            })
            component_rows.append(row)

        return {
            "components": component_rows,
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
            },
        }

    return router
