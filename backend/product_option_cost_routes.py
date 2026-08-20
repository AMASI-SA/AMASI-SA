"""Shared component and conditional option-cost engine for Product V2.

Cost contract:
    actual item cost = base product cost + costs for option values selected
    by the customer on that order.

A binding can use either:
* a shared component/service whose unit cost is maintained centrally; or
* a direct fixed amount local to one product option value.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from fastapi import APIRouter, Body, Depends, HTTPException
from pymongo import ASCENDING

from product_cost_revision import bump_product_cost_revision
from product_fulfillment_rules import PRODUCT_RESOURCE_BINDINGS
from product_v2_routes import PRODUCTS, _number, _text

RESOURCES = "mezan_cost_resources_v2"
BINDINGS = "mezan_product_option_cost_bindings_v2"
AUDIT = "mezan_cost_change_log_v2"
OPTION_LEVEL_VALUE_ID = "__option__"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _serialize(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return row
    row = dict(row)
    row.pop("_id", None)
    for key in ("created_at", "updated_at"):
        if hasattr(row.get(key), "isoformat"):
            row[key] = row[key].isoformat()
    return row


async def ensure_indexes(db: Any) -> None:
    await db[RESOURCES].create_index(
        [("user_id", ASCENDING), ("code", ASCENDING)], unique=True,
        name="uq_cost_resource_code_v2",
    )
    await db[BINDINGS].create_index(
        [
            ("user_id", ASCENDING),
            ("salla_product_id", ASCENDING),
            ("option_id", ASCENDING),
            ("value_id", ASCENDING),
        ],
        unique=True,
        name="uq_product_option_value_cost_v2",
    )
    await db[BINDINGS].create_index(
        [("user_id", ASCENDING), ("resource_id", ASCENDING)],
        name="ix_option_cost_resource_v2",
    )


async def _product(db: Any, user_id: str, product_id: str) -> dict[str, Any]:
    row = await db[PRODUCTS].find_one({
        "user_id": user_id,
        "$or": [
            {"id": product_id},
            {"mezan_product_id": product_id},
            {"salla_product_id": product_id},
        ],
    }, {"_id": 0})
    if not row:
        raise HTTPException(status_code=404, detail={"code": "product_v2_not_found"})
    return row


def _option_value(product: dict[str, Any], option_id: str, value_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    for option in product.get("options") or []:
        if str(option.get("id")) != str(option_id):
            continue
        values = option.get("values") or []
        if not values and str(value_id) == OPTION_LEVEL_VALUE_ID:
            return option, {
                "id": OPTION_LEVEL_VALUE_ID,
                "name": _text(option.get("name")) or "الخيار",
            }
        for value in values:
            if str(value.get("id")) == str(value_id):
                return option, value
    raise HTTPException(status_code=422, detail={"code": "option_value_not_found"})


def _binding_is_selected(
    binding: dict[str, Any],
    selected_keys: set[tuple[str, str]],
    selected_option_ids: set[str],
) -> bool:
    option_id = str(binding.get("option_id"))
    value_id = str(binding.get("value_id"))
    if value_id == OPTION_LEVEL_VALUE_ID:
        return option_id in selected_option_ids
    return (option_id, value_id) in selected_keys


async def _binding_view(db: Any, binding: dict[str, Any]) -> dict[str, Any]:
    row = _serialize(binding) or {}
    amount = _number(row.get("direct_amount"))
    if row.get("mode") == "resource":
        resource = await db[RESOURCES].find_one(
            {"user_id": row["user_id"], "id": row.get("resource_id")},
            {"_id": 0},
        )
        row["resource"] = _serialize(resource)
        unit_cost = _number((resource or {}).get("unit_cost")) or 0.0
        amount = unit_cost * (_number(row.get("quantity")) or 1.0)
    row["resolved_amount"] = amount or 0.0
    return row


def make_product_option_cost_router(db: Any, current_user: Callable[..., Any]) -> APIRouter:
    router = APIRouter(tags=["Mezan Product Option Costs"])

    @router.get("/components-v2/workspace")
    async def component_workspace(user: dict = Depends(current_user)) -> dict[str, Any]:
        await ensure_indexes(db)
        user_id = str(user["id"])
        resources = await db[RESOURCES].find({"user_id": user_id}, {"_id": 0}).sort("name", 1).to_list(length=1000)
        bindings = await db[BINDINGS].find({"user_id": user_id}, {"_id": 0}).to_list(length=10000)
        products = await db[PRODUCTS].find(
            {"user_id": user_id, "archived": {"$ne": True}},
            {"_id": 0, "salla_product_id": 1, "mezan_product_id": 1, "name": 1, "sku": 1, "options": 1},
        ).to_list(length=5000)
        by_resource: dict[str, list[dict[str, Any]]] = {}
        for binding in bindings:
            if binding.get("mode") != "resource" or not binding.get("resource_id"):
                continue
            product = next((p for p in products if str(p.get("salla_product_id")) == str(binding.get("salla_product_id"))), None)
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
        component_rows = []
        for resource in resources:
            row = _serialize(resource) or {}
            row.update({
                "track_inventory": bool(row.get("track_inventory")),
                "reference_cost": {
                    "amount": row.get("unit_cost"),
                    "currency": "SAR",
                    "source": "mezan_cost_resource_v2",
                },
                "product_usages": by_resource.get(str(row.get("id")), []),
            })
            component_rows.append(row)
        return {
            "components": component_rows,
            "products": [{
                "id": p.get("mezan_product_id"),
                "salla_id": p.get("salla_product_id"),
                "sku": p.get("sku"),
                "name": p.get("name"),
                "options": p.get("options") or [],
            } for p in products],
            "meta": {"mode": "production", "writes_enabled": True, "source": RESOURCES},
        }

    @router.post("/components-v2")
    async def create_component(payload: dict = Body(...), user: dict = Depends(current_user)) -> dict[str, Any]:
        await ensure_indexes(db)
        user_id = str(user["id"])
        name = _text(payload.get("name"))
        code = _text(payload.get("code")).upper()
        if not name or not code:
            raise HTTPException(status_code=422, detail={"code": "invalid_component"})
        now = _now()
        row = {
            "id": uuid.uuid4().hex,
            "user_id": user_id,
            "name": name,
            "code": code,
            "kind": _text(payload.get("kind")) or "service",
            "unit": _text(payload.get("unit")) or "job",
            "category": _text(payload.get("category")) or "other",
            "description": _text(payload.get("description")),
            "track_inventory": bool(payload.get("track_inventory")),
            "unit_cost": _number(payload.get("unit_cost")),
            "created_at": now,
            "updated_at": now,
        }
        try:
            await db[RESOURCES].insert_one(row)
        except Exception as exc:
            if "duplicate" in str(exc).lower():
                raise HTTPException(status_code=409, detail={"code": "component_code_exists"}) from exc
            raise
        await bump_product_cost_revision(db, user_id)
        return {"ok": True, "resource": _serialize(row)}

    @router.put("/components-v2/{resource_id}/cost")
    async def update_component_cost(resource_id: str, payload: dict = Body(...), user: dict = Depends(current_user)) -> dict[str, Any]:
        user_id = str(user["id"])
        amount = _number(payload.get("amount"))
        if amount is None or amount < 0:
            raise HTTPException(status_code=422, detail={"code": "invalid_cost"})
        now = _now()
        before = await db[RESOURCES].find_one({"user_id": user_id, "id": resource_id}, {"_id": 0})
        if not before:
            raise HTTPException(status_code=404, detail={"code": "component_not_found"})
        await db[RESOURCES].update_one(
            {"user_id": user_id, "id": resource_id},
            {"$set": {"unit_cost": amount, "updated_at": now}},
        )
        await bump_product_cost_revision(db, user_id)
        impacted = await db[BINDINGS].count_documents({"user_id": user_id, "resource_id": resource_id})
        await db[AUDIT].insert_one({
            "id": uuid.uuid4().hex,
            "user_id": user_id,
            "event_type": "resource_cost_changed",
            "resource_id": resource_id,
            "before": before.get("unit_cost"),
            "after": amount,
            "impacted_bindings": impacted,
            "created_at": now,
        })
        return {"ok": True, "amount": amount, "impacted_bindings": impacted}

    @router.get("/products-v2/{product_id}/option-costs")
    async def get_option_costs(product_id: str, user: dict = Depends(current_user)) -> dict[str, Any]:
        user_id = str(user["id"])
        product = await _product(db, user_id, product_id)
        salla_product_id = str(product["salla_product_id"])
        bindings = await db[BINDINGS].find({
            "user_id": user_id,
            "salla_product_id": salla_product_id,
        }, {"_id": 0}).to_list(length=1000)
        product_links = await db[PRODUCT_RESOURCE_BINDINGS].find({
            "user_id": user_id,
            "salla_product_id": salla_product_id,
        }, {"_id": 0}).to_list(length=1000)
        product_resource_ids = {
            str(row.get("resource_id"))
            for row in product_links
            if row.get("resource_id")
        }
        resources = []
        async for raw in db[RESOURCES].find(
            {"user_id": user_id}, {"_id": 0}
        ).sort("name", 1):
            row = _serialize(raw) or {}
            row["linked_to_product"] = str(row.get("id")) in product_resource_ids
            row["available_for_option_link"] = not row["linked_to_product"]
            resources.append(row)
        return {
            "salla_product_id": salla_product_id,
            "bindings": [await _binding_view(db, row) for row in bindings],
            "product_links": [_serialize(row) for row in product_links],
            "product_resource_ids": sorted(product_resource_ids),
            "resources": resources,
        }

    @router.put("/products-v2/{product_id}/option-costs/{option_id}/{value_id}")
    async def save_option_cost(product_id: str, option_id: str, value_id: str, payload: dict = Body(...), user: dict = Depends(current_user)) -> dict[str, Any]:
        await ensure_indexes(db)
        user_id = str(user["id"])
        product = await _product(db, user_id, product_id)
        option, value = _option_value(product, option_id, value_id)
        mode = _text(payload.get("mode"))
        if mode not in {"resource", "direct"}:
            raise HTTPException(status_code=422, detail={"code": "invalid_cost_mode"})
        resource_id = _text(payload.get("resource_id")) or None
        direct_amount = _number(payload.get("direct_amount"))
        quantity = _number(payload.get("quantity")) or 1.0
        if quantity <= 0:
            raise HTTPException(status_code=422, detail={"code": "invalid_quantity"})
        if mode == "resource":
            resource = await db[RESOURCES].find_one({"user_id": user_id, "id": resource_id}, {"_id": 0})
            if not resource:
                raise HTTPException(status_code=422, detail={"code": "component_not_found"})
            product_conflict = await db[PRODUCT_RESOURCE_BINDINGS].find_one(
                {
                    "user_id": user_id,
                    "salla_product_id": str(product["salla_product_id"]),
                    "resource_id": resource_id,
                },
                {"_id": 0, "id": 1},
            )
            if product_conflict:
                raise HTTPException(
                    status_code=409,
                    detail={"code": "resource_already_linked_to_product"},
                )
            direct_amount = None
        elif direct_amount is None or direct_amount < 0:
            raise HTTPException(status_code=422, detail={"code": "invalid_cost"})
        now = _now()
        selector = {
            "user_id": user_id,
            "salla_product_id": str(product["salla_product_id"]),
            "option_id": str(option_id),
            "value_id": str(value_id),
        }
        patch = {
            **selector,
            "product_name": product.get("name"),
            "option_name": option.get("name"),
            "value_name": value.get("name"),
            "mode": mode,
            "resource_id": resource_id if mode == "resource" else None,
            "direct_amount": direct_amount if mode == "direct" else None,
            "quantity": quantity,
            "updated_at": now,
        }
        await db[BINDINGS].update_one(
            selector,
            {"$set": patch, "$setOnInsert": {"id": uuid.uuid4().hex, "created_at": now}},
            upsert=True,
        )
        await bump_product_cost_revision(db, user_id)
        saved = await db[BINDINGS].find_one(selector, {"_id": 0})
        return {"ok": True, "binding": await _binding_view(db, saved or patch)}

    @router.delete("/products-v2/{product_id}/option-costs/{option_id}/{value_id}")
    async def delete_option_cost(product_id: str, option_id: str, value_id: str, user: dict = Depends(current_user)) -> dict[str, Any]:
        user_id = str(user["id"])
        product = await _product(db, user_id, product_id)
        result = await db[BINDINGS].delete_one({
            "user_id": user_id,
            "salla_product_id": str(product["salla_product_id"]),
            "option_id": str(option_id),
            "value_id": str(value_id),
        })
        if result.deleted_count:
            await bump_product_cost_revision(db, user_id)
        return {"ok": True, "deleted": result.deleted_count}

    @router.post("/products-v2/{product_id}/calculate-cost")
    async def calculate_selected_cost(product_id: str, payload: dict = Body(...), user: dict = Depends(current_user)) -> dict[str, Any]:
        user_id = str(user["id"])
        product = await _product(db, user_id, product_id)
        profile = await db["mezan_product_cost_profiles_v2"].find_one({
            "user_id": user_id,
            "salla_product_id": str(product["salla_product_id"]),
        }, {"_id": 0}) or {}
        base_cost = _number(profile.get("base_cost")) or 0.0
        selected = payload.get("selected_options") if isinstance(payload.get("selected_options"), list) else []
        selected_keys = {(str(row.get("option_id")), str(row.get("value_id"))) for row in selected if isinstance(row, dict)}
        selected_option_ids = {option_id for option_id, _value_id in selected_keys}
        bindings = await db[BINDINGS].find({
            "user_id": user_id,
            "salla_product_id": str(product["salla_product_id"]),
        }, {"_id": 0}).to_list(length=1000)
        product_links = await db[PRODUCT_RESOURCE_BINDINGS].find({
            "user_id": user_id,
            "salla_product_id": str(product["salla_product_id"]),
        }, {"_id": 0}).to_list(length=1000)
        product_resource_ids = [
            str(row.get("resource_id"))
            for row in product_links
            if row.get("resource_id")
        ]
        product_resources = await db[RESOURCES].find(
            {"user_id": user_id, "id": {"$in": product_resource_ids}},
            {"_id": 0},
        ).to_list(length=max(1, len(product_resource_ids)))
        product_resource_map = {
            str(row.get("id")): row for row in product_resources
        }
        applied = []
        option_additional = 0.0
        for binding in bindings:
            if not _binding_is_selected(binding, selected_keys, selected_option_ids):
                continue
            view = await _binding_view(db, binding)
            amount = _number(view.get("resolved_amount")) or 0.0
            option_additional += amount
            applied.append(view)
        applied_product_resources = []
        product_additional = 0.0
        for link in product_links:
            resource = product_resource_map.get(str(link.get("resource_id")), {})
            amount = (
                (_number(resource.get("unit_cost")) or 0.0)
                * (_number(link.get("quantity")) or 1.0)
            )
            product_additional += amount
            applied_product_resources.append({
                "binding_id": link.get("id"),
                "quantity": link.get("quantity") or 1,
                "resolved_amount": round(amount, 4),
                "resource": _serialize(resource),
            })
        additional = product_additional + option_additional
        return {
            "base_cost": base_cost,
            "product_resource_cost": round(product_additional, 4),
            "option_cost": round(option_additional, 4),
            "total_cost": round(base_cost + additional, 4),
            "applied_product_resources": applied_product_resources,
            "applied_bindings": applied,
        }

    return router
