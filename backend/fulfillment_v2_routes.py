"""Ready-to-ship routing, employee claiming and print batches for Mezan V2."""
from __future__ import annotations

import io
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from pymongo import ASCENDING, DESCENDING

from ai_store_access_contract import effective_permissions, find_role_assignment
from ai_store_operations_foundation import PERMISSIONS
from carrier_handoff import (
    CarrierHandoffError,
    carrier_handoff_custody_is_visible,
    confirm_carrier_label_print,
    receive_carrier_shipment,
)
from fulfillment_batch_pdf import generate_shipping_batch_pdf
from fulfillment_carrier_label import sync_completed_carrier_label
from order_engine.repository import MongoOrderRepository
from order_engine.service import OrderNotFoundError, get_order
from order_engine.shipping_label_service import ShippingLabelError
from order_option_cost_snapshot_routes import binding_matches, selected_option_tokens
from product_fulfillment_rules import (
    FULFILLMENT_DECISIONS,
    FULFILLMENT_TYPE_INSTANT,
    PRODUCT_OPERATION_PROFILES,
    PRODUCT_RESOURCE_BINDINGS,
    classify_line_fulfillment,
    evaluate_order_fulfillment,
)
from product_inventory_rules import (
    choose_inventory_rows,
    order_item_specifications,
)
from product_option_cost_routes import BINDINGS, RESOURCES
from product_v2_routes import PRODUCTS
from warehouse_location_routes import LOCATIONS


