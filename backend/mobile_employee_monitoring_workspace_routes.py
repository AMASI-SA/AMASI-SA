"""Read-only My Products workspace projection for operations monitoring.

This route deliberately reuses the canonical employee supplier/preparation
workspace builder so the monitoring screen can render the same information
architecture as «إدارة منتجاتي» without exposing any workflow mutation route.
"""
from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException

from employees_v2_routes import EMPLOYEES
from mobile_app_permissions import mobile_app_access_for_user
from preparation_supplier_dispatch import _employee_workspace
from datetime import datetime, timezone
import hashlib

MONITORING_PERMISSION = "app.page.operations_monitoring"


def _text(value: Any) -> str:
    return str(value or "").strip()


def monitored_work_account_id(employee: dict[str, Any]) -> str:
    """Use the login identity that preparation assignment and My Products use."""
    return _text(employee.get("account_user_id")) or _text(employee.get("id"))


async def _monitoring_owner_scope(
    db: Any,
    user: dict[str, Any],
) -> str:
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
        raise HTTPException(
            status_code=403,
            detail={"code": "mobile_owner_scope_required"},
        )
    return owner_id


async def _resolve_monitored_employee(
    db: Any,
    *,
    owner_id: str,
    employee_id: str,
) -> dict[str, Any] | None:
    """Resolve a real employee or the merchant owner synthetic monitoring row.

    The monitoring summary intentionally exposes the owner as a preparation
    card even when the owner does not have a duplicate Employees V2 record.
    Detail/workspace reads must resolve that same synthetic identity instead of
    returning employee_not_found.
    """
    employee = await db[EMPLOYEES].find_one(
        {"user_id": owner_id, "id": employee_id},
        {"_id": 0, "id": 1, "account_user_id": 1, "display_name": 1,
         "job_title": 1, "department": 1, "status": 1},
    )
    if employee:
        return employee
    if _text(employee_id) != _text(owner_id):
        return None
    owner = await db.users.find_one(
        {"id": owner_id},
        {"_id": 0, "id": 1, "name": 1, "display_name": 1, "email": 1},
    )
    if not owner:
        return None
    return {
        "id": owner_id,
        "display_name": (
            _text(owner.get("display_name"))
            or _text(owner.get("name"))
            or _text(owner.get("email"))
            or "مالك المتجر"
        ),
        "status": "active",
        "synthetic_owner": True,
    }


def make_mobile_employee_monitoring_workspace_router(
    db: Any,
    current_user: Callable[..., Any],
) -> APIRouter:
    router = APIRouter(
        prefix="/mobile/operations-monitoring",
        tags=["mobile-operations-monitoring"],
    )

    @router.post("/preparation/{employee_id}/pieces/{piece_id}/urgency")
    async def request_urgency(employee_id: str, piece_id: str, user: dict = Depends(current_user)):
        from operational_push import notification_actor
        from order_tracking_notes import ORDER_TRACKING_INSTRUCTIONS
        from operational_instruction_notifications import ensure_instruction_tasks, emit_instruction_notification, recipients
        from operational_instruction_progress import refresh_instruction_progress
        owner_id = await _monitoring_owner_scope(db, user)
        actor = await notification_actor(db, user)
        employee = await _resolve_monitored_employee(db, owner_id=owner_id, employee_id=employee_id)
        if not employee:
            raise HTTPException(404, detail={"code": "employee_not_found"})
        account = monitored_work_account_id(employee)
        piece = await db["mezan_preparation_pieces_v1"].find_one({
            "user_id": owner_id, "piece_id": piece_id, "responsible_employee_id": account,
            "experiment_archived_at": {"$in": [None, ""]}}, {"_id": 0})
        if not piece:
            raise HTTPException(404, detail={"code": "preparation_piece_not_found"})
        if piece.get("status") not in {"assigned", "in_progress", "blocked"} or piece.get("branch_handoff_at"):
            raise HTTPException(409, detail={"code": "preparation_responsibility_ended"})
        identity = "monitor-urgency-" + hashlib.sha256(f"{owner_id}:{actor['id']}:{piece_id}:{piece.get('assigned_at') or ''}".encode()).hexdigest()
        row = {"id": identity, "user_id": owner_id, "created_by": actor["id"],
            "created_by_name": actor.get("name") or actor.get("display_name") or "موظف",
            "order_number": _text(piece.get("order_number")), "scope": "piece", "target_ids": [piece_id],
            "target_id": piece_id, "action_type": "urgent_preparation", "priority": "urgent",
            "note": "عجّل بتجهيز المنتج، تأخّر", "status": "active", "enforcement": "notice",
            "required_action": "none", "approval_required": False, "thread_protocol": 1,
            "urgency_receipt_protocol": 1, "activation_state": "active",
            "target_stages": ["preparation", "supplier_dispatch", "supplier_receiving", "preparation_receiving"],
            "created_at": datetime.now(timezone.utc)}
        await db[ORDER_TRACKING_INSTRUCTIONS].update_one({"_id": identity}, {"$setOnInsert": row}, upsert=True)
        saved = await db[ORDER_TRACKING_INSTRUCTIONS].find_one({"_id": identity}, {"_id": 0})
        await ensure_instruction_tasks(db, owner_id, saved)
        await refresh_instruction_progress(db, saved)
        await emit_instruction_notification(db, merchant_id=owner_id, instruction=saved,
            event_type="created", recipient_ids={account} & await recipients(db, owner_id, saved))
        return {"ok": True, "instruction_id": identity}

    @router.get("/preparation/{employee_id}/workspace")
    async def employee_workspace(
        employee_id: str,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        owner_id = await _monitoring_owner_scope(db, user)
        employee = await _resolve_monitored_employee(
            db,
            owner_id=owner_id,
            employee_id=employee_id,
        )
        if not employee:
            raise HTTPException(
                status_code=404,
                detail={"code": "employee_not_found"},
            )

        workspace = await _employee_workspace(
            db,
            user_id=owner_id,
            employee_id=monitored_work_account_id(employee),
            limit=100,
            piece_grain=True,
        )
        return {
            "ok": True,
            "read_only": True,
            "employee": {
                "employee_id": employee_id,
                "employee_name": _text(employee.get("display_name")) or "موظف",
                "status": _text(employee.get("status")) or None,
                "is_owner": bool(employee.get("synthetic_owner")),
            },
            "workspace": workspace,
            "allowed_monitoring_mutations": [],
        }

    return router


__all__ = [
    "_resolve_monitored_employee",
    "monitored_work_account_id",
    "make_mobile_employee_monitoring_workspace_router",
]
