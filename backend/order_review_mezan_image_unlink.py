"""Global unlink-and-delete flow for Mezan-only review images.

Deleting a Mezan image is an explicit destructive action. Once confirmed, the
image is removed from every review workflow and every product image preference
that references it. Review items then have no manual Mezan selection, so the
normal order-review detail contract falls back to the original Salla image.
Salla images themselves are never modified or deleted.
"""
from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException

import order_review_routes as base
from order_engine.repository import MongoOrderRepository
from order_engine.service import OrderNotFoundError, get_order


MEZAN_IMAGES = "order_review_mezan_images"
MEZAN_IMAGE_PREFIX = "/api/order-reviews-v1/mezan-images/"
MAX_GLOBAL_UNLINK_PASSES = 6
MAX_AFFECTED_ORDERS_IN_EVENT = 500


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


def build_workflow_clear_plans(
    workflows: Any,
    image_url: str,
    *,
    actor_id: str,
    updated_at: Any,
) -> tuple[list[dict[str, Any]], int]:
    """Build CAS updates for every workflow using the image, regardless of stage."""
    plans: list[dict[str, Any]] = []
    total_cleared = 0
    for workflow in workflows or []:
        if not isinstance(workflow, dict):
            continue
        next_items, cleared = clear_image_from_workflow_items(
            workflow.get("items") or [],
            image_url,
            actor_id=actor_id,
            updated_at=updated_at,
        )
        if not cleared:
            continue
        raw_revision = workflow.get("revision")
        plans.append({
            "order_number": base._text(workflow.get("order_number")),
            "stage": base._text(workflow.get("stage")),
            "revision_present": "revision" in workflow,
            "expected_revision": raw_revision,
            "next_revision": int(raw_revision or 0) + 1,
            "items": next_items,
            "cleared_items": cleared,
        })
        total_cleared += cleared
    return plans, total_cleared


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

    async def clear_all_workflow_references(
        *,
        user_id: str,
        image_url: str,
        actor_id: str,
        updated_at: Any,
    ) -> tuple[int, list[str]]:
        """Retry optimistic updates until no workflow references remain."""
        total_cleared = 0
        affected_orders: set[str] = set()

        for _ in range(MAX_GLOBAL_UNLINK_PASSES):
            workflows = await db[base.WORKFLOWS].find(
                {
                    "user_id": user_id,
                    "items.selected_image_url": image_url,
                },
                {
                    "_id": 0,
                    "order_number": 1,
                    "stage": 1,
                    "revision": 1,
                    "items": 1,
                },
            ).to_list(5000)
            if not workflows:
                break

            plans, _ = build_workflow_clear_plans(
                workflows,
                image_url,
                actor_id=actor_id,
                updated_at=updated_at,
            )
            progressed = False
            for plan in plans:
                order_number = plan["order_number"]
                if not order_number:
                    continue
                query: dict[str, Any] = {
                    "user_id": user_id,
                    "order_number": order_number,
                    "items.selected_image_url": image_url,
                }
                if plan["revision_present"]:
                    query["revision"] = plan["expected_revision"]
                else:
                    query["revision"] = {"$exists": False}

                result = await db[base.WORKFLOWS].update_one(
                    query,
                    {
                        "$set": {
                            "items": plan["items"],
                            "revision": plan["next_revision"],
                            "updated_at": updated_at,
                            "updated_by": actor_id,
                            "last_image_fallback_source": "salla",
                        }
                    },
                )
                if result.matched_count:
                    progressed = True
                    total_cleared += int(plan["cleared_items"] or 0)
                    affected_orders.add(order_number)

            # A concurrent editor may have won one revision. Re-read and retry.
            if not progressed:
                break

        remaining = await db[base.WORKFLOWS].count_documents(
            {"user_id": user_id, "items.selected_image_url": image_url}
        )
        if remaining:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "mezan_image_global_unlink_conflict",
                    "message": (
                        "تغير أحد الطلبات أثناء حذف الصورة. حدّث الصفحة وأعد "
                        "المحاولة ليُستكمل فك الارتباط تلقائيًا."
                    ),
                    "remaining_order_count": int(remaining),
                },
            )
        return total_cleared, sorted(affected_orders)

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
        now = base._now()

        # Clear every order, including approved/advanced workflows, as requested.
        # Removing selected_image_url makes the normal detail mapper use Salla's
        # original product image without modifying any Salla media.
        cleared_items, affected_orders = await clear_all_workflow_references(
            user_id=user_id,
            image_url=image_url,
            actor_id=actor_id,
            updated_at=now,
        )

        preference_result = await db[base.PREFERENCES].delete_many(
            {
                "user_id": user_id,
                "selected_image_url": image_url,
            }
        )

        # Protect against a new concurrent reference created after the sweep.
        remaining_references = await db[base.WORKFLOWS].count_documents(
            {"user_id": user_id, "items.selected_image_url": image_url}
        )
        if remaining_references:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "mezan_image_global_unlink_conflict",
                    "message": (
                        "ظهر ارتباط جديد أثناء الحذف. حدّث الصفحة وأعد المحاولة "
                        "وسيُعاد الطلب تلقائيًا إلى صورة سلة."
                    ),
                    "remaining_order_count": int(remaining_references),
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
                    "fallback_image_source": "salla",
                    "affected_order_count": len(affected_orders),
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
                "event_type": "review_mezan_image_globally_unlinked_and_deleted",
                "image_id": image_id,
                "fallback_image_source": "salla",
                "cleared_item_links": cleared_items,
                "cleared_preference_links": preference_count,
                "affected_order_count": len(affected_orders),
                "affected_order_numbers": affected_orders[
                    :MAX_AFFECTED_ORDERS_IN_EVENT
                ],
                "occurred_at": now,
                "actor_id": actor_id,
            }
        )
        return await base._detail(db, user_id, order)

    return router
