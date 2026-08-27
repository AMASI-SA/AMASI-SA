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


def _item_value(item: Any, key: str, default: Any = None) -> Any:
    if isinstance(item, dict):
        return item.get(key, default)
    return getattr(item, key, default)


def selected_option_tokens(item: Any) -> set[tuple[str, str]]:
    """Extract selected (option, value) identities from canonical order item."""
    tokens: set[tuple[str, str]] = set()
    raw_rows = (
        _item_value(item, "options_raw")
        or _item_value(item, "options")
        or []
    )
    for row in raw_rows:
        if not isinstance(row, dict):
            continue
        option = row.get("option") if isinstance(row.get("option"), dict) else {}
        raw_value = row.get("value")
        value = raw_value if isinstance(raw_value, dict) else {}
        option_id = row.get("option_id") or row.get("id") or option.get("id")
        value_id = row.get("value_id") or value.get("id")
        option_name = row.get("option_name") or row.get("name") or option.get("name")
        value_name = row.get("value_name") or value.get("name") or raw_value
        if option_id not in (None, "") and value_id not in (None, ""):
            tokens.add((f"id:{option_id}", f"id:{value_id}"))
        if option_name in (None, ""):
            continue
        # The legacy Salla order projection intentionally preserves plural
        # ``values`` as a list.  Treat every selected value as its own token;
        # stringifying the whole list (for example ``['فستان']``) prevents a
        # binding stored as ``فستان`` from matching and silently drops its
        # additional cost from both product cards and executive totals.
        selected_values = (
            value_name
            if isinstance(value_name, (list, tuple, set))
            else [value_name]
        )
        for selected_value in selected_values:
            if isinstance(selected_value, dict):
                selected_value = (
                    selected_value.get("name")
                    or selected_value.get("value")
                    or selected_value.get("label")
                    or selected_value.get("text")
                )
            if selected_value not in (None, "", {}):
                tokens.add((
                    f"name:{_norm(option_name)}",
                    f"name:{_norm(selected_value)}",
                ))

    normalized = _item_value(item, "options_normalized") or {}
    if isinstance(normalized, dict):
        for key, value in normalized.items():
            if isinstance(value, list):
                for entry in value:
                    tokens.add((f"name:{_norm(key)}", f"name:{_norm(entry)}"))
            else:
                tokens.add((f"name:{_norm(key)}", f"name:{_norm(value)}"))
    return tokens


def binding_matches(binding: dict[str, Any], tokens: set[tuple[str, str]]) -> bool:
    option_id = binding.get("option_id")
    value_id = binding.get("value_id")
    option_name = _norm(binding.get("option_name"))
    value_name = _norm(binding.get("value_name"))
    id_match = (
        option_id not in (None, "")
        and value_id not in (None, "")
        and (f"id:{option_id}", f"id:{value_id}") in tokens
    )
    name_match = (
        bool(option_name)
        and bool(value_name)
        and (f"name:{option_name}", f"name:{value_name}") in tokens
    )
    return id_match or name_match


def resolve_base_unit_cost(
    item: Any,
    profile: dict[str, Any] | None,
    product: dict[str, Any] | None,
) -> tuple[float | None, str]:
    """Resolve a line's base cost with the Mezan V2 → Salla fallback rule.

    An explicit zero is a real Mezan cost and must never fall back to Salla.
    Variant costs take precedence over their corresponding product base cost.
    """
    profile = profile or {}
    product = product or {}
    variant_id = str(_item_value(item, "variant_id") or "").strip()
    sku = str(_item_value(item, "sku") or "").strip().casefold()

    variant_costs = profile.get("variant_costs")
    if isinstance(variant_costs, dict) and variant_id:
        if variant_id in variant_costs:
            parsed = _number(variant_costs.get(variant_id))
            if parsed is not None:
                return parsed, "mezan_v2_variant"

    if "base_cost" in profile and profile.get("base_cost") not in (None, ""):
        parsed = _number(profile.get("base_cost"))
        if parsed is not None:
            return parsed, "mezan_v2_base"

    for variant in product.get("variants") or []:
        if not isinstance(variant, dict):
            continue
        same_variant = variant_id and str(variant.get("id") or "").strip() == variant_id
        same_sku = sku and str(variant.get("sku") or "").strip().casefold() == sku
        if not (same_variant or same_sku):
            continue
        for cost_key in ("cost_price_from_salla", "cost_price", "cost"):
            if variant.get(cost_key) in (None, ""):
                continue
            parsed = _number(variant.get(cost_key))
            if parsed is not None:
                return parsed, "salla_variant_fallback"

    for cost_key in ("cost_price_from_salla", "cost_price", "cost"):
        if product.get(cost_key) in (None, ""):
            continue
        parsed = _number(product.get(cost_key))
        if parsed is not None:
            return parsed, "salla_product_fallback"
    return None, "missing"


