"""Read-only sold-products report over canonical orders and Mezan/Salla costs.

Order quantity and date belong to unified_orders. Historical order cost is
authoritative; ordinary catalog fallback includes selected components. A bundle
cost replaces the complete parent calculation once.
"""
from __future__ import annotations

import asyncio
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from typing import Any
import unicodedata

from dashboard_cpu_budget import CPUWorkBudget, COST_PROFIT_BUDGET_MS
from order_cost_authority import (
    calculate_catalog_line_cost, load_order_cost_authority, resolve_order_line_cost,
)
from product_fulfillment_rules import PRODUCT_RESOURCE_BINDINGS
from product_option_cost_routes import BINDINGS, RESOURCES
from product_catalog_cost_resolution import (
    index_current_catalog_products, resolve_current_catalog_line_product,
)
from product_v2_details_routes import COST_PROFILES
from product_v2_routes import PRODUCTS

CATALOG_FIELDS = {
    "_id": 0, "id": 1, "salla_product_id": 1, "mezan_product_id": 1,
    "sku": 1, "name": 1, "main_image": 1, "variants": 1,
    "cost_price_from_salla": 1, "cost_price": 1, "cost": 1,
    "raw_salla": 1, "raw_salla_details": 1,
}
SELECTABLE_STATUSES = {
    "pending_review": ("بإنتظار المراجعة", "بانتظار المراجعة", "انتظار المراجعة", "under review", "waiting review", "pending review"),
    "reviewed": ("تم المراجعة", "تمت المراجعة", "reviewed"),
    "in_progress": ("قيد التنفيذ", "جاري التنفيذ", "processing", "in progress"),
}
DEFAULT_STATUS_NAMES = (
    *SELECTABLE_STATUSES["pending_review"],
    *SELECTABLE_STATUSES["reviewed"],
    *SELECTABLE_STATUSES["in_progress"],
    "تم التنفيذ", "completed", "تم التجهيز", "prepared",
    "جاري التوصيل", "out for delivery", "delivering",
    "تم الشحن", "shipped", "تم التوصيل", "delivered",
    "مسند إلى مندوب التوصيل", "مسند الى مندوب التوصيل", "assigned",
)


