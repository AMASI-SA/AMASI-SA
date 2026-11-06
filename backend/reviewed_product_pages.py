"""Reviewed product navigation headers and bounded canonical piece pages.

Headers grant no allocation authority. Every returned selectable piece still
comes from the original canonical catalogue and keeps its immutable revision.
"""
from __future__ import annotations

import base64
import json
from typing import Any

from fastapi import HTTPException

from reviewed_products_catalog import (
    WORKFLOWS, PRODUCTS, assignment_workflow_query, _available_workflow_pipeline,
    _text, _normalized, _product_group_key, _raw_product_categories, build_category_catalog,
    load_reviewed_product_context,
)


async def _group_index(db: Any, *, user_id: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    pipeline = _available_workflow_pipeline({"user_id": user_id, **assignment_workflow_query()})
    pipeline.append({"$project": {"order_number": 1,
        **{"items." + field: 1 for field in (
            "order_item_id", "product_id", "parent_product_id", "product_key", "product_name", "name",
            "sku", "selected_image_url", "supplier_export", "source_addition", "review_status")}}})
    cursor = db[WORKFLOWS].aggregate(pipeline, batchSize=64)
    groups: dict[str, Any] = {}
    try:
        async for workflow in cursor:
            number = _text(workflow.get("order_number"))
            states = [row for row in workflow.get("items") or [] if isinstance(row, dict)
                and row.get("supplier_export") is not False
                and not (row.get("source_addition") is True and row.get("review_status") != "reviewed")]
            if not states and workflow.get("items"):
                continue
            if not states:
                states = [{"_fallback_order": number, "name": "منتجات الطلب #" + number}]
            for state in states:
                kind, _, value = _text(state.get("product_key")).partition(":")
                identity = {"product_id": state.get("product_id") or (value if kind == "product" else None),
                    "parent_product_id": state.get("parent_product_id") or (value if kind == "parent" else None),
                    "sku": state.get("sku") or (value if kind == "sku" else None),
                    "name": state.get("product_name") or state.get("name"),
                    "order_item_id": state.get("order_item_id")}
                fallback = bool(state.get("_fallback_order") or not identity.get("order_item_id"))
                key = "order:" + number if fallback else _product_group_key(identity)
                group = groups.setdefault(key, {"key": key, "product_id": _text(identity.get("product_id") or identity.get("parent_product_id")),
                    "product_name": _text(identity.get("name")) or "منتج", "sku": _text(identity.get("sku")),
                    "image_url": _text(state.get("selected_image_url")), "category_ids": [],
                    "_lines": {}, "_fallback_orders": set()})
                group["_lines"].setdefault(number, set()).add(_text(state.get("order_item_id")))
                if fallback:
                    group["_fallback_orders"].add(number)
    finally:
        await cursor.close()
    product_ids = sorted({row["product_id"] for row in groups.values()} - {""})
    skus = sorted({row["sku"] for row in groups.values()} - {""})
    clauses = []
    if product_ids:
        clauses.append({"salla_product_id": {"$in": product_ids}})
    if skus:
        clauses.append({"sku": {"$in": skus}})
    products = await db[PRODUCTS].find({"user_id": user_id, "$or": clauses},
        {"_id": 0, "salla_product_id": 1, "sku": 1, "name": 1, "main_image": 1, "categories": 1}
    ).to_list(None) if clauses else []
    by_id = {_text(row.get("salla_product_id")): row for row in products if _text(row.get("salla_product_id"))}
    by_sku = {_text(row.get("sku")): row for row in products if _text(row.get("sku"))}
    for group in groups.values():
        product = by_id.get(group["product_id"]) or by_sku.get(group["sku"]) or {}
        if not group["image_url"]:
            image = product.get("main_image")
            group["image_url"] = _text(image.get("url") if isinstance(image, dict) else image)
        group["category_ids"] = [_text(row.get("id")) for row in _raw_product_categories(product)]
    return groups, build_category_catalog(products)


async def reviewed_groups(db: Any, *, user_id: str) -> dict[str, Any]:
    groups, categories = await _group_index(db, user_id=user_id)
    headers = [{key: value for key, value in row.items() if not key.startswith("_")} for row in groups.values()]
    headers.sort(key=lambda row: (_normalized(row["product_name"]), row["key"]))
    return {"ok": True, "read_only": True, "selection_grain": "group_preview", "groups": headers, "categories": categories}


def _position(row: dict[str, Any]) -> tuple[str, str, int, str]:
    source = (row.get("source_lines") or [{}])[0]
    return (_text(source.get("order_number")), _text(source.get("order_item_id")),
            int(row.get("unit_index") or 0), _text(row.get("ready_unit_id")))


def _cursor(value: tuple[str, str, int, str]) -> str:
    return base64.urlsafe_b64encode(json.dumps(value, separators=(",", ":")).encode()).decode().rstrip("=")


def _decode(value: str) -> tuple[str, str, int, str]:
    if not value:
        return ("", "", 0, "")
    try:
        row = json.loads(base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)))
        if not isinstance(row, list) or len(row) != 4 or not all(isinstance(row[i], str) for i in (0, 1, 3)):
            raise ValueError()
        if type(row[2]) is not int or row[2] < 0:
            raise ValueError()
        return tuple(row)
    except (ValueError, TypeError, UnicodeError) as exc:
        raise HTTPException(status_code=400, detail={"code": "reviewed_group_cursor_invalid"}) from exc


async def reviewed_group_page(db: Any, *, user_id: str, group_key: str, after: str) -> dict[str, Any]:
    position = _decode(after)
    groups, _categories = await _group_index(db, user_id=user_id)
    target = groups.get(group_key)
    if not target:
        return {"ok": True, "read_only": True, "selection_grain": "physical_piece", "products": [],
                "categories": [], "truncated": False, "next_cursor": None}
    numbers = sorted(number for number in target["_lines"] if number >= position[0])
    batch = numbers[:10]
    context = await load_reviewed_product_context(db, user_id=user_id, limit=10, order_numbers=batch)
    selected = []
    for row in context["catalog"].get("products") or []:
        source = (row.get("source_lines") or [{}])[0]
        number, line = _text(source.get("order_number")), _text(source.get("order_item_id"))
        if number not in target["_fallback_orders"] and line not in target["_lines"].get(number, set()):
            continue
        if _position(row) > position:
            selected.append(row)
    selected.sort(key=_position)
    next_cursor = None
    if len(selected) > 10:
        next_cursor = _cursor(_position(selected[9]))
    elif len(numbers) > len(batch) and batch:
        next_cursor = _cursor((batch[-1], "\uffff", 2147483647, ""))
    return {"ok": True, "read_only": True, "selection_grain": "physical_piece", "products": selected[:10],
            "categories": context["catalog"].get("categories") or [], "truncated": False, "next_cursor": next_cursor}
