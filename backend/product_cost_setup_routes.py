"""Product-level cost setup classification, completion, and final review queue.

A sold product remains in the cost-setup queue until ``cost_setup_complete`` is
explicitly set. Completed products then enter a separate server-side review
queue. Reviewing a product never deletes it; it records the reviewed revision.
Later cost-category/completion changes can requeue the product without reopening
its cost setup.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from component_workspace_cost_compat_routes import COMPONENT_CATEGORIES
from product_group_link_routes import _extended_operations_view
from product_fulfillment_routes import (
    _product,
    ensure_product_fulfillment_indexes,
)
from product_option_cost_routes import AUDIT
from product_v2_routes import PRODUCTS, _text


class ProductCostCategoryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category_id: str = Field(min_length=1, max_length=160)


class ProductCostCompletionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    complete: bool = True
    reviewed: bool = False
    expected_review_revision: int | None = Field(default=None, ge=1)


class ProductCostReviewCompleteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _review_revision(product: dict[str, Any]) -> int:
    return max(1, int(product.get("cost_review_revision") or 1))


def _queue_review_patch(
    product: dict[str, Any],
    *,
    now: datetime,
    reason_code: str,
    reason_label: str,
) -> dict[str, Any]:
    revision = _review_revision(product)
    if product.get("cost_review_status") == "reviewed":
        revision += 1
    return {
        "cost_review_status": "pending",
        "cost_review_revision": revision,
        "cost_review_reason_code": reason_code,
        "cost_review_reason_label": reason_label,
        "cost_review_requested_at": now,
        "cost_review_source": "product_cost_setup_v2",
    }


def _serialize_review_product(product: dict[str, Any]) -> dict[str, Any]:
    row = dict(product)
    for key in (
        "created_at", "updated_at", "last_synced_at", "cost_setup_completed_at",
        "cost_review_requested_at", "cost_reviewed_at",
    ):
        value = row.get(key)
        if hasattr(value, "isoformat"):
            row[key] = value.isoformat()
    row["review_revision"] = _review_revision(product)
    row["review_reason_code"] = _text(product.get("cost_review_reason_code")) or "cost_setup_completed"
    row["review_reason_label"] = _text(product.get("cost_review_reason_label")) or "اكتملت تكلفة المنتج"
    return row


async def _mark_reviewed(
    db: Any,
    *,
    user_id: str,
    product_id: str,
    product: dict[str, Any],
    expected_revision: int,
) -> dict[str, Any]:
    if product.get("cost_setup_complete") is not True:
        raise HTTPException(status_code=409, detail={"code": "product_cost_setup_not_complete"})
    current_revision = _review_revision(product)
    if expected_revision != current_revision:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "product_cost_review_revision_conflict",
                "expected_revision": expected_revision,
                "current_revision": current_revision,
            },
        )
    if product.get("cost_review_status") == "reviewed" and int(product.get("cost_reviewed_revision") or 0) == current_revision:
        return {"ok": True, "idempotent_replay": True, "review_revision": current_revision}
    now = _now()
    revision_selector: Any = {"$in": [current_revision, None]} if current_revision == 1 else current_revision
    result = await db[PRODUCTS].update_one(
        {
            "user_id": user_id,
            "id": product.get("id"),
            "cost_setup_complete": True,
            "cost_review_revision": revision_selector,
            "cost_review_status": {"$in": ["pending", None]},
        },
        {"$set": {
            "cost_review_status": "reviewed",
            "cost_reviewed_revision": current_revision,
            "cost_reviewed_at": now,
            "cost_reviewed_by": user_id,
            "updated_at": now,
        }},
    )
    if result.modified_count != 1:
        latest = await _product(db, user_id, product_id)
        if latest.get("cost_review_status") == "reviewed" and int(latest.get("cost_reviewed_revision") or 0) == current_revision:
            return {"ok": True, "idempotent_replay": True, "review_revision": current_revision}
        raise HTTPException(status_code=409, detail={"code": "product_cost_review_concurrent_update"})
    await db[AUDIT].insert_one({
        "id": f"product-cost-review:{product.get('id')}:{now.timestamp()}",
        "user_id": user_id,
        "event_type": "product_cost_review_completed",
        "product_id": product.get("mezan_product_id") or product.get("id"),
        "review_revision": current_revision,
        "created_at": now,
    })
    return {"ok": True, "idempotent_replay": False, "review_revision": current_revision}


async def _view(
    db: Any,
    *,
    user_id: str,
    product: dict[str, Any],
) -> dict[str, Any]:
    view = await _extended_operations_view(
        db,
        user_id=user_id,
        product=product,
    )
    category_id = _text(product.get("cost_category_id"))
    categories = list(view.get("categories") or [])
    category = next(
        (row for row in categories if _text(row.get("id")) == category_id),
        None,
    )
    product_view = dict(view.get("product") or {})
    product_view.update({
        "cost_category_id": category_id or None,
        "cost_category_name": (
            _text((category or {}).get("name"))
            or _text(product.get("cost_category_name"))
            or None
        ),
        "cost_setup_complete": product.get("cost_setup_complete") is True,
        "cost_setup_completed_at": product.get("cost_setup_completed_at"),
        "cost_setup_completed_by": _text(product.get("cost_setup_completed_by")) or None,
        "cost_review_status": product.get("cost_review_status") or None,
        "cost_review_revision": _review_revision(product),
        "cost_review_reason_label": _text(product.get("cost_review_reason_label")) or None,
        "cost_reviewed_at": product.get("cost_reviewed_at"),
    })
    view["product"] = product_view
    view["cost_setup"] = {
        "category_required_for_resource_picker": True,
        "cost_category_id": category_id or None,
        "cost_category_name": product_view["cost_category_name"],
        "complete": product_view["cost_setup_complete"],
        "base_cost_required": False,
        "components_required": False,
        "services_required": False,
        "completion_is_explicit": True,
        "edits_do_not_reopen_completion": True,
        "final_review_required": True,
        "review_status": product_view["cost_review_status"],
        "review_revision": product_view["cost_review_revision"],
    }
    view.setdefault("rules", {}).update({
        "resource_candidates_follow_product_cost_category": True,
        "cost_setup_completion_is_manual": True,
        "cost_setup_completion_requires_base_cost": False,
        "cost_setup_completion_requires_resources": False,
        "cost_setup_edits_do_not_uncomplete": True,
        "completed_cost_requires_final_review": True,
    })
    return view


def make_product_cost_setup_router(
    db: Any,
    current_user: Callable[..., Any],
) -> APIRouter:
    router = APIRouter(prefix="/products-v2", tags=["Product V2 Cost Setup"])

    @router.get("/cost-review")
    async def list_cost_review_queue(
        user: dict = Depends(current_user),
        page: int = Query(default=1, ge=1),
        per_page: int = Query(default=30, ge=1, le=100),
        q: str | None = Query(default=None, max_length=160),
    ) -> dict[str, Any]:
        user_id = str(user["id"])
        query: dict[str, Any] = {
            "user_id": user_id,
            "archived": {"$ne": True},
            "cost_setup_complete": True,
            "$or": [
                {"cost_review_status": {"$exists": False}},
                {"cost_review_status": None},
                {"cost_review_status": "pending"},
            ],
        }
        if q and q.strip():
            pattern = re.escape(q.strip())
            query["$and"] = [{"$or": [
                {"name": {"$regex": pattern, "$options": "i"}},
                {"sku": {"$regex": pattern, "$options": "i"}},
                {"salla_product_id": {"$regex": pattern, "$options": "i"}},
                {"mezan_product_id": {"$regex": pattern, "$options": "i"}},
            ]}]
        total = await db[PRODUCTS].count_documents(query)
        rows = await (
            db[PRODUCTS]
            .find(query, {"_id": 0, "raw_salla": 0, "raw_salla_details": 0})
            .sort([
                ("cost_review_requested_at", -1),
                ("cost_setup_completed_at", -1),
                ("updated_at", -1),
            ])
            .skip((page - 1) * per_page)
            .limit(per_page)
            .to_list(length=per_page)
        )
        return {
            "items": [_serialize_review_product(row) for row in rows],
            "pagination": {
                "page": page,
                "per_page": per_page,
                "total": total,
                "total_pages": max(1, (total + per_page - 1) // per_page),
            },
            "meta": {"source": "products_v2_cost_review", "server_ssot": True},
        }

    @router.get("/{product_id}/operations")
    async def get_product_operations(
        product_id: str,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        await ensure_product_fulfillment_indexes(db)
        user_id = str(user["id"])
        product = await _product(db, user_id, product_id)
        return await _view(db, user_id=user_id, product=product)

    @router.put("/{product_id}/cost-category")
    async def set_cost_category(
        product_id: str,
        payload: ProductCostCategoryRequest,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        user_id = str(user["id"])
        product = await _product(db, user_id, product_id)
        category_id = _text(payload.category_id)
        category = await db[COMPONENT_CATEGORIES].find_one(
            {"user_id": user_id, "id": category_id, "status": {"$ne": "inactive"}},
            {"_id": 0},
        )
        if not category:
            raise HTTPException(status_code=422, detail={"code": "product_cost_category_not_found"})
        now = _now()
        before = {
            "cost_category_id": product.get("cost_category_id"),
            "cost_category_name": product.get("cost_category_name"),
        }
        patch = {
            "cost_category_id": category_id,
            "cost_category_name": _text(category.get("name")),
            "cost_category_updated_at": now,
            "cost_category_updated_by": user_id,
            "updated_at": now,
        }
        if product.get("cost_setup_complete") is True and category_id != _text(product.get("cost_category_id")):
            patch.update(_queue_review_patch(
                product,
                now=now,
                reason_code="cost_category_changed",
                reason_label="تم تعديل تصنيف تكلفة المنتج",
            ))
        await db[PRODUCTS].update_one(
            {"user_id": user_id, "id": product.get("id")},
            {"$set": patch},
        )
        await db[AUDIT].insert_one({
            "id": f"product-cost-category:{product.get('id')}:{now.timestamp()}",
            "user_id": user_id,
            "event_type": "product_cost_category_saved",
            "product_id": product.get("mezan_product_id") or product.get("id"),
            "before": before,
            "after": {
                "cost_category_id": category_id,
                "cost_category_name": _text(category.get("name")),
            },
            "created_at": now,
        })
        refreshed = await _product(db, user_id, product_id)
        return {"ok": True, **(await _view(db, user_id=user_id, product=refreshed))}

    @router.put("/{product_id}/cost-completion")
    async def set_cost_completion(
        product_id: str,
        payload: ProductCostCompletionRequest,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        user_id = str(user["id"])
        product = await _product(db, user_id, product_id)
        if payload.reviewed:
            if payload.expected_review_revision is None:
                raise HTTPException(status_code=422, detail={"code": "product_cost_review_revision_required"})
            result = await _mark_reviewed(
                db,
                user_id=user_id,
                product_id=product_id,
                product=product,
                expected_revision=payload.expected_review_revision,
            )
            refreshed = await _product(db, user_id, product_id)
            return {**result, **(await _view(db, user_id=user_id, product=refreshed))}

        now = _now()
        complete = bool(payload.complete)
        was_complete = product.get("cost_setup_complete") is True
        patch: dict[str, Any] = {"cost_setup_complete": complete, "updated_at": now}
        unset: dict[str, str] = {}
        if complete:
            if not was_complete:
                patch.update({
                    "cost_setup_completed_at": now,
                    "cost_setup_completed_by": user_id,
                    **_queue_review_patch(
                        product,
                        now=now,
                        reason_code="cost_setup_completed",
                        reason_label="اكتملت تكلفة المنتج وتحتاج مراجعة نهائية",
                    ),
                })
        else:
            patch["cost_review_status"] = "incomplete"
            unset.update({
                "cost_setup_completed_at": "",
                "cost_setup_completed_by": "",
                "cost_review_requested_at": "",
            })
        update: dict[str, Any] = {"$set": patch}
        if unset:
            update["$unset"] = unset
        await db[PRODUCTS].update_one(
            {"user_id": user_id, "id": product.get("id")},
            update,
        )
        await db[AUDIT].insert_one({
            "id": f"product-cost-completion:{product.get('id')}:{now.timestamp()}",
            "user_id": user_id,
            "event_type": "product_cost_completion_changed",
            "product_id": product.get("mezan_product_id") or product.get("id"),
            "before": {"complete": was_complete},
            "after": {"complete": complete},
            "created_at": now,
        })
        refreshed = await _product(db, user_id, product_id)
        return {"ok": True, **(await _view(db, user_id=user_id, product=refreshed))}

    @router.post("/{product_id}/cost-review/complete")
    async def complete_cost_review(
        product_id: str,
        payload: ProductCostReviewCompleteRequest,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        user_id = str(user["id"])
        product = await _product(db, user_id, product_id)
        return await _mark_reviewed(
            db,
            user_id=user_id,
            product_id=product_id,
            product=product,
            expected_revision=payload.expected_revision,
        )

    return router


__all__ = [
    "ProductCostCategoryRequest",
    "ProductCostCompletionRequest",
    "ProductCostReviewCompleteRequest",
    "make_product_cost_setup_router",
]
