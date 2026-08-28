"""Aggregated product catalogue for the reviewed fulfillment stage.

The reviewed screen is product-first, not order-first. It reads durable Mezan
order/review/product snapshots and the immutable preparation allocation ledger.
It never mutates Salla or Qoyod.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from fastapi import APIRouter, Depends, Query

from order_engine.repository import MongoOrderRepository
from order_engine.service import OrderNotFoundError, get_order
from order_review_routes import WORKFLOWS, _merchant_user_id, _require_reviewer, _text
from product_category_variant_support import _build_category_catalog, _flatten_categories
from reviewed_preparation_v3 import (
    stable_ready_item_id,
    stable_ready_unit_id,
    stable_reviewed_line_revision,
    stable_reviewed_product_revision,
)


PRODUCTS = "mezan_products_v2"
PREPARATION_UNIT_ALLOCATIONS = "mezan_preparation_unit_allocations_v2"
ACTIVE_PREPARATION_ALLOCATION_STATUSES = ("reserved", "committed")
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


def _unit_quantity(value: Any) -> int:
    """Return a physical piece count and fail closed for fractional quantities."""
    number = _number(value)
    rounded = int(round(number))
    if number <= 0 or abs(number - rounded) > 0.000001:
        return 0
    return rounded


def _normalized(value: Any) -> str:
    return " ".join(_text(value).casefold().replace("_", " ").split())


def _product_group_key(item: dict[str, Any]) -> str:
    """Group every order line of the same Salla product into one card.

    Personalized text, order item ids and variant selections deliberately do
    not participate in this key. They remain available in ``source_lines``
    for preparation-file allocation.
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


