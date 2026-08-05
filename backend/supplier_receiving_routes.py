"""Supplier receiving sessions for customer-order preparation pieces.

An authorised receiver opens a temporary supplier-scoped session and scans
physical preparation pieces. A scan records who prepared and who received
the piece independently, applies the approved Mezan 2 supplier-service link,
and the final save stores a Mezan-only operational invoice snapshot. Financial
invoices, liabilities and every Salla/Qoyod write remain deliberately deferred.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from pymongo import ASCENDING, DESCENDING, ReturnDocument
from pymongo.errors import DuplicateKeyError

from fulfillment_v2_routes import _actor_context, _require_permission
from mezan_supplier_management_routes import MEZAN_SUPPLIERS_V2
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

SUPPLIERS = MEZAN_SUPPLIERS_V2
SESSIONS = "mezan_supplier_receiving_sessions_v1"
RECEIVING_EVENTS = "mezan_supplier_receiving_events_v1"
RECEIVE_PERMISSION = "inventory.preparation.receive"
MAX_SESSION_SCANS = 5000
SCAN_LOCK_SECONDS = 120
ELIGIBLE_PIECE_STATUSES = {
    PIECE_STATUS_IN_PROGRESS,
    PIECE_STATUS_READY_FOR_RECEIPT,
}
RECEIPT_PIECE_FIELDS = (
    "status",
    "execution_status",
    "received_at",
    "received_by_id",
    "received_by_name",
    "supplier_receiving_session_id",
    "supplier_receiving_reference",
    "supplier_id",
    "supplier_name",
    "supplier_service_ids",
    "supplier_service_link_status",
    "supplier_receiving_scanned_barcode",
    "receipt_event_id",
    "updated_at",
    "mezan_only",
    "salla_updated",
    "qoyod_updated",
)


class SupplierReceivingSessionCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_request_id: str = Field(min_length=8, max_length=160)
    supplier_id: str = Field(min_length=1, max_length=160)
    note: str | None = Field(default=None, max_length=1000)


class SupplierPieceScanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    barcode: str = Field(min_length=1, max_length=500)


class SupplierReceivingInvoiceLineRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    piece_ids: list[str] = Field(min_length=1, max_length=5000)
    unit_price_halalas: int = Field(ge=0, le=100_000_000_000)


class SupplierReceivingSessionCloseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: str | None = Field(default=None, max_length=1000)
    invoice_lines: list[SupplierReceivingInvoiceLineRequest] = Field(
        default_factory=list,
        max_length=5000,
    )


class SupplierReceivingSessionCancelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: str | None = Field(default=None, max_length=1000)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _actor_name(user: dict[str, Any]) -> str:
    return _text(user.get("name") or user.get("email"))


def _halalas(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if not amount.is_finite() or amount < 0:
        return None
    return int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def supplier_piece_reference_price(piece: dict[str, Any]) -> dict[str, Any]:
    """Calculate one piece's suggested supplier price from its service plan."""
    total_halalas = 0
    missing_service_ids: list[str] = []
    services = list(piece.get("services") or [])
    for service in services:
        unit_cost_halalas = _halalas(service.get("reference_unit_cost"))
        service_id = _text(service.get("service_id"))
        try:
            quantity = Decimal(str(service.get("required_quantity") or 1))
        except (InvalidOperation, TypeError, ValueError):
            quantity = Decimal("1")
        if not quantity.is_finite() or quantity <= 0:
            quantity = Decimal("1")
        if unit_cost_halalas is None:
            missing_service_ids.append(service_id)
            continue
        total_halalas += int(
            (Decimal(unit_cost_halalas) * quantity).quantize(
                Decimal("1"),
                rounding=ROUND_HALF_UP,
            )
        )
    return {
        "reference_unit_price_halalas": total_halalas,
        "reference_price_complete": bool(services) and not missing_service_ids,
        "missing_price_service_ids": missing_service_ids,
    }


