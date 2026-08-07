"""Employee-to-supplier handoff for preparation pieces.

The reviewed PDF remains an immutable planning snapshot.  Employees work on
the materialised physical pieces: they can send selected product quantities to
one Mezan 2 supplier, reject unsent pieces outside their responsibility, and a
manager can reassign rejected pieces without deleting the original file.

This module is Mezan-only.  It performs no Salla, Qoyod, WhatsApp, email, or
external supplier-account writes.
"""
from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator
from pymongo import ASCENDING, DESCENDING
from pymongo.errors import DuplicateKeyError

from mezan_supplier_management_routes import MEZAN_SUPPLIERS_V2
from order_review_export_controls import user_can_manage_preparation
from order_review_routes import (
    WORKFLOWS,
    _merchant_user_id,
    _normalized,
    _require_reviewer,
    _text,
)
from preparation_file_registry import REGISTRY, _assignable_employees
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
from reviewed_preparation_batches import BATCHES


DISPATCHES = "mezan_supplier_dispatches_v1"
DISPATCH_EVENTS = "mezan_supplier_dispatch_events_v1"
DISPATCH_STATUS_SENT = "sent"
DISPATCH_STATUS_READY = "ready"
DISPATCH_STATUS_PARTIAL = "partial_received"
DISPATCH_STATUS_RECEIVED = "received"
ASSIGNMENT_STATUS_UNASSIGNED = "unassigned_after_rejection"
MAX_SELECTIONS = 200
MAX_SELECTED_PIECES = 1500


class PreparationPieceSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    group_key: str = Field(min_length=1, max_length=500)
    quantity: int = Field(ge=1, le=MAX_SELECTED_PIECES)


class _SelectionsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_request_id: str = Field(min_length=8, max_length=160)
    file_number: str = Field(min_length=1, max_length=120)
    selections: list[PreparationPieceSelection] = Field(
        min_length=1,
        max_length=MAX_SELECTIONS,
    )

    @field_validator("selections")
    @classmethod
    def validate_selections(
        cls,
        values: list[PreparationPieceSelection],
    ) -> list[PreparationPieceSelection]:
        keys = [_text(row.group_key) for row in values]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate_piece_group")
        if sum(row.quantity for row in values) > MAX_SELECTED_PIECES:
            raise ValueError("piece_selection_limit_exceeded")
        return values


class CreateSupplierDispatchRequest(_SelectionsRequest):
    supplier_id: str = Field(min_length=1, max_length=160)
    note: str | None = Field(default=None, max_length=1000)


class RejectPreparationPiecesRequest(_SelectionsRequest):
    reason: str = Field(min_length=3, max_length=1000)

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value: str) -> str:
        reason = _text(value)
        if len(reason) < 3:
            raise ValueError("preparation_rejection_reason_required")
        return reason


class ReassignPreparationPiecesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_request_id: str = Field(min_length=8, max_length=160)
    piece_ids: list[str] = Field(min_length=1, max_length=MAX_SELECTED_PIECES)
    responsible_employee_id: str = Field(min_length=1, max_length=120)
    note: str | None = Field(default=None, max_length=1000)

    @field_validator("piece_ids")
    @classmethod
    def unique_piece_ids(cls, values: list[str]) -> list[str]:
        result: list[str] = []
        seen: set[str] = set()
        for value in values:
            piece_id = _text(value)
            if not piece_id or piece_id in seen:
                raise ValueError("invalid_or_duplicate_piece_id")
            seen.add(piece_id)
            result.append(piece_id)
        return result


class MarkSupplierDispatchReadyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: str | None = Field(default=None, max_length=1000)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _actor_name(user: dict[str, Any]) -> str:
    return _text(user.get("name") or user.get("email")) or "موظف"


def _is_manager(user: dict[str, Any]) -> bool:
    role = _normalized(user.get("role"))
    return bool(
        role in {"owner", "admin", "operations"}
        or user.get("is_owner") is True
        or "orders.manage" in set(user.get("extra_permissions") or [])
    )


def _require_preparation_worker(user: Any) -> dict[str, Any]:
    if not isinstance(user, dict) or not user_can_manage_preparation(user):
        raise HTTPException(
            status_code=403,
            detail={"code": "preparation_manage_permission_required"},
        )
    return user


def _piece_sort_key(row: dict[str, Any]) -> tuple[Any, ...]:
    try:
        unit_index = int(row.get("unit_index") or 0)
    except (TypeError, ValueError, OverflowError):
        unit_index = 0
    return (
        _text(row.get("order_number")),
        _text(row.get("order_item_id")),
        unit_index,
        _text(row.get("piece_id")),
    )


