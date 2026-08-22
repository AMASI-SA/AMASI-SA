"""Mezan-only customer-review waiting queue for stage-one order review."""
from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from pymongo.errors import DuplicateKeyError

from order_engine.repository import MongoOrderRepository
from order_engine.service import OrderNotFoundError, get_order
from order_review_routes import (
    EVENTS,
    REVIEW_COMPLETED_STAGES,
    WORKFLOWS,
    _merchant_user_id,
    _now,
    _require_reviewer,
    _text,
)


WAITING_CUSTOMER_REVIEW_STAGE = "waiting_customer_review"
PENDING_REVIEW_STAGE = "pending_review"
CURRENT_REVIEW_STATUSES = {
    "under review",
    "waiting review",
    "pending review",
    "بإنتظار المراجعة",
    "بانتظار المراجعة",
    "انتظار المراجعة",
}


def _is_currently_pending_review(order: Any) -> bool:
    """Keep the Mezan waiting queue subordinate to the real order status."""
    for value in (getattr(order, "status", None), getattr(order, "status_native", None)):
        normalized = " ".join(_text(value).casefold().replace("_", " ").split())
        if normalized in CURRENT_REVIEW_STATUSES:
            return True
    return False


class CustomerWaitingStageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=0)


def customer_waiting_summary(
    order: Any,
    workflow: dict[str, Any],
) -> dict[str, Any]:
    payload = order.model_dump(mode="json")
    payload.update({
        "stage": WAITING_CUSTOMER_REVIEW_STAGE,
        "revision": int(workflow.get("revision") or 0),
        "waiting_customer_review_at": workflow.get(
            "waiting_customer_review_at"
        ),
        "waiting_customer_review_by": workflow.get(
            "waiting_customer_review_by"
        ),
        "waiting_customer_review_by_name": workflow.get(
            "waiting_customer_review_by_name"
        ),
    })
    return payload


def _stage_transition_document(
    *,
    workflow: dict[str, Any] | None,
    user_id: str,
    order: Any,
    stage: str,
    actor: dict[str, Any],
) -> dict[str, Any]:
    current = dict(workflow or {})
    current.pop("_id", None)
    revision = int(current.get("revision") or 0)
    now = _now()
    actor_id = _text(actor.get("id"))
    actor_name = _text(actor.get("name") or actor.get("email"))

    current.update({
        "user_id": user_id,
        "order_number": order.order_number,
        "order_id": order.order_id,
        "stage": stage,
        "revision": revision + 1,
        "updated_at": now,
        "updated_by": actor_id,
    })
    current.setdefault("items", [])
    current.setdefault("operational_items", [])
    current.setdefault("created_at", now)

    if stage == WAITING_CUSTOMER_REVIEW_STAGE:
        current.update({
            "waiting_customer_review_at": now,
            "waiting_customer_review_by": actor_id,
            "waiting_customer_review_by_name": actor_name,
        })
    else:
        current.update({
            "customer_review_resumed_at": now,
            "customer_review_resumed_by": actor_id,
            "customer_review_resumed_by_name": actor_name,
        })
    return current


async def _replace_stage(
    db: Any,
    *,
    workflow: dict[str, Any] | None,
    new_doc: dict[str, Any],
) -> None:
    revision = int((workflow or {}).get("revision") or 0)
    selector = {
        "user_id": new_doc["user_id"],
        "order_number": new_doc["order_number"],
    }
    if workflow:
        result = await db[WORKFLOWS].replace_one(
            {**selector, "revision": revision},
            new_doc,
        )
        if not result.matched_count:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "review_revision_conflict",
                    "message": "عدّل موظف آخر الطلب؛ حدّث البيانات ثم أعد المحاولة.",
                },
            )
        return
    try:
        await db[WORKFLOWS].insert_one(new_doc)
    except DuplicateKeyError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "review_revision_conflict"},
        ) from exc


