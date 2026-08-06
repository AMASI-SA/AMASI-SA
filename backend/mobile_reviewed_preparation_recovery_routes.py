"""Recovery wrapper for the mobile reviewed-preparation file endpoint.

The underlying builder intentionally performs several durable steps: reserve
units, build a PDF snapshot, commit allocations, register the file, and attach
the assignment to reviewed workflows.  This wrapper makes the last steps
recoverable after a transient database failure without deleting already
committed unit allocations or creating duplicate assignment records.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException

import mobile_reviewed_preparation_routes as original
import reviewed_preparation_batches as batch_module
from order_review_routes import EVENTS, WORKFLOWS, _merchant_user_id, _require_reviewer, _text
from preparation_file_registry import REGISTRY
from reviewed_products_catalog import PREPARATION_UNIT_ALLOCATIONS


async def _safe_rollback_build(
    db: Any,
    *,
    user_id: str,
    batch_id: str,
    request_id: str,
) -> None:
    """Remove only uncommitted work; preserve a completed PDF snapshot."""
    await db[PREPARATION_UNIT_ALLOCATIONS].delete_many({
        "user_id": user_id,
        "batch_id": batch_id,
        "status": "reserved",
    })
    ready = await db[batch_module.BATCHES].find_one(
        {"user_id": user_id, "id": batch_id, "status": "ready"},
        {"_id": 0, "id": 1},
    )
    if ready:
        return
    await db[batch_module.BATCHES].delete_one({
        "user_id": user_id,
        "id": batch_id,
        "status": {"$ne": "ready"},
    })
    await db[REGISTRY].delete_one({
        "user_id": user_id,
        "client_request_id": request_id,
        "status": {"$ne": "ready"},
    })


def _stable_assigned_at(batch: dict[str, Any], registry: dict[str, Any]) -> str:
    value = (
        batch.get("ready_at")
        or registry.get("registered_at")
        or batch.get("created_at")
        or registry.get("created_at")
    )
    if isinstance(value, datetime):
        return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).isoformat()
    return _text(value) or datetime.now(timezone.utc).isoformat()


async def _stable_record_planning_assignments(
    db: Any,
    *,
    user_id: str,
    batch: dict[str, Any],
    registry: dict[str, Any],
    actor: dict[str, Any],
) -> None:
    """Attach one deterministic assignment per file and order."""
    actor_id = _text(actor.get("id"))
    assigned_at = _stable_assigned_at(batch, registry)
    order_numbers = sorted({
        _text(row.get("order_number"))
        for row in batch.get("lines") or []
        if _text(row.get("order_number"))
    } | {
        _text(value)
        for value in batch.get("order_numbers") or []
        if _text(value)
    })
    assignment = {
        "batch_id": _text(batch.get("id")),
        "file_number": _text(registry.get("file_number") or batch.get("file_number")),
        "file_title": _text(registry.get("file_title") or batch.get("file_title")),
        "status": original.ASSIGNMENT_STATUS,
        "responsible_employee_id": _text(
            registry.get("responsible_employee_id")
            or batch.get("responsible_employee_id")
        ),
        "responsible_employee_name": _text(
            registry.get("responsible_employee_name")
            or batch.get("responsible_employee_name")
        ),
        "required_completion_at": (
            registry.get("required_completion_at")
            or batch.get("required_completion_at")
        ),
        "required_completion_at_riyadh": _text(
            registry.get("required_completion_at_riyadh")
            or batch.get("required_completion_at_riyadh")
        ),
        "assigned_at": assigned_at,
        "assigned_by": actor_id,
    }
    for order_number in order_numbers:
        result = await db[WORKFLOWS].update_one(
            {
                "user_id": user_id,
                "order_number": order_number,
                "stage": "reviewed",
            },
            {
                "$addToSet": {
                    "preparation_batch_ids": assignment["batch_id"],
                    "preparation_assignments": assignment,
                },
                "$set": {
                    "last_preparation_assignment": assignment,
                    "updated_at": assigned_at,
                    "updated_by": actor_id,
                },
            },
        )
        if int(getattr(result, "matched_count", 0) or 0) == 0:
            continue
        event_selector = {
            "user_id": user_id,
            "order_number": order_number,
            "batch_id": assignment["batch_id"],
            "event_type": "preparation_file_assigned_not_started",
        }
        existing_event = await db[EVENTS].find_one(event_selector, {"_id": 1})
        if not existing_event:
            await db[EVENTS].insert_one({
                **event_selector,
                "file_number": assignment["file_number"],
                "responsible_employee_id": assignment["responsible_employee_id"],
                "required_completion_at": assignment["required_completion_at"],
                "occurred_at": assigned_at,
                "actor_id": actor_id,
                "mezan_only": True,
                "salla_updated": False,
                "qoyod_updated": False,
            })


async def _repair_registry(
    db: Any,
    *,
    user_id: str,
    batch: dict[str, Any],
) -> dict[str, Any]:
    request_id = _text(batch.get("client_request_id"))
    row = await db[REGISTRY].find_one(
        {"user_id": user_id, "client_request_id": request_id},
        {"_id": 0},
    )
    now = datetime.now(timezone.utc)
    patch = {
        "status": "ready",
        "batch_id": _text(batch.get("id")),
        "allocated_quantity": int(batch.get("allocated_quantity") or 0),
        "selected_product_count": int(batch.get("selected_product_count") or 0),
        "order_count": int(batch.get("order_count") or 0),
        "file_number": _text((row or {}).get("file_number") or batch.get("file_number")),
        "file_title": _text((row or {}).get("file_title") or batch.get("file_title")),
        "file_name": _text((row or {}).get("file_name") or batch.get("file_name")),
        "responsible_employee_id": _text(
            (row or {}).get("responsible_employee_id")
            or batch.get("responsible_employee_id")
        ),
        "responsible_employee_name": _text(
            (row or {}).get("responsible_employee_name")
            or batch.get("responsible_employee_name")
        ),
        "responsible_employee_email": _text(
            (row or {}).get("responsible_employee_email")
            or batch.get("responsible_employee_email")
        ),
        "required_completion_at": (
            (row or {}).get("required_completion_at")
            or batch.get("required_completion_at")
        ),
        "required_completion_at_riyadh": _text(
            (row or {}).get("required_completion_at_riyadh")
            or batch.get("required_completion_at_riyadh")
        ),
        "required_completion_display": _text(
            (row or {}).get("required_completion_display")
            or batch.get("required_completion_display")
        ),
        "assignment_status": original.ASSIGNMENT_STATUS,
        "registered_at": (row or {}).get("registered_at") or batch.get("ready_at") or now,
        "updated_at": now,
        "mezan_only": True,
        "salla_updated": False,
        "qoyod_updated": False,
    }
    await db[REGISTRY].update_one(
        {"user_id": user_id, "client_request_id": request_id},
        {
            "$set": patch,
            "$setOnInsert": {
                "id": f"registry-{request_id}",
                "user_id": user_id,
                "client_request_id": request_id,
                "created_at": batch.get("created_at") or now,
            },
            "$unset": {"expires_at": ""},
        },
        upsert=True,
    )
    return {**(row or {}), **patch, "client_request_id": request_id}


def install_mobile_reviewed_preparation_recovery() -> None:
    original._rollback_build = _safe_rollback_build
    original._record_planning_assignments = _stable_record_planning_assignments


def make_mobile_reviewed_preparation_recovery_router(
    db: Any,
    current_user: Callable,
) -> APIRouter:
    """Expose a repaired POST /files route before the original router."""
    install_mobile_reviewed_preparation_recovery()
    source_router = original.make_mobile_reviewed_preparation_router(db, current_user)
    source_route = next(
        (
            route for route in source_router.routes
            if route.path.endswith("/files") and "POST" in (route.methods or set())
        ),
        None,
    )
    if source_route is None:
        raise RuntimeError("mobile_reviewed_preparation_create_route_missing")
    source_endpoint = source_route.endpoint

    router = APIRouter(
        prefix="/mobile-reviewed-preparation-v1",
        tags=["Mobile Reviewed Preparation Recovery"],
    )

    @router.post("/files")
    async def create_file_recoverable(
        payload: original.CreateMobilePreparationFileRequest,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        reviewer = _require_reviewer(user)
        user_id = _merchant_user_id(reviewer)
        result = await source_endpoint(payload=payload, user=user)
        batch = await db[batch_module.BATCHES].find_one(
            {
                "user_id": user_id,
                "client_request_id": payload.client_request_id,
                "status": "ready",
            },
            {"_id": 0},
        )
        if not batch:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "preparation_file_not_ready_after_create",
                    "message": "لم يكتمل تسجيل ملف التجهيز. أعد المحاولة بنفس الاختيار.",
                },
            )
        registry = await _repair_registry(db, user_id=user_id, batch=batch)
        await _stable_record_planning_assignments(
            db,
            user_id=user_id,
            batch=batch,
            registry=registry,
            actor=reviewer,
        )
        return original._file_response(batch, registry) or result

    return router


__all__ = [
    "install_mobile_reviewed_preparation_recovery",
    "make_mobile_reviewed_preparation_recovery_router",
]
