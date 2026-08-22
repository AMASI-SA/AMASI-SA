"""Read-only operations monitoring for the native AMASI app.

The module reads canonical employee, preparation-piece and store-delivery facts.
It never mutates preparation state, invoices, supplier dispatches, custody,
product cost, delivery state, or any external system. Product cost/service edits
remain owned by their existing governed routes.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException, Query

from employees_v2_routes import EMPLOYEES
from mobile_app_permissions import mobile_app_access_for_user
from preparation_piece_operations import (
    PIECES,
    PIECE_STATUS_ASSIGNED,
    PIECE_STATUS_IN_PROGRESS,
    PIECE_STATUS_READY_FOR_RECEIPT,
    PIECE_STATUS_RECEIVED,
    PIECE_STATUS_READY_FOR_ASSEMBLY,
    PIECE_STATUS_BLOCKED,
    PIECE_STATUS_CANCELLED,
)
from store_delivery_driver_routes import STORE_DRIVERS
from store_delivery_handover_routes import ASSIGNMENTS

MONITORING_PERMISSION = "app.page.operations_monitoring"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _as_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        dt = value
    else:
        raw = _text(value)
        if not raw:
            return None
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def resolve_monitoring_range(
    *,
    now: datetime | None = None,
    from_at: Any = None,
    to_at: Any = None,
) -> tuple[datetime, datetime]:
    """Return an inclusive-start/exclusive-end UTC range; default rolling 30d."""
    end = _as_datetime(to_at) or now or datetime.now(timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
    else:
        end = end.astimezone(timezone.utc)
    start = _as_datetime(from_at) or (end - timedelta(days=30))
    if start >= end:
        raise ValueError("operations_monitoring_range_invalid")
    if end - start > timedelta(days=366):
        raise ValueError("operations_monitoring_range_too_large")
    return start, end


def _inside(value: Any, start: datetime, end: datetime) -> bool:
    dt = _as_datetime(value)
    return bool(dt is not None and start <= dt < end)


def _positive_duration_seconds(started_at: Any, completed_at: Any) -> int | None:
    started = _as_datetime(started_at)
    completed = _as_datetime(completed_at)
    if started is None or completed is None or completed <= started:
        return None
    return int((completed - started).total_seconds())


def _piece_waiting_for_supplier_dispatch(piece: dict[str, Any]) -> bool:
    """Match the employee's live «بانتظار المراجعة/الإرسال» queue."""
    return (
        _text(piece.get("status")) == PIECE_STATUS_ASSIGNED
        and not _text(piece.get("supplier_dispatch_status"))
        and not _text(piece.get("supplier_receiving_session_id"))
    )


def summarize_preparation_employee(
    pieces: list[dict[str, Any]],
    *,
    start: datetime,
    end: datetime,
) -> dict[str, Any]:
    """Calculate current workload plus period-scoped completed performance.

    Pending/in-progress/held counts describe the employee's current live custody.
    Completed count and average are scoped to the selected period by completed_at.
    """
    completed_durations: list[int] = []
    completed_in_range = 0
    pending_review_count = 0
    in_progress_count = 0
    current_held = 0
    ready_not_handed_off = 0

    for piece in pieces:
        status = _text(piece.get("status"))
        if status in {PIECE_STATUS_BLOCKED, PIECE_STATUS_CANCELLED}:
            continue

        if status in {
            PIECE_STATUS_ASSIGNED,
            PIECE_STATUS_IN_PROGRESS,
            PIECE_STATUS_READY_FOR_RECEIPT,
            PIECE_STATUS_RECEIVED,
        }:
            current_held += 1
        if _piece_waiting_for_supplier_dispatch(piece):
            pending_review_count += 1
        if status == PIECE_STATUS_IN_PROGRESS:
            in_progress_count += 1
        if status in {PIECE_STATUS_READY_FOR_RECEIPT, PIECE_STATUS_RECEIVED}:
            ready_not_handed_off += 1

        completed_at = piece.get("completed_at")
        if _inside(completed_at, start, end):
            completed_in_range += 1
            seconds = _positive_duration_seconds(piece.get("started_at"), completed_at)
            if seconds is not None:
                completed_durations.append(seconds)

    measured_count = len(completed_durations)
    average_seconds = (
        round(sum(completed_durations) / measured_count)
        if measured_count
        else None
    )
    return {
        "pending_review_count": pending_review_count,
        "in_progress_count": in_progress_count,
        "completed_count": completed_in_range,
        "current_held_pieces": current_held,
        "ready_not_handed_off_pieces": ready_not_handed_off,
        "average_preparation_seconds": average_seconds,
        "measured_count": measured_count,
    }