def _order_items_with_review_snapshot(
    order: dict[str, Any],
    workflow: dict[str, Any],
) -> list[dict[str, Any]]:
    """Keep reviewed lines visible when Salla later omits live order items.

    Review completion freezes the operational identity, quantity, SKU and
    customer specifications for every line.  The live Salla order remains the
    preferred source, while missing identities are appended from that durable
    snapshot.  Matching by order-item identity prevents double counting when
    Salla still returns a line normally.
    """
    live_items = [
        _dict(value) for value in (order.get("items") or []) if _dict(value)
    ]
    live_ids = {
        _text(item.get("order_item_id"))
        for item in live_items
        if _text(item.get("order_item_id"))
    }
    live_by_id = {
        _text(item.get("order_item_id")): item
        for item in live_items
        if _text(item.get("order_item_id"))
    }

    def product_signature(item: dict[str, Any]) -> tuple[str, str, str, str]:
        return (
            _text(item.get("product_id")),
            _text(item.get("parent_product_id")),
            _text(item.get("variant_id")),
            _text(item.get("sku")).upper(),
        )

    # Old review snapshots can predate preservation of Salla's order-item id.
    # Track live quantity by product identity so those anonymous rows can be
    # reconciled without duplicating a line that Salla still returns.
    unmatched_live_quantity: dict[tuple[str, str, str, str], int] = defaultdict(int)
    for item in live_items:
        unmatched_live_quantity[product_signature(item)] += _unit_quantity(item.get("quantity"))

    order_number = _text(order.get("order_number") or workflow.get("order_number"))
    for snapshot_index, row in enumerate(workflow.get("items") or []):
        snapshot = _dict(row)
        order_item_id = _text(snapshot.get("order_item_id"))
        quantity = _unit_quantity(snapshot.get("quantity"))
        if quantity <= 0:
            continue

        signature = product_signature(snapshot)
        if order_item_id and order_item_id in live_ids:
            live = live_by_id[order_item_id]
            # The review snapshot is the durable identity captured before
            # Salla later returned a sparse in-progress order.  Restore only
            # missing facts; never replace a current non-empty value.
            for field in (
                "product_id",
                "parent_product_id",
                "variant_id",
                "source_item_id",
                "sku",
                "barcode",
            ):
                if not _text(live.get(field)) and _text(snapshot.get(field)):
                    live[field] = snapshot.get(field)
            unmatched_live_quantity[signature] = max(
                0, unmatched_live_quantity[signature] - quantity
            )
            continue

        if not order_item_id:
            represented = min(quantity, unmatched_live_quantity[signature])
            unmatched_live_quantity[signature] -= represented
            quantity -= represented
            if quantity <= 0:
                continue
            order_item_id = f"review-snapshot:{order_number}:{snapshot_index}"

        live_items.append({
            **snapshot,
            "order_item_id": order_item_id,
            "quantity": quantity,
            "name": _text(snapshot.get("product_name")) or "منتج بدون اسم",
            "image_url": _text(snapshot.get("selected_image_url")) or None,
            "options_normalized": snapshot.get("specifications_snapshot") or {},
            "_review_snapshot_state": True,
            "_review_snapshot_index": snapshot_index,
        })
        live_ids.add(order_item_id)
    return live_items


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
    products_by_name: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for product in product_documents:
        for identity in (
            product.get("salla_product_id"),
            product.get("product_id"),
            product.get("sku"),
        ):
            value = _text(identity)
            if value:
                product_lookup[value] = product
        normalized_name = _normalized(product.get("name"))
        if normalized_name:
            products_by_name[normalized_name].append(product)

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
        order_items = _order_items_with_review_snapshot(order, workflow)
        total_products_in_order = sum(
            _unit_quantity(item.get("quantity")) for item in order_items
        ) or 1
        shipping = _dict(order.get("shipping"))

        for line_index, item in enumerate(order_items):
            state = states.get(_text(item.get("order_item_id")), {})
            if item.get("_review_snapshot_state"):
                state = {**item, **state}
            # A reviewer may deliberately exclude a line from supplier files or
            # route it to internal preparation. Such a line is already routed
            # operationally and must not appear in this supplier-file queue.
            if state.get("supplier_export") is False:
                continue

            quantity_units = _unit_quantity(item.get("quantity"))
            if quantity_units <= 0:
                continue
            total_source_lines += 1
            frozen_product_key = _text(state.get("product_key"))
            frozen_kind, _, frozen_value = frozen_product_key.partition(":")
            raw_product_id = _text(
                item.get("product_id")
                or item.get("parent_product_id")
                or state.get("product_id")
                or state.get("parent_product_id")
                or (frozen_value if frozen_kind in {"product", "parent"} else "")
            )
            raw_sku = _text(
                item.get("sku")
                or state.get("sku")
                or (frozen_value if frozen_kind == "sku" else "")
            )
            product = product_lookup.get(raw_product_id) or product_lookup.get(raw_sku) or {}
            if not product:
                # Some legacy Salla/order snapshots lose both SKU and product
                # id while retaining the exact catalog name. Resolve by name
                # only when it is unambiguous so the line rejoins the real
                # product card instead of becoming a second name-only card.
                name_matches = products_by_name.get(_normalized(item.get("name")), [])
                if len(name_matches) == 1:
                    product = name_matches[0]
            product_id = _text(
                product.get("salla_product_id")
                or product.get("product_id")
                or raw_product_id
            )
            sku = raw_sku or _text(product.get("sku"))
            canonical_item = {
                **item,
                "product_id": product_id or None,
                "parent_product_id": _text(
                    item.get("parent_product_id") or state.get("parent_product_id")
                ) or None,
                "variant_id": _text(
                    item.get("variant_id") or state.get("variant_id")
                ) or None,
                "sku": sku or None,
                "name": _text(product.get("name")) or _text(item.get("name")),
            }
            key = _product_group_key(canonical_item)
            product_categories = _raw_product_categories(product)
            direct_category_ids = {
                _text(row.get("id"))
                for row in product_categories
                if _text(row.get("id"))
            }
            category_ids = _category_ancestor_ids(direct_category_ids, categories_by_id)
            if not category_ids:
                category_ids = {UNCATEGORIZED_ID}

            selected_image = _text(state.get("selected_image_url"))
            image = selected_image or _text(product.get("main_image")) or _text(item.get("image_url"))
            product_name = _text(product.get("name")) or _text(item.get("name")) or "منتج بدون اسم"

            group = groups.setdefault(key, {
                "group_key": key,
                "product_id": product_id or None,
                "parent_product_id": _text(canonical_item.get("parent_product_id")) or None,
                "sku": sku or _text(product.get("sku")) or None,
                "name": product_name,
                "image_url": image or None,
                "quantity": 0,
                "source_order_numbers": set(),
                "source_line_count": 0,
                "category_ids": set(),
                "direct_category_ids": set(),
                "source_lines": [],
            })
            group["quantity"] += quantity_units
            group["source_line_count"] += 1
            if order_number:
                group["source_order_numbers"].add(order_number)
            group["category_ids"].update(category_ids)
            group["direct_category_ids"].update(direct_category_ids)
            if not group.get("image_url") and image:
                group["image_url"] = image
            source_line = {
                "group_key": key,
                "order_number": order_number,
                "order_item_id": _text(item.get("order_item_id")),
                "line_index": line_index,
                "quantity": quantity_units,
                "variant_id": _text(canonical_item.get("variant_id")) or None,
                "parent_product_id": _text(canonical_item.get("parent_product_id")) or None,
                "barcode": _text(item.get("barcode") or state.get("barcode")) or None,
                "source_item_id": _text(item.get("source_item_id") or state.get("source_item_id")) or None,
                "product_id": product_id or None,
                "product_name": product_name,
                "sku": sku or None,
                "image_url": image or None,
                "order_date": order.get("created_at"),
                "reviewed_at": workflow.get("reviewed_at"),
                "incident_recovery_id": _text(workflow.get("incident_recovery_id")) or None,
                "shipping_company": _text(shipping.get("company")) or None,
                "total_products_in_order": total_products_in_order,
                # Prefer the frozen reviewed choice, including an empty mapping.
                # Live OrderDTO enrichment may otherwise reorder these options.
                "options_normalized": (
                    state.get("specifications_snapshot")
                    if state.get("specifications_snapshot") is not None
                    else (item.get("options_normalized") or {})
                ),
                "selected_image_url": selected_image or None,
                "preparation_note": _text(state.get("preparation_note")) or None,
                "identity_source": "review_snapshot" if item.get("_review_snapshot_state") else "reviewed_ready",
                "review_snapshot_index": item.get("_review_snapshot_index"),
                # Normal reviewed lines become immutable, file-ready records.
                # This is derived lazily from the durable review workflow so
                # existing reviewed products need no destructive backfill.
                "ready_item_identity": ({
                    "order_item_id": _text(item.get("order_item_id")),
                    "source_item_id": _text(
                        state.get("source_item_id") or item.get("source_item_id")
                    ),
                    "product_id": _text(
                        state.get("product_id") or item.get("product_id") or product_id
                    ),
                    "parent_product_id": _text(
                        state.get("parent_product_id") or item.get("parent_product_id")
                    ),
                    "variant_id": _text(
                        state.get("variant_id") or item.get("variant_id")
                    ),
                    "sku": _text(state.get("sku") or item.get("sku") or sku),
                    "barcode": _text(state.get("barcode") or item.get("barcode")),
                    "quantity": quantity_units,
                    "product_name": _text(
                        state.get("product_name") or item.get("name") or product_name
                    ),
                    "selected_image_url": selected_image or _text(item.get("image_url")),
                    "options": (
                        state.get("specifications_snapshot")
                        if state.get("specifications_snapshot") is not None
                        else (
                            state.get("options")
                            if state.get("options") is not None
                            else (item.get("options_normalized") or {})
                        )
                    ),
                } if not item.get("_review_snapshot_state") else None),
                # Keep the immutable facts that identify the reviewed snapshot
                # separate from the Product V2-enriched display identity above.
                # File creation can then prove it is materialising the same
                # reviewed line without treating a newly resolved SKU/product id
                # as a stale-data conflict.
                "review_snapshot_identity": ({
                    "order_item_id": _text(item.get("order_item_id")),
                    "source_item_id": _text(item.get("source_item_id")),
                    "product_id": _text(item.get("product_id")),
                    "parent_product_id": _text(item.get("parent_product_id")),
                    "variant_id": _text(item.get("variant_id")),
                    "sku": _text(item.get("sku")),
                    "barcode": _text(item.get("barcode")),
                    "quantity": quantity_units,
                    "options": (
                        state.get("specifications_snapshot")
                        if state.get("specifications_snapshot") is not None
                        else (item.get("options_normalized") or {})
                    ),
                } if item.get("_review_snapshot_state") else None),
            }
            source_line["ready_item_id"] = stable_ready_item_id(source_line)
            group["source_lines"].append(source_line)

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

    counts: dict[str, set[str]] = defaultdict(set)
    products: list[dict[str, Any]] = []
    total_quantity = 0
    for group in groups.values():
        for source_line in group["source_lines"]:
            source_line["line_revision"] = stable_reviewed_line_revision(source_line)
        group["source_lines"].sort(key=lambda row: (
            0 if _text(row.get("incident_recovery_id")) else 1,
            _text(row.get("reviewed_at")),
            _text(row.get("order_number")),
            int(row.get("line_index") or 0),
        ))
        total_quantity += int(group["quantity"])
        for category_id in group["category_ids"]:
            counts[category_id].add(group["group_key"])
        product_row = {
            **group,
            "quantity": int(group["quantity"]),
            "source_order_numbers": sorted(group["source_order_numbers"]),
            "source_order_count": len(group["source_order_numbers"]),
            "category_ids": sorted(group["category_ids"]),
            "direct_category_ids": sorted(group["direct_category_ids"]),
        }
        product_row["revision"] = stable_reviewed_product_revision(product_row)
        products.append(product_row)

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
            "total_quantity": total_quantity,
            "source_line_count": total_source_lines,
        },
    }


