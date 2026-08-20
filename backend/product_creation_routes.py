"""Governed product creation from Mezan OS to Salla.

This workflow is deliberately one-way and creation-only:

Mezan draft -> preview -> human approval -> explicit publish -> verification.

It never imports a product, updates an existing Salla product, or invokes
Salla's bulk import endpoint.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, model_validator
from pymongo import ASCENDING, DESCENDING, ReturnDocument

from ai_store_access_control import effective_permissions
from ai_store_access_contract import find_role_assignment
from product_fulfillment_rules import (
    DEFAULT_LOW_STOCK_THRESHOLD,
    FROZEN_FULFILLMENT_TYPE,
    FROZEN_INVENTORY_POLICY,
    FULFILLMENT_TYPES,
    INVENTORY_POLICIES,
    PRODUCT_OPERATION_CHOICES_FROZEN,
    PRODUCT_OPERATION_PROFILES,
    STOCKOUT_POLICY_CLOSE,
    inventory_policy_details,
    inventory_policy_for_fulfillment,
    normalize_low_stock_threshold,
    normalize_inventory_policy,
    normalize_stockout_policy,
    normalize_fulfillment_type,
)
from product_fulfillment_routes import ensure_product_fulfillment_indexes
from product_v2_routes import (
    PRODUCTS,
    _text,
    ensure_product_v2_indexes,
    normalize_salla_product,
)
from salla_integration.service import SallaError, call_salla


DRAFTS = "mezan_product_creation_drafts_v2"
EVENTS = "mezan_product_creation_events_v2"
PUBLISH_CONFIRMATION = "إنشاء المنتج في سلة"
REQUIRED_SALLA_SCOPE = "products.read_write"
EDITABLE_STATUSES = {"draft", "approved"}
ALLOWED_PRODUCT_TYPES = {"product"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _serialize(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return row
    result = dict(row)
    result.pop("_id", None)
    for key, value in list(result.items()):
        if hasattr(value, "isoformat"):
            result[key] = value.isoformat()
    return result


def _normalized_sku(value: Any) -> str:
    return _text(value).upper()


def _safe_sku(value: Any) -> str:
    sku = _normalized_sku(value)
    if not sku:
        raise ValueError("sku_required")
    if len(sku) > 100:
        raise ValueError("sku_too_long")
    if not re.fullmatch(r"[A-Z0-9._\-/]+", sku):
        raise ValueError("sku_invalid_characters")
    return sku


class ProductCreationDraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=2, max_length=200)
    sku: str = Field(min_length=1, max_length=100)
    price: float = Field(ge=0, le=100000000)
    description: str | None = Field(default=None, max_length=50000)
    product_type: str = Field(default="product", max_length=40)
    category_ids: list[int] = Field(default_factory=list, max_length=50)
    image_urls: list[str] = Field(default_factory=list, max_length=20)
    fulfillment_type: str = Field(
        default=FROZEN_FULFILLMENT_TYPE,
        max_length=40,
    )
    inventory_policy: str = Field(
        default=FROZEN_INVENTORY_POLICY,
        max_length=60,
    )
    stockout_policy: str = Field(
        default=STOCKOUT_POLICY_CLOSE,
        max_length=60,
    )
    low_stock_threshold: int = Field(
        default=DEFAULT_LOW_STOCK_THRESHOLD,
        ge=0,
        le=100000,
    )

    @model_validator(mode="before")
    @classmethod
    def discard_legacy_product_inventory_fields(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        sanitized = dict(value)
        sanitized.pop("warehouse_id", None)
        sanitized.pop("quantity", None)
        return sanitized


def normalize_creation_input(payload: ProductCreationDraftRequest) -> dict[str, Any]:
    name = _text(payload.name)
    if len(name) < 2:
        raise ValueError("name_required")
    sku = _safe_sku(payload.sku)
    product_type = _text(payload.product_type).casefold()
    if product_type not in ALLOWED_PRODUCT_TYPES:
        raise ValueError("unsupported_product_type")
    # Product operation choices are temporarily frozen. Inputs are accepted
    # for compatibility with older clients, but cannot alter effective rules.
    if PRODUCT_OPERATION_CHOICES_FROZEN:
        fulfillment_type = FROZEN_FULFILLMENT_TYPE
        inventory_policy = FROZEN_INVENTORY_POLICY
        stockout_policy = STOCKOUT_POLICY_CLOSE
        low_stock_threshold = DEFAULT_LOW_STOCK_THRESHOLD
    else:
        fulfillment_type = normalize_fulfillment_type(
            payload.fulfillment_type
        )
        inventory_policy = normalize_inventory_policy(
            payload.inventory_policy
        )
        stockout_policy = normalize_stockout_policy(
            payload.stockout_policy
        )
        low_stock_threshold = normalize_low_stock_threshold(
            payload.low_stock_threshold
        )
    image_urls: list[str] = []
    for value in payload.image_urls:
        url = _text(value)
        if not url:
            continue
        if not re.match(r"^https://", url, flags=re.IGNORECASE):
            raise ValueError("image_url_must_use_https")
        image_urls.append(url)
    return {
        "name": name,
        "sku": sku,
        "price": float(payload.price),
        "description": _text(payload.description) or None,
        "product_type": product_type,
        "category_ids": list(dict.fromkeys(payload.category_ids)),
        "image_urls": list(dict.fromkeys(image_urls)),
        "fulfillment_type": fulfillment_type,
        "inventory_policy": inventory_policy,
        "stockout_policy": stockout_policy,
        "low_stock_threshold": low_stock_threshold,
    }


def _serialize_draft(row: dict[str, Any] | None) -> dict[str, Any] | None:
    result = _serialize(row)
    if result:
        result.pop("warehouse_id", None)
        result.pop("quantity", None)
        if PRODUCT_OPERATION_CHOICES_FROZEN:
            result.update({
                "fulfillment_type": FROZEN_FULFILLMENT_TYPE,
                "inventory_policy": FROZEN_INVENTORY_POLICY,
                "stockout_policy": STOCKOUT_POLICY_CLOSE,
                "low_stock_threshold": DEFAULT_LOW_STOCK_THRESHOLD,
                "operation_choices_frozen": True,
            })
        else:
            if (
                result.get("inventory_policy") not in INVENTORY_POLICIES
                and result.get("fulfillment_type") in FULFILLMENT_TYPES
            ):
                result["inventory_policy"] = (
                    inventory_policy_for_fulfillment(
                        result["fulfillment_type"]
                    )["mode"]
                )
                result["inventory_policy_inferred_from_legacy"] = True
            if not result.get("stockout_policy"):
                result["stockout_policy"] = STOCKOUT_POLICY_CLOSE
                result["stockout_policy_inferred_from_legacy"] = True
            try:
                result["low_stock_threshold"] = (
                    normalize_low_stock_threshold(
                        result.get(
                            "low_stock_threshold",
                            DEFAULT_LOW_STOCK_THRESHOLD,
                        )
                    )
                )
            except ValueError:
                result["low_stock_threshold"] = (
                    DEFAULT_LOW_STOCK_THRESHOLD
                )
    return result


def _draft_inventory_policy(draft: dict[str, Any]) -> dict[str, Any]:
    if PRODUCT_OPERATION_CHOICES_FROZEN:
        return inventory_policy_details(FROZEN_INVENTORY_POLICY)
    value = draft.get("inventory_policy")
    if value in INVENTORY_POLICIES:
        return inventory_policy_details(value)
    return inventory_policy_for_fulfillment(draft["fulfillment_type"])


def build_salla_product_payload(draft: dict[str, Any]) -> dict[str, Any]:
    """Build the bounded payload accepted by Salla's create-product API."""
    inventory_policy = _draft_inventory_policy(draft)
    payload: dict[str, Any] = {
        "name": draft["name"],
        "sku": draft["sku"],
        "price": float(draft["price"]),
        "product_type": draft.get("product_type") or "product",
        "status": inventory_policy["initial_salla_status"],
        "require_shipping": True,
    }
    if draft.get("description"):
        payload["description"] = draft["description"]
    if draft.get("category_ids"):
        payload["categories"] = list(draft["category_ids"])
    if draft.get("image_urls"):
        payload["images"] = [
            {
                "original": url,
                "thumbnail": url,
                "alt": draft["name"],
                "default": index == 0,
                "sort": index + 1,
            }
            for index, url in enumerate(draft["image_urls"])
        ]
    return payload


