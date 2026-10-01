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
from store_courier_domain import (
    DELIVERED as WORKFLOW_DELIVERED,
    DELIVERING as WORKFLOW_DELIVERING,
    WORKFLOWS,
)
from store_delivery_payment_evidence_routes import (
    CUSTOMER_CONVERSATION_EVIDENCE,
    DELIVERY_PROOFS,
    RECEIPTS,
    authoritative_outstanding_amount,
    canonical_order_for_assignment,
    validate_customer_conversation_reference,
    validate_delivery_proof_reference,
    validate_receipt_reference,
)

DRIVER_EARNINGS = "store_delivery_driver_earnings"
DRIVER_COLLECTIONS = "store_delivery_collections"
DRIVER_PAYMENT_REVIEWS = "store_delivery_payment_reviews"
DRIVER_SETTLEMENTS = "store_delivery_driver_settlements"
DRIVER_RECEIVE_SESSIONS = "store_delivery_driver_receive_sessions"

DELIVERY_EXCEPTION_CUSTOMER_UNREACHABLE = "customer_unreachable"
DELIVERY_EXCEPTION_CUSTOMER_REQUESTED_DELAY = "customer_requested_delay"
DELIVERY_EXCEPTION_CUSTOMER_REQUESTED_CANCEL = "customer_requested_cancel"
DELIVERY_EXCEPTION_CODES = frozenset({
    DELIVERY_EXCEPTION_CUSTOMER_UNREACHABLE,
    DELIVERY_EXCEPTION_CUSTOMER_REQUESTED_DELAY,
    DELIVERY_EXCEPTION_CUSTOMER_REQUESTED_CANCEL,
})

ASSIGNMENT_EXCEPTION_FIELDS = (
    "delivery_exception_code",
    "delivery_exception_note",
    "delivery_exception_at",
    "delivery_exception_by_driver_id",
    "delivery_exception_evidence_reference",
    "delivery_exception_evidence_url",
)
ORDER_EXCEPTION_FIELDS = (
    "store_delivery_exception_code",
    "store_delivery_exception_note",
    "store_delivery_exception_at",
    "store_delivery_exception_driver_id",
    "store_delivery_exception_evidence_reference",
    "store_delivery_exception_evidence_url",
    "store_delivery_customer_service_attention_required",
)
WORKFLOW_EXCEPTION_FIELDS = (
    "store_courier_exception_code",
    "store_courier_exception_note",
    "store_courier_exception_at",
    "store_courier_exception_driver_id",
    "store_courier_exception_evidence_reference",
    "store_courier_exception_evidence_url",
    "customer_service_attention_required",
)

DRIVER_STATUS_TRANSITIONS = {
    DELIVERY_STATUS_ASSIGNED: frozenset({
        DELIVERY_STATUS_OUT_FOR_DELIVERY,
        DELIVERY_STATUS_DELIVERED,
    }),
    DELIVERY_STATUS_OUT_FOR_DELIVERY: frozenset({
        DELIVERY_STATUS_OUT_FOR_DELIVERY,
        DELIVERY_STATUS_DELIVERED,
    }),
    DELIVERY_STATUS_DELIVERED: frozenset(),
}


def _unset_fields(fields: tuple[str, ...]) -> dict[str, str]:
    return {field: "" for field in fields}


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


def _true_barcode_match(value: str) -> list[dict[str, Any]]:
    value = normalize_text(value)
    return [
        {"barcode": value},
        {"shipping_barcode": value},
        {"tracking_number": value},
    ]


def _salla_order_id(order: dict[str, Any], assignment: dict[str, Any]) -> str:
    raw_sources = order.get("raw_by_source") if isinstance(order.get("raw_by_source"), dict) else {}
    raw_salla = raw_sources.get("salla_direct") if isinstance(raw_sources.get("salla_direct"), dict) else {}
    return normalize_text(
        raw_salla.get("id")
        or order.get("order_id")
        or assignment.get("order_id")
    )


class DriverSallaStatusError(RuntimeError):
    def __init__(self, message: str, *, status_code: int = 502, needs_reauth: bool = False):
        super().__init__(message)
        self.status_code = status_code
        self.needs_reauth = needs_reauth


async def _call_salla(
    db: Any,
    user_id: str,
    method: str,
    path: str,
    **kwargs: Any,
) -> dict[str, Any]:
    # Lazy import keeps the focused Store Delivery test job purpose-bound and
    # avoids loading the full Salla router/crypto stack merely to import this
    # driver module.
    from salla_integration.service import SallaError, call_salla
    try:
        return await call_salla(db, user_id, method, path, **kwargs)
    except SallaError as exc:
        raise DriverSallaStatusError(
            str(exc),
            status_code=int(exc.status_code or 502),
            needs_reauth=bool(exc.needs_reauth),
        ) from exc


