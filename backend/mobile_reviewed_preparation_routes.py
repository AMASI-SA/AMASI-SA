"""Governed mobile workflow for reviewed-product preparation files.

This route keeps the reviewed stage as a planning and assignment stage:
creating a PDF allocates the selected units, assigns the whole file to one
employee, records a required Riyadh completion time, and produces a printable
PDF. It deliberately does not move orders to ``in_progress``; that transition
belongs to the employee's later explicit "start execution" action.
"""
from __future__ import annotations

import hashlib
import io
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from urllib.parse import quote
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from pymongo.errors import BulkWriteError, DuplicateKeyError

import reviewed_preparation_batches as batch_module
import reviewed_products_catalog as catalog_module
from order_review_routes import EVENTS, WORKFLOWS, _merchant_user_id, _require_reviewer, _text
from preparation_file_registry import (
    DRAFT_TTL_HOURS,
    REGISTRY,
    _assignable_employees,
    _next_file_sequence,
    ensure_preparation_file_registry_indexes,
    normalize_file_title,
    preparation_file_name,
    preparation_file_number,
)
from preparation_pdf import generate_preparation_pdf
from reviewed_products_catalog import PREPARATION_UNIT_ALLOCATIONS
from tz_utils import riyadh_now_aware


RIYADH = ZoneInfo("Asia/Riyadh")
PRINT_LINK_TTL_MINUTES = 10
ASSIGNMENT_STATUS = "assigned_not_started"


class MobilePreparationSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    group_key: str = Field(min_length=1, max_length=500)
    quantity: int = Field(ge=1, le=batch_module.MAX_BATCH_UNITS)


class CreateMobilePreparationFileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_request_id: str = Field(min_length=8, max_length=160)
    file_title: str = Field(min_length=1, max_length=120)
    responsible_employee_id: str = Field(min_length=1, max_length=120)
    required_completion_at: str = Field(min_length=16, max_length=80)
    selections: list[MobilePreparationSelection] = Field(
        min_length=1,
        max_length=batch_module.MAX_BATCH_SELECTIONS,
    )

    @field_validator("selections")
    @classmethod
    def validate_selections(
        cls,
        values: list[MobilePreparationSelection],
    ) -> list[MobilePreparationSelection]:
        keys = [row.group_key.strip() for row in values]
        if len(keys) != len(set(keys)):
            raise ValueError("duplicate_product_group")
        if sum(row.quantity for row in values) > batch_module.MAX_BATCH_UNITS:
            raise ValueError("batch_unit_limit_exceeded")
        return values



def _now() -> datetime:
    return datetime.now(timezone.utc)



def _parse_required_completion_at(value: str) -> tuple[datetime, datetime, str]:
    raw = _text(value)
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "invalid_required_completion_at",
                "message": "أدخل تاريخ ووقت الإنجاز بصيغة صحيحة.",
            },
        ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=RIYADH)
    riyadh_value = parsed.astimezone(RIYADH)
    utc_value = riyadh_value.astimezone(timezone.utc)
    if utc_value <= _now():
        raise HTTPException(
            status_code=422,
            detail={
                "code": "required_completion_at_must_be_future",
                "message": "وقت الإنجاز المطلوب يجب أن يكون في المستقبل بتوقيت الرياض.",
            },
        )
    display = riyadh_value.strftime("%Y/%m/%d %I:%M %p")
    return utc_value, riyadh_value, display


async def _employee_for_assignment(
    db: Any,
    *,
    user_id: str,
    reviewer: dict[str, Any],
    employee_id: str,
) -> dict[str, Any]:
    rows = await _assignable_employees(
        db,
        user_id=user_id,
        reviewer=reviewer,
    )
    employee = next((row for row in rows if row["id"] == employee_id), None)
    if not employee:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "responsible_employee_unavailable",
                "message": "اختر موظفًا مسؤولًا نشطًا يملك صلاحية إدارة التجهيز.",
            },
        )
    return employee


async def _rollback_build(db: Any, *, user_id: str, batch_id: str, request_id: str) -> None:
    await db[PREPARATION_UNIT_ALLOCATIONS].delete_many({
        "user_id": user_id,
        "batch_id": batch_id,
    })
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


