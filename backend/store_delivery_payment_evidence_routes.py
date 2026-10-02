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
DELIVERY_PROOFS = "store_delivery_delivery_proofs"
CUSTOMER_CONVERSATION_EVIDENCE = "store_delivery_customer_conversation_evidence"
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


def _normalized_payment_method(value: Any) -> str:
    if isinstance(value, dict):
        value = (
            value.get("code")
            or value.get("slug")
            or value.get("name")
            or value.get("label")
        )
    return " ".join(
        normalize_text(value)
        .casefold()
        .replace("-", " ")
        .replace("_", " ")
        .split()
    )


def _raw_salla_order(order: dict[str, Any]) -> dict[str, Any]:
    raw_by_source = order.get("raw_by_source")
    if not isinstance(raw_by_source, dict):
        return {}
    raw = raw_by_source.get("salla_direct")
    return raw if isinstance(raw, dict) else {}


def _uncollected_cod_total_fallback(order: dict[str, Any]) -> float | None:
    """Carry the same Salla COD evidence used by Order Review into delivery.

    Salla can report an uncollected COD order as:
    remaining_action.remaining_amount = null and
    remaining_action.has_remaining_amount = false.

    Order Review already interprets that provider shape as "the full order
    total is still due" while the order is under review.  Delivery must retain
    the same collection fact after the operational status later moves to
    completed/delivering; otherwise a real COD balance is silently reduced to
    zero for the driver.
    """
    raw = _raw_salla_order(order)
    payment_actions = raw.get("payment_actions") if isinstance(raw.get("payment_actions"), dict) else {}
    remaining_action = (
        payment_actions.get("remaining_action")
        if isinstance(payment_actions.get("remaining_action"), dict)
        else {}
    )
    if not remaining_action:
        return None

    raw_payment = raw.get("payment") if isinstance(raw.get("payment"), dict) else {}
    method = _normalized_payment_method(
        raw.get("payment_method")
        or raw_payment.get("method")
        or order.get("payment_method")
    )
    if method not in {
        "cod",
        "cash on delivery",
        "الدفع عند الاستلام",
        "دفع عند الاستلام",
    }:
        return None

    if remaining_action.get("remaining_amount") is not None:
        return None
    if remaining_action.get("has_remaining_amount") is not False:
        return None

    paid_value = money(order.get("paid_amount") or 0)
    if paid_value != money(0):
        return None

    payment_statuses = {
        normalize_text(order.get("payment_status")).casefold(),
        normalize_text(raw_payment.get("status")).casefold(),
    }
    if payment_statuses.intersection({"paid", "completed", "refunded", "مدفوع", "مكتمل", "مسترجع"}):
        return None

    collection_status = normalize_text(order.get("payment_collection_status")).casefold()
    if collection_status in {"paid", "partial"}:
        return None

    total = order.get("total_amount")
    if total is None:
        return None
    total_value = money(total)
    if total_value <= money(0):
        return None
    return float(total_value)


def authoritative_outstanding_amount(order: dict[str, Any]) -> float:
    """Return the current remaining amount from the order SSOT.

    Positive explicit remaining values always win.  For the one known Salla
    COD shape where a null provider balance is normalized to zero before
    collection, reuse the same provider evidence rule as Order Review so the
    driver still sees the amount due.  Otherwise explicit zero remains valid.
    """
    remaining_present = (
        "remaining_amount" in order and order.get("remaining_amount") is not None
    )
    if remaining_present:
        remaining_value = money(order.get("remaining_amount"))
        if remaining_value > money(0):
            return float(remaining_value)

    paid_status = normalize_text(order.get("payment_status")).casefold()
    if paid_status in {"paid", "مدفوع", "مكتمل", "completed", "refunded", "مسترجع"}:
        return 0.0

    cod_fallback = _uncollected_cod_total_fallback(order)
    if cod_fallback is not None:
        return cod_fallback

    if remaining_present:
        return float(money(order.get("remaining_amount")))

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
    for collection in (RECEIPTS, DELIVERY_PROOFS, CUSTOMER_CONVERSATION_EVIDENCE):
        await db[collection].create_index([("user_id", 1), ("token", 1)], unique=True)
        await db[collection].create_index([("user_id", 1), ("assignment_id", 1), ("created_at", -1)])


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


async def _validate_evidence_reference(
    db: Any,
    *,
    collection: str,
    user_id: str,
    driver_id: str,
    assignment_id: str,
    token: str,
    invalid_code: str,
) -> dict[str, Any]:
    row = await db[collection].find_one(
        {
            "user_id": user_id,
            "token": normalize_text(token),
            "driver_id": driver_id,
            "assignment_id": assignment_id,
            "status": "uploaded",
        },
        {"_id": 0, "content": 0},
    )
    if not row:
        raise HTTPException(status_code=422, detail={"code": invalid_code})
    return row


async def validate_delivery_proof_reference(
    db: Any,
    *,
    user_id: str,
    driver_id: str,
    assignment_id: str,
    proof_reference: str,
) -> dict[str, Any]:
    return await _validate_evidence_reference(
        db,
        collection=DELIVERY_PROOFS,
        user_id=user_id,
        driver_id=driver_id,
        assignment_id=assignment_id,
        token=proof_reference,
        invalid_code="delivery_proof_invalid",
    )


async def validate_customer_conversation_reference(
    db: Any,
    *,
    user_id: str,
    driver_id: str,
    assignment_id: str,
    evidence_reference: str,
) -> dict[str, Any]:
    return await _validate_evidence_reference(
        db,
        collection=CUSTOMER_CONVERSATION_EVIDENCE,
        user_id=user_id,
        driver_id=driver_id,
        assignment_id=assignment_id,
        token=evidence_reference,
        invalid_code="customer_conversation_evidence_invalid",
    )


