"""Restricted API surface for the standalone Amasi Delivery driver app.

A ``store_driver`` sees only assignments linked to their own driver profile and
can only transition assigned -> out_for_delivery -> delivered. Customer COD is
always read from the canonical unified order at the moment of delivery; a driver
can never type or override the amount owed.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from store_delivery_domain import (
    DELIVERY_STATUS_ASSIGNED,
    DELIVERY_STATUS_DELIVERED,
    DELIVERY_STATUS_OUT_FOR_DELIVERY,
    PAYMENT_METHOD_BANK_TRANSFER,
    PAYMENT_METHOD_CARD_TERMINAL,
    StoreDeliveryRuleError,
    collection_requirements,
    driver_earning,
    normalize_text,
)
from store_delivery_driver_routes import STORE_DRIVERS
from store_delivery_handover_routes import ASSIGNMENTS, EVENTS, ORDERS
from store_delivery_payment_evidence_routes import (
    RECEIPTS,
    authoritative_outstanding_amount,
    canonical_order_for_assignment,
    validate_receipt_reference,
)

DRIVER_EARNINGS = "store_delivery_driver_earnings"
DRIVER_COLLECTIONS = "store_delivery_collections"
DRIVER_PAYMENT_REVIEWS = "store_delivery_payment_reviews"
DRIVER_SETTLEMENTS = "store_delivery_driver_settlements"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _require_store_driver(user: Any) -> dict[str, Any]:
    if not isinstance(user, dict) or normalize_text(user.get("role")).casefold() != "store_driver":
        raise HTTPException(status_code=403, detail={"code": "store_driver_account_required"})
    return user


async def _driver_for_user(db: Any, user: dict[str, Any]) -> dict[str, Any]:
    owner_id = normalize_text(user.get("created_by"))
    row = await db[STORE_DRIVERS].find_one(
        {
            "user_id": owner_id,
            "account_user_id": normalize_text(user.get("id")),
            "status": "active",
        },
        {"_id": 0},
    )
    if not row:
        raise HTTPException(status_code=403, detail={"code": "store_driver_profile_not_linked"})
    return row


def _merchant_id(driver: dict[str, Any]) -> str:
    return normalize_text(driver.get("user_id"))


def _barcode_match(value: str) -> list[dict[str, Any]]:
    value = normalize_text(value)
    return [
        {"order_number": value},
        {"order_id": value},
        {"barcode": value},
        {"shipping_barcode": value},
        {"tracking_number": value},
    ]


class DriverStatusUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    barcode: str = Field(min_length=1, max_length=180)
    target_status: str
    # Deprecated input retained for backward compatibility only. It is ignored.
    outstanding_amount: float | None = Field(default=None, ge=0, le=1_000_000)
    payment_method: str | None = None
    receipt_reference: str | None = Field(default=None, max_length=500)
    bank_account_id: str | None = Field(default=None, max_length=120)


async def ensure_store_delivery_driver_app_indexes(db: Any) -> None:
    await db[DRIVER_EARNINGS].create_index([("user_id", 1), ("assignment_id", 1)], unique=True)
    await db[DRIVER_COLLECTIONS].create_index([("user_id", 1), ("assignment_id", 1)], unique=True)
    await db[DRIVER_PAYMENT_REVIEWS].create_index([("user_id", 1), ("assignment_id", 1)], unique=True)


async def _enrich_assignments_with_order_state(db: Any, user_id: str, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ids = sorted({normalize_text(row.get("order_id")) for row in items if normalize_text(row.get("order_id"))})
    numbers = sorted({normalize_text(row.get("order_number")) for row in items if normalize_text(row.get("order_number"))})
    if not ids and not numbers:
        return items
    orders = await db[ORDERS].find(
        {
            "user_id": user_id,
            "$or": [
                {"order_id": {"$in": ids + numbers}},
                {"order_number": {"$in": numbers + ids}},
            ],
        },
        {
            "_id": 0,
            "order_id": 1,
            "order_number": 1,
            "remaining_amount": 1,
            "paid_amount": 1,
            "total_amount": 1,
            "has_remaining_amount": 1,
            "payment_status": 1,
            "customer_name": 1,
            "customer_mobile": 1,
            "shipping_district": 1,
            "shipping_street": 1,
        },
    ).to_list(length=5000)
    by_key: dict[str, dict[str, Any]] = {}
    for order in orders:
        for key in (normalize_text(order.get("order_id")), normalize_text(order.get("order_number"))):
            if key:
                by_key[key] = order
    result: list[dict[str, Any]] = []
    for assignment in items:
        row = dict(assignment)
        order = by_key.get(normalize_text(row.get("order_id"))) or by_key.get(normalize_text(row.get("order_number")))
        if order:
            try:
                row["outstanding_amount"] = authoritative_outstanding_amount(order)
                row["outstanding_amount_available"] = True
            except StoreDeliveryRuleError:
                row["outstanding_amount"] = None
                row["outstanding_amount_available"] = False
            row["customer_name"] = order.get("customer_name")
            row["customer_mobile"] = order.get("customer_mobile")
            row["shipping_district"] = order.get("shipping_district")
            row["shipping_street"] = order.get("shipping_street")
        else:
            row["outstanding_amount"] = None
            row["outstanding_amount_available"] = False
        result.append(row)
    return result


def make_store_delivery_driver_app_router(db: Any, current_user: Callable[..., Any]) -> APIRouter:
    router = APIRouter(prefix="/store-delivery/app", tags=["Amasi Delivery Driver App"])

    @router.get("/me")
    async def me(user: dict = Depends(current_user)) -> dict[str, Any]:
        actor = _require_store_driver(user)
        driver = await _driver_for_user(db, actor)
        return {
            "id": driver["id"],
            "name": driver.get("name"),
            "phone": driver.get("phone"),
            "city": driver.get("city"),
            "region": driver.get("region"),
            "district": driver.get("district"),
            "street": driver.get("street"),
            "coverage_mode": driver.get("coverage_mode") or "city",
        }

    @router.get("/bank-accounts")
    async def official_bank_accounts(user: dict = Depends(current_user)) -> dict[str, Any]:
        actor = _require_store_driver(user)
        driver = await _driver_for_user(db, actor)
        items = await db.accounts.find(
            {"user_id": _merchant_id(driver), "account_type": "bank", "status": "active"},
            {
                "_id": 0,
                "id": 1,
                "name": 1,
                "provider": 1,
                "account_number": 1,
                "iban": 1,
            },
        ).sort("name", 1).to_list(length=200)
        return {"items": items, "total": len(items), "source": "financial_center_accounts"}

    @router.get("/deliveries")
    async def deliveries(user: dict = Depends(current_user)) -> dict[str, Any]:
        actor = _require_store_driver(user)
        driver = await _driver_for_user(db, actor)
        merchant_id = _merchant_id(driver)
        items = await db[ASSIGNMENTS].find(
            {"user_id": merchant_id, "driver_id": driver["id"], "active": True},
            {"_id": 0, "user_id": 0},
        ).sort("assigned_at", -1).to_list(length=1000)
        items = await _enrich_assignments_with_order_state(db, merchant_id, items)
        return {"items": items, "total": len(items)}

    @router.post("/deliveries/status")
    async def update_status(payload: DriverStatusUpdate, user: dict = Depends(current_user)) -> dict[str, Any]:
        actor = _require_store_driver(user)
        driver = await _driver_for_user(db, actor)
        merchant_id = _merchant_id(driver)
        await ensure_store_delivery_driver_app_indexes(db)
        assignment = await db[ASSIGNMENTS].find_one(
            {
                "user_id": merchant_id,
                "driver_id": driver["id"],
                "active": True,
                "$or": _barcode_match(payload.barcode),
            },
            {"_id": 0},
        )
        if not assignment:
            raise HTTPException(status_code=404, detail={"code": "driver_assignment_not_found"})

        current = normalize_text(assignment.get("status"))
        target = normalize_text(payload.target_status)
        valid = {
            DELIVERY_STATUS_ASSIGNED: {DELIVERY_STATUS_OUT_FOR_DELIVERY},
            DELIVERY_STATUS_OUT_FOR_DELIVERY: {DELIVERY_STATUS_DELIVERED},
            DELIVERY_STATUS_DELIVERED: set(),
        }
        if target not in valid.get(current, set()):
            raise HTTPException(status_code=409, detail={"code": "driver_delivery_status_transition_invalid"})

        now = _now()
        if target == DELIVERY_STATUS_OUT_FOR_DELIVERY:
            result = await db[ASSIGNMENTS].find_one_and_update(
                {"user_id": merchant_id, "id": assignment["id"], "status": current},
                {"$set": {"status": target, "out_for_delivery_at": now, "updated_at": now}},
                return_document=True,
                projection={"_id": 0, "user_id": 0},
            )
            if not result:
                raise HTTPException(status_code=409, detail={"code": "driver_delivery_status_conflict"})
            await db[ORDERS].update_one(
                {
                    "user_id": merchant_id,
                    "$or": [
                        {"order_id": assignment.get("order_id")},
                        {"order_number": assignment.get("order_number")},
                    ],
                },
                {"$set": {"store_delivery_status": target, "store_delivery_updated_at": now}},
            )
            return result

        order = await canonical_order_for_assignment(db, user_id=merchant_id, assignment=assignment)
        try:
            outstanding_amount = authoritative_outstanding_amount(order)
            requirements = collection_requirements(
                outstanding_amount=outstanding_amount,
                payment_method=payload.payment_method,
            )
        except StoreDeliveryRuleError as exc:
            raise HTTPException(status_code=422, detail={"code": str(exc)}) from exc

        receipt_row = None
        if requirements["receipt_required"]:
            if not normalize_text(payload.receipt_reference):
                raise HTTPException(status_code=422, detail={"code": "collection_receipt_required"})
            receipt_row = await validate_receipt_reference(
                db,
                user_id=merchant_id,
                driver_id=driver["id"],
                assignment_id=assignment["id"],
                receipt_reference=payload.receipt_reference,
            )
        if requirements["bank_account_required"] and not normalize_text(payload.bank_account_id):
            raise HTTPException(status_code=422, detail={"code": "business_bank_account_required"})
        if requirements["bank_account_required"]:
            bank = await db.accounts.find_one(
                {
                    "user_id": merchant_id,
                    "id": normalize_text(payload.bank_account_id),
                    "account_type": "bank",
                    "status": "active",
                },
                {"_id": 0, "id": 1, "name": 1, "provider": 1},
            )
            if not bank:
                raise HTTPException(status_code=422, detail={"code": "business_bank_account_invalid"})
        else:
            bank = None

        earning = driver_earning(assignment=assignment, delivered=True)
        earning_row = {
            "id": str(uuid.uuid4()),
            "user_id": merchant_id,
            "assignment_id": assignment["id"],
            "order_id": assignment["order_id"],
            "order_number": assignment.get("order_number"),
            "driver_id": driver["id"],
            "driver_name_snapshot": assignment.get("driver_name_snapshot"),
            "amount": earning,
            "status": "due",
            "earned_at": now,
        }
        collection_row = {
            "id": str(uuid.uuid4()),
            "user_id": merchant_id,
            "assignment_id": assignment["id"],
            "order_id": assignment["order_id"],
            "order_number": assignment.get("order_number"),
            "driver_id": driver["id"],
            "amount": requirements["amount"],
            "amount_source": "unified_orders.remaining_amount",
            "payment_method": requirements["payment_method"],
            "cod_custody_amount": requirements["cod_custody_amount"],
            "receipt_reference": normalize_text(payload.receipt_reference),
            "receipt_url": (receipt_row or {}).get("token") and f"/api/store-delivery/evidence/receipt/{receipt_row['token']}",
            "bank_account_id": normalize_text(payload.bank_account_id),
            "bank_name_snapshot": (bank or {}).get("name") or (bank or {}).get("provider"),
            "review_status": requirements["review_status"],
            "collected_at": now,
        }
        try:
            await db[DRIVER_EARNINGS].insert_one(earning_row)
            await db[DRIVER_COLLECTIONS].insert_one(collection_row)
            if requirements["review_status"] == "pending_accountant_review":
                await db[DRIVER_PAYMENT_REVIEWS].insert_one({
                    "id": str(uuid.uuid4()),
                    "user_id": merchant_id,
                    "assignment_id": assignment["id"],
                    "driver_id": driver["id"],
                    "driver_name_snapshot": assignment.get("driver_name_snapshot"),
                    "order_id": assignment["order_id"],
                    "order_number": assignment.get("order_number"),
                    "amount": requirements["amount"],
                    "amount_source": "unified_orders.remaining_amount",
                    "payment_method": requirements["payment_method"],
                    "receipt_reference": normalize_text(payload.receipt_reference),
                    "receipt_url": collection_row.get("receipt_url"),
                    "bank_account_id": normalize_text(payload.bank_account_id),
                    "bank_name_snapshot": (bank or {}).get("name") or (bank or {}).get("provider"),
                    "status": "pending",
                    "submitted_at": now,
                })
        except Exception:
            await db[DRIVER_EARNINGS].delete_one({"user_id": merchant_id, "assignment_id": assignment["id"]})
            await db[DRIVER_COLLECTIONS].delete_one({"user_id": merchant_id, "assignment_id": assignment["id"]})
            await db[DRIVER_PAYMENT_REVIEWS].delete_one({"user_id": merchant_id, "assignment_id": assignment["id"]})
            raise

        result = await db[ASSIGNMENTS].find_one_and_update(
            {"user_id": merchant_id, "id": assignment["id"], "status": current},
            {"$set": {
                "status": DELIVERY_STATUS_DELIVERED,
                "delivered_at": now,
                "updated_at": now,
                "collection_amount": requirements["amount"],
                "collection_method": requirements["payment_method"],
                "payment_review_status": requirements["review_status"],
            }},
            return_document=True,
            projection={"_id": 0, "user_id": 0},
        )
        if not result:
            await db[DRIVER_EARNINGS].delete_one({"user_id": merchant_id, "assignment_id": assignment["id"]})
            await db[DRIVER_COLLECTIONS].delete_one({"user_id": merchant_id, "assignment_id": assignment["id"]})
            await db[DRIVER_PAYMENT_REVIEWS].delete_one({"user_id": merchant_id, "assignment_id": assignment["id"]})
            raise HTTPException(status_code=409, detail={"code": "driver_delivery_status_conflict"})

        if receipt_row:
            await db[RECEIPTS].update_one(
                {"user_id": merchant_id, "token": receipt_row["token"], "status": "uploaded"},
                {"$set": {"status": "bound", "bound_at": now}},
            )

        payment_state = (
            "not_required"
            if requirements["amount"] == 0
            else "cash_in_driver_custody"
            if requirements["payment_method"] == "cash"
            else "pending_accountant_review"
        )
        await db[ORDERS].update_one(
            {
                "user_id": merchant_id,
                "$or": [
                    {"order_id": assignment.get("order_id")},
                    {"order_number": assignment.get("order_number")},
                ],
            },
            {"$set": {
                "store_delivery_assignment_id": assignment["id"],
                "store_delivery_driver_id": driver["id"],
                "store_delivery_status": DELIVERY_STATUS_DELIVERED,
                "store_delivery_delivered_at": now,
                "store_delivery_collection_amount": requirements["amount"],
                "store_delivery_collection_method": requirements["payment_method"],
                "store_delivery_payment_status": payment_state,
                "store_delivery_payment_review_status": requirements["review_status"],
                "store_delivery_updated_at": now,
            }},
        )
        await db[EVENTS].insert_one({
            "id": str(uuid.uuid4()),
            "user_id": merchant_id,
            "event_type": "store_delivery_delivered",
            "assignment_id": assignment["id"],
            "driver_id": driver["id"],
            "order_id": assignment["order_id"],
            "earning_amount": earning,
            "collection_amount": requirements["amount"],
            "payment_method": requirements["payment_method"],
            "amount_source": "unified_orders.remaining_amount",
            "occurred_at": now,
        })
        return {
            **result,
            "earning_amount": earning,
            "collection": requirements,
            "authoritative_outstanding_amount": outstanding_amount,
        }

    @router.get("/accounts/summary")
    async def accounts_summary(user: dict = Depends(current_user)) -> dict[str, Any]:
        actor = _require_store_driver(user)
        driver = await _driver_for_user(db, actor)
        merchant_id = _merchant_id(driver)
        assignments = await db[ASSIGNMENTS].find(
            {"user_id": merchant_id, "driver_id": driver["id"], "active": True},
            {"_id": 0, "status": 1},
        ).to_list(length=5000)
        earnings = await db[DRIVER_EARNINGS].find(
            {"user_id": merchant_id, "driver_id": driver["id"]},
            {"_id": 0, "amount": 1},
        ).to_list(length=5000)
        collections = await db[DRIVER_COLLECTIONS].find(
            {"user_id": merchant_id, "driver_id": driver["id"]},
            {"_id": 0, "cod_custody_amount": 1, "amount": 1, "payment_method": 1, "review_status": 1},
        ).to_list(length=5000)
        settlements = await db[DRIVER_SETTLEMENTS].find(
            {"user_id": merchant_id, "driver_id": driver["id"], "status": "posted"},
            {"_id": 0, "amount": 1, "settlement_type": 1},
        ).to_list(length=5000)
        counts = {
            state: sum(1 for row in assignments if row.get("status") == state)
            for state in (DELIVERY_STATUS_ASSIGNED, DELIVERY_STATUS_OUT_FOR_DELIVERY, DELIVERY_STATUS_DELIVERED)
        }
        earnings_total = round(sum(float(row.get("amount") or 0) for row in earnings), 2)
        earnings_paid = round(sum(float(row.get("amount") or 0) for row in settlements if row.get("settlement_type") == "earning_payment"), 2)
        cod_collected = round(sum(float(row.get("cod_custody_amount") or 0) for row in collections), 2)
        cod_remitted = round(sum(float(row.get("amount") or 0) for row in settlements if row.get("settlement_type") == "cod_remittance"), 2)
        return {
            "driver_id": driver["id"],
            "delivery_counts": counts,
            "earnings_total": earnings_total,
            "earnings_paid": earnings_paid,
            "earnings_due": round(max(earnings_total - earnings_paid, 0), 2),
            "cod_cash_collected": cod_collected,
            "cod_cash_remitted": cod_remitted,
            "cod_cash_custody": round(max(cod_collected - cod_remitted, 0), 2),
            "card_pending_review": round(sum(float(row.get("amount") or 0) for row in collections if row.get("payment_method") == PAYMENT_METHOD_CARD_TERMINAL and row.get("review_status") == "pending_accountant_review"), 2),
            "bank_transfer_pending_review": round(sum(float(row.get("amount") or 0) for row in collections if row.get("payment_method") == PAYMENT_METHOD_BANK_TRANSFER and row.get("review_status") == "pending_accountant_review"), 2),
        }

    return router


__all__ = [
    "make_store_delivery_driver_app_router",
    "ensure_store_delivery_driver_app_indexes",
    "DRIVER_EARNINGS",
    "DRIVER_COLLECTIONS",
    "DRIVER_PAYMENT_REVIEWS",
]