WORKFLOWS = "order_review_workflows"
BATCHES = "mezan_fulfillment_batches_v2"
EVENTS = "mezan_fulfillment_events_v2"
INVENTORY_RESERVATIONS = "mezan_inventory_reservations_v2"
TERMINAL_WORKFLOW_STAGES = {
    "completed",
    "delivering",
    "delivered",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text(value: Any) -> str:
    return str(value or "").strip()


class ClaimReadyBatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    order_numbers: list[str] = Field(min_length=1, max_length=100)


class PrintBatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reprint_reason: str | None = Field(default=None, max_length=500)


class BatchActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    note: str | None = Field(default=None, max_length=500)


class CarrierBarcodeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    barcode: str = Field(min_length=1, max_length=256)


async def ensure_fulfillment_indexes(db: Any) -> None:
    await db[WORKFLOWS].create_index(
        [("user_id", ASCENDING), ("order_number", ASCENDING)],
        unique=True,
    )
    await db[WORKFLOWS].create_index(
        [("user_id", ASCENDING), ("carrier_label_barcode", ASCENDING)],
        unique=True,
        partialFilterExpression={"carrier_label_barcode": {"$type": "string"}},
        name="uq_carrier_label_barcode_v2",
    )
    await db[WORKFLOWS].create_index(
        [
            ("user_id", ASCENDING),
            ("carrier_handoff_employee_id", ASCENDING),
            ("carrier_handoff_state", ASCENDING),
            ("carrier_handoff_scanned_at", DESCENDING),
        ],
        name="ix_carrier_handoff_employee_v2",
    )
    await db[FULFILLMENT_DECISIONS].create_index(
        [("user_id", ASCENDING), ("order_number", ASCENDING)],
        unique=True,
        name="uq_fulfillment_decision_v2",
    )
    await db[BATCHES].create_index(
        [("user_id", ASCENDING), ("created_at", DESCENDING)],
        name="ix_fulfillment_batches_v2",
    )
    await db[EVENTS].create_index(
        [
            ("user_id", ASCENDING),
            ("order_number", ASCENDING),
            ("occurred_at", DESCENDING),
        ],
        name="ix_fulfillment_events_v2",
    )
    await db[INVENTORY_RESERVATIONS].create_index(
        [
            ("user_id", ASCENDING),
            ("order_number", ASCENDING),
            ("line_key", ASCENDING),
        ],
        unique=True,
        name="uq_inventory_reservation_order_line_v2",
    )
    await db[INVENTORY_RESERVATIONS].create_index(
        [
            ("user_id", ASCENDING),
            ("status", ASCENDING),
            ("updated_at", DESCENDING),
        ],
        name="ix_inventory_reservation_status_v2",
    )


def _product_id(item: Any) -> str:
    return _text(
        getattr(item, "product_id", None)
        or getattr(item, "parent_product_id", None)
    )


def _inventory_rows(locations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for location in locations:
        if location.get("state") == "disabled":
            continue
        warehouse_id = _text(location.get("warehouse_id"))
        if not warehouse_id:
            continue
        occupancy = location.get("occupancy") or {}
        for index, item in enumerate(occupancy.get("items") or []):
            quantity = float(item.get("quantity") or 0)
            if quantity <= 0:
                continue
            identifiers = {
                _text(item.get("product_id")),
                _text(item.get("mezan_product_id")),
                _text(item.get("salla_variant_id")),
                _text(item.get("sku")),
            }
            identifiers.discard("")
            receipt_id = _text(item.get("receipt_id"))
            row_key = (
                f"receipt:{receipt_id}"
                if receipt_id
                else f"{location.get('id') or location.get('code')}:{index}"
            )
            rows.append({
                "key": row_key,
                "item_index": index,
                "location_id": _text(location.get("id")),
                "warehouse_id": warehouse_id,
                "identifiers": identifiers,
                "on_hand": quantity,
                "remaining": quantity,
                "receipt_id": receipt_id or None,
                "salla_variant_id": (
                    _text(item.get("salla_variant_id")) or None
                ),
                "preparation_state": item.get("preparation_state"),
                "specifications": item.get("specifications") or {},
                "configuration_key": item.get("configuration_key"),
                "lot_id": item.get("lot_id"),
            })
    return rows


def _apply_inventory_reservations(
    stock_rows: list[dict[str, Any]],
    reservations: list[dict[str, Any]],
    *,
    current_order_number: str,
) -> None:
    """Subtract active order holds from current physical stock."""
    rows_by_key = {
        _text(row.get("key")): row
        for row in stock_rows
        if _text(row.get("key"))
    }
    for reservation in reservations:
        status_value = _text(reservation.get("status"))
        if status_value != "active":
            continue
        if (
            _text(reservation.get("order_number"))
            == current_order_number
        ):
            continue
        for allocation in reservation.get("allocations") or []:
            row = rows_by_key.get(
                _text(allocation.get("inventory_row_key"))
            )
            if not row:
                continue
            row["remaining"] = max(
                0.0,
                float(row.get("remaining") or 0)
                - float(allocation.get("quantity") or 0),
            )
            quantity = float(allocation.get("quantity") or 0)
            row["reserved_quantity"] = (
                float(row.get("reserved_quantity") or 0)
                + quantity
            )


async def _release_order_inventory_reservations(
    db: Any,
    *,
    user_id: str,
    order_number: str,
    reason: str,
) -> None:
    await db[INVENTORY_RESERVATIONS].update_many(
        {
            "user_id": user_id,
            "order_number": order_number,
            "status": "active",
        },
        {
            "$set": {
                "status": "released",
                "release_reason": reason,
                "released_at": _now(),
                "updated_at": _now(),
            },
        },
    )


def _inventory_reservation_blockers(blockers: list[str]) -> list[str]:
    """Preparation progress may wait while its physical stock stays held."""
    return [
        blocker for blocker in blockers
        if blocker != "operational_items_not_ready"
    ]


async def _persist_order_inventory_reservations(
    db: Any,
    *,
    user_id: str,
    order_number: str,
    lines: list[dict[str, Any]],
) -> list[str]:
    now = _now()
    desired_keys: list[str] = []
    reservation_ids: list[str] = []
    for index, line in enumerate(lines):
        if line.get("requires_branch_inventory") is not True:
            continue
        allocations = list(line.get("inventory_allocations") or [])
        if line.get("inventory_available") is not True or not allocations:
            continue
        line_key = _text(
            line.get("order_item_id")
            or line.get("inventory_reservation_key")
            or f"line-{index}"
        )
        desired_keys.append(line_key)
        selector = {
            "user_id": user_id,
            "order_number": order_number,
            "line_key": line_key,
        }
        existing = await db[INVENTORY_RESERVATIONS].find_one(
            selector,
            {"_id": 0, "id": 1, "status": 1},
        )
        if existing and existing.get("status") == "consumed":
            reservation_ids.append(_text(existing.get("id")))
            continue
        reservation_id = (
            _text((existing or {}).get("id")) or uuid.uuid4().hex
        )
        reservation_ids.append(reservation_id)
        await db[INVENTORY_RESERVATIONS].update_one(
            selector,
            {
                "$set": {
                    **selector,
                    "id": reservation_id,
                    "status": "active",
                    "allocations": allocations,
                    "quantity": float(line.get("quantity") or 0),
                    "product_id": line.get("salla_product_id"),
                    "mezan_product_id": line.get("mezan_product_id"),
                    "sku": line.get("sku"),
                    "warehouse_ids": line.get("warehouse_ids") or [],
                    "configuration_keys": (
                        line.get("inventory_configuration_keys") or []
                    ),
                    "updated_at": now,
                },
                "$unset": {
                    "release_reason": "",
                    "released_at": "",
                },
                "$setOnInsert": {
                    "created_at": now,
                },
            },
            upsert=True,
        )
    stale_filter: dict[str, Any] = {
        "user_id": user_id,
        "order_number": order_number,
        "status": "active",
    }
    if desired_keys:
        stale_filter["line_key"] = {"$nin": desired_keys}
    await db[INVENTORY_RESERVATIONS].update_many(
        stale_filter,
        {
            "$set": {
                "status": "released",
                "release_reason": "order_inventory_reallocated",
                "released_at": now,
                "updated_at": now,
            },
        },
    )
    return reservation_ids


async def _consume_order_inventory_reservations(
    db: Any,
    *,
    user_id: str,
    order_numbers: list[str],
    actor_id: str,
    batch_id: str,
) -> int:
    """Deduct reserved units from their physical locations once handed off."""
    reservations = await db[INVENTORY_RESERVATIONS].find(
        {
            "user_id": user_id,
            "order_number": {"$in": order_numbers},
            "status": "active",
        },
        {"_id": 0},
    ).to_list(length=10000)
    targets = _inventory_consumption_targets(reservations)

    location_ids = sorted({
        row["location_id"] for row in targets.values()
    })
    locations = await db[LOCATIONS].find(
        {
            "user_id": user_id,
            "id": {"$in": location_ids},
        },
        {"_id": 0, "id": 1, "occupancy": 1},
    ).to_list(length=max(1, len(location_ids)))
    locations_by_id = {
        _text(row.get("id")): row for row in locations
    }
    for target in targets.values():
        location = locations_by_id.get(target["location_id"])
        items = list(
            ((location or {}).get("occupancy") or {}).get("items") or []
        )
        if target["receipt_id"]:
            item = next(
                (
                    row for row in items
                    if _text(row.get("receipt_id"))
                    == target["receipt_id"]
                ),
                None,
            )
        else:
            index = int(target["item_index"])
            item = items[index] if 0 <= index < len(items) else None
        if (
            not item
            or float(item.get("quantity") or 0) < target["quantity"]
        ):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "reserved_inventory_changed_reconciliation_required",
                    "location_id": target["location_id"],
                },
            )

    now = _now()
    for target in targets.values():
        quantity = float(target["quantity"])
        if target["receipt_id"]:
            result = await db[LOCATIONS].update_one(
                {
                    "user_id": user_id,
                    "id": target["location_id"],
                    "occupancy.items": {
                        "$elemMatch": {
                            "receipt_id": target["receipt_id"],
                            "quantity": {"$gte": quantity},
                        },
                    },
                },
                {
                    "$inc": {
                        "occupancy.items.$[stock].quantity": -quantity,
                        "occupancy.total_quantity": -quantity,
                    },
                    "$set": {"updated_at": now},
                },
                array_filters=[{
                    "stock.receipt_id": target["receipt_id"],
                }],
            )
        else:
            item_index = int(target["item_index"])
            quantity_path = (
                f"occupancy.items.{item_index}.quantity"
            )
            result = await db[LOCATIONS].update_one(
                {
                    "user_id": user_id,
                    "id": target["location_id"],
                    quantity_path: {"$gte": quantity},
                },
                {
                    "$inc": {
                        quantity_path: -quantity,
                        "occupancy.total_quantity": -quantity,
                    },
                    "$set": {"updated_at": now},
                },
            )
        if not result.modified_count:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "inventory_consumption_conflict",
                    "location_id": target["location_id"],
                },
            )
    if location_ids:
        await db[LOCATIONS].update_many(
            {
                "user_id": user_id,
                "id": {"$in": location_ids},
                "occupancy.total_quantity": {"$lte": 0},
            },
            {
                "$set": {
                    "state": "empty",
                    "occupancy.total_quantity": 0,
                    "updated_at": now,
                },
            },
        )
    await db[INVENTORY_RESERVATIONS].update_many(
        {
            "user_id": user_id,
            "order_number": {"$in": order_numbers},
            "status": "active",
        },
        {
            "$set": {
                "status": "consumed",
                "consumed_at": now,
                "consumed_by": actor_id,
                "consumed_batch_id": batch_id,
                "updated_at": now,
            },
        },
    )
    return len(reservations)


def _inventory_consumption_targets(
    reservations: list[dict[str, Any]],
) -> dict[tuple[str, str, str], dict[str, Any]]:
    targets: dict[tuple[str, str, str], dict[str, Any]] = {}
    for reservation in reservations:
        for allocation in reservation.get("allocations") or []:
            location_id = _text(allocation.get("location_id"))
            receipt_id = _text(allocation.get("receipt_id"))
            item_index = allocation.get("item_index")
            if not location_id or (not receipt_id and item_index is None):
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "inventory_reservation_target_missing",
                        "reservation_id": reservation.get("id"),
                    },
                )
            target_type = "receipt" if receipt_id else "index"
            target_value = receipt_id or str(item_index)
            key = (location_id, target_type, target_value)
            target = targets.setdefault(key, {
                "location_id": location_id,
                "receipt_id": receipt_id or None,
                "item_index": item_index,
                "quantity": 0.0,
            })
            target["quantity"] += float(
                allocation.get("quantity") or 0
            )
    return targets