async def _record_planning_assignments(
    db: Any,
    *,
    user_id: str,
    batch: dict[str, Any],
    registry: dict[str, Any],
    actor: dict[str, Any],
) -> None:
    now = _now().isoformat()
    actor_id = _text(actor.get("id"))
    order_numbers = sorted({
        _text(row.get("order_number"))
        for row in batch.get("lines") or []
        if _text(row.get("order_number"))
    })
    for order_number in order_numbers:
        assignment = {
            "batch_id": _text(batch.get("id")),
            "file_number": _text(registry.get("file_number")),
            "file_title": _text(registry.get("file_title")),
            "status": ASSIGNMENT_STATUS,
            "responsible_employee_id": _text(registry.get("responsible_employee_id")),
            "responsible_employee_name": _text(registry.get("responsible_employee_name")),
            "required_completion_at": registry.get("required_completion_at"),
            "required_completion_at_riyadh": registry.get("required_completion_at_riyadh"),
            "assigned_at": now,
            "assigned_by": actor_id,
        }
        await db[WORKFLOWS].update_one(
            {
                "user_id": user_id,
                "order_number": order_number,
                "stage": "reviewed",
            },
            {
                "$addToSet": {
                    "preparation_batch_ids": _text(batch.get("id")),
                    "preparation_assignments": assignment,
                },
                "$set": {
                    "last_preparation_assignment": assignment,
                    "updated_at": now,
                    "updated_by": actor_id,
                },
                "$inc": {"revision": 1},
            },
        )
        await db[EVENTS].insert_one({
            "user_id": user_id,
            "order_number": order_number,
            "batch_id": _text(batch.get("id")),
            "file_number": _text(registry.get("file_number")),
            "event_type": "preparation_file_assigned_not_started",
            "responsible_employee_id": _text(registry.get("responsible_employee_id")),
            "required_completion_at": registry.get("required_completion_at"),
            "occurred_at": now,
            "actor_id": actor_id,
            "mezan_only": True,
            "salla_updated": False,
            "qoyod_updated": False,
        })



def _file_response(batch: dict[str, Any], registry: dict[str, Any]) -> dict[str, Any]:
    return {
        "ok": _text(batch.get("status")) == "ready",
        "batch_id": _text(batch.get("id")),
        "file_number": _text(registry.get("file_number")),
        "file_title": _text(registry.get("file_title")),
        "file_name": _text(registry.get("file_name") or batch.get("file_name")),
        "status": ASSIGNMENT_STATUS,
        "selected_product_count": int(batch.get("selected_product_count") or 0),
        "allocated_quantity": int(batch.get("allocated_quantity") or 0),
        "order_count": int(batch.get("order_count") or 0),
        "responsible_employee_id": _text(registry.get("responsible_employee_id")),
        "responsible_employee_name": _text(registry.get("responsible_employee_name")),
        "required_completion_at": registry.get("required_completion_at"),
        "required_completion_at_riyadh": registry.get("required_completion_at_riyadh"),
        "required_completion_display": _text(registry.get("required_completion_display")),
        "moved_to_in_progress": False,
        "mezan_only": True,
        "salla_updated": False,
        "qoyod_updated": False,
    }



