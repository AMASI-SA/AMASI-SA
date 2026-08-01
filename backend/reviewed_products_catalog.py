"""Aggregated product catalogue for the reviewed fulfillment stage.

The reviewed screen is product-first, not order-first.  It reads only durable
Mezan order/review/product snapshots and never mutates Salla or Qoyod.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Callable

from fastapi import APIRouter, Depends, Query

from order_engine.repository import MongoOrderRepository
from order_engine.service import OrderNotFoundError, get_order
from order_review_routes import WORKFLOWS, _merchant_user_id, _require_reviewer, _text
from product_category_variant_support import _build_category_catalog, _flatten_categories


PRODUCTS = "mezan_products_v2"
UNCATEGORIZED_ID = "uncategorized"
MAX_REVIEWED_ORDERS = 2000


def _plain(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    return value


def _dict(value: Any) -> dict[str, Any]:
    value = _plain(value)
    return dict(value) if isinstance(value, dict) else {}


def _number(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError, OverflowError):
        return default


def _normalized(value: Any) -> str:
    return " ".join(_text(value).casefold().replace("_", " ").split())


def _product_group_key(item: dict[str, Any]) -> str:
    """Group every order line of the same Salla product into one card.

    Personalized text, order item ids and variant selections deliberately do
    not participate in this key.  They remain available in ``source_lines``
    for the next fulfillment step.
    """
    for kind, key in (
        ("product", item.get("product_id")),
        ("parent", item.get("parent_product_id")),
        ("sku", item.get("sku")),
        ("name", _normalized(item.get("name"))),
    ):
        value = _text(key)
        if value:
            return f"{kind}:{value}"
    return f"line:{_text(item.get('order_item_id'))}"


def _review_state_map(workflow: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        _text(row.get("order_item_id")): dict(row)
        for row in workflow.get("items") or []
        if isinstance(row, dict) and _text(row.get("order_item_id"))
    }


def _raw_product_categories(product: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    raw = product.get("raw_salla") if isinstance(product.get("raw_salla"), dict) else {}
    raw_categories = raw.get("categories") or raw.get("category") or []
    if isinstance(raw_categories, dict):
        raw_categories = [raw_categories]
    rows.extend(_flatten_categories(raw_categories))

    stored = product.get("categories") or []
    if isinstance(stored, dict):
        stored = [stored]
    for row in stored if isinstance(stored, list) else []:
        if not isinstance(row, dict):
            continue
        category_id = _text(row.get("id") or row.get("category_id"))
        name = _text(row.get("name") or row.get("title") or row.get("label"))
        if not category_id and not name:
            continue
        rows.append({
            "id": category_id or f"name:{_normalized(name)}",
            "name": name or category_id,
            "path": _text(row.get("path") or row.get("full_name") or name or category_id),
            "parent_id": _text(row.get("parent_id") or row.get("parentId") or row.get("parent")),
            "status": _text(row.get("status") or "active").lower() or "active",
            "is_hidden": bool(row.get("is_hidden")),
        })
    return rows


def build_category_catalog(products: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for product in products:
        for row in _raw_product_categories(product):
            key = _text(row.get("id")) or f"name:{_normalized(row.get('name'))}"
            if not key:
                continue
            merged[key] = {**merged.get(key, {}), **row, "id": key}
    return _build_category_catalog(list(merged.values()))


def _category_ancestor_ids(
    direct_ids: set[str],
    categories_by_id: dict[str, dict[str, Any]],
) -> set[str]:
    result = set(direct_ids)
    for category_id in list(direct_ids):
        current = category_id
        visited: set[str] = set()
        while current and current not in visited:
            visited.add(current)
            parent_id = _text((categories_by_id.get(current) or {}).get("parent_id"))
            if not parent_id or parent_id in {"0", current}:
                break
            result.add(parent_id)
            current = parent_id
    return result


def aggregate_reviewed_products(
    order_workflow_pairs: list[tuple[Any, dict[str, Any]]],
    product_documents: list[dict[str, Any]],
) -> dict[str, Any]:
    product_lookup: dict[str, dict[str, Any]] = {}
    for product in product_documents:
        for identity in (
            product.get("salla_product_id"),
            product.get("product_id"),
            product.get("sku"),
        ):
            value = _text(identity)
            if value:
                product_lookup[value] = product

    category_catalog = build_category_catalog(product_documents)
    categories_by_id = {
        _text(row.get("id")): row
        for row in category_catalog
        if _text(row.get("id"))
    }

    groups: dict[str, dict[str, Any]] = {}
    reviewed_order_numbers: set[str] = set()
    total_source_lines = 0

    for order_value, workflow in order_workflow_pairs:
        order = _dict(order_value)
        order_number = _text(order.get("order_number"))
        if order_number:
            reviewed_order_numbers.add(order_number)
        states = _review_state_map(workflow)

        for item_value in order.get("items") or []:
            item = _dict(item_value)
            if not item:
                continue
            total_source_lines += 1
            key = _product_group_key(item)
            product_id = _text(item.get("product_id") or item.get("parent_product_id"))
            sku = _text(item.get("sku"))
            product = product_lookup.get(product_id) or product_lookup.get(sku) or {}
            product_categories = _raw_product_categories(product)
            direct_category_ids = {
                _text(row.get("id"))
                for row in product_categories
                if _text(row.get("id"))
            }
            category_ids = _category_ancestor_ids(direct_category_ids, categories_by_id)
            if not category_ids:
                category_ids = {UNCATEGORIZED_ID}

            state = states.get(_text(item.get("order_item_id")), {})
            selected_image = _text(state.get("selected_image_url"))
            image = selected_image or _text(product.get("main_image")) or _text(item.get("image_url"))
            quantity = _number(item.get("quantity"), 1.0)

            group = groups.setdefault(key, {
                "group_key": key,
                "product_id": product_id or None,
                "parent_product_id": _text(item.get("parent_product_id")) or None,
                "sku": sku or _text(product.get("sku")) or None,
                "name": _text(product.get("name")) or _text(item.get("name")) or "منتج بدون اسم",
                "image_url": image or None,
                "quantity": 0.0,
                "source_order_numbers": set(),
                "source_line_count": 0,
                "category_ids": set(),
                "direct_category_ids": set(),
                "source_lines": [],
            })
            group["quantity"] += quantity
            group["source_line_count"] += 1
            if order_number:
                group["source_order_numbers"].add(order_number)
            group["category_ids"].update(category_ids)
            group["direct_category_ids"].update(direct_category_ids)
            if not group.get("image_url") and image:
                group["image_url"] = image
            group["source_lines"].append({
                "order_number": order_number,
                "order_item_id": _text(item.get("order_item_id")),
                "quantity": quantity,
                "variant_id": _text(item.get("variant_id")) or None,
                "sku": sku or None,
                "options_normalized": item.get("options_normalized") or {},
                "selected_image_url": selected_image or None,
                "preparation_note": _text(state.get("preparation_note")) or None,
            })

    if any(UNCATEGORIZED_ID in group["category_ids"] for group in groups.values()):
        category_catalog.append({
            "id": UNCATEGORIZED_ID,
            "name": "غير مصنف",
            "path": "غير مصنف",
            "parent_id": "",
            "depth": 0,
            "status": "active",
            "status_label": "نشط",
            "is_hidden": False,
        })
        categories_by_id[UNCATEGORIZED_ID] = category_catalog[-1]

    counts: dict[str, set[str]] = defaultdict(set)
    products: list[dict[str, Any]] = []
    total_quantity = 0.0
    for group in groups.values():
        total_quantity += group["quantity"]
        for category_id in group["category_ids"]:
            counts[category_id].add(group["group_key"])
        products.append({
            **group,
            "quantity": round(group["quantity"], 4),
            "source_order_numbers": sorted(group["source_order_numbers"]),
            "source_order_count": len(group["source_order_numbers"]),
            "category_ids": sorted(group["category_ids"]),
            "direct_category_ids": sorted(group["direct_category_ids"]),
        })

    products.sort(key=lambda row: (_normalized(row.get("name")), row.get("group_key") or ""))
    categories = [
        {**row, "product_count": len(counts.get(_text(row.get("id")), set()))}
        for row in category_catalog
        if counts.get(_text(row.get("id")))
    ]
    categories.sort(key=lambda row: (_number(row.get("depth")), _normalized(row.get("path") or row.get("name"))))

    return {
        "categories": categories,
        "products": products,
        "summary": {
            "reviewed_order_count": len(reviewed_order_numbers),
            "unique_product_count": len(products),
            "total_quantity": round(total_quantity, 4),
            "source_line_count": total_source_lines,
        },
    }


def make_reviewed_products_catalog_router(db: Any, current_user: Callable) -> APIRouter:
    router = APIRouter(prefix="/reviewed-products-v1", tags=["Reviewed Products Catalog"])
    repository = MongoOrderRepository(db)

    @router.get("/catalog")
    async def reviewed_products_catalog(
        limit: int = Query(500, ge=1, le=MAX_REVIEWED_ORDERS),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        reviewer = _require_reviewer(user)
        user_id = _merchant_user_id(reviewer)
        workflows = await db[WORKFLOWS].find(
            {"user_id": user_id, "stage": "reviewed"},
            {"_id": 0},
        ).sort("reviewed_at", 1).limit(limit + 1).to_list(limit + 1)
        truncated = len(workflows) > limit
        workflows = workflows[:limit]

        pairs: list[tuple[Any, dict[str, Any]]] = []
        product_ids: set[str] = set()
        skus: set[str] = set()
        for workflow in workflows:
            order_number = _text(workflow.get("order_number"))
            if not order_number:
                continue
            try:
                order = await get_order(repository, user_id=user_id, order_number=order_number)
            except OrderNotFoundError:
                continue
            pairs.append((order, workflow))
            for item in order.items:
                if _text(item.product_id):
                    product_ids.add(_text(item.product_id))
                if _text(item.parent_product_id):
                    product_ids.add(_text(item.parent_product_id))
                if _text(item.sku):
                    skus.add(_text(item.sku))

        clauses: list[dict[str, Any]] = []
        if product_ids:
            clauses.append({"salla_product_id": {"$in": sorted(product_ids)}})
        if skus:
            clauses.append({"sku": {"$in": sorted(skus)}})
        product_documents = []
        if clauses:
            product_documents = await db[PRODUCTS].find(
                {"user_id": user_id, "$or": clauses},
                {"_id": 0},
            ).to_list(max(len(product_ids) + len(skus), 1))

        result = aggregate_reviewed_products(pairs, product_documents)
        result.update({
            "ok": True,
            "stage": "reviewed",
            "truncated": truncated,
            "order_limit": limit,
        })
        return result

    return router
