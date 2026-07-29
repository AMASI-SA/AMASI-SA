"""Order cost snapshots using the customer's selected product options.

The product base cost is always applied. Conditional option costs are applied only
when the matching option value exists on the order item. Shared resource costs are
resolved at snapshot time so historical order profitability never changes when a
component's current cost is edited later.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException
from pymongo import ASCENDING

from order_engine.repository import MongoOrderRepository
from order_engine.service import OrderNotFoundError, get_order
from product_fulfillment_rules import PRODUCT_RESOURCE_BINDINGS
from product_option_cost_routes import BINDINGS, RESOURCES
from product_v2_details_routes import COST_PROFILES
from product_v2_routes import PRODUCTS, _number

SNAPSHOTS = "mezan_order_item_cost_snapshots_v2"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _norm(value: Any) -> str:
    return " ".join(str(value or "").replace("_", " ").strip().casefold().split())


def selected_option_tokens(item: Any) -> set[tuple[str, str]]:
    """Extract selected (option, value) identities from canonical order item."""
    tokens: set[tuple[str, str]] = set()
    for row in getattr(item, "options_raw", None) or []:
        if not isinstance(row, dict):
            continue
        option = row.get("option") if isinstance(row.get("option"), dict) else {}
        value = row.get("value") if isinstance(row.get("value"), dict) else {}
        option_id = row.get("option_id") or row.get("id") or option.get("id")
        value_id = row.get("value_id") or value.get("id")
        option_name = row.get("option_name") or row.get("name") or option.get("name")
        value_name = row.get("value_name") or value.get("name") or row.get("value")
        if option_id not in (None, "") and value_id not in (None, ""):
            tokens.add((f"id:{option_id}", f"id:{value_id}"))
        if option_name not in (None, "") and value_name not in (None, "", {}):
            tokens.add((f"name:{_norm(option_name)}", f"name:{_norm(value_name)}"))

    normalized = getattr(item, "options_normalized", None) or {}
    if isinstance(normalized, dict):
        for key, value in normalized.items():
            if isinstance(value, list):
                for entry in value:
                    tokens.add((f"name:{_norm(key)}", f"name:{_norm(entry)}"))
            else:
                tokens.add((f"name:{_norm(key)}", f"name:{_norm(value)}"))
    return tokens


def binding_matches(binding: dict[str, Any], tokens: set[tuple[str, str]]) -> bool:
    id_token = (f"id:{binding.get('option_id')}", f"id:{binding.get('value_id')}")
    name_token = (f"name:{_norm(binding.get('option_name'))}", f"name:{_norm(binding.get('value_name'))}")
    return id_token in tokens or name_token in tokens


async def ensure_indexes(db: Any) -> None:
    await db[SNAPSHOTS].create_index(
        [("user_id", ASCENDING), ("order_number", ASCENDING), ("order_item_id", ASCENDING)],
        unique=True,
        name="uq_order_item_cost_snapshot_v2",
    )


async def calculate_order_cost_snapshot(db: Any, *, user_id: str, order: Any) -> dict[str, Any]:
    await ensure_indexes(db)
    product_ids = {str(item.product_id or item.parent_product_id or "").strip() for item in order.items}
    product_ids.discard("")
    products = await db[PRODUCTS].find(
        {"user_id": user_id, "salla_product_id": {"$in": list(product_ids)}},
        {"_id": 0, "salla_product_id": 1, "mezan_product_id": 1, "name": 1},
    ).to_list(length=max(1, len(product_ids)))
    product_map = {str(row.get("salla_product_id")): row for row in products}

    profiles = await db[COST_PROFILES].find(
        {"user_id": user_id, "salla_product_id": {"$in": list(product_ids)}}, {"_id": 0}
    ).to_list(length=max(1, len(product_ids)))
    profile_map = {str(row.get("salla_product_id")): row for row in profiles}

    bindings = await db[BINDINGS].find(
        {"user_id": user_id, "salla_product_id": {"$in": list(product_ids)}}, {"_id": 0}
    ).to_list(length=10000)
    product_bindings = await db[PRODUCT_RESOURCE_BINDINGS].find(
        {
            "user_id": user_id,
            "salla_product_id": {"$in": list(product_ids)},
        },
        {"_id": 0},
    ).to_list(length=10000)
    bindings_by_product: dict[str, list[dict[str, Any]]] = {}
    product_bindings_by_product: dict[str, list[dict[str, Any]]] = {}
    resource_ids = set()
    for row in bindings:
        bindings_by_product.setdefault(str(row.get("salla_product_id")), []).append(row)
        if row.get("mode") == "resource" and row.get("resource_id"):
            resource_ids.add(str(row.get("resource_id")))
    for row in product_bindings:
        product_bindings_by_product.setdefault(
            str(row.get("salla_product_id")),
            [],
        ).append(row)
        if row.get("resource_id"):
            resource_ids.add(str(row.get("resource_id")))
    resources = await db[RESOURCES].find(
        {"user_id": user_id, "id": {"$in": list(resource_ids)}}, {"_id": 0}
    ).to_list(length=max(1, len(resource_ids)))
    resource_map = {str(row.get("id")): row for row in resources}

    now = _now()
    rows = []
    order_total_cost = 0.0
    for item in order.items:
        product_id = str(item.product_id or item.parent_product_id or "").strip()
        quantity = float(item.quantity or 1)
        profile = profile_map.get(product_id, {})
        base_unit_cost = _number(profile.get("base_cost")) or 0.0
        applied_product_resources = []
        product_resource_unit_cost = 0.0
        for binding in product_bindings_by_product.get(product_id, []):
            resource = resource_map.get(str(binding.get("resource_id")), {})
            unit_cost = _number(resource.get("unit_cost")) or 0.0
            amount = unit_cost * (_number(binding.get("quantity")) or 1.0)
            product_resource_unit_cost += amount
            applied_product_resources.append({
                "binding_id": binding.get("id"),
                "quantity": binding.get("quantity") or 1,
                "resolved_amount": round(amount, 4),
                "resource": {
                    "id": resource.get("id"),
                    "code": resource.get("code"),
                    "name": resource.get("name"),
                    "kind": resource.get("kind"),
                    "unit_cost": unit_cost,
                },
            })
        tokens = selected_option_tokens(item)
        applied = []
        option_unit_cost = 0.0
        for binding in bindings_by_product.get(product_id, []):
            if not binding_matches(binding, tokens):
                continue
            amount = _number(binding.get("direct_amount")) or 0.0
            resource_snapshot = None
            if binding.get("mode") == "resource":
                resource = resource_map.get(str(binding.get("resource_id")), {})
                unit_cost = _number(resource.get("unit_cost")) or 0.0
                amount = unit_cost * (_number(binding.get("quantity")) or 1.0)
                resource_snapshot = {
                    "id": resource.get("id"), "code": resource.get("code"),
                    "name": resource.get("name"), "unit_cost": unit_cost,
                }
            option_unit_cost += amount
            applied.append({
                "binding_id": binding.get("id"),
                "option_id": binding.get("option_id"), "option_name": binding.get("option_name"),
                "value_id": binding.get("value_id"), "value_name": binding.get("value_name"),
                "mode": binding.get("mode"), "quantity": binding.get("quantity") or 1,
                "resolved_amount": round(amount, 4), "resource": resource_snapshot,
            })
        unit_cost = (
            base_unit_cost
            + product_resource_unit_cost
            + option_unit_cost
        )
        line_cost = unit_cost * quantity
        order_total_cost += line_cost
        snapshot = {
            "id": uuid.uuid4().hex,
            "user_id": user_id,
            "order_number": order.order_number,
            "order_id": order.order_id,
            "order_item_id": item.order_item_id,
            "source_item_id": item.source_item_id,
            "salla_product_id": product_id or None,
            "mezan_product_id": (product_map.get(product_id) or {}).get("mezan_product_id"),
            "product_name": item.name,
            "sku": item.sku,
            "quantity": quantity,
            "base_unit_cost": round(base_unit_cost, 4),
            "product_resource_unit_cost": round(
                product_resource_unit_cost,
                4,
            ),
            "option_unit_cost": round(option_unit_cost, 4),
            "unit_cost": round(unit_cost, 4),
            "line_cost": round(line_cost, 4),
            "selected_options": [list(token) for token in sorted(tokens)],
            "applied_product_resources": applied_product_resources,
            "applied_option_costs": applied,
            "cost_authority": "mezan_v2_snapshot",
            "calculated_at": now,
        }
        selector = {"user_id": user_id, "order_number": order.order_number, "order_item_id": item.order_item_id}
        await db[SNAPSHOTS].update_one(
            selector,
            {"$set": {**snapshot, "updated_at": now}, "$setOnInsert": {"created_at": now}},
            upsert=True,
        )
        rows.append(snapshot)
    return {
        "order_number": order.order_number,
        "currency": "SAR",
        "total_cost": round(order_total_cost, 4),
        "items": rows,
        "calculated_at": now.isoformat(),
    }


def make_order_option_cost_snapshot_router(db: Any, current_user: Callable[..., Any]) -> APIRouter:
    router = APIRouter(prefix="/orders-v2", tags=["Mezan Order Cost Snapshots"])

    @router.post("/{order_number}/cost-snapshot")
    async def rebuild_cost_snapshot(order_number: str, user: dict = Depends(current_user)) -> dict[str, Any]:
        user_id = str(user["id"])
        try:
            order = await get_order(MongoOrderRepository(db), user_id=user_id, order_number=order_number)
        except OrderNotFoundError as exc:
            raise HTTPException(status_code=404, detail={"code": "order_not_found"}) from exc
        return await calculate_order_cost_snapshot(db, user_id=user_id, order=order)

    @router.get("/{order_number}/cost-snapshot")
    async def get_cost_snapshot(order_number: str, user: dict = Depends(current_user)) -> dict[str, Any]:
        user_id = str(user["id"])
        rows = await db[SNAPSHOTS].find(
            {"user_id": user_id, "order_number": str(order_number)}, {"_id": 0}
        ).sort("order_item_id", 1).to_list(length=1000)
        for row in rows:
            for key in ("created_at", "updated_at", "calculated_at"):
                if hasattr(row.get(key), "isoformat"):
                    row[key] = row[key].isoformat()
        return {
            "order_number": str(order_number),
            "items": rows,
            "total_cost": round(sum((_number(row.get("line_cost")) or 0.0) for row in rows), 4),
            "snapshot_exists": bool(rows),
        }

    return router