def _normalized(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold().replace("_", " ")
    text = "".join(char for char in text if not unicodedata.combining(char))
    return " ".join(text.translate(str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا"})).split())


def order_matches_status(order: dict, selected: list[str], configured: list[str]) -> bool:
    status = _normalized(order.get("order_status"))
    slug = _normalized(order.get("order_status_slug"))
    if selected == ["default"]:
        if configured:
            # Same case-insensitive partial matching as Dashboard V2.
            return any(status and term and (term in status or status in term)
                       for term in (_normalized(value) for value in configured))
        allowed = {_normalized(value) for value in DEFAULT_STATUS_NAMES}
    else:
        allowed = {_normalized(value) for key in selected for value in SELECTABLE_STATUSES[key]}
    return status in allowed or slug in allowed


def _decimal(value: Any) -> Decimal | None:
    try:
        number = Decimal(str(value))
        return number if number.is_finite() else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def summarize(rows: list[dict]) -> dict:
    units = sum((_decimal(row["units_sold"]) or Decimal(0) for row in rows), Decimal(0))
    costed = [row for row in rows if row["total_cost"] is not None]
    known = sum((_decimal(row["total_cost"]) or Decimal(0) for row in costed), Decimal(0))
    missing = len(costed) != len(rows)
    return {
        "units_sold": float(units),
        "average_unit_cost": round(float(known / units), 2) if units and not missing else None,
        "total_cost": round(float(known), 2) if not missing else None,
        "known_cost_total": round(float(known), 2),
        "known_cost_units": sum(row["units_sold"] for row in costed),
    }


def aggregate_sold_products(
    orders: list[dict], products: list[dict], profiles: list[dict],
    bundle_costs: dict[tuple[str, str], dict | None] | None = None,
    *,
    authority: Any = None,
    catalog_context: dict[str, Any] | None = None,
) -> list[dict]:
    by_id, by_variant, by_sku = index_current_catalog_products(products)
    profile_map = {str(row.get("salla_product_id")): row for row in profiles}
    aggregated: dict[str, dict] = {}
    context = catalog_context or {}
    for order in orders:
        for item in order.get("products") or []:
            if not isinstance(item, dict):
                continue
            product = resolve_current_catalog_line_product(
                item, products_by_id=by_id, products_by_variant=by_variant,
                products_by_sku=by_sku,
            )
            identity = str((product or {}).get("salla_product_id") or item.get("parent_product_id") or item.get("product_id") or "").strip()
            variant = str(item.get("variant_id") or "").strip()
            variant_row = next((v for v in (product or {}).get("variants") or []
                                if isinstance(v, dict) and str(v.get("id") or "") == variant), {}) if variant else {}
            sku = str(item.get("sku") or variant_row.get("sku") or (product or {}).get("sku") or "").strip()
            name = str(item.get("name") or (product or {}).get("name") or "").strip()
            key = f"{identity}:{variant or sku.casefold()}" if identity else sku.casefold() or name.casefold()
            if not key:
                continue
            quantity = _decimal(item.get("quantity"))
            if quantity is None or quantity <= 0:
                continue
            product_id = str((product or {}).get("salla_product_id") or "")
            cost = calculate_catalog_line_cost(
                item,
                product=product,
                profile=profile_map.get(product_id),
                product_bindings=context.get("product_binding_map", {}).get(product_id, []),
                option_bindings=context.get("option_map", {}).get(product_id, []),
                resources=context.get("resources", {}),
            )
            order_number = str(order.get("order_number") or "")
            if authority is not None:
                cost = resolve_order_line_cost(
                    item, order_number=order_number, catalog_cost=cost,
                    authority=authority, product_id=product_id,
                )
            amount = _decimal(cost["line_total"]) if cost["calculation_cost_available"] else None
            unit = amount / quantity if amount is not None else None
            source = str(cost.get("calculation_cost_source") or cost["base_cost_source"])
            cost_source = (
                source if cost["base_cost_source"] in {"bundle_snapshot", "order_snapshot"}
                else "mezan" if cost["mezan_cost_complete"] else "salla"
            )
            # Retain the public pure helper's historical explicit-map API. The
            # database loader below always uses the shared authority instead.
            if authority is None and bundle_costs is not None:
                line_id = str(item.get("order_item_id") or item.get("source_item_id") or item.get("id") or "")
                bundle_key = (order_number, line_id)
                bundle_known = bundle_key in bundle_costs or (order_number, "product:" + identity) in bundle_costs
                bundle = bundle_costs.get(bundle_key)
                if bundle_known:
                    amount = None
                    if bundle and _decimal(bundle.get("quantity")) == quantity:
                        amount = _decimal(bundle.get("actual_cost"))
                        cost_source = "bundle_actual"
                        if amount is None:
                            amount = _decimal(bundle.get("expected_cost"))
                            cost_source = "bundle_expected"
                    unit = amount / quantity if amount is not None else None
            row = aggregated.setdefault(key, {
                "name": name, "sku": sku, "product_id": identity,
                "mezan_product_id": str((product or {}).get("mezan_product_id") or ""),
                "catalog_product_found": bool(product),
                "image_url": (product or {}).get("main_image") or item.get("image_url") or item.get("image") or "",
                "units_sold": Decimal(0), "_known_cost": Decimal(0),
                "unit_cost": None, "cost_status": "complete", "cost_source": "",
            })
            row["units_sold"] += quantity
            if unit is None:
                row["cost_status"] = "incomplete"
            else:
                row["unit_cost"] = round(float(unit), 2)
                row["cost_source"] = cost_source
                row["_known_cost"] += unit * quantity
    rows = list(aggregated.values())
    for row in rows:
        row["units_sold"] = float(row["units_sold"])
        known_cost = row.pop("_known_cost")
        row["total_cost"] = round(float(known_cost), 2) if row["cost_status"] == "complete" else None
        if row["cost_status"] == "complete":
            row["unit_cost"] = round(float(known_cost / Decimal(str(row["units_sold"]))), 2)
        if row["cost_status"] != "complete":
            row["unit_cost"] = None
            row["cost_source"] = "missing"
    rows.sort(key=lambda row: (row["cost_status"] == "complete", -row["units_sold"], row["sku"].casefold(), row["name"].casefold()))
    return rows


async def load_sold_products(db: Any, user_id: str, orders: list[dict]) -> list[dict]:
    """Read one bounded cohort; never issue per-order bundle lookups or writes."""
    products = await db[PRODUCTS].find({"user_id": user_id}, CATALOG_FIELDS).to_list(length=100000)
    ids = [str(row.get("salla_product_id")) for row in products if row.get("salla_product_id")]
    query = {"user_id": user_id, "salla_product_id": {"$in": ids}}
    profiles, option_bindings, product_bindings = await asyncio.gather(
        db[COST_PROFILES].find(query, {"_id": 0}).to_list(length=max(1, len(ids))),
        db[BINDINGS].find(query, {"_id": 0}).to_list(length=100000),
        db[PRODUCT_RESOURCE_BINDINGS].find(query, {"_id": 0}).to_list(length=100000),
    )
    resource_ids = sorted({str(row["resource_id"]) for row in option_bindings + product_bindings
                           if row.get("resource_id")})
    resource_rows = await db[RESOURCES].find(
        {"user_id": user_id, "id": {"$in": resource_ids}}, {"_id": 0},
    ).to_list(length=max(1, len(resource_ids)))
    option_map: dict[str, list[dict[str, Any]]] = defaultdict(list)
    product_binding_map: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in option_bindings:
        option_map[str(row.get("salla_product_id"))].append(row)
    for row in product_bindings:
        product_binding_map[str(row.get("salla_product_id"))].append(row)
    context = {
        "option_map": option_map,
        "product_binding_map": product_binding_map,
        "resources": {str(row.get("id")): row for row in resource_rows},
    }
    authority = await load_order_cost_authority(
        db, user_id, orders, budget=CPUWorkBudget(COST_PROFIT_BUDGET_MS),
    )
    from current_bundle_reporting_estimate import load_current_bundle_estimates
    authority["current_bundle_estimates"] = await load_current_bundle_estimates(
        db, user_id, orders, authority, products, profiles,
        option_map, product_binding_map, context["resources"],
    )
    return aggregate_sold_products(orders, products, profiles, authority=authority, catalog_context=context)
