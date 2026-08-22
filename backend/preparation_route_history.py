"""Strict preparation-route eligibility and durable transition history.

This module keeps the employee preparation workspace aligned with the order's
current Mezan/Salla status without deleting operational records. A physical
piece remains durable for audit, while its current visibility is derived from
the live order status. Every route/status transition is snapshotted so a later
"outside preparation" page can explain exactly where the order came from,
which employee/supplier held it, and why it left the normal flow.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from pymongo import ASCENDING, DESCENDING


ROUTE_EVENTS = "mezan_preparation_route_events_v1"
PIECES = "mezan_preparation_pieces_v1"
ROUTE_STATE_EMPLOYEE = "employee_preparation"
ROUTE_STATE_OUTSIDE = "outside_preparation"

_EMPLOYEE_ORDER_STATUSES = {
    "reviewed",
    "تمت المراجعة",
    "تم المراجعة",
    "تمت المراجعه",
    "تم المراجعه",
    "processing",
    "in progress",
    "قيد التنفيذ",
    "جاري التنفيذ",
}

_REVIEW_ORDER_STATUSES = {
    "under review",
    "waiting review",
    "pending review",
    "بإنتظار المراجعة",
    "بانتظار المراجعة",
    "انتظار المراجعة",
    "بإنتظار المراجعه",
    "بانتظار المراجعه",
    "انتظار المراجعه",
}

_INSTALLED = False
_INTERNAL_WORKSPACE_LIMIT = 200


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _text(value: Any) -> str:
    return str(value or "").strip()


def normalize_order_status(value: Any) -> str:
    return " ".join(_text(value).casefold().replace("_", " ").split())


def order_status_allows_employee_preparation(value: Any) -> bool:
    return normalize_order_status(value) in _EMPLOYEE_ORDER_STATUSES


def route_state_for_order_status(value: Any) -> str:
    return ROUTE_STATE_EMPLOYEE if order_status_allows_employee_preparation(value) else ROUTE_STATE_OUTSIDE


def outside_reason_for_order_status(value: Any) -> str:
    status = normalize_order_status(value)
    if not status:
        return "order_status_missing_or_unknown"
    if status in _REVIEW_ORDER_STATUSES:
        return "order_returned_to_review_queue"
    if status in {"cancelled", "canceled", "deleted", "ملغي", "ملغى", "محذوف"}:
        return "order_cancelled"
    if status in {"completed", "تم التنفيذ"}:
        return "order_completed_before_preparation_route_finished"
    if status in {"shipping", "shipped", "delivering", "out for delivery", "جاري التوصيل", "تم الشحن"}:
        return "order_moved_to_shipping"
    if status in {"delivered", "تم التوصيل"}:
        return "order_delivered"
    if status in {"refunded", "returned", "restored", "مسترجع", "تم الاسترجاع"}:
        return "order_refunded_or_returned"
    return "order_status_not_eligible_for_preparation"


def _effective_status_from_order(row: dict[str, Any] | None) -> str:
    """Mirror the existing Order Engine status-authority rule."""
    if not row:
        return ""
    current = _text(row.get("order_status"))
    current_normalized = normalize_order_status(current)
    raw = ((row.get("raw_by_source") or {}).get("salla_direct") or {})
    status = raw.get("status") if isinstance(raw, dict) else None
    customized = status.get("customized") if isinstance(status, dict) else None
    if isinstance(customized, dict):
        customized = customized.get("name") or customized.get("label") or customized.get("title") or customized.get("slug")
    customized_text = _text(customized)
    if current_normalized and current_normalized not in _REVIEW_ORDER_STATUSES:
        return current
    if customized_text:
        return customized_text
    if current:
        return current
    if isinstance(status, dict):
        return _text(status.get("name") or status.get("slug"))
    return _text(status)


def piece_route_snapshot(piece: dict[str, Any]) -> dict[str, Any]:
    """Capture the complete operational ownership/stage facts we need to audit."""
    return {
        "piece_status": _text(piece.get("status")) or None,
        "execution_status": _text(piece.get("execution_status")) or None,
        "assignment_status": _text(piece.get("assignment_status")) or None,
        "responsible_employee_id": _text(piece.get("responsible_employee_id")) or None,
        "responsible_employee_name": _text(piece.get("responsible_employee_name")) or None,
        "previous_responsible_employee_id": _text(piece.get("previous_responsible_employee_id")) or None,
        "previous_responsible_employee_name": _text(piece.get("previous_responsible_employee_name")) or None,
        "supplier_id": _text(piece.get("supplier_id")) or None,
        "supplier_name": _text(piece.get("supplier_name")) or None,
        "supplier_dispatch_id": _text(piece.get("supplier_dispatch_id")) or None,
        "supplier_dispatch_status": _text(piece.get("supplier_dispatch_status")) or None,
        "supplier_receiving_session_id": _text(piece.get("supplier_receiving_session_id")) or None,
        "supplier_receiving_reference": _text(piece.get("supplier_receiving_reference")) or None,
        "sent_to_supplier_by_id": _text(piece.get("sent_to_supplier_by_id")) or None,
        "sent_to_supplier_by_name": _text(piece.get("sent_to_supplier_by_name")) or None,
        "received_by_id": _text(piece.get("received_by_id")) or None,
        "received_by_name": _text(piece.get("received_by_name")) or None,
        "service_plan_status": _text(piece.get("service_plan_status")) or None,
        "service_count": int(piece.get("service_count") or 0),
        "completed_service_count": int(piece.get("completed_service_count") or 0),
        "remaining_service_count": int(piece.get("remaining_service_count") or 0),
        "assigned_at": piece.get("assigned_at"),
        "started_at": piece.get("started_at"),
        "sent_to_supplier_at": piece.get("sent_to_supplier_at"),
        "supplier_ready_at": piece.get("supplier_ready_at"),
        "received_at": piece.get("received_at"),
        "branch_handoff_at": piece.get("branch_handoff_at"),
        "file_number": _text(piece.get("file_number")) or None,
        "batch_id": _text(piece.get("batch_id")) or None,
        "product_id": _text(piece.get("product_id")) or None,
        "product_name": _text(piece.get("product_name")) or None,
        "sku": _text(piece.get("sku")) or None,
        "order_item_id": _text(piece.get("order_item_id")) or None,
        "unit_index": piece.get("unit_index"),
        "assignment_history": list(piece.get("assignment_history") or []),
        "supplier_receiving_history": list(piece.get("supplier_receiving_history") or []),
    }


async def ensure_route_history_indexes(db: Any) -> None:
    await db[ROUTE_EVENTS].create_index(
        [("user_id", ASCENDING), ("piece_id", ASCENDING), ("occurred_at", DESCENDING)],
        name="ix_preparation_route_piece_history_v1",
    )
    await db[ROUTE_EVENTS].create_index(
        [("user_id", ASCENDING), ("order_number", ASCENDING), ("occurred_at", DESCENDING)],
        name="ix_preparation_route_order_history_v1",
    )
    await db[ROUTE_EVENTS].create_index(
        [("user_id", ASCENDING), ("route_state", ASCENDING), ("occurred_at", DESCENDING)],
        name="ix_preparation_route_state_v1",
    )
    await db[ROUTE_EVENTS].create_index(
        [("user_id", ASCENDING), ("id", ASCENDING)],
        unique=True,
        name="uq_preparation_route_event_v1",
    )


async def reconcile_employee_workspace_route(
    db: Any,
    *,
    user_id: str,
    employee_id: str,
    pieces: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Persist each order-route transition and return (eligible, outside).

    Core operational state is never deleted/reclassified here. A deterministic
    transition id is used in both the piece's embedded history and the dedicated
    audit collection. The audit event is upserted before the piece update so a
    transient second write failure cannot erase the evidence needed to recover.
    """
    if not pieces:
        return [], []

    await ensure_route_history_indexes(db)
    order_numbers = sorted({_text(piece.get("order_number")) for piece in pieces if _text(piece.get("order_number"))})
    rows = (
        await db.unified_orders.find(
            {"user_id": user_id, "order_number": {"$in": order_numbers}},
            {"_id": 0, "order_number": 1, "order_status": 1, "raw_by_source.salla_direct.status": 1},
        ).to_list(max(1, len(order_numbers)))
        if order_numbers else []
    )
    status_by_order = {
        _text(row.get("order_number")): _effective_status_from_order(row)
        for row in rows if _text(row.get("order_number"))
    }

    now = _now()
    eligible: list[dict[str, Any]] = []
    outside: list[dict[str, Any]] = []

    for piece in pieces:
        piece_id = _text(piece.get("piece_id") or piece.get("id"))
        order_number = _text(piece.get("order_number"))
        current_status = status_by_order.get(order_number, "")
        normalized_status = normalize_order_status(current_status)
        route_state = route_state_for_order_status(current_status)
        previous_route_state = _text(piece.get("preparation_route_state"))
        previous_status = _text(piece.get("preparation_route_order_status"))
        changed = previous_route_state != route_state or normalize_order_status(previous_status) != normalized_status

        piece["preparation_route_state"] = route_state
        piece["preparation_route_order_status"] = current_status or None
        (eligible if route_state == ROUTE_STATE_EMPLOYEE else outside).append(piece)
        if not piece_id or not changed:
            continue

        reason = (
            "order_status_eligible_for_employee_preparation"
            if route_state == ROUTE_STATE_EMPLOYEE
            else outside_reason_for_order_status(current_status)
        )
        snapshot = piece_route_snapshot(piece)
        transition_key = "|".join([
            user_id,
            piece_id,
            previous_route_state or "none",
            normalize_order_status(previous_status) or "none",
            route_state,
            normalized_status or "none",
        ])
        event_id = uuid.uuid5(uuid.NAMESPACE_URL, f"preparation-route:{transition_key}").hex
        history_row = {
            "event_id": event_id,
            "previous_route_state": previous_route_state or None,
            "route_state": route_state,
            "previous_order_status": previous_status or None,
            "order_status": current_status or None,
            "normalized_order_status": normalized_status or None,
            "reason": reason,
            "occurred_at": now,
            "snapshot": snapshot,
        }
        event = {
            "id": event_id,
            "user_id": user_id,
            "piece_id": piece_id,
            "order_number": order_number or None,
            "event_type": "preparation_route_transition",
            "previous_route_state": previous_route_state or None,
            "route_state": route_state,
            "previous_order_status": previous_status or None,
            "order_status": current_status or None,
            "normalized_order_status": normalized_status or None,
            "reason": reason,
            "employee_id_at_observation": employee_id or None,
            "snapshot": snapshot,
            "occurred_at": now,
            "mezan_only": True,
            "salla_updated": False,
            "qoyod_updated": False,
        }
        await db[ROUTE_EVENTS].update_one(
            {"user_id": user_id, "id": event_id},
            {"$setOnInsert": event},
            upsert=True,
        )

        set_values: dict[str, Any] = {
            "preparation_route_state": route_state,
            "preparation_route_order_status": current_status or None,
            "preparation_route_status_observed_at": now,
            "preparation_route_last_event_id": event_id,
        }
        unset_values: dict[str, str] = {}
        if route_state == ROUTE_STATE_OUTSIDE:
            set_values.update({
                "outside_preparation": True,
                "outside_preparation_reason": reason,
                "outside_preparation_order_status": current_status or None,
                "outside_preparation_status_changed_at": now,
            })
            if previous_route_state != ROUTE_STATE_OUTSIDE or not piece.get("outside_preparation_at"):
                set_values["outside_preparation_at"] = now
        else:
            set_values["outside_preparation"] = False
            unset_values = {
                "outside_preparation_reason": "",
                "outside_preparation_order_status": "",
                "outside_preparation_at": "",
                "outside_preparation_status_changed_at": "",
            }
        update_doc: dict[str, Any] = {"$set": set_values, "$addToSet": {"preparation_route_history": history_row}}
        if unset_values:
            update_doc["$unset"] = unset_values

        selector = {
            "user_id": user_id,
            "piece_id": piece_id,
            "responsible_employee_id": employee_id,
            "$or": [
                {"preparation_route_state": {"$ne": route_state}},
                {"preparation_route_order_status": {"$ne": current_status or None}},
            ],
        }
        result = await db[PIECES].update_one(selector, update_doc)
        if not int(result.modified_count or 0):
            continue
        piece.update(set_values)

    return eligible, outside


