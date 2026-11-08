"""Share one existing component with a product's category and link it atomically."""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from component_category_policy import COMPONENT_CATEGORIES
from component_status_policy import require_active_component
from product_cost_revision import bump_product_cost_revision
from product_cost_setup_routes import _view
from product_fulfillment_routes import _product_key, ensure_product_fulfillment_indexes
from product_fulfillment_rules import PRODUCT_RESOURCE_BINDINGS
from product_option_cost_routes import AUDIT, BINDINGS, RESOURCES
from product_v2_routes import PRODUCTS


class ShareComponentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_category_id: str = Field(min_length=1, max_length=200)
    product_category_id: str = Field(min_length=1, max_length=200)
    quantity: float = Field(default=1, gt=0, le=100000, allow_inf_nan=False)


async def share_and_link_component(db: Any, *, user_id: str, product_id: str,
                                   resource_id: str, payload: ShareComponentRequest,
                                   mongo_session: Any = None) -> dict:
    kwargs = {"session": mongo_session} if mongo_session is not None else {}
    product = await db[PRODUCTS].find_one({"user_id": user_id,
        "$or": [{field: product_id} for field in ("id", "mezan_product_id", "salla_product_id")]}, {"_id": 0}, **kwargs)
    if not product:
        raise HTTPException(404, detail={"code": "product_v2_not_found"})
    category_id = str(product.get("cost_category_id") or "").strip()
    if not category_id:
        raise HTTPException(422, detail={"code": "product_cost_category_required", "message": "حدد تصنيف المنتج أولًا."})
    if category_id != payload.product_category_id:
        raise HTTPException(409, detail={"code": "product_cost_category_changed", "message": "تغيّر تصنيف المنتج. حدّث الصفحة وأعد اختيار المكوّن."})
    for category in {category_id, payload.source_category_id}:
        if not await db[COMPONENT_CATEGORIES].find_one({"user_id": user_id, "id": category,
                "status": {"$ne": "inactive"}}, {"_id": 0, "id": 1}, **kwargs):
            raise HTTPException(422, detail={"code": "component_category_not_found"})
    resource = await db[RESOURCES].find_one({"user_id": user_id, "id": resource_id}, {"_id": 0}, **kwargs)
    require_active_component(resource)
    if resource.get("kind") == "service" or not (resource.get("kind") in {"stock_component", "component"} or resource.get("track_inventory") is True):
        raise HTTPException(422, detail={"code": "component_sharing_requires_component", "message": "اختر مكوّنًا من التصنيف المحدد."})
    if payload.source_category_id not in (resource.get("category_ids") or []):
        raise HTTPException(409, detail={"code": "component_source_category_changed", "message": "لم يعد المكوّن مرتبطًا بالتصنيف المختار. حدّث القائمة."})
    salla_id = _product_key(product)
    option_link = await db[BINDINGS].find_one({"user_id": user_id, "salla_product_id": salla_id,
            "mode": "resource", "resource_id": resource_id}, {"_id": 0, "id": 1}, **kwargs)
    if option_link is not None:
        raise HTTPException(409, detail={"code": "resource_already_linked_to_option"})
    selector = {"user_id": user_id, "salla_product_id": salla_id, "resource_id": resource_id}
    existing = await db[PRODUCT_RESOURCE_BINDINGS].find_one(selector, {"_id": 0}, **kwargs)
    if category_id in (resource.get("category_ids") or []) and existing and existing.get("manual_link", True) is not False and existing.get("quantity") == float(payload.quantity):
        return {"ok": True, "product": product, "idempotent": True, "resource_id": resource_id, "category_id": category_id}
    now = datetime.now(timezone.utc)
    await db[RESOURCES].update_one({"user_id": user_id, "id": resource_id},
        {"$addToSet": {"category_ids": category_id}, "$set": {"updated_at": now}}, **kwargs)
    await db[PRODUCT_RESOURCE_BINDINGS].update_one(selector, {"$set": {
        "mezan_product_id": product.get("mezan_product_id") or product.get("id"),
        "product_name": product.get("name"), "resource_name": resource.get("name"),
        "quantity": float(payload.quantity), "manual_link": True, "updated_at": now,
    }, "$setOnInsert": {"id": uuid.uuid4().hex, "group_ids": [], "created_at": now}}, upsert=True, **kwargs)
    fingerprint = hashlib.sha256(f"{user_id}:{salla_id}:{resource_id}:{category_id}".encode()).hexdigest()
    await db[AUDIT].update_one({"_id": f"component-share:{fingerprint}"}, {"$setOnInsert": {
        "id": f"component-share:{fingerprint}", "user_id": user_id,
        "event_type": "product_component_shared_with_category", "salla_product_id": salla_id,
        "resource_id": resource_id, "source_category_id": payload.source_category_id,
        "category_id": category_id, "quantity": float(payload.quantity), "created_at": now,
    }}, upsert=True, **kwargs)
    await bump_product_cost_revision(db, user_id, session=mongo_session)
    return {"ok": True, "product": product, "idempotent": False, "resource_id": resource_id, "category_id": category_id}


def make_product_component_category_share_router(db: Any, current_user: Callable) -> APIRouter:
    router = APIRouter(prefix="/products-v2", tags=["Product Component Category Sharing"])

    @router.put("/{product_id}/resource-links/{resource_id}/share-category")
    async def share(product_id: str, resource_id: str, payload: ShareComponentRequest,
                    user: dict = Depends(current_user)):
        user_id = str(user["id"])
        await ensure_product_fulfillment_indexes(db)
        client = getattr(db, "client", None)
        if client is None or not hasattr(client, "start_session"):
            raise HTTPException(503, detail={"code": "component_share_transaction_required"})
        async def apply(session):
            return await share_and_link_component(db, user_id=user_id, product_id=product_id,
                resource_id=resource_id, payload=payload, mongo_session=session)
        async with await client.start_session() as session:
            result = await session.with_transaction(apply)
        return {"ok": True, "shared_resource_id": result["resource_id"], "shared_category_id": result["category_id"],
            "idempotent": result["idempotent"], **await _view(db, user_id=user_id, product=result["product"])}
    return router
