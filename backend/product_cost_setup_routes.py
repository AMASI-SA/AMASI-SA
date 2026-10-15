"""Product-level cost setup classification and explicit completion.

The product chooses one component category once. Candidate components/services
are then filtered by that category in every UI. Base cost and resources remain
optional; only the explicit completion action removes a sold product from the
missing-cost queue. Later cost/resource edits never unset completion.

This module also owns the final human review queue for completed product costs.
Review revisions are derived from the current cost-related snapshot so a stale
mobile review cannot be approved after cost data changes.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from math import ceil
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from component_workspace_cost_compat_routes import COMPONENT_CATEGORIES
from product_group_link_routes import _extended_operations_view
from product_fulfillment_routes import (
    _product,
    ensure_product_fulfillment_indexes,
)
from product_fulfillment_rules import PRODUCT_RESOURCE_BINDINGS
from product_option_cost_routes import AUDIT, BINDINGS, RESOURCES
from product_v2_details_routes import COST_PROFILES
from product_v2_routes import PRODUCTS, _text


class ProductCostCategoryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category_id: str = Field(min_length=1, max_length=160)


class ProductCostCompletionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    complete: bool = True


class ProductCostReviewCompletionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _json_safe(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
            if key != "_id"
        }
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except Exception:
            pass
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


async def _cost_review_revision(
    db: Any,
    *,
    user_id: str,
    product: dict[str, Any],
) -> int:
    """Build a stable revision from the current cost-related product snapshot."""
    salla_id = _text(product.get("salla_product_id"))
    product_id = _text(product.get("mezan_product_id") or product.get("id"))

    cost_profile = await db[COST_PROFILES].find_one(
        {"user_id": user_id, "salla_product_id": salla_id},
        {"_id": 0},
    )
    product_links = await db[PRODUCT_RESOURCE_BINDINGS].find(
        {"user_id": user_id, "salla_product_id": salla_id},
        {"_id": 0},
    ).to_list(length=5000)
    option_links = await db[BINDINGS].find(
        {"user_id": user_id, "salla_product_id": salla_id},
        {"_id": 0},
    ).to_list(length=5000)

    linked_resource_ids = {
        _text(row.get("resource_id"))
        for row in [*product_links, *option_links]
        if _text(row.get("resource_id"))
    }
    resources = []
    if linked_resource_ids:
        resources = await db[RESOURCES].find(
            {"user_id": user_id, "id": {"$in": sorted(linked_resource_ids)}},
            {"_id": 0},
        ).to_list(length=5000)

    product_snapshot = {
        "id": product_id,
        "salla_product_id": salla_id,
        "cost_setup_complete": product.get("cost_setup_complete") is True,
        "cost_setup_completed_at": product.get("cost_setup_completed_at"),
        "cost_category_id": product.get("cost_category_id"),
        "cost_category_name": product.get("cost_category_name"),
        "cost_price_from_salla": product.get("cost_price_from_salla"),
        "variants": product.get("variants") or [],
        "updated_at": product.get("updated_at"),
    }
    snapshot = {
        "product": product_snapshot,
        "cost_profile": cost_profile or {},
        "product_links": sorted(
            product_links,
            key=lambda row: (
                _text(row.get("resource_id")),
                _text(row.get("id")),
            ),
        ),
        "option_links": sorted(
            option_links,
            key=lambda row: (
                _text(row.get("option_id")),
                _text(row.get("value_id")),
                _text(row.get("resource_id")),
                _text(row.get("id")),
            ),
        ),
        "resources": sorted(
            resources,
            key=lambda row: (_text(row.get("id")), _text(row.get("name"))),
        ),
    }
    encoded = json.dumps(
        _json_safe(snapshot),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    revision = int(hashlib.sha256(encoded).hexdigest()[:8], 16)
    return max(revision, 1)


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
    }
    view.setdefault("rules", {}).update({
        "resource_candidates_follow_product_cost_category": True,
        "cost_setup_completion_is_manual": True,
        "cost_setup_completion_requires_base_cost": False,
        "cost_setup_completion_requires_resources": False,
        "cost_setup_edits_do_not_uncomplete": True,
    })
    return view


def make_product_cost_setup_router(
    db: Any,
    current_user: Callable[..., Any],
) -> APIRouter:
    router = APIRouter(prefix="/products-v2", tags=["Product V2 Cost Setup"])

    # Keep static /cost-review ahead of /{product_id}/... routes so FastAPI
    # never interprets "cost-review" as a product id.
    @router.get("/cost-review")
    async def list_completed_cost_reviews(
        user: dict = Depends(current_user),
        page: int = Query(default=1, ge=1),
        per_page: int = Query(default=6, ge=1, le=100),
        q: str | None = Query(default=None, max_length=160),
    ) -> dict[str, Any]:
        user_id = str(user["id"])
        selector: dict[str, Any] = {
            "user_id": user_id,
            "archived": {"$ne": True},
            "cost_setup_complete": True,
        }
        if q and q.strip():
            pattern = re.escape(q.strip())
            selector["$or"] = [
                {"name": {"$regex": pattern, "$options": "i"}},
                {"sku": {"$regex": pattern, "$options": "i"}},
                {"salla_product_id": {"$regex": pattern, "$options": "i"}},
            ]

        candidates = await db[PRODUCTS].find(
            selector,
            {"_id": 0, "raw_salla": 0, "raw_salla_details": 0},
        ).sort("cost_setup_completed_at", -1).to_list(length=5000)

        pending: list[dict[str, Any]] = []
        for product in candidates:
            revision = await _cost_review_revision(
                db,
                user_id=user_id,
                product=product,
            )
            completed_revision = int(product.get("cost_review_completed_revision") or 0)
            if completed_revision == revision:
                continue
            row = dict(product)
            row["review_revision"] = revision
            row["cost_review_revision"] = revision
            row["review_reason_label"] = (
                _text(product.get("cost_review_reason_label"))
                or "اكتملت تكلفة المنتج وتحتاج مراجعة نهائية"
            )
            row["cost_review_reason_label"] = row["review_reason_label"]
            row["cost_review_requested_at"] = (
                product.get("cost_review_requested_at")
                or product.get("cost_setup_completed_at")
            )
            pending.append(row)

        total = len(pending)
        total_pages = max(1, ceil(total / per_page)) if total else 1
        start = (page - 1) * per_page
        items = pending[start:start + per_page] if start < total else []
        return {
            "items": items,
            "pagination": {
                "page": page,
                "per_page": per_page,
                "total": total,
                "total_pages": total_pages,
            },
        }

    @router.post("/{product_id}/cost-review/complete")
    async def complete_cost_review(
        product_id: str,
        payload: ProductCostReviewCompletionRequest,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        user_id = str(user["id"])
        product = await _product(db, user_id, product_id)
        if product.get("cost_setup_complete") is not True:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "product_cost_setup_not_complete",
                    "message": "لا يمكن اعتماد المراجعة قبل اكتمال تكلفة المنتج.",
                },
            )
        current_revision = await _cost_review_revision(
            db,
            user_id=user_id,
            product=product,
        )
        if int(payload.expected_revision) != current_revision:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "product_cost_review_stale",
                    "message": "تغيّرت بيانات تكلفة المنتج. افتح المنتج وراجعه مرة أخرى قبل الاعتماد.",
                    "expected_revision": int(payload.expected_revision),
                    "current_revision": current_revision,
                },
            )

        now = _now()
        await db[PRODUCTS].update_one(
            {"user_id": user_id, "id": product.get("id")},
            {"$set": {
                "cost_review_completed_revision": current_revision,
                "cost_review_completed_at": now,
                "cost_review_completed_by": user_id,
            }},
        )
        await db[AUDIT].insert_one({
            "id": f"product-cost-review:{product.get('id')}:{now.timestamp()}",
            "user_id": user_id,
            "event_type": "product_cost_review_completed",
            "product_id": product.get("mezan_product_id") or product.get("id"),
            "salla_product_id": product.get("salla_product_id"),
            "revision": current_revision,
            "created_at": now,
        })
        return {
            "ok": True,
            "product_id": product.get("mezan_product_id") or product.get("id"),
            "review_revision": current_revision,
            "reviewed_at": now,
        }

    # Registered before the older operations routes so both web and mobile see
    # the product-level category and manual completion state from one endpoint.
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
            {
                "user_id": user_id,
                "id": category_id,
                "status": {"$ne": "inactive"},
            },
            {"_id": 0},
        )
        if not category:
            raise HTTPException(
                status_code=422,
                detail={"code": "product_cost_category_not_found"},
            )
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
        now = _now()
        complete = bool(payload.complete)
        patch: dict[str, Any] = {
            "cost_setup_complete": complete,
            "updated_at": now,
        }
        unset: dict[str, str] = {}
        if complete:
            patch.update({
                "cost_setup_completed_at": now,
                "cost_setup_completed_by": user_id,
                "cost_review_requested_at": now,
            })
        else:
            unset.update({
                "cost_setup_completed_at": "",
                "cost_setup_completed_by": "",
                "cost_review_requested_at": "",
                "cost_review_completed_revision": "",
                "cost_review_completed_at": "",
                "cost_review_completed_by": "",
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
            "before": {"complete": product.get("cost_setup_complete") is True},
            "after": {"complete": complete},
            "created_at": now,
        })
        refreshed = await _product(db, user_id, product_id)
        return {"ok": True, **(await _view(db, user_id=user_id, product=refreshed))}

    return router


__all__ = [
    "ProductCostCategoryRequest",
    "ProductCostCompletionRequest",
    "ProductCostReviewCompletionRequest",
    "make_product_cost_setup_router",
]
