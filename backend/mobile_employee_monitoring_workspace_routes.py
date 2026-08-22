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

MONITORING_PERMISSION = "app.page.operations_monitoring"


def _text(value: Any) -> str:
    return str(value or "").strip()


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


def make_mobile_employee_monitoring_workspace_router(
    db: Any,
    current_user: Callable[..., Any],
) -> APIRouter:
    router = APIRouter(
        prefix="/mobile/operations-monitoring",
        tags=["mobile-operations-monitoring"],
    )

    @router.get("/preparation/{employee_id}/workspace")
    async def employee_workspace(
        employee_id: str,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        owner_id = await _monitoring_owner_scope(db, user)
        employee = await db[EMPLOYEES].find_one(
            {"user_id": owner_id, "id": employee_id},
            {"_id": 0, "id": 1, "display_name": 1, "status": 1},
        )
        if not employee:
            raise HTTPException(
                status_code=404,
                detail={"code": "employee_not_found"},
            )

        workspace = await _employee_workspace(
            db,
            user_id=owner_id,
            employee_id=employee_id,
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
            },
            "workspace": workspace,
            "allowed_monitoring_mutations": [],
        }

    return router


__all__ = ["make_mobile_employee_monitoring_workspace_router"]
