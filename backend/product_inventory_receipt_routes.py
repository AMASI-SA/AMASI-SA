"""Purchase receiving into configuration-aware branch inventory for Mezan V2."""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from pymongo import ASCENDING, DESCENDING
from pymongo.errors import DuplicateKeyError

from fulfillment_v2_routes import (
    INVENTORY_RESERVATIONS,
    _actor_context,
    _apply_inventory_reservations,
    _inventory_rows,
    _require_permission,
    _warehouse_allowed,
)
from inventory_receipt_service import (
    InventoryLocationCapacityError,
    place_inventory_receipt,
)
from product_fulfillment_rules import (
    DEFAULT_LOW_STOCK_THRESHOLD,
    INVENTORY_POLICY_BRANCH_STOCK,
    PRODUCT_OPERATION_PROFILES,
    STOCKOUT_POLICY_CLOSE,
    STOCKOUT_POLICY_PREORDER,
)
from product_inventory_rules import (
    PREPARATION_STATE_READY_COMPLETE,
    PREPARATION_STATE_REQUIRES_PREPARATION,
    build_inventory_configuration_key,
    canonical_specifications,
)
from product_v2_routes import PRODUCTS
from warehouse_location_routes import (
    CABINETS,
    EVENTS,
    LOCATIONS,
    WAREHOUSES,
)


PURCHASE_INVOICES = "purchase_invoices"
INVENTORY_RECEIPTS = "mezan_inventory_receipts_v2"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text(value: Any) -> str:
    return str(value or "").strip()


class InventorySpecification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=80)
    value: str = Field(min_length=1, max_length=240)


class PurchaseInventoryReceiptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idempotency_key: str = Field(min_length=8, max_length=120)
    purchase_invoice_id: str = Field(min_length=1, max_length=120)
    purchase_invoice_line_id: str = Field(min_length=1, max_length=120)
    product_id: str = Field(min_length=1, max_length=160)
    variant_id: str | None = Field(default=None, max_length=160)
    location_id: str = Field(min_length=1, max_length=120)
    scanned_barcode: str = Field(min_length=1, max_length=160)
    quantity: int = Field(ge=1, le=100000)
    preparation_state: Literal[
        "requires_preparation",
        "ready_complete",
    ]
    specifications: list[InventorySpecification] = Field(
        default_factory=list,
        max_length=30,
    )


def _receipt_fingerprint(payload: PurchaseInventoryReceiptRequest) -> str:
    value = {
        **payload.model_dump(),
        "idempotency_key": _text(payload.idempotency_key),
        "scanned_barcode": _text(payload.scanned_barcode).upper(),
        "specifications": canonical_specifications(
            [row.model_dump() for row in payload.specifications]
        ),
    }
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _public_receipt(receipt: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in receipt.items()
        if key not in {"_id", "payload_fingerprint", "user_id"}
    }


