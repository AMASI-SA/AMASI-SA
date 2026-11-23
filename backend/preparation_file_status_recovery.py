"""Explicit status continuation for saved files; never invoked by printing or assignment."""
from __future__ import annotations

import hashlib
import re
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from order_review_routes import WORKFLOWS, _merchant_user_id, _require_reviewer, _text
from preparation_status_result import preparation_status_sync_view
from reviewed_preparation_batches import BATCHES, MAX_BATCH_SELECTIONS, _now_iso

MAX_STATUS_RECOVERY_ORDERS = MAX_BATCH_SELECTIONS


class PreparationStatusRecoveryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    order_number: str | None = Field(default=None, min_length=1, max_length=80)


async def _reconcile_order_stage(db, **kwargs):
    from preparation_status_continuation import reconcile_saved_assignment
    return await reconcile_saved_assignment(db, **kwargs)


_STATUS_SYNC_REASONS = frozenset({
    "preparation_assignment_source_not_eligible", "local_review_preparation_not_eligible",
    "preparation_status_scope_changed", "preparation_routing_incomplete",
    "preparation_routing_not_proven", "direct_preparation_routing_not_proven",
    "salla_status_writes_disabled", "salla_current_status_not_eligible",
    "salla_in_progress_read_failed", "in_progress_status_not_found",
    "salla_in_progress_confirmation_required", "salla_in_progress_not_confirmed",
    "missing_salla_order_id", "review_completion_mode_unknown",
    "preparation_status_readback_missing", "preparation_status_not_confirmed",
})


def _status_sync_reason(failure: Exception) -> str:
    """Persist only known codes, never provider bodies, credentials or tracebacks."""
    if isinstance(failure, HTTPException) and isinstance(failure.detail, dict):
        code = failure.detail.get("code")
    elif isinstance(failure, RuntimeError) and str(failure).startswith("salla_in_progress_status_sync_failed:"):
        code = str(failure).split(":", 1)[1]
    else:
        code = ""
    if isinstance(code, str) and (code in _STATUS_SYNC_REASONS or re.fullmatch(r"salla_(?:verify_)?[1-5][0-9]{2}", code)):
        return code
    return "preparation_status_check_failed"


async def reconcile_file_orders(
    db: Any,
    *,
    user_id: str,
    batch: dict[str, Any],
    actor: dict[str, Any],
    local_only: bool = True,
    authorize: Callable | None = None,
    selected_order_numbers: list[str] | None = None,
) -> dict[str, Any]:
    """Reconcile status separately from the already committed physical file.

    Historical unmarked results are not success. Every order has an explicit
    outcome, including a source guard that cannot currently prove eligibility.
    The explicit recovery endpoint permits first dispatch only for contracts
    with the permanent dispatch fence; it cannot replay a legacy provider POST.
    """
    from preparation_file_registry import REGISTRY
    from review_local_policy import LOCAL_COMPLETION_MODE

    order_numbers = sorted({
        _text(row.get("order_number"))
        for row in batch.get("lines") or []
        if _text(row.get("order_number"))
    })
    if selected_order_numbers is not None and not set(selected_order_numbers).issubset(order_numbers):
        raise HTTPException(409, detail={"code": "preparation_status_order_not_in_file"})
    batch_id = _text(batch.get("id"))
    batch_selector = {"user_id": user_id, "id": batch_id, "status": "ready"}
    registry_selector = {"user_id": user_id, "batch_id": batch_id, "status": "ready"}
    # Record unprocessed orders before the first external await. A disconnect
    # or process stop cannot erase the fact that status delivery is unfinished.
    await db[BATCHES].update_one(batch_selector, {"$set": {"status_sync_order_numbers": order_numbers}})
    await db[REGISTRY].update_one(registry_selector, {"$set": {"status_sync_order_numbers": order_numbers}})
    for order_number in selected_order_numbers if selected_order_numbers is not None else order_numbers:
        outcome = {"order_number": order_number, "state": "pending",
                   "reason": "preparation_status_not_confirmed"}
        try:
            if authorize is not None:
                await authorize(db)
            if local_only:
                workflow = await db[WORKFLOWS].find_one(
                    {"user_id": user_id, "order_number": order_number},
                    {"_id": 0, "completion_mode": 1},
                ) or {}
                if workflow.get("completion_mode") != LOCAL_COMPLETION_MODE:
                    outcome["reason"] = "legacy_assignment_status_requires_verification"
                    key = hashlib.sha256(order_number.encode("utf-8")).hexdigest()
                    patch = {f"status_sync_results.{key}": {**outcome, "checked_at": _now_iso()}}
                    await db[BATCHES].update_one(batch_selector, {"$set": patch})
                    await db[REGISTRY].update_one(registry_selector, {"$set": patch})
                    continue
            complete, remaining = await _reconcile_order_stage(
                db,
                user_id=user_id,
                order_number=order_number,
                batch_id=_text(batch.get("id")),
                actor=actor,
                **({"authorize": authorize} if authorize is not None else {}),
            )
            if complete:
                outcome = {"order_number": order_number, "state": "confirmed"}
            elif remaining > 0:
                outcome = {"order_number": order_number, "state": "partial", "remaining_quantity": remaining}
        except Exception as failure:
            if authorize is not None and isinstance(failure, HTTPException) and failure.status_code in {401, 403}:
                raise
            outcome["reason"] = _status_sync_reason(failure)
        key = hashlib.sha256(order_number.encode("utf-8")).hexdigest()
        patch = {f"status_sync_results.{key}": {**outcome, "checked_at": _now_iso()}}
        # Checkpoint each order, not only the end of the whole file. Distinct
        # order keys also preserve concurrent continuations of the same file.
        await db[BATCHES].update_one(batch_selector, {"$set": patch})
        await db[REGISTRY].update_one(registry_selector, {"$set": patch})
    if authorize is not None:
        await authorize(db)
    saved = await db[BATCHES].find_one(batch_selector, {"_id": 0}) or {}
    summary = preparation_status_sync_view(saved) or {"state": "pending", "confirmed_count": 0,
        "partial_count": 0, "pending_count": len(order_numbers), "orders": [], "checked_at": None}
    result = {
        "transitioned_order_numbers": [item["order_number"] for item in summary["orders"] if item["state"] == "confirmed"],
        "remaining_review_order_numbers": [item["order_number"] for item in summary["orders"] if item["state"] == "partial"],
        "reconciliation_required": [item["order_number"] for item in summary["orders"] if item["state"] == "pending"],
        "order_status_sync": summary,
    }
    # Keep the result visible even when the assignment receipt is immutable.
    # Neither update touches allocation, PDF, piece, employee or review identity.
    await db[BATCHES].update_one(
        batch_selector,
        {"$set": result},
    )
    await db[REGISTRY].update_one(
        registry_selector,
        {"$set": {"order_status_sync": summary}},
    )
    return result



