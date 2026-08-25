"""Atomic preparation-file metadata and safe failed-build recovery.

The reviewed product catalogue subtracts committed physical-unit allocations.
A PDF batch can be ready before registry finalisation and piece materialisation
finish. If the latter step fails, the UI must not strand the product outside
"تمت المراجعة". This module provides:

* a validated draft endpoint that stores employee and schedule metadata before
  any physical unit is committed;
* an idempotent rollback for incomplete requests;
* stale-orphan recovery for interrupted browser sessions; and
* a non-fatal piece-materialisation wrapper once the file registry is already
  finalised (the registered PDF remains authoritative and pieces can backfill).

All writes are Mezan operational writes only. No Salla, Qoyod, supplier,
WhatsApp, campaign, or accounting API is called.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from pymongo.errors import DuplicateKeyError

from order_review_routes import (
    EVENTS,
    WORKFLOWS,
    _merchant_user_id,
    _require_reviewer,
    _text,
)
from preparation_file_registry import (
    DRAFT_TTL_HOURS,
    MAX_FILE_TITLE_LENGTH,
    REGISTRY,
    _assignable_employees,
    _next_file_sequence,
    ensure_preparation_file_registry_indexes,
    normalize_file_title,
    preparation_file_number,
    preparation_file_view,
)
from preparation_piece_operations import PIECES
from reviewed_preparation_batches import BATCHES
from reviewed_products_catalog import PREPARATION_UNIT_ALLOCATIONS
from tz_utils import riyadh_now_aware


RECOVERY_GRACE_SECONDS = 120
MAX_STALE_RECOVERY_ROWS = 100
MAX_RELEASE_RECONCILIATION_EVENTS = 500
_INSTALLED = False
_ORIGINAL_FINALIZE = None


class SafePreparationFileDraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_request_id: str = Field(min_length=8, max_length=160)
    file_title: str = Field(min_length=1, max_length=MAX_FILE_TITLE_LENGTH)
    responsible_employee_id: str = Field(min_length=1, max_length=120)
    expected_quantity: int = Field(ge=1, le=1500)
    selected_product_count: int = Field(ge=1, le=200)
    schedule_mode: Literal["automatic", "required"] = "automatic"
    required_due_at: datetime | None = None

    @model_validator(mode="after")
    def validate_required_schedule(self):
        if self.schedule_mode == "required" and self.required_due_at is None:
            raise ValueError("required_due_at_required")
        if self.schedule_mode == "automatic":
            self.required_due_at = None
        return self


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _safe_draft_view(row: dict[str, Any]) -> dict[str, Any]:
    view = preparation_file_view(row)
    view.update({
        "schedule_mode": _text(row.get("schedule_mode")) or "automatic",
        "required_due_at": row.get("required_due_at"),
    })
    return view


async def create_safe_preparation_draft(
    db: Any,
    *,
    user_id: str,
    reviewer: dict[str, Any],
    payload: SafePreparationFileDraftRequest,
) -> dict[str, Any]:
    """Persist all required file metadata before batch allocation begins."""
    await ensure_preparation_file_registry_indexes(db)
    try:
        title = normalize_file_title(payload.file_title)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "code": str(exc),
                "message": "اكتب اسمًا واضحًا لملف التجهيز.",
            },
        ) from exc

    employees = await _assignable_employees(
        db,
        user_id=user_id,
        reviewer=reviewer,
    )
    employee = next(
        (
            row
            for row in employees
            if row["id"] == payload.responsible_employee_id
        ),
        None,
    )
    if not employee:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "responsible_employee_unavailable",
                "message": (
                    "اختر موظفًا مسؤولًا نشطًا يملك صلاحية إدارة التجهيز."
                ),
            },
        )

    required_due_at = _utc(payload.required_due_at)
    selector = {
        "user_id": user_id,
        "client_request_id": payload.client_request_id,
    }
    existing = await db[REGISTRY].find_one(selector, {"_id": 0})
    if existing:
        same = (
            _text(existing.get("file_title")) == title
            and _text(existing.get("responsible_employee_id"))
            == employee["id"]
            and int(existing.get("expected_quantity") or 0)
            == payload.expected_quantity
            and int(existing.get("selected_product_count") or 0)
            == payload.selected_product_count
            and (_text(existing.get("schedule_mode")) or "automatic")
            == payload.schedule_mode
            and existing.get("required_due_at") == required_due_at
        )
        if not same:
            raise HTTPException(
                status_code=409,
                detail={"code": "preparation_file_draft_conflict"},
            )
        return existing

    now = riyadh_now_aware()
    sequence = await _next_file_sequence(db, user_id)
    file_date = now.strftime("%Y-%m-%d")
    row = {
        "id": f"registry-{payload.client_request_id}",
        "user_id": user_id,
        "client_request_id": payload.client_request_id,
        "status": "draft",
        "file_number": preparation_file_number(file_date, sequence),
        "file_title": title,
        "file_date": file_date,
        "file_date_display": f"{now.year}/{now.month}/{now.day}",
        "expected_quantity": payload.expected_quantity,
        "allocated_quantity": 0,
        "selected_product_count": payload.selected_product_count,
        "order_count": 0,
        "responsible_employee_id": employee["id"],
        "responsible_employee_name": employee["name"],
        "responsible_employee_email": employee["email"],
        "schedule_mode": payload.schedule_mode,
        "required_due_at": required_due_at,
        "created_at": now,
        "created_by": _text(reviewer.get("id")),
        "created_by_name": _text(
            reviewer.get("name") or reviewer.get("email")
        ),
        "expires_at": now + timedelta(hours=DRAFT_TTL_HOURS),
        "mezan_only": True,
        "salla_updated": False,
        "qoyod_updated": False,
    }
    try:
        await db[REGISTRY].insert_one(row)
    except DuplicateKeyError:
        duplicate = await db[REGISTRY].find_one(selector, {"_id": 0})
        if duplicate:
            return duplicate
        raise
    return row


def _batch_order_numbers(
    batch: dict[str, Any] | None,
    allocations: list[dict[str, Any]],
) -> list[str]:
    values = {
        _text(value)
        for value in (batch or {}).get("order_numbers") or []
        if _text(value)
    }
    values.update(
        _text(row.get("order_number"))
        for row in (batch or {}).get("lines") or []
        if isinstance(row, dict) and _text(row.get("order_number"))
    )
    values.update(
        _text(row.get("order_number"))
        for row in allocations
        if _text(row.get("order_number"))
    )
    return sorted(values)


async def release_incomplete_preparation_request(
    db: Any,
    *,
    user_id: str,
    client_request_id: str,
    actor: dict[str, Any],
    reason: str,
) -> dict[str, Any]:
    """Release units only when the registry never became authoritative."""
    selector = {
        "user_id": user_id,
        "client_request_id": client_request_id,
    }
    registry = await db[REGISTRY].find_one(selector, {"_id": 0})
    if registry and _text(registry.get("status")) == "ready":
        return {
            "ok": True,
            "released": False,
            "status": "already_finalized",
            "file_number": _text(registry.get("file_number")),
        }

    batch = await db[BATCHES].find_one(selector, {"_id": 0})
    batch_id = _text((batch or {}).get("id"))
    if not registry and not batch:
        return {
            "ok": True,
            "released": False,
            "status": "already_absent",
        }

    if batch_id:
        started_piece = await db[PIECES].find_one(
            {
                "user_id": user_id,
                "batch_id": batch_id,
                "$or": [
                    {"started_at": {"$ne": None}},
                    {"execution_status": {"$nin": [None, "not_started", "assigned"]}},
                ],
            },
            {"_id": 0, "piece_id": 1},
        )
        if started_piece or _text((batch or {}).get("execution_status")) == "in_progress":
            raise HTTPException(
                status_code=409,
                detail={"code": "started_preparation_file_cannot_be_released"},
            )

    allocations = (
        await db[PREPARATION_UNIT_ALLOCATIONS].find(
            {
                "user_id": user_id,
                "batch_id": batch_id,
                "status": {"$in": ["reserved", "committed"]},
            },
            {"_id": 0, "order_number": 1},
        ).to_list(100000)
        if batch_id
        else []
    )
    order_numbers = _batch_order_numbers(batch, allocations)

    if batch_id:
        await db[PREPARATION_UNIT_ALLOCATIONS].delete_many({
            "user_id": user_id,
            "batch_id": batch_id,
            "status": {"$in": ["reserved", "committed"]},
        })
        await db[PIECES].delete_many({
            "user_id": user_id,
            "batch_id": batch_id,
            "started_at": {"$in": [None, ""]},
        })
        await db[BATCHES].delete_one({
            "user_id": user_id,
            "id": batch_id,
            "execution_status": {"$ne": "in_progress"},
        })
    await db[REGISTRY].delete_one({
        **selector,
        "status": {"$ne": "ready"},
    })

    # Recalculate assignment progress after releasing the ledger rows. The
    # active function is patched by preparation_piece_operations, so reviewed
    # orders remain reviewed and receive accurate remaining quantities.
    import reviewed_preparation_batches as batch_module

    for order_number in order_numbers:
        try:
            await batch_module._reconcile_order_stage(
                db,
                user_id=user_id,
                order_number=order_number,
                batch_id="",
                actor=actor,
            )
        except Exception:
            # Catalogue visibility is governed by the allocation ledger and is
            # already restored. A later catalogue load can refresh progress.
            continue

    await db[EVENTS].insert_one({
        "id": uuid.uuid4().hex,
        "user_id": user_id,
        "batch_id": batch_id or None,
        "client_request_id": client_request_id,
        "event_type": "failed_preparation_request_released",
        "order_numbers": order_numbers,
        "released_unit_count": len(allocations),
        "reason": _text(reason) or "incomplete_file_creation",
        "actor_id": _text(actor.get("id")),
        "occurred_at": datetime.now(timezone.utc),
        "mezan_only": True,
        "salla_updated": False,
        "qoyod_updated": False,
    })
    return {
        "ok": True,
        "released": True,
        "status": "released",
        "batch_id": batch_id or None,
        "released_unit_count": len(allocations),
        "order_numbers": order_numbers,
    }


async def recover_stale_preparation_requests(
    db: Any,
    *,
    user_id: str,
    actor: dict[str, Any],
) -> dict[str, Any]:
    """Recover interrupted draft/batch pairs after a short concurrency grace."""
    threshold = datetime.now(timezone.utc) - timedelta(
        seconds=RECOVERY_GRACE_SECONDS
    )
    drafts = await db[REGISTRY].find(
        {
            "user_id": user_id,
            "status": "draft",
            "created_at": {"$lte": threshold},
        },
        {"_id": 0, "client_request_id": 1},
    ).sort("created_at", 1).limit(MAX_STALE_RECOVERY_ROWS).to_list(
        MAX_STALE_RECOVERY_ROWS
    )
    batches = await db[BATCHES].find(
        {
            "user_id": user_id,
            "status": {"$in": ["building", "ready"]},
            "created_at": {"$lte": threshold},
            "execution_status": {"$ne": "in_progress"},
        },
        {"_id": 0, "client_request_id": 1},
    ).sort("created_at", 1).limit(MAX_STALE_RECOVERY_ROWS).to_list(
        MAX_STALE_RECOVERY_ROWS
    )
    request_ids = {
        _text(row.get("client_request_id"))
        for row in [*drafts, *batches]
        if _text(row.get("client_request_id"))
    }
    recovered = []
    skipped = []
    for request_id in sorted(request_ids):
        try:
            result = await release_incomplete_preparation_request(
                db,
                user_id=user_id,
                client_request_id=request_id,
                actor=actor,
                reason="stale_incomplete_file_creation",
            )
            if result.get("released"):
                recovered.append(result)
            else:
                skipped.append(result)
        except HTTPException as exc:
            skipped.append({
                "client_request_id": request_id,
                "status": "not_recoverable",
                "code": (exc.detail or {}).get("code")
                if isinstance(exc.detail, dict)
                else "not_recoverable",
            })
    orphan_recovery = await release_orphan_ready_batches(
        db,
        user_id=user_id,
        actor=actor,
        threshold=threshold,
    )
    stage_repair = await reconcile_released_preparation_stages(
        db,
        user_id=user_id,
        actor=actor,
    )
    return {
        "ok": True,
        "recovered_count": len(recovered),
        "recovered": recovered,
        "skipped": skipped,
        "orphan_batch_count": orphan_recovery["released_count"],
        "orphan_released_unit_count": orphan_recovery["released_unit_count"],
        "orphan_batches": orphan_recovery["released"],
        "restored_order_count": stage_repair["restored_order_count"],
        "restored_order_numbers": stage_repair["restored_order_numbers"],
    }


async def reconcile_released_preparation_stages(
    db: Any,
    *,
    user_id: str,
    actor: dict[str, Any],
) -> dict[str, Any]:
    """Replay released-file events so legacy stranded stages self-heal.

    Older recovery removed the failed allocation ledger correctly but treated
    ``in_progress`` as permanent completion evidence.  The event retains the
    exact affected order numbers, allowing the corrected reconciler to return
    only orders that still have uncovered units to ``reviewed``.  Orders fully
    covered by another valid file remain in progress.
    """
    rows = await db[EVENTS].find(
        {
            "user_id": user_id,
            "event_type": "failed_preparation_request_released",
        },
        {"_id": 0, "order_numbers": 1},
    ).sort("occurred_at", -1).limit(MAX_RELEASE_RECONCILIATION_EVENTS).to_list(
        MAX_RELEASE_RECONCILIATION_EVENTS
    )
    order_numbers = sorted({
        _text(order_number)
        for row in rows
        for order_number in (row.get("order_numbers") or [])
        if _text(order_number)
    })
    # Some legacy failures happened after the workflow was moved to
    # ``in_progress`` but before the release event itself was persisted.  Such
    # orders have no event to replay and otherwise remain invisible forever.
    # Reconcile every in-progress workflow as a safe fallback: the canonical
    # allocation ledger keeps fully covered orders in progress, while only
    # genuinely uncovered units are returned to reviewed.
    stranded_candidates = await db[WORKFLOWS].find(
        {"user_id": user_id, "stage": "in_progress"},
        {"_id": 0, "order_number": 1},
    ).limit(MAX_RELEASE_RECONCILIATION_EVENTS).to_list(
        MAX_RELEASE_RECONCILIATION_EVENTS
    )
    order_numbers = sorted({
        *order_numbers,
        *(
            _text(row.get("order_number"))
            for row in stranded_candidates
            if _text(row.get("order_number"))
        ),
    })
    import reviewed_preparation_batches as batch_module

    restored: list[str] = []
    failed: list[str] = []
    for order_number in order_numbers:
        before = await db[WORKFLOWS].find_one(
            {"user_id": user_id, "order_number": order_number},
            {"_id": 0, "stage": 1},
        )
        try:
            complete, remaining = await batch_module._reconcile_order_stage(
                db,
                user_id=user_id,
                order_number=order_number,
                batch_id="",
                actor=actor,
            )
            if (
                _text((before or {}).get("stage")) == "in_progress"
                and not complete
                and remaining > 0
            ):
                restored.append(order_number)
        except Exception:
            failed.append(order_number)
    return {
        "restored_order_count": len(restored),
        "restored_order_numbers": restored,
        "failed_order_numbers": failed,
    }


async def release_orphan_ready_batches(
    db: Any,
    *,
    user_id: str,
    actor: dict[str, Any],
    threshold: datetime,
) -> dict[str, Any]:
    """Release finalized allocations that never gained a file registry row.

    A network interruption can finish the batch and commit its unit ledger,
    then fail before the authoritative numbered-file registry is created.
    Only old batches with no ready registry row and no physically started
    piece are eligible; visible files and begun employee work are untouched.
    """
    candidates = await db[BATCHES].find(
        {
            "user_id": user_id,
            "status": "ready",
            "created_at": {"$lte": threshold},
        },
        {"_id": 0},
    ).sort("created_at", 1).limit(MAX_STALE_RECOVERY_ROWS).to_list(
        MAX_STALE_RECOVERY_ROWS
    )
    released: list[dict[str, Any]] = []
    for batch in candidates:
        batch_id = _text(batch.get("id"))
        if not batch_id:
            continue
        registry = await db[REGISTRY].find_one(
            {"user_id": user_id, "batch_id": batch_id, "status": "ready"},
            {"_id": 0, "file_number": 1},
        )
        if registry:
            continue
        started_piece = await db[PIECES].find_one(
            {
                "user_id": user_id,
                "batch_id": batch_id,
                "$or": [
                    {"started_at": {"$nin": [None, ""]}},
                    {"execution_status": {"$nin": [None, "not_started", "assigned"]}},
                ],
            },
            {"_id": 0, "piece_id": 1},
        )
        if started_piece:
            continue
        allocations = await db[PREPARATION_UNIT_ALLOCATIONS].find(
            {
                "user_id": user_id,
                "batch_id": batch_id,
                "status": {"$in": ["reserved", "committed"]},
            },
            {"_id": 0, "order_number": 1},
        ).to_list(100000)
        order_numbers = _batch_order_numbers(batch, allocations)
        await db[PREPARATION_UNIT_ALLOCATIONS].delete_many({
            "user_id": user_id,
            "batch_id": batch_id,
            "status": {"$in": ["reserved", "committed"]},
        })
        await db[PIECES].delete_many({
            "user_id": user_id,
            "batch_id": batch_id,
            "started_at": {"$in": [None, ""]},
        })
        await db[BATCHES].delete_one({
            "user_id": user_id,
            "id": batch_id,
            "status": "ready",
        })
        event = {
            "id": uuid.uuid4().hex,
            "user_id": user_id,
            "batch_id": batch_id,
            "client_request_id": _text(batch.get("client_request_id")) or None,
            "event_type": "orphan_ready_batch_released",
            "order_numbers": order_numbers,
            "released_unit_count": len(allocations),
            "actor_id": _text(actor.get("id")),
            "occurred_at": datetime.now(timezone.utc),
            "mezan_only": True,
            "salla_updated": False,
            "qoyod_updated": False,
        }
        await db[EVENTS].insert_one(event)
        released.append({
            "batch_id": batch_id,
            "released_unit_count": len(allocations),
            "order_numbers": order_numbers,
        })
    released_batch_ids = {row["batch_id"] for row in released}
    committed_rows = await db[PREPARATION_UNIT_ALLOCATIONS].find(
        {
            "user_id": user_id,
            "status": "committed",
            "committed_at": {"$lte": threshold},
        },
        {"_id": 0, "batch_id": 1, "order_number": 1},
    ).limit(100000).to_list(100000)
    allocations_by_batch: dict[str, list[dict[str, Any]]] = {}
    for row in committed_rows:
        batch_id = _text(row.get("batch_id"))
        if batch_id and batch_id not in released_batch_ids:
            allocations_by_batch.setdefault(batch_id, []).append(row)
    for batch_id, allocations in allocations_by_batch.items():
        batch = await db[BATCHES].find_one(
            {"user_id": user_id, "id": batch_id},
            {"_id": 0},
        )
        # A ready batch is authoritative here. Missing-registry ready batches
        # were handled above; never infer that a visible file is orphaned.
        if batch and _text(batch.get("status")) == "ready":
            continue
        started_piece = await db[PIECES].find_one(
            {
                "user_id": user_id,
                "batch_id": batch_id,
                "$or": [
                    {"started_at": {"$nin": [None, ""]}},
                    {"execution_status": {"$nin": [None, "not_started", "assigned"]}},
                ],
            },
            {"_id": 0, "piece_id": 1},
        )
        if started_piece:
            continue
        order_numbers = _batch_order_numbers(batch, allocations)
        await db[PREPARATION_UNIT_ALLOCATIONS].delete_many({
            "user_id": user_id,
            "batch_id": batch_id,
            "status": "committed",
        })
        await db[PIECES].delete_many({
            "user_id": user_id,
            "batch_id": batch_id,
            "started_at": {"$in": [None, ""]},
        })
        if batch:
            await db[BATCHES].delete_one({
                "user_id": user_id,
                "id": batch_id,
                "status": {"$ne": "ready"},
            })
        await db[EVENTS].insert_one({
            "id": uuid.uuid4().hex,
            "user_id": user_id,
            "batch_id": batch_id,
            "event_type": "orphan_committed_allocation_released",
            "order_numbers": order_numbers,
            "released_unit_count": len(allocations),
            "actor_id": _text(actor.get("id")),
            "occurred_at": datetime.now(timezone.utc),
            "mezan_only": True,
            "salla_updated": False,
            "qoyod_updated": False,
        })
        released.append({
            "batch_id": batch_id,
            "released_unit_count": len(allocations),
            "order_numbers": order_numbers,
        })
    return {
        "released_count": len(released),
        "released_unit_count": sum(row["released_unit_count"] for row in released),
        "released": released,
    }


def install_preparation_finalize_safety() -> None:
    """Do not report a registered PDF as failed solely on piece backfill."""
    global _INSTALLED, _ORIGINAL_FINALIZE
    if _INSTALLED:
        return
    import preparation_file_registry as registry_module

    _ORIGINAL_FINALIZE = registry_module._finalize_registry_row

    async def finalize_with_nonfatal_piece_backfill(
        db: Any,
        *,
        user_id: str,
        client_request_id: str,
        actor: dict[str, Any],
    ) -> dict[str, Any]:
        assert _ORIGINAL_FINALIZE is not None
        try:
            return await _ORIGINAL_FINALIZE(
                db,
                user_id=user_id,
                client_request_id=client_request_id,
                actor=actor,
            )
        except Exception as exc:
            registry = await db[REGISTRY].find_one(
                {
                    "user_id": user_id,
                    "client_request_id": client_request_id,
                },
                {"_id": 0},
            )
            if not registry or _text(registry.get("status")) != "ready":
                raise
            now = datetime.now(timezone.utc)
            warning = {
                "piece_registry_status": "recovery_required",
                "piece_registry_last_error": type(exc).__name__,
                "piece_registry_last_error_at": now,
                "updated_at": now,
            }
            await db[REGISTRY].update_one(
                {
                    "user_id": user_id,
                    "client_request_id": client_request_id,
                    "status": "ready",
                },
                {"$set": warning},
            )
            batch_id = _text(registry.get("batch_id"))
            if batch_id:
                await db[BATCHES].update_one(
                    {"user_id": user_id, "id": batch_id},
                    {"$set": warning},
                )
            registry.update(warning)
            return registry

    registry_module._finalize_registry_row = (
        finalize_with_nonfatal_piece_backfill
    )
    _INSTALLED = True


def make_preparation_file_failure_safety_router(
    db: Any,
    current_user: Callable,
) -> APIRouter:
    router = APIRouter(
        prefix="/preparation-file-safety-v1",
        tags=["Preparation File Safety"],
    )

    @router.post("/drafts")
    async def create_draft(
        payload: SafePreparationFileDraftRequest,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        reviewer = _require_reviewer(user)
        row = await create_safe_preparation_draft(
            db,
            user_id=_merchant_user_id(reviewer),
            reviewer=reviewer,
            payload=payload,
        )
        return _safe_draft_view(row)

    @router.post("/requests/{client_request_id}/release")
    async def release_failed_request(
        client_request_id: str,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        reviewer = _require_reviewer(user)
        return await release_incomplete_preparation_request(
            db,
            user_id=_merchant_user_id(reviewer),
            client_request_id=client_request_id,
            actor=reviewer,
            reason="client_observed_file_creation_failure",
        )

    @router.post("/recover-stale")
    async def recover_stale(
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        reviewer = _require_reviewer(user)
        return await recover_stale_preparation_requests(
            db,
            user_id=_merchant_user_id(reviewer),
            actor=reviewer,
        )

    return router


__all__ = [
    "RECOVERY_GRACE_SECONDS",
    "SafePreparationFileDraftRequest",
    "create_safe_preparation_draft",
    "install_preparation_finalize_safety",
    "make_preparation_file_failure_safety_router",
    "recover_stale_preparation_requests",
    "reconcile_released_preparation_stages",
    "release_orphan_ready_batches",
    "release_incomplete_preparation_request",
]
