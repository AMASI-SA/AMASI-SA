"""Accountant review for store-driver non-cash collection evidence.

The review queue is sourced from ``store_delivery_payment_reviews`` created by
the driver app. Approval atomically posts a verified native settlement and updates
the review. Rejection has no financial effect. Salla payment fields stay authoritative.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException, Query, status
from accounting_financial_identity import list_financial_accounts
from accounting_onboarding_ssot import FACTS

from store_delivery_domain import normalize_text
from store_delivery_driver_app_routes import DRIVER_PAYMENT_REVIEWS
from store_delivery_driver_routes import STORE_DRIVERS
from store_delivery_handover_routes import ASSIGNMENTS
from accounting_driver_payment_review import DriverReviewInput as ReviewPayload, PosBankInput

PAYMENT_EVENTS = "store_delivery_payment_review_events"


def _merchant_user_id(user: dict[str, Any]) -> str:
    if normalize_text(user.get("role")).casefold() == "owner" or user.get("is_owner") is True:
        return normalize_text(user.get("id"))
    owner_id = normalize_text(user.get("created_by"))
    if not owner_id:
        raise HTTPException(status_code=409, detail={"code": "employee_store_not_linked"})
    return owner_id


async def _require_accountant(db: Any, user: Any) -> dict[str, Any]:
    if not isinstance(user, dict):
        raise HTTPException(status_code=403, detail={"code": "accountant_permission_required"})

    actor = dict(user)
    mobile_actor_id = normalize_text(user.get("_mobile_actor_id"))
    if mobile_actor_id:
        owner_id = normalize_text(user.get("_mobile_owner_id") or user.get("id"))
        stored = await db.users.find_one(
            {"id": mobile_actor_id},
            {
                "_id": 0,
                "id": 1,
                "name": 1,
                "email": 1,
                "role": 1,
                "is_owner": 1,
                "created_by": 1,
                "extra_permissions": 1,
                "denied_permissions": 1,
                "is_active": 1,
                "disabled": 1,
            },
        )
        if not stored or stored.get("disabled") is True or stored.get("is_active") is False:
            raise HTTPException(status_code=403, detail={"code": "accountant_permission_required"})
        linked_owner = normalize_text(stored.get("created_by"))
        if not linked_owner:
            employee = await db["mezan_employees_v2"].find_one(
                {"account_user_id": mobile_actor_id},
                {"_id": 0, "user_id": 1},
            )
            linked_owner = normalize_text((employee or {}).get("user_id"))
        if not owner_id or linked_owner != owner_id:
            raise HTTPException(status_code=403, detail={"code": "accountant_permission_required"})
        actor = {**stored, "created_by": owner_id, "_native_mobile_actor": True}

    role = normalize_text(actor.get("role")).casefold()
    permission = "store_delivery.payments.review"
    native_permission = "app.action.couriers.payment_review"
    mobile_permissions = set(user.get("_mobile_app_permissions") or [])
    allowed = (
        role in {"owner", "admin", "accountant"}
        or actor.get("is_owner") is True
        or permission in set(actor.get("extra_permissions") or [])
        or (mobile_actor_id and native_permission in mobile_permissions)
    ) and permission not in set(actor.get("denied_permissions") or [])
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "accountant_permission_required"},
        )
    return actor


async def ensure_store_delivery_payment_review_indexes(db: Any) -> None:
    await db[PAYMENT_EVENTS].create_index([("user_id", 1), ("assignment_id", 1), ("occurred_at", -1)])
    await db[DRIVER_PAYMENT_REVIEWS].create_index([("user_id", 1), ("assignment_id", 1)], unique=True)


def make_store_delivery_payment_review_router(db: Any, current_user: Callable[..., Any]) -> APIRouter:
    router = APIRouter(prefix="/store-delivery/payment-review", tags=["Store Delivery Payment Review"])

    @router.get("/pending")
    async def pending_reviews(
        method: str | None = Query(default=None),
        driver_id: str | None = Query(default=None),
        limit: int = Query(default=250, ge=1, le=1000),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        actor = await _require_accountant(db, user)
        user_id = _merchant_user_id(actor)
        query: dict[str, Any] = {"user_id": user_id, "status": "pending"}
        if method:
            query["payment_method"] = normalize_text(method)
        if driver_id:
            query["driver_id"] = normalize_text(driver_id)
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

    @router.get("/{assignment_id}/approval-options")
    async def approval_options(
        assignment_id: str,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        actor = await _require_accountant(db, user)
        user_id = _merchant_user_id(actor)
        review = await db[DRIVER_PAYMENT_REVIEWS].find_one(
            {
                "user_id": user_id,
                "assignment_id": normalize_text(assignment_id),
                "status": "pending",
            },
            {"_id": 0},
        )
        if not review:
            raise HTTPException(
                status_code=404,
                detail={"code": "store_delivery_payment_review_not_found"},
            )
        method = normalize_text(review.get("payment_method"))
        amount = str(review.get("amount") or "")
        result: dict[str, Any] = {
            "assignment_id": review.get("assignment_id"),
            "payment_method": method,
            "amount": review.get("amount"),
            "bank_account_id": review.get("bank_account_id"),
            "bank_name_snapshot": review.get("bank_name_snapshot"),
            "pos_receivables": [],
            "bank_movements": [],
        }
        if method == "card_terminal":
            rows = await db[FACTS].find(
                {
                    "user_id": user_id,
                    "status": "active",
                    "category": "other_receivable",
                    "source": "documented_opening_fact",
                },
                {
                    "_id": 0,
                    "id": 1,
                    "display_name": 1,
                    "reference": 1,
                    "currency": 1,
                    "cutover_date": 1,
                    "evidence": 1,
                },
            ).sort("display_name", 1).to_list(500)
            result["pos_receivables"] = [
                row for row in rows
                if row.get("currency") == "SAR" and normalize_text(row.get("id"))
            ]
        elif method == "bank_transfer":
            bank_id = normalize_text(review.get("bank_account_id"))
            if not bank_id:
                result["blocker"] = "business_bank_account_required"
                return result
            accounts = await list_financial_accounts(
                db,
                user_id,
                account_types=("bank",),
                currency="SAR",
            )
            bank = next((row for row in accounts if row.get("id") == bank_id), None)
            if not bank:
                result["blocker"] = "MZ2_LINK_REQUIRED"
                return result
            try:
                expected = Decimal(amount)
            except (InvalidOperation, ValueError):
                expected = Decimal("-1")
            candidates = await db.mz2_daily_movements.find(
                {
                    "user_id": user_id,
                    "bank_account_id": bank_id,
                    "status": "unclassified",
                    "direction": "in",
                    "currency": "SAR",
                    "source": {
                        "$in": [
                            "bank_statement_import",
                            "manual_reconciled_bank_statement",
                        ]
                    },
                },
                {
                    "_id": 0,
                    "id": 1,
                    "movement_date": 1,
                    "amount": 1,
                    "description": 1,
                    "reference": 1,
                    "bank_account_id": 1,
                    "status": 1,
                    "source": 1,
                    "receipt_id": 1,
                    "accounting_event_id": 1,
                    "confirmed_provider": 1,
                    "explicit_provider": 1,
                    "suggested_provider": 1,
                },
            ).sort([("movement_date", -1), ("created_at", -1)]).limit(500).to_list(500)
            safe: list[dict[str, Any]] = []
            for row in candidates:
                try:
                    row_amount = Decimal(str(row.get("amount")))
                except (InvalidOperation, ValueError):
                    continue
                if (
                    row_amount != expected
                    or any(
                        row.get(key)
                        for key in (
                            "receipt_id",
                            "accounting_event_id",
                            "confirmed_provider",
                            "explicit_provider",
                            "suggested_provider",
                        )
                    )
                ):
                    continue
                safe.append({
                    key: row.get(key)
                    for key in (
                        "id",
                        "movement_date",
                        "amount",
                        "description",
                        "reference",
                        "bank_account_id",
                    )
                })
            result["bank_movements"] = safe
        else:
            result["blocker"] = "payment_review_not_required"
        return result

    @router.get("/bank-accounts")
    async def official_bank_accounts(user: dict = Depends(current_user)) -> dict[str, Any]:
        actor = await _require_accountant(db, user)
        user_id = _merchant_user_id(actor)
        accounts = await list_financial_accounts(db, user_id, account_types=("bank",), currency="SAR")
        items = [{key: row.get(key) for key in ("id", "name", "account_type", "currency", "status")}
                 for row in accounts]
        return {"items": items, "total": len(items), "source": "mz2_financial_accounts"}

    @router.post("/{assignment_id}")
    async def review_payment(
        assignment_id: str,
        payload: ReviewPayload,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        actor = await _require_accountant(db, user)
        user_id = _merchant_user_id(actor)
        if payload.decision == "rejected" and not normalize_text(payload.note):
            raise HTTPException(
                status_code=422,
                detail={"code": "payment_review_rejection_reason_required"},
            )
        from accounting_driver_payment_review import review_driver_payment
        from accounting_ledger_v2 import AccountingLedgerV2Error
        try:
            return await review_driver_payment(db, owner=user_id, actor_id=normalize_text(actor.get("id")),
                                               assignment_id=assignment_id, payload=payload)
        except AccountingLedgerV2Error as exc:
            raise HTTPException(409, detail={"code": exc.code}) from exc

    @router.post("/{assignment_id}/pos-bank-settlement")
    async def pos_bank_settlement(assignment_id: str, payload: PosBankInput,
                                  user: dict = Depends(current_user)) -> dict[str, Any]:
        actor = await _require_accountant(db, user)
        from accounting_driver_payment_review import settle_pos_to_bank
        from accounting_ledger_v2 import AccountingLedgerV2Error
        try:
            return await settle_pos_to_bank(db, owner=_merchant_user_id(actor), actor_id=normalize_text(actor.get("id")),
                                            assignment_id=assignment_id, payload=payload)
        except AccountingLedgerV2Error as exc:
            raise HTTPException(409, detail={"code": exc.code}) from exc

    return router


__all__ = [
    "ReviewPayload",
    "ensure_store_delivery_payment_review_indexes",
    "make_store_delivery_payment_review_router",
]