def register_preparation_status_recovery_routes(
    router: APIRouter, db: Any, current_user: Callable,
) -> None:

    async def recovery_batch(batch_id: str, user_id: str):
        from preparation_file_registry import REGISTRY
        batch = await db[BATCHES].find_one(
            {"user_id": user_id, "id": batch_id, "status": "ready"}, {"_id": 0},
        )
        if not batch:
            raise HTTPException(404, detail={"code": "preparation_batch_not_found"})
        registry = await db[REGISTRY].find_one(
            {"user_id": user_id, "batch_id": batch_id, "status": "ready"}, {"_id": 0},
        ) or {}
        if not _text(registry.get("responsible_employee_id")) or not batch.get("lines"):
            raise HTTPException(409, detail={"code": "preparation_employee_assignment_incomplete"})
        numbers = sorted({_text(line.get("order_number")) for line in batch["lines"]
                          if _text(line.get("order_number"))})
        if not numbers or len(numbers) > MAX_STATUS_RECOVERY_ORDERS:
            raise HTTPException(409, detail={"code": "preparation_status_recovery_too_large"})
        return batch, numbers

    @router.get("/batches/{batch_id}/status-plan")
    async def status_plan(batch_id: str, user: dict = Depends(current_user)) -> dict[str, Any]:
        reviewer = _require_reviewer(user)
        batch, numbers = await recovery_batch(batch_id, _merchant_user_id(reviewer))
        # Pure metadata read. The browser starts one explicit POST per order;
        # no single HTTP request waits for every provider call in a large file.
        return {"batch_id": batch_id, "order_numbers": numbers,
                "order_status_sync": preparation_status_sync_view(batch)}

    @router.post("/batches/{batch_id}/reconcile-status")
    async def reconcile_status(batch_id: str, payload: PreparationStatusRecoveryRequest,
                               user: dict = Depends(current_user)) -> dict[str, Any]:
        if user.get("_web_workspace_read"):
            raise HTTPException(403, detail={"code": "order_review_permission_required"})
        reviewer = _require_reviewer(user)
        user_id = _merchant_user_id(reviewer)
        batch, numbers = await recovery_batch(batch_id, user_id)
        number = payload.order_number or (numbers[0] if len(numbers) == 1 else None)
        if number not in numbers:
            raise HTTPException(422, detail={"code": "preparation_status_order_not_in_file"})

        async def authorize(scoped):
            account = await scoped.users.find_one({"id": _text(reviewer.get("id"))}, {"_id": 0})
            if (not account or account.get("disabled") is True or account.get("is_active") is False
                    or account.get("deleted_at")):
                raise HTTPException(403, detail={"code": "order_review_permission_required"})
            current = _require_reviewer(account)
            if _merchant_user_id(current) != user_id:
                raise HTTPException(403, detail={"code": "order_review_permission_required"})

        await authorize(db)
        result = await reconcile_file_orders(db, user_id=user_id, batch=batch,
            actor=reviewer, local_only=True, authorize=authorize, selected_order_numbers=[number])
        return {"ok": True, "batch_id": batch_id, **result}

