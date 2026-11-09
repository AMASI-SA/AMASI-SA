"""Filter intake by synchronized categories and actual Mezan resource bindings."""
from __future__ import annotations

import asyncio
import math
import re
from collections import defaultdict
from typing import Any

from component_status_policy import active_component_selector
from product_fulfillment_rules import PRODUCT_RESOURCE_BINDINGS
from product_option_cost_routes import BINDINGS, RESOURCES
from product_v2_details_routes import COST_PROFILES
from product_v2_routes import PRODUCTS, _number, _text


PRODUCT_FIELDS = {
    "id": 1, "mezan_product_id": 1, "salla_product_id": 1, "name": 1, "sku": 1,
    "status": 1, "main_image": 1, "salla_created_at": 1, "created_at": 1,
    "description": 1, "description_html": 1, "images": 1, "categories": 1,
    "details_loaded": 1, "options_count": 1, "option_cost_links_count": 1, "options": 1,
    "raw_salla.options": 1, "raw_salla.product_options": 1,
    "raw_salla_details.options": 1, "raw_salla_details.product_options": 1,
}
BATCH_SIZE = 250


def _category_selector(category_ids: list[str]) -> dict:
    identities: list[str | int] = list(category_ids)
    identities.extend(int(value) for value in category_ids if value.isascii() and value.isdecimal() and len(value) <= 18)
    category_paths = ("categories", "raw_salla.categories", "raw_salla.category", "raw_salla_details.categories", "raw_salla_details.category")
    return {"$or": [
        {f"{path}.{field}": {"$in": identities}}
        for path in category_paths
        for field in ("id", "category_id", "parent_id", "parentId", "parent.id")
    ]}


def _priced_values(product: dict) -> set[tuple[str, str]]:
    """Only positive, explicit option additions count as a surcharge."""
    known = {}
    for data in (product, product.get("raw_salla_details"), product.get("raw_salla")):
        if not isinstance(data, dict):
            continue
        options = data.get("options") or data.get("product_options")
        for option in options if isinstance(options, list) else []:
            if not isinstance(option, dict):
                continue
            option_id = _text(option.get("id") or option.get("option_id"))
            values = option.get("values") or option.get("options") or option.get("details")
            for value in values if isinstance(values, list) else []:
                if not isinstance(value, dict):
                    continue
                value_id = _text(value.get("id") or value.get("value_id"))
                amount = _number(value.get("additional_price"))
                if amount is None:
                    # Merchant API returns price on the option value itself.
                    amount = _number(value.get("price"))
                if amount is not None and math.isfinite(amount):
                    known.setdefault((option_id, value_id), amount)
    return {key for key, amount in known.items() if amount > 0}


async def _batch_facts(db: Any, owner_id: str, products: list[dict]) -> tuple[dict, dict]:
    ids = {value for row in products if (value := row.get("salla_product_id")) not in (None, "")}
    ids.update(str(value) for value in tuple(ids))
    selector = {"user_id": owner_id, "salla_product_id": {"$in": list(ids)}}
    fields = {"_id": 0, "salla_product_id": 1, "resource_id": 1, "option_id": 1, "value_id": 1}
    profiles, direct, option = await asyncio.gather(
        db[COST_PROFILES].find(selector, {"_id": 0, "salla_product_id": 1, "base_cost": 1}).to_list(None),
        db[PRODUCT_RESOURCE_BINDINGS].find(selector, fields).to_list(None),
        db[BINDINGS].find({**selector, "mode": "resource"}, fields).to_list(None),
    )
    resource_ids = {_text(row.get("resource_id")) for row in (*direct, *option)} - {""}
    resources = await db[RESOURCES].find(
        {"user_id": owner_id, "id": {"$in": sorted(resource_ids)}, **active_component_selector()},
        {"_id": 0, "id": 1, "kind": 1, "track_inventory": 1},
    ).to_list(None) if resource_ids else []
    kinds = {
        _text(row["id"]): "component" if row.get("kind") == "stock_component" or row.get("track_inventory") is True else "service"
        for row in resources
    }
    by_product = defaultdict(list)
    for from_option, rows in ((False, direct), (True, option)):
        for row in rows:
            kind = kinds.get(_text(row.get("resource_id")))
            if kind:
                by_product[_text(row.get("salla_product_id"))].append((kind, from_option, row))
    return {_text(row["salla_product_id"]): row for row in profiles}, by_product


def _facts(product: dict, links: list) -> dict[str, bool]:
    priced = _priced_values(product)
    services = [(conditional, row) for kind, conditional, row in links if kind == "service"]
    return {
        "components": any(kind == "component" for kind, _, _ in links),
        "services": bool(services),
        "option_service": any(conditional for conditional, _ in services),
        "independent_service": any(not conditional for conditional, _ in services),
        "option_surcharge": bool(priced),
        "service_surcharge": any(
            conditional
            and bool(_text(row.get("option_id")) and _text(row.get("value_id")))
            and (_text(row.get("option_id")), _text(row.get("value_id"))) in priced
            for conditional, row in services
        ),
    }


async def search_intake(
    db: Any, owner_id: str, *, q: str, category_ids: list[str], status: str,
    page: int, limit: int, flags: dict[str, bool],
) -> dict:
    from ai_store_operations_foundation import product_readiness

    conditions = []
    search = q.strip()
    if search:
        pattern = re.escape(search)
        names = [{key: {"$regex": pattern, "$options": "i"}} for key in ("name", "sku", "salla_product_id")]
        if search.isascii() and search.isdecimal() and len(search) <= 18:
            names.append({"salla_product_id": int(search)})
        conditions.append({"$or": names})
    if category_ids:
        conditions.append(_category_selector(category_ids))
    selector = {"user_id": owner_id, "archived": {"$ne": True}}
    if conditions:
        selector["$and"] = conditions
    cursor = db[PRODUCTS].find(selector, PRODUCT_FIELDS).sort([("salla_created_at", -1), ("created_at", -1), ("_id", -1)])
    offset = (page - 1) * limit
    items = []
    total = ready = without_cost = score = 0

    while batch := await cursor.to_list(BATCH_SIZE):
        profiles, links = await _batch_facts(db, owner_id, batch)
        for product in batch:
            key = _text(product.get("salla_product_id"))
            facts = _facts(product, links.get(key, []))
            if any(facts[name] != expected for name, expected in flags.items()):
                continue
            readiness = product_readiness(product, profiles.get(key))
            if (status == "ready" and not readiness["ready"]) or (status == "needs_attention" and readiness["ready"]):
                continue
            if offset <= total < offset + limit:
                items.append({
                    "mezan_product_id": product.get("mezan_product_id") or product.get("id"),
                    "salla_product_id": product.get("salla_product_id"),
                    "name": product.get("name"),
                    "sku": product.get("sku"),
                    "status": product.get("status"),
                    "main_image": product.get("main_image"),
                    "created_at": product.get("salla_created_at") or product.get("created_at"),
                    "readiness": readiness,
                    "links": facts,
                })
            total += 1
            ready += int(readiness["ready"])
            without_cost += int(not readiness["checks"]["has_base_cost"])
            score += readiness["score"]
    return {
        "items": items, "total": total, "page": page, "limit": limit,
        "total_pages": max(1, (total + limit - 1) // limit),
        "stats": {"ready": ready, "without_cost": without_cost, "average_readiness": round(score / total) if total else 0},
    }