def _invoice_group_key(scan: dict[str, Any]) -> tuple[Any, ...]:
    services = tuple(sorted(
        (
            _text(service.get("service_id")),
            str(service.get("required_quantity") or 1),
        )
        for service in (scan.get("services") or [])
        if _text(service.get("service_id"))
    ))
    return (
        _text(scan.get("product_id")),
        _text(scan.get("sku")),
        _text(scan.get("product_name")).casefold(),
        services,
    )


def build_supplier_receiving_invoice(
    *,
    session: dict[str, Any],
    scans: list[dict[str, Any]],
    requested_lines: list[SupplierReceivingInvoiceLineRequest],
    saved_at: datetime,
) -> dict[str, Any]:
    """Validate the full scanned set and create a Mezan-only invoice snapshot."""
    scans_by_piece = {
        _text(scan.get("piece_id")): scan
        for scan in scans
        if _text(scan.get("piece_id"))
    }
    requested_piece_ids: list[str] = []
    public_lines: list[dict[str, Any]] = []
    for line_number, line in enumerate(requested_lines, start=1):
        piece_ids = [_text(piece_id) for piece_id in line.piece_ids if _text(piece_id)]
        if len(piece_ids) != len(line.piece_ids) or len(set(piece_ids)) != len(piece_ids):
            raise HTTPException(
                status_code=409,
                detail={"code": "supplier_receiving_invoice_duplicate_piece"},
            )
        line_scans = [scans_by_piece.get(piece_id) for piece_id in piece_ids]
        if any(scan is None for scan in line_scans):
            raise HTTPException(
                status_code=409,
                detail={"code": "supplier_receiving_invoice_piece_mismatch"},
            )
        group_keys = {_invoice_group_key(scan or {}) for scan in line_scans}
        if len(group_keys) != 1:
            raise HTTPException(
                status_code=409,
                detail={"code": "supplier_receiving_invoice_group_mismatch"},
            )
        first = line_scans[0] or {}
        quantity = len(piece_ids)
        total_halalas = quantity * int(line.unit_price_halalas)
        public_lines.append({
            "line_number": line_number,
            "product_id": _text(first.get("product_id")) or None,
            "product_name": _text(first.get("product_name")) or "منتج",
            "sku": _text(first.get("sku")) or None,
            "quantity": quantity,
            "unit_price_halalas": int(line.unit_price_halalas),
            "total_halalas": total_halalas,
            "piece_ids": piece_ids,
            "services": list(first.get("services") or []),
        })
        requested_piece_ids.extend(piece_ids)

    expected_piece_ids = set(scans_by_piece)
    if (
        len(requested_piece_ids) != len(set(requested_piece_ids))
        or set(requested_piece_ids) != expected_piece_ids
    ):
        raise HTTPException(
            status_code=409,
            detail={"code": "supplier_receiving_invoice_piece_mismatch"},
        )

    subtotal_halalas = sum(line["total_halalas"] for line in public_lines)
    return {
        "reference": _text(session.get("reference")),
        "status": "saved",
        "currency": "SAR",
        "line_count": len(public_lines),
        "piece_count": len(expected_piece_ids),
        "subtotal_halalas": subtotal_halalas,
        "total_halalas": subtotal_halalas,
        "lines": public_lines,
        "saved_at": saved_at,
        "financial_invoice_created": False,
        "liability_created": False,
        "mezan_only": True,
    }


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
        "supplier_context_only": False,
        "supplier_operational_linked": True,
        "supplier_service_link_status": _text(row.get("supplier_service_link_status"))
        or "catalog_linked",
        "opened_by": _text(row.get("opened_by")),
        "opened_by_name": _text(row.get("opened_by_name")),
        "opened_at": row.get("opened_at"),
        "closed_by": _text(row.get("closed_by")) or None,
        "closed_by_name": _text(row.get("closed_by_name")) or None,
        "closed_at": row.get("closed_at"),
        "note": _text(row.get("note")) or None,
        "close_note": _text(row.get("close_note")) or None,
        "cancelled_by": _text(row.get("cancelled_by")) or None,
        "cancelled_by_name": _text(row.get("cancelled_by_name")) or None,
        "cancelled_at": row.get("cancelled_at"),
        "cancel_note": _text(row.get("cancel_note")) or None,
        "cancelled_piece_count": int(row.get("cancelled_piece_count") or 0),
        "scan_count": int(row.get("scan_count") or 0),
        "order_numbers": list(row.get("order_numbers") or []),
        "file_numbers": list(row.get("file_numbers") or []),
        "preparation_employee_ids": list(row.get("preparation_employee_ids") or []),
        "last_scanned_at": row.get("last_scanned_at"),
        "operational_invoice": dict(row.get("operational_invoice") or {}) or None,
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


