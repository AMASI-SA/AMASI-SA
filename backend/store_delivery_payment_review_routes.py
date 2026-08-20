"""Accountant review for store-driver non-cash collection evidence.

The review queue is sourced from ``store_delivery_payment_reviews`` created by
the driver app. Approval/rejection updates the immutable operational projection
without writing into Salla-authoritative payment fields.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field

from store_delivery_domain import normalize_text
from store_delivery_driver_app_routes import DRIVER_COLLECTIONS, DRIVER_PAYMENT_REVIEWS
from store_delivery_driver_routes import STORE_DRIVERS
from store_delivery_handover_routes import ASSIGNMENTS, ORDERS
from store_delivery_payment_evidence_routes import RECEIPTS

PAYMENT_EVENTS = "store_delivery_payment_review_events"
ReviewDecision = Literal["approved", "rejected"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _merchant_user_id(user: dict[str, Any]) -> str:
    if normalize_text(user.get("role")).casefold() == "owner" or user.get("is_owner") is True:
        return normalize_text(user.get("id"))
    owner_id = normalize_text(user.get("created_by"))
    if not owner_id:
        raise HTTPException(status_code=409, detail={"code": "employee_store_not_linked"})
    return owner_id


def _require_accountant(user: Any) -> dict[str, Any]:
    if not isinstance(user, dict):
        raise HTTPException(status_code=403, detail={"code": "accountant_permission_required"})
    role = normalize_text(user.get("role")).casefold()
    permission = "store_delivery.payments.review"
    allowed = (
        role in {"owner", "admin", "accountant"}
        or user.get("is_owner") is True
        or permission in set(user.get("extra_permissions") or [])
    ) and permission not in set(user.get("denied_permissions") or [])
    if not allowed:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail={"code": "accountant_permission_required"})
    return user


class ReviewPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: ReviewDecision
    note: str = Field(default="", max_length=1000)


async def ensure_store_delivery_payment_review_indexes(db: Any) -> None:
    await db[PAYMENT_EVENTS].create_index([("user_id", 1), ("assignment_id", 1), ("occurred_at", -1)])
    await db[DRIVER_PAYMENT_REVIEWS].create_index([("user_id", 1), ("assignment_id", 1)], unique=True)


def make_store_delivery_payment_review_router(db: Any, current_user: Callable[..., Any]) -> APIRouter:
    router = APIRouter(prefix="/store-delivery/payment-review", tags=["Store Delivery Payment Review"])

    @router.get("/pending")
    async def pending_reviews(
        method: str | None = Query(default=None),
        limit: int = Query(default=250, ge=1, le=1000),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        actor = _require_accountant(user)
        user_id = _merchant_user_id(actor)
        query: dict[str, Any] = {"user_id": user_id, "status": "pending"}
        if method:
            query["payment_method"] = normalize_text(method)
        reviews = await db[DRIVER_PAYMENT_REVIEWS].find(
            query, {"_id": 0, "user_id": 0}
        ).sort("submitted_at", 1).to_list(length=limit)
        assignment_ids = [row.get("assignment_id") for row in reviews if row.get("assignment_id")]
        assignments = (
            await db[ASSIGNMENTS].find(
                {"user_id": user_id, "id": {"$in": assignment_ids}}, {"_id": 0, "user_id": 0}
            ).to_list(length=max(len(assignment_ids), 1))
            if assignment_ids else []
        )
        by_assignment = {row["id"]: row for row in assignments}
        driver_ids = list({row.get("driver_id") for row in reviews if row.get("driver_id")})
        drivers = (
            await db[STORE_DRIVERS].find(
                {"user_id": user_id, "id": {"$in": driver_ids}}, {"_id": 0, "id": 1, "name": 1, "phone": 1}
            ).to_list(length=max(len(driver_ids), 1))
            if driver_ids else []
        )
        by_driver = {row["id"]: row for row in drivers}
        items = []
        for review in reviews:
            assignment = by_assignment.get(review.get("assignment_id"), {})
            driver = by_driver.get(review.get("driver_id"), {})
            items.append({
                **review,
                "order_number": assignment.get("order_number") or review.get("order_number"),
                "delivery_status": assignment.get("status"),
                "delivered_at": assignment.get("delivered_at"),
                "driver_name": driver.get("name") or assignment.get("driver_name_snapshot") or review.get("driver_name_snapshot"),
                "driver_phone": driver.get("phone"),
            })
        return {"items": items, "total": len(items)}

    @router.get("/bank-accounts")
    async def official_bank_accounts(user: dict = Depends(current_user)) -> dict[str, Any]:
        actor = _require_accountant(user)
        user_id = _merchant_user_id(actor)
        items = await db.accounts.find(
            {"user_id": user_id, "account_type": "bank", "status": "active"},
            {"_id": 0, "id": 1, "name": 1, "provider": 1, "account_number": 1, "iban": 1, "status": 1},
        ).sort("name", 1).to_list(length=200)
        return {"items": items, "total": len(items), "source": "financial_center_accounts"}

    @router.post("/{assignment_id}")
    async def review_payment(
        assignment_id: str,
        payload: ReviewPayload,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        actor = _require_accountant(user)
        user_id = _merchant_user_id(actor)
        await ensure_store_delivery_payment_review_indexes(db)
        review = await db[DRIVER_PAYMENT_REVIEWS].find_one(
            {"user_id": user_id, "assignment_id": assignment_id}, {"_id": 0}
        )
        if not review:
            raise HTTPException(status_code=404, detail={"code": "store_delivery_payment_review_not_found"})
        if review.get("status") != "pending":
            raise HTTPException(status_code=409, detail={"code": "payment_review_already_final"})
        assignment = await db[ASSIGNMENTS].find_one(
            {"user_id": user_id, "id": assignment_id}, {"_id": 0}
        )
        if not assignment:
            raise HTTPException(status_code=404, detail={"code": "store_delivery_assignment_not_found"})
        if assignment.get("status") != "delivered":
            raise HTTPException(status_code=409, detail={"code": "delivery_not_completed"})
        if review.get("payment_method") not in {"card_terminal", "bank_transfer"}:
            raise HTTPException(status_code=409, detail={"code": "payment_review_not_required"})

        now = _now()
        approved = payload.decision == "approved"
        final_status = "approved" if approved else "rejected"
        updated_review = await db[DRIVER_PAYMENT_REVIEWS].find_one_and_update(
            {"user_id": user_id, "assignment_id": assignment_id, "status": "pending"},
            {"$set": {
                "status": final_status,
                "reviewed_at": now,
                "reviewed_by": normalize_text(actor.get("id")),
                "review_note": normalize_text(payload.note),
            }},
            return_document=True,
            projection={"_id": 0, "user_id": 0},
        )
        if not updated_review:
            raise HTTPException(status_code=409, detail={"code": "payment_review_concurrent_update"})

        payment_status = "paid" if approved else "payment_evidence_rejected"
        await db[DRIVER_COLLECTIONS].update_one(
            {"user_id": user_id, "assignment_id": assignment_id},
            {"$set": {
                "review_status": final_status,
                "reviewed_at": now,
                "reviewed_by": normalize_text(actor.get("id")),
                "review_note": normalize_text(payload.note),
                "payment_confirmed": approved,
                "payment_status": payment_status,
            }},
        )
        assignment_patch = {
            "payment_review_status": final_status,
            "payment_reviewed_at": now,
            "payment_reviewed_by": normalize_text(actor.get("id")),
            "payment_review_note": normalize_text(payload.note),
            "payment_method_snapshot": review.get("payment_method"),
            "payment_amount_snapshot": review.get("amount"),
            "payment_bank_account_id_snapshot": review.get("bank_account_id"),
            "payment_bank_name_snapshot": review.get("bank_name_snapshot"),
            "payment_confirmed": approved,
            "payment_status": payment_status,
            "updated_at": now,
        }
        await db[ASSIGNMENTS].update_one(
            {"user_id": user_id, "id": assignment_id}, {"$set": assignment_patch}
        )

        order_patch = {
            "store_delivery_payment_status": payment_status,
            "store_delivery_payment_method": review.get("payment_method"),
            "store_delivery_payment_amount": review.get("amount"),
            "store_delivery_payment_confirmed": approved,
            "store_delivery_payment_review_status": final_status,
            "store_delivery_payment_review_note": normalize_text(payload.note),
            "store_delivery_payment_reviewed_at": now,
            "store_delivery_payment_reviewed_by": normalize_text(actor.get("id")),
        }
        if review.get("bank_account_id"):
            order_patch["store_delivery_bank_account_id"] = review.get("bank_account_id")
            order_patch["store_delivery_bank_name"] = review.get("bank_name_snapshot")
        await db[ORDERS].update_one(
            {
                "user_id": user_id,
                "$or": [
                    {"order_id": assignment.get("order_id")},
                    {"order_number": assignment.get("order_number")},
                ],
            },
            {"$set": order_patch},
        )

        receipt_reference = normalize_text(review.get("receipt_reference"))
        if receipt_reference:
            await db[RECEIPTS].update_one(
                {"user_id": user_id, "token": receipt_reference},
                {"$set": {
                    "review_status": final_status,
                    "reviewed_at": now,
                    "reviewed_by": normalize_text(actor.get("id")),
                    "review_note": normalize_text(payload.note),
                }},
            )

        await db[PAYMENT_EVENTS].insert_one({
            "id": str(uuid.uuid4()),
            "user_id": user_id,
            "assignment_id": assignment_id,
            "order_id": assignment.get("order_id"),
            "driver_id": assignment.get("driver_id"),
            "decision": payload.decision,
            "note": normalize_text(payload.note),
            "payment_method": review.get("payment_method"),
            "amount": review.get("amount"),
            "actor_id": normalize_text(actor.get("id")),
            "occurred_at": now,
        })
        return {
            "assignment_id": assignment_id,
            "decision": payload.decision,
            "payment_status": payment_status,
            "review": updated_review,
        }

    return router


__all__ = [
    "ReviewPayload",
    "ensure_store_delivery_payment_review_indexes",
    "make_store_delivery_payment_review_router",
]