async def _apply_salla_inventory_policy(
    db: Any,
    *,
    merchant_id: str,
    salla_product_id: str,
    inventory_policy: str,
) -> dict[str, Any]:
    """Apply the documented zero-stock policy after product creation.

    Tracked products remain out of stock until branch inventory is entered.
    Products explicitly configured not to track finished goods are switched
    to unlimited quantity. Preparation services do not change either policy.
    """
    policy = inventory_policy_details(inventory_policy)
    if not policy["unlimited_quantity"]:
        return {
            **policy,
            "external_update_required": False,
            "external_update_queued": False,
        }
    await call_salla(
        db,
        merchant_id,
        "POST",
        "/products/quantities/bulk",
        json={
            "products": [{
                "identifer_type": "id",
                "identifer": salla_product_id,
                "quantity": 0,
                "mode": "overwrite",
                "unlimited_quantity": True,
            }],
        },
    )
    return {
        **policy,
        "external_update_required": True,
        "external_update_queued": True,
    }


async def ensure_product_creation_indexes(db: Any) -> None:
    await db[DRAFTS].create_index(
        [("user_id", ASCENDING), ("sku", ASCENDING)],
        unique=True,
        name="uq_product_creation_draft_sku_v2",
    )
    await db[DRAFTS].create_index(
        [("user_id", ASCENDING), ("updated_at", DESCENDING)],
        name="ix_product_creation_drafts_v2",
    )
    await db[EVENTS].create_index(
        [
            ("user_id", ASCENDING),
            ("draft_id", ASCENDING),
            ("occurred_at", DESCENDING),
        ],
        name="ix_product_creation_events_v2",
    )