def supplier_piece_service_blocker(
    piece: dict[str, Any],
    session: dict[str, Any],
) -> dict[str, Any] | None:
    """Require the selected Mezan 2 supplier to cover every piece service."""
    required_services = [
        row for row in (piece.get("services") or [])
        if _text(row.get("service_id"))
    ]
    if not required_services:
        return {
            "code": "supplier_piece_services_missing",
            "message": (
                "هذه القطعة لا تحتوي على خدمة تجهيز مرتبطة. "
                "اربط المنتج بخدمة من مكونات المنتجات قبل الاستلام من المورد."
            ),
        }
    supplier = dict(session.get("supplier_snapshot") or {})
    provided_ids = {
        _text(row.get("service_id"))
        for row in (supplier.get("service_links") or [])
        if _text(row.get("service_id"))
    }
    missing = [
        {
            "service_id": _text(row.get("service_id")),
            "service_name": _text(row.get("service_name"))
            or _text(row.get("service_code"))
            or _text(row.get("service_id")),
        }
        for row in required_services
        if _text(row.get("service_id")) not in provided_ids
    ]
    if not missing:
        return None
    return {
        "code": "supplier_piece_service_mismatch",
        "message": (
            "المورد المحدد لا يقدم جميع الخدمات المطلوبة لهذه القطعة."
        ),
        "supplier_id": _text(supplier.get("id")),
        "supplier_name": _text(supplier.get("company_name")),
        "missing_services": missing,
    }


def supplier_receipt_piece_patch(
    *,
    session: dict[str, Any],
    actor: dict[str, Any],
    piece_id: str,
    barcode: str,
    received_at: datetime,
) -> dict[str, Any]:
    """Build the operational supplier link without accounting side effects."""
    supplier = dict(session.get("supplier_snapshot") or {})
    supplier_service_ids = [
        _text(row.get("service_id"))
        for row in (supplier.get("service_links") or [])
        if _text(row.get("service_id"))
    ]
    return {
        "status": PIECE_STATUS_RECEIVED,
        "execution_status": "received_from_supplier",
        "received_at": received_at,
        "received_by_id": _text(actor.get("id")),
        "received_by_name": _actor_name(actor),
        "supplier_receiving_session_id": _text(session.get("id")),
        "supplier_receiving_reference": _text(session.get("reference")),
        "supplier_id": _text(supplier.get("id")),
        "supplier_name": _text(supplier.get("company_name")),
        "supplier_service_ids": supplier_service_ids,
        "supplier_service_link_status": "catalog_linked",
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


def supplier_receipt_previous_piece_state(piece: dict[str, Any]) -> dict[str, Any]:
    """Capture only fields changed by a supplier scan so cancel can restore them."""
    present_fields = [field for field in RECEIPT_PIECE_FIELDS if field in piece]
    return {
        "previous_piece_state": {
            field: piece.get(field)
            for field in present_fields
        },
        "previous_piece_present_fields": present_fields,
    }


def supplier_receipt_piece_rollback_update(
    scan_event: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """Build an exact inverse of the fields changed by one supplier scan."""
    state = scan_event.get("previous_piece_state")
    present_fields = scan_event.get("previous_piece_present_fields")
    if not isinstance(state, dict) or not isinstance(present_fields, list):
        raise ValueError("supplier_receiving_cancel_rollback_unavailable")
    allowed = set(RECEIPT_PIECE_FIELDS)
    present = {
        _text(field)
        for field in present_fields
        if _text(field) in allowed
    }
    update: dict[str, dict[str, Any]] = {
        "$set": {
            field: state.get(field)
            for field in present
        },
        "$unset": {
            field: ""
            for field in RECEIPT_PIECE_FIELDS
            if field not in present
        },
    }
    if not update["$set"]:
        update.pop("$set")
    if not update["$unset"]:
        update.pop("$unset")
    return update


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
            {
                "_id": 0,
                "user_id": 0,
                "previous_piece_state": 0,
                "previous_piece_present_fields": 0,
            },
        )
        .sort("occurred_at", -1)
        .limit(limit)
        .to_list(limit)
    )
    return rows