def build_inventory_health_rows(
    *,
    products: list[dict[str, Any]],
    profiles: list[dict[str, Any]],
    stock_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Summarize scoped branch stock after active/consumed commitments."""
    profiles_by_product = {
        _text(row.get("salla_product_id")): row
        for row in profiles
        if _text(row.get("salla_product_id"))
    }
    result = []
    for product in products:
        product_key = _text(
            product.get("salla_product_id")
            or product.get("mezan_product_id")
            or product.get("id")
        )
        profile = profiles_by_product.get(product_key)
        if (
            not profile
            or profile.get("inventory_policy")
            != INVENTORY_POLICY_BRANCH_STOCK
        ):
            continue
        identifiers = {
            _text(product.get("salla_product_id")),
            _text(product.get("mezan_product_id")),
            _text(product.get("sku")),
        }
        identifiers.discard("")
        matching = [
            row for row in stock_rows
            if not identifiers.isdisjoint(
                row.get("identifiers") or set()
            )
        ]
        received = sum(
            float(row.get("on_hand") or 0) for row in matching
        )
        reserved = sum(
            float(row.get("reserved_quantity") or 0)
            for row in matching
        )
        on_hand = received
        available = sum(
            float(row.get("remaining") or 0) for row in matching
        )
        try:
            threshold = int(
                profile.get(
                    "low_stock_threshold",
                    DEFAULT_LOW_STOCK_THRESHOLD,
                )
            )
        except (TypeError, ValueError, OverflowError):
            threshold = DEFAULT_LOW_STOCK_THRESHOLD
        threshold = min(100000, max(0, threshold))
        stockout_policy = (
            profile.get("stockout_policy")
            if profile.get("stockout_policy")
            in {STOCKOUT_POLICY_CLOSE, STOCKOUT_POLICY_PREORDER}
            else STOCKOUT_POLICY_CLOSE
        )
        if available <= 0:
            health_status = (
                "preorder"
                if stockout_policy == STOCKOUT_POLICY_PREORDER
                else "out_of_stock"
            )
            catalog_action = (
                "show_preorder"
                if stockout_policy == STOCKOUT_POLICY_PREORDER
                else "close_product"
            )
        elif available <= threshold:
            health_status = "low_stock"
            catalog_action = "replenish_stock"
        else:
            health_status = "healthy"
            catalog_action = None
        result.append({
            "mezan_product_id": product.get("mezan_product_id"),
            "salla_product_id": product.get("salla_product_id"),
            "name": product.get("name"),
            "sku": product.get("sku"),
            "main_image": product.get("main_image"),
            "received_quantity": received,
            "on_hand_quantity": on_hand,
            "reserved_quantity": reserved,
            "committed_quantity": reserved,
            "available_quantity": available,
            "low_stock_threshold": threshold,
            "stockout_policy": stockout_policy,
            "health_status": health_status,
            "catalog_action_required": catalog_action,
            "external_catalog_write_performed": False,
        })
    priority = {
        "out_of_stock": 0,
        "preorder": 1,
        "low_stock": 2,
        "healthy": 3,
    }
    return sorted(
        result,
        key=lambda row: (
            priority.get(row["health_status"], 9),
            float(row["available_quantity"]),
            _text(row.get("name")),
        ),
    )


async def ensure_inventory_receipt_indexes(db: Any) -> None:
    await db[INVENTORY_RECEIPTS].create_index(
        [("user_id", ASCENDING), ("idempotency_key", ASCENDING)],
        unique=True,
        name="uq_inventory_receipt_idempotency_v2",
    )
    await db[INVENTORY_RECEIPTS].create_index(
        [
            ("user_id", ASCENDING),
            ("purchase_invoice_id", ASCENDING),
            ("purchase_invoice_line_id", ASCENDING),
            ("status", ASCENDING),
        ],
        name="ix_inventory_receipt_purchase_line_v2",
    )
    await db[INVENTORY_RECEIPTS].create_index(
        [("user_id", ASCENDING), ("posted_at", DESCENDING)],
        name="ix_inventory_receipt_posted_v2",
    )


async def _purchase_catalog(
    db: Any,
    *,
    merchant_id: str,
) -> list[dict[str, Any]]:
    invoices = await db[PURCHASE_INVOICES].find(
        {"user_id": merchant_id},
        {
            "_id": 0,
            "id": 1,
            "invoice_number": 1,
            "invoice_date": 1,
            "supplier_name": 1,
            "lines": 1,
        },
    ).sort("invoice_date", -1).limit(300).to_list(length=300)
    receipts = await db[INVENTORY_RECEIPTS].find(
        {
            "user_id": merchant_id,
            "status": "posted",
            "purchase_invoice_id": {
                "$in": [row.get("id") for row in invoices]
            },
        },
        {
            "_id": 0,
            "purchase_invoice_id": 1,
            "purchase_invoice_line_id": 1,
            "quantity": 1,
        },
    ).to_list(length=20000)
    received: dict[tuple[str, str], int] = {}
    for receipt in receipts:
        key = (
            _text(receipt.get("purchase_invoice_id")),
            _text(receipt.get("purchase_invoice_line_id")),
        )
        received[key] = received.get(key, 0) + int(
            receipt.get("quantity") or 0
        )
    result = []
    for invoice in invoices:
        lines = []
        has_remaining = False
        for line in invoice.get("lines") or []:
            quantity = float(line.get("quantity") or 0)
            received_quantity = float(received.get(
                (_text(invoice.get("id")), _text(line.get("id"))),
                0,
            ))
            remaining_quantity = max(0.0, quantity - received_quantity)
            if remaining_quantity > 0:
                has_remaining = True
            lines.append({
                **line,
                "received_quantity": received_quantity,
                "remaining_quantity": remaining_quantity,
            })
        result.append({
            **invoice,
            "lines": lines,
            "has_remaining": has_remaining,
        })
    return result


def make_product_inventory_receipt_router(
    db: Any,
    current_user: Callable[..., Any],
) -> APIRouter:
    router = APIRouter(
        prefix="/inventory-v2",
        tags=["Mezan Product Inventory V2"],
    )

    @router.get("/purchase-receiving/catalog")
    async def receiving_catalog(
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        context = await _actor_context(db, user)
        _require_permission(context, "inventory.receipts.read")
        await ensure_inventory_receipt_indexes(db)
        merchant_id = context["merchant_id"]
        warehouse_query: dict[str, Any] = {
            "user_id": merchant_id,
            "status": {"$ne": "disabled"},
        }
        if not context["is_owner"]:
            warehouse_query["id"] = {
                "$in": sorted(context["warehouse_ids"])
            }
        warehouses = await db[WAREHOUSES].find(
            warehouse_query,
            {
                "_id": 0,
                "id": 1,
                "name": 1,
                "code": 1,
                "city": 1,
                "is_primary": 1,
            },
        ).sort([("is_primary", -1), ("created_at", 1)]).to_list(500)
        warehouse_ids = [row["id"] for row in warehouses]
        cabinets = await db[CABINETS].find(
            {
                "user_id": merchant_id,
                "warehouse_id": {"$in": warehouse_ids},
                "status": {"$ne": "disabled"},
            },
            {
                "_id": 0,
                "id": 1,
                "warehouse_id": 1,
                "name": 1,
                "code": 1,
            },
        ).to_list(length=5000)
        cabinet_map = {
            _text(row.get("id")): row for row in cabinets
        }
        locations = await db[LOCATIONS].find(
            {
                "user_id": merchant_id,
                "warehouse_id": {"$in": warehouse_ids},
                "state": {"$ne": "disabled"},
            },
            {
                "_id": 0,
                "id": 1,
                "warehouse_id": 1,
                "cabinet_id": 1,
                "code": 1,
                "barcode_value": 1,
                "state": 1,
                "max_items": 1,
                "occupancy.total_quantity": 1,
            },
        ).sort("code", 1).to_list(length=20000)
        locations = [{
            **row,
            "cabinet_name": (
                cabinet_map.get(_text(row.get("cabinet_id")), {}).get("name")
            ),
            "current_quantity": int(
                ((row.get("occupancy") or {}).get("total_quantity")) or 0
            ),
        } for row in locations]
        products = await db[PRODUCTS].find(
            {
                "user_id": merchant_id,
                "archived": {"$ne": True},
            },
            {
                "_id": 0,
                "mezan_product_id": 1,
                "salla_product_id": 1,
                "name": 1,
                "sku": 1,
                "barcode": 1,
                "main_image": 1,
                "status": 1,
                "options": 1,
                "options_count": 1,
                "custom_fields": 1,
                "details_loaded": 1,
                "variants": 1,
                "variants_count": 1,
            },
        ).sort("name", 1).to_list(length=10000)
        profiles = await db[PRODUCT_OPERATION_PROFILES].find(
            {
                "user_id": merchant_id,
                "inventory_policy": INVENTORY_POLICY_BRANCH_STOCK,
            },
            {"_id": 0},
        ).to_list(length=10000)
        inventory_locations = await db[LOCATIONS].find(
            {
                "user_id": merchant_id,
                "warehouse_id": {"$in": warehouse_ids},
                "state": {"$ne": "disabled"},
                "occupancy": {"$ne": None},
            },
            {
                "_id": 0,
                "id": 1,
                "code": 1,
                "warehouse_id": 1,
                "state": 1,
                "occupancy": 1,
            },
        ).to_list(length=20000)
        stock_rows = _inventory_rows(inventory_locations)
        reservations = await db[INVENTORY_RESERVATIONS].find(
            {
                "user_id": merchant_id,
                "status": "active",
            },
            {"_id": 0},
        ).to_list(length=50000)
        _apply_inventory_reservations(
            stock_rows,
            reservations,
            current_order_number="",
        )
        inventory_health = build_inventory_health_rows(
            products=products,
            profiles=profiles,
            stock_rows=stock_rows,
        )
        recent_receipts = await db[INVENTORY_RECEIPTS].find(
            {
                "user_id": merchant_id,
                "status": "posted",
            },
            {"_id": 0, "payload_fingerprint": 0, "user_id": 0},
        ).sort("posted_at", -1).limit(30).to_list(length=30)
        return {
            "ok": True,
            "purchase_invoices": await _purchase_catalog(
                db,
                merchant_id=merchant_id,
            ),
            "products": products,
            "warehouses": warehouses,
            "locations": locations,
            "recent_receipts": recent_receipts,
            "inventory_health": inventory_health,
            "inventory_alerts": [
                row for row in inventory_health
                if row["health_status"] != "healthy"
            ],
            "preparation_states": [
                {
                    "value": PREPARATION_STATE_REQUIRES_PREPARATION,
                    "label": "يحتاج تجهيز",
                },
                {
                    "value": PREPARATION_STATE_READY_COMPLETE,
                    "label": "جاهز كامل",
                },
            ],
        }

    @router.post("/purchase-receipts", status_code=201)
    async def receive_purchase(
        payload: PurchaseInventoryReceiptRequest,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        context = await _actor_context(db, user)
        _require_permission(context, "inventory.receipts.write")
        await ensure_inventory_receipt_indexes(db)
        merchant_id = context["merchant_id"]
        actor_id = context["actor_id"]
        idempotency_key = _text(payload.idempotency_key)
        fingerprint = _receipt_fingerprint(payload)

        existing = await db[INVENTORY_RECEIPTS].find_one(
            {
                "user_id": merchant_id,
                "idempotency_key": idempotency_key,
            },
            {"_id": 0},
        )
        if existing and existing.get("payload_fingerprint") != fingerprint:
            raise HTTPException(
                status_code=409,
                detail={"code": "inventory_receipt_idempotency_conflict"},
            )
        if existing and existing.get("status") == "posted":
            return {
                "ok": True,
                "duplicate": True,
                "receipt": _public_receipt(existing),
            }

        invoice = await db[PURCHASE_INVOICES].find_one(
            {
                "id": payload.purchase_invoice_id,
                "user_id": merchant_id,
            },
            {"_id": 0},
        )
        if not invoice:
            raise HTTPException(
                status_code=404,
                detail={"code": "purchase_invoice_not_found"},
            )
        invoice_line = next(
            (
                row for row in invoice.get("lines") or []
                if _text(row.get("id")) == payload.purchase_invoice_line_id
            ),
            None,
        )
        if not invoice_line:
            raise HTTPException(
                status_code=404,
                detail={"code": "purchase_invoice_line_not_found"},
            )
        product = await db[PRODUCTS].find_one(
            {
                "user_id": merchant_id,
                "$or": [
                    {"mezan_product_id": payload.product_id},
                    {"salla_product_id": payload.product_id},
                ],
                "archived": {"$ne": True},
            },
            {"_id": 0},
        )
        if not product:
            raise HTTPException(
                status_code=404,
                detail={"code": "mezan_product_not_found"},
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
                if _text(row.get("id")) == _text(payload.variant_id)
            ),
            None,
        )
        if variants and not _text(payload.variant_id):
            raise HTTPException(
                status_code=422,
                detail={"code": "inventory_variant_required"},
            )
        if _text(payload.variant_id) and not selected_variant:
            raise HTTPException(
                status_code=404,
                detail={"code": "inventory_variant_not_found"},
            )
        inventory_sku = _text(
            (selected_variant or {}).get("sku")
            or product.get("sku")
        )
        line_sku = _text(invoice_line.get("sku")).upper()
        product_sku = inventory_sku.upper()
        if line_sku and product_sku and line_sku != product_sku:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "purchase_line_product_sku_mismatch",
                    "invoice_sku": line_sku,
                    "product_sku": product_sku,
                },
            )
        location = await db[LOCATIONS].find_one(
            {
                "id": payload.location_id,
                "user_id": merchant_id,
            },
            {"_id": 0},
        )
        if not location:
            raise HTTPException(
                status_code=404,
                detail={"code": "inventory_location_not_found"},
            )
        warehouse_id = _text(location.get("warehouse_id"))
        if not _warehouse_allowed(context, [warehouse_id]):
            raise HTTPException(
                status_code=403,
                detail={"code": "inventory_warehouse_not_assigned"},
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

        invoice_quantity = float(invoice_line.get("quantity") or 0)
        specifications = canonical_specifications(
            [row.model_dump() for row in payload.specifications]
        )
        configuration_key = build_inventory_configuration_key(
            sku=inventory_sku or product.get("mezan_product_id"),
            preparation_state=payload.preparation_state,
            specifications=specifications,
        )
        now = _now()
        receipt_id = _text((existing or {}).get("id")) or uuid.uuid4().hex
        receipt = {
            "id": receipt_id,
            "user_id": merchant_id,
            "idempotency_key": idempotency_key,
            "payload_fingerprint": fingerprint,
            "status": "pending",
            "purchase_invoice_id": payload.purchase_invoice_id,
            "purchase_invoice_line_id": payload.purchase_invoice_line_id,
            "invoice_number": invoice.get("invoice_number"),
            "supplier_name": invoice.get("supplier_name"),
            "mezan_product_id": product.get("mezan_product_id"),
            "salla_product_id": product.get("salla_product_id"),
            "salla_variant_id": (
                _text((selected_variant or {}).get("id")) or None
            ),
            "variant_name": (
                (selected_variant or {}).get("display_name")
                or (selected_variant or {}).get("name")
            ),
            "product_name": product.get("name"),
            "sku": inventory_sku or None,
            "quantity": payload.quantity,
            "preparation_state": payload.preparation_state,
            "specifications": specifications,
            "configuration_key": configuration_key,
            "warehouse_id": warehouse_id,
            "location_id": payload.location_id,
            "location_code": expected_barcode,
            "received_by": actor_id,
            "created_at": (existing or {}).get("created_at") or now,
            "updated_at": now,
        }
        if not existing:
            try:
                await db[INVENTORY_RECEIPTS].insert_one(dict(receipt))
            except DuplicateKeyError:
                existing = await db[INVENTORY_RECEIPTS].find_one(
                    {
                        "user_id": merchant_id,
                        "idempotency_key": idempotency_key,
                    },
                    {"_id": 0},
                )
                if (
                    not existing
                    or existing.get("payload_fingerprint") != fingerprint
                ):
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "code": (
                                "inventory_receipt_idempotency_conflict"
                            )
                        },
                    )
                if existing.get("status") == "posted":
                    return {
                        "ok": True,
                        "duplicate": True,
                        "receipt": _public_receipt(existing),
                    }
                receipt_id = _text(existing.get("id"))
                receipt = {**existing, **receipt, "id": receipt_id}

        await db[INVENTORY_RECEIPTS].update_one(
            {"user_id": merchant_id, "id": receipt_id},
            {
                "$set": {
                    **receipt,
                    "status": "pending",
                    "updated_at": _now(),
                },
                "$unset": {"failure_code": ""},
            },
        )
        active_receipts = await db[INVENTORY_RECEIPTS].find(
            {
                "user_id": merchant_id,
                "purchase_invoice_id": payload.purchase_invoice_id,
                "purchase_invoice_line_id": (
                    payload.purchase_invoice_line_id
                ),
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
            if accepted_quantity + pending_quantity > invoice_quantity:
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
                            "purchase_invoice_quantity_exceeded"
                        ),
                        "updated_at": _now(),
                    },
                },
            )
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "purchase_invoice_quantity_exceeded",
                    "remaining_quantity": max(
                        0,
                        invoice_quantity - posted_quantity,
                    ),
                },
            )

        inventory_item = {
            "receipt_id": receipt_id,
            "product_id": (
                product.get("salla_product_id")
                or product.get("mezan_product_id")
            ),
            "mezan_product_id": product.get("mezan_product_id"),
            "salla_variant_id": (
                _text((selected_variant or {}).get("id")) or None
            ),
            "variant_name": (
                (selected_variant or {}).get("display_name")
                or (selected_variant or {}).get("name")
            ),
            "product_name": product.get("name"),
            "sku": inventory_sku or None,
            "quantity": payload.quantity,
            "preparation_state": payload.preparation_state,
            "specifications": specifications,
            "configuration_key": configuration_key,
            "lot_id": (
                f"purchase:{payload.purchase_invoice_id}:"
                f"{payload.purchase_invoice_line_id}:{receipt_id}"
            ),
            "source_type": "purchase_invoice",
            "source_id": payload.purchase_invoice_id,
            "source_line_id": payload.purchase_invoice_line_id,
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
                {
                    "user_id": merchant_id,
                    "id": receipt_id,
                },
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
        await db[EVENTS].insert_one({
            "id": uuid.uuid4().hex,
            "user_id": merchant_id,
            "event_type": "purchase_inventory_received_v2",
            "receipt_id": receipt_id,
            "purchase_invoice_id": payload.purchase_invoice_id,
            "purchase_invoice_line_id": payload.purchase_invoice_line_id,
            "warehouse_id": warehouse_id,
            "location_id": payload.location_id,
            "product_id": product.get("mezan_product_id"),
            "salla_variant_id": (
                _text((selected_variant or {}).get("id")) or None
            ),
            "quantity": payload.quantity,
            "preparation_state": payload.preparation_state,
            "configuration_key": configuration_key,
            "actor_id": actor_id,
            "occurred_at": posted_at,
        })
        receipt.update({
            "status": "posted",
            "posted_at": posted_at,
            "updated_at": posted_at,
        })
        return {
            "ok": True,
            "duplicate": False,
            "receipt": _public_receipt(receipt),
        }

    return router


__all__ = [
    "INVENTORY_RECEIPTS",
    "InventorySpecification",
    "PurchaseInventoryReceiptRequest",
    "build_inventory_health_rows",
    "ensure_inventory_receipt_indexes",
    "make_product_inventory_receipt_router",
]
