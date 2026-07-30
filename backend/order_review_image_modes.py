"""Selectable preparation-image save modes and Mezan-only image uploads."""
from __future__ import annotations

import base64
import hashlib
import json
import uuid
from typing import Any, Callable, Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field
from pymongo.errors import DuplicateKeyError

import order_review_routes as base
from order_engine.repository import MongoOrderRepository
from order_engine.service import OrderNotFoundError, get_order

MEZAN_IMAGES = "order_review_mezan_images"
MAX_IMAGE_BYTES = 5 * 1024 * 1024
ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}
MEZAN_IMAGE_PREFIX = "/api/order-reviews-v1/mezan-images/"


def _selected_values(item: Any) -> dict[str, str]:
    values: dict[str, str] = {}
    for option in getattr(item, "options", None) or []:
        name = base._text(getattr(option, "name", None))
        value = base._text(getattr(option, "value", None))
        if name and value:
            values[base._normalized(name)] = base._normalized(value)
    for name in ("color", "size", "material"):
        value = base._text(getattr(item, name, None))
        if value:
            values.setdefault(base._normalized(name), base._normalized(value))
    for field in getattr(item, "custom_fields", None) or []:
        if not isinstance(field, dict):
            continue
        name = base._text(field.get("name") or field.get("label") or field.get("title") or field.get("question") or field.get("key"))
        raw = field.get("value") or field.get("answer") or field.get("selected") or field.get("choice") or field.get("text") or field.get("response")
        value = base._text(raw.get("name") if isinstance(raw, dict) else raw)
        if name and value:
            values.setdefault(base._normalized(name), base._normalized(value))
    return values


def _signature(product_key: str, values: dict[str, str]) -> str:
    canonical = json.dumps(values, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(f"{product_key}|{canonical}".encode("utf-8")).hexdigest()


def _image_url(image_id: str) -> str:
    return f"{MEZAN_IMAGE_PREFIX}{image_id}"


async def _ensure_mezan_image_indexes(db: Any) -> None:
    await db[MEZAN_IMAGES].create_index([("user_id", 1), ("product_key", 1), ("created_at", -1)])
    await db[MEZAN_IMAGES].create_index([("user_id", 1), ("id", 1)], unique=True)


async def _preference_map(db: Any, user_id: str, identities: list[Any]) -> dict[tuple[str, str], dict[str, Any]]:
    contexts = []
    product_keys = set()
    for item in identities:
        product_key, exact_signature, _ = base.build_image_preference_identity(item)
        contexts.append((item, product_key, exact_signature, _selected_values(item)))
        product_keys.add(product_key)
    if not product_keys:
        return {}
    docs = await db[base.PREFERENCES].find(
        {"user_id": user_id, "product_key": {"$in": sorted(product_keys)}}, {"_id": 0}
    ).to_list(5000)
    by_product: dict[str, list[dict[str, Any]]] = {}
    for doc in docs:
        by_product.setdefault(base._text(doc.get("product_key")), []).append(doc)
    result = {}
    for _, product_key, exact_signature, current_values in contexts:
        best = None
        best_score = -2
        for doc in by_product.get(product_key, []):
            mode = base._text(doc.get("mode")) or "options"
            rule_values = {
                base._normalized(key): base._normalized(value)
                for key, value in (doc.get("option_values") or {}).items()
                if base._text(key) and base._text(value)
            }
            if mode == "default":
                score = -1
            elif rule_values and all(current_values.get(key) == value for key, value in rule_values.items()):
                score = len(rule_values)
            elif doc.get("option_signature") == exact_signature:
                score = len(rule_values)
            else:
                continue
            if score > best_score:
                best, best_score = doc, score
        if best:
            result[(product_key, exact_signature)] = best
    return result


_original_review_item_identities = base._review_item_identities
_original_item_view = base._item_view


async def _review_item_identities(db: Any, user_id: str, order: Any) -> list[Any]:
    identities = await _original_review_item_identities(db, user_id, order)
    if not identities:
        return identities
    product_keys = []
    by_key: dict[str, Any] = {}
    for item in identities:
        product_key, _, _ = base.build_image_preference_identity(item)
        product_keys.append(product_key)
        by_key[product_key] = item
    docs = await db[MEZAN_IMAGES].find(
        {"user_id": user_id, "product_key": {"$in": sorted(set(product_keys))}, "deleted_at": {"$exists": False}},
        {"_id": 0, "id": 1, "product_key": 1},
    ).sort("created_at", 1).to_list(5000)
    for doc in docs:
        item = by_key.get(base._text(doc.get("product_key")))
        if item is None:
            continue
        url = _image_url(base._text(doc.get("id")))
        current = list(getattr(item, "image_urls", None) or [])
        if url not in current:
            current.append(url)
            item.image_urls = current
    return identities


def _item_view(item: Any, saved: Any, preference: Any) -> dict[str, Any]:
    view = _original_item_view(item, saved, preference)
    view["mezan_images"] = [url for url in view.get("gallery") or [] if base._text(url).startswith(MEZAN_IMAGE_PREFIX)]
    return view


base._preference_map = _preference_map
base._review_item_identities = _review_item_identities
base._item_view = _item_view


class ImageChoiceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=0)
    selected_image_url: str = Field(min_length=1, max_length=3000)
    mode: Literal["order_only", "options", "default"]
    selected_spec_keys: list[str] = Field(default_factory=list, max_length=40)


class MezanImageUploadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    filename: str = Field(min_length=1, max_length=200)
    content_type: str = Field(min_length=1, max_length=100)
    data_base64: str = Field(min_length=1)


def make_order_review_router(db: Any, current_user: Callable) -> APIRouter:
    router = APIRouter()
    router.include_router(base.make_order_review_router(db, current_user))
    repository = MongoOrderRepository(db)

    async def resolve_item(order_number: str, order_item_id: str, user: dict) -> tuple[str, str, Any, Any]:
        reviewer = base._require_reviewer(user)
        user_id = base._merchant_user_id(reviewer)
        try:
            order = await get_order(repository, user_id=user_id, order_number=order_number)
        except OrderNotFoundError as exc:
            raise HTTPException(status_code=404, detail={"code": "order_not_found"}) from exc
        identities = await base._review_item_identities(db, user_id, order)
        target = next((item for item in identities if item.order_item_id == order_item_id), None)
        if target is None:
            raise HTTPException(status_code=404, detail={"code": "order_item_not_found"})
        product_key, _, _ = base.build_image_preference_identity(target)
        return user_id, str(reviewer["id"]), order, target

    @router.get("/order-reviews-v1/mezan-images/{image_id}")
    async def get_mezan_image(image_id: str, user: dict = Depends(current_user)) -> Response:
        reviewer = base._require_reviewer(user)
        user_id = base._merchant_user_id(reviewer)
        row = await db[MEZAN_IMAGES].find_one({"user_id": user_id, "id": image_id, "deleted_at": {"$exists": False}}, {"_id": 0})
        if not row:
            raise HTTPException(status_code=404, detail={"code": "mezan_image_not_found"})
        try:
            raw = base64.b64decode(row["data_base64"], validate=True)
        except Exception as exc:
            raise HTTPException(status_code=500, detail={"code": "mezan_image_corrupt"}) from exc
        return Response(content=raw, media_type=row.get("content_type") or "image/jpeg", headers={"Cache-Control": "private, max-age=3600"})

    @router.post("/order-reviews-v1/{order_number}/items/{order_item_id:path}/mezan-images")
    async def upload_mezan_image(
        order_number: str,
        order_item_id: str,
        payload: MezanImageUploadRequest = Body(...),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        user_id, actor_id, order, target = await resolve_item(order_number, order_item_id, user)
        content_type = payload.content_type.lower().strip()
        if content_type not in ALLOWED_IMAGE_TYPES:
            raise HTTPException(status_code=422, detail={"code": "unsupported_image_type", "message": "الصيغ المسموحة JPG أو PNG أو WEBP."})
        try:
            raw = base64.b64decode(payload.data_base64, validate=True)
        except Exception as exc:
            raise HTTPException(status_code=422, detail={"code": "invalid_image_data"}) from exc
        if not raw or len(raw) > MAX_IMAGE_BYTES:
            raise HTTPException(status_code=422, detail={"code": "image_too_large", "message": "الحد الأقصى للصورة 5 MB."})
        signatures = {
            "image/jpeg": (b"\xff\xd8\xff",),
            "image/png": (b"\x89PNG\r\n\x1a\n",),
            "image/webp": (b"RIFF",),
        }
        if not any(raw.startswith(signature) for signature in signatures[content_type]):
            raise HTTPException(status_code=422, detail={"code": "image_signature_mismatch"})
        await _ensure_mezan_image_indexes(db)
        product_key, _, _ = base.build_image_preference_identity(target)
        image_id = uuid.uuid4().hex
        await db[MEZAN_IMAGES].insert_one({
            "id": image_id,
            "user_id": user_id,
            "product_key": product_key,
            "filename": payload.filename,
            "content_type": content_type,
            "size": len(raw),
            "sha256": hashlib.sha256(raw).hexdigest(),
            "data_base64": payload.data_base64,
            "source_order_number": order.order_number,
            "source_order_item_id": order_item_id,
            "created_at": base._now(),
            "created_by": actor_id,
        })
        await db[base.EVENTS].insert_one({"user_id": user_id, "order_number": order.order_number, "order_item_id": order_item_id, "event_type": "review_mezan_image_uploaded", "image_id": image_id, "occurred_at": base._now(), "actor_id": actor_id})
        return await base._detail(db, user_id, order)

    @router.delete("/order-reviews-v1/{order_number}/items/{order_item_id:path}/mezan-images/{image_id}")
    async def delete_mezan_image(order_number: str, order_item_id: str, image_id: str, user: dict = Depends(current_user)) -> dict[str, Any]:
        user_id, actor_id, order, target = await resolve_item(order_number, order_item_id, user)
        product_key, _, _ = base.build_image_preference_identity(target)
        row = await db[MEZAN_IMAGES].find_one({"user_id": user_id, "id": image_id, "product_key": product_key, "deleted_at": {"$exists": False}}, {"_id": 0})
        if not row:
            raise HTTPException(status_code=404, detail={"code": "mezan_image_not_found"})
        url = _image_url(image_id)
        if await db[base.PREFERENCES].count_documents({"user_id": user_id, "selected_image_url": url}):
            raise HTTPException(status_code=409, detail={"code": "mezan_image_in_use", "message": "فك ارتباط الصورة من الصورة الرئيسية أو قواعد الخيارات قبل حذفها."})
        if await db[base.WORKFLOWS].count_documents({"user_id": user_id, "items.selected_image_url": url}):
            raise HTTPException(status_code=409, detail={"code": "mezan_image_in_use", "message": "الصورة مستخدمة في طلب محفوظ ولا يمكن حذفها الآن."})
        await db[MEZAN_IMAGES].update_one({"user_id": user_id, "id": image_id}, {"$set": {"deleted_at": base._now(), "deleted_by": actor_id}, "$unset": {"data_base64": ""}})
        await db[base.EVENTS].insert_one({"user_id": user_id, "order_number": order.order_number, "order_item_id": order_item_id, "event_type": "review_mezan_image_deleted", "image_id": image_id, "occurred_at": base._now(), "actor_id": actor_id})
        return await base._detail(db, user_id, order)

    @router.post("/order-reviews-v1/{order_number}/items/{order_item_id:path}/image-choice")
    async def save_image_choice(
        order_number: str,
        order_item_id: str,
        payload: ImageChoiceRequest = Body(...),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        user_id, actor_id, order, target = await resolve_item(order_number, order_item_id, user)
        selected = base._text(payload.selected_image_url)
        allowed = {base._text(url) for url in [*(target.image_urls or []), target.image_url] if base._text(url)}
        if selected not in allowed:
            raise HTTPException(status_code=422, detail={"code": "image_not_in_product_gallery", "message": "اختر صورة من صور هذا المنتج."})
        workflow = await db[base.WORKFLOWS].find_one({"user_id": user_id, "order_number": order.order_number}, {"_id": 0})
        if (workflow or {}).get("stage") in base.REVIEW_COMPLETED_STAGES:
            raise HTTPException(status_code=409, detail={"code": "review_already_completed"})
        revision = int((workflow or {}).get("revision") or 0)
        if revision != payload.expected_revision:
            raise HTTPException(status_code=409, detail={"code": "review_revision_conflict"})
        states = base._state_map(workflow)
        current = states.get(order_item_id, {"order_item_id": order_item_id, "review_status": "pending_review", "revision": 0})
        current.update({"selected_image_url": selected, "selected_image_source": "manual", "order_item_id": order_item_id, "review_status": "pending_review", "revision": int(current.get("revision") or 0) + 1, "updated_at": base._now(), "updated_by": actor_id})
        states[order_item_id] = current
        new_doc = {**(workflow or {}), "user_id": user_id, "order_number": order.order_number, "order_id": order.order_id, "stage": "pending_review", "revision": revision + 1, "items": list(states.values()), "updated_at": base._now(), "updated_by": actor_id}
        new_doc.pop("_id", None)
        if workflow:
            result = await db[base.WORKFLOWS].replace_one({"user_id": user_id, "order_number": order.order_number, "revision": revision}, new_doc)
            if not result.matched_count:
                raise HTTPException(status_code=409, detail={"code": "review_revision_conflict"})
        else:
            new_doc["created_at"] = new_doc["updated_at"]
            try:
                await db[base.WORKFLOWS].insert_one(new_doc)
            except DuplicateKeyError as exc:
                raise HTTPException(status_code=409, detail={"code": "review_revision_conflict"}) from exc
        product_key, _, _ = base.build_image_preference_identity(target)
        if payload.mode == "options":
            all_values = _selected_values(target)
            selected_keys = {base._normalized(key) for key in payload.selected_spec_keys if base._text(key)}
            option_values = {key: all_values[key] for key in sorted(selected_keys) if key in all_values}
            if not option_values:
                raise HTTPException(status_code=422, detail={"code": "image_choice_options_required", "message": "اختر خيارًا واحدًا على الأقل."})
            preference_signature = _signature(product_key, option_values)
            mode = "options"
        elif payload.mode == "default":
            option_values = {}
            preference_signature = "__default__"
            mode = "default"
        else:
            option_values = None
            preference_signature = None
            mode = "order_only"
        if preference_signature:
            await db[base.PREFERENCES].update_one(
                {"user_id": user_id, "product_key": product_key, "option_signature": preference_signature},
                {"$set": {"selected_image_url": selected, "option_values": option_values, "mode": mode, "updated_at": base._now(), "updated_by": actor_id}, "$setOnInsert": {"created_at": base._now()}},
                upsert=True,
            )
        await db[base.EVENTS].insert_one({"user_id": user_id, "order_number": order.order_number, "order_item_id": order_item_id, "event_type": "review_image_choice_saved", "mode": mode, "occurred_at": base._now(), "actor_id": actor_id})
        return await base._detail(db, user_id, order)

    return router