MEZAN_V2_COST_SOURCES = frozenset({"mezan_v2_variant", "mezan_v2_base"})
SALLA_FALLBACK_COST_SOURCES = frozenset({
    "salla_variant_fallback",
    "salla_product_fallback",
})
COST_SEMANTICS_VERSION = "mezan-cost-semantics-v1"


def classify_base_unit_cost(
    item: Any,
    profile: dict[str, Any] | None,
    product: dict[str, Any] | None,
) -> dict[str, Any]:
    """Describe both calculation availability and Mezan V2 completeness.

    Salla remains a valid temporary fallback for profitability calculations,
    but it never marks the product as having an explicit Mezan V2 cost.  This
    distinction drives the actionable dashboard warning and product filter.
    """
    unit_cost, source = resolve_base_unit_cost(item, profile, product)
    calculation_cost_available = unit_cost is not None
    mezan_cost_complete = source in MEZAN_V2_COST_SOURCES
    uses_salla_fallback = source in SALLA_FALLBACK_COST_SOURCES
    return {
        "semantics_version": COST_SEMANTICS_VERSION,
        "unit_cost": unit_cost,
        "source": source,
        # Product setup completeness is deliberately Mezan-only.  A Salla
        # fallback must never hide a product from "missing Mezan cost".
        "mezan_cost_complete": mezan_cost_complete,
        "mezan_cost_missing": not mezan_cost_complete,
        # Profitability/campaign calculations may use Salla when Mezan has no
        # explicit cost.  Keep this axis separate from setup completeness.
        "calculation_cost_available": calculation_cost_available,
        "calculation_cost_source": source,
        "calculation_uses_salla_fallback": uses_salla_fallback,
        # Compatibility aliases for existing dashboard and snapshot callers.
        "cost_available": calculation_cost_available,
        "uses_salla_fallback": uses_salla_fallback,
    }


async def ensure_indexes(db: Any) -> None:
    await db[SNAPSHOTS].create_index(
        [("user_id", ASCENDING), ("order_number", ASCENDING), ("order_item_id", ASCENDING)],
        unique=True,
        name="uq_order_item_cost_snapshot_v2",
    )


async def calculate_order_cost_snapshot(db: Any, *, user_id: str, order: Any) -> dict[str, Any]:
    await ensure_indexes(db)
    product_ids = {str(item.parent_product_id or item.product_id or "").strip() for item in order.items}
    product_ids.discard("")
    products = await db[PRODUCTS].find(
        {"user_id": user_id, "salla_product_id": {"$in": list(product_ids)}},
        {
            "_id": 0,
            "salla_product_id": 1,
            "mezan_product_id": 1,
            "name": 1,
            "cost_price_from_salla": 1,
            "variants": 1,
        },
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
        product_id = str(item.parent_product_id or item.product_id or "").strip()
        quantity = float(item.quantity or 1)
        profile = profile_map.get(product_id, {})
        base_status = classify_base_unit_cost(
            item,
            profile,
            product_map.get(product_id),
        )
        resolved_base_cost = base_status["unit_cost"]
        base_cost_source = base_status["source"]
        base_unit_cost = resolved_base_cost if resolved_base_cost is not None else 0.0
        applied_product_resources = []
        product_resource_unit_cost = 0.0
        seen_product_binding_ids: set[str] = set()
        for binding in product_bindings_by_product.get(product_id, []):
            binding_id = str(binding.get("id") or "")
            if binding_id and binding_id in seen_product_binding_ids:
                continue
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
            if binding_id:
                seen_product_binding_ids.add(binding_id)
        tokens = selected_option_tokens(item)
        applied = []
        option_unit_cost = 0.0
        seen_option_binding_ids: set[str] = set()
        for binding in bindings_by_product.get(product_id, []):
            if not binding_matches(binding, tokens):
                continue
            binding_id = str(binding.get("id") or "")
            if binding_id and binding_id in seen_option_binding_ids:
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
            if binding_id:
                seen_option_binding_ids.add(binding_id)
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
            "base_cost_source": base_cost_source,
            "cost_complete": resolved_base_cost is not None,
            "mezan_cost_complete": base_status["mezan_cost_complete"],
            "mezan_cost_missing": base_status["mezan_cost_missing"],
            "calculation_cost_available": base_status["calculation_cost_available"],
            "uses_salla_fallback": base_status["calculation_uses_salla_fallback"],
            "cost_semantics_version": base_status["semantics_version"],
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
