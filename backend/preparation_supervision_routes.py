"""Read-only preparation supervision for AMASI mobile managers.

The supervision view is intentionally derived from the durable physical-piece
SSOT.  It never mutates preparation, supplier, invoice, Salla, or Qoyod data.

Card metrics:
- with_employee: current responsibility excluding pieces already ready for the
  next employee (those are shown separately as ready_not_handed_off).
- waiting_review: active pieces not yet raised to a supplier.
- ready_not_handed_off: pieces received from the supplier but not yet accepted
  by the next preparation/assembly employee; historical, not month-scoped.
- delivered_this_month: pieces accepted by the next employee during the current
  Riyadh calendar month.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException

from fulfillment_v2_routes import _actor_context
from order_review_export_controls import user_can_manage_preparation
from preparation_piece_operations import (
    PIECES,
    PIECE_STATUS_CANCELLED,
    PIECE_STATUS_RECEIVED,
    PIECE_STATUS_READY_FOR_ASSEMBLY,
)
from preparation_supplier_dispatch import piece_is_available_for_supplier_dispatch

RIYADH = ZoneInfo("Asia/Riyadh")
MAX_PIECES = 50000


def _text(value: Any) -> str:
    return str(value or "").strip()


def _as_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def _month_bounds(now: datetime | None = None) -> tuple[datetime, datetime, str]:
    local = (now or datetime.now(timezone.utc)).astimezone(RIYADH)
    start_local = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if start_local.month == 12:
        next_local = start_local.replace(year=start_local.year + 1, month=1)
    else:
        next_local = start_local.replace(month=start_local.month + 1)
    return (
        start_local.astimezone(timezone.utc),
        next_local.astimezone(timezone.utc),
        start_local.strftime("%Y-%m"),
    )


def _is_real_piece(row: dict[str, Any]) -> bool:
    return row.get("experiment_archived_at") in (None, "")


def _is_handed_off(row: dict[str, Any]) -> bool:
    return bool(
        _as_datetime(row.get("preparation_received_at"))
        or _text(row.get("preparation_receipt_status")) == "received"
        or _text(row.get("status")) == PIECE_STATUS_READY_FOR_ASSEMBLY
    )


def _ready_not_handed_off(row: dict[str, Any]) -> bool:
    return bool(
        _is_real_piece(row)
        and _text(row.get("status")) == PIECE_STATUS_RECEIVED
        and not _is_handed_off(row)
    )


def _current_with_employee(row: dict[str, Any]) -> bool:
    if not _is_real_piece(row):
        return False
    status = _text(row.get("status"))
    if status == PIECE_STATUS_CANCELLED or _is_handed_off(row):
        return False
    # Ready pieces are deliberately excluded from "with employee" because the
    # card exposes them in their own ready-not-handed-off metric.
    if _ready_not_handed_off(row):
        return False
    return True


def _waiting_review(row: dict[str, Any]) -> bool:
    return _current_with_employee(row) and piece_is_available_for_supplier_dispatch(row)


def _delivered_in_month(
    row: dict[str, Any],
    *,
    month_start: datetime,
    next_month: datetime,
) -> bool:
    if not _is_real_piece(row):
        return False
    timestamp = _as_datetime(row.get("preparation_received_at"))
    if timestamp is None:
        return False
    timestamp = timestamp.astimezone(timezone.utc)
    return month_start <= timestamp < next_month


def _piece_view(row: dict[str, Any], bucket: str) -> dict[str, Any]:
    return {
        "piece_id": _text(row.get("piece_id") or row.get("id")),
        "file_number": _text(row.get("file_number")) or None,
        "order_number": _text(row.get("order_number")) or None,
        "order_item_id": _text(row.get("order_item_id")) or None,
        "unit_index": int(row.get("unit_index") or 0),
        "product_id": _text(row.get("product_id")) or None,
        "product_name": _text(row.get("product_name")) or "منتج",
        "sku": _text(row.get("sku")) or None,
        "image_url": _text(
            row.get("selected_image_url")
            or row.get("resolved_image_url")
            or row.get("image_url")
        ) or None,
        "status": _text(row.get("status")),
        "execution_status": _text(row.get("execution_status")) or None,
        "supplier_id": _text(row.get("supplier_id")) or None,
        "supplier_name": _text(row.get("supplier_name")) or None,
        "supplier_dispatch_status": _text(row.get("supplier_dispatch_status")) or None,
        "assigned_at": row.get("assigned_at") or row.get("created_at"),
        "started_at": row.get("started_at"),
        "supplier_received_at": row.get("received_at"),
        "handed_off_at": row.get("preparation_received_at"),
        "services": [
            {
                "service_id": _text(service.get("service_id")) or None,
                "service_name": _text(service.get("service_name")) or "خدمة",
                "status": _text(service.get("status")) or "pending",
            }
            for service in (row.get("services") or [])
            if isinstance(service, dict)
        ],
        "bucket": bucket,
        "read_only": True,
    }


def _employee_rows(
    pieces: list[dict[str, Any]],
    *,
    month_start: datetime,
    next_month: datetime,
) -> list[dict[str, Any]]:
    by_employee: dict[str, dict[str, Any]] = {}
    for row in pieces:
        employee_id = _text(row.get("responsible_employee_id"))
        if not employee_id or not _is_real_piece(row):
            continue
        item = by_employee.setdefault(employee_id, {
            "employee_id": employee_id,
            "employee_name": _text(row.get("responsible_employee_name")) or "موظف تجهيز",
            "with_employee": 0,
            "waiting_review": 0,
            "ready_not_handed_off": 0,
            "delivered_this_month": 0,
        })
        # Prefer the latest non-empty stored name after account renames.
        if _text(row.get("responsible_employee_name")):
            item["employee_name"] = _text(row.get("responsible_employee_name"))
        if _current_with_employee(row):
            item["with_employee"] += 1
        if _waiting_review(row):
            item["waiting_review"] += 1
        if _ready_not_handed_off(row):
            item["ready_not_handed_off"] += 1
        if _delivered_in_month(row, month_start=month_start, next_month=next_month):
            item["delivered_this_month"] += 1
    return sorted(
        by_employee.values(),
        key=lambda row: (row["employee_name"].casefold(), row["employee_id"]),
    )


def _detail_for_employee(
    pieces: list[dict[str, Any]],
    *,
    employee_id: str,
    month_start: datetime,
    next_month: datetime,
) -> dict[str, Any] | None:
    rows = [
        row for row in pieces
        if _is_real_piece(row) and _text(row.get("responsible_employee_id")) == employee_id
    ]
    if not rows:
        return None
    employee_name = next(
        (
            _text(row.get("responsible_employee_name"))
            for row in reversed(rows)
            if _text(row.get("responsible_employee_name"))
        ),
        "موظف تجهيز",
    )
    waiting: list[dict[str, Any]] = []
    progress: list[dict[str, Any]] = []
    ready: list[dict[str, Any]] = []
    delivered: list[dict[str, Any]] = []
    for row in rows:
        if _delivered_in_month(row, month_start=month_start, next_month=next_month):
            delivered.append(_piece_view(row, "delivered_this_month"))
        if _ready_not_handed_off(row):
            ready.append(_piece_view(row, "ready_not_handed_off"))
        elif _waiting_review(row):
            waiting.append(_piece_view(row, "waiting_review"))
        elif _current_with_employee(row):
            progress.append(_piece_view(row, "in_progress"))

    def sort_key(row: dict[str, Any]) -> tuple[str, str, int]:
        return (
            _text(row.get("file_number")),
            _text(row.get("order_number")),
            int(row.get("unit_index") or 0),
        )

    for bucket in (waiting, progress, ready, delivered):
        bucket.sort(key=sort_key)
    return {
        "employee_id": employee_id,
        "employee_name": employee_name,
        "read_only": True,
        "summary": {
            "with_employee": len(waiting) + len(progress),
            "waiting_review": len(waiting),
            "ready_not_handed_off": len(ready),
            "delivered_this_month": len(delivered),
        },
        "sections": {
            "waiting_review": waiting,
            "in_progress": progress,
            "ready_not_handed_off": ready,
            "delivered_this_month": delivered,
        },
    }


async def _require_supervisor(db: Any, user: dict[str, Any]) -> dict[str, Any]:
    context = await _actor_context(db, user)
    permissions = set(context.get("permissions") or [])
    if (
        context.get("is_owner")
        or "preparation.manage" in permissions
        or user_can_manage_preparation(user)
    ):
        return context
    raise HTTPException(
        status_code=403,
        detail={"code": "preparation_supervision_permission_required"},
    )


async def _load_pieces(db: Any, merchant_id: str) -> list[dict[str, Any]]:
    return await db[PIECES].find(
        {
            "user_id": merchant_id,
            "$or": [
                {"experiment_archived_at": {"$exists": False}},
                {"experiment_archived_at": None},
            ],
        },
        {"_id": 0},
    ).sort("updated_at", -1).limit(MAX_PIECES).to_list(MAX_PIECES)


def make_preparation_supervision_router(
    db: Any,
    current_user: Callable[..., Any],
) -> APIRouter:
    router = APIRouter(
        prefix="/preparation-supervision-v1",
        tags=["Preparation Supervision V1"],
    )

    @router.get("/overview")
    async def overview(user: dict = Depends(current_user)) -> dict[str, Any]:
        context = await _require_supervisor(db, user)
        month_start, next_month, month_key = _month_bounds()
        pieces = await _load_pieces(db, context["merchant_id"])
        employees = _employee_rows(
            pieces,
            month_start=month_start,
            next_month=next_month,
        )
        return {
            "ok": True,
            "month": month_key,
            "employees": employees,
            "summary": {
                "employee_count": len(employees),
                "with_employee": sum(row["with_employee"] for row in employees),
                "waiting_review": sum(row["waiting_review"] for row in employees),
                "ready_not_handed_off": sum(row["ready_not_handed_off"] for row in employees),
                "delivered_this_month": sum(row["delivered_this_month"] for row in employees),
            },
            "read_only": True,
            "source": PIECES,
        }

    @router.get("/employees/{employee_id}")
    async def employee_detail(
        employee_id: str,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        context = await _require_supervisor(db, user)
        month_start, next_month, month_key = _month_bounds()
        pieces = await _load_pieces(db, context["merchant_id"])
        detail = _detail_for_employee(
            pieces,
            employee_id=_text(employee_id),
            month_start=month_start,
            next_month=next_month,
        )
        if not detail:
            raise HTTPException(
                status_code=404,
                detail={"code": "preparation_supervision_employee_not_found"},
            )
        return {
            "ok": True,
            "month": month_key,
            "employee": detail,
            "read_only": True,
            "source": PIECES,
        }

    return router


__all__ = [
    "make_preparation_supervision_router",
    "_employee_rows",
    "_detail_for_employee",
]