def apply_preparation_allocations(
    catalog: dict[str, Any],
    allocation_documents: list[dict[str, Any]],
) -> dict[str, Any]:
    """Subtract active unit allocations and expose only quantities still free."""
    used_units: dict[tuple[str, str], set[int]] = defaultdict(set)
    for row in allocation_documents:
        if _text(row.get("status")) not in ACTIVE_PREPARATION_ALLOCATION_STATUSES:
            continue
        key = (_text(row.get("order_number")), _text(row.get("order_item_id")))
        try:
            unit_index = int(row.get("unit_index"))
        except (TypeError, ValueError):
            continue
        if key[0] and key[1] and unit_index > 0:
            used_units[key].add(unit_index)

    remaining_products: list[dict[str, Any]] = []
    category_counts: dict[str, set[str]] = defaultdict(set)
    remaining_order_numbers: set[str] = set()
    original_total = allocated_total = remaining_total = 0

    for product in catalog.get("products") or []:
        source_lines = []
        product_original = product_allocated = product_remaining = 0
        remaining_orders: set[str] = set()
        for line in product.get("source_lines") or []:
            quantity = _unit_quantity(line.get("quantity"))
            if quantity <= 0:
                continue
            key = (_text(line.get("order_number")), _text(line.get("order_item_id")))
            allocated_indices = sorted(
                index for index in used_units.get(key, set()) if index <= quantity
            )
            allocated = len(allocated_indices)
            remaining = max(0, quantity - allocated)
            product_original += quantity
            product_allocated += allocated
            product_remaining += remaining
            if remaining <= 0:
                continue
            available_indices = [
                index for index in range(1, quantity + 1)
                if index not in set(allocated_indices)
            ]
            source_lines.append({
                **line,
                "allocated_quantity": allocated,
                "remaining_quantity": remaining,
                "allocated_unit_indices": allocated_indices,
                "available_unit_indices": available_indices,
            })
            order_number = _text(line.get("order_number"))
            if order_number:
                remaining_orders.add(order_number)
                remaining_order_numbers.add(order_number)

        original_total += product_original
        allocated_total += product_allocated
        remaining_total += product_remaining
        if product_remaining <= 0:
            continue
        row = {
            **product,
            "quantity": product_remaining,
            "total_quantity": product_original,
            "allocated_quantity": product_allocated,
            "remaining_quantity": product_remaining,
            "source_lines": source_lines,
            "source_order_numbers": sorted(remaining_orders),
            "source_order_count": len(remaining_orders),
            "source_line_count": len(source_lines),
        }
        for source_line in row["source_lines"]:
            source_line["line_revision"] = (
                source_line.get("line_revision")
                or stable_reviewed_line_revision(source_line)
            )
        row["revision"] = stable_reviewed_product_revision(row)
        remaining_products.append(row)
        for category_id in row.get("category_ids") or []:
            category_counts[_text(category_id)].add(_text(row.get("group_key")))

    remaining_categories = [
        {**row, "product_count": len(category_counts.get(_text(row.get("id")), set()))}
        for row in catalog.get("categories") or []
        if category_counts.get(_text(row.get("id")))
    ]
    summary = {
        **(catalog.get("summary") or {}),
        "reviewed_order_count": len(remaining_order_numbers),
        "unique_product_count": len(remaining_products),
        "total_quantity": remaining_total,
        "original_quantity": original_total,
        "allocated_quantity": allocated_total,
        "remaining_quantity": remaining_total,
        "source_line_count": sum(
            int(row.get("source_line_count") or 0) for row in remaining_products
        ),
    }
    return {
        **catalog,
        "products": remaining_products,
        "categories": remaining_categories,
        "summary": summary,
    }