def _recompute_file_counts(file_row: dict[str, Any]) -> dict[str, Any]:
    products = list(file_row.get("products") or [])
    file_row["piece_count"] = len(products)
    for target, source in (
        ("available_quantity", "available_quantity"),
        ("sent_quantity", "sent_quantity"),
        ("ready_quantity", "ready_quantity"),
        ("received_quantity", "received_quantity"),
    ):
        file_row[target] = sum(int(row.get(source) or 0) for row in products)
    file_row["is_new"] = any(int(row.get("available_quantity") or 0) > 0 for row in products)
    return file_row


def install_supplier_dispatch_route_guard() -> None:
    """Install strict route filtering for the native piece-grain workspace."""
    global _INSTALLED
    if _INSTALLED:
        return
    import preparation_supplier_dispatch as base

    original = base._employee_workspace

    async def guarded_employee_workspace(
        db: Any,
        *,
        user_id: str,
        employee_id: str,
        limit: int,
        piece_grain: bool = False,
    ) -> dict[str, Any]:
        internal_limit = max(limit, _INTERNAL_WORKSPACE_LIMIT) if piece_grain else limit
        result = await original(
            db,
            user_id=user_id,
            employee_id=employee_id,
            limit=internal_limit,
            piece_grain=piece_grain,
        )
        if not piece_grain:
            return result

        raw_pieces = await db[PIECES].find(
            {
                "user_id": user_id,
                "responsible_employee_id": employee_id,
                "experiment_archived_at": None,
                "status": {"$ne": base.PIECE_STATUS_CANCELLED},
            },
            {"_id": 0, "user_id": 0, "image_b64": 0},
        ).sort("updated_at", -1).limit(50000).to_list(50000)
        eligible, outside = await reconcile_employee_workspace_route(
            db,
            user_id=user_id,
            employee_id=employee_id,
            pieces=raw_pieces,
        )
        eligible_ids = {
            _text(row.get("piece_id") or row.get("id"))
            for row in eligible
            if _text(row.get("piece_id") or row.get("id"))
        }

        filtered_files: list[dict[str, Any]] = []
        for raw_file in result.get("files") or []:
            file_row = dict(raw_file)
            file_row["products"] = [
                row
                for row in (raw_file.get("products") or [])
                if _text(row.get("piece_id")) in eligible_ids
            ]
            if file_row["products"]:
                filtered_files.append(_recompute_file_counts(file_row))
        result["summary"] = base.employee_workspace_summary(filtered_files, eligible)
        result["files"] = filtered_files[:limit]

        filtered_accounts: list[dict[str, Any]] = []
        for raw_account in result.get("supplier_accounts") or []:
            account = dict(raw_account)
            account["products"] = [
                row
                for row in (raw_account.get("products") or [])
                if _text(row.get("piece_id")) in eligible_ids
            ]
            if not account["products"]:
                continue
            account["sent_quantity"] = sum(int(row.get("sent_quantity") or 0) for row in account["products"])
            account["ready_quantity"] = sum(int(row.get("ready_quantity") or 0) for row in account["products"])
            account["received_quantity"] = sum(int(row.get("received_quantity") or 0) for row in account["products"])
            filtered_accounts.append(account)
        result["supplier_accounts"] = filtered_accounts
        result["outside_preparation_piece_count"] = len(outside)
        result["route_status_enforced"] = True
        return result

    base._employee_workspace = guarded_employee_workspace
    _INSTALLED = True


__all__ = [
    "PIECES",
    "ROUTE_EVENTS",
    "ROUTE_STATE_EMPLOYEE",
    "ROUTE_STATE_OUTSIDE",
    "ensure_route_history_indexes",
    "install_supplier_dispatch_route_guard",
    "normalize_order_status",
    "order_status_allows_employee_preparation",
    "outside_reason_for_order_status",
    "piece_route_snapshot",
    "reconcile_employee_workspace_route",
    "route_state_for_order_status",
]
