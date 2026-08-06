"""Mobile execution controls for preparation files assigned from reviewed stage.

A generated file starts as ``assigned_not_started``. The responsible employee
(or an owner/operations override) explicitly starts the file. Only then does
Mezan reconcile each order into ``in_progress`` when all of that order's
supplier-file units have already been allocated. No Salla or Qoyod writes are
performed here.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Literal

from fastapi import APIRouter, Depends, HTTPException, Query

import reviewed_preparation_batches as batch_module
from mobile_reviewed_preparation_routes import ASSIGNMENT_STATUS
from order_review_export_controls import user_can_manage_preparation
from order_review_routes import EVENTS, WORKFLOWS, _merchant_user_id, _require_reviewer, _text
from preparation_file_registry import REGISTRY


EXECUTION_STATUS = "in_progress"
VISIBLE_STATUSES = (ASSIGNMENT_STATUS, EXECUTION_STATUS)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _actor_can_override(user: dict[str, Any]) -> bool:
    role = _text(user.get("role")).casefold()
    return bool(
        user.get("is_owner") is True
        or user.get("isOwner") is True
        or role in {"owner", "admin", "operations"}
    )


def _due_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    raw = _text(value)
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def preparation_execution_file_view(
    row: dict[str, Any],
    *,
    actor_id: str,
    can_override: bool,
) -> dict[str, Any]:
    status = _text(row.get("assignment_status")) or ASSIGNMENT_STATUS
    due = _due_datetime(row.get("required_completion_at"))
    now = _now()
    assigned_to_actor = _text(row.get("responsible_employee_id")) == actor_id
    return {
        "batch_id": _text(row.get("id")),
        "file_number": _text(row.get("file_number")),
        "file_title": _text(row.get("file_title")) or "ملف تجهيز",
        "file_name": _text(row.get("file_name")),
        "status": status,
        "status_label": "مسند ولم يبدأ" if status == ASSIGNMENT_STATUS else "قيد التنفيذ",
        "allocated_quantity": int(row.get("allocated_quantity") or 0),
        "selected_product_count": int(row.get("selected_product_count") or 0),
        "order_count": int(row.get("order_count") or 0),
        "order_numbers": list(row.get("order_numbers") or []),
        "responsible_employee_id": _text(row.get("responsible_employee_id")),
        "responsible_employee_name": _text(row.get("responsible_employee_name")),
        "required_completion_at": row.get("required_completion_at"),
        "required_completion_at_riyadh": _text(row.get("required_completion_at_riyadh")),
        "required_completion_display": _text(row.get("required_completion_display")),
        "execution_started_at": row.get("execution_started_at"),
        "execution_started_by": _text(row.get("execution_started_by")),
        "overdue": bool(due and due < now and status != "completed"),
        "can_start": bool(
            status == ASSIGNMENT_STATUS
            and (assigned_to_actor or can_override)
        ),
        "can_print": True,
        "mezan_only": True,
        "salla_updated": False,
        "qoyod_updated": False,
    }


def _assert_file_access(
    batch: dict[str, Any],
    *,
    actor: dict[str, Any],
) -> None:
    actor_id = _text(actor.get("id"))
    if _text(batch.get("responsible_employee_id")) == actor_id:
        return
    if _actor_can_override(actor):
        return
    raise HTTPException(
        status_code=403,
        detail={
            "code": "preparation_file_not_assigned_to_actor",
            "message": "هذا الملف مسند إلى موظف آخر.",
        },
    )


def make_mobile_preparation_execution_router(
    db: Any,
    current_user: Callable,
) -> APIRouter:
    router = APIRouter(
        prefix="/mobile-preparation-execution-v1",
        tags=["Mobile Preparation Execution"],
    )

    @router.get("/files")
    async def list_files(
        scope: Literal["mine", "all"] = Query("mine"),
        limit: int = Query(100, ge=1, le=300),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        reviewer = _require_reviewer(user)
        if not user_can_manage_preparation(reviewer):
            raise HTTPException(status_code=403, detail={"code": "preparation_permission_required"})
        user_id = _merchant_user_id(reviewer)
        actor_id = _text(reviewer.get("id"))
        can_override = _actor_can_override(reviewer)
        query: dict[str, Any] = {
            "user_id": user_id,
            "status": "ready",
            "assignment_status": {"$in": list(VISIBLE_STATUSES)},
        }
        if scope == "mine" or not can_override:
            query["responsible_employee_id"] = actor_id
        rows = await db[batch_module.BATCHES].find(
            query,
            {"_id": 0, "lines": 0},
        ).sort("required_completion_at", 1).limit(limit).to_list(limit)
        return {
            "items": [
                preparation_execution_file_view(
                    row,
                    actor_id=actor_id,
                    can_override=can_override,
                )
                for row in rows
            ],
            "scope": scope if can_override else "mine",
        }

    @router.post("/files/{batch_id}/start")
    async def start_file(
        batch_id: str,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        reviewer = _require_reviewer(user)
        if not user_can_manage_preparation(reviewer):
            raise HTTPException(status_code=403, detail={"code": "preparation_permission_required"})
        user_id = _merchant_user_id(reviewer)
        actor_id = _text(reviewer.get("id"))
        batch = await db[batch_module.BATCHES].find_one(
            {"user_id": user_id, "id": batch_id, "status": "ready"},
            {"_id": 0},
        )
        if not batch:
            raise HTTPException(status_code=404, detail={"code": "preparation_batch_not_found"})
        _assert_file_access(batch, actor=reviewer)

        current_status = _text(batch.get("assignment_status")) or ASSIGNMENT_STATUS
        if current_status not in VISIBLE_STATUSES:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "preparation_file_cannot_start",
                    "message": "حالة الملف الحالية لا تسمح ببدء التنفيذ.",
                },
            )
        if current_status == EXECUTION_STATUS:
            return {
                "ok": True,
                "idempotent": True,
                "file": preparation_execution_file_view(
                    batch,
                    actor_id=actor_id,
                    can_override=_actor_can_override(reviewer),
                ),
                "transitioned_order_numbers": list(batch.get("started_transitioned_order_numbers") or []),
                "remaining_review_order_numbers": list(batch.get("started_remaining_review_order_numbers") or []),
            }

        started_at = _now()
        update_result = await db[batch_module.BATCHES].update_one(
            {
                "user_id": user_id,
                "id": batch_id,
                "status": "ready",
                "assignment_status": ASSIGNMENT_STATUS,
            },
            {
                "$set": {
                    "assignment_status": EXECUTION_STATUS,
                    "execution_started_at": started_at,
                    "execution_started_by": actor_id,
                    "execution_started_by_name": _text(reviewer.get("name") or reviewer.get("email")),
                    "updated_at": started_at,
                },
            },
        )
        if not int(getattr(update_result, "modified_count", 0) or 0):
            latest = await db[batch_module.BATCHES].find_one(
                {"user_id": user_id, "id": batch_id, "status": "ready"},
                {"_id": 0},
            )
            if latest and _text(latest.get("assignment_status")) == EXECUTION_STATUS:
                return {
                    "ok": True,
                    "idempotent": True,
                    "file": preparation_execution_file_view(
                        latest,
                        actor_id=actor_id,
                        can_override=_actor_can_override(reviewer),
                    ),
                    "transitioned_order_numbers": list(latest.get("started_transitioned_order_numbers") or []),
                    "remaining_review_order_numbers": list(latest.get("started_remaining_review_order_numbers") or []),
                }
            raise HTTPException(status_code=409, detail={"code": "preparation_file_start_conflict"})

        order_numbers = sorted({
            _text(value) for value in (batch.get("order_numbers") or []) if _text(value)
        })
        transitioned: list[str] = []
        remaining_review: list[str] = []
        reconciliation_required: list[str] = []
        for order_number in order_numbers:
            try:
                complete, remaining = await batch_module._reconcile_order_stage(
                    db,
                    user_id=user_id,
                    order_number=order_number,
                    batch_id=batch_id,
                    actor=reviewer,
                )
                if complete:
                    transitioned.append(order_number)
                elif remaining > 0:
                    remaining_review.append(order_number)
            except Exception:
                reconciliation_required.append(order_number)

        execution_record = {
            "batch_id": batch_id,
            "file_number": _text(batch.get("file_number")),
            "status": EXECUTION_STATUS,
            "responsible_employee_id": _text(batch.get("responsible_employee_id")),
            "responsible_employee_name": _text(batch.get("responsible_employee_name")),
            "required_completion_at": batch.get("required_completion_at"),
            "started_at": started_at,
            "started_by": actor_id,
        }
        if order_numbers:
            await db[WORKFLOWS].update_many(
                {"user_id": user_id, "order_number": {"$in": order_numbers}},
                {
                    "$set": {
                        f"preparation_execution_by_batch.{batch_id}": execution_record,
                        "last_preparation_execution": execution_record,
                        "updated_at": started_at.isoformat(),
                        "updated_by": actor_id,
                    },
                },
            )
        result_patch = {
            "started_transitioned_order_numbers": transitioned,
            "started_remaining_review_order_numbers": remaining_review,
            "started_reconciliation_required": reconciliation_required,
        }
        await db[batch_module.BATCHES].update_one(
            {"user_id": user_id, "id": batch_id},
            {"$set": result_patch},
        )
        await db[REGISTRY].update_one(
            {"user_id": user_id, "batch_id": batch_id},
            {
                "$set": {
                    "assignment_status": EXECUTION_STATUS,
                    "execution_started_at": started_at,
                    "execution_started_by": actor_id,
                    "updated_at": started_at,
                },
            },
        )
        await db[EVENTS].insert_one({
            "user_id": user_id,
            "batch_id": batch_id,
            "file_number": batch.get("file_number"),
            "event_type": "preparation_file_execution_started",
            "order_numbers": order_numbers,
            "transitioned_order_numbers": transitioned,
            "remaining_review_order_numbers": remaining_review,
            "occurred_at": started_at.isoformat(),
            "actor_id": actor_id,
            "mezan_only": True,
            "salla_updated": False,
            "qoyod_updated": False,
        })
        batch.update({
            "assignment_status": EXECUTION_STATUS,
            "execution_started_at": started_at,
            "execution_started_by": actor_id,
            **result_patch,
        })
        return {
            "ok": True,
            "idempotent": False,
            "file": preparation_execution_file_view(
                batch,
                actor_id=actor_id,
                can_override=_actor_can_override(reviewer),
            ),
            "transitioned_order_numbers": transitioned,
            "remaining_review_order_numbers": remaining_review,
            "reconciliation_required": reconciliation_required,
        }

    return router


__all__ = [
    "EXECUTION_STATUS",
    "VISIBLE_STATUSES",
    "make_mobile_preparation_execution_router",
    "preparation_execution_file_view",
]
