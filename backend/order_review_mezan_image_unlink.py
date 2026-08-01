"""Safe unlink-and-delete flow for Mezan-only review images.

The existing delete endpoint intentionally blocks deletion while an image is
used by a product preference or a saved review item.  This router provides the
missing explicit action: unlink the image from the current pending review and
from this product's image rules, then soft-delete it.  References owned by any
other order remain a hard blocker.
"""
from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException

import order_review_routes as base
from order_engine.repository import MongoOrderRepository
from order_engine.service import OrderNotFoundError, get_order


MEZAN_IMAGES = "order_review_mezan_images"
MEZAN_IMAGE_PREFIX = "/api/order-reviews-v1/mezan-images/"


def mezan_image_url(image_id: str) -> str:
    return f"{MEZAN_IMAGE_PREFIX}{image_id}"


def workflow_uses_image(workflow: Any, image_url: str) -> bool:
    if not isinstance(workflow, dict):
        return False
    return any(
        isinstance(row, dict)
        and base._text(row.get("selected_image_url")) == image_url
        for row in (workflow.get("items") or [])
    )


def clear_image_from_workflow_items(
    items: Any,
    image_url: str,
    *,
    actor_id: str,
    updated_at: Any,
) -> tuple[list[Any], int]:
    """Clear one image selection without changing unrelated item state."""
    updated: list[Any] = []
    cleared = 0
    for raw in items or []:
        if not isinstance(raw, dict):
            updated.append(raw)
            continue
        row = dict(raw)
        if base._text(row.get("selected_image_url")) == image_url:
            row.pop("selected_image_url", None)
            row.pop("selected_image_source", None)
            row["revision"] = int(row.get("revision") or 0) + 1
            row["updated_at"] = updated_at
            row["updated_by"] = actor_id
            cleared += 1
        updated.append(row)
    return updated, cleared


def make_order_review_mezan_image_unlink_router(
    db: Any,
    current_user: Callable,
) -> APIRouter:
    router = APIRouter(tags=["order-review-mezan-image-unlink"])
    repository = MongoOrderRepository(db)

    async def resolve_item(
        order_number: str,
        order_item_id: str,
        user: dict,
    ) -> tuple[str, str, Any, Any]:
        reviewer = base._require_reviewer(user)
        user_id = base._merchant_user_id(reviewer)
        actor_id = base._text(reviewer.get("id"))
        try:
            order = await get_order(
                repository,
                user_id=user_id,
                order_number=order_number,
            )
        except OrderNotFoundError as exc:
            raise HTTPException(
                status_code=404,
                detail={"code": "order_not_found"},
            ) from exc
        identities = await base._review_item_identities(db, user_id, order)
        target = next(
            (item for item in identities if item.order_item_id == order_item_id),
            None,
        )
        if target is None:
            raise HTTPException(
                status_code=404,
                detail={"code": "order_item_not_found"},
            )
        return user_id, actor_id, order, target

    @router.post(
        "/order-reviews-v1/{order_number}/items/{order_item_id:path}/"
        "mezan-images/{image_id}/unlink-and-delete"
    )
    async def unlink_and_delete_mezan_image(
        order_number: str,
        order_item_id: str,
        image_id: str,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        user_id, actor_id, order, target = await resolve_item(
            order_number,
            order_item_id,
            user,
        )
        product_key, _, _ = base.build_image_preference_identity(target)
        image = await db[MEZAN_IMAGES].find_one(
            {
                "user_id": user_id,
                "id": image_id,
                "product_key": product_key,
                "deleted_at": {"$exists": False},
            },
            {"_id": 0},
        )
        if not image:
            raise HTTPException(
                status_code=404,
                detail={"code": "mezan_image_not_found"},
            )

        image_url = mezan_image_url(image_id)

        other_order = await db[base.WORKFLOWS].find_one(
            {
                "user_id": user_id,
                "order_number": {"$ne": order.order_number},
                "items.selected_image_url": image_url,
            },
            {"_id": 0, "order_number": 1, "stage": 1},
        )
        if other_order:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "mezan_image_linked_to_other_order",
                    "message": (
                        "الصورة مستخدمة في طلب آخر ولا يمكن حذفها تلقائيًا. "
                        "افتح الطلب الآخر واختر له صورة بديلة أولًا."
                    ),
                    "order_number": base._text(other_order.get("order_number")),
                },
            )

        workflow = await db[base.WORKFLOWS].find_one(
            {"user_id": user_id, "order_number": order.order_number},
            {"_id": 0},
        )
        if (
            workflow
            and workflow.get("stage") in base.REVIEW_COMPLETED_STAGES
            and workflow_uses_image(workflow, image_url)
        ):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "review_already_completed",
                    "message": (
                        "الصورة مستخدمة في مراجعة معتمدة. لا يمكن فك ارتباطها "
                        "من هذه المرحلة."
                    ),
                },
            )

        now = base._now()
        cleared_items = 0
        if workflow and workflow_uses_image(workflow, image_url):
            next_items, cleared_items = clear_image_from_workflow_items(
                workflow.get("items") or [],
                image_url,
                actor_id=actor_id,
                updated_at=now,
            )
            revision = int(workflow.get("revision") or 0)
            result = await db[base.WORKFLOWS].update_one(
                {
                    "user_id": user_id,
                    "order_number": order.order_number,
                    "revision": revision,
                },
                {
                    "$set": {
                        "items": next_items,
                        "revision": revision + 1,
                        "updated_at": now,
                        "updated_by": actor_id,
                    }
                },
            )
            if not result.matched_count:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "review_revision_conflict",
                        "message": "تغير الطلب أثناء فك الارتباط؛ حدّث الصفحة وأعد المحاولة.",
                    },
                )

        preference_result = await db[base.PREFERENCES].delete_many(
            {
                "user_id": user_id,
                "product_key": product_key,
                "selected_image_url": image_url,
            }
        )

        remaining_references = await db[base.WORKFLOWS].count_documents(
            {"user_id": user_id, "items.selected_image_url": image_url}
        )
        if remaining_references:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "mezan_image_still_in_use",
                    "message": "بقي ارتباط آخر بالصورة؛ حدّث الصفحة واختر صورة بديلة ثم أعد الحذف.",
                },
            )

        delete_result = await db[MEZAN_IMAGES].update_one(
            {
                "user_id": user_id,
                "id": image_id,
                "product_key": product_key,
                "deleted_at": {"$exists": False},
            },
            {
                "$set": {
                    "deleted_at": now,
                    "deleted_by": actor_id,
                    "unlink_deleted_at": now,
                },
                "$unset": {"data_base64": ""},
            },
        )
        if not delete_result.matched_count:
            raise HTTPException(
                status_code=404,
                detail={"code": "mezan_image_not_found"},
            )

        preference_count = int(
            getattr(preference_result, "deleted_count", 0) or 0
        )
        await db[base.EVENTS].insert_one(
            {
                "user_id": user_id,
                "order_number": order.order_number,
                "order_item_id": order_item_id,
                "event_type": "review_mezan_image_unlinked_and_deleted",
                "image_id": image_id,
                "cleared_item_links": cleared_items,
                "cleared_preference_links": preference_count,
                "occurred_at": now,
                "actor_id": actor_id,
            }
        )
        return await base._detail(db, user_id, order)

    return router