def _reserve_inventory_for_line(
    *,
    stock_rows: list[dict[str, Any]],
    identifiers: set[str],
    quantity: float,
) -> tuple[bool, float, list[str]]:
    matches = [
        row for row in stock_rows
        if row["remaining"] > 0
        and _text(row.get("warehouse_id"))
        and not identifiers.isdisjoint(row["identifiers"])
    ]
    available = sum(float(row["remaining"]) for row in matches)
    if available < quantity:
        return False, available, []
    needed = quantity
    warehouse_ids: set[str] = set()
    for row in matches:
        take = min(float(row["remaining"]), needed)
        row["remaining"] -= take
        needed -= take
        if take > 0 and row["warehouse_id"]:
            warehouse_ids.add(str(row["warehouse_id"]))
        if needed <= 0:
            break
    return True, available, sorted(warehouse_ids)


def _satisfy_preparation_with_ready_stock(
    classification: dict[str, Any],
) -> dict[str, Any]:
    """A fully prepared exact unit has already completed its services."""
    satisfied_services = list(
        classification.get("forcing_services") or []
    )
    return {
        **classification,
        "resolved_type": FULFILLMENT_TYPE_INSTANT,
        "requires_preparation": False,
        "forcing_services": [],
        "forcing_services_satisfied_by_inventory": satisfied_services,
        "supplier_export_eligible": False,
    }


