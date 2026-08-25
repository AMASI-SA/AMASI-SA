"""Owner-only Mezan V2 dashboard backed by V2 operational sources.

The response intentionally preserves the legacy dashboard contract so the same
UI can be reused. Orders/sales come from ``unified_orders``; product costs are
recalculated from Mezan V2 products, cost profiles, components and selected
options; ad spend comes from the native V2 reporting facts (with Google kept as
an explicitly labelled transitional read). Salaries, shipping configuration and
payment fee formulae stay inherited from the legacy dashboard response.
"""
from __future__ import annotations

import asyncio
import logging
import re
import unicodedata
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query

from auth import ensure_user_settings
from customer_identity import CUSTOMER_IDENTITY_COLLECTION, decrypt_private_payload
from dashboard_v2_ad_costs import (
    apply_mezan_v2_ad_account_costs,
    merge_ad_bank_fees_into_dashboard,
)
from dashboard_v2_ads_executive import build_salla_ads_executive_breakdown
from dashboard_snapchat_spend import load_snapchat_dashboard_spend
from integrations_control_center.meta_oauth_security import META_PROVIDER_ID
from integrations_control_center.snapchat_oauth_security import SNAPCHAT_PROVIDER_ID
from integrations_control_center.tiktok_oauth_security import TIKTOK_PROVIDER_ID
from order_option_cost_snapshot_routes import (
    binding_matches,
    classify_base_unit_cost,
    selected_option_tokens,
)
from order_status_policy import effective_product_cost, get_policy_map
from product_fulfillment_rules import PRODUCT_RESOURCE_BINDINGS
from product_option_cost_routes import BINDINGS, RESOURCES
from product_catalog_cost_resolution import (
    index_current_catalog_products,
    resolve_current_catalog_line_product,
)
from product_v2_details_routes import COST_PROFILES
from product_v2_routes import PRODUCTS, _number
from recurring_obligations_routes import compute_recurring_obligations_for_range
from salla_marketing_attribution import (
    SALLA_RAW_ATTRIBUTION_PROJECTION,
    attach_projected_salla_attribution,
)
from salla_integration.abandoned_carts import (
    AbandonedCartScopeError,
    parse_salla_datetime,
    reconcile_recent_abandoned_carts,
)
from salla_integration.service import SallaError, call_salla
from unified_marketing.dashboard_shadow import build_dashboard_unified_shadow
from unified_marketing.gateway import (
    load_unified_marketing_account_report,
    load_unified_marketing_dashboard_spend,
)


SNAP_FACTS = "mezan_snapchat_performance_daily_v2"
META_FACTS = "mezan_meta_performance_daily_v2"
TIKTOK_FACTS = "mezan_tiktok_performance_daily_v2"
RIYADH_TZ = ZoneInfo("Asia/Riyadh")
PROVIDER_IDS = {
    "snapchat": SNAPCHAT_PROVIDER_ID,
    "meta": META_PROVIDER_ID,
    "tiktok": TIKTOK_PROVIDER_ID,
}
log = logging.getLogger("mezan.dashboard_v2")

PRODUCT_COST_CATALOG_PROJECTION = {
    "_id": 0,
    "id": 1,
    "salla_product_id": 1,
    "mezan_product_id": 1,
    "name": 1,
    "sku": 1,
    "main_image": 1,
    "cost_price_from_salla": 1,
    "cost_price": 1,
    "cost": 1,
    "variants": 1,
    "raw_salla": 1,
    "raw_salla_details": 1,
}


def _today_riyadh() -> date:
    return datetime.now(timezone.utc).astimezone(RIYADH_TZ).date()


def _float(value: Any) -> float:
    try:
        parsed = float(value or 0)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    return parsed


_ARABIC_DIACRITICS_RE = re.compile(r"[\u0610-\u061a\u064b-\u065f\u0670\u06d6-\u06ed]")


def _normalize_match_text(value: object) -> str:
    """Normalize presentation variants without changing business semantics.

    Arabic hamza/alef presentation differences are common between Salla status
    labels and saved Mezan settings (for example بانتظار vs بإنتظار). Matching
    should not drop otherwise identical orders because of those orthographic
    variants. The function also removes combining marks/tatweel and collapses
    whitespace, while preserving the actual words and status policy.
    """
    rendered = unicodedata.normalize("NFKC", str(value or "")).casefold()
    rendered = _ARABIC_DIACRITICS_RE.sub("", rendered).replace("ـ", "")
    rendered = rendered.translate(str.maketrans({
        "أ": "ا",
        "إ": "ا",
        "آ": "ا",
        "ٱ": "ا",
    }))
    return " ".join(rendered.split())


def _matches_any(value: str, allowed: list[str]) -> bool:
    if not allowed:
        return True
    normalized = _normalize_match_text(value)
    return any(
        candidate and (
            candidate == normalized
            or candidate in normalized
            or normalized in candidate
        )
        for candidate in (_normalize_match_text(item) for item in allowed)
    )


def _cart_datetime(row: dict[str, Any], *keys: str) -> datetime | None:
    for key in keys:
        value = row.get(key)
        if isinstance(value, date) and not isinstance(value, datetime):
            return datetime.combine(value, datetime.min.time(), tzinfo=RIYADH_TZ)
        parsed = parse_salla_datetime(value)
        if parsed is not None:
            return parsed
    return None


def _cart_day(row: dict[str, Any], *keys: str) -> str:
    parsed = _cart_datetime(row, *keys)
    return parsed.astimezone(RIYADH_TZ).date().isoformat() if parsed else ""


def _cart_activity_at(row: dict[str, Any]) -> datetime | None:
    return _cart_datetime(
        row,
        "cart_updated_at",
        "last_received_at",
        "updated_at",
        "cart_created_at",
        "first_seen_at",
        "created_at",
    )


def _cart_activity_iso(row: dict[str, Any]) -> str | None:
    parsed = _cart_activity_at(row)
    return parsed.astimezone(timezone.utc).isoformat() if parsed else None