def expand_reviewed_ready_units(catalog: dict[str, Any]) -> dict[str, Any]:
    """Expose each normal reviewed piece as its own selectable card.

    Recovery lines deliberately remain on their existing aggregated snapshot
    path. Normal lines receive a deterministic unit identity based on the
    reviewed order line and physical unit index, so the UI and file builder
    select the exact same piece rather than re-resolving an aggregate quantity.
    """
    products: list[dict[str, Any]] = []
    category_counts: dict[str, set[str]] = defaultdict(set)

    for product in catalog.get("products") or []:
        recovery_lines: list[dict[str, Any]] = []
        for source in product.get("source_lines") or []:
            line = dict(source)
            if _text(line.get("identity_source")) != "reviewed_ready":
                recovery_lines.append(line)
                continue

            quantity = _unit_quantity(line.get("quantity"))
            available_indices = sorted({
                int(value)
                for value in (line.get("available_unit_indices") or [])
                if 0 < int(value) <= quantity
            })
            for unit_index in available_indices:
                ready_unit_id = stable_ready_unit_id(line, unit_index)
                unit_line = {
                    **line,
                    "group_key": ready_unit_id,
                    "ready_unit_id": ready_unit_id,
                    "unit_index": unit_index,
                    "unit_total": quantity,
                    "remaining_quantity": 1,
                    "available_unit_indices": [unit_index],
                }
                row = {
                    **product,
                    "group_key": ready_unit_id,
                    "base_product_group_key": _text(product.get("group_key")),
                    "piece_level": True,
                    "ready_unit_id": ready_unit_id,
                    "unit_index": unit_index,
                    "unit_total": quantity,
                    "quantity": 1,
                    "total_quantity": 1,
                    "allocated_quantity": 0,
                    "remaining_quantity": 1,
                    "source_lines": [unit_line],
                    "source_order_numbers": [_text(line.get("order_number"))],
                    "source_order_count": 1,
                    "source_line_count": 1,
                }
                row["revision"] = stable_reviewed_product_revision(row)
                products.append(row)

        if recovery_lines:
            remaining_quantity = sum(
                _unit_quantity(
                    line.get("remaining_quantity")
                    if line.get("remaining_quantity") is not None
                    else line.get("quantity")
                )
                for line in recovery_lines
            )
            order_numbers = sorted({
                _text(line.get("order_number"))
                for line in recovery_lines
                if _text(line.get("order_number"))
            })
            recovery_row = {
                **product,
                "quantity": remaining_quantity,
                "total_quantity": sum(
                    _unit_quantity(line.get("quantity")) for line in recovery_lines
                ),
                "allocated_quantity": sum(
                    _unit_quantity(line.get("allocated_quantity")) for line in recovery_lines
                ),
                "remaining_quantity": remaining_quantity,
                "source_lines": recovery_lines,
                "source_order_numbers": order_numbers,
                "source_order_count": len(order_numbers),
                "source_line_count": len(recovery_lines),
            }
            recovery_row["revision"] = stable_reviewed_product_revision(recovery_row)
            products.append(recovery_row)

    products.sort(key=lambda row: (
        _normalized(row.get("name")),
        _text((row.get("source_order_numbers") or [""])[0]),
        int(row.get("unit_index") or 0),
        _text(row.get("group_key")),
    ))
    for row in products:
        for category_id in row.get("category_ids") or []:
            category_counts[_text(category_id)].add(_text(row.get("group_key")))

    categories = [
        {**row, "product_count": len(category_counts.get(_text(row.get("id")), set()))}
        for row in catalog.get("categories") or []
        if category_counts.get(_text(row.get("id")))
    ]
    summary = {
        **(catalog.get("summary") or {}),
        "selectable_card_count": len(products),
        "piece_card_count": sum(1 for row in products if row.get("piece_level")),
        "total_quantity": sum(_unit_quantity(row.get("quantity")) for row in products),
        "remaining_quantity": sum(
            _unit_quantity(row.get("remaining_quantity")) for row in products
        ),
    }
    return {
        **catalog,
        "products": products,
        "categories": categories,
        "summary": summary,
        "selection_grain": "physical_piece",
    }