async def _actor_context(db: Any, user: dict[str, Any]) -> dict[str, Any]:
    actor_id = _text(user.get("id"))
    if _text(user.get("role")).casefold() == "owner" or user.get("is_owner"):
        return {
            "actor_id": actor_id,
            "merchant_id": actor_id,
            "permissions": {"products.create", "products.publish"},
            "is_owner": True,
        }
    merchant_id = _text(user.get("created_by"))
    assignment = await find_role_assignment(
        db,
        owner_user_id=merchant_id,
        user_id=actor_id,
    )
    return {
        "actor_id": actor_id,
        "merchant_id": merchant_id,
        "permissions": set(effective_permissions(assignment)),
        "is_owner": False,
    }


def _require_permission(context: dict[str, Any], permission: str) -> None:
    if not context.get("merchant_id"):
        raise HTTPException(
            status_code=409,
            detail={"code": "employee_store_not_linked"},
        )
    if permission not in context["permissions"]:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "product_creation_permission_required",
                "permission": permission,
            },
        )


async def _event(
    db: Any,
    *,
    context: dict[str, Any],
    draft_id: str,
    event_type: str,
    details: dict[str, Any] | None = None,
) -> None:
    await db[EVENTS].insert_one({
        "id": uuid.uuid4().hex,
        "user_id": context["merchant_id"],
        "draft_id": draft_id,
        "actor_id": context["actor_id"],
        "event_type": event_type,
        "details": details or {},
        "occurred_at": _now(),
    })


async def _draft(
    db: Any,
    *,
    merchant_id: str,
    draft_id: str,
) -> dict[str, Any]:
    row = await db[DRAFTS].find_one(
        {"user_id": merchant_id, "id": draft_id},
        {"_id": 0},
    )
    if not row:
        raise HTTPException(
            status_code=404,
            detail={"code": "product_creation_draft_not_found"},
        )
    return row


async def _validate_unique_sku(
    db: Any,
    *,
    merchant_id: str,
    sku: str,
    draft_id: str | None = None,
) -> None:
    draft_query: dict[str, Any] = {"user_id": merchant_id, "sku": sku}
    if draft_id:
        draft_query["id"] = {"$ne": draft_id}
    existing_draft = await db[DRAFTS].find_one(
        draft_query,
        {"_id": 0, "id": 1, "status": 1},
    )
    existing_product = await db[PRODUCTS].find_one(
        {"user_id": merchant_id, "sku": sku, "archived": {"$ne": True}},
        {"_id": 0, "salla_product_id": 1},
    )
    if existing_draft or existing_product:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "product_sku_already_used",
                "draft_id": (existing_draft or {}).get("id"),
                "salla_product_id": (existing_product or {}).get(
                    "salla_product_id"
                ),
            },
        )