def select_abandoned_carts_for_period(
    rows: list[dict[str, Any]],
    *,
    start: str,
    end: str,
) -> tuple[list[dict[str, Any]], int, int]:
    """Return active rows and period counts without changing stored carts."""
    if end < start:
        start, end = end, start
    abandoned_rows = [
        row for row in rows
        if start <= _cart_day(
            row,
            "cart_created_at",
            "first_seen_at",
            "cart_updated_at",
            "last_received_at",
            "updated_at",
            "created_at",
        ) <= end
    ]
    active_period_rows = [
        row for row in rows
        if row.get("purchased") is not True
        and start <= _cart_day(
            row,
            "cart_updated_at",
            "cart_created_at",
            "last_received_at",
            "updated_at",
            "first_seen_at",
            "created_at",
        ) <= end
    ]
    recovered_rows = [
        row for row in rows
        if row.get("purchased") is True
        and start <= _cart_day(row, "cart_updated_at", "updated_at") <= end
    ]
    active_rows = sorted(
        active_period_rows,
        key=lambda row: _cart_activity_at(row) or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    return active_rows, len(abandoned_rows), len(recovered_rows)


async def _to_list(cursor: Any, length: int) -> list[dict[str, Any]]:
    if hasattr(cursor, "to_list"):
        return await cursor.to_list(length=length)
    return [row async for row in cursor]


def _line_product(
    item: dict[str, Any],
    *,
    products_by_id: dict[str, dict[str, Any]],
    products_by_variant: dict[str, dict[str, Any]],
    products_by_sku: dict[str, dict[str, Any]],
) -> dict[str, Any] | None:
    return resolve_current_catalog_line_product(
        item,
        products_by_id=products_by_id,
        products_by_variant=products_by_variant,
        products_by_sku=products_by_sku,
    )


def _index_products(
    products: list[dict[str, Any]],
) -> tuple[
    dict[str, dict[str, Any]],
    dict[str, dict[str, Any]],
    dict[str, dict[str, Any]],
]:
    """Index the current full Salla catalog through the shared cost contract."""
    return index_current_catalog_products(products)


def calculate_mezan_v2_line_cost(
    item: dict[str, Any],
    *,
    product: dict[str, Any] | None,
    profile: dict[str, Any] | None,
    product_bindings: list[dict[str, Any]],
    option_bindings: list[dict[str, Any]],
    resources: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Calculate one order line without mutating any operational record."""
    quantity = _number(item.get("quantity"))
    quantity = quantity if quantity is not None and quantity > 0 else 1.0
    base_status = classify_base_unit_cost(item, profile, product)
    base = base_status["unit_cost"]
    source = base_status["source"]
    base_unit = base if base is not None else 0.0

    product_resource_unit = 0.0
    applied_product_binding_ids: set[str] = set()
    for binding in product_bindings:
        binding_id = str(binding.get("id") or "")
        if binding_id and binding_id in applied_product_binding_ids:
            continue
        resource = resources.get(str(binding.get("resource_id") or ""), {})
        unit_cost = _number(resource.get("unit_cost")) or 0.0
        multiplier = _number(binding.get("quantity")) or 1.0
        product_resource_unit += unit_cost * multiplier
        if binding_id:
            applied_product_binding_ids.add(binding_id)

    option_resource_unit = 0.0
    applied_option_binding_ids: set[str] = set()
    tokens = selected_option_tokens(item)
    for binding in option_bindings:
        if not binding_matches(binding, tokens):
            continue
        binding_id = str(binding.get("id") or "")
        if binding_id and binding_id in applied_option_binding_ids:
            continue
        amount = _number(binding.get("direct_amount")) or 0.0
        if binding.get("mode") == "resource":
            resource = resources.get(str(binding.get("resource_id") or ""), {})
            unit_cost = _number(resource.get("unit_cost")) or 0.0
            amount = unit_cost * (_number(binding.get("quantity")) or 1.0)
        option_resource_unit += amount
        if binding_id:
            applied_option_binding_ids.add(binding_id)

    component_unit = product_resource_unit + option_resource_unit
    return {
        "quantity": quantity,
        "base_cost_source": source,
        "base_complete": base_status["calculation_cost_available"],
        "calculation_cost_available": base_status["calculation_cost_available"],
        "calculation_cost_source": base_status["calculation_cost_source"],
        "mezan_cost_complete": base_status["mezan_cost_complete"],
        "mezan_cost_missing": base_status["mezan_cost_missing"],
        "uses_salla_fallback": base_status["uses_salla_fallback"],
        "cost_semantics_version": base_status["semantics_version"],
        "base_total": round(base_unit * quantity, 4),
        "product_components_total": round(product_resource_unit * quantity, 4),
        "selected_options_total": round(option_resource_unit * quantity, 4),
        "components_total": round(component_unit * quantity, 4),
        "line_total": round((base_unit + component_unit) * quantity, 4),
    }


def _line_sales_total(item: dict[str, Any], quantity: float) -> float:
    """Return product-line sales using the source total when available.

    Fresh Salla order rows carry ``total`` as the authoritative line amount.
    Older normalized rows may only have a unit ``price`` plus discount/tax, so
    keep a deterministic fallback for them instead of dropping their sales.
    """
    source_total = _number(item.get("total"))
    if source_total is not None:
        return round(max(source_total, 0.0), 4)
    unit_price = _number(item.get("price") or item.get("unit_price")) or 0.0
    discount = _number(item.get("discount")) or 0.0
    tax = _number(item.get("tax")) or 0.0
    return round(max((unit_price * quantity) - discount + tax, 0.0), 4)


def _finalize_product_profit_rows(
    rows: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Shape product-grain dashboard rows without inventing missing profit."""
    items: list[dict[str, Any]] = []
    for raw in rows.values():
        units = max(_float(raw.get("units_sold")), 0.0)
        if units <= 0:
            continue
        sales = round(_float(raw.get("total_sales")), 2)
        accumulated_cost = round(_float(raw.get("total_cost")), 2)
        missing_everywhere = bool(raw.get("missing_everywhere"))
        uses_salla_fallback = bool(raw.get("uses_salla_fallback"))
        mezan_complete = bool(raw.get("mezan_cost_complete"))
        if missing_everywhere:
            cost_status = "missing"
        elif not mezan_complete or uses_salla_fallback:
            cost_status = "salla_fallback"
        else:
            cost_status = "complete"

        reportable_cost = None if cost_status == "missing" else accumulated_cost
        average_unit_cost = (
            round(reportable_cost / units, 2)
            if reportable_cost is not None and units > 0
            else None
        )
        net_profit = (
            round(sales - reportable_cost, 2)
            if reportable_cost is not None
            else None
        )
        items.append({
            "identity": raw.get("identity") or "",
            "salla_product_id": raw.get("salla_product_id") or "",
            "mezan_product_id": raw.get("mezan_product_id") or "",
            "catalog_product_found": bool(raw.get("catalog_product_found")),
            "name": raw.get("name") or "منتج بدون اسم",
            "sku": raw.get("sku") or "",
            "image_url": raw.get("image_url") or "",
            "units_sold": round(units, 2),
            "orders_count": int(raw.get("orders_count") or 0),
            "average_unit_cost": average_unit_cost,
            "total_sales": sales,
            "total_cost": reportable_cost,
            "net_profit": net_profit,
            "profit_margin_pct": (
                round((net_profit / sales) * 100, 2)
                if net_profit is not None and sales > 0
                else None
            ),
            "cost_status": cost_status,
            "uses_salla_fallback": uses_salla_fallback,
            "missing_everywhere": missing_everywhere,
            "cost_sources": sorted(raw.get("cost_sources") or []),
        })

    # "Best selling" is defined by sold quantity. Revenue breaks ties so the
    # result stays deterministic and useful when two products sell equally.
    items.sort(key=lambda row: (
        -_float(row.get("units_sold")),
        -_float(row.get("total_sales")),
        str(row.get("name") or "").casefold(),
    ))
    has_unpriced = any(row["cost_status"] == "missing" for row in items)
    has_fallback = any(row["cost_status"] == "salla_fallback" for row in items)
    total_sales = round(sum(_float(row["total_sales"]) for row in items), 2)
    accumulated_cost = round(sum(_float(raw.get("total_cost")) for raw in rows.values()), 2)
    return items, {
        "product_count": len(items),
        "total_units": round(sum(_float(row["units_sold"]) for row in items), 2),
        "total_sales": total_sales,
        "total_cost": accumulated_cost,
        "net_profit": None if has_unpriced else round(total_sales - accumulated_cost, 2),
        "has_unpriced_products": has_unpriced,
        "uses_salla_fallback": has_fallback,
    }


async def _filtered_orders(
    db: Any,
    user_id: str,
    *,
    from_date: str | None,
    to_date: str | None,
    payment_methods: str | None,
    shipping_companies: str | None,
    include_marketing_attribution: bool = False,
) -> list[dict[str, Any]]:
    settings = await ensure_user_settings(db, user_id)
    query: dict[str, Any] = {"user_id": user_id}
    if from_date or to_date:
        query["order_date"] = {}
        if from_date:
            query["order_date"]["$gte"] = from_date
        if to_date:
            query["order_date"]["$lte"] = to_date
    if settings.get("hide_inferred_date_orders"):
        query["order_date_inferred"] = {"$ne": True}
    orders = await _to_list(
        db.unified_orders.find(query, {"_id": 0, "raw_by_source": 0}),
        100000,
    )
    if include_marketing_attribution and orders:
        # Fetch only Salla's whitelisted attribution metadata in a separate
        # projection.  The main dashboard query remains lightweight and no
        # customer, address, payment or product raw data is loaded.
        attribution_rows = await _to_list(
            db.unified_orders.find(query, SALLA_RAW_ATTRIBUTION_PROJECTION),
            100000,
        )
        attach_projected_salla_attribution(orders, attribution_rows)
    pm_list = [part.strip() for part in (payment_methods or "").split(",") if part.strip()]
    ship_list = [part.strip() for part in (shipping_companies or "").split(",") if part.strip()]
    included_statuses = settings.get("report_included_statuses") or []
    return [
        order for order in orders
        if _matches_any(order.get("payment_method", ""), pm_list)
        and _matches_any(order.get("shipping_company", ""), ship_list)
        and _matches_any(order.get("order_status", ""), included_statuses)
    ]


async def build_mezan_v2_product_cost(
    db: Any,
    user_id: str,
    orders: list[dict[str, Any]],
) -> dict[str, Any]:
    products = await _to_list(
        db[PRODUCTS].find(
            {"user_id": user_id},
            PRODUCT_COST_CATALOG_PROJECTION,
        ),
        100000,
    )
    products_by_id, products_by_variant, products_by_sku = _index_products(products)

    product_ids = [
        str(product.get("salla_product_id") or "").strip()
        for product in products
        if str(product.get("salla_product_id") or "").strip()
    ]
    profiles = await _to_list(
        db[COST_PROFILES].find(
            {"user_id": user_id, "salla_product_id": {"$in": product_ids}},
            {"_id": 0},
        ),
        max(1, len(product_ids)),
    )
    option_bindings = await _to_list(
        db[BINDINGS].find(
            {"user_id": user_id, "salla_product_id": {"$in": product_ids}},
            {"_id": 0},
        ),
        100000,
    )
    product_bindings = await _to_list(
        db[PRODUCT_RESOURCE_BINDINGS].find(
            {"user_id": user_id, "salla_product_id": {"$in": product_ids}},
            {"_id": 0},
        ),
        100000,
    )
    resource_ids = {
        str(binding.get("resource_id"))
        for binding in option_bindings + product_bindings
        if binding.get("resource_id")
    }
    resource_rows = await _to_list(
        db[RESOURCES].find(
            {"user_id": user_id, "id": {"$in": list(resource_ids)}},
            {"_id": 0},
        ),
        max(1, len(resource_ids)),
    )
    profile_map = {str(row.get("salla_product_id")): row for row in profiles}
    option_map: dict[str, list[dict[str, Any]]] = defaultdict(list)
    product_binding_map: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in option_bindings:
        option_map[str(row.get("salla_product_id"))].append(row)
    for row in product_bindings:
        product_binding_map[str(row.get("salla_product_id"))].append(row)
    resources = {str(row.get("id")): row for row in resource_rows}
    policy = await get_policy_map(db, user_id)

    totals = defaultdict(float)
    source_lines = defaultdict(int)
    linked_products: set[str] = set()
    missing_products: dict[str, dict[str, Any]] = {}
    salla_fallback_products: set[str] = set()
    missing_all_cost_products: set[str] = set()
    missing_lines = 0
    missing_all_cost_lines = 0
    no_products_orders = 0
    incomplete_orders = 0
    product_profit_rows: dict[str, dict[str, Any]] = {}

    for order in orders:
        raw_order_total = 0.0
        order_parts = defaultdict(float)
        order_product_lines: list[dict[str, Any]] = []
        items = order.get("products") or []
        order_incomplete = not bool(items)
        if not items:
            no_products_orders += 1
        for item in items:
            if not isinstance(item, dict):
                continue
            product = _line_product(
                item,
                products_by_id=products_by_id,
                products_by_variant=products_by_variant,
                products_by_sku=products_by_sku,
            )
            product_id = str((product or {}).get("salla_product_id") or "")
            result = calculate_mezan_v2_line_cost(
                item,
                product=product,
                profile=profile_map.get(product_id),
                product_bindings=product_binding_map.get(product_id, []),
                option_bindings=option_map.get(product_id, []),
                resources=resources,
            )
            identity = str(
                product_id
                or item.get("parent_product_id")
                or item.get("product_id")
                or item.get("sku")
                or item.get("variant_id")
                or item.get("name")
                or "unknown"
            ).strip().casefold()
            source_lines[result["base_cost_source"]] += 1
            if result["mezan_cost_complete"]:
                linked_products.add(identity)
            else:
                missing_lines += 1
                order_incomplete = True
                current_missing = missing_products.setdefault(identity, {
                    "identity": identity,
                    "salla_product_id": product_id or str(
                        item.get("parent_product_id")
                        or item.get("product_id")
                        or ""
                    ).strip(),
                    "mezan_product_id": str(
                        (product or {}).get("mezan_product_id") or ""
                    ).strip(),
                    "catalog_product_found": bool(product),
                    "name": (product or {}).get("name") or item.get("name") or "منتج بدون اسم",
                    "sku": item.get("sku") or (product or {}).get("sku") or "",
                    "uses_salla_fallback": False,
                    "missing_everywhere": False,
                    "fallback_sources": set(),
                })
                current_missing["uses_salla_fallback"] = bool(
                    current_missing["uses_salla_fallback"]
                    or result["uses_salla_fallback"]
                )
                current_missing["missing_everywhere"] = bool(
                    current_missing["missing_everywhere"]
                    or not result["base_complete"]
                )
                if result["uses_salla_fallback"]:
                    salla_fallback_products.add(identity)
                    current_missing["fallback_sources"].add(result["base_cost_source"])
                if not result["base_complete"]:
                    missing_all_cost_lines += 1
                    missing_all_cost_products.add(identity)
            raw_order_total += result["line_total"]
            order_parts[result["base_cost_source"]] += result["base_total"]
            order_parts["product_components"] += result["product_components_total"]
            order_parts["selected_options"] += result["selected_options_total"]
            order_product_lines.append({
                "identity": identity,
                "salla_product_id": product_id or str(
                    item.get("parent_product_id") or item.get("product_id") or ""
                ).strip(),
                "mezan_product_id": str((product or {}).get("mezan_product_id") or "").strip(),
                "catalog_product_found": bool(product),
                "name": (product or {}).get("name") or item.get("name") or "منتج بدون اسم",
                "sku": item.get("sku") or (product or {}).get("sku") or "",
                "image_url": (
                    (product or {}).get("main_image")
                    or item.get("image_url")
                    or item.get("image")
                    or ""
                ),
                "quantity": result["quantity"],
                "line_sales": _line_sales_total(item, result["quantity"]),
                "line_cost": result["line_total"],
                "base_complete": result["base_complete"],
                "mezan_cost_complete": result["mezan_cost_complete"],
                "uses_salla_fallback": result["uses_salla_fallback"],
                "base_cost_source": result["base_cost_source"],
            })

        adjusted_total = effective_product_cost(
            {**order, "total_product_cost": raw_order_total},
            policy,
        )
        scale = adjusted_total / raw_order_total if raw_order_total > 0 else 0.0
        totals["total"] += adjusted_total
        for key, amount in order_parts.items():
            totals[key] += amount * scale
        # Apply the same return/cancellation scale used by the authoritative
        # product-cost total. Missing-cost orders have no cost denominator, so
        # retain their sold quantity and sales to surface them for correction.
        product_scale = scale if raw_order_total > 0 else 1.0
        seen_in_order: set[str] = set()
        for line in order_product_lines:
            if product_scale <= 0:
                continue
            identity = str(line["identity"])
            row = product_profit_rows.setdefault(identity, {
                "identity": identity,
                "salla_product_id": line["salla_product_id"],
                "mezan_product_id": line["mezan_product_id"],
                "catalog_product_found": line["catalog_product_found"],
                "name": line["name"],
                "sku": line["sku"],
                "image_url": line["image_url"],
                "units_sold": 0.0,
                "orders_count": 0,
                "total_sales": 0.0,
                "total_cost": 0.0,
                "mezan_cost_complete": True,
                "uses_salla_fallback": False,
                "missing_everywhere": False,
                "cost_sources": set(),
            })
            row["units_sold"] += _float(line["quantity"]) * product_scale
            row["total_sales"] += _float(line["line_sales"]) * product_scale
            row["total_cost"] += _float(line["line_cost"]) * product_scale
            row["mezan_cost_complete"] = bool(
                row["mezan_cost_complete"] and line["mezan_cost_complete"]
            )
            row["uses_salla_fallback"] = bool(
                row["uses_salla_fallback"] or line["uses_salla_fallback"]
            )
            row["missing_everywhere"] = bool(
                row["missing_everywhere"] or not line["base_complete"]
            )
            row["cost_sources"].add(str(line["base_cost_source"]))
            if not row["image_url"] and line["image_url"]:
                row["image_url"] = line["image_url"]
            if identity not in seen_in_order:
                row["orders_count"] += 1
                seen_in_order.add(identity)
        if order_incomplete:
            incomplete_orders += 1

    missing_product_rows = []
    for row in missing_products.values():
        missing_product_rows.append({
            **row,
            "fallback_sources": sorted(row["fallback_sources"]),
        })
    missing_product_rows.sort(key=lambda row: (str(row.get("name") or "").casefold(), row["identity"]))
    product_rows, product_profit_summary = _finalize_product_profit_rows(product_profit_rows)

    return {
        "total": round(totals["total"], 2),
        "breakdown": {
            "mezan_v2_base": round(totals["mezan_v2_base"], 2),
            "mezan_v2_variant": round(totals["mezan_v2_variant"], 2),
            "salla_product_fallback": round(totals["salla_product_fallback"], 2),
            "salla_variant_fallback": round(totals["salla_variant_fallback"], 2),
            "product_components": round(totals["product_components"], 2),
            "selected_options": round(totals["selected_options"], 2),
        },
        "source_lines": dict(source_lines),
        "linked_products_count": len(linked_products - set(missing_products)),
        "missing_products_count": len(missing_products),
        "missing_product_cost_count": missing_lines,
        "missing_all_cost_products_count": len(missing_all_cost_products),
        "missing_all_cost_lines_count": missing_all_cost_lines,
        "salla_fallback_products_count": len(salla_fallback_products),
        "missing_products": missing_product_rows,
        "product_rows": product_rows,
        "product_profit_summary": product_profit_summary,
        "no_products_orders_count": no_products_orders,
        "incomplete_orders_count": incomplete_orders,
        "source_contract": {
            "base_precedence": [
                "mezan_v2_variant",
                "mezan_v2_base",
                "salla_variant_fallback",
                "salla_product_fallback",
            ],
            "always_added": ["product_components", "selected_option_components"],
            "mezan_completion_sources": ["mezan_v2_variant", "mezan_v2_base"],
            "salla_fallback_is_missing_mezan_cost": True,
            "product_sales": "unified_orders.products.total; price*quantity-discount+tax fallback",
            "product_profit": "product sales minus Mezan V2 product cost; ads/shipping/payment fees are not allocated per product",
        },
    }


async def _selected_account_ids(db: Any, user_id: str, provider: str) -> list[str]:
    query: dict[str, Any] = {
        "user_id": user_id,
        "provider": PROVIDER_IDS[provider],
        "connection_status": "connected",
        "connection_provenance": "api_connection",
    }
    if provider in {"snapchat", "meta"}:
        query["mezan_selected"] = True
    rows = await _to_list(
        db.mezan_integration_accounts_v2.find(
            query,
            {"_id": 0, "ad_account_id": 1, "external_account_id": 1, "display_name": 1},
        ),
        100,
    )
    return [
        str(row.get("ad_account_id") or row.get("external_account_id"))
        for row in rows
        if row.get("ad_account_id") or row.get("external_account_id")
    ]


async def _provider_rows(
    db: Any,
    user_id: str,
    provider: str,
    start: str,
    end: str,
) -> list[dict[str, Any]]:
    collections = {"snapchat": SNAP_FACTS, "meta": META_FACTS, "tiktok": TIKTOK_FACTS}
    account_ids = await _selected_account_ids(db, user_id, provider)
    if not account_ids:
        return []
    query: dict[str, Any] = {
        "user_id": user_id,
        "provider": PROVIDER_IDS[provider],
        "ad_account_id": {"$in": account_ids},
        "date": {"$gte": start, "$lte": end},
    }
    if provider == "snapchat":
        query["entity_type"] = "ad_account"
    return await _to_list(db[collections[provider]].find(query, {"_id": 0}), 100000)


def _aggregate_provider_rows(rows: list[dict[str, Any]], start: str, end: str) -> dict[str, Any]:
    selected = [row for row in rows if start <= str(row.get("date") or "") <= end]
    spend = sum(_float(
        row.get("effective_spend_sar")
        if row.get("effective_spend_sar") is not None
        else row.get("spend_sar")
    ) for row in selected)
    orders = sum(
        _float(
            row.get("purchases")
            if row.get("purchases") is not None
            else row.get("conversions")
            if row.get("conversions") is not None
            else (row.get("metrics") or {}).get("conversion_purchases")
        )
        for row in selected
    )
    revenue = sum(_float(row.get("purchase_value_sar")) for row in selected)
    impressions = sum(
        int(_float(row.get("impressions") if row.get("impressions") is not None else (row.get("metrics") or {}).get("impressions")))
        for row in selected
    )
    clicks = sum(
        int(_float(row.get("clicks") if row.get("clicks") is not None else (row.get("metrics") or {}).get("swipes")))
        for row in selected
    )
    return {
        "spend": round(spend, 2),
        "orders": int(round(orders)),
        "revenue": round(revenue, 2),
        "impressions": impressions,
        "clicks": clicks,
        "roas": round(revenue / spend, 2) if spend > 0 else 0.0,
        "cpa": round(spend / orders, 2) if orders > 0 else 0.0,
        "cost_per_order": round(spend / orders, 2) if spend > 0 and orders > 0 else None,
        "cpc": round(spend / clicks, 2) if clicks > 0 else 0.0,
        "cpm": round(spend / impressions * 1000, 2) if impressions > 0 else 0.0,
        "ctr": round(clicks / impressions * 100, 2) if impressions > 0 else 0.0,
    }


def _build_snapchat_account_summaries(
    rows: list[dict[str, Any]],
    accounts_meta: list[dict[str, Any]],
    *,
    month_start: str,
    today: str,
) -> list[dict[str, Any]]:
    """Return account-grain Snapchat KPIs without cross-account mixing."""
    by_account: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        account_id = str(row.get("ad_account_id") or "").strip()
        if account_id:
            by_account[account_id].append(row)

    total_spend = sum(_float(
        row.get("effective_spend_sar")
        if row.get("effective_spend_sar") is not None
        else row.get("spend_sar")
    ) for row in rows)
    accounts: list[dict[str, Any]] = []
    for meta in accounts_meta:
        account_id = str(
            meta.get("ad_account_id") or meta.get("external_account_id") or ""
        ).strip()
        if not account_id:
            continue
        account_rows = by_account.get(account_id, [])
        month_metrics = _aggregate_provider_rows(
            account_rows, month_start, today
        )
        today_metrics = _aggregate_provider_rows(account_rows, today, today)
        latest = max(
            (
                str(row.get("updated_at") or row.get("fetched_at") or "")
                for row in account_rows
                if row.get("updated_at") or row.get("fetched_at")
            ),
            default="",
        )
        first_fact = account_rows[0] if account_rows else {}
        currency = str(
            meta.get("currency") or first_fact.get("currency") or "SAR"
        ).upper()
        account_timezone = str(
            meta.get("timezone")
            or first_fact.get("account_timezone")
            or "Asia/Riyadh"
        )
        accounts.append({
            "id": account_id,
            "external_account_id": account_id,
            "name": meta.get("display_name") or account_id,
            "currency": currency,
            "timezone": account_timezone,
            "today": {"date": today, **today_metrics},
            "month": {"start": month_start, **month_metrics},
            # Backward-compatible aliases used by the compact legacy view.
            "spend": month_metrics["spend"],
            "orders": month_metrics["orders"],
            "revenue": month_metrics["revenue"],
            "roas": month_metrics["roas"],
            "cost_per_order": month_metrics["cost_per_order"],
            "spend_share_pct": round(
                month_metrics["spend"] / total_spend * 100, 2
            ) if total_spend > 0 else 0.0,
            "last_fetched_at": latest or None,
            "credit_limit": None,
            "open_debt": 0,
        })
    return accounts


async def build_mezan_v2_ads(
    db: Any,
    user_id: str,
    *,
    from_date: str | None,
    to_date: str | None,
) -> dict[str, Any]:
    today = _today_riyadh()
    start_text = from_date or today.isoformat()
    end_text = to_date or start_text
    try:
        start_date = date.fromisoformat(start_text)
        end_date = date.fromisoformat(end_text)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="invalid_date_range") from exc
    if end_date < start_date or (end_date - start_date).days + 1 > 90:
        raise HTTPException(status_code=422, detail="invalid_date_range")
    start = start_date.isoformat()
    end = end_date.isoformat()
    snapchat = await load_unified_marketing_dashboard_spend(
        db,
        user_id,
        provider="snapchat_ads",
        date_from=start_date,
        date_to=end_date,
        timezone_name="Asia/Riyadh",
    )
    raw_platform_rows = {
        provider: await _provider_rows(db, user_id, provider, start, end)
        for provider in ("meta", "tiktok")
    }
    raw_platform_rows["snapchat"] = []
    account_costs = await apply_mezan_v2_ad_account_costs(
        db,
        user_id,
        raw_platform_rows,
    )
    platform_rows = account_costs["platform_rows"]
    platform_rows["snapchat"] = list(snapchat.get("rows") or [])
    breakdown = {
        provider: round(sum(_float(
            row.get("effective_spend_sar")
            if row.get("effective_spend_sar") is not None
            else row.get("spend_sar")
        ) for row in rows), 2)
        for provider, rows in platform_rows.items()
        if provider != "snapchat"
    }
    breakdown["snapchat"] = snapchat.get("total_sar")
    google_rows = await _to_list(
        db.daily_costs.find(
            {"user_id": user_id, "date": {"$gte": start, "$lte": end}},
            {"_id": 0, "date": 1, "google_ads": 1},
        ),
        100000,
    )
    breakdown["google_transitional"] = round(
        sum(_float(row.get("google_ads")) for row in google_rows),
        2,
    )
    snap_quality = snapchat.get("quality") if isinstance(snapchat.get("quality"), dict) else {}
    amount_complete = snap_quality.get("amount_complete") is True
    amount_available = (
        amount_complete or snap_quality.get("amount_available") is True
    )
    provisional_amount = (
        amount_available
        and not amount_complete
        and snap_quality.get("provisional") is True
    )
    bank_commissions: dict[str, Any] | None = None
    if amount_available:
        base_bank = {
            key: value for key, value in account_costs.items()
            if key != "platform_rows"
        }
        snap_bank = snapchat.get("bank_commissions") or {}
        base_accounts = list(base_bank.get("accounts") or [])
        snap_accounts = list(snap_bank.get("accounts") or [])
        bank_commissions = {
            **base_bank,
            "accounts": base_accounts + snap_accounts,
            "total_fee_sar": round(
                _float(base_bank.get("total_fee_sar"))
                + _float(snap_bank.get("total_fee_sar")),
                2,
            ),
            "fee_subject_spend_sar": round(
                _float(base_bank.get("fee_subject_spend_sar"))
                + _float(snap_bank.get("fee_subject_spend_sar")),
                2,
            ),
            "total_effective_spend_sar": round(
                _float(base_bank.get("total_effective_spend_sar"))
                + _float(snap_bank.get("total_effective_spend_sar")),
                2,
            ),
            "coverage": {
                "meta_tiktok": base_bank.get("coverage") or {},
                "snapchat": snap_bank.get("coverage") or {},
                "complete": True,
            },
            "google_transitional_spend_sar": breakdown["google_transitional"],
            "google_account_allocation": (
                "not_available" if breakdown["google_transitional"] > 0 else "not_required"
            ),
        }
    history_by_date: dict[str, dict[str, float | None]] = {
        (start_date + timedelta(days=offset)).isoformat(): {
            "snapchat": (snapchat.get("daily_sar") or {}).get(
                (start_date + timedelta(days=offset)).isoformat()
            ),
            "meta": 0.0,
            "tiktok": 0.0,
            "google": 0.0,
        }
        for offset in range((end_date - start_date).days + 1)
    }
    for provider, rows in platform_rows.items():
        if provider == "snapchat":
            continue
        for row in rows:
            row_date = str(row.get("date") or "")[:10]
            if row_date not in history_by_date:
                continue
            history_by_date[row_date][provider] = _float(history_by_date[row_date][provider]) + _float(
                row.get("effective_spend_sar")
                if row.get("effective_spend_sar") is not None
                else row.get("spend_sar")
            )
    for row in google_rows:
        row_date = str(row.get("date") or "")[:10]
        if row_date in history_by_date:
            history_by_date[row_date]["google"] = _float(
                history_by_date[row_date]["google"]
            ) + _float(row.get("google_ads"))
    history = [
        {
            "date": row_date,
            **{
                provider: (round(amount, 2) if amount is not None else None)
                for provider, amount in values.items()
            },
        }
        for row_date, values in sorted(history_by_date.items())
    ]
    provider_metrics = {
        provider: _aggregate_provider_rows(rows, start, end)
        for provider, rows in platform_rows.items()
        if provider != "snapchat"
    }
    if amount_available:
        provider_metrics["snapchat"] = _aggregate_provider_rows(
            platform_rows["snapchat"], start, end
        )
    else:
        provider_metrics["snapchat"] = {
            key: None
            for key in (
                "spend", "orders", "revenue", "impressions", "clicks",
                "roas", "cpa", "cost_per_order", "cpc", "cpm", "ctr",
            )
        }
    provider_metrics["snapchat"].update({
        "data_state": snap_quality.get("data_state") or "unknown_incomplete",
        "coverage_complete": snap_quality.get("coverage_complete") is True,
        "amount_complete": amount_complete,
        "amount_available": amount_available,
        "provisional": provisional_amount,
    })
    known_subtotal = round(sum(
        float(value)
        for provider, value in breakdown.items()
        if provider != "snapchat" and value is not None
    ), 2)
    total = (
        round(known_subtotal + float(breakdown["snapchat"]), 2)
        if amount_available and breakdown["snapchat"] is not None
        else None
    )
    return {
        "total": total,
        "known_subtotal_sar": known_subtotal,
        "breakdown": breakdown,
        "history": history,
        "providers": provider_metrics,
        "spend_quality": {
            "status": (
                "complete"
                if amount_complete
                else "provisional"
                if provisional_amount
                else "incomplete"
            ),
            "amount_complete": amount_complete,
            "amount_available": amount_available,
            "provisional": provisional_amount,
            "known_subtotal_sar": known_subtotal,
            "snapchat": snap_quality,
        },
        "bank_commissions": bank_commissions,
        "source_contract": {
            "snapchat": "unified-marketing-data-v1:snapchat-v2:riyadh-dashboard-spend",
            "meta": f"{META_FACTS}:selected_accounts:spend_native",
            "tiktok": f"{TIKTOK_FACTS}:connected_accounts:spend_native",
            "exchange_rates": "mezan_ad_account_cost_settings_v2:per_account",
            "bank_commissions": "mezan_ad_account_cost_settings_v2:per_account",
            "google": "daily_costs.google_ads:transitional_read_only:no_account_allocation",
            "excluded": ["legacy_ad_ledger", "legacy_ads_currency_settings", "daily_costs.snapchat_ads", "daily_costs.instagram_ads"],
        },
    }


async def build_dashboard_v2_unified_shadow(
    db: Any,
    user_id: str,
    *,
    from_date: date,
    to_date: date,
) -> dict[str, Any]:
    """Compare Dashboard V1 Snapchat spend with the read-only V2 contract.

    This observer is intentionally separate from ``build_mezan_v2_ads`` so a
    slow commerce attribution read can never delay the authoritative
    Dashboard response. It does not persist data and cannot make decisions or
    reach provider/accounting write paths.
    """
    try:
        legacy = await load_snapchat_dashboard_spend(
            db,
            str(user_id),
            start=from_date,
            end=to_date,
        )
        unified = await load_unified_marketing_account_report(
            db,
            str(user_id),
            provider="snapchat_ads",
            date_from=from_date,
            date_to=to_date,
            timezone_name="Asia/Riyadh",
        )
        return build_dashboard_unified_shadow(
            {
                "total_sar": legacy.get("total_sar"),
                "quality": legacy.get("quality") or {},
            },
            unified,
            period_closed=to_date < _today_riyadh(),
        )
    except Exception as exc:  # noqa: BLE001 - observer always fails closed
        return {
            "mode": "shadow",
            "provider": "snapchat_ads",
            "shadow_passed": False,
            "cutover_ready": False,
            "reason": str(type(exc).__name__)[:96],
            "decision_eligibility": {
                "eligible": False,
                "reason": "dashboard_shadow_unavailable",
            },
            "source_only": True,
            "provider_write_reached": False,
            "campaign_write_reached": False,
            "accounting_write_reached": False,
            "qoyod_write_reached": False,
        }


async def build_provider_summary(db: Any, user_id: str, provider: str) -> dict[str, Any]:
    today = _today_riyadh()
    today_s = today.isoformat()
    month_start = today.replace(day=1).isoformat()
    d30_start = (today - timedelta(days=29)).isoformat()
    raw_rows = await _provider_rows(db, user_id, provider, d30_start, today_s)
    costed = await apply_mezan_v2_ad_account_costs(
        db,
        user_id,
        {slug: raw_rows if slug == provider else [] for slug in ("snapchat", "meta", "tiktok")},
    )
    rows = costed["platform_rows"].get(provider, [])
    by_date = defaultdict(float)
    for row in rows:
        by_date[str(row.get("date") or "")] += _float(
            row.get("effective_spend_sar")
            if row.get("effective_spend_sar") is not None
            else row.get("spend_sar")
        )
    return {
        "today": {"date": today_s, **_aggregate_provider_rows(rows, today_s, today_s)},
        "month": {"start": month_start, **_aggregate_provider_rows(rows, month_start, today_s)},
        "last_30d": {"start": d30_start, **_aggregate_provider_rows(rows, d30_start, today_s)},
        "history": [
            {"date": (today - timedelta(days=offset)).isoformat(), "spend": round(by_date[(today - timedelta(days=offset)).isoformat()], 2)}
            for offset in range(29, -1, -1)
        ],
        "source": f"mezan_v2_{provider}_native_with_account_fx",
        "cost_settings_coverage": costed.get("coverage") or {},
        "has_data": bool(rows),
        "connection_status": "ok" if rows else "unavailable",
        "source_only": True,
        "accounting_write_reached": False,
        "qoyod_write_reached": False,
    }


def make_dashboard_v2_router(
    db: Any,
    current_user: Callable[..., Any],
    legacy_dashboard: Callable[..., Any],
    require_owner: Callable[[dict[str, Any]], Any],
) -> APIRouter:
    router = APIRouter(tags=["Mezan Dashboard V2"])

    def owner(user: dict[str, Any]) -> dict[str, Any]:
        require_owner(user)
        return user

    @router.get("/dashboard-v2")
    async def dashboard_v2(
        from_date: str | None = None,
        to_date: str | None = None,
        payment_methods: str | None = None,
        shipping_companies: str | None = None,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        current = owner(user)
        user_id = str(current["id"])
        today = _today_riyadh()
        month_start = today.replace(day=1).isoformat()
        today_s = today.isoformat()
        selected_is_current_month = from_date == month_start and to_date == today_s
        initial_reads = [
            legacy_dashboard(
                user=current,
                from_date=from_date,
                to_date=to_date,
                payment_methods=payment_methods,
                shipping_companies=shipping_companies,
                include_legacy_analyses=False,
                allow_self_heal=False,
            ),
            _filtered_orders(
                db,
                user_id,
                from_date=from_date,
                to_date=to_date,
                payment_methods=payment_methods,
                shipping_companies=shipping_companies,
                include_marketing_attribution=True,
            ),
        ]
        if not selected_is_current_month:
            initial_reads.append(_filtered_orders(
                db,
                user_id,
                from_date=month_start,
                to_date=today_s,
                payment_methods=payment_methods,
                shipping_companies=shipping_companies,
            ))
        initial_results = await asyncio.gather(*initial_reads)
        response = initial_results[0]
        orders = initial_results[1]
        if selected_is_current_month:
            month_orders = orders
        else:
            month_orders = initial_results[2]
        month_kpis = {
            "from_date": month_start,
            "to_date": today_s,
            "total_orders": len(month_orders),
            "total_sales": round(
                sum(_float(order.get("total_amount")) for order in month_orders),
                2,
            ),
        }
        totals = response["totals"]
        # The V2 filtered order set is the authoritative source for both the
        # count and gross sales.  The legacy dashboard can under-report fresh
        # Salla Direct orders when payment-collection fields are still empty,
        # even though each normalized order already has a valid total_amount.
        authoritative_sales = round(
            sum(_float(order.get("total_amount")) for order in orders),
            2,
        )
        previous_sales = _float(totals.get("total_sales"))
        sales_delta = round(authoritative_sales - previous_sales, 2)
        totals["total_orders"] = len(orders)
        totals["total_sales"] = authoritative_sales
        previous_product = _float(totals.get("total_product_cost"))
        previous_ads = _float(totals.get("total_ads_cost"))
        previous_operating = _float(totals.get("operating_expenses_total"))
        salary_total = _float(totals.get("operating_salaries_total"))
        try:
            operating_from = date.fromisoformat(from_date) if from_date else today.replace(day=1)
        except (TypeError, ValueError):
            operating_from = today.replace(day=1)
        try:
            operating_to = date.fromisoformat(to_date) if to_date else today
        except (TypeError, ValueError):
            operating_to = today
        if operating_to < operating_from:
            operating_from, operating_to = operating_to, operating_from
        product_cost, ads, recurring = await asyncio.gather(
            build_mezan_v2_product_cost(db, user_id, orders),
            build_mezan_v2_ads(
                db,
                user_id,
                from_date=from_date,
                to_date=to_date,
            ),
            compute_recurring_obligations_for_range(
                db, user_id, operating_from, operating_to
            ),
        )
        ads["executive_breakdown"] = build_salla_ads_executive_breakdown(
            orders,
            ads,
        )
        recurring_total = _float(recurring.get("total"))
        operating_total = salary_total + recurring_total
        product_total = product_cost["total"]
        ads_total = ads["total"]
        ads_quality = ads.get("spend_quality") or {}
        ads_amount_available = (
            ads_total is not None
            and (
                ads_quality.get("amount_available") is True
                or ads_quality.get("amount_complete") is True
            )
        )
        ads_amount_complete = (
            ads_total is not None
            and ads_quality.get("amount_complete") is True
        )
        ads_amount_provisional = (
            ads_amount_available
            and not ads_amount_complete
            and ads_quality.get("provisional") is True
        )
        if ads_amount_available:
            totals["net_profit"] = round(
                _float(totals.get("net_profit"))
                + sales_delta
                + previous_product - product_total
                + previous_ads - float(ads_total)
                + previous_operating - operating_total,
                2,
            )
        else:
            totals["net_profit"] = None
        totals["net_sales"] = round(
            _float(totals.get("net_sales")) + sales_delta,
            2,
        )
        config = response.get("net_sales_config") or {}
        if config.get("deduct_product_costs", True):
            totals["net_sales"] = round(
                _float(totals.get("net_sales")) + previous_product - product_total,
                2,
            )
        if config.get("deduct_ads", True):
            totals["net_sales"] = (
                round(
                    _float(totals.get("net_sales"))
                    + previous_ads - float(ads_total),
                    2,
                )
                if ads_amount_available
                else None
            )
        if config.get("deduct_operating_expenses", True):
            if totals.get("net_sales") is not None:
                totals["net_sales"] = round(
                    _float(totals.get("net_sales")) + previous_operating - operating_total,
                    2,
                )
        totals.update({
            "total_product_cost": product_total,
            "computed_product_cost": product_total,
            "manual_product_cost": 0.0,
            "missing_product_cost_count": product_cost["missing_products_count"],
            "incomplete_profit_orders_count": product_cost["incomplete_orders_count"],
            "no_products_orders_count": product_cost["no_products_orders_count"],
            "excel_no_products_count": 0,
            "total_ads_cost": ads_total,
            "daily_ads_total": ads_total,
            "ads_spend_data_complete": ads_amount_complete,
            "ads_spend_amount_available": ads_amount_available,
            "ads_spend_provisional": ads_amount_provisional,
            "daily_products_total": product_total,
            "daily_costs_total": (
                round(product_total + float(ads_total), 2)
                if ads_amount_available
                else None
            ),
            "daily_expenses_total": product_total,
            "operating_expenses_total": round(operating_total, 2),
            "operating_rentals_total": recurring["rentals_total"],
            "operating_utilities_total": recurring["utilities_total"],
            "operating_renewals_total": recurring["renewals_total"],
            "operating_recurring_total": recurring["total"],
            "operating_recurring_by_type": recurring["by_type"],
            "operating_prepaid_total": 0.0,
            "operating_prepaid_by_type": {},
            "operating_daily_other_total": 0.0,
            "overall_roas": (
                round(_float(totals.get("total_sales")) / float(ads_total), 2)
                if ads_amount_available and float(ads_total) > 0
                else None
            ),
            "avg_cost_per_order": (
                round(float(ads_total) / int(totals.get("total_orders") or 0), 2)
                if ads_amount_available
                and float(ads_total) > 0
                and int(totals.get("total_orders") or 0) > 0
                else None
            ),
            "tiktok_spend": ads["providers"]["tiktok"]["spend"],
            "tiktok_purchases": ads["providers"]["tiktok"]["orders"],
            "tiktok_revenue": ads["providers"]["tiktok"]["revenue"],
            "tiktok_roas": ads["providers"]["tiktok"]["roas"],
            "meta_spend": ads["providers"]["meta"]["spend"],
            "meta_purchases": ads["providers"]["meta"]["orders"],
            "meta_revenue": ads["providers"]["meta"]["revenue"],
            "meta_roas": ads["providers"]["meta"]["roas"],
            "legacy_analyses_count": 0,
            "analyses_count": 0,
        })
        if ads_amount_available and ads.get("bank_commissions") is not None:
            merge_ad_bank_fees_into_dashboard(response, ads)
        else:
            totals["ad_bank_commission_fees"] = None
            totals["total_payment_fees"] = None
            totals["net_profit"] = None
            if config.get("deduct_payment_fees", True):
                totals["net_sales"] = None
            response["payment_breakdown"] = [
                row
                for row in (response.get("payment_breakdown") or [])
                if row.get("key") != "ad_bank_commissions"
            ]
        response.update({
            "recent_analyses": [],
            "product_cost_v2": product_cost,
            "ads_v2": ads,
            "month_kpis": month_kpis,
            "dashboard_source": "mezan_v2",
            "source_contract": {
                "orders_sales_payment_methods": "unified_orders:mezan_v2",
                "product_cost": product_cost["source_contract"],
                "advertising": ads["source_contract"],
                "employee_salaries": "mezan_employee_salary_contracts_v2",
                "recurring_obligations": "operating_recurring_obligations_v2",
                "shipping_partners": "legacy_shipping_cost_ssot",
                "payment_gateway_fees": "legacy_payment_method_settings + mezan_ad_account_cost_settings_v2",
            },
            "source_only": True,
            "accounting_write_reached": False,
            "qoyod_write_reached": False,
        })
        response["recurring_obligations_v2"] = recurring
        return response

    @router.get("/dashboard-v2/unified-marketing-shadow")
    async def unified_marketing_shadow(
        from_date: str | None = Query(default=None),
        to_date: str | None = Query(default=None),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        current = owner(user)
        today = _today_riyadh()
        try:
            start = date.fromisoformat(from_date or today.isoformat())
            end = date.fromisoformat(to_date or from_date or today.isoformat())
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail="invalid_date_range") from exc
        if end < start or (end - start).days + 1 > 90:
            raise HTTPException(status_code=422, detail="invalid_date_range")
        return await build_dashboard_v2_unified_shadow(
            db,
            str(current["id"]),
            from_date=start,
            to_date=end,
        )

    @router.get("/dashboard-v2/abandoned-carts/recent")
    async def recent_abandoned_carts(
        from_date: str | None = None,
        to_date: str | None = None,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        """Date-scoped cart totals plus active cart rows for the dashboard rail."""
        current = owner(user)
        today_s = _today_riyadh().isoformat()
        start = from_date or today_s
        end = to_date or start
        if end < start:
            start, end = end, start
        live_sync: dict[str, Any] = {
            "attempted": False,
            "reason": "historical_period",
        }
        if end >= today_s:
            try:
                live_sync = await asyncio.wait_for(
                    reconcile_recent_abandoned_carts(
                        db,
                        str(current["id"]),
                        call_provider=call_salla,
                    ),
                    timeout=12,
                )
            except AbandonedCartScopeError:
                live_sync = {"attempted": False, "reason": "scope_not_granted"}
            except asyncio.TimeoutError:
                live_sync = {"attempted": True, "reason": "provider_timeout"}
            except SallaError as exc:
                live_sync = {
                    "attempted": True,
                    "reason": "provider_error",
                    "status_code": int(getattr(exc, "status_code", 0) or 0),
                }
            except Exception as exc:  # noqa: BLE001 - keep dashboard readable
                log.warning(
                    "abandoned_cart_live_reconcile_failed user_id=%s error_type=%s",
                    str(current["id"]),
                    type(exc).__name__,
                )
                live_sync = {"attempted": True, "reason": "unexpected_error"}
        all_rows = await _to_list(
            db.salla_abandoned_carts_v1.find(
                {"user_id": str(current["id"])},
                {
                    "_id": 0,
                    "cart_id": 1,
                    "currency": 1,
                    "total": 1,
                    "items": 1,
                    "customer_identity_id": 1,
                    "purchased": 1,
                    "cart_created_at": 1,
                    "cart_updated_at": 1,
                    "first_seen_at": 1,
                    "last_received_at": 1,
                    "created_at": 1,
                    "updated_at": 1,
                },
            ),
            100000,
        )
        rows, abandoned_count, recovered_count = select_abandoned_carts_for_period(
            all_rows,
            start=start,
            end=end,
        )
        identity_ids = sorted({
            str(row.get("customer_identity_id"))
            for row in rows
            if row.get("customer_identity_id")
        })
        customer_names: dict[str, str] = {}
        if identity_ids:
            identities = await _to_list(
                db[CUSTOMER_IDENTITY_COLLECTION].find(
                    {
                        "user_id": str(current["id"]),
                        "customer_identity_id": {"$in": identity_ids},
                    },
                    {"_id": 0, "customer_identity_id": 1, "private_profile_ciphertext": 1},
                ),
                len(identity_ids),
            )
            for identity in identities:
                try:
                    profile = decrypt_private_payload(identity.get("private_profile_ciphertext"))
                except (RuntimeError, ValueError, TypeError):
                    profile = {}
                name = str(
                    profile.get("name")
                    or " ".join(filter(None, [profile.get("first_name"), profile.get("last_name")]))
                ).strip()
                if name:
                    customer_names[str(identity.get("customer_identity_id"))] = name[:160]

        product_ids = sorted({
            str(item.get("product_id"))
            for row in rows
            for item in (row.get("items") or [])
            if isinstance(item, dict) and item.get("product_id")
        })
        product_images: dict[str, str] = {}
        if product_ids:
            product_id_values: list[Any] = list(product_ids)
            product_id_values.extend(
                int(product_id) for product_id in product_ids if product_id.isdigit()
            )
            products = await _to_list(
                db[PRODUCTS].find(
                    {
                        "user_id": str(current["id"]),
                        "salla_product_id": {"$in": product_id_values},
                    },
                    {"_id": 0, "salla_product_id": 1, "main_image": 1},
                ),
                len(product_ids),
            )
            product_images = {
                str(product.get("salla_product_id")): str(product.get("main_image") or "")
                for product in products
                if product.get("main_image")
            }

        for row in rows:
            row["activity_at"] = _cart_activity_iso(row)
            row["customer_name"] = customer_names.get(
                str(row.get("customer_identity_id") or ""),
                "عميل سلة",
            )
            row.pop("customer_identity_id", None)
            for item in row.get("items") or []:
                if isinstance(item, dict) and not item.get("image_url"):
                    item["image_url"] = product_images.get(str(item.get("product_id") or ""), "")
        return {
            "items": rows,
            "count": len(rows),
            "abandoned_count": abandoned_count,
            "recovered_count": recovered_count,
            "period": {"from": start, "to": end},
            "showing_latest_active_fallback": False,
            "live_sync": live_sync,
            "live": True,
        }

    @router.get("/dashboard-v2/product-cost-summary")
    async def product_cost_summary(user: dict = Depends(current_user)) -> dict[str, Any]:
        current = owner(user)
        user_id = str(current["id"])
        today = _today_riyadh()
        month_start = today.replace(day=1).isoformat()
        today_s = today.isoformat()
        month_orders = await _filtered_orders(
            db, user_id, from_date=month_start, to_date=today_s,
            payment_methods=None, shipping_companies=None,
        )
        month = await build_mezan_v2_product_cost(db, user_id, month_orders)
        today_cost = await build_mezan_v2_product_cost(
            db,
            user_id,
            [order for order in month_orders if str(order.get("order_date") or "")[:10] == today_s],
        )
        return {
            "today_total": today_cost["total"],
            "month_total": month["total"],
            "linked_products_count": month["linked_products_count"],
            "missing_products_count": month["missing_products_count"],
            "missing_all_cost_products_count": month["missing_all_cost_products_count"],
            "salla_fallback_products_count": month["salla_fallback_products_count"],
            "missing_products": month["missing_products"],
            "period": {"from": month_start, "to": today_s},
            "breakdown": month["breakdown"],
            "source_contract": month["source_contract"],
            "source_only": True,
        }

    @router.get("/dashboard-v2/snapchat-accounts-summary")
    async def snapchat_accounts_summary(user: dict = Depends(current_user)) -> dict[str, Any]:
        current = owner(user)
        user_id = str(current["id"])
        today = _today_riyadh()
        start = today.replace(day=1).isoformat()
        end = today.isoformat()
        raw_rows = await _provider_rows(db, user_id, "snapchat", start, end)
        costed = await apply_mezan_v2_ad_account_costs(
            db,
            user_id,
            {"snapchat": raw_rows, "meta": [], "tiktok": []},
        )
        rows = costed["platform_rows"].get("snapchat", [])
        accounts_meta = await _to_list(
            db.mezan_integration_accounts_v2.find(
                {
                    "user_id": user_id,
                    "provider": SNAPCHAT_PROVIDER_ID,
                    "connection_status": "connected",
                    "connection_provenance": "api_connection",
                    "mezan_selected": True,
                },
                {
                    "_id": 0,
                    "ad_account_id": 1,
                    "external_account_id": 1,
                    "display_name": 1,
                    "currency": 1,
                    "timezone": 1,
                },
            ),
            100,
        )
        accounts = _build_snapchat_account_summaries(
            rows,
            accounts_meta,
            month_start=start,
            today=end,
        )
        return {
            "month_start": start,
            "today": end,
            "accounts": accounts,
            "source": SNAP_FACTS,
            "source_contract": {
                "grain": "one independent card per selected Snapchat ad account",
                "identity": "ad_account_id",
                "metrics": "provider native spend multiplied by Mezan 2 account exchange rate",
                "bank_commissions": "reported separately under payment fees",
                "cross_account_allocation": False,
            },
            "source_only": True,
        }

    @router.get("/dashboard-v2/{provider}-summary")
    async def provider_summary(
        provider: str,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        current = owner(user)
        if provider not in {"snapchat", "meta", "tiktok"}:
            return {"today": {}, "month": {}, "last_30d": {}, "history": [], "has_data": False}
        return await build_provider_summary(db, str(current["id"]), provider)

    @router.get("/dashboard-v2/ads-cost-breakdown")
    async def ads_cost_breakdown(
        from_date: str | None = Query(default=None),
        to_date: str | None = Query(default=None),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        current = owner(user)
        ads = await build_mezan_v2_ads(
            db, str(current["id"]), from_date=from_date, to_date=to_date,
        )
        items = [
            {
                "id": provider,
                "date": f"{from_date or _today_riyadh().isoformat()} → {to_date or from_date or _today_riyadh().isoformat()}",
                "ad_account_name": "حسابات ميزان 2 المحددة",
                "ad_provider": provider.replace("_transitional", ""),
                "amount": amount,
                "covered_from_balance": 0,
                "created_debt": 0,
                "source": ads["source_contract"].get(provider.replace("_transitional", ""), "mezan_v2"),
                "description": "قراءة فقط من مصدر لوحة ميزان 2",
            }
            for provider, amount in ads["breakdown"].items()
            if isinstance(amount, (int, float)) and not isinstance(amount, bool) and amount > 0
        ]
        return {
            "total_amount": ads["total"],
            "total_entries": len(items),
            "items": items,
            "by_provider": {
                key.replace("_transitional", ""): value
                for key, value in ads["breakdown"].items()
                if isinstance(value, (int, float))
                and not isinstance(value, bool)
                and value > 0
            },
            "spend_quality": ads.get("spend_quality") or {},
            "by_account": {},
            "source_only": True,
        }

    return router


__all__ = [
    "build_dashboard_v2_unified_shadow",
    "build_mezan_v2_ads",
    "build_mezan_v2_product_cost",
    "calculate_mezan_v2_line_cost",
    "make_dashboard_v2_router",
]