async def _push_salla_delivery_status(
    db: Any,
    *,
    user_id: str,
    assignment: dict[str, Any],
    order: dict[str, Any],
    slug: str,
) -> dict[str, Any]:
    salla_order_id = _salla_order_id(order, assignment)
    if not salla_order_id:
        raise HTTPException(status_code=409, detail={"code": "salla_order_id_missing"})
    try:
        await _call_salla(
            db,
            user_id,
            "POST",
            f"/orders/{salla_order_id}/status",
            json={"slug": slug, "send_status_sms": False},
        )
        readback = await _call_salla(
            db,
            user_id,
            "GET",
            f"/orders/{salla_order_id}",
            params={"format": "light"},
        )
    except DriverSallaStatusError as exc:
        raise HTTPException(
            status_code=exc.status_code if 400 <= int(exc.status_code or 0) < 600 else 502,
            detail={
                "code": "salla_delivery_status_update_failed",
                "message": str(exc),
                "needs_reauth": bool(exc.needs_reauth),
            },
        ) from exc
    data = readback.get("data") if isinstance(readback, dict) else None
    status = data.get("status") if isinstance(data, dict) else None
    actual_slug = normalize_text(status.get("slug") if isinstance(status, dict) else "")
    if actual_slug and actual_slug != slug:
        raise HTTPException(
            status_code=502,
            detail={
                "code": "salla_delivery_status_readback_mismatch",
                "expected_slug": slug,
                "actual_slug": actual_slug,
            },
        )
    return {"order_id": salla_order_id, "slug": slug, "verified_slug": actual_slug or slug}


class DriverStatusUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    barcode: str = Field(min_length=1, max_length=180)
    target_status: str
    # Deprecated input retained for backward compatibility only. It is ignored.
    outstanding_amount: float | None = Field(default=None, ge=0, le=1_000_000)
    payment_method: str | None = None
    receipt_reference: str | None = Field(default=None, max_length=500)
    delivery_proof_reference: str | None = Field(default=None, max_length=500)
    conversation_evidence_reference: str | None = Field(default=None, max_length=500)
    bank_account_id: str | None = Field(default=None, max_length=120)


class DriverDeliveryException(BaseModel):
    model_config = ConfigDict(extra="forbid")
    barcode: str = Field(min_length=1, max_length=180)
    exception_code: str = Field(min_length=1, max_length=80)
    note: str | None = Field(default=None, max_length=2000)
    evidence_reference: str | None = Field(default=None, max_length=500)


class DriverReceiveScan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    barcode: str = Field(min_length=1, max_length=180)


