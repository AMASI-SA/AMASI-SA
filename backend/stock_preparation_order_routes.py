"""Operational stock-preparation orders that end in retained branch inventory.

These are internal work orders, not customer orders and not accounting
purchase invoices. They share the same preparation vocabulary and the same
inventory-location receipt primitive used by purchase receiving.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from pymongo import ASCENDING, DESCENDING
from pymongo.errors import DuplicateKeyError

from ai_store_access_control import effective_permissions
from ai_store_access_contract import find_role_assignments
from fulfillment_v2_routes import (
    _actor_context,
    _require_permission,
    _warehouse_allowed,
)
from inventory_receipt_service import (
    InventoryLocationCapacityError,
    place_inventory_receipt,
)
from product_inventory_receipt_routes import (
    INVENTORY_RECEIPTS,
    InventorySpecification,
    ensure_inventory_receipt_indexes,
)
from product_inventory_rules import (
    PREPARATION_STATE_READY_COMPLETE,
    build_inventory_configuration_key,
    canonical_specifications,
    normalize_specification_name,
    normalize_specification_text,
)
from product_field_cost_support import readable_variant_label
from product_v2_routes import PRODUCTS
from warehouse_location_routes import EVENTS, LOCATIONS, WAREHOUSES


STOCK_PREPARATION_ORDERS = "mezan_stock_preparation_orders_v2"
SUPPLIERS = "suppliers"
STOCK_PREPARATION_RESPONSIBILITY = "stock_preparation"

STOCK_PREPARATION_STATUS_REVIEWED = "reviewed"
STOCK_PREPARATION_STATUS_IN_PROGRESS = "in_progress"
STOCK_PREPARATION_STATUS_READY = "ready_for_receipt"
STOCK_PREPARATION_STATUS_RECEIVED = "received"
STOCK_PREPARATION_STATUS_CANCELLED = "cancelled"

ACTIVE_STOCK_PREPARATION_STATUSES = {
    STOCK_PREPARATION_STATUS_REVIEWED,
    STOCK_PREPARATION_STATUS_IN_PROGRESS,
    STOCK_PREPARATION_STATUS_READY,
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text(value: Any) -> str:
    return str(value or "").strip()


class StockPreparationSpecificationError(ValueError):
    def __init__(
        self,
        code: str,
        *,
        field: str | None = None,
        value: str | None = None,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.field = field
        self.value = value


def stock_preparation_product_fields(
    product: dict[str, Any],
) -> list[dict[str, Any]]:
    """Return the option/custom-field contract loaded from Salla."""
    fields: list[dict[str, Any]] = []
    for source_key, source_kind in (
        ("options", "option"),
        ("custom_fields", "custom_field"),
    ):
        rows = product.get(source_key) or []
        if not isinstance(rows, list):
            continue
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                continue
            name = _text(
                row.get("name")
                or row.get("label")
                or row.get("title")
            )
            if not name:
                continue
            values = []
            raw_values = row.get("values") or row.get("options") or []
            for value_index, value in enumerate(
                raw_values if isinstance(raw_values, list) else []
            ):
                if isinstance(value, dict):
                    value_id = _text(
                        value.get("id")
                        or value.get("value")
                        or value.get("key")
                    ) or str(value_index)
                    value_name = _text(
                        value.get("name")
                        or value.get("label")
                        or value.get("value")
                    ) or value_id
                else:
                    value_id = _text(value) or str(value_index)
                    value_name = _text(value) or value_id
                values.append({
                    "id": value_id,
                    "name": value_name,
                    "normalized_name": normalize_specification_text(
                        value_name
                    ),
                })
            field_id = _text(
                row.get("id")
                or row.get("field_id")
                or row.get("key")
            ) or str(index)
            fields.append({
                "source": source_kind,
                "id": field_id,
                "name": name,
                "canonical_name": normalize_specification_name(name),
                "type": _text(
                    row.get("type")
                    or row.get("input_type")
                    or row.get("field_type")
                ).lower() or ("select" if values else "text"),
                "required": bool(
                    row.get("required") or row.get("is_required")
                ),
                "values": values,
            })
    return fields


def validate_stock_preparation_specifications(
    *,
    product: dict[str, Any],
    specifications: Any,
) -> tuple[dict[str, str], list[dict[str, Any]]]:
    """Validate employee choices against the live Salla product contract."""
    if not product.get("details_loaded"):
        raise StockPreparationSpecificationError(
            "inventory_product_details_required",
        )

    canonical = canonical_specifications(specifications)
    fields = stock_preparation_product_fields(product)
    fields_by_name: dict[str, list[dict[str, Any]]] = {}
    for field in fields:
        fields_by_name.setdefault(field["canonical_name"], []).append(field)
        if field["required"] and field["canonical_name"] not in canonical:
            raise StockPreparationSpecificationError(
                "inventory_required_specification_missing",
                field=field["name"],
            )

    selected: list[dict[str, Any]] = []
    for name, value in canonical.items():
        candidates = fields_by_name.get(name) or []
        if not candidates:
            raise StockPreparationSpecificationError(
                "inventory_specification_not_in_salla",
                field=name,
                value=value,
            )
        matched_field = None
        matched_value = None
        for field in candidates:
            allowed_values = field.get("values") or []
            if not allowed_values:
                matched_field = field
                break
            matched_value = next(
                (
                    row
                    for row in allowed_values
                    if row["normalized_name"] == value
                    or normalize_specification_text(row["id"]) == value
                ),
                None,
            )
            if matched_value:
                matched_field = field
                break
        if not matched_field:
            raise StockPreparationSpecificationError(
                "inventory_specification_value_not_in_salla",
                field=name,
                value=value,
            )
        selected.append({
            "source": matched_field["source"],
            "field_id": matched_field["id"],
            "field_name": matched_field["name"],
            "value_id": (
                matched_value.get("id")
                if matched_value
                else None
            ),
            "value_name": (
                matched_value.get("name")
                if matched_value
                else value
            ),
        })
    return canonical, selected


def validate_stock_preparation_variant(
    *,
    product: dict[str, Any],
    variant: dict[str, Any] | None,
    specifications: dict[str, str],
) -> None:
    if not variant:
        return
    _, selections = readable_variant_label(
        variant,
        product.get("options") or [],
    )
    variant_specifications = canonical_specifications([
        {
            "name": row.get("option_name"),
            "value": row.get("value_name"),
        }
        for row in selections
    ])
    for name, value in variant_specifications.items():
        if specifications.get(name) != value:
            raise StockPreparationSpecificationError(
                "inventory_variant_specification_mismatch",
                field=name,
                value=specifications.get(name),
            )


def _public_order(order: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in order.items()
        if key not in {"_id", "user_id", "payload_fingerprint"}
    }


class StockPreparationItemRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    product_id: str = Field(min_length=1, max_length=160)
    variant_id: str | None = Field(default=None, max_length=160)
    quantity: int = Field(ge=1, le=100000)
    specifications: list[InventorySpecification] = Field(
        default_factory=list,
        max_length=30,
    )


class StockPreparationOrderCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idempotency_key: str = Field(min_length=8, max_length=120)
    supplier_id: str = Field(min_length=1, max_length=120)
    assigned_employee_id: str = Field(min_length=1, max_length=120)
    destination_warehouse_id: str = Field(min_length=1, max_length=120)
    items: list[StockPreparationItemRequest] = Field(
        min_length=1,
        max_length=100,
    )
    note: str | None = Field(default=None, max_length=1200)


class StockPreparationActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal[
        "start_preparation",
        "mark_ready_for_receipt",
        "return_to_preparation",
        "cancel",
    ]
    expected_revision: int = Field(ge=1)
    note: str | None = Field(default=None, max_length=1200)


class StockPreparationReceiptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idempotency_key: str = Field(min_length=8, max_length=120)
    item_id: str = Field(min_length=1, max_length=120)
    location_id: str = Field(min_length=1, max_length=120)
    scanned_barcode: str = Field(min_length=1, max_length=160)
    quantity: int = Field(ge=1, le=100000)


def stock_preparation_order_fingerprint(
    payload: StockPreparationOrderCreateRequest,
) -> str:
    normalized = {
        **payload.model_dump(),
        "idempotency_key": _text(payload.idempotency_key),
        "note": _text(payload.note) or None,
        "items": [
            {
                "product_id": _text(item.product_id),
                "variant_id": _text(item.variant_id) or None,
                "quantity": item.quantity,
                "specifications": canonical_specifications(
                    [
                        row.model_dump()
                        for row in item.specifications
                    ]
                ),
            }
            for item in payload.items
        ],
    }
    encoded = json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def stock_preparation_receipt_fingerprint(
    *,
    order_id: str,
    payload: StockPreparationReceiptRequest,
) -> str:
    encoded = json.dumps(
        {
            **payload.model_dump(),
            "order_id": _text(order_id),
            "idempotency_key": _text(payload.idempotency_key),
            "scanned_barcode": _text(
                payload.scanned_barcode
            ).upper(),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def apply_received_quantities(
    order: dict[str, Any],
    received_by_item: dict[str, int],
) -> dict[str, Any]:
    """Return an order view with receipt-ledger-derived item progress."""
    items = []
    requested_total = 0
    received_total = 0
    for row in order.get("items") or []:
        item = dict(row)
        requested = int(item.get("quantity") or 0)
        received = min(
            requested,
            max(0, int(received_by_item.get(_text(item.get("id")), 0))),
        )
        item["received_quantity"] = received
        item["remaining_quantity"] = max(0, requested - received)
        items.append(item)
        requested_total += requested
        received_total += received
    return {
        **order,
        "items": items,
        "requested_quantity": requested_total,
        "received_quantity": received_total,
        "remaining_quantity": max(0, requested_total - received_total),
        "retention_complete": (
            requested_total > 0 and received_total >= requested_total
        ),
    }


def next_stock_preparation_status(
    *,
    current_status: str,
    action: str,
    has_received_quantity: bool = False,
) -> str:
    transitions = {
        (
            STOCK_PREPARATION_STATUS_REVIEWED,
            "start_preparation",
        ): STOCK_PREPARATION_STATUS_IN_PROGRESS,
        (
            STOCK_PREPARATION_STATUS_IN_PROGRESS,
            "mark_ready_for_receipt",
        ): STOCK_PREPARATION_STATUS_READY,
        (
            STOCK_PREPARATION_STATUS_READY,
            "return_to_preparation",
        ): STOCK_PREPARATION_STATUS_IN_PROGRESS,
    }
    if action == "cancel":
        if (
            current_status not in ACTIVE_STOCK_PREPARATION_STATUSES
            or has_received_quantity
        ):
            raise ValueError("stock_preparation_cancel_forbidden")
        return STOCK_PREPARATION_STATUS_CANCELLED
    target = transitions.get((current_status, action))
    if not target:
        raise ValueError("stock_preparation_transition_invalid")
    return target


async def ensure_stock_preparation_indexes(db: Any) -> None:
    await db[STOCK_PREPARATION_ORDERS].create_index(
        [("user_id", ASCENDING), ("id", ASCENDING)],
        unique=True,
        name="uq_stock_preparation_order_v2",
    )
    await db[STOCK_PREPARATION_ORDERS].create_index(
        [("user_id", ASCENDING), ("idempotency_key", ASCENDING)],
        unique=True,
        name="uq_stock_preparation_idempotency_v2",
    )
    await db[STOCK_PREPARATION_ORDERS].create_index(
        [
            ("user_id", ASCENDING),
            ("assigned_employee_id", ASCENDING),
            ("status", ASCENDING),
            ("updated_at", DESCENDING),
        ],
        name="ix_stock_preparation_employee_stage_v2",
    )


async def _received_by_order_item(
    db: Any,
    *,
    merchant_id: str,
    order_ids: list[str],
) -> dict[tuple[str, str], int]:
    if not order_ids:
        return {}
    receipts = await db[INVENTORY_RECEIPTS].find(
        {
            "user_id": merchant_id,
            "status": "posted",
            "source_type": "stock_preparation_order",
            "source_id": {"$in": order_ids},
        },
        {
            "_id": 0,
            "source_id": 1,
            "source_line_id": 1,
            "quantity": 1,
        },
    ).to_list(length=50000)
    result: dict[tuple[str, str], int] = {}
    for receipt in receipts:
        key = (
            _text(receipt.get("source_id")),
            _text(receipt.get("source_line_id")),
        )
        result[key] = result.get(key, 0) + int(
            receipt.get("quantity") or 0
        )
    return result


async def _order_with_progress(
    db: Any,
    *,
    merchant_id: str,
    order: dict[str, Any],
) -> dict[str, Any]:
    totals = await _received_by_order_item(
        db,
        merchant_id=merchant_id,
        order_ids=[_text(order.get("id"))],
    )
    return apply_received_quantities(
        order,
        {
            item_id: quantity
            for (order_id, item_id), quantity in totals.items()
            if order_id == _text(order.get("id"))
        },
    )


async def _eligible_operators(
    db: Any,
    *,
    merchant_id: str,
) -> list[dict[str, Any]]:
    users = await db.users.find(
        {
            "$or": [
                {"id": merchant_id},
                {"created_by": merchant_id},
            ],
        },
        {
            "_id": 0,
            "id": 1,
            "name": 1,
            "email": 1,
            "role": 1,
            "created_by": 1,
        },
    ).sort("created_at", -1).to_list(5000)
    user_ids = [
        _text(row.get("id"))
        for row in users
        if _text(row.get("id"))
    ]
    assignments = await find_role_assignments(
        db,
        owner_user_id=merchant_id,
        user_ids=user_ids,
    )
    by_user = {
        _text(row.get("user_id")): row for row in assignments
    }
    result = []
    for member in users:
        member_id = _text(member.get("id"))
        is_owner = (
            member_id == merchant_id
            or _text(member.get("role")).casefold() == "owner"
        )
        assignment = by_user.get(member_id)
        permissions = set(effective_permissions(assignment))
        responsibilities = set(
            (assignment or {}).get("fulfillment_responsibilities")
            or []
        )
        eligible = (
            is_owner
            or (
                (assignment or {}).get("enabled", True)
                and "inventory.preparation.work" in permissions
                and STOCK_PREPARATION_RESPONSIBILITY
                in responsibilities
            )
        )
        result.append({
            "id": member_id,
            "name": member.get("name"),
            "email": member.get("email"),
            "is_owner": is_owner,
            "warehouse_ids": (
                []
                if is_owner
                else list((assignment or {}).get("warehouse_ids") or [])
            ),
            "eligible_for_stock_preparation": eligible,
        })
    return result


async def _validate_assigned_operator(
    db: Any,
    *,
    merchant_id: str,
    employee_id: str,
    warehouse_id: str,
) -> dict[str, Any]:
    operators = await _eligible_operators(
        db,
        merchant_id=merchant_id,
    )
    operator = next(
        (
            row for row in operators
            if _text(row.get("id")) == employee_id
        ),
        None,
    )
    if not operator:
        raise HTTPException(
            status_code=404,
            detail={"code": "stock_preparation_employee_not_found"},
        )
    if not operator["eligible_for_stock_preparation"]:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "stock_preparation_employee_not_eligible"
            },
        )
    if (
        not operator["is_owner"]
        and warehouse_id not in set(operator["warehouse_ids"])
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "code": (
                    "stock_preparation_employee_warehouse_mismatch"
                )
            },
        )
    return operator


async def _list_orders(
    db: Any,
    *,
    context: dict[str, Any],
    limit: int,
) -> list[dict[str, Any]]:
    query: dict[str, Any] = {"user_id": context["merchant_id"]}
    if not context["is_owner"]:
        query["assigned_employee_id"] = context["actor_id"]
        query["destination_warehouse_id"] = {
            "$in": sorted(context["warehouse_ids"])
        }
    orders = await db[STOCK_PREPARATION_ORDERS].find(
        query,
        {"_id": 0, "payload_fingerprint": 0},
    ).sort("updated_at", -1).limit(limit).to_list(limit)
    totals = await _received_by_order_item(
        db,
        merchant_id=context["merchant_id"],
        order_ids=[_text(row.get("id")) for row in orders],
    )
    result = []
    for order in orders:
        order_id = _text(order.get("id"))
        result.append(apply_received_quantities(
            order,
            {
                item_id: quantity
                for (receipt_order_id, item_id), quantity
                in totals.items()
                if receipt_order_id == order_id
            },
        ))
    return result


def make_stock_preparation_order_router(
    db: Any,
    current_user: Callable[..., Any],
) -> APIRouter:
    router = APIRouter(
        prefix="/inventory-v2",
        tags=["Mezan Stock Preparation V2"],
    )

    @router.get("/stock-preparation-orders/catalog")
    async def stock_preparation_catalog(
        limit: int = Query(default=200, ge=1, le=500),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        context = await _actor_context(db, user)
        _require_permission(context, "inventory.preparation.read")
        await ensure_stock_preparation_indexes(db)
        merchant_id = context["merchant_id"]
        suppliers = await db[SUPPLIERS].find(
            {
                "user_id": merchant_id,
                "status": {"$ne": "inactive"},
            },
            {
                "_id": 0,
                "id": 1,
                "company_name": 1,
                "contact_person": 1,
                "phone": 1,
            },
        ).sort("company_name", 1).to_list(2000)
        return {
            "ok": True,
            "suppliers": suppliers,
            "operators": await _eligible_operators(
                db,
                merchant_id=merchant_id,
            ),
            "orders": await _list_orders(
                db,
                context=context,
                limit=limit,
            ),
            "permissions": {
                "can_create": (
                    "inventory.preparation.create"
                    in context["permissions"]
                ),
                "can_work": (
                    "inventory.preparation.work"
                    in context["permissions"]
                    and (
                        context["is_owner"]
                        or STOCK_PREPARATION_RESPONSIBILITY
                        in context["responsibilities"]
                    )
                ),
                "can_receive": (
                    "inventory.preparation.receive"
                    in context["permissions"]
                ),
            },
            "retention_mode": {
                "value": "keep_in_inventory",
                "label": "الاحتفاظ بالمخزون",
                "customer_shipping_allowed": False,
                "financial_invoice_created_automatically": False,
            },
        }

    @router.post("/stock-preparation-orders", status_code=201)
    async def create_stock_preparation_order(
        payload: StockPreparationOrderCreateRequest,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        context = await _actor_context(db, user)
        _require_permission(
            context,
            "inventory.preparation.create",
        )
        await ensure_stock_preparation_indexes(db)
        merchant_id = context["merchant_id"]
        warehouse_id = _text(payload.destination_warehouse_id)
        if not _warehouse_allowed(context, [warehouse_id]):
            raise HTTPException(
                status_code=403,
                detail={"code": "inventory_warehouse_not_assigned"},
            )
        warehouse = await db[WAREHOUSES].find_one(
            {
                "id": warehouse_id,
                "user_id": merchant_id,
                "status": {"$ne": "disabled"},
            },
            {
                "_id": 0,
                "id": 1,
                "name": 1,
                "code": 1,
                "city": 1,
            },
        )
        if not warehouse:
            raise HTTPException(
                status_code=404,
                detail={
                    "code": "stock_preparation_warehouse_not_found"
                },
            )
        supplier = await db[SUPPLIERS].find_one(
            {
                "id": payload.supplier_id,
                "user_id": merchant_id,
                "status": {"$ne": "inactive"},
            },
            {
                "_id": 0,
                "id": 1,
                "company_name": 1,
                "contact_person": 1,
                "phone": 1,
            },
        )
        if not supplier:
            raise HTTPException(
                status_code=404,
                detail={"code": "stock_preparation_supplier_not_found"},
            )
        operator = await _validate_assigned_operator(
            db,
            merchant_id=merchant_id,
            employee_id=_text(payload.assigned_employee_id),
            warehouse_id=warehouse_id,
        )
        fingerprint = stock_preparation_order_fingerprint(payload)
        existing = await db[STOCK_PREPARATION_ORDERS].find_one(
            {
                "user_id": merchant_id,
                "idempotency_key": _text(payload.idempotency_key),
            },
            {"_id": 0},
        )
        if existing:
            if existing.get("payload_fingerprint") != fingerprint:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": (
                            "stock_preparation_idempotency_conflict"
                        )
                    },
                )
            return {
                "ok": True,
                "duplicate": True,
                "order": _public_order(await _order_with_progress(
                    db,
                    merchant_id=merchant_id,
                    order=existing,
                )),
            }

        requested_product_ids = {
            _text(row.product_id) for row in payload.items
        }
        products = await db[PRODUCTS].find(
            {
                "user_id": merchant_id,
                "archived": {"$ne": True},
                "$or": [
                    {
                        "mezan_product_id": {
                            "$in": sorted(requested_product_ids)
                        },
                    },
                    {
                        "salla_product_id": {
                            "$in": sorted(requested_product_ids)
                        },
                    },
                ],
            },
            {
                "_id": 0,
                "mezan_product_id": 1,
                "salla_product_id": 1,
                "name": 1,
                "sku": 1,
                "barcode": 1,
                "main_image": 1,
                "options": 1,
                "custom_fields": 1,
                "details_loaded": 1,
                "variants": 1,
                "variants_count": 1,
            },
        ).to_list(length=1000)
        by_identifier: dict[str, dict[str, Any]] = {}
        for product in products:
            for value in (
                product.get("mezan_product_id"),
                product.get("salla_product_id"),
            ):
                if _text(value):
                    by_identifier[_text(value)] = product

        order_items = []
        for requested in payload.items:
            product = by_identifier.get(_text(requested.product_id))
            if not product:
                raise HTTPException(
                    status_code=404,
                    detail={
                        "code": "mezan_product_not_found",
                        "product_id": requested.product_id,
                    },
                )
            variants = [
                row
                for row in product.get("variants") or []
                if isinstance(row, dict) and _text(row.get("id"))
            ]
            if int(product.get("variants_count") or 0) > 0 and not variants:
                raise HTTPException(
                    status_code=409,
                    detail={"code": "inventory_variants_not_loaded"},
                )
            selected_variant = next(
                (
                    row
                    for row in variants
                    if _text(row.get("id"))
                    == _text(requested.variant_id)
                ),
                None,
            )
            if variants and not _text(requested.variant_id):
                raise HTTPException(
                    status_code=422,
                    detail={"code": "inventory_variant_required"},
                )
            if _text(requested.variant_id) and not selected_variant:
                raise HTTPException(
                    status_code=404,
                    detail={"code": "inventory_variant_not_found"},
                )
            try:
                specifications, salla_selections = (
                    validate_stock_preparation_specifications(
                        product=product,
                        specifications=[
                            row.model_dump()
                            for row in requested.specifications
                        ],
                    )
                )
                validate_stock_preparation_variant(
                    product=product,
                    variant=selected_variant,
                    specifications=specifications,
                )
            except StockPreparationSpecificationError as exc:
                raise HTTPException(
                    status_code=422
                    if exc.code
                    != "inventory_product_details_required"
                    else 409,
                    detail={
                        "code": exc.code,
                        "field": exc.field,
                        "value": exc.value,
                    },
                ) from exc
            inventory_sku = _text(
                (selected_variant or {}).get("sku")
                or product.get("sku")
            )
            order_items.append({
                "id": f"stock_item_{uuid.uuid4().hex}",
                "mezan_product_id": product.get(
                    "mezan_product_id"
                ),
                "salla_product_id": product.get(
                    "salla_product_id"
                ),
                "salla_variant_id": (
                    _text((selected_variant or {}).get("id")) or None
                ),
                "variant_name": (
                    (selected_variant or {}).get("display_name")
                    or (selected_variant or {}).get("name")
                ),
                "product_name": product.get("name"),
                "sku": inventory_sku or None,
                "barcode": product.get("barcode"),
                "main_image": product.get("main_image"),
                "quantity": requested.quantity,
                "specifications": specifications,
                "salla_option_selections": salla_selections,
                "target_preparation_state": (
                    PREPARATION_STATE_READY_COMPLETE
                ),
                "configuration_key": (
                    build_inventory_configuration_key(
                        sku=(
                            inventory_sku
                            or product.get("mezan_product_id")
                        ),
                        preparation_state=(
                            PREPARATION_STATE_READY_COMPLETE
                        ),
                        specifications=specifications,
                    )
                ),
            })

        now = _now()
        order_id = f"stockprep_{uuid.uuid4().hex}"
        reference = (
            f"STK-{datetime.now(timezone.utc):%Y%m%d}-"
            f"{order_id[-6:].upper()}"
        )
        order = {
            "id": order_id,
            "reference": reference,
            "user_id": merchant_id,
            "idempotency_key": _text(payload.idempotency_key),
            "payload_fingerprint": fingerprint,
            "source_type": "stock_preparation_order",
            "retention_mode": "keep_in_inventory",
            "customer_order_id": None,
            "customer_shipping_allowed": False,
            "financial_invoice_created_automatically": False,
            "status": STOCK_PREPARATION_STATUS_REVIEWED,
            "stage": STOCK_PREPARATION_STATUS_REVIEWED,
            "revision": 1,
            "supplier_id": supplier.get("id"),
            "supplier_name": supplier.get("company_name"),
            "supplier_snapshot": supplier,
            "assigned_employee_id": operator.get("id"),
            "assigned_employee_name": (
                operator.get("name") or operator.get("email")
            ),
            "destination_warehouse_id": warehouse.get("id"),
            "destination_warehouse_name": warehouse.get("name"),
            "destination_warehouse_snapshot": warehouse,
            "items": order_items,
            "note": _text(payload.note) or None,
            "history": [{
                "action": "stock_preparation_created",
                "from_status": None,
                "to_status": STOCK_PREPARATION_STATUS_REVIEWED,
                "actor_id": context["actor_id"],
                "actor_name": user.get("name") or user.get("email"),
                "note": _text(payload.note) or None,
                "occurred_at": now,
            }],
            "created_at": now,
            "created_by": context["actor_id"],
            "updated_at": now,
            "updated_by": context["actor_id"],
        }
        try:
            await db[STOCK_PREPARATION_ORDERS].insert_one(dict(order))
        except DuplicateKeyError as exc:
            duplicate = await db[STOCK_PREPARATION_ORDERS].find_one(
                {
                    "user_id": merchant_id,
                    "idempotency_key": _text(
                        payload.idempotency_key
                    ),
                },
                {"_id": 0},
            )
            if (
                not duplicate
                or duplicate.get("payload_fingerprint") != fingerprint
            ):
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": (
                            "stock_preparation_idempotency_conflict"
                        )
                    },
                ) from exc
            return {
                "ok": True,
                "duplicate": True,
                "order": _public_order(duplicate),
            }
        await db[EVENTS].insert_one({
            "id": uuid.uuid4().hex,
            "user_id": merchant_id,
            "event_type": "stock_preparation_order_created",
            "stock_preparation_order_id": order_id,
            "reference": reference,
            "supplier_id": supplier.get("id"),
            "assigned_employee_id": operator.get("id"),
            "warehouse_id": warehouse.get("id"),
            "requested_quantity": sum(
                int(row["quantity"]) for row in order_items
            ),
            "actor_id": context["actor_id"],
            "occurred_at": now,
        })
        return {
            "ok": True,
            "duplicate": False,
            "order": _public_order(apply_received_quantities(
                order,
                {},
            )),
        }

    @router.post("/stock-preparation-orders/{order_id}/actions")
    async def transition_stock_preparation_order(
        order_id: str,
        payload: StockPreparationActionRequest,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        context = await _actor_context(db, user)
        _require_permission(
            context,
            "inventory.preparation.work",
            responsibility=STOCK_PREPARATION_RESPONSIBILITY,
        )
        merchant_id = context["merchant_id"]
        order = await db[STOCK_PREPARATION_ORDERS].find_one(
            {"id": order_id, "user_id": merchant_id},
            {"_id": 0},
        )
        if not order:
            raise HTTPException(
                status_code=404,
                detail={"code": "stock_preparation_order_not_found"},
            )
        if not _warehouse_allowed(
            context,
            [_text(order.get("destination_warehouse_id"))],
        ):
            raise HTTPException(
                status_code=403,
                detail={"code": "inventory_warehouse_not_assigned"},
            )
        if (
            not context["is_owner"]
            and _text(order.get("assigned_employee_id"))
            != context["actor_id"]
        ):
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "stock_preparation_not_assigned_to_actor"
                },
            )
        progressed = await _order_with_progress(
            db,
            merchant_id=merchant_id,
            order=order,
        )
        try:
            target_status = next_stock_preparation_status(
                current_status=_text(order.get("status")),
                action=payload.action,
                has_received_quantity=(
                    int(progressed.get("received_quantity") or 0) > 0
                ),
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": str(exc)},
            ) from exc
        now = _now()
        update = await db[STOCK_PREPARATION_ORDERS].update_one(
            {
                "id": order_id,
                "user_id": merchant_id,
                "revision": payload.expected_revision,
                "status": order.get("status"),
            },
            {
                "$set": {
                    "status": target_status,
                    "stage": target_status,
                    "updated_at": now,
                    "updated_by": context["actor_id"],
                    f"{target_status}_at": now,
                },
                "$inc": {"revision": 1},
                "$push": {
                    "history": {
                        "action": payload.action,
                        "from_status": order.get("status"),
                        "to_status": target_status,
                        "actor_id": context["actor_id"],
                        "actor_name": (
                            user.get("name") or user.get("email")
                        ),
                        "note": _text(payload.note) or None,
                        "occurred_at": now,
                    },
                },
            },
        )
        if not update.modified_count:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "stock_preparation_revision_conflict"
                },
            )
        updated = await db[STOCK_PREPARATION_ORDERS].find_one(
            {"id": order_id, "user_id": merchant_id},
            {"_id": 0},
        )
        await db[EVENTS].insert_one({
            "id": uuid.uuid4().hex,
            "user_id": merchant_id,
            "event_type": "stock_preparation_order_transitioned",
            "stock_preparation_order_id": order_id,
            "action": payload.action,
            "from_status": order.get("status"),
            "to_status": target_status,
            "actor_id": context["actor_id"],
            "occurred_at": now,
        })
        return {
            "ok": True,
            "order": _public_order(await _order_with_progress(
                db,
                merchant_id=merchant_id,
                order=updated or {},
            )),
        }

    @router.post(
        "/stock-preparation-orders/{order_id}/receipts",
        status_code=201,
    )
    async def receive_prepared_stock(
        order_id: str,
        payload: StockPreparationReceiptRequest,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        context = await _actor_context(db, user)
        _require_permission(
            context,
            "inventory.preparation.receive",
        )
        await ensure_inventory_receipt_indexes(db)
        merchant_id = context["merchant_id"]
        actor_id = context["actor_id"]
        order = await db[STOCK_PREPARATION_ORDERS].find_one(
            {"id": order_id, "user_id": merchant_id},
            {"_id": 0},
        )
        if not order:
            raise HTTPException(
                status_code=404,
                detail={"code": "stock_preparation_order_not_found"},
            )
        if order.get("status") != STOCK_PREPARATION_STATUS_READY:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "stock_preparation_not_ready_for_receipt"
                },
            )
        warehouse_id = _text(
            order.get("destination_warehouse_id")
        )
        if not _warehouse_allowed(context, [warehouse_id]):
            raise HTTPException(
                status_code=403,
                detail={"code": "inventory_warehouse_not_assigned"},
            )
        item = next(
            (
                row for row in order.get("items") or []
                if _text(row.get("id")) == payload.item_id
            ),
            None,
        )
        if not item:
            raise HTTPException(
                status_code=404,
                detail={"code": "stock_preparation_item_not_found"},
            )
        location = await db[LOCATIONS].find_one(
            {
                "id": payload.location_id,
                "user_id": merchant_id,
                "warehouse_id": warehouse_id,
            },
            {"_id": 0},
        )
        if not location:
            raise HTTPException(
                status_code=404,
                detail={"code": "inventory_location_not_found"},
            )
        if location.get("state") == "disabled":
            raise HTTPException(
                status_code=409,
                detail={"code": "inventory_location_disabled"},
            )
        expected_barcode = _text(
            location.get("barcode_value") or location.get("code")
        ).upper()
        scanned_barcode = _text(payload.scanned_barcode).upper()
        if expected_barcode != scanned_barcode:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "inventory_location_barcode_mismatch",
                    "expected_location_code": expected_barcode,
                },
            )
        fingerprint = stock_preparation_receipt_fingerprint(
            order_id=order_id,
            payload=payload,
        )
        existing = await db[INVENTORY_RECEIPTS].find_one(
            {
                "user_id": merchant_id,
                "idempotency_key": _text(payload.idempotency_key),
            },
            {"_id": 0},
        )
        if (
            existing
            and existing.get("payload_fingerprint") != fingerprint
        ):
            raise HTTPException(
                status_code=409,
                detail={"code": "inventory_receipt_idempotency_conflict"},
            )
        if existing and existing.get("status") == "posted":
            return {
                "ok": True,
                "duplicate": True,
                "receipt": {
                    key: value
                    for key, value in existing.items()
                    if key not in {
                        "_id",
                        "user_id",
                        "payload_fingerprint",
                    }
                },
                "order": _public_order(await _order_with_progress(
                    db,
                    merchant_id=merchant_id,
                    order=order,
                )),
            }
        now = _now()
        receipt_id = _text((existing or {}).get("id")) or uuid.uuid4().hex
        receipt = {
            "id": receipt_id,
            "user_id": merchant_id,
            "idempotency_key": _text(payload.idempotency_key),
            "payload_fingerprint": fingerprint,
            "status": "pending",
            "source_type": "stock_preparation_order",
            "source_id": order_id,
            "source_line_id": payload.item_id,
            "stock_preparation_order_id": order_id,
            "stock_preparation_reference": order.get("reference"),
            "supplier_id": order.get("supplier_id"),
            "supplier_name": order.get("supplier_name"),
            "mezan_product_id": item.get("mezan_product_id"),
            "salla_product_id": item.get("salla_product_id"),
            "salla_variant_id": item.get("salla_variant_id"),
            "variant_name": item.get("variant_name"),
            "product_name": item.get("product_name"),
            "sku": item.get("sku"),
            "quantity": payload.quantity,
            "preparation_state": PREPARATION_STATE_READY_COMPLETE,
            "specifications": item.get("specifications") or {},
            "configuration_key": item.get("configuration_key"),
            "warehouse_id": warehouse_id,
            "location_id": payload.location_id,
            "location_code": expected_barcode,
            "retention_mode": "keep_in_inventory",
            "received_by": actor_id,
            "created_at": (existing or {}).get("created_at") or now,
            "updated_at": now,
        }
        if not existing:
            try:
                await db[INVENTORY_RECEIPTS].insert_one(dict(receipt))
            except DuplicateKeyError as exc:
                duplicate = await db[INVENTORY_RECEIPTS].find_one(
                    {
                        "user_id": merchant_id,
                        "idempotency_key": _text(
                            payload.idempotency_key
                        ),
                    },
                    {"_id": 0},
                )
                if (
                    not duplicate
                    or duplicate.get("payload_fingerprint")
                    != fingerprint
                ):
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "code": (
                                "inventory_receipt_idempotency_conflict"
                            )
                        },
                    ) from exc
                if duplicate.get("status") == "posted":
                    return {
                        "ok": True,
                        "duplicate": True,
                        "receipt": {
                            key: value
                            for key, value in duplicate.items()
                            if key not in {
                                "_id",
                                "user_id",
                                "payload_fingerprint",
                            }
                        },
                        "order": _public_order(
                            await _order_with_progress(
                                db,
                                merchant_id=merchant_id,
                                order=order,
                            )
                        ),
                    }
                receipt_id = _text(duplicate.get("id"))
                receipt = {
                    **duplicate,
                    **receipt,
                    "id": receipt_id,
                }

        active_receipts = await db[INVENTORY_RECEIPTS].find(
            {
                "user_id": merchant_id,
                "source_type": "stock_preparation_order",
                "source_id": order_id,
                "source_line_id": payload.item_id,
                "status": {"$in": ["pending", "posted"]},
            },
            {
                "_id": 0,
                "id": 1,
                "quantity": 1,
                "status": 1,
                "created_at": 1,
            },
        ).to_list(length=10000)
        requested_quantity = int(item.get("quantity") or 0)
        posted_quantity = sum(
            int(row.get("quantity") or 0)
            for row in active_receipts
            if row.get("status") == "posted"
        )
        pending_receipts = sorted(
            (
                row for row in active_receipts
                if row.get("status") == "pending"
            ),
            key=lambda row: (
                _text(row.get("created_at")),
                _text(row.get("id")),
            ),
        )
        accepted_quantity = posted_quantity
        current_accepted = False
        for pending in pending_receipts:
            pending_quantity = int(pending.get("quantity") or 0)
            if (
                accepted_quantity + pending_quantity
                > requested_quantity
            ):
                continue
            accepted_quantity += pending_quantity
            if _text(pending.get("id")) == receipt_id:
                current_accepted = True
        if not current_accepted:
            await db[INVENTORY_RECEIPTS].update_one(
                {"user_id": merchant_id, "id": receipt_id},
                {
                    "$set": {
                        "status": "failed",
                        "failure_code": (
                            "stock_preparation_quantity_exceeded"
                        ),
                        "updated_at": _now(),
                    },
                },
            )
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "stock_preparation_quantity_exceeded",
                    "remaining_quantity": max(
                        0,
                        requested_quantity - posted_quantity,
                    ),
                },
            )

        inventory_item = {
            "receipt_id": receipt_id,
            "product_id": (
                item.get("salla_product_id")
                or item.get("mezan_product_id")
            ),
            "mezan_product_id": item.get("mezan_product_id"),
            "salla_variant_id": item.get("salla_variant_id"),
            "variant_name": item.get("variant_name"),
            "product_name": item.get("product_name"),
            "sku": item.get("sku"),
            "quantity": payload.quantity,
            "preparation_state": PREPARATION_STATE_READY_COMPLETE,
            "specifications": item.get("specifications") or {},
            "configuration_key": item.get("configuration_key"),
            "lot_id": (
                f"stock-preparation:{order_id}:"
                f"{payload.item_id}:{receipt_id}"
            ),
            "source_type": "stock_preparation_order",
            "source_id": order_id,
            "source_line_id": payload.item_id,
            "supplier_id": order.get("supplier_id"),
            "retention_mode": "keep_in_inventory",
            "placed_at": now,
            "placed_by": actor_id,
        }
        try:
            await place_inventory_receipt(
                db,
                merchant_id=merchant_id,
                location_id=payload.location_id,
                receipt_id=receipt_id,
                inventory_item=inventory_item,
                quantity=payload.quantity,
                scanned_barcode=scanned_barcode,
                occurred_at=now,
            )
        except InventoryLocationCapacityError as exc:
            await db[INVENTORY_RECEIPTS].update_one(
                {"user_id": merchant_id, "id": receipt_id},
                {
                    "$set": {
                        "status": "failed",
                        "failure_code": (
                            "inventory_location_capacity_exceeded"
                        ),
                        "updated_at": _now(),
                    },
                },
            )
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "inventory_location_capacity_exceeded"
                },
            ) from exc

        posted_at = _now()
        await db[INVENTORY_RECEIPTS].update_one(
            {"user_id": merchant_id, "id": receipt_id},
            {
                "$set": {
                    **receipt,
                    "status": "posted",
                    "posted_at": posted_at,
                    "updated_at": posted_at,
                },
                "$unset": {"failure_code": ""},
            },
        )
        progressed = await _order_with_progress(
            db,
            merchant_id=merchant_id,
            order=order,
        )
        target_status = (
            STOCK_PREPARATION_STATUS_RECEIVED
            if progressed["retention_complete"]
            else STOCK_PREPARATION_STATUS_READY
        )
        history_event = {
            "action": "retain_prepared_inventory",
            "from_status": order.get("status"),
            "to_status": target_status,
            "actor_id": actor_id,
            "actor_name": user.get("name") or user.get("email"),
            "item_id": payload.item_id,
            "quantity": payload.quantity,
            "location_id": payload.location_id,
            "occurred_at": posted_at,
        }
        await db[STOCK_PREPARATION_ORDERS].update_one(
            {"id": order_id, "user_id": merchant_id},
            {
                "$set": {
                    "status": target_status,
                    "stage": target_status,
                    "received_quantity_snapshot": (
                        progressed["received_quantity"]
                    ),
                    "remaining_quantity_snapshot": (
                        progressed["remaining_quantity"]
                    ),
                    "updated_at": posted_at,
                    "updated_by": actor_id,
                    **(
                        {"received_at": posted_at}
                        if target_status
                        == STOCK_PREPARATION_STATUS_RECEIVED
                        else {}
                    ),
                },
                "$inc": {"revision": 1},
                "$push": {"history": history_event},
            },
        )
        updated = await db[STOCK_PREPARATION_ORDERS].find_one(
            {"id": order_id, "user_id": merchant_id},
            {"_id": 0},
        )
        updated_progress = await _order_with_progress(
            db,
            merchant_id=merchant_id,
            order=updated or order,
        )
        await db[EVENTS].insert_one({
            "id": uuid.uuid4().hex,
            "user_id": merchant_id,
            "event_type": (
                "stock_preparation_inventory_retained"
            ),
            "stock_preparation_order_id": order_id,
            "stock_preparation_item_id": payload.item_id,
            "receipt_id": receipt_id,
            "warehouse_id": warehouse_id,
            "location_id": payload.location_id,
            "product_id": item.get("mezan_product_id"),
            "quantity": payload.quantity,
            "preparation_state": (
                PREPARATION_STATE_READY_COMPLETE
            ),
            "retention_mode": "keep_in_inventory",
            "actor_id": actor_id,
            "occurred_at": posted_at,
        })
        return {
            "ok": True,
            "duplicate": False,
            "receipt": {
                key: value
                for key, value in {
                    **receipt,
                    "status": "posted",
                    "posted_at": posted_at,
                    "updated_at": posted_at,
                }.items()
                if key not in {
                    "_id",
                    "user_id",
                    "payload_fingerprint",
                }
            },
            "order": _public_order(updated_progress),
        }

    return router


__all__ = [
    "ACTIVE_STOCK_PREPARATION_STATUSES",
    "STOCK_PREPARATION_ORDERS",
    "STOCK_PREPARATION_RESPONSIBILITY",
    "StockPreparationActionRequest",
    "StockPreparationItemRequest",
    "StockPreparationOrderCreateRequest",
    "StockPreparationReceiptRequest",
    "apply_received_quantities",
    "ensure_stock_preparation_indexes",
    "make_stock_preparation_order_router",
    "next_stock_preparation_status",
    "stock_preparation_order_fingerprint",
    "stock_preparation_receipt_fingerprint",
]
