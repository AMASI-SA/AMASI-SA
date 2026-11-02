"""Standalone Mezan operational-item images, isolated from source product media.

Never write to Salla's product gallery, normal Mezan product image preferences,
or the normal order_review_mezan_images collection. Images are order-owned
snapshots; an exact product + operational name + non-personal option signature
may be reused on a later order, without copying customer-specific inscriptions.
"""
from __future__ import annotations

import base64
import hashlib
import json
import uuid
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field

IMAGES = "order_review_operational_images"
PREFERENCES = "product_operational_image_preferences"
IMAGE_PREFIX = "/api/order-reviews-v1/operational-images/"
MAX_IMAGE_BYTES = 5 * 1024 * 1024
IMAGE_TYPES = {
    "image/jpeg": b"\xff\xd8\xff",
    "image/png": b"\x89PNG\r\n\x1a\n",
    "image/webp": b"RIFF",
}


class OperationalImageInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    filename: str = Field(min_length=1, max_length=200)
    content_type: str = Field(min_length=1, max_length=100)
    data_base64: str = Field(min_length=1, max_length=7_000_000)


def _base():
    import order_review_routes
    return order_review_routes


def preference_identity(source_item: Any, name: str, linked_specs: list[dict[str, Any]]) -> tuple[str, str, str | None, dict[str, str]]:
    """Reuse only an exact named operational product + non-personal option set.

    Deliberately do not learn engraving, gift messages or customer-specific
    names. Source product identities use the existing stable product/variant key.
    """
    base = _base()
    product_key, _, _ = base.build_image_preference_identity(source_item)
    name_key = base._normalized(name)
    values: dict[str, str] = {}
    for spec in linked_specs:
        if not isinstance(spec, dict):
            continue
        key = base._normalized(spec.get("key") or spec.get("name"))
        field_name = base._text(spec.get("name") or key)
        value = base._normalized(spec.get("value"))
        if not key or not value or base._is_personal_option(field_name):
            continue
        values[key] = value
    if not values:
        return product_key, name_key, None, {}
    serialized = json.dumps(values, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    signature = hashlib.sha256(f"{product_key}|{name_key}|{serialized}".encode("utf-8")).hexdigest()
    return product_key, name_key, signature, values


def _decode_image(image: OperationalImageInput) -> tuple[bytes, str]:
    kind = image.content_type.strip().casefold()
    if kind not in IMAGE_TYPES:
        raise HTTPException(422, detail={"code": "operational_image_type_invalid"})
    encoded = image.data_base64
    if len(encoded) > 4 * ((MAX_IMAGE_BYTES + 2) // 3) + 4:
        raise HTTPException(422, detail={"code": "operational_image_too_large"})
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, base64.binascii.Error) as exc:
        raise HTTPException(422, detail={"code": "operational_image_data_invalid"}) from exc
    if not raw or len(raw) > MAX_IMAGE_BYTES:
        raise HTTPException(422, detail={"code": "operational_image_too_large"})
    if not raw.startswith(IMAGE_TYPES[kind]) or (kind == "image/webp" and (len(raw) < 12 or raw[8:12] != b"WEBP")):
        raise HTTPException(422, detail={"code": "operational_image_signature_invalid"})
    return raw, kind


async def _ensure_indexes(db: Any) -> None:
    await db[IMAGES].create_index([("user_id", 1), ("id", 1)], unique=True)
    await db[PREFERENCES].create_index(
        [("user_id", 1), ("product_key", 1), ("operational_name_key", 1), ("option_signature", 1)],
        unique=True,
    )


async def upload_operational_image(
    db: Any, *, user_id: str, actor_id: str, source_item: Any,
    operational_name: str, linked_specs: list[dict[str, Any]],
    payload: OperationalImageInput, order_number: str, operational_item_id: str,
) -> dict[str, Any]:
    raw, kind = _decode_image(payload)
    product_key, name_key, signature, values = preference_identity(source_item, operational_name, linked_specs)
    await _ensure_indexes(db)
    image_id = uuid.uuid4().hex
    now = _base()._now()
    await db[IMAGES].insert_one({
        "id": image_id, "user_id": user_id, "product_key": product_key,
        "operational_name_key": name_key, "option_signature": signature,
        "option_values": values, "source_order_number": order_number,
        "operational_item_id": operational_item_id, "filename": payload.filename,
        "content_type": kind, "size": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(), "data_base64": payload.data_base64,
        "created_at": now, "created_by": actor_id,
    })
    return {
        "id": image_id, "url": f"{IMAGE_PREFIX}{image_id}",
        "product_key": product_key, "operational_name_key": name_key,
        "option_signature": signature, "option_values": values,
    }


async def forget_unattached_image(db: Any, *, user_id: str, image_id: str) -> None:
    """Remove an uncommitted private blob when optimistic review CAS loses."""
    await db[IMAGES].delete_one({"user_id": user_id, "id": image_id})


async def remember_operational_image(
    db: Any, *, user_id: str, actor_id: str, image: dict[str, Any],
) -> None:
    if not image.get("option_signature"):
        # Personal-only operational text is never learned on future orders.
        return
    await _ensure_indexes(db)
    await db[PREFERENCES].update_one(
        {
            "user_id": user_id, "product_key": image["product_key"],
            "operational_name_key": image["operational_name_key"],
            "option_signature": image["option_signature"],
        },
        {
            "$set": {"image_id": image["id"], "option_values": image["option_values"],
                     "updated_at": _base()._now(), "updated_by": actor_id},
            "$setOnInsert": {"created_at": _base()._now()},
        },
        upsert=True,
    )


async def learned_operational_image(
    db: Any, *, user_id: str, source_item: Any,
    operational_name: str, linked_specs: list[dict[str, Any]],
) -> str | None:
    product_key, name_key, signature, _ = preference_identity(source_item, operational_name, linked_specs)
    if not signature:
        return None
    record = await db[PREFERENCES].find_one({
        "user_id": user_id, "product_key": product_key,
        "operational_name_key": name_key, "option_signature": signature,
    }, {"_id": 0, "image_id": 1})
    if not record:
        return None
    image_id = str(record.get("image_id") or "")
    if not image_id:
        return None
    exists = await db[IMAGES].find_one(
        {"user_id": user_id, "id": image_id, "deleted_at": {"$exists": False}},
        {"_id": 0, "id": 1},
    )
    return f"{IMAGE_PREFIX}{image_id}" if exists else None


def _merchant_reader_id(user: Any) -> str:
    base = _base()
    if not isinstance(user, dict) or base._normalized(user.get("role")) == "store_driver":
        raise HTTPException(403, detail={"code": "operational_image_read_permission_required"})
    if user.get("_session_client") == "amasi_mobile":
        privileges = set(user.get("_mobile_app_permissions") or []) | base._mobile_app_permissions(user)
        if (not user.get("is_owner") and not privileges.intersection(
            {"app.role.manager", "app.page.pending_review", "app.page.reviewed_preparation",
             "app.page.assembly_shipping"}
        )):
            raise HTTPException(403, detail={"code": "operational_image_read_permission_required"})
        tenant = base._text(user.get("_mobile_owner_id") or user.get("created_by") or (
            user.get("id") if user.get("is_owner") else None
        ))
        if not tenant:
            raise HTTPException(403, detail={"code": "operational_image_read_permission_required"})
        return tenant
    if not (base._can_review(user) or base._normalized(user.get("role")) in
            {"shipping", "warehouse", "preparation"} or
            "fulfillment.view" in set(user.get("extra_permissions") or [])):
        raise HTTPException(403, detail={"code": "operational_image_read_permission_required"})
    return base._merchant_user_id(user)


def make_order_review_operational_image_router(db: Any, current_user: Callable[..., Any]) -> APIRouter:
    router = APIRouter(tags=["Mezan operational item images"])

    @router.get("/order-reviews-v1/operational-images/{image_id}")
    async def get_operational_image(image_id: str, user: dict = Depends(current_user)) -> Response:
        owner_id = _merchant_reader_id(user)
        image = await db[IMAGES].find_one(
            {"user_id": owner_id, "id": image_id, "deleted_at": {"$exists": False}},
            {"_id": 0, "content_type": 1, "data_base64": 1},
        )
        if not image:
            raise HTTPException(404, detail={"code": "operational_image_not_found"})
        try:
            data = base64.b64decode(image["data_base64"], validate=True)
        except (KeyError, ValueError, base64.binascii.Error) as exc:
            raise HTTPException(500, detail={"code": "operational_image_corrupt"}) from exc
        return Response(content=data, media_type=image["content_type"],
                        headers={"Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff"})

    return router