async def build_order_fulfillment_decision(
    db: Any,
    *,
    user_id: str,
    order: Any,
    operational_items: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Resolve product rules and Mezan warehouse availability for one order."""
    await ensure_fulfillment_indexes(db)
    product_ids = {_product_id(item) for item in order.items}
    product_ids.discard("")
    products = await db[PRODUCTS].find(
        {
            "user_id": user_id,
            "salla_product_id": {"$in": list(product_ids)},
        },
        {
            "_id": 0,
            "salla_product_id": 1,
            "mezan_product_id": 1,
            "sku": 1,
            "name": 1,
        },
    ).to_list(length=max(1, len(product_ids)))
    products_by_salla = {
        _text(row.get("salla_product_id")): row for row in products
    }
    profiles = await db[PRODUCT_OPERATION_PROFILES].find(
        {
            "user_id": user_id,
            "salla_product_id": {"$in": list(product_ids)},
        },
        {"_id": 0},
    ).to_list(length=max(1, len(product_ids)))
    profiles_by_product = {
        _text(row.get("salla_product_id")): row for row in profiles
    }
    product_links = await db[PRODUCT_RESOURCE_BINDINGS].find(
        {
            "user_id": user_id,
            "salla_product_id": {"$in": list(product_ids)},
        },
        {"_id": 0},
    ).to_list(length=10000)
    option_links = await db[BINDINGS].find(
        {
            "user_id": user_id,
            "salla_product_id": {"$in": list(product_ids)},
            "mode": "resource",
        },
        {"_id": 0},
    ).to_list(length=10000)
    product_links_by_product: dict[str, list[dict[str, Any]]] = {}
    option_links_by_product: dict[str, list[dict[str, Any]]] = {}
    resource_ids = set()
    for row in product_links:
        product_links_by_product.setdefault(
            _text(row.get("salla_product_id")),
            [],
        ).append(row)
        resource_ids.add(_text(row.get("resource_id")))
    for row in option_links:
        option_links_by_product.setdefault(
            _text(row.get("salla_product_id")),
            [],
        ).append(row)
        resource_ids.add(_text(row.get("resource_id")))
    resource_ids.discard("")
    resources = await db[RESOURCES].find(
        {"user_id": user_id, "id": {"$in": list(resource_ids)}},
        {"_id": 0},
    ).to_list(length=max(1, len(resource_ids)))
    resource_map = {_text(row.get("id")): row for row in resources}

    locations = await db[LOCATIONS].find(
        {
            "user_id": user_id,
            "state": {"$ne": "disabled"},
            "occupancy": {"$ne": None},
        },
        {"_id": 0, "id": 1, "code": 1, "warehouse_id": 1, "state": 1, "occupancy": 1},
    ).to_list(length=20000)
    stock_rows = _inventory_rows(locations)
    existing_reservations = await db[INVENTORY_RESERVATIONS].find(
        {
            "user_id": user_id,
            "status": "active",
        },
        {"_id": 0},
    ).to_list(length=50000)
    _apply_inventory_reservations(
        stock_rows,
        existing_reservations,
        current_order_number=_text(order.order_number),
    )

    lines = []
    for item_index, item in enumerate(order.items):
        product_id = _product_id(item)
        product = products_by_salla.get(product_id, {})
        profile = profiles_by_product.get(product_id)
        product_resources = []
        for link in product_links_by_product.get(product_id, []):
            resource = dict(resource_map.get(_text(link.get("resource_id")), {}))
            resource["_link_source"] = "product"
            product_resources.append(resource)
        tokens = selected_option_tokens(item)
        selected_option_resources = []
        for link in option_links_by_product.get(product_id, []):
            if not binding_matches(link, tokens):
                continue
            resource = dict(resource_map.get(_text(link.get("resource_id")), {}))
            resource["_link_source"] = "option"
            selected_option_resources.append(resource)
        classification = classify_line_fulfillment(
            profile=profile,
            product_resources=product_resources,
            selected_option_resources=selected_option_resources,
        )
        quantity = float(getattr(item, "quantity", 1) or 1)
        identifiers = {
            product_id,
            _text(product.get("mezan_product_id")),
            _text(product.get("sku")),
            _text(getattr(item, "sku", None)),
        }
        identifiers.discard("")
        inventory_available = None
        available_quantity = None
        warehouse_ids: list[str] = []
        inventory_match_type = None
        inventory_configuration_keys: list[str] = []
        inventory_allocations: list[dict[str, Any]] = []
        preparation_satisfied_by_ready_stock = False
        specifications = order_item_specifications(item)
        if classification["requires_branch_inventory"]:
            selection = choose_inventory_rows(
                rows=stock_rows,
                identifiers=identifiers,
                quantity=quantity,
                order_specifications=specifications,
                preparation_required=classification[
                    "requires_preparation"
                ],
            )
            inventory_available = selection["available"]
            available_quantity = selection["available_quantity"]
            warehouse_ids = selection["warehouse_ids"]
            inventory_match_type = selection["match_type"]
            inventory_configuration_keys = selection[
                "configuration_keys"
            ]
            inventory_allocations = selection["allocations"]
            preparation_satisfied_by_ready_stock = selection[
                "preparation_satisfied_by_ready_stock"
            ]
            if preparation_satisfied_by_ready_stock:
                classification = _satisfy_preparation_with_ready_stock(
                    classification
                )
        lines.append({
            "order_item_id": getattr(item, "order_item_id", None),
            "salla_product_id": product_id or None,
            "mezan_product_id": product.get("mezan_product_id"),
            "name": getattr(item, "name", None),
            "sku": getattr(item, "sku", None) or product.get("sku"),
            "quantity": quantity,
            **classification,
            "inventory_available": inventory_available,
            "available_quantity": available_quantity,
            "warehouse_ids": warehouse_ids,
            "order_specifications": specifications,
            "inventory_match_type": inventory_match_type,
            "inventory_configuration_keys": inventory_configuration_keys,
            "inventory_allocations": inventory_allocations,
            "inventory_reservation_key": _text(
                getattr(item, "order_item_id", None)
            ) or f"line-{item_index}",
            "preparation_satisfied_by_ready_stock": (
                preparation_satisfied_by_ready_stock
            ),
            "warehouse_resolution_source": (
                "inventory_location"
                if warehouse_ids
                else (
                    "inventory_location_missing"
                    if classification["requires_branch_inventory"]
                    else "not_required"
                )
            ),
        })

    decision = evaluate_order_fulfillment(order=order, lines=lines)
    unready_operational_items = [
        row for row in (operational_items or [])
        if row.get("blocks_order_completion") is not False
        and _text(row.get("preparation_status")).casefold() != "ready"
    ]
    if unready_operational_items:
        decision["ready_to_ship"] = False
        decision["route_stage"] = "reviewed"
        decision["preparation_stages_required"] = True
        decision["supplier_export_order_required"] = True
        decision["blockers"] = list(dict.fromkeys([
            *(decision.get("blockers") or []),
            "operational_items_not_ready",
        ]))
        decision["unready_operational_item_ids"] = [
            row.get("operational_item_id")
            for row in unready_operational_items
        ]
    inventory_eligibility_blockers = _inventory_reservation_blockers(
        decision.get("blockers") or []
    )
    if inventory_eligibility_blockers:
        await _release_order_inventory_reservations(
            db,
            user_id=user_id,
            order_number=_text(order.order_number),
            reason="order_not_inventory_eligible",
        )
        reservation_ids: list[str] = []
    else:
        reservation_ids = await _persist_order_inventory_reservations(
            db,
            user_id=user_id,
            order_number=_text(order.order_number),
            lines=lines,
        )
    decision.update({
        "id": uuid.uuid4().hex,
        "user_id": user_id,
        "order_id": getattr(order, "order_id", None),
        "evaluated_at": _now(),
        "inventory_authority": "mezan_operational_inventory_v2",
        "external_calls_made": False,
        "inventory_reservation_ids": reservation_ids,
        "inventory_reservation_status": (
            "active" if reservation_ids else "not_reserved"
        ),
    })
    await db[FULFILLMENT_DECISIONS].update_one(
        {"user_id": user_id, "order_number": order.order_number},
        {
            "$set": decision,
            "$setOnInsert": {"created_at": _now()},
        },
        upsert=True,
    )
    return decision


async def auto_route_instant_order(
    db: Any,
    *,
    user_id: str,
    order: Any,
) -> dict[str, Any]:
    """Promote a newly ingested eligible order without human preparation.

    The function is local-only and fail-closed. It can be called after a
    verified webhook or an explicit Orders V2 refresh. Claimed/terminal work
    is never moved backwards by a later event.
    """
    await ensure_fulfillment_indexes(db)
    workflow = await db[WORKFLOWS].find_one(
        {"user_id": user_id, "order_number": order.order_number},
        {"_id": 0},
    )
    current_stage = _text((workflow or {}).get("stage")) or "pending_review"
    if workflow and (
        current_stage in TERMINAL_WORKFLOW_STAGES
        or workflow.get("claim_batch_id")
    ):
        return {
            "promoted": False,
            "reverted": False,
            "stage": current_stage,
            "decision": workflow.get("fulfillment_decision"),
            "reason": "workflow_locked",
        }
    decision = await build_order_fulfillment_decision(
        db,
        user_id=user_id,
        order=order,
        operational_items=list(
            (workflow or {}).get("operational_items") or []
        ),
    )
    if decision.get("ready_to_ship") is not True:
        if (
            workflow
            and current_stage == "ready_to_ship"
            and workflow.get("auto_routed_instant") is True
            and not workflow.get("claim_batch_id")
        ):
            restored_stage = (
                _text(workflow.get("auto_route_previous_stage"))
                or "pending_review"
            )
            now = _now()
            await db[WORKFLOWS].update_one(
                {
                    "user_id": user_id,
                    "order_number": order.order_number,
                    "stage": "ready_to_ship",
                    "auto_routed_instant": True,
                    "$or": [
                        {"claim_batch_id": {"$exists": False}},
                        {"claim_batch_id": None},
                        {"claim_batch_id": ""},
                    ],
                },
                {"$set": {
                    "stage": restored_stage,
                    "fulfillment_decision": decision,
                    "auto_route_reverted_at": now,
                    "updated_at": now,
                }},
            )
            return {
                "promoted": False,
                "reverted": True,
                "stage": restored_stage,
                "decision": decision,
            }
        return {
            "promoted": False,
            "reverted": False,
            "stage": current_stage,
            "decision": decision,
        }

    now = _now()
    if workflow:
        result = await db[WORKFLOWS].update_one(
            {
                "user_id": user_id,
                "order_number": order.order_number,
                "stage": current_stage,
                "$or": [
                    {"claim_batch_id": {"$exists": False}},
                    {"claim_batch_id": None},
                    {"claim_batch_id": ""},
                ],
            },
            {"$set": {
                "stage": "ready_to_ship",
                "fulfillment_decision": decision,
                "ready_to_ship_at": (
                    workflow.get("ready_to_ship_at") or now
                ),
                "auto_routed_instant": True,
                "auto_route_previous_stage": current_stage,
                "auto_routed_at": now,
                "updated_at": now,
                "revision": int(workflow.get("revision") or 0) + 1,
            }},
        )
        promoted = bool(result.modified_count)
    else:
        await db[WORKFLOWS].insert_one({
            "user_id": user_id,
            "order_number": order.order_number,
            "order_id": getattr(order, "order_id", None),
            "stage": "ready_to_ship",
            "revision": 1,
            "items": [],
            "operational_items": [],
            "fulfillment_decision": decision,
            "ready_to_ship_at": now,
            "auto_routed_instant": True,
            "auto_route_previous_stage": "pending_review",
            "auto_routed_at": now,
            "created_at": now,
            "updated_at": now,
        })
        promoted = True
    if promoted:
        await db[EVENTS].insert_one({
            "id": uuid.uuid4().hex,
            "user_id": user_id,
            "event_type": "instant_order_auto_routed",
            "order_number": order.order_number,
            "warehouse_ids": decision.get("warehouse_ids") or [],
            "occurred_at": now,
        })
    return {
        "promoted": promoted,
        "reverted": False,
        "stage": "ready_to_ship" if promoted else current_stage,
        "decision": decision,
    }


async def _actor_context(
    db: Any,
    user: dict[str, Any],
) -> dict[str, Any]:
    actor_id = _text(user.get("id"))
    role = _text(user.get("role")).casefold()
    if role == "owner" or user.get("is_owner") is True:
        return {
            "actor_id": actor_id,
            "merchant_id": actor_id,
            "is_owner": True,
            "permissions": set(PERMISSIONS),
            "warehouse_ids": None,
            "workplace_warehouse_id": None,
            "responsibilities": {
                "instant_ready",
                "packing",
                "shipping_labeling",
                "carrier_handoff",
            },
        }
    merchant_id = _text(user.get("created_by"))
    if not merchant_id:
        raise HTTPException(
            status_code=409,
            detail={"code": "employee_store_not_linked"},
        )
    assignment = await find_role_assignment(
        db,
        owner_user_id=merchant_id,
        user_id=actor_id,
    )
    return {
        "actor_id": actor_id,
        "merchant_id": merchant_id,
        "is_owner": False,
        "permissions": set(effective_permissions(assignment)),
        "warehouse_ids": set((assignment or {}).get("warehouse_ids") or []),
        "workplace_warehouse_id": _text(
            (assignment or {}).get("workplace_warehouse_id")
        ) or None,
        "responsibilities": set(
            (assignment or {}).get("fulfillment_responsibilities") or []
        ),
    }


def _require_permission(
    context: dict[str, Any],
    permission: str,
    *,
    responsibility: str | None = None,
) -> None:
    if permission not in context["permissions"]:
        raise HTTPException(
            status_code=403,
            detail={"code": "fulfillment_permission_required", "permission": permission},
        )
    if (
        not context["is_owner"]
        and responsibility
        and responsibility not in context["responsibilities"]
    ):
        raise HTTPException(
            status_code=403,
            detail={
                "code": "fulfillment_responsibility_required",
                "responsibility": responsibility,
            },
        )


def _can_operate_completed_carrier_label(context: dict[str, Any]) -> bool:
    if "fulfillment.labels.print" not in context["permissions"]:
        return False
    return bool(
        context["is_owner"]
        or "shipping_labeling" in context["responsibilities"]
    )


def _can_receive_carrier_handoff(context: dict[str, Any]) -> bool:
    return bool(
        "fulfillment.carrier.handoff" in context["permissions"]
        and (
            context["is_owner"]
            or "carrier_handoff" in context["responsibilities"]
        )
    )


def _carrier_handoff_http_error(exc: CarrierHandoffError) -> HTTPException:
    return HTTPException(
        status_code=exc.status_code,
        detail={
            "code": exc.code,
            "message": str(exc),
            **exc.details,
        },
    )


def _warehouse_allowed(
    context: dict[str, Any],
    warehouse_ids: list[str],
) -> bool:
    required = {str(value) for value in warehouse_ids if value}
    if not required:
        return False
    if context["is_owner"]:
        return True
    return required.issubset(context["warehouse_ids"])


def _ready_order_allowed(
    context: dict[str, Any],
    workflow: dict[str, Any],
) -> bool:
    warehouses = (
        (workflow.get("fulfillment_decision") or {}).get("warehouse_ids")
        or []
    )
    if warehouses:
        return _warehouse_allowed(context, warehouses)
    # Preparation orders are physical pieces already received into Mezan's
    # assembly queue. They do not need an inventory-location assignment to be
    # assembled and labeled.
    return _text(workflow.get("ready_to_ship_source")) == "preparation_receipt"


async def _order_view(
    repository: MongoOrderRepository,
    *,
    user_id: str,
    workflow: dict[str, Any],
) -> dict[str, Any] | None:
    try:
        order = await get_order(
            repository,
            user_id=user_id,
            order_number=_text(workflow.get("order_number")),
        )
    except OrderNotFoundError:
        return None
    return {
        "order_number": order.order_number,
        "order_id": order.order_id,
        "customer_name": order.customer.name,
        "customer_mobile": order.customer.mobile,
        "city": (
            order.shipping.address.city
            if order.shipping.address
            else None
        ),
        "shipping_company": order.shipping.company,
        "items_count": len(order.items),
        "items": [{
            "order_item_id": item.order_item_id,
            "name": item.name,
            "sku": item.sku,
            "quantity": item.quantity,
        } for item in order.items],
        "warehouse_ids": (
            (workflow.get("fulfillment_decision") or {}).get("warehouse_ids")
            or []
        ),
        "warehouse_resolution_source": (
            (workflow.get("fulfillment_decision") or {}).get(
                "warehouse_resolution_source"
            )
            or "inventory_location_missing"
        ),
        "claimed": bool(workflow.get("claim_batch_id")),
        "claim_batch_id": workflow.get("claim_batch_id"),
        "claimed_by": workflow.get("claimed_by"),
        "claimed_by_name": workflow.get("claimed_by_name"),
        "ready_at": workflow.get("ready_to_ship_at"),
        "ready_to_ship_source": workflow.get("ready_to_ship_source"),
        "assembly_status": workflow.get("assembly_status"),
        "assembly_ready_count": int(
            workflow.get("assembly_ready_piece_count") or 0
        ),
        "assembly_piece_count": int(
            workflow.get("assembly_piece_count")
            or workflow.get("preparation_piece_count")
            or 0
        ),
        "print_batch_id": (
            workflow.get("shipping_print_batch_id")
            or workflow.get("claim_batch_id")
        ),
        "completed_at": workflow.get("completed_at"),
        "order_status": order.status,
        "order_status_native": order.status_native,
        "salla_order_status": workflow.get("salla_order_status"),
        "carrier_label_status": workflow.get("carrier_label_status"),
        "carrier_label_ready": bool(workflow.get("carrier_label_ready")),
        "carrier_label_url": workflow.get("carrier_label_url"),
        "carrier_label_type": workflow.get("carrier_label_type"),
        "carrier_name": workflow.get("carrier_name") or order.shipping.company,
        "carrier_tracking_number": workflow.get("carrier_tracking_number"),
        "carrier_label_message": workflow.get("carrier_label_message"),
        "carrier_label_error_code": workflow.get("carrier_label_error_code"),
        "carrier_label_error_message": workflow.get("carrier_label_error_message"),
        "carrier_label_print_confirmed": bool(
            workflow.get("carrier_label_print_confirmed")
        ),
        "carrier_label_print_confirmed_at": workflow.get(
            "carrier_label_print_confirmed_at"
        ),
        "carrier_label_print_confirmed_by_name": workflow.get(
            "carrier_label_print_confirmed_by_name"
        ),
        "carrier_handoff_state": workflow.get("carrier_handoff_state"),
        "carrier_handoff_employee_id": workflow.get(
            "carrier_handoff_employee_id"
        ),
        "carrier_handoff_employee_name": workflow.get(
            "carrier_handoff_employee_name"
        ),
        "carrier_handoff_scanned_at": workflow.get(
            "carrier_handoff_scanned_at"
        ),
        "carrier_handoff_released_at": workflow.get(
            "carrier_handoff_released_at"
        ),
        "carrier_handoff_release_source": workflow.get(
            "carrier_handoff_release_source"
        ),
        "delivering_at": workflow.get("delivering_at"),
        "delivered_at": workflow.get("delivered_at"),
        "customer_service_instructions": list(
            workflow.get("customer_service_instructions") or []
        ),
        "customer_service_hold_active": bool(
            workflow.get("customer_service_hold_active")
        ),
    }


def make_fulfillment_v2_router(
    db: Any,
    current_user: Callable[..., Any],
) -> APIRouter:
    router = APIRouter(prefix="/fulfillment-v2", tags=["Mezan Fulfillment V2"])
    repository = MongoOrderRepository(db)

    @router.get("/ready-to-ship")
    async def list_ready_to_ship(
        limit: int = Query(default=100, ge=1, le=300),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        context = await _actor_context(db, user)
        _require_permission(
            context,
            "fulfillment.ready.read",
            responsibility="instant_ready",
        )
        workflows = await db[WORKFLOWS].find(
            {
                "user_id": context["merchant_id"],
                "stage": "ready_to_ship",
            },
            {"_id": 0},
        ).sort("ready_to_ship_at", 1).limit(limit * 3).to_list(limit * 3)
        items = []
        for workflow in workflows:
            if not _ready_order_allowed(context, workflow):
                continue
            claimed_by = _text(workflow.get("claimed_by"))
            if claimed_by and claimed_by != context["actor_id"] and not context["is_owner"]:
                continue
            row = await _order_view(
                repository,
                user_id=context["merchant_id"],
                workflow=workflow,
            )
            if row:
                items.append(row)
            if len(items) >= limit:
                break
        return {
            "items": items,
            "total": len(items),
            "permissions": {
                "can_claim": "fulfillment.batch.claim" in context["permissions"],
                "can_print": "fulfillment.labels.print" in context["permissions"],
                "can_reprint": "fulfillment.labels.reprint" in context["permissions"],
                "can_pack": "fulfillment.pack.confirm" in context["permissions"],
                "can_handoff": "fulfillment.carrier.handoff" in context["permissions"],
            },
        }

    @router.get("/completed")
    async def list_completed_assembly_orders(
        limit: int = Query(default=100, ge=1, le=300),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        context = await _actor_context(db, user)
        _require_permission(context, "fulfillment.ready.read")
        can_label = _can_operate_completed_carrier_label(context)
        can_handoff = _can_receive_carrier_handoff(context)
        if not can_label and not can_handoff:
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "fulfillment_responsibility_required",
                    "responsibility": "shipping_labeling_or_carrier_handoff",
                },
            )
        query: dict[str, Any] = {
            "user_id": context["merchant_id"],
            "stage": "completed",
            "assembly_status": "completed",
        }
        # Labeling owns the order until the handoff employee scans the AWB.
        # Handoff employees receive their custody list from the dedicated
        # endpoint below and must not see other employees' labeling queues.
        if can_label:
            query["$or"] = [
                {"carrier_handoff_employee_id": {"$exists": False}},
                {"carrier_handoff_employee_id": None},
                {"carrier_handoff_employee_id": ""},
            ]
        else:
            query["carrier_handoff_employee_id"] = "__scan_to_receive__"
        workflows = await db[WORKFLOWS].find(
            query,
            {"_id": 0},
        ).sort("completed_at", -1).limit(limit * 2).to_list(limit * 2)
        items = []
        for workflow in workflows:
            claimed_by = _text(workflow.get("claimed_by"))
            if (
                claimed_by
                and claimed_by != context["actor_id"]
                and not context["is_owner"]
            ):
                continue
            row = await _order_view(
                repository,
                user_id=context["merchant_id"],
                workflow=workflow,
            )
            if row:
                items.append(row)
            if len(items) >= limit:
                break
        batch_ids = sorted({
            _text(row.get("print_batch_id"))
            for row in items
            if _text(row.get("print_batch_id"))
        })
        batches = (
            await db[BATCHES].find(
                {
                    "user_id": context["merchant_id"],
                    "id": {"$in": batch_ids},
                },
                {"_id": 0, "id": 1, "print_count": 1, "status": 1},
            ).to_list(max(1, len(batch_ids)))
            if batch_ids
            else []
        )
        batch_by_id = {_text(row.get("id")): row for row in batches}
        for row in items:
            batch = batch_by_id.get(_text(row.get("print_batch_id"))) or {}
            row["print_count"] = int(batch.get("print_count") or 0)
            row["print_status"] = _text(batch.get("status")) or None
        return {
            "items": items,
            "total": len(items),
            "permissions": {
                "can_print": can_label,
                "can_confirm_print": can_label,
                "can_handoff_scan": can_handoff,
                "can_reprint": (
                    "fulfillment.labels.reprint" in context["permissions"]
                ),
            },
        }

    async def _carrier_label_action(
        *,
        order_number: str,
        user: dict[str, Any],
        action: str,
    ) -> dict[str, Any]:
        context = await _actor_context(db, user)
        _require_permission(context, "fulfillment.labels.print")
        if not _can_operate_completed_carrier_label(context):
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "fulfillment_responsibility_required",
                    "responsibility": "shipping_labeling",
                },
            )
        actor_name = _text(user.get("name") or user.get("email")) or "مستخدم ميزان"
        try:
            return await sync_completed_carrier_label(
                db,
                user_id=context["merchant_id"],
                order_number=_text(order_number),
                actor_id=context["actor_id"],
                actor_name=actor_name,
                action=action,
            )
        except ShippingLabelError as exc:
            raise HTTPException(
                status_code=exc.status_code,
                detail={
                    "code": exc.code,
                    "message": str(exc),
                    "order_number": _text(order_number),
                },
            ) from exc

    @router.post("/completed/{order_number}/carrier-label")
    async def issue_completed_order_carrier_label(
        order_number: str,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        return await _carrier_label_action(
            order_number=order_number,
            user=user,
            action="issue",
        )

    @router.post("/completed/{order_number}/carrier-label/refresh")
    async def refresh_completed_order_carrier_label(
        order_number: str,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        return await _carrier_label_action(
            order_number=order_number,
            user=user,
            action="refresh",
        )

    @router.post("/completed/{order_number}/carrier-label/confirm-print")
    async def confirm_completed_order_carrier_label_print(
        order_number: str,
        payload: CarrierBarcodeRequest,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        context = await _actor_context(db, user)
        _require_permission(
            context,
            "fulfillment.labels.print",
            responsibility="shipping_labeling",
        )
        try:
            return await confirm_carrier_label_print(
                db,
                user_id=context["merchant_id"],
                order_number=_text(order_number),
                scanned_barcode=payload.barcode,
                actor_id=context["actor_id"],
                actor_name=(
                    _text(user.get("name") or user.get("email"))
                    or "موظف العنونة والشحن"
                ),
            )
        except CarrierHandoffError as exc:
            raise _carrier_handoff_http_error(exc) from exc

    @router.get("/carrier-handoff")
    async def list_employee_carrier_handoff_shipments(
        limit: int = Query(default=100, ge=1, le=300),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        context = await _actor_context(db, user)
        _require_permission(
            context,
            "fulfillment.carrier.handoff",
            responsibility="carrier_handoff",
        )
        query: dict[str, Any] = {
            "user_id": context["merchant_id"],
            "stage": "completed",
            "carrier_handoff_state": "with_handoff_employee",
        }
        if not context["is_owner"]:
            query["carrier_handoff_employee_id"] = context["actor_id"]
        workflows = await db[WORKFLOWS].find(
            query,
            {"_id": 0},
        ).sort("carrier_handoff_scanned_at", -1).limit(limit).to_list(limit)
        items = []
        for workflow in workflows:
            row = await _order_view(
                repository,
                user_id=context["merchant_id"],
                workflow=workflow,
            )
            if not row:
                continue
            if not carrier_handoff_custody_is_visible(
                workflow_stage=workflow.get("stage"),
                handoff_state=workflow.get("carrier_handoff_state"),
                order_status_slug=row.get("order_status"),
                order_status_name=row.get("order_status_native"),
            ):
                continue
            items.append(row)
        return {
            "items": items,
            "total": len(items),
            "poll_seconds": 15,
        }

    @router.get("/delivery-tracking")
    async def list_delivery_tracking_shipments(
        stage: str = Query(pattern="^(delivering|delivered)$"),
        limit: int = Query(default=100, ge=1, le=300),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        """Read the external-carrier delivery board from Mezan state only.

        This endpoint never calls Salla. The existing Orders V2 page sync is
        the sole process that advances carrier custody to these stages.
        Store-courier orders intentionally remain on their separate flow.
        """
        context = await _actor_context(db, user)
        _require_permission(context, "fulfillment.ready.read")
        normalized_stage = _text(stage).casefold()
        workflows = await db[WORKFLOWS].find(
            {
                "user_id": context["merchant_id"],
                "stage": normalized_stage,
                "carrier_label_type": {"$ne": "store_courier"},
            },
            {"_id": 0},
        ).sort(
            (
                "delivered_at"
                if normalized_stage == "delivered"
                else "delivering_at"
            ),
            -1,
        ).limit(limit).to_list(limit)
        items = []
        for workflow in workflows:
            row = await _order_view(
                repository,
                user_id=context["merchant_id"],
                workflow=workflow,
            )
            if row:
                items.append(row)
        return {
            "stage": normalized_stage,
            "items": items,
            "total": len(items),
            "flow": "external_carrier",
            "sync_source": "mezan_orders_page_status_sync",
            "poll_seconds": 15,
        }

    @router.post("/carrier-handoff/scan")
    async def scan_carrier_handoff_shipment(
        payload: CarrierBarcodeRequest,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        context = await _actor_context(db, user)
        _require_permission(
            context,
            "fulfillment.carrier.handoff",
            responsibility="carrier_handoff",
        )
        try:
            return await receive_carrier_shipment(
                db,
                user_id=context["merchant_id"],
                scanned_barcode=payload.barcode,
                actor_id=context["actor_id"],
                actor_name=(
                    _text(user.get("name") or user.get("email"))
                    or "موظف تسليم الشحن"
                ),
            )
        except CarrierHandoffError as exc:
            raise _carrier_handoff_http_error(exc) from exc

    @router.post("/ready-to-ship/claim")
    async def claim_ready_batch(
        payload: ClaimReadyBatchRequest,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        context = await _actor_context(db, user)
        _require_permission(
            context,
            "fulfillment.batch.claim",
            responsibility="instant_ready",
        )
        await ensure_fulfillment_indexes(db)
        order_numbers = list(dict.fromkeys(
            _text(value) for value in payload.order_numbers if _text(value)
        ))
        workflows = await db[WORKFLOWS].find(
            {
                "user_id": context["merchant_id"],
                "order_number": {"$in": order_numbers},
                "stage": "ready_to_ship",
                "$or": [
                    {"claim_batch_id": {"$exists": False}},
                    {"claim_batch_id": None},
                    {"claim_batch_id": ""},
                ],
            },
            {"_id": 0},
        ).to_list(length=len(order_numbers))
        by_number = {
            _text(row.get("order_number")): row for row in workflows
        }
        if set(by_number) != set(order_numbers):
            raise HTTPException(
                status_code=409,
                detail={"code": "ready_orders_changed_refresh_required"},
            )
        inventory_by_order = {
            _text(workflow.get("order_number")): sorted({
                str(warehouse_id)
                for warehouse_id in (
                    (workflow.get("fulfillment_decision") or {}).get(
                        "warehouse_ids"
                    )
                    or []
                )
                if warehouse_id
            })
            for workflow in workflows
        }
        preparation_orders = {
            _text(workflow.get("order_number"))
            for workflow in workflows
            if _text(workflow.get("ready_to_ship_source"))
            == "preparation_receipt"
        }
        missing_inventory_orders = sorted(
            order_number
            for order_number, warehouse_ids in inventory_by_order.items()
            if not warehouse_ids and order_number not in preparation_orders
        )
        if missing_inventory_orders:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "ready_order_inventory_location_missing",
                    "order_numbers": missing_inventory_orders,
                },
            )
        unauthorized_orders = sorted(
            order_number
            for order_number, warehouse_ids in inventory_by_order.items()
            if warehouse_ids and not _warehouse_allowed(context, warehouse_ids)
        )
        if unauthorized_orders:
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "ready_orders_outside_assigned_warehouses",
                    "order_numbers": unauthorized_orders,
                },
            )
        warehouse_ids = sorted({
            warehouse_id
            for inventory_ids in inventory_by_order.values()
            for warehouse_id in inventory_ids
        })
        batch_id = f"ship_{uuid.uuid4().hex}"
        now = _now()
        result = await db[WORKFLOWS].update_many(
            {
                "user_id": context["merchant_id"],
                "order_number": {"$in": order_numbers},
                "stage": "ready_to_ship",
                "$or": [
                    {"claim_batch_id": {"$exists": False}},
                    {"claim_batch_id": None},
                    {"claim_batch_id": ""},
                ],
            },
            {"$set": {
                "claim_batch_id": batch_id,
                "claimed_by": context["actor_id"],
                "claimed_by_name": _text(user.get("name") or user.get("email")),
                "claimed_at": now,
                "updated_at": now,
            }},
        )
        if result.modified_count != len(order_numbers):
            await db[WORKFLOWS].update_many(
                {
                    "user_id": context["merchant_id"],
                    "claim_batch_id": batch_id,
                },
                {"$unset": {
                    "claim_batch_id": "",
                    "claimed_by": "",
                    "claimed_by_name": "",
                    "claimed_at": "",
                }},
            )
            raise HTTPException(
                status_code=409,
                detail={"code": "ready_orders_claim_conflict"},
            )
        batch = {
            "id": batch_id,
            "user_id": context["merchant_id"],
            "status": "claimed",
            "order_numbers": order_numbers,
            "warehouse_ids": warehouse_ids,
            "warehouse_resolution_sources": sorted({
                *(["inventory_location"] if warehouse_ids else []),
                *(["preparation_receipt"] if preparation_orders else []),
            }),
            "claimed_by": context["actor_id"],
            "claimed_by_name": _text(user.get("name") or user.get("email")),
            "claimed_at": now,
            "print_count": 0,
            "created_at": now,
            "updated_at": now,
        }
        await db[BATCHES].insert_one(batch)
        await db[EVENTS].insert_one({
            "id": uuid.uuid4().hex,
            "user_id": context["merchant_id"],
            "event_type": "shipping_batch_claimed",
            "batch_id": batch_id,
            "order_numbers": order_numbers,
            "warehouse_ids": warehouse_ids,
            "actor_id": context["actor_id"],
            "occurred_at": now,
        })
        batch.pop("_id", None)
        return {"ok": True, "batch": batch}

    @router.get("/batches")
    async def list_batches(
        limit: int = Query(default=50, ge=1, le=200),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        context = await _actor_context(db, user)
        _require_permission(
            context,
            "fulfillment.ready.read",
            responsibility="instant_ready",
        )
        query: dict[str, Any] = {"user_id": context["merchant_id"]}
        if not context["is_owner"]:
            query["claimed_by"] = context["actor_id"]
        rows = await db[BATCHES].find(
            query,
            {"_id": 0},
        ).sort("created_at", -1).limit(limit).to_list(limit)
        return {"items": rows, "total": len(rows)}

    @router.post("/batches/{batch_id}/print")
    async def print_batch(
        batch_id: str,
        payload: PrintBatchRequest = Body(default_factory=PrintBatchRequest),
        user: dict = Depends(current_user),
    ):
        context = await _actor_context(db, user)
        _require_permission(
            context,
            "fulfillment.labels.print",
            responsibility="shipping_labeling",
        )
        query: dict[str, Any] = {
            "id": batch_id,
            "user_id": context["merchant_id"],
        }
        if not context["is_owner"]:
            query["claimed_by"] = context["actor_id"]
        batch = await db[BATCHES].find_one(query, {"_id": 0})
        if not batch:
            raise HTTPException(
                status_code=404,
                detail={"code": "shipping_batch_not_found"},
            )
        print_count = int(batch.get("print_count") or 0)
        reason = _text(payload.reprint_reason)
        reprint = print_count > 0
        if reprint:
            if not reason:
                raise HTTPException(
                    status_code=409,
                    detail={"code": "batch_already_printed_reprint_reason_required"},
                )
            _require_permission(
                context,
                "fulfillment.labels.reprint",
                responsibility="shipping_labeling",
            )
        orders = []
        for order_number in batch.get("order_numbers") or []:
            try:
                orders.append(await get_order(
                    repository,
                    user_id=context["merchant_id"],
                    order_number=_text(order_number),
                ))
            except OrderNotFoundError:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "batch_order_not_found",
                        "order_number": order_number,
                    },
                )
        pdf_bytes = generate_shipping_batch_pdf(batch=batch, orders=orders)
        now = _now()
        event = {
            "id": uuid.uuid4().hex,
            "printed_at": now,
            "printed_by": context["actor_id"],
            "printed_by_name": _text(user.get("name") or user.get("email")),
            "reprint": reprint,
            "reprint_reason": reason or None,
        }
        guard = {**query, "print_count": print_count}
        if print_count == 0:
            guard["$or"] = [
                {"print_count": 0},
                {"print_count": {"$exists": False}},
            ]
            guard.pop("print_count", None)
        result = await db[BATCHES].update_one(
            guard,
            {
                "$set": {
                    "status": "printed",
                    "last_printed_at": now,
                    "last_printed_by": context["actor_id"],
                    "updated_at": now,
                },
                "$inc": {"print_count": 1},
                "$push": {"prints": event},
            },
        )
        if not result.modified_count:
            raise HTTPException(
                status_code=409,
                detail={"code": "batch_print_conflict_refresh_required"},
            )
        await db[EVENTS].insert_one({
            "id": uuid.uuid4().hex,
            "user_id": context["merchant_id"],
            "event_type": (
                "shipping_batch_reprinted"
                if reprint
                else "shipping_batch_printed"
            ),
            "batch_id": batch_id,
            "order_numbers": batch.get("order_numbers") or [],
            "reprint_reason": reason or None,
            "actor_id": context["actor_id"],
            "occurred_at": now,
        })
        filename = f"mezan_shipping_batch_{batch_id}.pdf"
        return StreamingResponse(
            io.BytesIO(pdf_bytes),
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "X-Mezan-Batch-Id": batch_id,
                "X-Mezan-Reprint": "true" if reprint else "false",
            },
        )

    @router.post("/batches/{batch_id}/pack")
    async def confirm_batch_packed(
        batch_id: str,
        payload: BatchActionRequest = Body(default_factory=BatchActionRequest),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        context = await _actor_context(db, user)
        _require_permission(
            context,
            "fulfillment.pack.confirm",
            responsibility="packing",
        )
        query: dict[str, Any] = {
            "id": batch_id,
            "user_id": context["merchant_id"],
            "print_count": {"$gt": 0},
        }
        if not context["is_owner"]:
            query["claimed_by"] = context["actor_id"]
        now = _now()
        result = await db[BATCHES].update_one(
            query,
            {"$set": {
                "status": "packed",
                "packed_at": now,
                "packed_by": context["actor_id"],
                "packing_note": _text(payload.note) or None,
                "updated_at": now,
            }},
        )
        if not result.modified_count:
            raise HTTPException(
                status_code=409,
                detail={"code": "batch_must_be_printed_before_packing"},
            )
        return {"ok": True, "batch_id": batch_id, "status": "packed"}

    @router.post("/batches/{batch_id}/handoff")
    async def confirm_carrier_handoff(
        batch_id: str,
        payload: BatchActionRequest = Body(default_factory=BatchActionRequest),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        context = await _actor_context(db, user)
        _require_permission(
            context,
            "fulfillment.carrier.handoff",
            responsibility="carrier_handoff",
        )
        query: dict[str, Any] = {
            "id": batch_id,
            "user_id": context["merchant_id"],
            "status": "packed",
        }
        if not context["is_owner"]:
            query["claimed_by"] = context["actor_id"]
        batch = await db[BATCHES].find_one(query, {"_id": 0})
        if not batch:
            raise HTTPException(
                status_code=409,
                detail={"code": "batch_must_be_packed_before_handoff"},
            )
        now = _now()
        lock = await db[BATCHES].update_one(
            query,
            {"$set": {
                "status": "inventory_consuming",
                "updated_at": now,
            }},
        )
        if not lock.modified_count:
            raise HTTPException(
                status_code=409,
                detail={"code": "batch_handoff_conflict_refresh_required"},
            )
        try:
            consumed_reservations = (
                await _consume_order_inventory_reservations(
                    db,
                    user_id=context["merchant_id"],
                    order_numbers=list(batch.get("order_numbers") or []),
                    actor_id=context["actor_id"],
                    batch_id=batch_id,
                )
            )
        except Exception:
            await db[BATCHES].update_one(
                {
                    "id": batch_id,
                    "user_id": context["merchant_id"],
                    "status": "inventory_consuming",
                },
                {
                    "$set": {
                        "status": "inventory_reconciliation_required",
                        "inventory_reconciliation_required_at": _now(),
                        "updated_at": _now(),
                    },
                },
            )
            raise
        handed_off_at = _now()
        await db[BATCHES].update_one(
            {
                "id": batch_id,
                "user_id": context["merchant_id"],
                "status": "inventory_consuming",
            },
            {
                "$set": {
                    "status": "handed_off",
                    "handed_off_at": handed_off_at,
                    "handed_off_by": context["actor_id"],
                    "handoff_note": _text(payload.note) or None,
                    "inventory_reservations_consumed": consumed_reservations,
                    "updated_at": handed_off_at,
                },
            },
        )
        await db[WORKFLOWS].update_many(
            {
                "user_id": context["merchant_id"],
                "claim_batch_id": batch_id,
                "stage": "ready_to_ship",
            },
            {"$set": {
                "stage": "completed",
                "completed_at": handed_off_at,
                "carrier_handoff_at": handed_off_at,
                "updated_at": handed_off_at,
            }},
        )
        return {"ok": True, "batch_id": batch_id, "status": "handed_off"}

    return router


__all__ = [
    "build_order_fulfillment_decision",
    "ensure_fulfillment_indexes",
    "INVENTORY_RESERVATIONS",
    "make_fulfillment_v2_router",
]
