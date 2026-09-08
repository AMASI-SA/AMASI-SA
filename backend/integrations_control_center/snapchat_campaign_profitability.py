"""Read-only Snapchat campaign profitability from exact Salla attribution.

This module joins three already-authoritative Mezan sources:

* campaign identity and spend from the selected Snapchat accounts;
* financially included orders from ``unified_orders``;
* product/variant/component costs from the Mezan V2 cost engine.

Profitability is deliberately conservative. A campaign contribution profit is
reported only when every matched order has a usable product cost. Product-level
ad spend is an explicitly labelled revenue-share allocation; it is not claimed
to be a provider fact. Payment, BNPL and shipping fees are left for the next
allocation phase and are never silently estimated here.
"""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any

from dashboard_v2_routes import (
    _filtered_orders,
    _index_products,
    _line_product,
    _line_sales_total,
    _to_list,
    calculate_mezan_v2_line_cost,
)
from order_status_policy import effective_product_cost, get_policy_map
from product_fulfillment_rules import PRODUCT_RESOURCE_BINDINGS
from product_option_cost_routes import BINDINGS, RESOURCES
from product_cost_revision import get_product_cost_revision
from product_v2_details_routes import COST_PROFILES
from product_v2_routes import PRODUCTS, _number

from .snapchat_account_selection import _load_selected_accounts
from .snapchat_campaign_result_source_routes import (
    _campaign_identities,
    _campaign_native_rows,
    _match_order_campaign,
    _norm,
    _source_is_snapchat,
    _text,
    _unique_lookup,
)

CAMPAIGN_PROFITABILITY_SOURCE_MODE = (
    "snapchat_salla_exact_campaign_product_profitability_v1"
)
CAMPAIGN_PROFITABILITY_CACHE_TTL_SECONDS = 5 * 60
CAMPAIGN_PROFITABILITY_ALLOCATION_METHOD = (
    "order_sales_to_products_by_line_revenue_share_then_campaign_ad_spend_by_product_sales_share"
)
MAX_COST_CONTEXT_IDENTITIES = 10_000
MAX_COST_CONTEXT_PRODUCTS = 10_000
MAX_COST_CONTEXT_RELATED_ROWS = 50_000

_CACHE: dict[tuple[str, str, str, int], tuple[datetime, dict[str, Any]]] = {}


def _float(value: Any) -> float:
    try:
        parsed = float(value or 0)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    if parsed != parsed or abs(parsed) == float("inf"):
        return 0.0
    return parsed