def make_mobile_reviewed_preparation_router(
    db: Any,
    current_user: Callable,
) -> APIRouter:
    router = APIRouter(
        prefix="/mobile-reviewed-preparation-v1",
        tags=["Mobile Reviewed Preparation"],
    )

    @router.get("/employees")
    async def list_employees(
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        reviewer = _require_reviewer(user)
        user_id = _merchant_user_id(reviewer)
        rows = await _assignable_employees(
            db,
            user_id=user_id,
            reviewer=reviewer,
        )
        return {"items": rows}

    @router.post("/files")
    async def create_file(
        payload: CreateMobilePreparationFileRequest,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        reviewer = _require_reviewer(user)
        user_id = _merchant_user_id(reviewer)
        await batch_module.ensure_preparation_batch_indexes(db)
        await ensure_preparation_file_registry_indexes(db)
        await batch_module._cleanup_stale_builds(db, user_id)

        title = normalize_file_title(payload.file_title)
        due_utc, due_riyadh, due_display = _parse_required_completion_at(
            payload.required_completion_at,
        )
        employee = await _employee_for_assignment(
            db,
            user_id=user_id,
            reviewer=reviewer,
            employee_id=payload.responsible_employee_id,
        )

        existing_batch = await db[batch_module.BATCHES].find_one(
            {
                "user_id": user_id,
                "client_request_id": payload.client_request_id,
                "status": "ready",
            },
            {"_id": 0},
        )
        if existing_batch:
            existing_registry = await db[REGISTRY].find_one(
                {
                    "user_id": user_id,
                    "client_request_id": payload.client_request_id,
                    "status": "ready",
                },
                {"_id": 0},
            ) or {}
            return _file_response(existing_batch, existing_registry)

        context = await catalog_module.load_reviewed_product_context(
            db,
            user_id=user_id,
            limit=catalog_module.MAX_REVIEWED_ORDERS,
        )
        if context.get("truncated"):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "reviewed_catalog_truncated",
                    "message": "القائمة ناقصة؛ لا يمكن إنشاء ملف تجهيز منها.",
                },
            )
        selection_rows = [row.model_dump() for row in payload.selections]
        try:
            planned = batch_module.plan_and_validate_preparation_allocations(
                (context.get("catalog") or {}).get("products") or [],
                selection_rows,
                context.get("allocation_documents") or [],
            )
        except ValueError as exc:
            code = str(exc)
            labels = {
                "reviewed_product_not_available": "المنتج لم يعد متاحًا في تمت المراجعة.",
                "preparation_quantity_exceeds_remaining": "الكمية أكبر من المتبقي.",
                "reviewed_product_allocation_incomplete": "تعذّر توزيع الكمية على الطلبات.",
                "duplicate_product_group": "لا يمكن تكرار المنتج نفسه.",
                "preparation_identity_ambiguous": (
                    "تعارضت هويات القطعة؛ لم يتم إنشاء ملف حتى تُحسم الهوية المرجعية."
                ),
            }
            raise HTTPException(
                status_code=409,
                detail={"code": code, "message": labels.get(code, "اختيار المنتجات غير صالح.")},
            ) from exc

        now = _now()
        riyadh_now = riyadh_now_aware()
        sequence = await _next_file_sequence(db, user_id)
        file_date = riyadh_now.strftime("%Y-%m-%d")
        file_number = preparation_file_number(file_date, sequence)
        expected_quantity = sum(int(row.quantity) for row in payload.selections)
        selected_product_count = len(payload.selections)
        file_name = preparation_file_name(title, file_date, expected_quantity)
        batch_id = uuid.uuid4().hex
        pdf_title = (
            f"{title} — المسؤول: {employee['name']} — "
            f"الإنجاز: {due_display}"
        )
        registry = {
            "id": f"registry-{payload.client_request_id}",
            "user_id": user_id,
            "client_request_id": payload.client_request_id,
            "status": "building",
            "file_number": file_number,
            "file_title": title,
            "file_name": file_name,
            "file_date": file_date,
            "file_date_display": f"{riyadh_now.year}/{riyadh_now.month}/{riyadh_now.day}",
            "expected_quantity": expected_quantity,
            "allocated_quantity": 0,
            "selected_product_count": selected_product_count,
            "order_count": 0,
            "responsible_employee_id": employee["id"],
            "responsible_employee_name": employee["name"],
            "responsible_employee_email": employee["email"],
            "required_completion_at": due_utc,
            "required_completion_at_riyadh": due_riyadh.isoformat(),
            "required_completion_display": due_display,
            "assignment_status": ASSIGNMENT_STATUS,
            "created_at": now,
            "created_by": _text(reviewer.get("id")),
            "created_by_name": _text(reviewer.get("name") or reviewer.get("email")),
            "expires_at": now + timedelta(hours=DRAFT_TTL_HOURS),
            "mezan_only": True,
            "salla_updated": False,
            "qoyod_updated": False,
        }
        shell = {
            "id": batch_id,
            "user_id": user_id,
            "client_request_id": payload.client_request_id,
            "status": "building",
            "title": pdf_title,
            "file_number": file_number,
            "file_title": title,
            "file_name": file_name,
            "selections": selection_rows,
            "responsible_employee_id": employee["id"],
            "responsible_employee_name": employee["name"],
            "responsible_employee_email": employee["email"],
            "required_completion_at": due_utc,
            "required_completion_at_riyadh": due_riyadh.isoformat(),
            "required_completion_display": due_display,
            "assignment_status": ASSIGNMENT_STATUS,
            "created_at": now,
            "created_by": _text(reviewer.get("id")),
            "created_by_name": _text(reviewer.get("name") or reviewer.get("email")),
            "expires_at": now + timedelta(minutes=batch_module.BATCH_BUILD_TTL_MINUTES),
            "mezan_only": True,
            "salla_updated": False,
            "qoyod_updated": False,
        }
        try:
            await db[REGISTRY].insert_one(registry)
            await db[batch_module.BATCHES].insert_one(shell)
        except DuplicateKeyError as exc:
            duplicate = await db[batch_module.BATCHES].find_one(
                {"user_id": user_id, "client_request_id": payload.client_request_id},
                {"_id": 0},
            )
            duplicate_registry = await db[REGISTRY].find_one(
                {"user_id": user_id, "client_request_id": payload.client_request_id},
                {"_id": 0},
            ) or {}
            if duplicate and _text(duplicate.get("status")) == "ready":
                return _file_response(duplicate, duplicate_registry)
            raise HTTPException(
                status_code=409,
                detail={"code": "preparation_file_build_in_progress"},
            ) from exc

        allocation_docs: list[dict[str, Any]] = []
        reservation_expiry = now + timedelta(minutes=batch_module.BATCH_BUILD_TTL_MINUTES)
        for allocation in planned:
            for unit_index in allocation.get("unit_indices") or []:
                allocation_docs.append({
                    "id": uuid.uuid4().hex,
                    "user_id": user_id,
                    "batch_id": batch_id,
                    "status": "reserved",
                    "group_key": allocation["group_key"],
                    "order_number": allocation["order_number"],
                    "order_item_id": allocation["order_item_id"],
                    **batch_module.preparation_allocation_identity_fields(allocation),
                    "unit_index": int(unit_index),
                    "reserved_at": now,
                    "expires_at": reservation_expiry,
                })
        try:
            await db[PREPARATION_UNIT_ALLOCATIONS].insert_many(allocation_docs, ordered=True)
        except (BulkWriteError, DuplicateKeyError) as exc:
            await _rollback_build(
                db,
                user_id=user_id,
                batch_id=batch_id,
                request_id=payload.client_request_id,
            )
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "preparation_units_already_allocated",
                    "message": "حجز موظف آخر بعض القطع. حدّث الصفحة وأعد الاختيار.",
                },
            ) from exc

        try:
            batch_lines = await batch_module.build_batch_lines_after_allocation_guard(
                db,
                user_id=user_id,
                context=context,
                planned=planned,
            )
            pdf_bytes = generate_preparation_pdf(
                [batch_module._line_from_batch_storage(row) for row in batch_lines],
                serial_start=1,
                title=pdf_title,
            )
            if not pdf_bytes.startswith(b"%PDF"):
                raise ValueError("invalid_preparation_pdf")
            order_numbers = sorted({row["order_number"] for row in batch_lines})
            actual_products = len({row["group_key"] for row in batch_lines})
            actual_quantity = sum(int(row.get("quantity") or 0) for row in batch_lines)
            if actual_products != selected_product_count or actual_quantity != expected_quantity:
                raise ValueError("preparation_file_quantity_mismatch")
            ready_at = _now()
            ready_patch = {
                "status": "ready",
                "ready_at": ready_at,
                "updated_at": ready_at,
                "lines": batch_lines,
                "card_count": len(batch_lines),
                "order_count": len(order_numbers),
                "order_numbers": order_numbers,
                "selected_product_count": actual_products,
                "allocated_quantity": actual_quantity,
                "pdf_size_bytes": len(pdf_bytes),
                "pdf_sha256": hashlib.sha256(pdf_bytes).hexdigest(),
                "moved_to_in_progress": False,
            }
            await db[batch_module.BATCHES].update_one(
                {"user_id": user_id, "id": batch_id, "status": "building"},
                {"$set": ready_patch, "$unset": {"expires_at": ""}},
            )
            await db[PREPARATION_UNIT_ALLOCATIONS].update_many(
                {"user_id": user_id, "batch_id": batch_id, "status": "reserved"},
                {
                    "$set": {"status": "committed", "committed_at": ready_at},
                    "$unset": {"expires_at": ""},
                },
            )
            registry_patch = {
                "status": "ready",
                "batch_id": batch_id,
                "allocated_quantity": actual_quantity,
                "order_count": len(order_numbers),
                "registered_at": ready_at,
                "updated_at": ready_at,
            }
            await db[REGISTRY].update_one(
                {"user_id": user_id, "client_request_id": payload.client_request_id},
                {"$set": registry_patch, "$unset": {"expires_at": ""}},
            )
            registry.update(registry_patch)
            batch = {**shell, **ready_patch}
            await _record_planning_assignments(
                db,
                user_id=user_id,
                batch=batch,
                registry=registry,
                actor=reviewer,
            )
            await db[EVENTS].insert_one({
                "user_id": user_id,
                "batch_id": batch_id,
                "file_number": file_number,
                "event_type": "preparation_file_created_assigned_not_started",
                "order_numbers": order_numbers,
                "allocated_quantity": actual_quantity,
                "responsible_employee_id": employee["id"],
                "required_completion_at": due_utc,
                "occurred_at": ready_at.isoformat(),
                "actor_id": _text(reviewer.get("id")),
                "mezan_only": True,
                "salla_updated": False,
                "qoyod_updated": False,
            })
            return _file_response(batch, registry)
        except HTTPException:
            await _rollback_build(
                db,
                user_id=user_id,
                batch_id=batch_id,
                request_id=payload.client_request_id,
            )
            raise
        except Exception as exc:
            await _rollback_build(
                db,
                user_id=user_id,
                batch_id=batch_id,
                request_id=payload.client_request_id,
            )
            raise HTTPException(
                status_code=500,
                detail={
                    "code": "preparation_batch_generation_failed",
                    "message": "تعذّر إنشاء ملف التجهيز؛ أُعيدت الحجوزات وبقيت الطلبات في تمت المراجعة.",
                },
            ) from exc

    @router.post("/files/{batch_id}/print-link")
    async def create_print_link(
        batch_id: str,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        reviewer = _require_reviewer(user)
        user_id = _merchant_user_id(reviewer)
        batch = await db[batch_module.BATCHES].find_one(
            {"user_id": user_id, "id": batch_id, "status": "ready"},
            {"_id": 0, "lines": 0},
        )
        if not batch:
            raise HTTPException(status_code=404, detail={"code": "preparation_batch_not_found"})
        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        expires_at = _now() + timedelta(minutes=PRINT_LINK_TTL_MINUTES)
        await db[batch_module.BATCHES].update_one(
            {"user_id": user_id, "id": batch_id, "status": "ready"},
            {
                "$set": {
                    "mobile_print_token_hash": token_hash,
                    "mobile_print_expires_at": expires_at,
                    "mobile_print_created_by": _text(reviewer.get("id")),
                },
            },
        )
        return {
            "ok": True,
            "path": f"/mobile-reviewed-preparation-v1/print/{token}",
            "expires_at": expires_at.isoformat(),
        }

    @router.get("/print/{token}")
    async def print_file(token: str) -> StreamingResponse:
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        batch = await db[batch_module.BATCHES].find_one(
            {
                "status": "ready",
                "mobile_print_token_hash": token_hash,
                "mobile_print_expires_at": {"$gt": _now()},
            },
            {"_id": 0},
        )
        if not batch:
            raise HTTPException(status_code=404, detail={"code": "print_link_expired"})
        try:
            pdf_bytes = batch_module.render_preparation_batch_pdf(batch)
        except Exception as exc:
            raise HTTPException(
                status_code=500,
                detail={"code": "preparation_batch_pdf_failed"},
            ) from exc
        file_name = _text(batch.get("file_name")) or f"preparation-{batch.get('id')}.pdf"
        encoded = quote(file_name)
        return StreamingResponse(
            io.BytesIO(pdf_bytes),
            media_type="application/pdf",
            headers={
                "Content-Disposition": (
                    f"inline; filename=preparation-{batch.get('id')}.pdf; "
                    f"filename*=UTF-8''{encoded}"
                ),
                "Cache-Control": "private, no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    return router


__all__ = [
    "ASSIGNMENT_STATUS",
    "CreateMobilePreparationFileRequest",
    "MobilePreparationSelection",
    "make_mobile_reviewed_preparation_router",
]
