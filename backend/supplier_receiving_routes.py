"""Supplier receiving sessions for customer-order preparation pieces.

An authorised receiver opens a temporary supplier-scoped session and scans
physical preparation pieces.  A scan records who prepared and who received
the piece independently, while deliberately deferring supplier service
attribution, invoices, liabilities and every Salla/Qoyod write.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from pymongo import ASCENDING, DESCENDING, ReturnDocument
from pymongo.errors import DuplicateKeyError

from fulfillment_v2_routes import _actor_context, _require_permission
from preparation_piece_barcode import parse_preparation_piece_barcode
from preparation_piece_operations import (
    PIECES,
    PIECE_EVENTS,
    PIECE_STATUS_ASSIGNED,
    PIECE_STATUS_BLOCKED,
    PIECE_STATUS_CANCELLED,
    PIECE_STATUS_IN_PROGRESS,
    PIECE_STATUS_READY_FOR_RECEIPT,
    PIECE_STATUS_RECEIVED,
)
from tz_utils import riyadh_now_aware

SUPPLIERS = "suppliers"
SESSIONS = "mezan_supplier_receiving_sessions_v1"
RECEIVING_EVENTS = "mezan_supplier_receiving_events_v1"
RECEIVE_PERMISSION = "inventory.preparation.receive"
MAX_SESSION_SCANS = 5000
SCAN_LOCK_SECONDS = 120
ELIGIBLE_PIECE_STATUSES = {
    PIECE_STATUS_IN_PROGRESS,
    PIECE_STATUS_READY_FOR_RECEIPT,
}


class SupplierReceivingSessionCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_request_id: str = Field(min_length=8, max_length=160)
    supplier_id: str = Field(min_length=1, max_length=160)
    note: str | None = Field(default=None, max_length=1000)


class SupplierPieceScanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    barcode: str = Field(min_length=1, max_length=500)


class SupplierReceivingSessionCloseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: str | None = Field(default=None, max_length=1000)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _actor_name(user: dict[str, Any]) -> str:
    return _text(user.get("name") or user.get("email"))


def _session_reference(now: datetime, session_id: str) -> str:
    local_date = now.astimezone(riyadh_now_aware().tzinfo).strftime("%Y%m%d")
    return f"SR-{local_date}-{session_id[-6:].upper()}"


def _public_piece(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    return {
        key: value
        for key, value in row.items()
        if key not in {"_id", "user_id", "image_b64"}
    }


def _public_session(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    return {
        "id": _text(row.get("id")),
        "reference": _text(row.get("reference")),
        "status": _text(row.get("status")),
        "supplier": dict(row.get("supplier_snapshot") or {}),
        "supplier_context_only": True,
        "supplier_service_link_status": _text(row.get("supplier_service_link_status"))
        or "pending_service_approval",
        "opened_by": _text(row.get("opened_by")),
        "opened_by_name": _text(row.get("opened_by_name")),
        "opened_at": row.get("opened_at"),
        "closed_by": _text(row.get("closed_by")) or None,
        "closed_by_name": _text(row.get("closed_by_name")) or None,
        "closed_at": row.get("closed_at"),
        "note": _text(row.get("note")) or None,
        "close_note": _text(row.get("close_note")) or None,
        "scan_count": int(row.get("scan_count") or 0),
        "order_numbers": list(row.get("order_numbers") or []),
        "file_numbers": list(row.get("file_numbers") or []),
        "preparation_employee_ids": list(row.get("preparation_employee_ids") or []),
        "last_scanned_at": row.get("last_scanned_at"),
        "financial_invoice_created": False,
        "liability_created": False,
        "mezan_only": True,
        "salla_updated": False,
        "qoyod_updated": False,
    }


def piece_scan_blocker(piece: dict[str, Any]) -> dict[str, Any] | None:
    """Return an explicit fail-closed reason for a non-receivable piece."""
    status = _text(piece.get("status")) or PIECE_STATUS_ASSIGNED
    if status == PIECE_STATUS_RECEIVED or piece.get("received_at"):
        return {
            "code": "supplier_piece_already_received",
            "message": "تم استلام هذه القطعة سابقًا؛ لم تُسجّل مرة ثانية.",
            "received_at": piece.get("received_at"),
            "received_by_name": piece.get("received_by_name"),
            "session_reference": piece.get("supplier_receiving_reference"),
        }
    if status in ELIGIBLE_PIECE_STATUSES:
        return None
    if status == PIECE_STATUS_BLOCKED:
        return {
            "code": "supplier_piece_blocked",
            "message": _text(piece.get("block_reason"))
            or "القطعة متوقفة ولا يمكن استلامها حتى معالجة سبب التوقف.",
            "reason": _text(piece.get("block_reason")) or None,
        }
    if status == PIECE_STATUS_CANCELLED:
        return {
            "code": "supplier_piece_cancelled",
            "message": _text(piece.get("cancellation_reason"))
            or "القطعة ملغاة ولا يمكن استلامها.",
            "reason": _text(piece.get("cancellation_reason")) or None,
        }
    if status == PIECE_STATUS_ASSIGNED:
        return {
            "code": "supplier_piece_not_started",
            "message": "ابدأ ملف التجهيز أولًا قبل استلام القطعة من المورد.",
        }
    return {
        "code": "supplier_piece_status_not_receivable",
        "message": "حالة القطعة الحالية لا تسمح بالاستلام.",
        "status": status,
    }


def supplier_receipt_piece_patch(
    *,
    session: dict[str, Any],
    actor: dict[str, Any],
    piece_id: str,
    barcode: str,
    received_at: datetime,
) -> dict[str, Any]:
    """Build the piece mutation without creating a formal supplier link."""
    return {
        "status": PIECE_STATUS_RECEIVED,
        "execution_status": "received_from_supplier",
        "received_at": received_at,
        "received_by_id": _text(actor.get("id")),
        "received_by_name": _actor_name(actor),
        "supplier_receiving_session_id": _text(session.get("id")),
        "supplier_receiving_reference": _text(session.get("reference")),
        "supplier_service_link_status": "pending_service_approval",
        "supplier_receiving_scanned_barcode": _text(barcode),
        "receipt_event_id": uuid.uuid5(
            uuid.NAMESPACE_URL,
            (
                f"supplier-receiving:{session.get('user_id')}:"
                f"{session.get('id')}:{_text(piece_id)}"
            ),
        ).hex,
        "updated_at": received_at,
        "mezan_only": True,
        "salla_updated": False,
        "qoyod_updated": False,
    }


async def ensure_supplier_receiving_indexes(db: Any) -> None:
    await db[SESSIONS].create_index(
        [("user_id", ASCENDING), ("client_request_id", ASCENDING)],
        unique=True,
        name="uq_supplier_receiving_request_v1",
    )
    await db[SESSIONS].create_index(
        [("user_id", ASCENDING), ("opened_by", ASCENDING), ("status", ASCENDING)],
        unique=True,
        partialFilterExpression={"status": "open"},
        name="uq_supplier_receiving_open_actor_v1",
    )
    await db[SESSIONS].create_index(
        [("user_id", ASCENDING), ("opened_at", DESCENDING)],
        name="ix_supplier_receiving_history_v1",
    )
    await db[RECEIVING_EVENTS].create_index(
        [
            ("user_id", ASCENDING),
            ("session_id", ASCENDING),
            ("occurred_at", DESCENDING),
        ],
        name="ix_supplier_receiving_session_events_v1",
    )
    await db[RECEIVING_EVENTS].create_index(
        [("user_id", ASCENDING), ("piece_id", ASCENDING), ("event_type", ASCENDING)],
        unique=True,
        partialFilterExpression={"event_type": "supplier_piece_scanned"},
        name="uq_supplier_receiving_piece_scan_v1",
    )


async def _session_for_actor(
    db: Any,
    *,
    context: dict[str, Any],
    session_id: str,
) -> dict[str, Any]:
    session = await db[SESSIONS].find_one(
        {"user_id": context["merchant_id"], "id": session_id},
        {"_id": 0},
    )
    if not session:
        raise HTTPException(
            status_code=404,
            detail={"code": "supplier_receiving_session_not_found"},
        )
    if (
        not context["is_owner"]
        and _text(session.get("opened_by")) != context["actor_id"]
    ):
        raise HTTPException(
            status_code=403,
            detail={"code": "supplier_receiving_session_owner_required"},
        )
    return session


async def resolve_scanned_piece(
    db: Any,
    *,
    user_id: str,
    barcode: str,
) -> dict[str, Any]:
    """Resolve a unique Mezan piece QR, with a safe legacy order fallback."""
    raw = _text(barcode)
    piece_id = parse_preparation_piece_barcode(raw)
    if piece_id:
        piece = await db[PIECES].find_one(
            {"user_id": user_id, "piece_id": piece_id},
            {"_id": 0},
        )
        if not piece:
            raise HTTPException(
                status_code=404,
                detail={"code": "supplier_piece_barcode_not_found"},
            )
        return piece

    if not raw.isdigit():
        raise HTTPException(
            status_code=422,
            detail={"code": "supplier_piece_barcode_invalid"},
        )

    rows = (
        await db[PIECES]
        .find(
            {"user_id": user_id, "order_number": raw},
            {"_id": 0},
        )
        .sort([("file_number", 1), ("order_item_id", 1), ("unit_index", 1)])
        .limit(25)
        .to_list(25)
    )
    if not rows:
        raise HTTPException(
            status_code=404,
            detail={"code": "supplier_piece_barcode_not_found"},
        )
    if len(rows) == 1:
        return rows[0]
    eligible = [row for row in rows if piece_scan_blocker(row) is None]
    raise HTTPException(
        status_code=409,
        detail={
            "code": "legacy_order_barcode_ambiguous",
            "message": (
                "باركود رقم الطلب القديم يطابق أكثر من قطعة. "
                "أعد تنزيل ملف التجهيز لطباعته بباركود القطعة الفريد."
            ),
            "order_number": raw,
            "candidate_count": len(rows),
            "eligible_candidate_count": len(eligible),
        },
    )


async def _recent_session_events(
    db: Any,
    *,
    user_id: str,
    session_id: str,
    limit: int = 100,
) -> list[dict[str, Any]]:
    rows = (
        await db[RECEIVING_EVENTS]
        .find(
            {
                "user_id": user_id,
                "session_id": session_id,
                "event_type": "supplier_piece_scanned",
            },
            {"_id": 0, "user_id": 0},
        )
        .sort("occurred_at", -1)
        .limit(limit)
        .to_list(limit)
    )
    return rows


def make_supplier_receiving_router(
    db: Any,
    current_user: Callable[..., Any],
) -> APIRouter:
    router = APIRouter(
        prefix="/supplier-receiving-v1",
        tags=["Supplier Receiving V1"],
    )

    @router.get("/catalog")
    async def catalog(
        limit: int = Query(default=50, ge=1, le=200),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        context = await _actor_context(db, user)
        _require_permission(context, RECEIVE_PERMISSION)
        await ensure_supplier_receiving_indexes(db)
        merchant_id = context["merchant_id"]
        session_query: dict[str, Any] = {"user_id": merchant_id}
        if not context["is_owner"]:
            session_query["opened_by"] = context["actor_id"]
        sessions = (
            await db[SESSIONS]
            .find(
                session_query,
                {"_id": 0},
            )
            .sort("opened_at", -1)
            .limit(limit)
            .to_list(limit)
        )
        active = next(
            (
                row
                for row in sessions
                if _text(row.get("status")) == "open"
                and _text(row.get("opened_by")) == context["actor_id"]
            ),
            None,
        )
        suppliers = (
            await db[SUPPLIERS]
            .find(
                {"user_id": merchant_id, "status": {"$ne": "inactive"}},
                {
                    "_id": 0,
                    "id": 1,
                    "company_name": 1,
                    "contact_person": 1,
                    "status": 1,
                },
            )
            .sort("company_name", 1)
            .to_list(2000)
        )
        eligible_count = await db[PIECES].count_documents(
            {
                "user_id": merchant_id,
                "status": {"$in": sorted(ELIGIBLE_PIECE_STATUSES)},
                "$or": [
                    {"supplier_receiving_session_id": {"$exists": False}},
                    {"supplier_receiving_session_id": None},
                    {"supplier_receiving_session_id": ""},
                ],
            }
        )
        return {
            "ok": True,
            "suppliers": suppliers,
            "active_session": _public_session(active),
            "active_session_scans": (
                await _recent_session_events(
                    db,
                    user_id=merchant_id,
                    session_id=_text(active.get("id")),
                )
                if active
                else []
            ),
            "sessions": [_public_session(row) for row in sessions],
            "eligible_piece_count": eligible_count,
            "permissions": {
                "can_open": True,
                "can_scan": True,
                "can_close": True,
            },
            "barcode_mode": "unique_piece_qr",
            "legacy_order_barcode_requires_unique_piece": True,
            "financial_invoice_created_automatically": False,
            "liability_created_automatically": False,
            "mezan_only": True,
        }

    @router.post("/sessions", status_code=201)
    async def open_session(
        payload: SupplierReceivingSessionCreateRequest,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        context = await _actor_context(db, user)
        _require_permission(context, RECEIVE_PERMISSION)
        await ensure_supplier_receiving_indexes(db)
        merchant_id = context["merchant_id"]
        existing_request = await db[SESSIONS].find_one(
            {
                "user_id": merchant_id,
                "client_request_id": _text(payload.client_request_id),
            },
            {"_id": 0},
        )
        if existing_request:
            if (
                _text(existing_request.get("supplier_id")) != _text(payload.supplier_id)
                or _text(existing_request.get("opened_by")) != context["actor_id"]
            ):
                raise HTTPException(
                    status_code=409,
                    detail={"code": "supplier_receiving_request_conflict"},
                )
            return {"ok": True, "session": _public_session(existing_request)}

        open_session_row = await db[SESSIONS].find_one(
            {
                "user_id": merchant_id,
                "opened_by": context["actor_id"],
                "status": "open",
            },
            {"_id": 0},
        )
        if open_session_row:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "supplier_receiving_open_session_exists",
                    "session": _public_session(open_session_row),
                },
            )
        supplier = await db[SUPPLIERS].find_one(
            {
                "user_id": merchant_id,
                "id": _text(payload.supplier_id),
                "status": {"$ne": "inactive"},
            },
            {
                "_id": 0,
                "id": 1,
                "company_name": 1,
                "contact_person": 1,
                "status": 1,
            },
        )
        if not supplier:
            raise HTTPException(
                status_code=404,
                detail={"code": "supplier_receiving_supplier_not_found"},
            )
        now = _now()
        session_id = f"supplier-receiving-{uuid.uuid4().hex}"
        row = {
            "id": session_id,
            "reference": _session_reference(now, session_id),
            "user_id": merchant_id,
            "client_request_id": _text(payload.client_request_id),
            "status": "open",
            "supplier_id": _text(supplier.get("id")),
            "supplier_snapshot": supplier,
            "supplier_context_only": True,
            "supplier_service_link_status": "pending_service_approval",
            "opened_by": context["actor_id"],
            "opened_by_name": _actor_name(user),
            "opened_at": now,
            "note": _text(payload.note) or None,
            "scan_count": 0,
            "order_numbers": [],
            "file_numbers": [],
            "preparation_employee_ids": [],
            "created_at": now,
            "updated_at": now,
            "financial_invoice_created": False,
            "liability_created": False,
            "mezan_only": True,
            "salla_updated": False,
            "qoyod_updated": False,
        }
        try:
            await db[SESSIONS].insert_one(dict(row))
        except DuplicateKeyError as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": "supplier_receiving_open_session_exists"},
            ) from exc
        await db[RECEIVING_EVENTS].insert_one(
            {
                "id": uuid.uuid4().hex,
                "user_id": merchant_id,
                "session_id": session_id,
                "event_type": "supplier_receiving_session_opened",
                "supplier_context": supplier,
                "actor_id": context["actor_id"],
                "actor_name": _actor_name(user),
                "occurred_at": now,
                "mezan_only": True,
                "salla_updated": False,
                "qoyod_updated": False,
            }
        )
        return {"ok": True, "session": _public_session(row)}

    @router.get("/sessions/{session_id}")
    async def get_session(
        session_id: str,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        context = await _actor_context(db, user)
        _require_permission(context, RECEIVE_PERMISSION)
        session = await _session_for_actor(
            db,
            context=context,
            session_id=session_id,
        )
        return {
            "ok": True,
            "session": _public_session(session),
            "scans": await _recent_session_events(
                db,
                user_id=context["merchant_id"],
                session_id=session_id,
                limit=200,
            ),
        }

    @router.post("/sessions/{session_id}/scan")
    async def scan_piece(
        session_id: str,
        payload: SupplierPieceScanRequest,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        context = await _actor_context(db, user)
        _require_permission(context, RECEIVE_PERMISSION)
        session = await _session_for_actor(
            db,
            context=context,
            session_id=session_id,
        )
        if _text(session.get("status")) != "open":
            raise HTTPException(
                status_code=409,
                detail={"code": "supplier_receiving_session_closed"},
            )
        if int(session.get("scan_count") or 0) >= MAX_SESSION_SCANS:
            raise HTTPException(
                status_code=409,
                detail={"code": "supplier_receiving_session_scan_limit"},
            )
        lock_started_at = _now()
        lock_token = uuid.uuid4().hex
        session = await db[SESSIONS].find_one_and_update(
            {
                "user_id": context["merchant_id"],
                "id": session_id,
                "status": "open",
                "opened_by": context["actor_id"],
                "$or": [
                    {"scan_lock_token": {"$exists": False}},
                    {"scan_lock_token": None},
                    {"scan_lock_expires_at": {"$lte": lock_started_at}},
                ],
            },
            {
                "$set": {
                    "scan_lock_token": lock_token,
                    "scan_lock_started_at": lock_started_at,
                    "scan_lock_expires_at": lock_started_at
                    + timedelta(seconds=SCAN_LOCK_SECONDS),
                    "updated_at": lock_started_at,
                }
            },
            return_document=ReturnDocument.AFTER,
        )
        if not session:
            latest = await db[SESSIONS].find_one(
                {"user_id": context["merchant_id"], "id": session_id},
                {"_id": 0, "status": 1},
            )
            code = (
                "supplier_receiving_session_closed"
                if _text((latest or {}).get("status")) != "open"
                else "supplier_receiving_scan_busy"
            )
            raise HTTPException(status_code=409, detail={"code": code})
        barcode = _text(payload.barcode)
        try:
            piece = await resolve_scanned_piece(
                db,
                user_id=context["merchant_id"],
                barcode=barcode,
            )
            blocker = piece_scan_blocker(piece)
            if blocker:
                raise HTTPException(status_code=409, detail=blocker)

            now = _now()
            patch = supplier_receipt_piece_patch(
                session=session,
                actor=user,
                piece_id=_text(piece.get("piece_id")),
                barcode=barcode,
                received_at=now,
            )
            updated_piece = await db[PIECES].find_one_and_update(
                {
                    "user_id": context["merchant_id"],
                    "piece_id": _text(piece.get("piece_id")),
                    "status": {"$in": sorted(ELIGIBLE_PIECE_STATUSES)},
                    "$or": [
                        {"supplier_receiving_session_id": {"$exists": False}},
                        {"supplier_receiving_session_id": None},
                        {"supplier_receiving_session_id": ""},
                    ],
                },
                {"$set": patch},
                return_document=ReturnDocument.AFTER,
            )
            if not updated_piece:
                latest = await db[PIECES].find_one(
                    {
                        "user_id": context["merchant_id"],
                        "piece_id": _text(piece.get("piece_id")),
                    },
                    {"_id": 0},
                )
                raise HTTPException(
                    status_code=409,
                    detail=piece_scan_blocker(latest or {})
                    or {
                        "code": "supplier_piece_scan_conflict",
                        "message": "تغيّرت القطعة أثناء المسح؛ حدّث الجلسة.",
                    },
                )

            updated_session = await db[SESSIONS].find_one_and_update(
                {
                    "user_id": context["merchant_id"],
                    "id": session_id,
                    "status": "open",
                    "opened_by": context["actor_id"],
                    "scan_lock_token": lock_token,
                },
                {
                    "$inc": {"scan_count": 1},
                    "$addToSet": {
                        "order_numbers": _text(updated_piece.get("order_number")),
                        "file_numbers": _text(updated_piece.get("file_number")),
                        "preparation_employee_ids": _text(
                            updated_piece.get("responsible_employee_id")
                        ),
                    },
                    "$set": {"last_scanned_at": now, "updated_at": now},
                    "$unset": {
                        "scan_lock_token": "",
                        "scan_lock_started_at": "",
                        "scan_lock_expires_at": "",
                    },
                },
                return_document=ReturnDocument.AFTER,
            )
            if updated_session:
                session = updated_session
            else:
                # The received piece is authoritative. If the short-lived lock
                # expires during a slow call, closing repairs the session count
                # from the pieces linked to the session.
                latest_session = await db[SESSIONS].find_one(
                    {"user_id": context["merchant_id"], "id": session_id},
                    {"_id": 0},
                )
                if latest_session:
                    session = latest_session
        except Exception:
            await db[SESSIONS].update_one(
                {
                    "user_id": context["merchant_id"],
                    "id": session_id,
                    "scan_lock_token": lock_token,
                },
                {
                    "$set": {"updated_at": _now()},
                    "$unset": {
                        "scan_lock_token": "",
                        "scan_lock_started_at": "",
                        "scan_lock_expires_at": "",
                    },
                },
            )
            raise

        event = {
            "id": _text(updated_piece.get("receipt_event_id")),
            "user_id": context["merchant_id"],
            "session_id": session_id,
            "session_reference": _text(session.get("reference")),
            "event_type": "supplier_piece_scanned",
            "piece_id": _text(updated_piece.get("piece_id")),
            "batch_id": _text(updated_piece.get("batch_id")),
            "file_number": _text(updated_piece.get("file_number")),
            "order_number": _text(updated_piece.get("order_number")),
            "order_item_id": _text(updated_piece.get("order_item_id")),
            "unit_index": updated_piece.get("unit_index"),
            "product_id": updated_piece.get("product_id"),
            "product_name": updated_piece.get("product_name"),
            "sku": updated_piece.get("sku"),
            "selected_image_url": updated_piece.get("selected_image_url"),
            "preparation_employee_id": _text(
                updated_piece.get("responsible_employee_id")
            ),
            "preparation_employee_name": _text(
                updated_piece.get("responsible_employee_name")
            ),
            "receiving_employee_id": context["actor_id"],
            "receiving_employee_name": _actor_name(user),
            "services": list(updated_piece.get("services") or []),
            "remaining_service_count": int(
                updated_piece.get("remaining_service_count") or 0
            ),
            "supplier_context": dict(session.get("supplier_snapshot") or {}),
            "supplier_service_link_status": "pending_service_approval",
            "scanned_barcode": barcode,
            "occurred_at": now,
            "financial_invoice_created": False,
            "liability_created": False,
            "mezan_only": True,
            "salla_updated": False,
            "qoyod_updated": False,
        }
        await db[RECEIVING_EVENTS].update_one(
            {"id": event["id"]},
            {"$setOnInsert": event},
            upsert=True,
        )
        await db[PIECE_EVENTS].update_one(
            {"id": event["id"]},
            {"$setOnInsert": event},
            upsert=True,
        )
        return {
            "ok": True,
            "piece": _public_piece(updated_piece),
            "session": _public_session(session),
            "scan": {key: value for key, value in event.items() if key != "user_id"},
            "supplier_service_link_created": False,
            "financial_invoice_created": False,
            "liability_created": False,
            "salla_updated": False,
            "qoyod_updated": False,
        }

    @router.post("/sessions/{session_id}/close")
    async def close_session(
        session_id: str,
        payload: SupplierReceivingSessionCloseRequest,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        context = await _actor_context(db, user)
        _require_permission(context, RECEIVE_PERMISSION)
        session = await _session_for_actor(
            db,
            context=context,
            session_id=session_id,
        )
        if _text(session.get("status")) == "closed":
            return {"ok": True, "session": _public_session(session)}
        if _text(session.get("status")) != "open":
            raise HTTPException(
                status_code=409,
                detail={"code": "supplier_receiving_session_not_open"},
            )
        actual_count = await db[PIECES].count_documents(
            {
                "user_id": context["merchant_id"],
                "supplier_receiving_session_id": session_id,
                "status": PIECE_STATUS_RECEIVED,
            }
        )
        now = _now()
        updated = await db[SESSIONS].find_one_and_update(
            {
                "user_id": context["merchant_id"],
                "id": session_id,
                "status": "open",
                "opened_by": context["actor_id"],
                "$or": [
                    {"scan_lock_token": {"$exists": False}},
                    {"scan_lock_token": None},
                    {"scan_lock_expires_at": {"$lte": now}},
                ],
            },
            {
                "$set": {
                    "status": "closed",
                    "scan_count": actual_count,
                    "closed_at": now,
                    "closed_by": context["actor_id"],
                    "closed_by_name": _actor_name(user),
                    "close_note": _text(payload.note) or None,
                    "supplier_service_link_status": "pending_service_approval",
                    "updated_at": now,
                }
            },
            return_document=ReturnDocument.AFTER,
        )
        if not updated:
            latest = await db[SESSIONS].find_one(
                {"user_id": context["merchant_id"], "id": session_id},
                {"_id": 0, "status": 1},
            )
            if _text((latest or {}).get("status")) == "open":
                raise HTTPException(
                    status_code=409,
                    detail={"code": "supplier_receiving_scan_busy"},
                )
            raise HTTPException(
                status_code=409,
                detail={"code": "supplier_receiving_session_close_conflict"},
            )
        event_id = uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"supplier-receiving-close:{context['merchant_id']}:{session_id}",
        ).hex
        await db[RECEIVING_EVENTS].update_one(
            {"id": event_id},
            {
                "$setOnInsert": {
                    "id": event_id,
                    "user_id": context["merchant_id"],
                    "session_id": session_id,
                    "session_reference": _text(updated.get("reference")),
                    "event_type": "supplier_receiving_session_closed",
                    "scan_count": actual_count,
                    "actor_id": context["actor_id"],
                    "actor_name": _actor_name(user),
                    "note": _text(payload.note) or None,
                    "occurred_at": now,
                    "supplier_service_link_status": "pending_service_approval",
                    "financial_invoice_created": False,
                    "liability_created": False,
                    "mezan_only": True,
                    "salla_updated": False,
                    "qoyod_updated": False,
                }
            },
            upsert=True,
        )
        return {
            "ok": True,
            "session": _public_session(updated),
            "next_step": "supplier_service_invoice_draft",
            "supplier_service_link_created": False,
            "financial_invoice_created": False,
            "liability_created": False,
            "salla_updated": False,
            "qoyod_updated": False,
        }

    return router


__all__ = [
    "ELIGIBLE_PIECE_STATUSES",
    "RECEIVING_EVENTS",
    "SESSIONS",
    "SupplierPieceScanRequest",
    "SupplierReceivingSessionCloseRequest",
    "SupplierReceivingSessionCreateRequest",
    "ensure_supplier_receiving_indexes",
    "make_supplier_receiving_router",
    "piece_scan_blocker",
    "resolve_scanned_piece",
    "supplier_receipt_piece_patch",
]
