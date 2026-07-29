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

from ai_store_access_control import effective_permissions
from ai_store_operations_foundation import PERMISSIONS, ROLE_ASSIGNMENTS
from fulfillment_batch_pdf import generate_shipping_batch_pdf
from order_engine.repository import MongoOrderRepository
from order_engine.service import OrderNotFoundError, get_order
from order_option_cost_snapshot_routes import binding_matches, selected_option_tokens
from product_fulfillment_rules import (
    FULFILLMENT_DECISIONS,
    FULFILLMENT_TYPE_INSTANT,
    PRODUCT_OPERATION_PROFILES,
    PRODUCT_RESOURCE_BINDINGS,
    classify_line_fulfillment,
    evaluate_order_fulfillment,
)
from product_option_cost_routes import BINDINGS, RESOURCES
from product_v2_routes import PRODUCTS
from warehouse_location_routes import LOCATIONS


WORKFLOWS = "order_review_workflows"
BATCHES = "mezan_fulfillment_batches_v2"
EVENTS = "mezan_fulfillment_events_v2"
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


async def ensure_fulfillment_indexes(db: Any) -> None:
    await db[WORKFLOWS].create_index(
        [("user_id", ASCENDING), ("order_number", ASCENDING)],
        unique=True,
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
        occupancy = location.get("occupancy") or {}
        for index, item in enumerate(occupancy.get("items") or []):
            quantity = float(item.get("quantity") or 0)
            if quantity <= 0:
                continue
            identifiers = {
                _text(item.get("product_id")),
                _text(item.get("sku")),
            }
            identifiers.discard("")
            rows.append({
                "key": f"{location.get('id') or location.get('code')}:{index}",
                "warehouse_id": _text(location.get("warehouse_id")),
                "identifiers": identifiers,
                "remaining": quantity,
            })
    return rows


def _reserve_inventory_for_line(
    *,
    stock_rows: list[dict[str, Any]],
    identifiers: set[str],
    quantity: float,
) -> tuple[bool, float, list[str]]:
    matches = [
        row for row in stock_rows
        if row["remaining"] > 0
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

    lines = []
    for item in order.items:
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
        if classification["resolved_type"] == FULFILLMENT_TYPE_INSTANT:
            (
                inventory_available,
                available_quantity,
                warehouse_ids,
            ) = _reserve_inventory_for_line(
                stock_rows=stock_rows,
                identifiers=identifiers,
                quantity=quantity,
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
            "warehouse_resolution_source": (
                "inventory_location"
                if warehouse_ids
                else "employee_assignment_pending"
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
    decision.update({
        "id": uuid.uuid4().hex,
        "user_id": user_id,
        "order_id": getattr(order, "order_id", None),
        "evaluated_at": _now(),
        "inventory_authority": "mezan_operational_inventory_v2",
        "external_calls_made": False,
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
    decision = await build_order_fulfillment_decision(
        db,
        user_id=user_id,
        order=order,
        operational_items=list(
            (workflow or {}).get("operational_items") or []
        ),
    )
    current_stage = _text((workflow or {}).get("stage")) or "pending_review"
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

    if workflow and (
        current_stage in TERMINAL_WORKFLOW_STAGES
        or workflow.get("claim_batch_id")
    ):
        return {
            "promoted": False,
            "reverted": False,
            "stage": current_stage,
            "decision": decision,
            "reason": "workflow_locked",
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
    assignment = await db[ROLE_ASSIGNMENTS].find_one(
        {"user_id": actor_id},
        {"_id": 0},
    )
    return {
        "actor_id": actor_id,
        "merchant_id": merchant_id,
        "is_owner": False,
        "permissions": set(effective_permissions(assignment)),
        "warehouse_ids": set((assignment or {}).get("warehouse_ids") or []),
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


def _warehouse_allowed(
    context: dict[str, Any],
    warehouse_ids: list[str],
) -> bool:
    if context["is_owner"]:
        return True
    required = {str(value) for value in warehouse_ids if value}
    if required:
        return required.issubset(context["warehouse_ids"])
    # No inventory location was recorded. An employee may take the order only
    # when their own branch/warehouse assignment can provide the fallback.
    return bool(context["warehouse_ids"])


def _resolve_claim_warehouse_ids(
    context: dict[str, Any],
    warehouse_ids: list[str],
) -> tuple[list[str], str]:
    inventory_ids = sorted({
        str(value) for value in warehouse_ids if value
    })
    if inventory_ids:
        return inventory_ids, "inventory_location"
    if context["is_owner"]:
        return [], "owner_assignment_pending"
    employee_ids = sorted({
        str(value) for value in context["warehouse_ids"] if value
    })
    return employee_ids, (
        "employee_assignment"
        if employee_ids
        else "employee_assignment_pending"
    )


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
            or "employee_assignment_pending"
        ),
        "claimed": bool(workflow.get("claim_batch_id")),
        "claim_batch_id": workflow.get("claim_batch_id"),
        "claimed_by": workflow.get("claimed_by"),
        "claimed_by_name": workflow.get("claimed_by_name"),
        "ready_at": workflow.get("ready_to_ship_at"),
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
            warehouses = (
                (workflow.get("fulfillment_decision") or {}).get("warehouse_ids")
                or []
            )
            if not _warehouse_allowed(context, warehouses):
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
        inventory_warehouse_ids = sorted({
            str(warehouse_id)
            for workflow in workflows
            for warehouse_id in (
                (workflow.get("fulfillment_decision") or {}).get("warehouse_ids")
                or []
            )
            if warehouse_id
        })
        if not _warehouse_allowed(context, inventory_warehouse_ids):
            raise HTTPException(
                status_code=403,
                detail={"code": "ready_orders_outside_assigned_warehouses"},
            )
        resolved_warehouses = {}
        for workflow in workflows:
            decision = workflow.get("fulfillment_decision") or {}
            resolved_warehouses[_text(workflow.get("order_number"))] = (
                _resolve_claim_warehouse_ids(
                    context,
                    decision.get("warehouse_ids") or [],
                )
            )
        warehouse_ids = sorted({
            warehouse_id
            for resolved_ids, _source in resolved_warehouses.values()
            for warehouse_id in resolved_ids
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
        for order_number, (resolved_ids, source) in resolved_warehouses.items():
            if source != "employee_assignment":
                continue
            await db[WORKFLOWS].update_one(
                {
                    "user_id": context["merchant_id"],
                    "order_number": order_number,
                    "claim_batch_id": batch_id,
                },
                {"$set": {
                    "fulfillment_decision.warehouse_ids": resolved_ids,
                    "fulfillment_decision.warehouse_resolution_source": source,
                    "fulfillment_decision.warehouse_resolved_by": (
                        context["actor_id"]
                    ),
                    "fulfillment_decision.warehouse_resolved_at": now,
                }},
            )
        batch = {
            "id": batch_id,
            "user_id": context["merchant_id"],
            "status": "claimed",
            "order_numbers": order_numbers,
            "warehouse_ids": warehouse_ids,
            "warehouse_resolution_sources": sorted({
                source for _ids, source in resolved_warehouses.values()
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
        await db[BATCHES].update_one(
            query,
            {"$set": {
                "status": "handed_off",
                "handed_off_at": now,
                "handed_off_by": context["actor_id"],
                "handoff_note": _text(payload.note) or None,
                "updated_at": now,
            }},
        )
        await db[WORKFLOWS].update_many(
            {
                "user_id": context["merchant_id"],
                "claim_batch_id": batch_id,
                "stage": "ready_to_ship",
            },
            {"$set": {
                "stage": "completed",
                "completed_at": now,
                "carrier_handoff_at": now,
                "updated_at": now,
            }},
        )
        return {"ok": True, "batch_id": batch_id, "status": "handed_off"}

    return router


__all__ = [
    "build_order_fulfillment_decision",
    "ensure_fulfillment_indexes",
    "make_fulfillment_v2_router",
]
