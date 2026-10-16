"""Read-only sold-products report over canonical orders and Mezan/Salla costs.

Order quantity and date belong to unified_orders. Unit cost is Mezan V2 first,
then Salla's registered fallback; optional components are excluded.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any
import unicodedata

from order_option_cost_snapshot_routes import classify_base_unit_cost
from product_catalog_cost_resolution import (
    index_current_catalog_products, resolve_current_catalog_line_product,
)
from product_v2_details_routes import COST_PROFILES
from product_v2_routes import PRODUCTS

CATALOG_FIELDS = {
    "_id": 0, "salla_product_id": 1, "mezan_product_id": 1,
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
) -> list[dict]:
    by_id, by_variant, by_sku = index_current_catalog_products(products)
    profile_map = {str(row.get("salla_product_id")): row for row in profiles}
    aggregated: dict[str, dict] = {}
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
            cost = classify_base_unit_cost(item, profile_map.get(str((product or {}).get("salla_product_id") or "")), product)
            unit = _decimal(cost["unit_cost"]) if cost["calculation_cost_available"] else None
            order_number = str(order.get("order_number") or "")
            line_id = str(item.get("order_item_id") or item.get("source_item_id") or item.get("id") or "")
            bundle_key = (order_number, line_id)
            bundle_known = bundle_key in (bundle_costs or {}) or (order_number, "product:" + identity) in (bundle_costs or {})
            bundle = (bundle_costs or {}).get(bundle_key)
            bundle_source = None
            if bundle_known:
                # A frozen sale line replaces parent catalog cost. Never create
                # component sales, or fall back to a newly edited parent price.
                amount = None
                if bundle and _decimal(bundle.get("quantity")) == quantity:
                    amount = _decimal(bundle.get("actual_cost"))
                    bundle_source = "bundle_actual"
                    if amount is None:
                        amount = _decimal(bundle.get("expected_cost"))
                        bundle_source = "bundle_expected"
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
                row["cost_source"] = bundle_source or ("mezan" if cost["mezan_cost_complete"] else "salla")
                row["_known_cost"] += unit * quantity
            if bundle_known:
                row["_bundle_cost"] = True
    rows = list(aggregated.values())
    for row in rows:
        row["units_sold"] = float(row["units_sold"])
        known_cost = row.pop("_known_cost")
        row["total_cost"] = round(float(known_cost), 2) if row["cost_status"] == "complete" else None
        if row.pop("_bundle_cost", False) and row["cost_status"] == "complete":
            row["unit_cost"] = round(float(known_cost / Decimal(str(row["units_sold"]))), 2)
        if row["cost_status"] != "complete":
            row["unit_cost"] = None
            row["cost_source"] = "missing"
    rows.sort(key=lambda row: (row["cost_status"] == "complete", -row["units_sold"], row["sku"].casefold(), row["name"].casefold()))
    return rows


async def load_sold_products(db: Any, user_id: str, orders: list[dict]) -> list[dict]:
    products = await db[PRODUCTS].find({"user_id": user_id}, CATALOG_FIELDS).to_list(length=100000)
    ids = [str(row.get("salla_product_id")) for row in products if row.get("salla_product_id")]
    profiles = await db[COST_PROFILES].find(
        {"user_id": user_id, "salla_product_id": {"$in": ids}}, {"_id": 0},
    ).to_list(length=max(1, len(ids)))
    from stock_component_consumption_service import PLANS
    from bundle_fulfillment_v2 import order_view
    numbers = sorted({str(order.get("order_number")) for order in orders if order.get("order_number")})
    plans = await db[PLANS].find({"user_id": user_id, "order_id": {"$in": numbers},
        "bundle_snapshots": {"$exists": True}}, {"_id": 0, "order_id": 1}).to_list(length=len(numbers) + 1)
    bundle_costs = {}
    for plan in plans:
        number = str(plan["order_id"])
        for bundle in (await order_view(db, user_id, number))["bundles"]:
            bundle_costs[(number, str(bundle["order_line_id"]))] = bundle
            # If canonical line identity is missing, mark cost unknown rather
            # than selecting one of potentially several personalized bundles.
            bundle_costs[(number, "product:" + str(bundle["product_id"]))] = None
    # Ingestion freezes composition before payment/review. Such orders have no
    # reservation plan yet, but their expected cost must not use today's parent.
    intakes = await db["mezan_component_order_lifecycle_v1"].find({"user_id": user_id,
        "order_number": {"$in": numbers}, "bundle_intake_snapshots": {"$exists": True}},
        {"_id": 0, "order_number": 1, "bundle_intake_snapshots": 1}).to_list(length=len(numbers) + 1)
    order_map = {str(order.get("order_number")): order for order in orders}
    for intake in intakes:
        number = str(intake["order_number"])
        items = order_map.get(number, {}).get("products") or []
        for line_id, snapshot in intake.get("bundle_intake_snapshots", {}).items():
            if not snapshot or (number, str(line_id)) in bundle_costs:
                continue
            item = next((item for item in items if isinstance(item, dict) and
                str(item.get("order_item_id") or item.get("source_item_id") or item.get("id") or "") == str(line_id)), {})
            quantity = _decimal(item.get("quantity"))
            estimated = _decimal(snapshot.get("estimated_cost"))
            bundle_costs[(number, str(line_id))] = {"quantity": quantity,
                "expected_cost": str(estimated * quantity) if estimated is not None and quantity is not None and quantity > 0 else None,
                "actual_cost": None}
            bundle_costs[(number, "product:" + str(snapshot["product_id"]))] = None
    from bundle_catalog_v2 import BUNDLES
    definitions = await db[BUNDLES].find({"user_id": user_id, "product_type": "composite"},
        {"_id": 0, "product_id": 1}).to_list(length=100000)
    composite_ids = {str(definition["product_id"]) for definition in definitions}
    by_id, by_variant, by_sku = index_current_catalog_products(products)
    for order in orders:
        for item in order.get("products") or []:
            if not isinstance(item, dict):
                continue
            product = resolve_current_catalog_line_product(item, products_by_id=by_id,
                products_by_variant=by_variant, products_by_sku=by_sku)
            identity = str((product or {}).get("salla_product_id") or item.get("parent_product_id") or item.get("product_id") or "")
            if identity in composite_ids:
                bundle_costs.setdefault((str(order.get("order_number") or ""), "product:" + identity), None)
    return aggregate_sold_products(orders, products, profiles, bundle_costs)
