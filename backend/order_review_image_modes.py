"""Selectable preparation-image save modes for order review."""
from __future__ import annotations

import hashlib
import json
from typing import Any, Callable, Literal

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from pymongo.errors import DuplicateKeyError

import order_review_routes as base
from order_engine.repository import MongoOrderRepository
from order_engine.service import OrderNotFoundError, get_order


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


base._preference_map = _preference_map


class ImageChoiceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=0)
    selected_image_url: str = Field(min_length=1, max_length=3000)
    mode: Literal["order_only", "options", "default"]
    selected_spec_keys: list[str] = Field(default_factory=list, max_length=40)


def make_order_review_router(db: Any, current_user: Callable) -> APIRouter:
    router = APIRouter()
    router.include_router(base.make_order_review_router(db, current_user))
    repository = MongoOrderRepository(db)

    @router.post("/order-reviews-v1/{order_number}/items/{order_item_id:path}/image-choice")
    async def save_image_choice(
        order_number: str,
        order_item_id: str,
        payload: ImageChoiceRequest = Body(...),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        reviewer = base._require_reviewer(user)
        user_id = base._merchant_user_id(reviewer)
        actor_id = str(reviewer["id"])
        await base._ensure_indexes(db)
        try:
            order = await get_order(repository, user_id=user_id, order_number=order_number)
        except OrderNotFoundError as exc:
            raise HTTPException(status_code=404, detail={"code": "order_not_found"}) from exc
        identities = await base._review_item_identities(db, user_id, order)
        target = next((item for item in identities if item.order_item_id == order_item_id), None)
        if target is None:
            raise HTTPException(status_code=404, detail={"code": "order_item_not_found"})
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
        current.update({
            "selected_image_url": selected,
            "selected_image_source": "manual",
            "order_item_id": order_item_id,
            "review_status": "pending_review",
            "revision": int(current.get("revision") or 0) + 1,
            "updated_at": base._now(),
            "updated_by": actor_id,
            "updated_by_name": base._text(reviewer.get("name") or reviewer.get("email")),
        })
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
                {"$set": {"selected_image_url": selected, "option_values": option_values, "mode": mode, "source_order_number": order.order_number, "source_order_item_id": order_item_id, "updated_at": base._now(), "updated_by": actor_id}, "$setOnInsert": {"created_at": base._now()}},
                upsert=True,
            )
        await db[base.EVENTS].insert_one({"user_id": user_id, "order_number": order.order_number, "order_item_id": order_item_id, "event_type": "review_image_choice_saved", "mode": mode, "selected_spec_keys": sorted(payload.selected_spec_keys), "occurred_at": base._now(), "actor_id": actor_id})
        return await base._detail(db, user_id, order)

    return router
