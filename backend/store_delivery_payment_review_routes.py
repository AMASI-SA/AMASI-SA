"""Accountant review for store-driver COD evidence.

Cash remains driver custody until settlement. Card-terminal and bank-transfer
receipts remain pending until an accountant approves or rejects them.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field

from store_delivery_domain import normalize_text
from store_delivery_driver_app_routes import (
    DRIVER_COLLECTIONS,
    DRIVER_PAYMENT_REVIEWS,
)

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
        items = await db[DRIVER_PAYMENT_REVIEWS].find(
            query,
            {"_id": 0, "user_id": 0},
        ).sort("submitted_at", 1).to_list(length=limit)
        return {"items": items, "total": len(items)}

    @router.get("/bank-accounts")
    async def official_bank_accounts(user: dict = Depends(current_user)) -> dict[str, Any]:
        actor = _require_accountant(user)
        user_id = _merchant_user_id(actor)
        items = await db.accounts.find(
            {"user_id": user_id, "account_type": "bank", "status": "active"},
            {
                "_id": 0,
                "id": 1,
                "name": 1,
                "provider": 1,
                "account_number": 1,
                "iban": 1,
                "status": 1,
            },
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
            raise HTTPException(status_code=404, detail={"code": "payment_review_not_found"})
        if review.get("status") != "pending":
            raise HTTPException(status_code=409, detail={"code": "payment_review_already_final"})
        if review.get("payment_method") not in {"card_terminal", "bank_transfer"}:
            raise HTTPException(status_code=409, detail={"code": "payment_review_not_required"})

        now = _now()
        approved = payload.decision == "approved"
        review_status = "approved" if approved else "rejected"
        review_patch = {
            "status": review_status,
            "reviewed_at": now,
            "reviewed_by": normalize_text(actor.get("id")),
            "review_note": normalize_text(payload.note),
        }
        updated_review = await db[DRIVER_PAYMENT_REVIEWS].find_one_and_update(
            {"user_id": user_id, "assignment_id": assignment_id, "status": "pending"},
            {"$set": review_patch},
            return_document=True,
            projection={"_id": 0, "user_id": 0},
        )
        if not updated_review:
            raise HTTPException(status_code=409, detail={"code": "payment_review_concurrent_update"})

        collection_patch = {
            "review_status": review_status,
            "payment_confirmed": approved,
            "payment_status": "paid" if approved else "payment_evidence_rejected",
            "reviewed_at": now,
            "reviewed_by": normalize_text(actor.get("id")),
            "review_note": normalize_text(payload.note),
        }
        await db[DRIVER_COLLECTIONS].update_one(
            {"user_id": user_id, "assignment_id": assignment_id},
            {"$set": collection_patch},
        )

        await db[PAYMENT_EVENTS].insert_one({
            "id": str(uuid.uuid4()),
            "user_id": user_id,
            "assignment_id": assignment_id,
            "order_id": review.get("order_id"),
            "driver_id": review.get("driver_id"),
            "payment_method": review.get("payment_method"),
            "amount": review.get("amount"),
            "decision": payload.decision,
            "note": normalize_text(payload.note),
            "actor_id": normalize_text(actor.get("id")),
            "occurred_at": now,
        })
        return updated_review

    return router


__all__ = [
    "ReviewPayload",
    "ensure_store_delivery_payment_review_indexes",
    "make_store_delivery_payment_review_router",
]