def _ratio(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator in {None, 0}:
        return None
    return round(numerator / denominator, 6)


def _cost_status(*, missing_everywhere: bool, uses_salla_fallback: bool) -> str:
    if missing_everywhere:
        return "missing"
    if uses_salla_fallback:
        return "salla_fallback"
    return "complete"


def _cost_identity_scope(
    orders: list[dict[str, Any]],
) -> tuple[set[str], set[str], set[str]]:
    """Return exact product, variant, and SKU identities used by orders."""
    product_ids: set[str] = set()
    variant_ids: set[str] = set()
    skus: set[str] = set()
    nested_keys = ("product", "variant", "sku", "source_product", "source_variant")
    for order in orders:
        for item in list(order.get("products") or []):
            if not isinstance(item, dict):
                continue
            sources = [item]
            sources.extend(
                value
                for key in nested_keys
                if isinstance((value := item.get(key)), dict)
            )
            for source in sources:
                for key in (
                    "salla_product_id",
                    "parent_product_id",
                    "product_id",
                    "source_product_id",
                ):
                    if value := _text(source.get(key)):
                        product_ids.add(value)
                for key in (
                    "variant_id",
                    "product_variant_id",
                    "source_variant_id",
                ):
                    if value := _text(source.get(key)):
                        variant_ids.add(value)
                for key in ("sku", "product_sku", "variant_sku", "code"):
                    if value := _text(source.get(key)):
                        skus.add(value)
    if len(product_ids | variant_ids | skus) > MAX_COST_CONTEXT_IDENTITIES:
        raise ValueError("Snapchat cost identity scope exceeded the safe limit")
    return product_ids, variant_ids, skus


async def _load_cost_context(
    db: Any,
    user_id: str,
    *,
    orders: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    requested_product_ids: set[str] = set()
    variant_ids: set[str] = set()
    skus: set[str] = set()
    product_query: dict[str, Any] = {"user_id": user_id}
    product_limit = 100_000
    restricted = orders is not None
    if restricted:
        requested_product_ids, variant_ids, skus = _cost_identity_scope(
            list(orders or [])
        )
        clauses = [
            *(
                {field: {"$in": sorted(requested_product_ids)}}
                for field in ("salla_product_id", "mezan_product_id", "id")
                if requested_product_ids
            ),
            *(
                {field: {"$in": sorted(variant_ids)}}
                for field in ("variants.id",)
                if variant_ids
            ),
            *(
                {field: {"$in": sorted(skus)}}
                for field in ("sku", "variants.sku")
                if skus
            ),
        ]
        product_query["$or"] = clauses or [{"_id": {"$exists": False}}]
        product_limit = MAX_COST_CONTEXT_PRODUCTS + 1
    products = await _to_list(
        db[PRODUCTS].find(
            product_query,
            {
                "_id": 0,
                "id": 1,
                "salla_product_id": 1,
                "mezan_product_id": 1,
                "name": 1,
                "sku": 1,
                "main_image": 1,
                "cost_price_from_salla": 1,
                "variants": 1,
            },
        ),
        product_limit,
    )
    if restricted and len(products) > MAX_COST_CONTEXT_PRODUCTS:
        raise ValueError("Snapchat cost product scope exceeded the safe limit")
    products_by_id, products_by_variant, products_by_sku = _index_products(products)

    loaded_product_ids = [
        _text(product.get("salla_product_id"))
        for product in products
        if _text(product.get("salla_product_id"))
    ]
    profiles = await _to_list(
        db[COST_PROFILES].find(
            {"user_id": user_id, "salla_product_id": {"$in": loaded_product_ids}},
            {"_id": 0},
        ),
        max(1, len(loaded_product_ids)),
    )
    related_limit = (
        MAX_COST_CONTEXT_RELATED_ROWS + 1
        if restricted
        else 100_000
    )
    option_bindings = await _to_list(
        db[BINDINGS].find(
            {"user_id": user_id, "salla_product_id": {"$in": loaded_product_ids}},
            {"_id": 0},
        ),
        related_limit,
    )
    product_bindings = await _to_list(
        db[PRODUCT_RESOURCE_BINDINGS].find(
            {"user_id": user_id, "salla_product_id": {"$in": loaded_product_ids}},
            {"_id": 0},
        ),
        related_limit,
    )
    if restricted and (
        len(option_bindings) > MAX_COST_CONTEXT_RELATED_ROWS
        or len(product_bindings) > MAX_COST_CONTEXT_RELATED_ROWS
    ):
        raise ValueError("Snapchat related cost scope exceeded the safe limit")
    resource_ids = {
        _text(binding.get("resource_id"))
        for binding in option_bindings + product_bindings
        if _text(binding.get("resource_id"))
    }
    resource_rows = await _to_list(
        db[RESOURCES].find(
            {"user_id": user_id, "id": {"$in": list(resource_ids)}},
            {"_id": 0},
        ),
        max(1, len(resource_ids)),
    )

    option_map: dict[str, list[dict[str, Any]]] = defaultdict(list)
    product_binding_map: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in option_bindings:
        option_map[_text(row.get("salla_product_id"))].append(row)
    for row in product_bindings:
        product_binding_map[_text(row.get("salla_product_id"))].append(row)

    return {
        "products_by_id": products_by_id,
        "products_by_variant": products_by_variant,
        "products_by_sku": products_by_sku,
        "profile_map": {
            _text(row.get("salla_product_id")): row
            for row in profiles
        },
        "option_map": option_map,
        "product_binding_map": product_binding_map,
        "resources": {_text(row.get("id")): row for row in resource_rows},
        "policy": await get_policy_map(db, user_id),
        "read_diagnostics": {
            "restricted_to_order_identities": restricted,
            "requested_product_ids": len(requested_product_ids),
            "requested_variant_ids": len(variant_ids),
            "requested_skus": len(skus),
            "products_materialized": len(products),
            "profiles_materialized": len(profiles),
            "option_bindings_materialized": len(option_bindings),
            "product_bindings_materialized": len(product_bindings),
            "resources_materialized": len(resource_rows),
        },
    }


def _order_cost_and_products(
    order: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    items = order.get("products") or []
    order_sales = max(
        _float(order.get("total_amount") or order.get("total")),
        0.0,
    )
    raw_cost = 0.0
    lines: list[dict[str, Any]] = []
    missing_everywhere = not bool(items)
    uses_salla_fallback = False
    mezan_cost_complete = bool(items)

    for item in items:
        if not isinstance(item, dict):
            continue
        product = _line_product(
            item,
            products_by_id=context["products_by_id"],
            products_by_variant=context["products_by_variant"],
            products_by_sku=context["products_by_sku"],
        )
        product_id = _text((product or {}).get("salla_product_id"))
        result = calculate_mezan_v2_line_cost(
            item,
            product=product,
            profile=context["profile_map"].get(product_id),
            product_bindings=context["product_binding_map"].get(product_id, []),
            option_bindings=context["option_map"].get(product_id, []),
            resources=context["resources"],
        )
        identity = _text(
            product_id
            or item.get("parent_product_id")
            or item.get("product_id")
            or item.get("sku")
            or item.get("variant_id")
            or item.get("name")
            or "unknown"
        ).casefold()
        line = {
            "identity": identity,
            "salla_product_id": product_id or _text(
                item.get("parent_product_id") or item.get("product_id")
            ),
            "mezan_product_id": _text((product or {}).get("mezan_product_id")),
            "name": _text((product or {}).get("name") or item.get("name"))
            or "منتج بدون اسم",
            "sku": _text(item.get("sku") or (product or {}).get("sku")),
            "image_url": _text(
                (product or {}).get("main_image")
                or item.get("image_url")
                or item.get("image")
            ),
            "quantity": _float(result.get("quantity")) or 1.0,
            "source_sales": _line_sales_total(
                item,
                _float(result.get("quantity")) or 1.0,
            ),
            "raw_cost": _float(result.get("line_total")),
            "base_complete": bool(result.get("base_complete")),
            "mezan_cost_complete": bool(result.get("mezan_cost_complete")),
            "uses_salla_fallback": bool(result.get("uses_salla_fallback")),
            "base_cost_source": _text(result.get("base_cost_source")),
        }
        raw_cost += line["raw_cost"]
        missing_everywhere = bool(
            missing_everywhere or not line["base_complete"]
        )
        uses_salla_fallback = bool(
            uses_salla_fallback or line["uses_salla_fallback"]
        )
        mezan_cost_complete = bool(
            mezan_cost_complete and line["mezan_cost_complete"]
        )
        lines.append(line)

    adjusted_cost = effective_product_cost(
        {**order, "total_product_cost": raw_cost},
        context["policy"],
    )
    cost_scale = adjusted_cost / raw_cost if raw_cost > 0 else 0.0
    unit_scale = cost_scale if raw_cost > 0 else 1.0
    source_sales_total = sum(max(_float(line["source_sales"]), 0.0) for line in lines)
    quantity_total = sum(max(_float(line["quantity"]), 0.0) for line in lines)

    for line in lines:
        if source_sales_total > 0:
            weight = max(_float(line["source_sales"]), 0.0) / source_sales_total
        elif quantity_total > 0:
            weight = max(_float(line["quantity"]), 0.0) / quantity_total
        else:
            weight = 0.0
        line["allocated_sales_sar"] = round(order_sales * weight, 6)
        line["cost_sar"] = round(_float(line["raw_cost"]) * cost_scale, 6)
        line["units"] = round(_float(line["quantity"]) * unit_scale, 6)

    allocated_sales = round(
        sum(_float(line.get("allocated_sales_sar")) for line in lines),
        6,
    )
    return {
        "order_sales_sar": round(order_sales, 6),
        "product_cost_sar": round(float(adjusted_cost or 0), 6),
        "allocated_product_sales_sar": allocated_sales,
        "unallocated_sales_sar": round(max(order_sales - allocated_sales, 0), 6),
        "missing_everywhere": missing_everywhere,
        "uses_salla_fallback": uses_salla_fallback,
        "mezan_cost_complete": mezan_cost_complete,
        "no_products": not bool(lines),
        "lines": lines,
    }


def _new_campaign_bucket() -> dict[str, Any]:
    return {
        "orders": 0,
        "sales_sar": 0.0,
        "product_cost_sar": 0.0,
        "allocated_product_sales_sar": 0.0,
        "unallocated_sales_sar": 0.0,
        "missing_cost_orders": 0,
        "fallback_cost_orders": 0,
        "no_products_orders": 0,
        "products": {},
    }


def _add_order_to_campaign(
    bucket: dict[str, Any],
    order_result: dict[str, Any],
) -> None:
    bucket["orders"] += 1
    bucket["sales_sar"] += _float(order_result.get("order_sales_sar"))
    bucket["product_cost_sar"] += _float(order_result.get("product_cost_sar"))
    bucket["allocated_product_sales_sar"] += _float(
        order_result.get("allocated_product_sales_sar")
    )
    bucket["unallocated_sales_sar"] += _float(
        order_result.get("unallocated_sales_sar")
    )
    bucket["missing_cost_orders"] += int(
        bool(order_result.get("missing_everywhere"))
    )
    bucket["fallback_cost_orders"] += int(
        bool(order_result.get("uses_salla_fallback"))
    )
    bucket["no_products_orders"] += int(bool(order_result.get("no_products")))

    seen: set[str] = set()
    for line in order_result.get("lines") or []:
        identity = _text(line.get("identity")) or "unknown"
        product = bucket["products"].setdefault(identity, {
            "identity": identity,
            "salla_product_id": _text(line.get("salla_product_id")),
            "mezan_product_id": _text(line.get("mezan_product_id")),
            "name": _text(line.get("name")) or "منتج بدون اسم",
            "sku": _text(line.get("sku")),
            "image_url": _text(line.get("image_url")),
            "units": 0.0,
            "orders": 0,
            "sales_sar": 0.0,
            "cost_sar": 0.0,
            "missing_everywhere": False,
            "uses_salla_fallback": False,
            "cost_sources": set(),
        })
        product["units"] += _float(line.get("units"))
        product["sales_sar"] += _float(line.get("allocated_sales_sar"))
        product["cost_sar"] += _float(line.get("cost_sar"))
        product["missing_everywhere"] = bool(
            product["missing_everywhere"] or not line.get("base_complete")
        )
        product["uses_salla_fallback"] = bool(
            product["uses_salla_fallback"]
            or line.get("uses_salla_fallback")
        )
        if _text(line.get("base_cost_source")):
            product["cost_sources"].add(_text(line.get("base_cost_source")))
        if identity not in seen:
            product["orders"] += 1
            seen.add(identity)


def _finalize_campaign(
    raw: dict[str, Any],
    *,
    spend_sar: float,
) -> dict[str, Any]:
    sales = round(_float(raw.get("sales_sar")), 2)
    known_cost = round(_float(raw.get("product_cost_sar")), 2)
    missing_cost_orders = int(raw.get("missing_cost_orders") or 0)
    complete = missing_cost_orders == 0
    product_cost = known_cost if complete else None
    gross_profit = (
        round(sales - product_cost, 2)
        if product_cost is not None
        else None
    )
    contribution_profit = (
        round(gross_profit - spend_sar, 2)
        if gross_profit is not None
        else None
    )

    products: list[dict[str, Any]] = []
    allocatable_sales = sum(
        _float(row.get("sales_sar"))
        for row in raw.get("products", {}).values()
    )
    for row in raw.get("products", {}).values():
        product_sales = round(_float(row.get("sales_sar")), 2)
        product_cost_known = round(_float(row.get("cost_sar")), 2)
        status = _cost_status(
            missing_everywhere=bool(row.get("missing_everywhere")),
            uses_salla_fallback=bool(row.get("uses_salla_fallback")),
        )
        reportable_cost = None if status == "missing" else product_cost_known
        share = product_sales / allocatable_sales if allocatable_sales > 0 else 0.0
        allocated_ad_spend = round(spend_sar * share, 2)
        product_profit = (
            round(product_sales - reportable_cost - allocated_ad_spend, 2)
            if reportable_cost is not None
            else None
        )
        products.append({
            "identity": row.get("identity"),
            "salla_product_id": row.get("salla_product_id"),
            "mezan_product_id": row.get("mezan_product_id"),
            "name": row.get("name"),
            "sku": row.get("sku"),
            "image_url": row.get("image_url"),
            "units": round(_float(row.get("units")), 2),
            "orders": int(row.get("orders") or 0),
            "sales_sar": product_sales,
            "product_cost_sar": reportable_cost,
            "allocated_ad_spend_sar": allocated_ad_spend,
            "contribution_profit_sar": product_profit,
            "profit_margin_pct": (
                round(product_profit / product_sales * 100, 2)
                if product_profit is not None and product_sales > 0
                else None
            ),
            "sales_share_pct": round(share * 100, 2),
            "cost_status": status,
            "cost_sources": sorted(row.get("cost_sources") or []),
        })
    products.sort(key=lambda row: (
        row.get("contribution_profit_sar") is None,
        -_float(row.get("contribution_profit_sar")),
        -_float(row.get("sales_sar")),
        _text(row.get("name")).casefold(),
    ))

    return {
        "source": "salla_exact_campaign_match",
        "orders": int(raw.get("orders") or 0),
        "sales_sar": sales,
        "product_cost_sar": product_cost,
        "known_product_cost_sar": known_cost,
        "ad_spend_sar": round(spend_sar, 2),
        "gross_profit_before_ads_sar": gross_profit,
        "contribution_profit_sar": contribution_profit,
        "gross_margin_pct": (
            round(gross_profit / sales * 100, 2)
            if gross_profit is not None and sales > 0
            else None
        ),
        "profit_margin_pct": (
            round(contribution_profit / sales * 100, 2)
            if contribution_profit is not None and sales > 0
            else None
        ),
        "break_even_roas": (
            _ratio(sales, gross_profit)
            if gross_profit is not None and gross_profit > 0
            else None
        ),
        "cost_status": (
            "missing"
            if missing_cost_orders > 0
            else "salla_fallback"
            if int(raw.get("fallback_cost_orders") or 0) > 0
            else "complete"
        ),
        "missing_cost_orders": missing_cost_orders,
        "fallback_cost_orders": int(raw.get("fallback_cost_orders") or 0),
        "no_products_orders": int(raw.get("no_products_orders") or 0),
        "allocated_product_sales_sar": round(
            _float(raw.get("allocated_product_sales_sar")), 2
        ),
        "unallocated_sales_sar": round(
            _float(raw.get("unallocated_sales_sar")), 2
        ),
        "product_count": len(products),
        "products": products,
        "allocation_method": CAMPAIGN_PROFITABILITY_ALLOCATION_METHOD,
        "profit_scope": "sales_minus_product_cost_minus_ad_spend_before_payment_shipping_bnpl_and_operating_allocations",
        "finance_authority": "mezan",
        "profit_metric": "contribution_profit",
        "contribution_profit_available": contribution_profit is not None,
        "net_profit_available": False,
        "net_profit_sar": None,
        "net_profit_unavailable_reason": "campaign_level_full_cost_allocation_not_implemented",
        "provider_sales_used_as_profit": False,
    }


def _total_campaign_spend(
    campaign_spend: dict[tuple[str, str], float],
) -> float:
    """Return report-wide spend, including campaigns without matched orders."""
    return round(
        sum(_float(value) for value in campaign_spend.values()),
        2,
    )


async def build_campaign_profitability(
    db: Any,
    user_id: str,
    *,
    date_from: str,
    date_to: str,
    use_cache: bool = True,
) -> dict[str, Any]:
    cost_revision = await get_product_cost_revision(db, user_id)
    cache_key = (user_id, date_from, date_to, cost_revision)
    now = datetime.now(timezone.utc)
    cached = _CACHE.get(cache_key)
    if use_cache and cached and now - cached[0] < timedelta(
        seconds=CAMPAIGN_PROFITABILITY_CACHE_TTL_SECONDS
    ):
        result = deepcopy(cached[1])
        result["coverage"]["cache_hit"] = True
        return result

    accounts = await _load_selected_accounts(db, user_id)
    account_ids = [
        _text(row.get("ad_account_id"))
        for row in accounts
        if _text(row.get("ad_account_id"))
    ]
    performance_rows = await _campaign_native_rows(
        db,
        user_id,
        account_ids=account_ids,
        date_from=date_from,
        date_to=date_to,
    )
    identities = await _campaign_identities(
        db,
        user_id,
        account_ids=account_ids,
        performance_rows=performance_rows,
    )
    id_lookup = _unique_lookup(identities, "campaign_id")
    name_lookup = _unique_lookup(identities, "campaign_name")
    orders = await _filtered_orders(
        db,
        user_id,
        from_date=date_from,
        to_date=date_to,
        payment_methods=None,
        shipping_companies=None,
        include_marketing_attribution=True,
    )
    cost_context = await _load_cost_context(db, user_id)

    by_campaign_raw: dict[tuple[str, str], dict[str, Any]] = defaultdict(
        _new_campaign_bucket
    )
    matched_orders = 0
    matched_sales = 0.0
    ambiguous_orders = 0
    unattributed_snapchat_orders = 0
    for order in orders:
        key, match_kind = _match_order_campaign(
            order,
            id_lookup=id_lookup,
            name_lookup=name_lookup,
        )
        if key is None:
            if match_kind.startswith("ambiguous"):
                ambiguous_orders += 1
            elif _source_is_snapchat(order):
                unattributed_snapchat_orders += 1
            continue
        order_result = _order_cost_and_products(order, cost_context)
        _add_order_to_campaign(by_campaign_raw[key], order_result)
        matched_orders += 1
        matched_sales += _float(order_result.get("order_sales_sar"))

    spend_by_campaign: dict[tuple[str, str], float] = defaultdict(float)
    for row in performance_rows:
        key = (
            _text(row.get("ad_account_id")),
            _text(row.get("campaign_id") or row.get("external_id")),
        )
        if all(key):
            spend_by_campaign[key] += _float(row.get("spend_sar"))

    by_campaign = {
        key: _finalize_campaign(
            raw,
            spend_sar=round(spend_by_campaign.get(key, 0.0), 6),
        )
        for key, raw in by_campaign_raw.items()
    }
    all_complete = all(
        row.get("product_cost_sar") is not None
        for row in by_campaign.values()
    )
    total_sales = round(sum(_float(row.get("sales_sar")) for row in by_campaign.values()), 2)
    total_known_cost = round(sum(_float(row.get("known_product_cost_sar")) for row in by_campaign.values()), 2)
    total_spend = _total_campaign_spend(spend_by_campaign)
    total_profit = (
        round(total_sales - total_known_cost - total_spend, 2)
        if all_complete
        else None
    )
    result = {
        "by_campaign": by_campaign,
        "totals": {
            "orders": matched_orders,
            "sales_sar": total_sales,
            "product_cost_sar": total_known_cost if all_complete else None,
            "known_product_cost_sar": total_known_cost,
            "ad_spend_sar": total_spend,
            "contribution_profit_sar": total_profit,
            "profit_margin_pct": (
                round(total_profit / total_sales * 100, 2)
                if total_profit is not None and total_sales > 0
                else None
            ),
            "campaigns_with_orders": len(by_campaign),
            "campaigns_with_missing_cost": sum(
                int(row.get("cost_status") == "missing")
                for row in by_campaign.values()
            ),
        },
        "coverage": {
            "source_mode": CAMPAIGN_PROFITABILITY_SOURCE_MODE,
            "eligible_salla_orders": len(orders),
            "exact_matched_orders": matched_orders,
            "exact_matched_sales_sar": round(matched_sales, 2),
            "ambiguous_orders": ambiguous_orders,
            "unattributed_snapchat_orders": unattributed_snapchat_orders,
            "campaigns_with_orders": len(by_campaign),
            "total_ad_spend_scope": "all_campaigns_in_report",
            "campaign_rows_exact_match_only": True,
            "product_cost_source": "mezan_v2_cost_engine_with_salla_fallback_flagging",
            "allocation_method": CAMPAIGN_PROFITABILITY_ALLOCATION_METHOD,
            "excluded_allocations": [
                "payment_gateway_fees",
                "bnpl_fees",
                "merchant_shipping_cost",
                "operating_expenses",
            ],
            "cache_ttl_seconds": CAMPAIGN_PROFITABILITY_CACHE_TTL_SECONDS,
            "cache_hit": False,
            "read_only": True,
        },
    }
    _CACHE[cache_key] = (now, deepcopy(result))
    if len(_CACHE) > 32:
        oldest_key = min(_CACHE, key=lambda key: _CACHE[key][0])
        _CACHE.pop(oldest_key, None)
    return result


def install_snapchat_campaign_profitability() -> None:
    from . import snapchat_campaign_result_source_routes as routes

    current = routes.build_snapchat_result_source_report
    if getattr(current, "_mezan_campaign_profitability", False):
        return

    async def wrapped(*args: Any, **kwargs: Any) -> dict[str, Any]:
        result = await current(*args, **kwargs)
        db = args[0] if args else kwargs.get("db")
        user_id = args[1] if len(args) > 1 else kwargs.get("user_id")
        date_from = _text(result.get("date_from"))
        date_to = _text(result.get("date_to"))
        if db is None or not user_id or not date_from or not date_to:
            return result

        profitability = await build_campaign_profitability(
            db,
            str(user_id),
            date_from=date_from,
            date_to=date_to,
        )
        by_campaign = profitability["by_campaign"]
        for campaign in result.get("campaigns") or []:
            key = (
                _text(campaign.get("account_id")),
                _text(campaign.get("campaign_id")),
            )
            campaign["profitability"] = by_campaign.get(key, {
                "source": "salla_exact_campaign_match",
                "orders": 0,
                "sales_sar": 0.0,
                "product_cost_sar": 0.0,
                "known_product_cost_sar": 0.0,
                "ad_spend_sar": round(_float(campaign.get("spend_sar")), 2),
                "gross_profit_before_ads_sar": 0.0,
                "contribution_profit_sar": round(-_float(campaign.get("spend_sar")), 2),
                "gross_margin_pct": None,
                "profit_margin_pct": None,
                "break_even_roas": None,
                "cost_status": "not_attributed",
                "missing_cost_orders": 0,
                "fallback_cost_orders": 0,
                "no_products_orders": 0,
                "allocated_product_sales_sar": 0.0,
                "unallocated_sales_sar": 0.0,
                "product_count": 0,
                "products": [],
                "allocation_method": CAMPAIGN_PROFITABILITY_ALLOCATION_METHOD,
                "profit_scope": "sales_minus_product_cost_minus_ad_spend_before_payment_shipping_bnpl_and_operating_allocations",
            })

        result.setdefault("totals", {})["profitability"] = profitability["totals"]
        result.setdefault("source", {})["campaign_profitability"] = profitability["coverage"]
        result.setdefault("policy", {}).update({
            "campaign_profitability_read_only": True,
            "product_ad_spend_is_allocated_not_provider_fact": True,
            "profitability_provider_write_reached": False,
            "profitability_accounting_write_reached": False,
            "profitability_qoyod_write_reached": False,
        })
        return result

    wrapped._mezan_campaign_profitability = True  # type: ignore[attr-defined]
    wrapped._mezan_campaign_profitability_base = current  # type: ignore[attr-defined]
    routes.build_snapchat_result_source_report = wrapped


__all__ = [
    "CAMPAIGN_PROFITABILITY_ALLOCATION_METHOD",
    "CAMPAIGN_PROFITABILITY_CACHE_TTL_SECONDS",
    "CAMPAIGN_PROFITABILITY_SOURCE_MODE",
    "build_campaign_profitability",
    "install_snapchat_campaign_profitability",
]