async def load_reviewed_product_context(
    db: Any,
    *,
    user_id: str,
    limit: int = MAX_REVIEWED_ORDERS,
    reviewed_date: str = "",
) -> dict[str, Any]:
    repository = MongoOrderRepository(db)
    workflow_query: dict[str, Any] = {"user_id": user_id, "stage": "reviewed"}
    historical = bool(_text(reviewed_date))
    if historical:
        # Older workflow rows stored reviewed_at as an ISO string while newer
        # rows may use BSON datetimes. Match both representations using Riyadh
        # calendar boundaries. The stage predicate is intentionally removed:
        # this view answers "passed reviewed on this date", not "is reviewed".
        local_start = datetime.fromisoformat(reviewed_date).replace(
            tzinfo=timezone(timedelta(hours=3)),
        )
        utc_start = local_start.astimezone(timezone.utc)
        utc_end = utc_start + timedelta(days=1)
        workflow_query = {
            "user_id": user_id,
            "$or": [
                {"reviewed_at": {"$regex": f"^{reviewed_date}"}},
                {
                    "reviewed_at": {
                        "$gte": utc_start.isoformat(),
                        "$lt": utc_end.isoformat(),
                    },
                },
                {"reviewed_at": {"$gte": utc_start, "$lt": utc_end}},
            ],
        }
    workflows = await db[WORKFLOWS].find(
        workflow_query,
        {"_id": 0},
    ).sort("reviewed_at", 1).limit(limit + 1).to_list(limit + 1)
    truncated = len(workflows) > limit
    workflows = workflows[:limit]

    pairs: list[tuple[Any, dict[str, Any]]] = []
    product_ids: set[str] = set()
    skus: set[str] = set()
    order_numbers: set[str] = set()

    for workflow in workflows:
        order_number = _text(workflow.get("order_number"))
        if not order_number:
            continue
        try:
            order = await get_order(
                repository,
                user_id=user_id,
                order_number=order_number,
            )
        except OrderNotFoundError:
            continue
        order_number = _text(workflow.get("order_number"))
        pairs.append((order, workflow))
        order_numbers.add(order_number)
        for item in order.items:
            if _text(item.product_id):
                product_ids.add(_text(item.product_id))
            if _text(item.parent_product_id):
                product_ids.add(_text(item.parent_product_id))
            if _text(item.sku):
                skus.add(_text(item.sku))
        # Product V2 enrichment must also be available when Salla returns a
        # sparse in-progress order and the durable review snapshot is the only
        # remaining source of product identity.
        for state in workflow.get("items") or []:
            if not isinstance(state, dict):
                continue
            for value in (state.get("product_id"), state.get("parent_product_id")):
                if _text(value):
                    product_ids.add(_text(value))
            if _text(state.get("sku")):
                skus.add(_text(state.get("sku")))
            product_key = _text(state.get("product_key"))
            kind, _, value = product_key.partition(":")
            if kind in {"product", "parent"} and _text(value):
                product_ids.add(_text(value))
            elif kind == "sku" and _text(value):
                skus.add(_text(value))

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

    allocation_documents = []
    if order_numbers:
        allocation_documents = await db[PREPARATION_UNIT_ALLOCATIONS].find(
            {
                "user_id": user_id,
                "order_number": {"$in": sorted(order_numbers)},
                "status": {"$in": list(ACTIVE_PREPARATION_ALLOCATION_STATUSES)},
            },
            {"_id": 0},
        ).to_list(100000)

    original_catalog = aggregate_reviewed_products(pairs, product_documents)
    catalog = original_catalog if historical else expand_reviewed_ready_units(
        apply_preparation_allocations(original_catalog, allocation_documents),
    )
    catalog.update({
        "ok": True,
        "stage": "reviewed_history" if historical else "reviewed",
        "historical": historical,
        "reviewed_date": reviewed_date or None,
        "truncated": truncated,
        "order_limit": limit,
    })
    return {
        "catalog": catalog,
        "pairs": pairs,
        "product_documents": product_documents,
        "allocation_documents": allocation_documents,
        "truncated": truncated,
    }


def make_reviewed_products_catalog_router(db: Any, current_user: Callable) -> APIRouter:
    router = APIRouter(prefix="/reviewed-products-v1", tags=["Reviewed Products Catalog"])

    @router.get("/catalog")
    async def reviewed_products_catalog(
        limit: int = Query(500, ge=1, le=MAX_REVIEWED_ORDERS),
        reviewed_date: str = Query("", pattern=r"^$|^\d{4}-\d{2}-\d{2}$"),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        reviewer = _require_reviewer(user)
        user_id = _merchant_user_id(reviewer)
        context = await load_reviewed_product_context(
            db,
            user_id=user_id,
            limit=limit,
            reviewed_date=reviewed_date,
        )
        return context["catalog"]

    return router


__all__ = [
    "ACTIVE_PREPARATION_ALLOCATION_STATUSES",
    "MAX_REVIEWED_ORDERS",
    "PREPARATION_UNIT_ALLOCATIONS",
    "PRODUCTS",
    "aggregate_reviewed_products",
    "apply_preparation_allocations",
    "expand_reviewed_ready_units",
    "load_reviewed_product_context",
    "make_reviewed_products_catalog_router",
]
