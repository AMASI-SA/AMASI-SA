"""Authoritative COD and receipt evidence for Amasi Delivery.

The driver app never decides how much the customer owes. Outstanding amount is
read from the canonical ``unified_orders`` record at delivery time. Card-terminal
and bank-transfer proof is uploaded as validated image bytes and retained as audit
evidence.
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timezone
from typing import Any, Callable

from bson.binary import Binary
from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile

from store_delivery_domain import StoreDeliveryRuleError, money, normalize_text
from store_delivery_driver_routes import STORE_DRIVERS
from store_delivery_handover_routes import ASSIGNMENTS, ORDERS

RECEIPTS = "store_delivery_receipts"
MAX_RECEIPT_BYTES = 8 * 1024 * 1024
ALLOWED_RECEIPT_TYPES = {"image/jpeg", "image/png", "image/webp"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _detected_type(data: bytes) -> str | None:
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def authoritative_outstanding_amount(order: dict[str, Any]) -> float:
    """Return the current remaining amount from the order SSOT.

    Zero is a valid explicit value. If no authoritative fields exist we fail
    closed instead of letting a driver type an arbitrary amount.
    """
    if "remaining_amount" in order and order.get("remaining_amount") is not None:
        return float(money(order.get("remaining_amount")))

    paid_status = normalize_text(order.get("payment_status")).casefold()
    if paid_status in {"paid", "مدفوع", "مكتمل", "completed"}:
        return 0.0

    total = order.get("total_amount")
    paid = order.get("paid_amount")
    if total is not None and paid is not None:
        total_value = money(total)
        paid_value = money(paid)
        return float(max(total_value - paid_value, money(0)))

    has_remaining = order.get("has_remaining_amount")
    if has_remaining is False:
        return 0.0

    raise StoreDeliveryRuleError("authoritative_outstanding_amount_unavailable")


async def canonical_order_for_assignment(db: Any, *, user_id: str, assignment: dict[str, Any]) -> dict[str, Any]:
    order_id = normalize_text(assignment.get("order_id"))
    order_number = normalize_text(assignment.get("order_number"))
    clauses: list[dict[str, Any]] = []
    if order_id:
        clauses.extend([{"order_id": order_id}, {"order_number": order_id}])
    if order_number:
        clauses.extend([{"order_number": order_number}, {"order_id": order_number}])
    if not clauses:
        raise HTTPException(status_code=409, detail={"code": "canonical_order_identity_missing"})
    order = await db[ORDERS].find_one({"user_id": user_id, "$or": clauses}, {"_id": 0})
    if not order:
        raise HTTPException(status_code=409, detail={"code": "canonical_order_not_found"})
    return order


async def ensure_store_delivery_receipt_indexes(db: Any) -> None:
    await db[RECEIPTS].create_index([("user_id", 1), ("token", 1)], unique=True)
    await db[RECEIPTS].create_index([("user_id", 1), ("assignment_id", 1), ("created_at", -1)])


def _merchant_user_id(user: dict[str, Any]) -> str:
    role = normalize_text(user.get("role")).casefold()
    if role == "owner" or user.get("is_owner") is True:
        return normalize_text(user.get("id"))
    owner_id = normalize_text(user.get("created_by"))
    if not owner_id:
        raise HTTPException(status_code=409, detail={"code": "employee_store_not_linked"})
    return owner_id


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


async def validate_receipt_reference(
    db: Any,
    *,
    user_id: str,
    driver_id: str,
    assignment_id: str,
    receipt_reference: str,
) -> dict[str, Any]:
    token = normalize_text(receipt_reference)
    row = await db[RECEIPTS].find_one(
        {
            "user_id": user_id,
            "token": token,
            "driver_id": driver_id,
            "assignment_id": assignment_id,
            "status": "uploaded",
        },
        {"_id": 0, "content": 0},
    )
    if not row:
        raise HTTPException(status_code=422, detail={"code": "collection_receipt_invalid"})
    return row


def make_store_delivery_payment_evidence_router(db: Any, current_user: Callable[..., Any]) -> APIRouter:
    router = APIRouter(prefix="/store-delivery/evidence", tags=["Store Delivery Evidence"])

    @router.post("/receipt")
    async def upload_receipt(
        assignment_id: str = Form(...),
        file: UploadFile = File(...),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        driver = await _driver_for_user(db, user)
        user_id = normalize_text(driver.get("user_id"))
        assignment = await db[ASSIGNMENTS].find_one(
            {
                "user_id": user_id,
                "id": normalize_text(assignment_id),
                "driver_id": driver["id"],
                "active": True,
                "status": {"$ne": "delivered"},
            },
            {"_id": 0, "id": 1},
        )
        if not assignment:
            raise HTTPException(status_code=404, detail={"code": "driver_assignment_not_found"})

        declared = normalize_text(file.content_type).casefold()
        if declared not in ALLOWED_RECEIPT_TYPES:
            raise HTTPException(status_code=415, detail={"code": "unsupported_receipt_image_type"})
        data = await file.read(MAX_RECEIPT_BYTES + 1)
        if not data:
            raise HTTPException(status_code=422, detail={"code": "empty_receipt_image"})
        if len(data) > MAX_RECEIPT_BYTES:
            raise HTTPException(status_code=413, detail={"code": "receipt_image_too_large", "max_bytes": MAX_RECEIPT_BYTES})
        detected = _detected_type(data)
        if detected != declared:
            raise HTTPException(status_code=415, detail={"code": "receipt_image_signature_mismatch"})

        await ensure_store_delivery_receipt_indexes(db)
        token = secrets.token_urlsafe(32)
        now = _now()
        await db[RECEIPTS].insert_one({
            "token": token,
            "user_id": user_id,
            "driver_id": driver["id"],
            "assignment_id": assignment["id"],
            "filename": normalize_text(file.filename)[:180],
            "content_type": detected,
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "content": Binary(data),
            "status": "uploaded",
            "created_at": now,
            "created_by_account_user_id": normalize_text(user.get("id")),
        })
        return {
            "ok": True,
            "receipt_reference": token,
            "receipt_url": f"/api/store-delivery/evidence/receipt/{token}",
            "content_type": detected,
            "size": len(data),
        }

    @router.get("/receipt/{token}")
    async def get_receipt(token: str, user: dict = Depends(current_user)) -> Response:
        user_id = _merchant_user_id(user)
        row = await db[RECEIPTS].find_one({"user_id": user_id, "token": normalize_text(token)})
        if not row:
            raise HTTPException(status_code=404, detail={"code": "receipt_not_found"})
        role = normalize_text(user.get("role")).casefold()
        if role == "store_driver":
            driver = await _driver_for_user(db, user)
            if row.get("driver_id") != driver.get("id"):
                raise HTTPException(status_code=403, detail={"code": "receipt_access_denied"})
        elif role not in {"owner", "admin", "accountant", "operations"} and user.get("is_owner") is not True:
            raise HTTPException(status_code=403, detail={"code": "receipt_access_denied"})
        return Response(
            content=bytes(row["content"]),
            media_type=row["content_type"],
            headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
        )

    return router


__all__ = [
    "RECEIPTS",
    "authoritative_outstanding_amount",
    "canonical_order_for_assignment",
    "ensure_store_delivery_receipt_indexes",
    "make_store_delivery_payment_evidence_router",
    "validate_receipt_reference",
]