def make_order_review_customer_waiting_router(
    db: Any,
    current_user: Callable,
) -> APIRouter:
    router = APIRouter(
        prefix="/order-review-customer-waiting-v1",
        tags=["order-review-customer-waiting"],
    )
    repository = MongoOrderRepository(db)

    @router.get("")
    async def list_customer_waiting_reviews(
        limit: int = Query(100, ge=1, le=250),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        reviewer = _require_reviewer(user)
        user_id = _merchant_user_id(reviewer)
        workflows = await db[WORKFLOWS].find(
            {
                "user_id": user_id,
                "stage": WAITING_CUSTOMER_REVIEW_STAGE,
            },
            {"_id": 0},
        ).sort("waiting_customer_review_at", -1).limit(limit).to_list(limit)

        items = []
        for workflow in workflows:
            order_number = _text(workflow.get("order_number"))
            if not order_number:
                continue
            try:
                order = await get_order(
                    repository,
                    user_id=user_id,
                    order_number=order_number,
                )
            except OrderNotFoundError:
                continue
            if not _is_currently_pending_review(order):
                continue
            items.append(customer_waiting_summary(order, workflow))
        return {"count": len(items), "items": items}

    @router.post("/{order_number}/wait")
    async def move_to_customer_waiting(
        order_number: str,
        payload: CustomerWaitingStageRequest,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        reviewer = _require_reviewer(user)
        user_id = _merchant_user_id(reviewer)
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

        workflow = await db[WORKFLOWS].find_one(
            {"user_id": user_id, "order_number": order.order_number},
            {"_id": 0},
        )
        stage = _text((workflow or {}).get("stage")) or PENDING_REVIEW_STAGE
        if stage in REVIEW_COMPLETED_STAGES:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "review_already_completed",
                    "message": "تم اعتماد مراجعة الطلب ولا يمكن نقله إلى انتظار مراجعة العميل.",
                },
            )
        revision = int((workflow or {}).get("revision") or 0)
        if revision != payload.expected_revision:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "review_revision_conflict",
                    "message": "حدّث بيانات الطلب قبل تغيير حالته.",
                },
            )
        if stage == WAITING_CUSTOMER_REVIEW_STAGE:
            return {
                "ok": True,
                "already_waiting": True,
                "order_number": order.order_number,
                "stage": stage,
                "revision": revision,
            }

        new_doc = _stage_transition_document(
            workflow=workflow,
            user_id=user_id,
            order=order,
            stage=WAITING_CUSTOMER_REVIEW_STAGE,
            actor=reviewer,
        )
        await _replace_stage(db, workflow=workflow, new_doc=new_doc)
        await db[EVENTS].insert_one({
            "user_id": user_id,
            "order_number": order.order_number,
            "event_type": "order_waiting_customer_review",
            "occurred_at": new_doc["updated_at"],
            "actor_id": _text(reviewer.get("id")),
        })
        return {
            "ok": True,
            "order_number": order.order_number,
            "stage": WAITING_CUSTOMER_REVIEW_STAGE,
            "revision": new_doc["revision"],
            "mezan_only": True,
            "salla_updated": False,
            "qoyod_updated": False,
        }

    @router.post("/{order_number}/resume")
    async def resume_pending_review(
        order_number: str,
        payload: CustomerWaitingStageRequest,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        reviewer = _require_reviewer(user)
        user_id = _merchant_user_id(reviewer)
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

        workflow = await db[WORKFLOWS].find_one(
            {"user_id": user_id, "order_number": order.order_number},
            {"_id": 0},
        )
        if not workflow or _text(workflow.get("stage")) != WAITING_CUSTOMER_REVIEW_STAGE:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "order_not_waiting_customer_review",
                    "message": "الطلب ليس في انتظار مراجعة العميل.",
                },
            )
        revision = int(workflow.get("revision") or 0)
        if revision != payload.expected_revision:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "review_revision_conflict",
                    "message": "حدّث بيانات الطلب قبل تغيير حالته.",
                },
            )

        new_doc = _stage_transition_document(
            workflow=workflow,
            user_id=user_id,
            order=order,
            stage=PENDING_REVIEW_STAGE,
            actor=reviewer,
        )
        await _replace_stage(db, workflow=workflow, new_doc=new_doc)
        await db[EVENTS].insert_one({
            "user_id": user_id,
            "order_number": order.order_number,
            "event_type": "order_customer_review_resumed",
            "occurred_at": new_doc["updated_at"],
            "actor_id": _text(reviewer.get("id")),
        })
        return {
            "ok": True,
            "order_number": order.order_number,
            "stage": PENDING_REVIEW_STAGE,
            "revision": new_doc["revision"],
            "mezan_only": True,
            "salla_updated": False,
            "qoyod_updated": False,
        }

    return router
