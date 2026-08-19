"""Product-level cost setup classification and explicit completion.

The product chooses one component category once. Candidate components/services
are then filtered by that category in every UI. Base cost and resources remain
optional; only the explicit completion action removes a sold product from the
missing-cost queue. Later cost/resource edits never unset completion.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException
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


def _now() -> datetime:
    return datetime.now(timezone.utc)


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
            })
        else:
            unset.update({
                "cost_setup_completed_at": "",
                "cost_setup_completed_by": "",
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
    "make_product_cost_setup_router",
]