async def ensure_store_delivery_driver_app_indexes(db: Any) -> None:
    await db[DRIVER_EARNINGS].create_index([("user_id", 1), ("assignment_id", 1)], unique=True)
    await db[DRIVER_COLLECTIONS].create_index([("user_id", 1), ("assignment_id", 1)], unique=True)
    await db[DRIVER_PAYMENT_REVIEWS].create_index([("user_id", 1), ("assignment_id", 1)], unique=True)
    await db[DRIVER_RECEIVE_SESSIONS].create_index([("user_id", 1), ("id", 1)], unique=True)
    await db[DRIVER_RECEIVE_SESSIONS].create_index([("user_id", 1), ("driver_id", 1), ("status", 1)])


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
            row["total_amount"] = order.get("total_amount")
            row["paid_amount"] = order.get("paid_amount")
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

    @router.get("/deliveries/home")
    async def deliveries_home(user: dict = Depends(current_user)) -> dict[str, Any]:
        actor = _require_store_driver(user)
        driver = await _driver_for_user(db, actor)
        merchant_id = _merchant_id(driver)
        items = await db[ASSIGNMENTS].find(
            {
                "user_id": merchant_id,
                "driver_id": driver["id"],
                "active": True,
                "status": {"$in": [DELIVERY_STATUS_ASSIGNED, DELIVERY_STATUS_OUT_FOR_DELIVERY]},
            },
            {"_id": 0, "user_id": 0},
        ).sort("assigned_at", 1).to_list(length=2000)
        items = await _enrich_assignments_with_order_state(db, merchant_id, items)
        district_counts: dict[str, int] = {}
        for row in items:
            district = normalize_text(row.get("shipping_district")) or "بدون حي"
            district_counts[district] = district_counts.get(district, 0) + 1
        districts = [
            {"district": name, "count": count}
            for name, count in sorted(
                district_counts.items(),
                key=lambda pair: (-pair[1], pair[0]),
            )
        ]
        return {
            "items": items,
            "total": len(items),
            "districts": districts,
            "assigned_count": sum(1 for row in items if row.get("status") == DELIVERY_STATUS_ASSIGNED),
            "out_for_delivery_count": sum(1 for row in items if row.get("status") == DELIVERY_STATUS_OUT_FOR_DELIVERY),
        }

    @router.get("/deliveries/search/{query}")
    async def search_delivery(query: str, user: dict = Depends(current_user)) -> dict[str, Any]:
        actor = _require_store_driver(user)
        driver = await _driver_for_user(db, actor)
        merchant_id = _merchant_id(driver)
        value = normalize_text(query)
        if not value:
            raise HTTPException(status_code=422, detail={"code": "driver_delivery_search_required"})
        row = await db[ASSIGNMENTS].find_one(
            {
                "user_id": merchant_id,
                "driver_id": driver["id"],
                "active": True,
                "$or": _barcode_match(value),
            },
            {"_id": 0, "user_id": 0},
        )
        if not row:
            raise HTTPException(status_code=404, detail={"code": "driver_assignment_not_found"})
        enriched = await _enrich_assignments_with_order_state(db, merchant_id, [row])
        return {"item": enriched[0]}

    @router.get("/deliveries/report")
    async def deliveries_report(user: dict = Depends(current_user)) -> dict[str, Any]:
        actor = _require_store_driver(user)
        driver = await _driver_for_user(db, actor)
        merchant_id = _merchant_id(driver)
        rows = await db[DRIVER_COLLECTIONS].find(
            {"user_id": merchant_id, "driver_id": driver["id"]},
            {
                "_id": 0,
                "assignment_id": 1,
                "order_id": 1,
                "order_number": 1,
                "amount": 1,
                "payment_method": 1,
                "cod_custody_amount": 1,
                "receipt_reference": 1,
                "receipt_url": 1,
                "delivery_proof_reference": 1,
                "delivery_proof_url": 1,
                "bank_account_id": 1,
                "bank_name_snapshot": 1,
                "review_status": 1,
                "accounting_status": 1,
                "financial_handoff_status": 1,
                "financial_source": 1,
                "collected_at": 1,
            },
        ).sort("collected_at", -1).to_list(length=5000)
        assignment_ids = [normalize_text(row.get("assignment_id")) for row in rows if normalize_text(row.get("assignment_id"))]
        assignments = await db[ASSIGNMENTS].find(
            {
                "user_id": merchant_id,
                "driver_id": driver["id"],
                "id": {"$in": assignment_ids},
            },
            {
                "_id": 0,
                "id": 1,
                "status": 1,
                "delivered_at": 1,
                "delivery_fee_snapshot": 1,
            },
        ).to_list(length=5000)
        by_assignment = {normalize_text(row.get("id")): row for row in assignments}
        items = []
        for row in rows:
            assignment = by_assignment.get(normalize_text(row.get("assignment_id"))) or {}
            items.append({
                **row,
                "delivery_status": assignment.get("status"),
                "delivered_at": assignment.get("delivered_at"),
                "delivery_fee": assignment.get("delivery_fee_snapshot"),
            })
        return {"items": items, "total": len(items)}

    async def _move_out_for_delivery(
        *,
        assignment: dict[str, Any],
        actor: dict[str, Any],
        driver: dict[str, Any],
        merchant_id: str,
    ) -> dict[str, Any]:
        if normalize_text(assignment.get("status")) != DELIVERY_STATUS_ASSIGNED:
            raise HTTPException(status_code=409, detail={"code": "driver_delivery_status_transition_invalid"})
        order = await canonical_order_for_assignment(db, user_id=merchant_id, assignment=assignment)
        salla_sync = await _push_salla_delivery_status(
            db,
            user_id=merchant_id,
            assignment=assignment,
            order=order,
            slug="delivering",
        )
        now = _now()
        result = await db[ASSIGNMENTS].find_one_and_update(
            {
                "user_id": merchant_id,
                "id": assignment["id"],
                "driver_id": driver["id"],
                "active": True,
                "status": DELIVERY_STATUS_ASSIGNED,
            },
            {
                "$set": {
                    "status": DELIVERY_STATUS_OUT_FOR_DELIVERY,
                    "out_for_delivery_at": now,
                    "updated_at": now,
                    "salla_status_slug": "delivering",
                    "salla_status_updated_at": now,
                },
                "$unset": _unset_fields(ASSIGNMENT_EXCEPTION_FIELDS),
            },
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
            {
                "$set": {
                    "store_delivery_status": DELIVERY_STATUS_OUT_FOR_DELIVERY,
                    "store_delivery_updated_at": now,
                    "store_delivery_salla_status_slug": "delivering",
                    "store_delivery_salla_status_updated_at": now,
                },
                "$unset": _unset_fields(ORDER_EXCEPTION_FIELDS),
            },
        )
        await db[WORKFLOWS].update_one(
            {
                "user_id": merchant_id,
                "order_number": assignment.get("order_number"),
                "store_delivery_assignment_id": assignment["id"],
            },
            {
                "$set": {
                    "stage": WORKFLOW_DELIVERING,
                    "store_courier_assignment_state": WORKFLOW_DELIVERING,
                    "store_courier_picked_up_at": now,
                    "store_courier_picked_up_by_id": normalize_text(actor.get("id")),
                    "updated_at": now,
                },
                "$unset": _unset_fields(WORKFLOW_EXCEPTION_FIELDS),
            },
        )
        await db[EVENTS].insert_one({
            "id": str(uuid.uuid4()),
            "user_id": merchant_id,
            "event_type": "store_delivery_out_for_delivery",
            "assignment_id": assignment["id"],
            "driver_id": driver["id"],
            "order_id": assignment.get("order_id"),
            "order_number": assignment.get("order_number"),
            "salla_status_slug": salla_sync["slug"],
            "occurred_at": now,
            "actor_account_user_id": normalize_text(actor.get("id")),
        })
        return result

    async def _resume_out_for_delivery(
        *,
        assignment: dict[str, Any],
        actor: dict[str, Any],
        driver: dict[str, Any],
        merchant_id: str,
    ) -> dict[str, Any]:
        if normalize_text(assignment.get("status")) != DELIVERY_STATUS_OUT_FOR_DELIVERY:
            raise HTTPException(status_code=409, detail={"code": "driver_delivery_status_transition_invalid"})
        order = await canonical_order_for_assignment(db, user_id=merchant_id, assignment=assignment)
        salla_sync = await _push_salla_delivery_status(
            db,
            user_id=merchant_id,
            assignment=assignment,
            order=order,
            slug="delivering",
        )
        now = _now()
        result = await db[ASSIGNMENTS].find_one_and_update(
            {
                "user_id": merchant_id,
                "id": assignment["id"],
                "driver_id": driver["id"],
                "active": True,
                "status": DELIVERY_STATUS_OUT_FOR_DELIVERY,
            },
            {
                "$set": {
                    "updated_at": now,
                    "delivery_resumed_at": now,
                    "salla_status_slug": salla_sync["slug"],
                    "salla_status_updated_at": now,
                },
                "$unset": _unset_fields(ASSIGNMENT_EXCEPTION_FIELDS),
            },
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
            {
                "$set": {
                    "store_delivery_status": DELIVERY_STATUS_OUT_FOR_DELIVERY,
                    "store_delivery_updated_at": now,
                    "store_delivery_salla_status_slug": salla_sync["slug"],
                    "store_delivery_salla_status_updated_at": now,
                },
                "$unset": _unset_fields(ORDER_EXCEPTION_FIELDS),
            },
        )
        await db[WORKFLOWS].update_one(
            {
                "user_id": merchant_id,
                "order_number": assignment.get("order_number"),
                "store_delivery_assignment_id": assignment["id"],
            },
            {
                "$set": {
                    "stage": WORKFLOW_DELIVERING,
                    "store_courier_assignment_state": WORKFLOW_DELIVERING,
                    "updated_at": now,
                },
                "$unset": _unset_fields(WORKFLOW_EXCEPTION_FIELDS),
            },
        )
        await db[EVENTS].insert_one({
            "id": str(uuid.uuid4()),
            "user_id": merchant_id,
            "event_type": "store_delivery_resumed",
            "assignment_id": assignment["id"],
            "driver_id": driver["id"],
            "order_id": assignment.get("order_id"),
            "order_number": assignment.get("order_number"),
            "salla_status_slug": salla_sync["slug"],
            "occurred_at": now,
            "actor_account_user_id": normalize_text(actor.get("id")),
        })
        return result

    @router.post("/deliveries/receive-sessions", status_code=201)
    async def open_receive_session(user: dict = Depends(current_user)) -> dict[str, Any]:
        actor = _require_store_driver(user)
        driver = await _driver_for_user(db, actor)
        merchant_id = _merchant_id(driver)
        await ensure_store_delivery_driver_app_indexes(db)
        existing = await db[DRIVER_RECEIVE_SESSIONS].find_one(
            {
                "user_id": merchant_id,
                "driver_id": driver["id"],
                "status": "open",
            },
            {"_id": 0, "user_id": 0},
            sort=[("started_at", -1)],
        )
        if existing:
            return existing
        now = _now()
        row = {
            "id": str(uuid.uuid4()),
            "user_id": merchant_id,
            "driver_id": driver["id"],
            "driver_name_snapshot": driver.get("name"),
            "status": "open",
            "accepted": [],
            "accepted_count": 0,
            "collection_count": 0,
            "collection_total": 0.0,
            "started_at": now,
            "closed_at": None,
            "started_by_account_user_id": normalize_text(actor.get("id")),
        }
        await db[DRIVER_RECEIVE_SESSIONS].insert_one(row)
        row.pop("_id", None)
        row.pop("user_id", None)
        return row

    @router.post("/deliveries/receive-sessions/{session_id}/scan")
    async def scan_receive_session(
        session_id: str,
        payload: DriverReceiveScan,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        actor = _require_store_driver(user)
        driver = await _driver_for_user(db, actor)
        merchant_id = _merchant_id(driver)
        session = await db[DRIVER_RECEIVE_SESSIONS].find_one(
            {
                "user_id": merchant_id,
                "id": normalize_text(session_id),
                "driver_id": driver["id"],
                "status": "open",
            },
            {"_id": 0},
        )
        if not session:
            raise HTTPException(status_code=404, detail={"code": "driver_receive_session_not_found"})
        barcode = normalize_text(payload.barcode)
        assignment = await db[ASSIGNMENTS].find_one(
            {
                "user_id": merchant_id,
                "driver_id": driver["id"],
                "active": True,
                "$or": _true_barcode_match(barcode),
            },
            {"_id": 0},
        )
        if not assignment:
            return {"accepted": False, "code": "driver_assignment_barcode_not_found", "barcode": barcode}
        if normalize_text(assignment.get("status")) == DELIVERY_STATUS_DELIVERED:
            return {"accepted": False, "code": "driver_delivery_already_delivered", "barcode": barcode}
        if normalize_text(assignment.get("status")) == DELIVERY_STATUS_OUT_FOR_DELIVERY:
            return {
                "accepted": False,
                "code": "driver_delivery_already_received",
                "barcode": barcode,
                "order_number": assignment.get("order_number"),
            }
        if assignment.get("id") in {row.get("assignment_id") for row in session.get("accepted") or []}:
            return {"accepted": False, "code": "driver_delivery_already_scanned_in_session", "barcode": barcode}

        order = await canonical_order_for_assignment(db, user_id=merchant_id, assignment=assignment)
        try:
            amount = authoritative_outstanding_amount(order)
            amount_available = True
        except StoreDeliveryRuleError:
            amount = 0.0
            amount_available = False
        updated = await _move_out_for_delivery(
            assignment=assignment,
            actor=actor,
            driver=driver,
            merchant_id=merchant_id,
        )
        accepted = {
            "assignment_id": assignment["id"],
            "order_id": assignment.get("order_id"),
            "order_number": assignment.get("order_number"),
            "barcode": barcode,
            "outstanding_amount": amount,
            "outstanding_amount_available": amount_available,
            "received_at": updated.get("out_for_delivery_at") or _now(),
        }
        update_result = await db[DRIVER_RECEIVE_SESSIONS].update_one(
            {
                "user_id": merchant_id,
                "id": session["id"],
                "status": "open",
            },
            {
                "$push": {"accepted": accepted},
                "$inc": {
                    "accepted_count": 1,
                    "collection_count": 1 if amount > 0 else 0,
                    "collection_total": float(amount),
                },
                "$set": {"updated_at": _now()},
            },
        )
        if update_result.modified_count != 1:
            raise HTTPException(status_code=409, detail={"code": "driver_receive_session_conflict"})
        refreshed = await db[DRIVER_RECEIVE_SESSIONS].find_one(
            {"user_id": merchant_id, "id": session["id"]},
            {"_id": 0, "user_id": 0},
        )
        return {"accepted": True, "delivery": updated, "session": refreshed}

    @router.post("/deliveries/receive-sessions/{session_id}/close")
    async def close_receive_session(session_id: str, user: dict = Depends(current_user)) -> dict[str, Any]:
        actor = _require_store_driver(user)
        driver = await _driver_for_user(db, actor)
        merchant_id = _merchant_id(driver)
        now = _now()
        row = await db[DRIVER_RECEIVE_SESSIONS].find_one_and_update(
            {
                "user_id": merchant_id,
                "id": normalize_text(session_id),
                "driver_id": driver["id"],
                "status": "open",
            },
            {"$set": {
                "status": "closed",
                "closed_at": now,
                "closed_by_account_user_id": normalize_text(actor.get("id")),
            }},
            return_document=True,
            projection={"_id": 0, "user_id": 0},
        )
        if not row:
            raise HTTPException(status_code=404, detail={"code": "driver_receive_session_not_found"})
        return row

    @router.post("/deliveries/exception")
    async def report_delivery_exception(
        payload: DriverDeliveryException,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        actor = _require_store_driver(user)
        driver = await _driver_for_user(db, actor)
        merchant_id = _merchant_id(driver)
        assignment = await db[ASSIGNMENTS].find_one(
            {
                "user_id": merchant_id,
                "driver_id": driver["id"],
                "active": True,
                "status": {"$in": [DELIVERY_STATUS_ASSIGNED, DELIVERY_STATUS_OUT_FOR_DELIVERY]},
                "$or": _barcode_match(payload.barcode),
            },
            {"_id": 0},
        )
        if not assignment:
            raise HTTPException(status_code=404, detail={"code": "driver_assignment_not_found"})
        exception_code = normalize_text(payload.exception_code)
        if exception_code not in DELIVERY_EXCEPTION_CODES:
            raise HTTPException(status_code=422, detail={"code": "driver_delivery_exception_invalid"})
        note = normalize_text(payload.note)
        evidence_reference = normalize_text(payload.evidence_reference)
        evidence_row = None
        if evidence_reference:
            evidence_row = await validate_customer_conversation_reference(
                db,
                user_id=merchant_id,
                driver_id=driver["id"],
                assignment_id=assignment["id"],
                evidence_reference=evidence_reference,
            )
        now = _now()
        evidence_url = (
            f"/api/store-delivery/evidence/customer-conversation/{evidence_reference}"
            if evidence_reference else None
        )
        patch = {
            "delivery_exception_code": exception_code,
            "delivery_exception_note": note or None,
            "delivery_exception_at": now,
            "delivery_exception_by_driver_id": driver["id"],
            "delivery_exception_evidence_reference": evidence_reference or None,
            "delivery_exception_evidence_url": evidence_url,
            "updated_at": now,
        }
        result = await db[ASSIGNMENTS].find_one_and_update(
            {
                "user_id": merchant_id,
                "id": assignment["id"],
                "driver_id": driver["id"],
                "active": True,
                "status": assignment.get("status"),
            },
            {"$set": patch},
            return_document=True,
            projection={"_id": 0, "user_id": 0},
        )
        if not result:
            raise HTTPException(status_code=409, detail={"code": "driver_delivery_exception_conflict"})
        await db[ORDERS].update_one(
            {
                "user_id": merchant_id,
                "$or": [
                    {"order_id": assignment.get("order_id")},
                    {"order_number": assignment.get("order_number")},
                ],
            },
            {"$set": {
                "store_delivery_exception_code": exception_code,
                "store_delivery_exception_note": note or None,
                "store_delivery_exception_at": now,
                "store_delivery_exception_driver_id": driver["id"],
                "store_delivery_exception_evidence_reference": evidence_reference or None,
                "store_delivery_exception_evidence_url": evidence_url,
                "store_delivery_customer_service_attention_required": True,
                "store_delivery_updated_at": now,
            }},
        )
        await db[WORKFLOWS].update_one(
            {
                "user_id": merchant_id,
                "order_number": assignment.get("order_number"),
                "store_delivery_assignment_id": assignment["id"],
            },
            {"$set": {
                "store_courier_exception_code": exception_code,
                "store_courier_exception_note": note or None,
                "store_courier_exception_at": now,
                "store_courier_exception_driver_id": driver["id"],
                "store_courier_exception_evidence_reference": evidence_reference or None,
                "store_courier_exception_evidence_url": evidence_url,
                "customer_service_attention_required": True,
                "updated_at": now,
            }},
        )
        event = {
            "id": str(uuid.uuid4()),
            "user_id": merchant_id,
            "event_type": f"store_delivery_{exception_code}",
            "assignment_id": assignment["id"],
            "driver_id": driver["id"],
            "driver_name_snapshot": assignment.get("driver_name_snapshot") or driver.get("name"),
            "order_id": assignment.get("order_id"),
            "order_number": assignment.get("order_number"),
            "exception_code": exception_code,
            "note": note or None,
            "evidence_reference": evidence_reference or None,
            "evidence_url": evidence_url,
            "customer_service_visible": True,
            "occurred_at": now,
            "actor_account_user_id": normalize_text(actor.get("id")),
        }
        await db[EVENTS].insert_one(event)
        if evidence_row:
            await db[CUSTOMER_CONVERSATION_EVIDENCE].update_one(
                {"user_id": merchant_id, "token": evidence_reference, "status": "uploaded"},
                {"$set": {"status": "bound", "bound_at": now, "bound_event_id": event["id"]}},
            )
        event.pop("_id", None)
        event.pop("user_id", None)
        return {"ok": True, "assignment": result, "event": event}

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
        if target not in DRIVER_STATUS_TRANSITIONS.get(current, frozenset()):
            raise HTTPException(status_code=409, detail={"code": "driver_delivery_status_transition_invalid"})

        conversation_reference = normalize_text(payload.conversation_evidence_reference)
        conversation_row = None
        if conversation_reference:
            conversation_row = await validate_customer_conversation_reference(
                db,
                user_id=merchant_id,
                driver_id=driver["id"],
                assignment_id=assignment["id"],
                evidence_reference=conversation_reference,
            )

        async def _bind_status_conversation(result: dict[str, Any]) -> dict[str, Any]:
            if not conversation_reference or not conversation_row:
                return result
            occurred_at = _now()
            evidence_url = f"/api/store-delivery/evidence/customer-conversation/{conversation_reference}"
            await db[ASSIGNMENTS].update_one(
                {
                    "user_id": merchant_id,
                    "id": assignment["id"],
                    "driver_id": driver["id"],
                    "active": True,
                },
                {"$set": {
                    "delivery_status_evidence_reference": conversation_reference,
                    "delivery_status_evidence_url": evidence_url,
                    "delivery_status_evidence_at": occurred_at,
                }},
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
                    "store_delivery_status_evidence_reference": conversation_reference,
                    "store_delivery_status_evidence_url": evidence_url,
                    "store_delivery_status_evidence_at": occurred_at,
                }},
            )
            event = {
                "id": str(uuid.uuid4()),
                "user_id": merchant_id,
                "event_type": "store_delivery_status_evidence",
                "assignment_id": assignment["id"],
                "driver_id": driver["id"],
                "order_id": assignment.get("order_id"),
                "order_number": assignment.get("order_number"),
                "target_status": target,
                "evidence_reference": conversation_reference,
                "evidence_url": evidence_url,
                "occurred_at": occurred_at,
                "actor_account_user_id": normalize_text(actor.get("id")),
            }
            await db[EVENTS].insert_one(event)
            await db[CUSTOMER_CONVERSATION_EVIDENCE].update_one(
                {"user_id": merchant_id, "token": conversation_reference, "status": "uploaded"},
                {"$set": {
                    "status": "bound",
                    "bound_at": occurred_at,
                    "bound_event_id": event["id"],
                }},
            )
            enriched = dict(result)
            enriched["delivery_status_evidence_reference"] = conversation_reference
            enriched["delivery_status_evidence_url"] = evidence_url
            return enriched

        if target == DELIVERY_STATUS_OUT_FOR_DELIVERY:
            if current == DELIVERY_STATUS_ASSIGNED:
                updated = await _move_out_for_delivery(
                    assignment=assignment,
                    actor=actor,
                    driver=driver,
                    merchant_id=merchant_id,
                )
            else:
                updated = await _resume_out_for_delivery(
                    assignment=assignment,
                    actor=actor,
                    driver=driver,
                    merchant_id=merchant_id,
                )
            return await _bind_status_conversation(updated)

        if target == DELIVERY_STATUS_DELIVERED and current == DELIVERY_STATUS_ASSIGNED:
            await _move_out_for_delivery(
                assignment=assignment,
                actor=actor,
                driver=driver,
                merchant_id=merchant_id,
            )
            assignment = await db[ASSIGNMENTS].find_one(
                {
                    "user_id": merchant_id,
                    "id": assignment["id"],
                    "driver_id": driver["id"],
                    "active": True,
                },
                {"_id": 0},
            )
            if not assignment:
                raise HTTPException(status_code=409, detail={"code": "driver_delivery_status_conflict"})
            current = DELIVERY_STATUS_OUT_FOR_DELIVERY

        now = _now()
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

        proof_reference = normalize_text(payload.delivery_proof_reference)
        if not proof_reference:
            raise HTTPException(status_code=422, detail={"code": "delivery_proof_required"})
        proof_row = await validate_delivery_proof_reference(
            db,
            user_id=merchant_id,
            driver_id=driver["id"],
            assignment_id=assignment["id"],
            proof_reference=proof_reference,
        )
        salla_sync = await _push_salla_delivery_status(
            db,
            user_id=merchant_id,
            assignment=assignment,
            order=order,
            slug="delivered",
        )

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
            "accounting_status": "operational_only",
            "financial_handoff_status": "pending_mz2_driver_balance_link",
            "financial_source": "store_delivery_operational",
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
            "delivery_proof_reference": proof_reference,
            "delivery_proof_url": f"/api/store-delivery/evidence/delivery-proof/{proof_reference}",
            "bank_account_id": normalize_text(payload.bank_account_id),
            "bank_name_snapshot": (bank or {}).get("name") or (bank or {}).get("provider"),
            "review_status": requirements["review_status"],
            "accounting_status": "operational_only",
            "financial_handoff_status": "pending_mz2_driver_balance_link",
            "financial_source": "store_delivery_operational",
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

        # Keep these records operational. After proof binding below, Track F's
        # observer separately applies native V2/P02/Opening/pause gates before
        # recognizing responsibility; it never posts a receipt settlement here.
        accounting_patch = {
            "accounting_status": "operational_only",
            "ledger_txn_group_id": None,
            "accounting_operation_id": None,
            "financial_handoff_status": "pending_mz2_driver_balance_link",
            "financial_source": "store_delivery_operational",
        }
        await db[DRIVER_EARNINGS].update_one(
            {"user_id": merchant_id, "assignment_id": assignment["id"]},
            {"$set": accounting_patch},
        )
        await db[DRIVER_COLLECTIONS].update_one(
            {"user_id": merchant_id, "assignment_id": assignment["id"]},
            {"$set": accounting_patch},
        )

        result = await db[ASSIGNMENTS].find_one_and_update(
            {"user_id": merchant_id, "id": assignment["id"], "status": current},
            {
                "$set": {
                    "status": DELIVERY_STATUS_DELIVERED,
                    "delivered_at": now,
                    "updated_at": now,
                    "collection_amount": requirements["amount"],
                    "collection_method": requirements["payment_method"],
                    "payment_review_status": requirements["review_status"],
                    "receipt_reference": normalize_text(payload.receipt_reference) or None,
                    "receipt_url": collection_row.get("receipt_url"),
                    "delivery_proof_reference": proof_reference,
                    "delivery_proof_url": f"/api/store-delivery/evidence/delivery-proof/{proof_reference}",
                    "salla_status_slug": salla_sync["slug"],
                    "salla_status_updated_at": now,
                    **accounting_patch,
                },
                "$unset": _unset_fields(ASSIGNMENT_EXCEPTION_FIELDS),
            },
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
        await db[DELIVERY_PROOFS].update_one(
            {"user_id": merchant_id, "token": proof_reference, "status": "uploaded"},
            {"$set": {"status": "bound", "bound_at": now, "bound_assignment_id": assignment["id"]}},
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
            {
                "$set": {
                    "store_delivery_assignment_id": assignment["id"],
                    "store_delivery_driver_id": driver["id"],
                    "store_delivery_status": DELIVERY_STATUS_DELIVERED,
                    "store_delivery_delivered_at": now,
                    "store_delivery_collection_amount": requirements["amount"],
                    "store_delivery_collection_method": requirements["payment_method"],
                    "store_delivery_payment_status": payment_state,
                    "store_delivery_payment_review_status": requirements["review_status"],
                    "store_delivery_receipt_reference": normalize_text(payload.receipt_reference) or None,
                    "store_delivery_receipt_url": collection_row.get("receipt_url"),
                    "store_delivery_proof_reference": proof_reference,
                    "store_delivery_proof_url": f"/api/store-delivery/evidence/delivery-proof/{proof_reference}",
                    "store_delivery_salla_status_slug": salla_sync["slug"],
                    "store_delivery_salla_status_updated_at": now,
                    "store_delivery_updated_at": now,
                },
                "$unset": _unset_fields(ORDER_EXCEPTION_FIELDS),
            },
        )
        await db[WORKFLOWS].update_one(
            {
                "user_id": merchant_id,
                "order_number": assignment.get("order_number"),
                "store_delivery_assignment_id": assignment["id"],
            },
            {
                "$set": {
                    "stage": WORKFLOW_DELIVERED,
                    "store_courier_assignment_state": WORKFLOW_DELIVERED,
                    "store_courier_delivered_at": now,
                    "store_courier_delivered_by_id": normalize_text(actor.get("id")),
                    "updated_at": now,
                },
                "$unset": _unset_fields(WORKFLOW_EXCEPTION_FIELDS),
            },
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
            "receipt_reference": normalize_text(payload.receipt_reference) or None,
            "delivery_proof_reference": proof_reference,
            "delivery_proof_url": f"/api/store-delivery/evidence/delivery-proof/{proof_reference}",
            "salla_status_slug": salla_sync["slug"],
            "occurred_at": now,
        })
        delivered_result = {
            **result,
            "earning_amount": earning,
            "collection": requirements,
            "authoritative_outstanding_amount": outstanding_amount,
        }
        # Track F consumes completed operational evidence only after all bound
        # records exist. Pause/P02 failures stay pending; no operational write
        # is rolled back or treated as a financial approval.
        try:
            from accounting_shipping_native_observer import observe_driver_delivery
            await observe_driver_delivery(db, owner=merchant_id, assignment_id=assignment["id"])
        except Exception:
            import logging
            logging.getLogger(__name__).exception("MZ2 driver responsibility observation failed after delivery")
        return await _bind_status_conversation(delivered_result)

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
        cash_collected = round(sum(float(row.get("amount") or 0) for row in collections if row.get("payment_method") == "cash"), 2)
        card_collected = round(sum(float(row.get("amount") or 0) for row in collections if row.get("payment_method") == PAYMENT_METHOD_CARD_TERMINAL), 2)
        bank_transfer_collected = round(sum(float(row.get("amount") or 0) for row in collections if row.get("payment_method") == PAYMENT_METHOD_BANK_TRANSFER), 2)
        return {
            "driver_id": driver["id"],
            "balance_source": "store_delivery_operational",
            "accounting_link_status": "pending_mz2_driver_balance_link",
            "delivery_counts": counts,
            "earnings_total": earnings_total,
            "earnings_paid": earnings_paid,
            "earnings_due": round(max(earnings_total - earnings_paid, 0), 2),
            "cod_cash_collected": cod_collected,
            "cod_cash_remitted": cod_remitted,
            "cod_cash_custody": round(max(cod_collected - cod_remitted, 0), 2),
            "cash_collected": cash_collected,
            "card_collected": card_collected,
            "bank_transfer_collected": bank_transfer_collected,
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