def make_store_delivery_payment_evidence_router(db: Any, current_user: Callable[..., Any]) -> APIRouter:
    router = APIRouter(prefix="/store-delivery/evidence", tags=["Store Delivery Evidence"])

    async def _read_valid_image(file: UploadFile) -> tuple[bytes, str]:
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
        return data, detected

    async def _store_driver_image(
        *,
        collection: str,
        assignment_id: str,
        file: UploadFile,
        user: dict[str, Any],
        evidence_kind: str,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
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
        data, detected = await _read_valid_image(file)
        await ensure_store_delivery_receipt_indexes(db)
        token = secrets.token_urlsafe(32)
        now = _now()
        row = {
            "token": token,
            "user_id": user_id,
            "driver_id": driver["id"],
            "assignment_id": assignment["id"],
            "evidence_kind": evidence_kind,
            "filename": normalize_text(file.filename)[:180],
            "content_type": detected,
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "content": Binary(data),
            "status": "uploaded",
            "created_at": now,
            "created_by_account_user_id": normalize_text(user.get("id")),
        }
        await db[collection].insert_one(row)
        row.pop("_id", None)
        return row, driver

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
            },
            {"_id": 0, "id": 1, "status": 1, "order_id": 1, "order_number": 1},
        )
        if not assignment:
            raise HTTPException(status_code=404, detail={"code": "driver_assignment_not_found"})
        if assignment.get("status") == "delivered":
            # A replacement receipt is an unbound candidate for the existing
            # rejected-payment resubmission route, never delivery proof.
            reviews = await db["store_delivery_payment_reviews"].find({
                "user_id": user_id, "assignment_id": assignment["id"],
            }).limit(2).to_list(2)
            review = reviews[0] if len(reviews) == 1 else None
            if (not review or review.get("status") != "rejected"
                    or review.get("payment_method") not in {"bank_transfer", "card_terminal"}
                    or review.get("driver_id") != driver["id"]
                    or not assignment.get("order_id") or not assignment.get("order_number")
                    or review.get("order_id") != assignment["order_id"]
                    or review.get("order_number") != assignment["order_number"]):
                raise HTTPException(status_code=409, detail={"code": "payment_review_not_rejected"})

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

    @router.post("/delivery-proof")
    async def upload_delivery_proof(
        assignment_id: str = Form(...),
        file: UploadFile = File(...),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        row, _ = await _store_driver_image(
            collection=DELIVERY_PROOFS,
            assignment_id=assignment_id,
            file=file,
            user=user,
            evidence_kind="delivery_proof",
        )
        return {
            "ok": True,
            "proof_reference": row["token"],
            "proof_url": f"/api/store-delivery/evidence/delivery-proof/{row['token']}",
            "content_type": row["content_type"],
            "size": row["size"],
        }

    @router.post("/customer-conversation")
    async def upload_customer_conversation(
        assignment_id: str = Form(...),
        file: UploadFile = File(...),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        row, _ = await _store_driver_image(
            collection=CUSTOMER_CONVERSATION_EVIDENCE,
            assignment_id=assignment_id,
            file=file,
            user=user,
            evidence_kind="customer_conversation",
        )
        return {
            "ok": True,
            "evidence_reference": row["token"],
            "evidence_url": f"/api/store-delivery/evidence/customer-conversation/{row['token']}",
            "content_type": row["content_type"],
            "size": row["size"],
        }

    async def _get_evidence(
        *,
        collection: str,
        token: str,
        user: dict[str, Any],
        not_found_code: str,
    ) -> Response:
        user_id = _merchant_user_id(user)
        row = await db[collection].find_one({"user_id": user_id, "token": normalize_text(token)})
        if not row:
            raise HTTPException(status_code=404, detail={"code": not_found_code})
        role = normalize_text(user.get("role")).casefold()
        if role == "store_driver":
            driver = await _driver_for_user(db, user)
            if row.get("driver_id") != driver.get("id"):
                raise HTTPException(status_code=403, detail={"code": "delivery_evidence_access_denied"})
        elif role not in {"owner", "admin", "accountant", "operations", "customer_service"} and user.get("is_owner") is not True:
            raise HTTPException(status_code=403, detail={"code": "delivery_evidence_access_denied"})
        return Response(
            content=bytes(row["content"]),
            media_type=row["content_type"],
            headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
        )

    @router.get("/delivery-proof/{token}")
    async def get_delivery_proof(token: str, user: dict = Depends(current_user)) -> Response:
        return await _get_evidence(
            collection=DELIVERY_PROOFS,
            token=token,
            user=user,
            not_found_code="delivery_proof_not_found",
        )

    @router.get("/customer-conversation/{token}")
    async def get_customer_conversation(token: str, user: dict = Depends(current_user)) -> Response:
        return await _get_evidence(
            collection=CUSTOMER_CONVERSATION_EVIDENCE,
            token=token,
            user=user,
            not_found_code="customer_conversation_evidence_not_found",
        )

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

    from store_delivery_late_evidence import install_driver_routes
    install_driver_routes(router, db, current_user, _read_valid_image)
    return router


__all__ = [
    "RECEIPTS",
    "DELIVERY_PROOFS",
    "CUSTOMER_CONVERSATION_EVIDENCE",
    "authoritative_outstanding_amount",
    "canonical_order_for_assignment",
    "ensure_store_delivery_receipt_indexes",
    "make_store_delivery_payment_evidence_router",
    "validate_receipt_reference",
    "validate_delivery_proof_reference",
    "validate_customer_conversation_reference",
]