def summarize_courier(
    assignments: list[dict[str, Any]],
    *,
    start: datetime,
    end: datetime,
) -> dict[str, Any]:
    """Calculate current workload and actual delivery-time metrics."""
    delivery_durations: list[int] = []
    assignment_cycle_durations: list[int] = []
    delivered_count = 0
    assigned_count = 0
    out_for_delivery_count = 0

    for row in assignments:
        status = _text(row.get("status"))
        active = row.get("active") is not False
        if active and status == "assigned":
            assigned_count += 1
        elif active and status == "out_for_delivery":
            out_for_delivery_count += 1

        delivered_at = row.get("delivered_at")
        if status != "delivered" or not _inside(delivered_at, start, end):
            continue
        delivered_count += 1
        actual_seconds = _positive_duration_seconds(
            row.get("out_for_delivery_at"),
            delivered_at,
        )
        if actual_seconds is not None:
            delivery_durations.append(actual_seconds)
        assignment_seconds = _positive_duration_seconds(
            row.get("assigned_at"),
            delivered_at,
        )
        if assignment_seconds is not None:
            assignment_cycle_durations.append(assignment_seconds)

    measured_count = len(delivery_durations)
    assignment_cycle_measured_count = len(assignment_cycle_durations)
    return {
        "assigned_count": assigned_count,
        "out_for_delivery_count": out_for_delivery_count,
        "current_delivery_count": assigned_count + out_for_delivery_count,
        "delivered_count": delivered_count,
        "average_delivery_seconds": (
            round(sum(delivery_durations) / measured_count)
            if measured_count
            else None
        ),
        "measured_count": measured_count,
        "average_assignment_cycle_seconds": (
            round(sum(assignment_cycle_durations) / assignment_cycle_measured_count)
            if assignment_cycle_measured_count
            else None
        ),
        "assignment_cycle_measured_count": assignment_cycle_measured_count,
    }


def _monitoring_piece_view(piece: dict[str, Any]) -> dict[str, Any]:
    return {
        "piece_id": _text(piece.get("piece_id") or piece.get("id")) or None,
        "file_number": _text(piece.get("file_number")) or None,
        "batch_id": _text(piece.get("batch_id")) or None,
        "order_number": _text(piece.get("order_number")) or None,
        "order_item_id": _text(piece.get("order_item_id")) or None,
        "product_id": _text(piece.get("product_id")) or None,
        "product_name": _text(piece.get("product_name")) or None,
        "sku": _text(piece.get("sku")) or None,
        "image_url": (
            _text(piece.get("selected_image_url"))
            or _text(piece.get("resolved_image_url"))
            or _text(piece.get("image_url"))
            or None
        ),
        "product_options": dict(piece.get("product_options_snapshot") or {}),
        "specifications": list(piece.get("specifications_snapshot") or []),
        "service_specifications": list(piece.get("service_specifications_snapshot") or []),
        "services": list(piece.get("services") or []),
        "status": _text(piece.get("status")) or None,
        "assigned_at": piece.get("assigned_at"),
        "started_at": piece.get("started_at"),
        "completed_at": piece.get("completed_at"),
        "due_at": piece.get("due_at"),
        "supplier_dispatch_status": _text(piece.get("supplier_dispatch_status")) or None,
        "supplier_dispatch_id": _text(piece.get("supplier_dispatch_id")) or None,
        "supplier_id": _text(piece.get("supplier_id")) or None,
        "supplier_name": _text(piece.get("supplier_name")) or None,
        "supplier_receiving_session_id": _text(piece.get("supplier_receiving_session_id")) or None,
        "read_only": True,
    }


def _monitoring_assignment_view(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "assignment_id": _text(row.get("id")) or None,
        "order_id": _text(row.get("order_id")) or None,
        "order_number": _text(row.get("order_number")) or None,
        "barcode": _text(row.get("barcode")) or None,
        "status": _text(row.get("status")) or None,
        "active": row.get("active") is not False,
        "assigned_at": row.get("assigned_at"),
        "out_for_delivery_at": row.get("out_for_delivery_at"),
        "delivered_at": row.get("delivered_at"),
        "driver_name_snapshot": _text(row.get("driver_name_snapshot")) or None,
        "shipping_city_snapshot": _text(row.get("shipping_city_snapshot")) or None,
        "delivery_fee_snapshot": row.get("delivery_fee_snapshot"),
        "read_only": True,
    }