async def _cancellable_session_events(
    db: Any,
    *,
    user_id: str,
    session_id: str,
) -> list[dict[str, Any]]:
    """Return internal scan snapshots, including partially rolled-back retries."""
    return (
        await db[RECEIVING_EVENTS]
        .find(
            {
                "user_id": user_id,
                "session_id": session_id,
                "event_type": {
                    "$in": [
                        "supplier_piece_scanned",
                        "supplier_piece_scan_cancelled",
                    ]
                },
            },
            {"_id": 0},
        )
        .sort("occurred_at", -1)
        .limit(MAX_SESSION_SCANS)
        .to_list(MAX_SESSION_SCANS)
    )


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
                if _text(row.get("status")) in {"open", "cancelling"}
                and _text(row.get("opened_by")) == context["actor_id"]
            ),
            None,
        )
        suppliers = (
            await db[SUPPLIERS]
            .find(
                {
                    "user_id": merchant_id,
                    "status": {"$ne": "inactive"},
                    "service_ids.0": {"$exists": True},
                },
                {
                    "_id": 0,
                    "id": 1,
                    "company_name": 1,
                    "contact_person": 1,
                    "status": 1,
                    "service_ids": 1,
                    "service_links": 1,
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
                    limit=MAX_SESSION_SCANS,
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
                "can_cancel": True,
            },
            "barcode_mode": "unique_piece_qr",
            "legacy_order_barcode_requires_unique_piece": True,
            "financial_invoice_created_automatically": False,
            "liability_created_automatically": False,
            "supplier_source": "mezan_suppliers_v2",
            "legacy_supplier_data_used": False,
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
                "status": {"$in": ["open", "cancelling"]},
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
                "service_ids": 1,
                "service_links": 1,
            },
        )
        if not supplier:
            raise HTTPException(
                status_code=404,
                detail={"code": "supplier_receiving_supplier_not_found"},
            )
        if not list(supplier.get("service_links") or []):
            raise HTTPException(
                status_code=409,
                detail={"code": "supplier_receiving_supplier_services_required"},
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
            "supplier_context_only": False,
            "supplier_operational_linked": True,
            "supplier_service_link_status": "catalog_linked",
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
                limit=MAX_SESSION_SCANS,
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
            service_blocker = supplier_piece_service_blocker(piece, session)
            if service_blocker:
                raise HTTPException(status_code=409, detail=service_blocker)

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
            "supplier_service_link_status": "catalog_linked",
            "scanned_barcode": barcode,
            "occurred_at": now,
            "financial_invoice_created": False,
            "liability_created": False,
            "mezan_only": True,
            "salla_updated": False,
            "qoyod_updated": False,
        }
        event.update(supplier_piece_reference_price(updated_piece))
        event.update(supplier_receipt_previous_piece_state(piece))
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
            "scan": {
                key: value
                for key, value in event.items()
                if key not in {
                    "user_id",
                    "previous_piece_state",
                    "previous_piece_present_fields",
                }
            },
            "supplier_service_link_applied": True,
            "financial_invoice_created": False,
            "liability_created": False,
            "salla_updated": False,
            "qoyod_updated": False,
        }

    @router.post("/sessions/{session_id}/cancel")
    async def cancel_session(
        session_id: str,
        payload: SupplierReceivingSessionCancelRequest,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        context = await _actor_context(db, user)
        _require_permission(context, RECEIVE_PERMISSION)
        session = await _session_for_actor(
            db,
            context=context,
            session_id=session_id,
        )
        status = _text(session.get("status"))
        if status == "cancelled":
            return {
                "ok": True,
                "session": _public_session(session),
                "cancelled": True,
            }
        if status == "closed":
            raise HTTPException(
                status_code=409,
                detail={"code": "supplier_receiving_session_closed"},
            )
        if status not in {"open", "cancelling"}:
            raise HTTPException(
                status_code=409,
                detail={"code": "supplier_receiving_session_not_open"},
            )

        scans = await _cancellable_session_events(
            db,
            user_id=context["merchant_id"],
            session_id=session_id,
        )
        unavailable = [
            scan
            for scan in scans
            if not scan.get("rolled_back")
            and (
                not isinstance(scan.get("previous_piece_state"), dict)
                or not isinstance(scan.get("previous_piece_present_fields"), list)
            )
        ]
        if unavailable:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "supplier_receiving_cancel_rollback_unavailable",
                    "piece_count": len(unavailable),
                },
            )

        now = _now()
        if status == "open":
            session = await db[SESSIONS].find_one_and_update(
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
                        "status": "cancelling",
                        "cancellation_started_at": now,
                        "cancelled_by": context["actor_id"],
                        "cancelled_by_name": _actor_name(user),
                        "cancel_note": _text(payload.note) or None,
                        "updated_at": now,
                    }
                },
                return_document=ReturnDocument.AFTER,
            )
            if not session:
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
                    detail={"code": "supplier_receiving_session_cancel_conflict"},
                )

        rolled_back_count = 0
        for scan in scans:
            if scan.get("rolled_back") is True:
                rolled_back_count += 1
                continue
            try:
                rollback_update = supplier_receipt_piece_rollback_update(scan)
            except ValueError as exc:
                raise HTTPException(
                    status_code=409,
                    detail={"code": str(exc)},
                ) from exc
            result = await db[PIECES].update_one(
                {
                    "user_id": context["merchant_id"],
                    "piece_id": _text(scan.get("piece_id")),
                    "supplier_receiving_session_id": session_id,
                    "receipt_event_id": _text(scan.get("id")),
                },
                rollback_update,
            )
            if not result.modified_count:
                latest_piece = await db[PIECES].find_one(
                    {
                        "user_id": context["merchant_id"],
                        "piece_id": _text(scan.get("piece_id")),
                    },
                    {
                        "_id": 0,
                        "supplier_receiving_session_id": 1,
                        "receipt_event_id": 1,
                    },
                )
                if (
                    _text((latest_piece or {}).get("supplier_receiving_session_id"))
                    == session_id
                    or _text((latest_piece or {}).get("receipt_event_id"))
                    == _text(scan.get("id"))
                ):
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "code": "supplier_receiving_cancel_piece_conflict",
                            "piece_id": _text(scan.get("piece_id")),
                        },
                    )
            rollback_event_patch = {
                "event_type": "supplier_piece_scan_cancelled",
                "original_event_type": "supplier_piece_scanned",
                "rolled_back": True,
                "rolled_back_at": now,
                "rolled_back_by": context["actor_id"],
                "rolled_back_by_name": _actor_name(user),
                "cancel_note": _text(payload.note) or None,
                "financial_invoice_created": False,
                "liability_created": False,
                "salla_updated": False,
                "qoyod_updated": False,
            }
            await db[RECEIVING_EVENTS].update_one(
                {
                    "user_id": context["merchant_id"],
                    "session_id": session_id,
                    "id": _text(scan.get("id")),
                },
                {"$set": rollback_event_patch},
            )
            await db[PIECE_EVENTS].update_one(
                {
                    "user_id": context["merchant_id"],
                    "id": _text(scan.get("id")),
                },
                {"$set": rollback_event_patch},
            )
            rolled_back_count += 1

        cancelled_at = _now()
        updated = await db[SESSIONS].find_one_and_update(
            {
                "user_id": context["merchant_id"],
                "id": session_id,
                "status": "cancelling",
                "opened_by": context["actor_id"],
            },
            {
                "$set": {
                    "status": "cancelled",
                    "cancelled_at": cancelled_at,
                    "cancelled_by": context["actor_id"],
                    "cancelled_by_name": _actor_name(user),
                    "cancel_note": _text(payload.note) or None,
                    "cancelled_piece_count": rolled_back_count,
                    "updated_at": cancelled_at,
                    "financial_invoice_created": False,
                    "liability_created": False,
                    "salla_updated": False,
                    "qoyod_updated": False,
                },
                "$unset": {
                    "scan_lock_token": "",
                    "scan_lock_started_at": "",
                    "scan_lock_expires_at": "",
                    "operational_invoice": "",
                },
            },
            return_document=ReturnDocument.AFTER,
        )
        if not updated:
            latest = await db[SESSIONS].find_one(
                {"user_id": context["merchant_id"], "id": session_id},
                {"_id": 0},
            )
            if _text((latest or {}).get("status")) == "cancelled":
                updated = latest
            else:
                raise HTTPException(
                    status_code=409,
                    detail={"code": "supplier_receiving_session_cancel_conflict"},
                )

        event_id = uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"supplier-receiving-cancel:{context['merchant_id']}:{session_id}",
        ).hex
        await db[RECEIVING_EVENTS].update_one(
            {"id": event_id},
            {
                "$setOnInsert": {
                    "id": event_id,
                    "user_id": context["merchant_id"],
                    "session_id": session_id,
                    "session_reference": _text(updated.get("reference")),
                    "event_type": "supplier_receiving_session_cancelled",
                    "rolled_back_piece_count": rolled_back_count,
                    "actor_id": context["actor_id"],
                    "actor_name": _actor_name(user),
                    "note": _text(payload.note) or None,
                    "occurred_at": cancelled_at,
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
            "cancelled": True,
            "rolled_back_piece_count": rolled_back_count,
            "operational_invoice_created": False,
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
        scans = await _recent_session_events(
            db,
            user_id=context["merchant_id"],
            session_id=session_id,
            limit=MAX_SESSION_SCANS,
        )
        actual_count = len(scans)
        now = _now()
        operational_invoice = build_supplier_receiving_invoice(
            session=session,
            scans=scans,
            requested_lines=payload.invoice_lines,
            saved_at=now,
        )
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
                    "supplier_service_link_status": "catalog_linked",
                    "operational_invoice": operational_invoice,
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
                    "supplier_service_link_status": "catalog_linked",
                    "operational_invoice": operational_invoice,
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
            "operational_invoice": operational_invoice,
            "next_step": "supplier_service_invoice_saved",
            "supplier_service_link_applied": True,
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
    "SupplierReceivingInvoiceLineRequest",
    "SupplierReceivingSessionCancelRequest",
    "SupplierReceivingSessionCloseRequest",
    "SupplierReceivingSessionCreateRequest",
    "ensure_supplier_receiving_indexes",
    "make_supplier_receiving_router",
    "piece_scan_blocker",
    "build_supplier_receiving_invoice",
    "resolve_scanned_piece",
    "supplier_receipt_piece_patch",
    "supplier_receipt_piece_rollback_update",
    "supplier_receipt_previous_piece_state",
    "supplier_piece_reference_price",
]