def plan_piece_selections(
    pieces: list[dict[str, Any]],
    selections: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Choose deterministic physical pieces for product/quantity selections."""
    by_group: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for piece in pieces:
        group_key = _text(piece.get("group_key"))
        if group_key:
            by_group[group_key].append(piece)
    for rows in by_group.values():
        rows.sort(key=_piece_sort_key)

    planned: list[dict[str, Any]] = []
    seen: set[str] = set()
    for selection in selections:
        group_key = _text(selection.get("group_key"))
        try:
            quantity = int(selection.get("quantity") or 0)
        except (TypeError, ValueError, OverflowError):
            quantity = 0
        if not group_key or quantity <= 0:
            raise ValueError("invalid_piece_selection")
        if group_key in seen:
            raise ValueError("duplicate_piece_group")
        seen.add(group_key)
        available = by_group.get(group_key) or []
        if len(available) < quantity:
            raise ValueError("piece_quantity_exceeds_available")
        planned.extend(available[:quantity])
    return planned


def piece_is_available_for_supplier_dispatch(piece: dict[str, Any]) -> bool:
    if _text(piece.get("assignment_status")) == ASSIGNMENT_STATUS_UNASSIGNED:
        return False
    if _text(piece.get("status")) in {
        PIECE_STATUS_BLOCKED,
        PIECE_STATUS_CANCELLED,
        PIECE_STATUS_RECEIVED,
    }:
        return False
    if _text(piece.get("supplier_receiving_session_id")):
        return False
    return _text(piece.get("supplier_dispatch_status")) in {
        "",
        DISPATCH_STATUS_PARTIAL,
    }


def supplier_dispatch_blocker(
    piece: dict[str, Any],
    supplier: dict[str, Any],
) -> dict[str, Any] | None:
    """Require at least one unfinished service offered by the supplier."""
    supplier_service_ids = {
        _text(row.get("service_id"))
        for row in supplier.get("service_links") or []
        if _text(row.get("service_id"))
    }
    pending = [
        row
        for row in piece.get("services") or []
        if _text(row.get("service_id"))
        and _text(row.get("status")) != "completed"
    ]
    matching = [
        row for row in pending
        if _text(row.get("service_id")) in supplier_service_ids
    ]
    if matching:
        return None
    return {
        "code": "supplier_dispatch_service_mismatch",
        "message": "المورد المحدد لا يقدم خدمة متبقية مرتبطة بهذا المنتج.",
        "product_name": _text(piece.get("product_name")) or "منتج",
        "required_services": [
            {
                "service_id": _text(row.get("service_id")),
                "service_name": _text(row.get("service_name"))
                or _text(row.get("service_code"))
                or _text(row.get("service_id")),
            }
            for row in pending
        ],
    }


def supplier_dispatch_lines(
    pieces: list[dict[str, Any]],
    supplier: dict[str, Any],
) -> list[dict[str, Any]]:
    """Print only the unfinished services offered by this supplier."""
    supplier_service_ids = {
        _text(row.get("service_id"))
        for row in supplier.get("service_links") or []
        if _text(row.get("service_id"))
    }
    lines = _group_piece_products(pieces)
    for line in lines:
        line["services"] = [
            service
            for service in line.get("services") or []
            if (
                _text(service.get("service_id")) in supplier_service_ids
                and _text(service.get("status")) != "completed"
            )
        ]
    return lines


def supplier_receiving_dispatch_blocker(
    piece: dict[str, Any],
    supplier_id: Any,
) -> dict[str, Any] | None:
    """Fail closed only for pieces governed by the new dispatch workflow."""
    dispatch_status = _text(piece.get("supplier_dispatch_status"))
    if not dispatch_status:
        return None  # Backward compatibility for historical files.
    if dispatch_status == DISPATCH_STATUS_PARTIAL:
        return {
            "code": "supplier_piece_not_dispatched",
            "message": "أرسل القطعة إلى المورد المطلوب قبل استلامها.",
        }
    expected_supplier_id = _text(piece.get("supplier_id"))
    actual_supplier_id = _text(supplier_id)
    if expected_supplier_id and expected_supplier_id != actual_supplier_id:
        return {
            "code": "supplier_piece_dispatched_to_different_supplier",
            "message": "هذه القطعة مرسلة إلى مورد مختلف عن جلسة الاستلام الحالية.",
            "expected_supplier_id": expected_supplier_id,
            "expected_supplier_name": _text(piece.get("supplier_name")) or None,
        }
    if dispatch_status not in {DISPATCH_STATUS_SENT, DISPATCH_STATUS_READY}:
        return {
            "code": "supplier_piece_not_dispatched",
            "message": "حالة إرسال القطعة لا تسمح باستلامها من المورد.",
            "dispatch_status": dispatch_status,
        }
    return None


def _group_piece_products(pieces: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for piece in pieces:
        group_key = _text(piece.get("group_key")) or _text(piece.get("piece_id"))
        row = grouped.setdefault(group_key, {
            "group_key": group_key,
            "product_id": _text(piece.get("product_id")) or None,
            "product_name": _text(piece.get("product_name")) or "منتج",
            "sku": _text(piece.get("sku")) or None,
            "selected_image_url": _text(piece.get("selected_image_url")) or None,
            "services": [
                {
                    "service_id": _text(service.get("service_id")),
                    "service_name": _text(service.get("service_name"))
                    or _text(service.get("service_code")),
                    "status": _text(service.get("status")) or "pending",
                }
                for service in piece.get("services") or []
                if _text(service.get("service_id"))
            ],
            "quantity": 0,
            "available_quantity": 0,
            "sent_quantity": 0,
            "ready_quantity": 0,
            "received_quantity": 0,
            "order_numbers": [],
        })
        order_number = _text(piece.get("order_number"))
        if order_number and order_number not in row["order_numbers"]:
            row["order_numbers"].append(order_number)
        row["quantity"] += 1
        dispatch_status = _text(piece.get("supplier_dispatch_status"))
        status = _text(piece.get("status"))
        if piece_is_available_for_supplier_dispatch(piece):
            row["available_quantity"] += 1
        if dispatch_status == DISPATCH_STATUS_SENT:
            row["sent_quantity"] += 1
        if dispatch_status == DISPATCH_STATUS_READY or status == PIECE_STATUS_READY_FOR_RECEIPT:
            row["ready_quantity"] += 1
        if dispatch_status == DISPATCH_STATUS_RECEIVED or status == PIECE_STATUS_RECEIVED:
            row["received_quantity"] += 1
    for row in grouped.values():
        row["order_numbers"].sort()
    return sorted(
        grouped.values(),
        key=lambda row: (_normalized(row.get("product_name")), row["group_key"]),
    )


def _file_view(
    registry: dict[str, Any],
    pieces: list[dict[str, Any]],
) -> dict[str, Any]:
    products = _group_piece_products(pieces)
    return {
        "file_number": _text(registry.get("file_number"))
        or _text((pieces[0] if pieces else {}).get("file_number")),
        "batch_id": _text(registry.get("batch_id"))
        or _text((pieces[0] if pieces else {}).get("batch_id")),
        "file_title": _text(registry.get("file_title"))
        or _text((pieces[0] if pieces else {}).get("file_title")),
        "execution_status": _text(registry.get("execution_status")) or "assigned",
        "registered_at": registry.get("registered_at"),
        "piece_count": len(pieces),
        "available_quantity": sum(row["available_quantity"] for row in products),
        "sent_quantity": sum(row["sent_quantity"] for row in products),
        "ready_quantity": sum(row["ready_quantity"] for row in products),
        "received_quantity": sum(row["received_quantity"] for row in products),
        "is_new": any(row["available_quantity"] > 0 for row in products),
        "products": products,
    }


def employee_workspace_summary(
    files: list[dict[str, Any]],
    pieces: list[dict[str, Any]],
) -> dict[str, int]:
    waiting_review_products = sum(
        1
        for file_row in files
        for product in file_row.get("products") or []
        if int(product.get("available_quantity") or 0) > 0
    )
    in_progress_products = sum(
        1
        for file_row in files
        for product in file_row.get("products") or []
        if (
            int(product.get("sent_quantity") or 0)
            + int(product.get("ready_quantity") or 0)
        ) > 0
    )
    received_awaiting_handoff = [
        row
        for row in pieces
        if (
            (
                _text(row.get("supplier_dispatch_status")) == DISPATCH_STATUS_RECEIVED
                or _text(row.get("status")) == PIECE_STATUS_RECEIVED
            )
            and not _text(row.get("branch_handoff_at"))
        )
    ]
    received_order_numbers = {
        _text(row.get("order_number"))
        for row in received_awaiting_handoff
        if _text(row.get("order_number"))
    }
    return {
        "new_files": sum(1 for row in files if row["is_new"]),
        "available_to_send": sum(row["available_quantity"] for row in files),
        "sent": sum(row["sent_quantity"] for row in files),
        "ready": sum(row["ready_quantity"] for row in files),
        "received": sum(row["received_quantity"] for row in files),
        "waiting_review_products": waiting_review_products,
        "in_progress_products": in_progress_products,
        "received_orders_awaiting_branch_handoff": len(received_order_numbers),
        "received_pieces_awaiting_branch_handoff": len(received_awaiting_handoff),
        "total_assigned_pieces": sum(
            1 for row in pieces if not _text(row.get("branch_handoff_at"))
        ),
    }


async def ensure_supplier_dispatch_indexes(db: Any) -> None:
    await db[DISPATCHES].create_index(
        [("user_id", ASCENDING), ("client_request_id", ASCENDING)],
        unique=True,
        name="uq_supplier_dispatch_request_v1",
    )
    await db[DISPATCHES].create_index(
        [("user_id", ASCENDING), ("sent_by_id", ASCENDING), ("sent_at", DESCENDING)],
        name="ix_supplier_dispatch_employee_v1",
    )
    await db[DISPATCHES].create_index(
        [("user_id", ASCENDING), ("supplier_id", ASCENDING), ("sent_at", DESCENDING)],
        name="ix_supplier_dispatch_supplier_v1",
    )
    await db[DISPATCH_EVENTS].create_index(
        [("user_id", ASCENDING), ("client_request_id", ASCENDING)],
        unique=True,
        sparse=True,
        name="uq_supplier_dispatch_event_request_v1",
    )
    await db[PIECES].create_index(
        [("user_id", ASCENDING), ("supplier_id", ASCENDING), ("supplier_dispatch_status", ASCENDING)],
        name="ix_preparation_piece_supplier_dispatch_v1",
    )
    await db[PIECES].create_index(
        [("user_id", ASCENDING), ("assignment_status", ASCENDING), ("updated_at", DESCENDING)],
        name="ix_preparation_piece_unassigned_v1",
    )


async def _active_suppliers(db: Any, *, user_id: str) -> list[dict[str, Any]]:
    return await db[MEZAN_SUPPLIERS_V2].find(
        {
            "user_id": user_id,
            "status": {"$ne": "inactive"},
            "service_ids.0": {"$exists": True},
        },
        {
            "_id": 0,
            "id": 1,
            "company_name": 1,
            "contact_person": 1,
            "service_ids": 1,
            "service_links": 1,
        },
    ).sort("company_name", 1).to_list(2000)


async def _employee_workspace(
    db: Any,
    *,
    user_id: str,
    employee_id: str,
    limit: int,
) -> dict[str, Any]:
    pieces = await db[PIECES].find(
        {
            "user_id": user_id,
            "responsible_employee_id": employee_id,
            "status": {"$ne": PIECE_STATUS_CANCELLED},
        },
        {"_id": 0, "user_id": 0, "image_b64": 0},
    ).sort("updated_at", -1).limit(50000).to_list(50000)
    batch_ids = sorted({
        _text(row.get("batch_id")) for row in pieces if _text(row.get("batch_id"))
    })
    registries = (
        await db[REGISTRY].find(
            {"user_id": user_id, "batch_id": {"$in": batch_ids}},
            {"_id": 0},
        ).to_list(max(1, len(batch_ids)))
        if batch_ids
        else []
    )
    registry_by_batch = {
        _text(row.get("batch_id")): row for row in registries
        if _text(row.get("batch_id"))
    }
    pieces_by_batch: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for piece in pieces:
        pieces_by_batch[_text(piece.get("batch_id"))].append(piece)
    files = [
        _file_view(registry_by_batch.get(batch_id, {}), rows)
        for batch_id, rows in pieces_by_batch.items()
    ]
    files.sort(
        key=lambda row: (
            bool(row["is_new"]),
            str(row.get("registered_at") or ""),
        ),
        reverse=True,
    )
    summary = employee_workspace_summary(files, pieces)
    files = files[:limit]

    supplier_rows: dict[str, dict[str, Any]] = {}
    for piece in pieces:
        supplier_id = _text(piece.get("supplier_id"))
        if not supplier_id or _text(piece.get("sent_to_supplier_by_id")) != employee_id:
            continue
        account = supplier_rows.setdefault(supplier_id, {
            "supplier_id": supplier_id,
            "supplier_name": _text(piece.get("supplier_name")) or "مورد",
            "sent_quantity": 0,
            "ready_quantity": 0,
            "received_quantity": 0,
            "products": [],
        })
        dispatch_status = _text(piece.get("supplier_dispatch_status"))
        status = _text(piece.get("status"))
        if dispatch_status == DISPATCH_STATUS_SENT:
            account["sent_quantity"] += 1
        if dispatch_status == DISPATCH_STATUS_READY or status == PIECE_STATUS_READY_FOR_RECEIPT:
            account["ready_quantity"] += 1
        if dispatch_status == DISPATCH_STATUS_RECEIVED or status == PIECE_STATUS_RECEIVED:
            account["received_quantity"] += 1
    for supplier_id, account in supplier_rows.items():
        account_pieces = [
            row for row in pieces
            if _text(row.get("supplier_id")) == supplier_id
            and _text(row.get("sent_to_supplier_by_id")) == employee_id
        ]
        account["products"] = _group_piece_products(account_pieces)

    dispatches = await db[DISPATCHES].find(
        {"user_id": user_id, "sent_by_id": employee_id, "status": {"$ne": "building"}},
        {"_id": 0, "user_id": 0},
    ).sort("sent_at", -1).limit(500).to_list(500)
    pieces_by_dispatch: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for piece in pieces:
        dispatch_id = _text(piece.get("supplier_dispatch_id"))
        if dispatch_id:
            pieces_by_dispatch[dispatch_id].append(piece)
    for dispatch in dispatches:
        current = pieces_by_dispatch.get(_text(dispatch.get("id"))) or []
        statuses = {
            _text(piece.get("supplier_dispatch_status")) for piece in current
        }
        if current and statuses == {DISPATCH_STATUS_RECEIVED}:
            dispatch["status"] = DISPATCH_STATUS_RECEIVED
        elif DISPATCH_STATUS_READY in statuses:
            dispatch["status"] = DISPATCH_STATUS_READY
        elif DISPATCH_STATUS_SENT in statuses:
            dispatch["status"] = DISPATCH_STATUS_SENT
        elif DISPATCH_STATUS_PARTIAL in statuses:
            dispatch["status"] = DISPATCH_STATUS_PARTIAL
    dispatch_by_id = {_text(row.get("id")): row for row in dispatches}
    for account in supplier_rows.values():
        supplier_id = account["supplier_id"]
        account["dispatches"] = [
            row for row in dispatch_by_id.values()
            if _text(row.get("supplier_id")) == supplier_id
        ]

    return {
        "employee_id": employee_id,
        "summary": summary,
        "files": files,
        "supplier_accounts": sorted(
            supplier_rows.values(),
            key=lambda row: (_normalized(row.get("supplier_name")), row["supplier_id"]),
        ),
        "suppliers": await _active_suppliers(db, user_id=user_id),
    }


async def _unassigned_workspace(
    db: Any,
    *,
    user_id: str,
    reviewer: dict[str, Any],
) -> dict[str, Any]:
    pieces = await db[PIECES].find(
        {"user_id": user_id, "assignment_status": ASSIGNMENT_STATUS_UNASSIGNED},
        {"_id": 0, "user_id": 0, "image_b64": 0},
    ).sort("rejected_at", 1).limit(5000).to_list(5000)
    grouped: dict[tuple[str, str], dict[str, Any]] = {}
    for piece in pieces:
        key = (_text(piece.get("file_number")), _text(piece.get("group_key")))
        row = grouped.setdefault(key, {
            "file_number": key[0],
            "batch_id": _text(piece.get("batch_id")),
            "file_title": _text(piece.get("file_title")),
            "group_key": key[1],
            "product_name": _text(piece.get("product_name")) or "منتج",
            "sku": _text(piece.get("sku")) or None,
            "selected_image_url": _text(piece.get("selected_image_url")) or None,
            "quantity": 0,
            "piece_ids": [],
            "rejected_by_employee_id": _text(piece.get("rejected_by_employee_id")),
            "rejected_by_employee_name": _text(piece.get("rejected_by_employee_name")),
            "rejection_reason": _text(piece.get("rejection_reason")) or "ليس من اختصاص الموظف",
            "rejected_at": piece.get("rejected_at"),
        })
        row["quantity"] += 1
        row["piece_ids"].append(_text(piece.get("piece_id")))
    employees = await _assignable_employees(
        db,
        user_id=user_id,
        reviewer=reviewer,
    )
    return {
        "summary": {
            "unassigned_products": len(grouped),
            "unassigned_pieces": len(pieces),
        },
        "items": list(grouped.values()),
        "employees": employees,
    }


async def _mark_orders_started(
    db: Any,
    *,
    user_id: str,
    registry: dict[str, Any],
    pieces: list[dict[str, Any]],
    actor: dict[str, Any],
) -> None:
    now = _now()
    actor_id = _text(actor.get("id"))
    actor_name = _actor_name(actor)
    batch_id = _text(registry.get("batch_id"))
    file_number = _text(registry.get("file_number"))
    order_numbers = sorted({
        _text(row.get("order_number")) for row in pieces
        if _text(row.get("order_number"))
    })
    await db[REGISTRY].update_one(
        {"user_id": user_id, "file_number": file_number, "status": "ready"},
        {"$set": {
            "execution_status": "in_progress",
            "started_at": registry.get("started_at") or now,
            "started_by": registry.get("started_by") or actor_id,
            "started_by_name": registry.get("started_by_name") or actor_name,
            "updated_at": now,
        }},
    )
    if batch_id:
        await db[BATCHES].update_one(
            {"user_id": user_id, "id": batch_id},
            {"$set": {
                "execution_status": "in_progress",
                "started_at": registry.get("started_at") or now,
                "started_by": registry.get("started_by") or actor_id,
                "updated_at": now,
            }},
        )
    if order_numbers:
        await db[WORKFLOWS].update_many(
            {
                "user_id": user_id,
                "order_number": {"$in": order_numbers},
                "stage": "reviewed",
            },
            {"$set": {
                "stage": "in_progress",
                "in_progress_at": now,
                "in_progress_by": actor_id,
                "in_progress_by_name": actor_name,
                "preparation_assignment_status": "supplier_dispatched",
                "updated_at": now,
            }, "$inc": {"revision": 1}},
        )


def make_preparation_supplier_dispatch_router(
    db: Any,
    current_user: Callable,
) -> APIRouter:
    router = APIRouter(
        prefix="/supplier-dispatch-v1",
        tags=["Preparation Supplier Dispatch"],
    )

    @router.get("/workspace")
    async def workspace(
        limit: int = Query(100, ge=1, le=200),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        worker = _require_preparation_worker(user)
        await ensure_supplier_dispatch_indexes(db)
        result = await _employee_workspace(
            db,
            user_id=_merchant_user_id(worker),
            employee_id=_text(worker.get("id")),
            limit=limit,
        )
        return {
            "ok": True,
            **result,
            "mezan_only": True,
            "external_supplier_login_enabled": False,
            "salla_updated": False,
            "qoyod_updated": False,
        }

    @router.get("/manager/unassigned")
    async def manager_unassigned(
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        manager = _require_reviewer(user)
        await ensure_supplier_dispatch_indexes(db)
        return {
            "ok": True,
            **await _unassigned_workspace(
                db,
                user_id=_merchant_user_id(manager),
                reviewer=manager,
            ),
            "mezan_only": True,
        }

    @router.post("/dispatches", status_code=201)
    async def create_dispatch(
        payload: CreateSupplierDispatchRequest,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        worker = _require_preparation_worker(user)
        user_id = _merchant_user_id(worker)
        employee_id = _text(worker.get("id"))
        await ensure_supplier_dispatch_indexes(db)
        existing = await db[DISPATCHES].find_one(
            {"user_id": user_id, "client_request_id": payload.client_request_id},
            {"_id": 0, "user_id": 0},
        )
        if existing:
            return {"ok": _text(existing.get("status")) != "building", "dispatch": existing}

        registry = await db[REGISTRY].find_one(
            {"user_id": user_id, "file_number": _text(payload.file_number), "status": "ready"},
            {"_id": 0},
        )
        if not registry:
            raise HTTPException(status_code=404, detail={"code": "preparation_file_not_found"})
        candidates = await db[PIECES].find(
            {
                "user_id": user_id,
                "file_number": _text(payload.file_number),
                "responsible_employee_id": employee_id,
                "status": {"$in": [PIECE_STATUS_ASSIGNED, PIECE_STATUS_IN_PROGRESS]},
                "$or": [
                    {"supplier_dispatch_status": {"$exists": False}},
                    {"supplier_dispatch_status": None},
                    {"supplier_dispatch_status": ""},
                    {"supplier_dispatch_status": DISPATCH_STATUS_PARTIAL},
                ],
            },
            {"_id": 0},
        ).to_list(50000)
        candidates = [row for row in candidates if piece_is_available_for_supplier_dispatch(row)]
        try:
            selected = plan_piece_selections(
                candidates,
                [row.model_dump() for row in payload.selections],
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": str(exc),
                    "message": "الكمية المختارة لم تعد متاحة؛ حدّث الملف وأعد المحاولة.",
                },
            ) from exc
        supplier = await db[MEZAN_SUPPLIERS_V2].find_one(
            {
                "user_id": user_id,
                "id": _text(payload.supplier_id),
                "status": {"$ne": "inactive"},
                "service_ids.0": {"$exists": True},
            },
            {"_id": 0},
        )
        if not supplier:
            raise HTTPException(
                status_code=404,
                detail={"code": "supplier_dispatch_supplier_not_found"},
            )
        for piece in selected:
            blocker = supplier_dispatch_blocker(piece, supplier)
            if blocker:
                raise HTTPException(status_code=409, detail=blocker)

        dispatch_id = f"sdv1_{uuid.uuid4().hex}"
        now = _now()
        piece_ids = [_text(row.get("piece_id")) for row in selected]
        lines = supplier_dispatch_lines(selected, supplier)
        shell = {
            "id": dispatch_id,
            "user_id": user_id,
            "client_request_id": payload.client_request_id,
            "status": "building",
            "file_number": _text(payload.file_number),
            "batch_id": _text(registry.get("batch_id")),
            "supplier_id": _text(supplier.get("id")),
            "supplier_name": _text(supplier.get("company_name")),
            "sent_by_id": employee_id,
            "sent_by_name": _actor_name(worker),
            "piece_ids": piece_ids,
            "piece_count": len(piece_ids),
            "lines": lines,
            "note": _text(payload.note) or None,
            "created_at": now,
            "mezan_only": True,
            "salla_updated": False,
            "qoyod_updated": False,
        }
        try:
            await db[DISPATCHES].insert_one(dict(shell))
        except DuplicateKeyError:
            duplicate = await db[DISPATCHES].find_one(
                {"user_id": user_id, "client_request_id": payload.client_request_id},
                {"_id": 0, "user_id": 0},
            )
            return {"ok": bool(duplicate), "dispatch": duplicate}

        result = await db[PIECES].update_many(
            {
                "user_id": user_id,
                "piece_id": {"$in": piece_ids},
                "responsible_employee_id": employee_id,
                "status": {"$in": [PIECE_STATUS_ASSIGNED, PIECE_STATUS_IN_PROGRESS]},
                "$or": [
                    {"supplier_dispatch_status": {"$exists": False}},
                    {"supplier_dispatch_status": None},
                    {"supplier_dispatch_status": ""},
                    {"supplier_dispatch_status": DISPATCH_STATUS_PARTIAL},
                ],
            },
            {"$set": {
                "status": PIECE_STATUS_IN_PROGRESS,
                "execution_status": "sent_to_supplier",
                "supplier_dispatch_id": dispatch_id,
                "supplier_dispatch_status": DISPATCH_STATUS_SENT,
                "supplier_id": _text(supplier.get("id")),
                "supplier_name": _text(supplier.get("company_name")),
                "sent_to_supplier_at": now,
                "sent_to_supplier_by_id": employee_id,
                "sent_to_supplier_by_name": _actor_name(worker),
                "updated_at": now,
                "mezan_only": True,
                "salla_updated": False,
                "qoyod_updated": False,
            }},
        )
        if int(result.modified_count or 0) != len(piece_ids):
            await db[PIECES].update_many(
                {"user_id": user_id, "supplier_dispatch_id": dispatch_id},
                {"$set": {
                    "status": PIECE_STATUS_ASSIGNED,
                    "execution_status": "not_started",
                    "updated_at": _now(),
                }, "$unset": {
                    "supplier_dispatch_id": "",
                    "supplier_dispatch_status": "",
                    "supplier_id": "",
                    "supplier_name": "",
                    "sent_to_supplier_at": "",
                    "sent_to_supplier_by_id": "",
                    "sent_to_supplier_by_name": "",
                }},
            )
            await db[DISPATCHES].update_one(
                {"user_id": user_id, "id": dispatch_id, "status": "building"},
                {"$set": {
                    "status": "failed_piece_conflict",
                    "failed_at": _now(),
                    "updated_at": _now(),
                }},
            )
            raise HTTPException(status_code=409, detail={"code": "supplier_dispatch_piece_conflict"})

        await _mark_orders_started(
            db,
            user_id=user_id,
            registry=registry,
            pieces=selected,
            actor=worker,
        )
        ready_patch = {"status": DISPATCH_STATUS_SENT, "sent_at": now, "updated_at": now}
        await db[DISPATCHES].update_one(
            {"user_id": user_id, "id": dispatch_id, "status": "building"},
            {"$set": ready_patch},
        )
        shell.update(ready_patch)
        event = {
            "id": uuid.uuid4().hex,
            "user_id": user_id,
            "client_request_id": payload.client_request_id,
            "event_type": "preparation_pieces_sent_to_supplier",
            "dispatch_id": dispatch_id,
            "file_number": _text(payload.file_number),
            "supplier_id": _text(supplier.get("id")),
            "supplier_name": _text(supplier.get("company_name")),
            "piece_ids": piece_ids,
            "piece_count": len(piece_ids),
            "actor_id": employee_id,
            "actor_name": _actor_name(worker),
            "occurred_at": now,
            "mezan_only": True,
            "salla_updated": False,
            "qoyod_updated": False,
        }
        await db[DISPATCH_EVENTS].insert_one(dict(event))
        await db[PIECE_EVENTS].insert_one(dict(event))
        return {
            "ok": True,
            "dispatch": {key: value for key, value in shell.items() if key != "user_id"},
            "moved_to_supplier_account": True,
            "mezan_only": True,
            "salla_updated": False,
            "qoyod_updated": False,
        }

    @router.post("/rejections", status_code=201)
    async def reject_pieces(
        payload: RejectPreparationPiecesRequest,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        worker = _require_preparation_worker(user)
        user_id = _merchant_user_id(worker)
        employee_id = _text(worker.get("id"))
        await ensure_supplier_dispatch_indexes(db)
        previous = await db[DISPATCH_EVENTS].find_one(
            {"user_id": user_id, "client_request_id": payload.client_request_id},
            {"_id": 0, "user_id": 0},
        )
        if previous:
            return {"ok": True, "rejection": previous}
        candidates = await db[PIECES].find(
            {
                "user_id": user_id,
                "file_number": _text(payload.file_number),
                "responsible_employee_id": employee_id,
                "status": {"$in": [PIECE_STATUS_ASSIGNED, PIECE_STATUS_IN_PROGRESS]},
                "$or": [
                    {"supplier_dispatch_status": {"$exists": False}},
                    {"supplier_dispatch_status": None},
                    {"supplier_dispatch_status": ""},
                    {"supplier_dispatch_status": DISPATCH_STATUS_PARTIAL},
                ],
            },
            {"_id": 0},
        ).to_list(50000)
        candidates = [row for row in candidates if piece_is_available_for_supplier_dispatch(row)]
        try:
            selected = plan_piece_selections(
                candidates,
                [row.model_dump() for row in payload.selections],
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": str(exc), "message": "الكمية المختارة لم تعد متاحة للرفض."},
            ) from exc
        piece_ids = [_text(row.get("piece_id")) for row in selected]
        now = _now()
        rejection_id = f"reject_{uuid.uuid4().hex}"
        reason = _text(payload.reason)
        result = await db[PIECES].update_many(
            {
                "user_id": user_id,
                "piece_id": {"$in": piece_ids},
                "responsible_employee_id": employee_id,
                "$or": [
                    {"supplier_dispatch_status": {"$exists": False}},
                    {"supplier_dispatch_status": None},
                    {"supplier_dispatch_status": ""},
                    {"supplier_dispatch_status": DISPATCH_STATUS_PARTIAL},
                ],
            },
            {"$set": {
                "status": PIECE_STATUS_ASSIGNED,
                "execution_status": ASSIGNMENT_STATUS_UNASSIGNED,
                "assignment_status": ASSIGNMENT_STATUS_UNASSIGNED,
                "previous_responsible_employee_id": employee_id,
                "previous_responsible_employee_name": _actor_name(worker),
                "rejection_id": rejection_id,
                "rejection_reason": reason,
                "rejected_at": now,
                "rejected_by_employee_id": employee_id,
                "rejected_by_employee_name": _actor_name(worker),
                "updated_at": now,
                "mezan_only": True,
                "salla_updated": False,
                "qoyod_updated": False,
            }, "$unset": {
                "responsible_employee_id": "",
                "responsible_employee_name": "",
                "supplier_dispatch_id": "",
                "supplier_dispatch_status": "",
                "supplier_id": "",
                "supplier_name": "",
            }},
        )
        if int(result.modified_count or 0) != len(piece_ids):
            await db[PIECES].update_many(
                {"user_id": user_id, "rejection_id": rejection_id},
                {"$set": {
                    "responsible_employee_id": employee_id,
                    "responsible_employee_name": _actor_name(worker),
                    "assignment_status": "assigned",
                    "execution_status": "not_started",
                    "updated_at": _now(),
                }, "$unset": {
                    "rejection_id": "",
                    "rejection_reason": "",
                    "rejected_at": "",
                    "rejected_by_employee_id": "",
                    "rejected_by_employee_name": "",
                }},
            )
            raise HTTPException(status_code=409, detail={"code": "preparation_rejection_piece_conflict"})
        event = {
            "id": rejection_id,
            "user_id": user_id,
            "client_request_id": payload.client_request_id,
            "event_type": "preparation_pieces_rejected_unassigned",
            "file_number": _text(payload.file_number),
            "piece_ids": piece_ids,
            "piece_count": len(piece_ids),
            "reason": reason,
            "actor_id": employee_id,
            "actor_name": _actor_name(worker),
            "occurred_at": now,
            "mezan_only": True,
            "salla_updated": False,
            "qoyod_updated": False,
        }
        await db[DISPATCH_EVENTS].insert_one(dict(event))
        await db[PIECE_EVENTS].insert_one(dict(event))
        return {
            "ok": True,
            "rejection": {key: value for key, value in event.items() if key != "user_id"},
            "moved_to_unassigned_queue": True,
        }

    @router.post("/manager/reassign")
    async def reassign_pieces(
        payload: ReassignPreparationPiecesRequest,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        manager = _require_reviewer(user)
        user_id = _merchant_user_id(manager)
        await ensure_supplier_dispatch_indexes(db)
        previous = await db[DISPATCH_EVENTS].find_one(
            {"user_id": user_id, "client_request_id": payload.client_request_id},
            {"_id": 0, "user_id": 0},
        )
        if previous:
            return {"ok": True, "assignment": previous}
        employees = await _assignable_employees(db, user_id=user_id, reviewer=manager)
        employee = next(
            (row for row in employees if row["id"] == payload.responsible_employee_id),
            None,
        )
        if not employee:
            raise HTTPException(
                status_code=422,
                detail={"code": "responsible_employee_unavailable"},
            )
        rows = await db[PIECES].find(
            {
                "user_id": user_id,
                "piece_id": {"$in": payload.piece_ids},
                "assignment_status": ASSIGNMENT_STATUS_UNASSIGNED,
            },
            {"_id": 0},
        ).to_list(len(payload.piece_ids))
        if len(rows) != len(payload.piece_ids):
            raise HTTPException(
                status_code=409,
                detail={"code": "preparation_reassignment_piece_conflict"},
            )
        now = _now()
        assignment_id = f"assign_{uuid.uuid4().hex}"
        history = {
            "assignment_id": assignment_id,
            "responsible_employee_id": employee["id"],
            "responsible_employee_name": employee["name"],
            "assigned_by": _text(manager.get("id")),
            "assigned_by_name": _actor_name(manager),
            "assigned_at": now,
            "note": _text(payload.note) or None,
        }
        result = await db[PIECES].update_many(
            {
                "user_id": user_id,
                "piece_id": {"$in": payload.piece_ids},
                "assignment_status": ASSIGNMENT_STATUS_UNASSIGNED,
            },
            {"$set": {
                "status": PIECE_STATUS_ASSIGNED,
                "execution_status": "not_started",
                "assignment_status": "assigned",
                "responsible_employee_id": employee["id"],
                "responsible_employee_name": employee["name"],
                "reassignment_id": assignment_id,
                "reassigned_at": now,
                "reassigned_by": _text(manager.get("id")),
                "reassigned_by_name": _actor_name(manager),
                "rejection_resolved_at": now,
                "updated_at": now,
                "mezan_only": True,
                "salla_updated": False,
                "qoyod_updated": False,
            }, "$push": {"assignment_history": history}},
        )
        if int(result.modified_count or 0) != len(payload.piece_ids):
            await db[PIECES].update_many(
                {"user_id": user_id, "reassignment_id": assignment_id},
                {"$set": {
                    "status": PIECE_STATUS_ASSIGNED,
                    "execution_status": ASSIGNMENT_STATUS_UNASSIGNED,
                    "assignment_status": ASSIGNMENT_STATUS_UNASSIGNED,
                    "updated_at": _now(),
                }, "$unset": {
                    "responsible_employee_id": "",
                    "responsible_employee_name": "",
                    "reassignment_id": "",
                    "reassigned_at": "",
                    "reassigned_by": "",
                    "reassigned_by_name": "",
                    "rejection_resolved_at": "",
                }, "$pull": {
                    "assignment_history": {"assignment_id": assignment_id},
                }},
            )
            raise HTTPException(
                status_code=409,
                detail={"code": "preparation_reassignment_piece_conflict"},
            )
        event = {
            "id": assignment_id,
            "user_id": user_id,
            "client_request_id": payload.client_request_id,
            "event_type": "preparation_pieces_reassigned",
            "piece_ids": payload.piece_ids,
            "piece_count": len(payload.piece_ids),
            "responsible_employee_id": employee["id"],
            "responsible_employee_name": employee["name"],
            "actor_id": _text(manager.get("id")),
            "actor_name": _actor_name(manager),
            "note": _text(payload.note) or None,
            "occurred_at": now,
            "mezan_only": True,
            "salla_updated": False,
            "qoyod_updated": False,
        }
        await db[DISPATCH_EVENTS].insert_one(dict(event))
        await db[PIECE_EVENTS].insert_one(dict(event))
        return {
            "ok": True,
            "assignment": {key: value for key, value in event.items() if key != "user_id"},
        }

    @router.post("/dispatches/{dispatch_id}/ready")
    async def mark_dispatch_ready(
        dispatch_id: str,
        payload: MarkSupplierDispatchReadyRequest,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        worker = _require_preparation_worker(user)
        user_id = _merchant_user_id(worker)
        dispatch = await db[DISPATCHES].find_one(
            {"user_id": user_id, "id": _text(dispatch_id), "status": DISPATCH_STATUS_SENT},
            {"_id": 0},
        )
        if not dispatch:
            existing = await db[DISPATCHES].find_one(
                {"user_id": user_id, "id": _text(dispatch_id)},
                {"_id": 0, "status": 1},
            )
            if _text((existing or {}).get("status")) == DISPATCH_STATUS_READY:
                return {"ok": True, "dispatch_id": dispatch_id, "status": DISPATCH_STATUS_READY}
            raise HTTPException(status_code=404, detail={"code": "supplier_dispatch_not_found"})
        if (
            _text(dispatch.get("sent_by_id")) != _text(worker.get("id"))
            and not _is_manager(worker)
        ):
            raise HTTPException(status_code=403, detail={"code": "supplier_dispatch_owner_required"})
        now = _now()
        piece_ids = [_text(value) for value in dispatch.get("piece_ids") or [] if _text(value)]
        await db[PIECES].update_many(
            {
                "user_id": user_id,
                "piece_id": {"$in": piece_ids},
                "supplier_dispatch_id": _text(dispatch_id),
                "supplier_dispatch_status": DISPATCH_STATUS_SENT,
            },
            {"$set": {
                "status": PIECE_STATUS_READY_FOR_RECEIPT,
                "execution_status": "supplier_ready_for_receipt",
                "supplier_dispatch_status": DISPATCH_STATUS_READY,
                "supplier_ready_at": now,
                "supplier_ready_confirmed_by": _text(worker.get("id")),
                "supplier_ready_confirmed_by_name": _actor_name(worker),
                "updated_at": now,
            }},
        )
        await db[DISPATCHES].update_one(
            {"user_id": user_id, "id": _text(dispatch_id), "status": DISPATCH_STATUS_SENT},
            {"$set": {
                "status": DISPATCH_STATUS_READY,
                "ready_at": now,
                "ready_confirmed_by": _text(worker.get("id")),
                "ready_confirmed_by_name": _actor_name(worker),
                "ready_note": _text(payload.note) or None,
                "updated_at": now,
            }},
        )
        event = {
            "id": uuid.uuid4().hex,
            "user_id": user_id,
            "event_type": "supplier_dispatch_marked_ready",
            "dispatch_id": _text(dispatch_id),
            "supplier_id": _text(dispatch.get("supplier_id")),
            "piece_ids": piece_ids,
            "piece_count": len(piece_ids),
            "actor_id": _text(worker.get("id")),
            "actor_name": _actor_name(worker),
            "note": _text(payload.note) or None,
            "occurred_at": now,
            "mezan_only": True,
            "salla_updated": False,
            "qoyod_updated": False,
        }
        await db[DISPATCH_EVENTS].insert_one(dict(event))
        await db[PIECE_EVENTS].insert_one(dict(event))
        return {
            "ok": True,
            "dispatch_id": _text(dispatch_id),
            "status": DISPATCH_STATUS_READY,
            "ready_piece_count": len(piece_ids),
        }

    return router


__all__ = [
    "ASSIGNMENT_STATUS_UNASSIGNED",
    "CreateSupplierDispatchRequest",
    "DISPATCHES",
    "DISPATCH_EVENTS",
    "DISPATCH_STATUS_PARTIAL",
    "DISPATCH_STATUS_READY",
    "DISPATCH_STATUS_RECEIVED",
    "DISPATCH_STATUS_SENT",
    "PreparationPieceSelection",
    "RejectPreparationPiecesRequest",
    "ReassignPreparationPiecesRequest",
    "ensure_supplier_dispatch_indexes",
    "employee_workspace_summary",
    "make_preparation_supplier_dispatch_router",
    "piece_is_available_for_supplier_dispatch",
    "plan_piece_selections",
    "supplier_dispatch_blocker",
    "supplier_dispatch_lines",
    "supplier_receiving_dispatch_blocker",
]