async def _require_salla_scope(db: Any, merchant_id: str) -> None:
    integration = await db.salla_integrations.find_one(
        {"user_id": merchant_id},
        {"_id": 0, "status": 1, "scope": 1},
    )
    if not integration or integration.get("status") != "connected":
        raise HTTPException(
            status_code=409,
            detail={"code": "salla_not_connected"},
        )
    scopes = set(_text(integration.get("scope")).split())
    if REQUIRED_SALLA_SCOPE not in scopes:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "salla_product_write_scope_required",
                "required_scope": REQUIRED_SALLA_SCOPE,
                "reconnect_required": True,
            },
        )


async def _salla_product_by_sku(
    db: Any,
    *,
    merchant_id: str,
    sku: str,
) -> dict[str, Any] | None:
    try:
        response = await call_salla(
            db,
            merchant_id,
            "GET",
            f"/products/sku/{quote(sku, safe='')}",
        )
    except SallaError as exc:
        if exc.status_code == 404:
            return None
        raise
    raw = response.get("data") if isinstance(response, dict) else None
    return raw if isinstance(raw, dict) else None


async def _save_created_product(
    db: Any,
    *,
    merchant_id: str,
    draft: dict[str, Any],
    raw: dict[str, Any],
    actor_id: str,
) -> dict[str, Any]:
    now = _now()
    normalized_raw = dict(raw)
    if not _text(normalized_raw.get("name")):
        normalized_raw["name"] = draft["name"]
    if not _text(normalized_raw.get("sku")):
        normalized_raw["sku"] = draft["sku"]
    if not _text(normalized_raw.get("description")) and draft.get(
        "description"
    ):
        normalized_raw["description"] = draft["description"]
    if not _text(
        normalized_raw.get("type")
        or normalized_raw.get("product_type")
    ):
        normalized_raw["product_type"] = draft.get("product_type") or "product"
    if normalized_raw.get("price") in (None, ""):
        normalized_raw["price"] = draft["price"]
    product = normalize_salla_product(
        normalized_raw,
        user_id=merchant_id,
        synced_at=now,
    )
    await ensure_product_v2_indexes(db)
    await db[PRODUCTS].update_one(
        {
            "user_id": merchant_id,
            "salla_product_id": product["salla_product_id"],
        },
        {
            "$set": {
                **product,
                "source": "mezan_created_in_salla",
                "creation_request_id": draft["creation_request_id"],
                "created_from_mezan_draft_id": draft["id"],
                "updated_at": now,
            },
            "$setOnInsert": {
                "id": uuid.uuid4().hex,
                "created_at": now,
            },
        },
        upsert=True,
    )
    await ensure_product_fulfillment_indexes(db)
    await db[PRODUCT_OPERATION_PROFILES].update_one(
        {
            "user_id": merchant_id,
            "salla_product_id": product["salla_product_id"],
        },
        {
            "$set": {
                "user_id": merchant_id,
                "salla_product_id": product["salla_product_id"],
                "mezan_product_id": product["mezan_product_id"],
                "fulfillment_type": (
                    FROZEN_FULFILLMENT_TYPE
                    if PRODUCT_OPERATION_CHOICES_FROZEN
                    else draft["fulfillment_type"]
                ),
                "inventory_policy": _draft_inventory_policy(draft)["mode"],
                "stockout_policy": (
                    STOCKOUT_POLICY_CLOSE
                    if PRODUCT_OPERATION_CHOICES_FROZEN
                    else draft.get(
                        "stockout_policy",
                        STOCKOUT_POLICY_CLOSE,
                    )
                ),
                "low_stock_threshold": (
                    DEFAULT_LOW_STOCK_THRESHOLD
                    if PRODUCT_OPERATION_CHOICES_FROZEN
                    else draft.get(
                        "low_stock_threshold",
                        DEFAULT_LOW_STOCK_THRESHOLD,
                    )
                ),
                "configured": True,
                "updated_at": now,
                "updated_by": actor_id,
            },
            "$unset": {"warehouse_id": ""},
            "$setOnInsert": {
                "id": uuid.uuid4().hex,
                "created_at": now,
            },
        },
        upsert=True,
    )
    return product