def make_mobile_operations_monitoring_router(
    db: Any,
    current_user: Callable[..., Any],
) -> APIRouter:
    router = APIRouter(prefix="/mobile/operations-monitoring", tags=["mobile-operations-monitoring"])

    async def require_monitoring_access(user: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        access = await mobile_app_access_for_user(db, user)
        if MONITORING_PERMISSION not in set(access.get("permissions") or []):
            raise HTTPException(
                status_code=403,
                detail={"code": "mobile_operations_monitoring_permission_required"},
            )
        role = _text(user.get("role")).casefold()
        owner_id = (
            _text(user.get("id"))
            if role == "owner" or user.get("is_owner") is True
            else _text(user.get("created_by"))
        )
        if not owner_id:
            raise HTTPException(status_code=403, detail={"code": "mobile_owner_scope_required"})
        return owner_id, access

    @router.get("/preparation")
    async def preparation_summary(
        from_at: str | None = Query(default=None),
        to_at: str | None = Query(default=None),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        owner_id, _ = await require_monitoring_access(user)
        try:
            start, end = resolve_monitoring_range(from_at=from_at, to_at=to_at)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail={"code": str(exc)}) from exc

        employees = await db[EMPLOYEES].find(
            {"user_id": owner_id, "status": {"$ne": "terminated"}},
            {"_id": 0, "id": 1, "display_name": 1, "job_title": 1, "department": 1, "status": 1},
        ).sort("display_name", 1).to_list(5000)
        employee_ids = [_text(row.get("id")) for row in employees if _text(row.get("id"))]
        pieces = await db[PIECES].find(
            {"user_id": owner_id, "responsible_employee_id": {"$in": employee_ids}},
            {"_id": 0},
        ).to_list(20000)
        by_employee: dict[str, list[dict[str, Any]]] = {}
        for piece in pieces:
            by_employee.setdefault(_text(piece.get("responsible_employee_id")), []).append(piece)

        rows = []
        for employee in employees:
            employee_id = _text(employee.get("id"))
            rows.append({
                "employee_id": employee_id,
                "employee_name": _text(employee.get("display_name")) or "موظف",
                "job_title": _text(employee.get("job_title")) or None,
                "department": _text(employee.get("department")) or None,
                "status": _text(employee.get("status")) or None,
                **summarize_preparation_employee(
                    by_employee.get(employee_id, []),
                    start=start,
                    end=end,
                ),
            })
        return {
            "ok": True,
            "read_only": True,
            "range": {"from_at": start.isoformat(), "to_at": end.isoformat()},
            "employees": rows,
        }

    @router.get("/preparation/{employee_id}")
    async def preparation_employee_detail(
        employee_id: str,
        from_at: str | None = Query(default=None),
        to_at: str | None = Query(default=None),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        owner_id, _ = await require_monitoring_access(user)
        employee = await db[EMPLOYEES].find_one(
            {"user_id": owner_id, "id": employee_id},
            {"_id": 0, "id": 1, "display_name": 1, "job_title": 1, "department": 1, "status": 1},
        )
        if not employee:
            raise HTTPException(status_code=404, detail={"code": "employee_not_found"})
        try:
            start, end = resolve_monitoring_range(from_at=from_at, to_at=to_at)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail={"code": str(exc)}) from exc

        pieces = await db[PIECES].find(
            {"user_id": owner_id, "responsible_employee_id": employee_id},
            {"_id": 0},
        ).sort("assigned_at", -1).to_list(5000)
        summary = summarize_preparation_employee(pieces, start=start, end=end)
        buckets = {
            "pending": [],
            "in_progress": [],
            "ready_for_receipt": [],
            "received": [],
            "completed": [],
        }
        for piece in pieces:
            status = _text(piece.get("status"))
            view = _monitoring_piece_view(piece)
            if _piece_waiting_for_supplier_dispatch(piece):
                buckets["pending"].append(view)
            elif status == PIECE_STATUS_IN_PROGRESS:
                buckets["in_progress"].append(view)
            elif status == PIECE_STATUS_READY_FOR_RECEIPT:
                buckets["ready_for_receipt"].append(view)
            elif status == PIECE_STATUS_RECEIVED:
                buckets["received"].append(view)
            elif status == PIECE_STATUS_READY_FOR_ASSEMBLY or piece.get("completed_at"):
                if _inside(piece.get("completed_at"), start, end):
                    buckets["completed"].append(view)
        return {
            "ok": True,
            "read_only": True,
            "range": {"from_at": start.isoformat(), "to_at": end.isoformat()},
            "employee": {
                "employee_id": employee_id,
                "employee_name": _text(employee.get("display_name")) or "موظف",
                "job_title": _text(employee.get("job_title")) or None,
                "department": _text(employee.get("department")) or None,
                "status": _text(employee.get("status")) or None,
            },
            "summary": summary,
            "buckets": buckets,
            "product_navigation": {
                "enabled": True,
                "target": "app.page.products",
                "allowed_mutations": ["product_cost", "product_service_cost_configuration"],
                "monitoring_mutations": [],
            },
        }

    @router.get("/couriers")
    async def courier_summary(
        from_at: str | None = Query(default=None),
        to_at: str | None = Query(default=None),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        owner_id, _ = await require_monitoring_access(user)
        try:
            start, end = resolve_monitoring_range(from_at=from_at, to_at=to_at)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail={"code": str(exc)}) from exc

        drivers = await db[STORE_DRIVERS].find(
            {"user_id": owner_id},
            {"_id": 0, "id": 1, "name": 1, "phone": 1, "city": 1, "status": 1},
        ).sort("name", 1).to_list(5000)
        driver_ids = [_text(row.get("id")) for row in drivers if _text(row.get("id"))]
        assignments = await db[ASSIGNMENTS].find(
            {"user_id": owner_id, "driver_id": {"$in": driver_ids}},
            {"_id": 0},
        ).to_list(100000)
        by_driver: dict[str, list[dict[str, Any]]] = {}
        for assignment in assignments:
            by_driver.setdefault(_text(assignment.get("driver_id")), []).append(assignment)

        rows = []
        for driver in drivers:
            driver_id = _text(driver.get("id"))
            rows.append({
                "driver_id": driver_id,
                "driver_name": _text(driver.get("name")) or "موصل",
                "phone": _text(driver.get("phone")) or None,
                "city": _text(driver.get("city")) or None,
                "status": _text(driver.get("status")) or None,
                **summarize_courier(
                    by_driver.get(driver_id, []),
                    start=start,
                    end=end,
                ),
            })
        return {
            "ok": True,
            "read_only": True,
            "range": {"from_at": start.isoformat(), "to_at": end.isoformat()},
            "couriers": rows,
        }

    @router.get("/couriers/{driver_id}")
    async def courier_detail(
        driver_id: str,
        from_at: str | None = Query(default=None),
        to_at: str | None = Query(default=None),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        owner_id, _ = await require_monitoring_access(user)
        driver = await db[STORE_DRIVERS].find_one(
            {"user_id": owner_id, "id": driver_id},
            {"_id": 0, "id": 1, "name": 1, "phone": 1, "city": 1, "status": 1},
        )
        if not driver:
            raise HTTPException(status_code=404, detail={"code": "store_driver_not_found"})
        try:
            start, end = resolve_monitoring_range(from_at=from_at, to_at=to_at)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail={"code": str(exc)}) from exc

        assignments = await db[ASSIGNMENTS].find(
            {"user_id": owner_id, "driver_id": driver_id},
            {"_id": 0},
        ).sort("assigned_at", -1).to_list(5000)
        summary = summarize_courier(assignments, start=start, end=end)
        buckets = {"assigned": [], "out_for_delivery": [], "delivered": []}
        for assignment in assignments:
            status = _text(assignment.get("status"))
            if status in {"assigned", "out_for_delivery"} and assignment.get("active") is not False:
                buckets[status].append(_monitoring_assignment_view(assignment))
            elif status == "delivered" and _inside(assignment.get("delivered_at"), start, end):
                buckets["delivered"].append(_monitoring_assignment_view(assignment))
        return {
            "ok": True,
            "read_only": True,
            "range": {"from_at": start.isoformat(), "to_at": end.isoformat()},
            "courier": {
                "driver_id": driver_id,
                "driver_name": _text(driver.get("name")) or "موصل",
                "phone": _text(driver.get("phone")) or None,
                "city": _text(driver.get("city")) or None,
                "status": _text(driver.get("status")) or None,
            },
            "summary": summary,
            "buckets": buckets,
        }

    return router


__all__ = [
    "MONITORING_PERMISSION",
    "make_mobile_operations_monitoring_router",
    "resolve_monitoring_range",
    "summarize_preparation_employee",
    "summarize_courier",
]
