"""Driver resubmission flow for rejected non-cash payment evidence."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from store_delivery_domain import normalize_text
from store_delivery_driver_app_routes import DRIVER_COLLECTIONS, DRIVER_PAYMENT_REVIEWS
from store_delivery_driver_routes import STORE_DRIVERS
from store_delivery_handover_routes import ASSIGNMENTS, ORDERS
from store_delivery_payment_evidence_routes import RECEIPTS, validate_receipt_reference


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _driver_for_user(db: Any, user: dict[str, Any]) -> dict[str, Any]:
    if normalize_text(user.get("role")).casefold() != "store_driver":
        raise HTTPException(status_code=403, detail={"code": "store_driver_account_required"})
    owner_id = normalize_text(user.get("created_by"))
    driver = await db[STORE_DRIVERS].find_one(
        {"user_id": owner_id, "account_user_id": normalize_text(user.get("id")), "status": "active"},
        {"_id": 0},
    )
    if not driver:
        raise HTTPException(status_code=403, detail={"code": "store_driver_profile_not_linked"})
    return driver


class ResubmitPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    receipt_reference: str = Field(min_length=1, max_length=500)
    bank_account_id: str | None = Field(default=None, max_length=120)


def make_store_delivery_payment_resubmission_router(db: Any, current_user: Callable[..., Any]) -> APIRouter:
    router = APIRouter(prefix="/store-delivery/app/payment-review", tags=["Amasi Delivery Payment Resubmission"])

    @router.post("/{assignment_id}/resubmit")
    async def resubmit(assignment_id: str, payload: ResubmitPayload,
                       user: dict = Depends(current_user)) -> dict[str, Any]:
        driver = await _driver_for_user(db, user)
        user_id = normalize_text(driver.get("user_id"))
        assignment = await db[ASSIGNMENTS].find_one(
            {"user_id": user_id, "id": assignment_id, "driver_id": driver["id"], "active": True, "status": "delivered"},
            {"_id": 0},
        )
        if not assignment:
            raise HTTPException(status_code=404, detail={"code": "driver_assignment_not_found"})
        review = await db[DRIVER_PAYMENT_REVIEWS].find_one(
            {"user_id": user_id, "assignment_id": assignment_id}, {"_id": 0}
        )
        if not review or review.get("status") != "rejected":
            raise HTTPException(status_code=409, detail={"code": "payment_review_not_rejected"})

        receipt = await validate_receipt_reference(
            db,
            user_id=user_id,
            driver_id=driver["id"],
            assignment_id=assignment_id,
            receipt_reference=payload.receipt_reference,
        )
        method = normalize_text(review.get("payment_method"))
        bank = None
        bank_account_id = normalize_text(payload.bank_account_id)
        if method == "bank_transfer":
            if not bank_account_id:
                raise HTTPException(status_code=422, detail={"code": "business_bank_account_required"})
            bank = await db.accounts.find_one(
                {"user_id": user_id, "id": bank_account_id, "account_type": "bank", "status": "active"},
                {"_id": 0, "id": 1, "name": 1, "provider": 1},
            )
            if not bank:
                raise HTTPException(status_code=422, detail={"code": "business_bank_account_invalid"})

        now = _now()
        revision = int(review.get("revision") or 1) + 1
        old_receipt = normalize_text(review.get("receipt_reference"))
        patch = {
            "status": "pending",
            "receipt_reference": receipt["token"],
            "receipt_url": f"/api/store-delivery/evidence/receipt/{receipt['token']}",
            "bank_account_id": bank_account_id if method == "bank_transfer" else "",
            "bank_name_snapshot": (bank or {}).get("name") or (bank or {}).get("provider"),
            "submitted_at": now,
            "resubmitted_at": now,
            "revision": revision,
            "reviewed_at": None,
            "reviewed_by": None,
            "review_note": "",
        }
        updated = await db[DRIVER_PAYMENT_REVIEWS].find_one_and_update(
            {"user_id": user_id, "assignment_id": assignment_id, "status": "rejected"},
            {"$set": patch},
            return_document=True,
            projection={"_id": 0, "user_id": 0},
        )
        if not updated:
            raise HTTPException(status_code=409, detail={"code": "payment_review_resubmit_conflict"})
        await db[DRIVER_COLLECTIONS].update_one(
            {"user_id": user_id, "assignment_id": assignment_id},
            {"$set": {
                "review_status": "pending_accountant_review",
                "payment_status": "pending_accountant_review",
                "payment_confirmed": False,
                "receipt_reference": receipt["token"],
                "receipt_url": patch["receipt_url"],
                "bank_account_id": patch["bank_account_id"],
                "bank_name_snapshot": patch["bank_name_snapshot"],
                "review_note": "",
            }},
        )
        await db[ASSIGNMENTS].update_one(
            {"user_id": user_id, "id": assignment_id},
            {"$set": {"payment_review_status": "pending_accountant_review", "payment_status": "pending_accountant_review", "updated_at": now}},
        )
        await db[ORDERS].update_one(
            {"user_id": user_id, "$or": [{"order_id": assignment.get("order_id")}, {"order_number": assignment.get("order_number")}]},
            {"$set": {"store_delivery_payment_status": "pending_accountant_review", "store_delivery_payment_review_status": "pending_accountant_review", "store_delivery_updated_at": now}},
        )
        await db[RECEIPTS].update_one(
            {"user_id": user_id, "token": receipt["token"]},
            {"$set": {"status": "bound", "bound_at": now, "review_status": None}},
        )
        if old_receipt and old_receipt != receipt["token"]:
            await db[RECEIPTS].update_one(
                {"user_id": user_id, "token": old_receipt},
                {"$set": {"status": "superseded", "superseded_at": now, "superseded_by": receipt["token"]}},
            )
        return updated

    return router


__all__ = ["make_store_delivery_payment_resubmission_router"]