def make_product_creation_router(
    db: Any,
    current_user: Callable[..., Any],
) -> APIRouter:
    router = APIRouter(
        prefix="/products-v2/creation-drafts",
        tags=["Product V2 Creation"],
    )

    @router.get("")
    async def list_creation_drafts(
        status: str | None = Query(default=None, max_length=40),
        limit: int = Query(default=100, ge=1, le=500),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        await ensure_product_creation_indexes(db)
        context = await _actor_context(db, user)
        _require_permission(context, "products.create")
        query: dict[str, Any] = {"user_id": context["merchant_id"]}
        if status:
            query["status"] = status
        rows = await db[DRAFTS].find(
            query,
            {"_id": 0, "salla_response": 0},
        ).sort("updated_at", -1).limit(limit).to_list(limit)
        return {
            "ok": True,
            "items": [_serialize_draft(row) for row in rows],
            "rules": {
                "direction": "mezan_to_salla",
                "mode": "create_only",
                "bulk_import_used": False,
                "publish_confirmation": PUBLISH_CONFIRMATION,
                "product_type_immutable_after_creation": True,
                "product_catalog_scope": "store",
                "inventory_scope": "branch_location",
                "warehouse_resolution": [
                    "inventory_location",
                ],
                "employee_assignment_role": "visibility_and_permissions_only",
                "inventory_and_preparation_are_independent": True,
                "product_service_applies_to_every_order": True,
                "allowed_inventory_policies": sorted(INVENTORY_POLICIES),
            },
        }

    @router.post("")
    async def create_draft(
        payload: ProductCreationDraftRequest,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        await ensure_product_creation_indexes(db)
        context = await _actor_context(db, user)
        _require_permission(context, "products.create")
        try:
            values = normalize_creation_input(payload)
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": str(exc),
                    "allowed_product_types": sorted(ALLOWED_PRODUCT_TYPES),
                    "allowed_fulfillment_types": sorted(FULFILLMENT_TYPES),
                    "allowed_inventory_policies": sorted(INVENTORY_POLICIES),
                },
            ) from exc
        await _validate_unique_sku(
            db,
            merchant_id=context["merchant_id"],
            sku=values["sku"],
        )
        now = _now()
        row = {
            "id": uuid.uuid4().hex,
            "creation_request_id": uuid.uuid4().hex,
            "user_id": context["merchant_id"],
            "status": "draft",
            **values,
            "created_at": now,
            "updated_at": now,
            "created_by": context["actor_id"],
            "updated_by": context["actor_id"],
        }
        await db[DRAFTS].insert_one(row)
        await _event(
            db,
            context=context,
            draft_id=row["id"],
            event_type="product_creation_draft_created",
        )
        return {"ok": True, "draft": _serialize_draft(row)}

    @router.get("/{draft_id}")
    async def get_creation_draft(
        draft_id: str,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        context = await _actor_context(db, user)
        _require_permission(context, "products.create")
        row = await _draft(
            db,
            merchant_id=context["merchant_id"],
            draft_id=draft_id,
        )
        return {
            "ok": True,
            "draft": _serialize_draft(row),
            "preview": build_salla_product_payload(row),
        }

    @router.put("/{draft_id}")
    async def update_draft(
        draft_id: str,
        payload: ProductCreationDraftRequest,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        context = await _actor_context(db, user)
        _require_permission(context, "products.create")
        current = await _draft(
            db,
            merchant_id=context["merchant_id"],
            draft_id=draft_id,
        )
        if current.get("status") not in EDITABLE_STATUSES:
            raise HTTPException(
                status_code=409,
                detail={"code": "product_creation_draft_not_editable"},
            )
        try:
            values = normalize_creation_input(payload)
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail={"code": str(exc)},
            ) from exc
        await _validate_unique_sku(
            db,
            merchant_id=context["merchant_id"],
            sku=values["sku"],
            draft_id=draft_id,
        )
        now = _now()
        updated = await db[DRAFTS].find_one_and_update(
            {"user_id": context["merchant_id"], "id": draft_id},
            {
                "$set": {
                    **values,
                    "status": "draft",
                    "approved_at": None,
                    "approved_by": None,
                    "updated_at": now,
                    "updated_by": context["actor_id"],
                },
                "$unset": {
                    "warehouse_id": "",
                    "quantity": "",
                },
            },
            return_document=ReturnDocument.AFTER,
        )
        await _event(
            db,
            context=context,
            draft_id=draft_id,
            event_type="product_creation_draft_updated",
        )
        return {"ok": True, "draft": _serialize_draft(updated)}

    @router.post("/{draft_id}/preview")
    async def preview_draft(
        draft_id: str,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        context = await _actor_context(db, user)
        _require_permission(context, "products.create")
        row = await _draft(
            db,
            merchant_id=context["merchant_id"],
            draft_id=draft_id,
        )
        return {
            "ok": True,
            "draft_id": draft_id,
            "salla_payload": build_salla_product_payload(row),
            "validation": {
                "valid": True,
                "sku_idempotency": True,
                "product_type_locked_after_publish": True,
                "inventory_policy": _draft_inventory_policy(row),
                "inventory_and_preparation_are_independent": True,
                "image_required_for_salla_visibility": True,
            },
            "external_calls_made": False,
            "writes_made": False,
        }

    @router.post("/{draft_id}/approve")
    async def approve_draft(
        draft_id: str,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        context = await _actor_context(db, user)
        _require_permission(context, "products.publish")
        row = await db[DRAFTS].find_one_and_update(
            {
                "user_id": context["merchant_id"],
                "id": draft_id,
                "status": "draft",
            },
            {"$set": {
                "status": "approved",
                "approved_at": _now(),
                "approved_by": context["actor_id"],
                "updated_at": _now(),
            }},
            return_document=ReturnDocument.AFTER,
        )
        if not row:
            raise HTTPException(
                status_code=409,
                detail={"code": "product_creation_draft_not_approvable"},
            )
        await _event(
            db,
            context=context,
            draft_id=draft_id,
            event_type="product_creation_draft_approved",
        )
        return {"ok": True, "draft": _serialize_draft(row)}

    @router.post("/{draft_id}/publish")
    async def publish_draft(
        draft_id: str,
        payload: dict = Body(default={}),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        context = await _actor_context(db, user)
        _require_permission(context, "products.publish")
        if payload.get("confirmation") != PUBLISH_CONFIRMATION:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "product_creation_confirmation_required",
                    "confirmation": PUBLISH_CONFIRMATION,
                },
            )
        await _require_salla_scope(db, context["merchant_id"])
        current = await _draft(
            db,
            merchant_id=context["merchant_id"],
            draft_id=draft_id,
        )
        if current.get("status") in {"published", "published_unverified"}:
            return {
                "ok": True,
                "idempotent": True,
                "draft": _serialize_draft(current),
                "salla_product_id": current.get("salla_product_id"),
            }
        if current.get("status") not in {"approved", "publish_unknown"}:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "product_creation_draft_not_approved",
                    "status": current.get("status"),
                },
            )
        previous_status = current["status"]
        locked = await db[DRAFTS].find_one_and_update(
            {
                "user_id": context["merchant_id"],
                "id": draft_id,
                "status": previous_status,
            },
            {"$set": {
                "status": "publishing",
                "publish_started_at": _now(),
                "publish_started_by": context["actor_id"],
                "updated_at": _now(),
            }},
            return_document=ReturnDocument.AFTER,
        )
        if not locked:
            raise HTTPException(
                status_code=409,
                detail={"code": "product_creation_publish_in_progress"},
            )

        try:
            remote_existing = await _salla_product_by_sku(
                db,
                merchant_id=context["merchant_id"],
                sku=locked["sku"],
            )
        except SallaError as exc:
            await db[DRAFTS].update_one(
                {"user_id": context["merchant_id"], "id": draft_id},
                {"$set": {
                    "status": previous_status,
                    "updated_at": _now(),
                    "last_error": str(exc)[:500],
                }},
            )
            raise HTTPException(
                status_code=exc.status_code if exc.status_code != 200 else 400,
                detail={
                    "code": "salla_sku_reconciliation_failed",
                    "message": str(exc),
                    "needs_reauth": exc.needs_reauth,
                },
            ) from exc

        if remote_existing and previous_status != "publish_unknown":
            await db[DRAFTS].update_one(
                {"user_id": context["merchant_id"], "id": draft_id},
                {"$set": {"status": "approved", "updated_at": _now()}},
            )
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "sku_exists_in_salla",
                    "salla_product_id": _text(remote_existing.get("id")),
                },
            )

        raw_created: dict[str, Any] | None = remote_existing
        reconciled = bool(remote_existing)
        if not raw_created:
            try:
                response = await call_salla(
                    db,
                    context["merchant_id"],
                    "POST",
                    "/products",
                    json=build_salla_product_payload(locked),
                )
                raw = response.get("data") if isinstance(response, dict) else None
                if not isinstance(raw, dict) or not _text(raw.get("id")):
                    raise RuntimeError("salla_create_response_missing_product")
                raw_created = raw
            except SallaError as exc:
                ambiguous = exc.status_code >= 500 or exc.status_code == 429
                await db[DRAFTS].update_one(
                    {"user_id": context["merchant_id"], "id": draft_id},
                    {"$set": {
                        "status": (
                            "publish_unknown" if ambiguous else "approved"
                        ),
                        "updated_at": _now(),
                        "last_error": str(exc)[:500],
                    }},
                )
                raise HTTPException(
                    status_code=exc.status_code if exc.status_code != 200 else 400,
                    detail={
                        "code": (
                            "salla_product_creation_uncertain"
                            if ambiguous
                            else "salla_product_creation_failed"
                        ),
                        "message": str(exc),
                        "safe_to_retry": not ambiguous,
                        "reconciliation_required": ambiguous,
                        "needs_reauth": exc.needs_reauth,
                    },
                ) from exc
            except (httpx.TimeoutException, httpx.NetworkError, RuntimeError) as exc:
                await db[DRAFTS].update_one(
                    {"user_id": context["merchant_id"], "id": draft_id},
                    {"$set": {
                        "status": "publish_unknown",
                        "updated_at": _now(),
                        "last_error": str(exc)[:500],
                    }},
                )
                raise HTTPException(
                    status_code=503,
                    detail={
                        "code": "salla_product_creation_uncertain",
                        "safe_to_retry": False,
                        "reconciliation_required": True,
                    },
                ) from exc

        salla_id = _text(raw_created.get("id"))
        try:
            inventory_policy = await _apply_salla_inventory_policy(
                db,
                merchant_id=context["merchant_id"],
                salla_product_id=salla_id,
                inventory_policy=_draft_inventory_policy(locked)["mode"],
            )
        except (SallaError, httpx.TimeoutException, httpx.NetworkError) as exc:
            await db[DRAFTS].update_one(
                {"user_id": context["merchant_id"], "id": draft_id},
                {"$set": {
                    "status": "publish_unknown",
                    "salla_product_id": salla_id,
                    "updated_at": _now(),
                    "last_error": str(exc)[:500],
                }},
            )
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "salla_product_inventory_policy_uncertain",
                    "safe_to_retry": False,
                    "reconciliation_required": True,
                    "product_created": True,
                    "salla_product_id": salla_id,
                },
            ) from exc
        verified = False
        verify_error = None
        try:
            verification = await call_salla(
                db,
                context["merchant_id"],
                "GET",
                f"/products/{quote(salla_id, safe='')}",
            )
            verified_raw = (
                verification.get("data")
                if isinstance(verification, dict)
                else None
            )
            if isinstance(verified_raw, dict) and _text(verified_raw.get("id")):
                raw_created = verified_raw
                verified = True
        except (SallaError, httpx.TimeoutException, httpx.NetworkError) as exc:
            verify_error = str(exc)[:500]

        product = await _save_created_product(
            db,
            merchant_id=context["merchant_id"],
            draft=locked,
            raw=raw_created,
            actor_id=context["actor_id"],
        )
        completed_at = _now()
        final_status = "published" if verified else "published_unverified"
        await db[DRAFTS].update_one(
            {"user_id": context["merchant_id"], "id": draft_id},
            {"$set": {
                "status": final_status,
                "salla_product_id": salla_id,
                "mezan_product_id": product["mezan_product_id"],
                "published_at": completed_at,
                "updated_at": completed_at,
                "published_by": context["actor_id"],
                "verified": verified,
                "verify_error": verify_error,
                "reconciled_after_unknown": reconciled,
                "inventory_policy": inventory_policy,
            }},
        )
        await _event(
            db,
            context=context,
            draft_id=draft_id,
            event_type="product_created_in_salla",
            details={
                "salla_product_id": salla_id,
                "verified": verified,
                "reconciled_after_unknown": reconciled,
                "inventory_policy": inventory_policy,
            },
        )
        saved = await _draft(
            db,
            merchant_id=context["merchant_id"],
            draft_id=draft_id,
        )
        return {
            "ok": True,
            "draft": _serialize_draft(saved),
            "product": _serialize(product),
            "salla_product_id": salla_id,
            "verified": verified,
            "reconciled_after_unknown": reconciled,
            "inventory_policy": inventory_policy,
            "bulk_import_used": False,
        }

    return router
