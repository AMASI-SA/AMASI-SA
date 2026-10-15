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
                row["cost_source"] = "mezan" if cost["mezan_cost_complete"] else "salla"
                row["_known_cost"] += unit * quantity
    rows = list(aggregated.values())
    for row in rows:
        row["units_sold"] = float(row["units_sold"])
        row["total_cost"] = round(float(row.pop("_known_cost")), 2) if row["cost_status"] == "complete" else None
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
    return aggregate_sold_products(orders, products, profiles)
