"""Auditable registry metadata for reviewed preparation PDF batches.

The quantity allocation and PDF snapshot remain owned by
``reviewed_preparation_batches``. This module adds the human-facing file name,
Riyadh date, responsible employee, permanent file number, and a recoverable
history record linked by the existing idempotency key.
"""
from __future__ import annotations

import re
from datetime import timedelta
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from pymongo import ASCENDING, DESCENDING, ReturnDocument
from pymongo.errors import DuplicateKeyError

from mobile_app_permissions import (
    MOBILE_APP_ACCESS,
    MOBILE_APP_ACCESS_OWNER_FIELD,
    effective_mobile_app_permissions,
)
from order_review_export_controls import assignable_employee_view
from order_review_routes import EVENTS, _merchant_user_id, _require_reviewer, _text
from reviewed_preparation_batches import BATCHES
from tz_utils import riyadh_now_aware


REGISTRY = "mezan_preparation_file_registry_v2"
COUNTERS = "mezan_sequence_counters_v2"
DRAFT_TTL_HOURS = 24
MAX_FILE_TITLE_LENGTH = 120
_INVALID_FILE_CHARS_RE = re.compile(r"[\\/:*?\"<>|\x00-\x1f]+")


class PreparationFileDraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_request_id: str = Field(min_length=8, max_length=160)
    file_title: str = Field(min_length=1, max_length=MAX_FILE_TITLE_LENGTH)
    responsible_employee_id: str = Field(min_length=1, max_length=120)
    expected_quantity: int = Field(ge=1, le=1500)
    selected_product_count: int = Field(ge=1, le=200)


def normalize_file_title(value: Any) -> str:
    title = _INVALID_FILE_CHARS_RE.sub(" ", _text(value))
    title = " ".join(title.split()).strip(" .-_—")
    if not title:
        raise ValueError("preparation_file_title_required")
    return title[:MAX_FILE_TITLE_LENGTH].rstrip()


def preparation_file_name(title: Any, file_date: str, quantity: int) -> str:
    clean = normalize_file_title(title)
    return f"{clean} — {file_date} — {int(quantity)} قطعة.pdf"


def preparation_file_number(file_date: str, sequence: int) -> str:
    date_key = str(file_date or "").replace("-", "")
    return f"PF-{date_key}-{int(sequence):04d}"


def preparation_file_view(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "file_number": _text(row.get("file_number")),
        "batch_id": _text(row.get("batch_id")),
        "client_request_id": _text(row.get("client_request_id")),
        "status": _text(row.get("status")),
        "file_title": _text(row.get("file_title")),
        "file_name": _text(row.get("file_name")),
        "file_date": _text(row.get("file_date")),
        "file_date_display": _text(row.get("file_date_display")),
        "allocated_quantity": int(row.get("allocated_quantity") or 0),
        "selected_product_count": int(row.get("selected_product_count") or 0),
        "order_count": int(row.get("order_count") or 0),
        "responsible_employee_id": _text(row.get("responsible_employee_id")),
        "responsible_employee_name": _text(row.get("responsible_employee_name")),
        "responsible_employee_email": _text(row.get("responsible_employee_email")),
        "created_at": row.get("created_at"),
        "registered_at": row.get("registered_at"),
        "created_by": _text(row.get("created_by")),
        "created_by_name": _text(row.get("created_by_name")),
        "mezan_only": True,
        "salla_updated": False,
        "qoyod_updated": False,
    }


async def ensure_preparation_file_registry_indexes(db: Any) -> None:
    await db[REGISTRY].create_index(
        [("user_id", ASCENDING), ("client_request_id", ASCENDING)],
        unique=True,
        name="uq_preparation_file_request_v2",
    )
    await db[REGISTRY].create_index(
        [("user_id", ASCENDING), ("file_number", ASCENDING)],
        unique=True,
        name="uq_preparation_file_number_v2",
    )
    await db[REGISTRY].create_index(
        [("user_id", ASCENDING), ("registered_at", DESCENDING)],
        name="ix_preparation_file_history_v2",
    )
    await db[REGISTRY].create_index(
        [("expires_at", ASCENDING)],
        expireAfterSeconds=0,
        name="ttl_preparation_file_drafts_v2",
    )
    await db[COUNTERS].create_index(
        [("user_id", ASCENDING), ("key", ASCENDING)],
        unique=True,
        name="uq_mezan_sequence_counter_v2",
    )


async def _assignable_employees(
    db: Any,
    *,
    user_id: str,
    reviewer: dict[str, Any],
) -> list[dict[str, Any]]:
    """Return active AMASI-app employees eligible for preparation assignment.

    Eligibility comes only from the native-app access contract, never Mezan web
    RBAC. The assignee must have active app access to ``app.page.my_products``
    because that is the page used to work assigned preparation files. The
    merchant owner remains eligible through the native owner override. Employees
    that already owned preparation files are surfaced first.
    """
    access_rows = await db[MOBILE_APP_ACCESS].find(
        {
            MOBILE_APP_ACCESS_OWNER_FIELD: user_id,
            "enabled": {"$ne": False},
        },
        {"_id": 0, "user_id": 1, "permissions": 1, "enabled": 1},
    ).to_list(1000)
    access_by_id = {
        _text(row.get("user_id")): row
        for row in access_rows
        if _text(row.get("user_id"))
    }

    # ``user_id`` is the merchant owner resolved by _merchant_user_id(). The
    # caller may itself be a staff reviewer, so never treat reviewer.id as owner.
    owner_id = _text(user_id)
    reviewer_id = _text(reviewer.get("id"))
    candidate_ids = sorted(set(access_by_id) | ({owner_id} if owner_id else set()))
    docs = await db.users.find(
        {"id": {"$in": candidate_ids}},
        {
            "_id": 0,
            "id": 1,
            "name": 1,
            "email": 1,
            "role": 1,
            "is_owner": 1,
            "disabled": 1,
            "is_active": 1,
            "deleted_at": 1,
        },
    ).to_list(max(len(candidate_ids), 1))
    users_by_id = {_text(row.get("id")): row for row in docs if _text(row.get("id"))}
    if owner_id and owner_id not in users_by_id and reviewer_id == owner_id:
        users_by_id[owner_id] = reviewer

    eligible_by_id: dict[str, dict[str, Any]] = {}
    for employee_id in candidate_ids:
        row = users_by_id.get(employee_id)
        if not row:
            continue
        if (
            row.get("disabled") is True
            or row.get("is_active") is False
            or row.get("deleted_at")
            or not _text(row.get("email"))
        ):
            continue

        is_owner = employee_id == owner_id
        permissions = set(
            effective_mobile_app_permissions(
                access_by_id.get(employee_id),
                account_active=True,
            )
        )
        if not is_owner and "app.page.my_products" not in permissions:
            continue
        eligible_by_id[employee_id] = row

    employee_ids = list(eligible_by_id)
    previously_assigned: set[str] = set()
    if employee_ids:
        rows = await db[REGISTRY].find(
            {
                "user_id": user_id,
                "responsible_employee_id": {"$in": employee_ids},
            },
            {"_id": 0, "responsible_employee_id": 1},
        ).to_list(5000)
        previously_assigned = {
            _text(row.get("responsible_employee_id"))
            for row in rows
            if _text(row.get("responsible_employee_id"))
        }

    result = [assignable_employee_view(row) for row in eligible_by_id.values()]
    result.sort(
        key=lambda row: (
            0 if row["id"] in previously_assigned else 1,
            _text(row.get("name")).casefold(),
            row["id"],
        )
    )
    return result


async def _next_file_sequence(db: Any, user_id: str) -> int:
    row = await db[COUNTERS].find_one_and_update(
        {"user_id": user_id, "key": "preparation_file"},
        {
            "$inc": {"value": 1},
            "$setOnInsert": {
                "user_id": user_id,
                "key": "preparation_file",
                "created_at": riyadh_now_aware().isoformat(),
            },
            "$set": {"updated_at": riyadh_now_aware().isoformat()},
        },
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return max(1, int((row or {}).get("value") or 1))


async def _finalize_registry_row(
    db: Any,
    *,
    user_id: str,
    client_request_id: str,
    actor: dict[str, Any],
) -> dict[str, Any]:
    registry = await db[REGISTRY].find_one(
        {"user_id": user_id, "client_request_id": client_request_id},
        {"_id": 0},
    )
    if not registry:
        raise HTTPException(
            status_code=404,
            detail={"code": "preparation_file_draft_not_found"},
        )
    if _text(registry.get("status")) == "ready" and _text(registry.get("batch_id")):
        return registry

    batch = await db[BATCHES].find_one(
        {
            "user_id": user_id,
            "client_request_id": client_request_id,
            "status": "ready",
        },
        {"_id": 0, "lines": 0},
    )
    if not batch:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "preparation_batch_not_ready",
                "message": "لم يكتمل إنشاء ملف PDF بعد.",
            },
        )

    actual_quantity = int(batch.get("allocated_quantity") or 0)
    actual_products = int(batch.get("selected_product_count") or 0)
    if (
        actual_quantity != int(registry.get("expected_quantity") or 0)
        or actual_products != int(registry.get("selected_product_count") or 0)
    ):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "preparation_file_quantity_mismatch",
                "message": "عدد القطع الفعلي لا يطابق بيانات الملف. حدّث الصفحة وأعد المحاولة.",
            },
        )

    now = riyadh_now_aware()
    file_name = preparation_file_name(
        registry.get("file_title"),
        _text(registry.get("file_date")),
        actual_quantity,
    )
    patch = {
        "status": "ready",
        "batch_id": _text(batch.get("id")),
        "file_name": file_name,
        "allocated_quantity": actual_quantity,
        "order_count": int(batch.get("order_count") or 0),
        "registered_at": now,
        "updated_at": now,
    }
    await db[BATCHES].update_one(
        {"user_id": user_id, "id": batch.get("id"), "status": "ready"},
        {
            "$set": {
                "file_number": registry.get("file_number"),
                "file_title": registry.get("file_title"),
                "file_name": file_name,
                "file_date": registry.get("file_date"),
                "file_date_display": registry.get("file_date_display"),
                "responsible_employee_id": registry.get("responsible_employee_id"),
                "responsible_employee_name": registry.get("responsible_employee_name"),
                "responsible_employee_email": registry.get("responsible_employee_email"),
                "registry_status": "ready",
                "registered_at": now,
            },
        },
    )
    await db[REGISTRY].update_one(
        {"user_id": user_id, "client_request_id": client_request_id},
        {"$set": patch, "$unset": {"expires_at": ""}},
    )
    registry.update(patch)
    await db[EVENTS].insert_one({
        "user_id": user_id,
        "batch_id": patch["batch_id"],
        "file_number": registry.get("file_number"),
        "event_type": "preparation_file_registered",
        "allocated_quantity": actual_quantity,
        "responsible_employee_id": registry.get("responsible_employee_id"),
        "occurred_at": now.isoformat(),
        "actor_id": _text(actor.get("id")),
        "mezan_only": True,
        "salla_updated": False,
        "qoyod_updated": False,
    })
    return registry


def make_preparation_file_registry_router(
    db: Any,
    current_user: Callable,
) -> APIRouter:
    router = APIRouter(
        prefix="/preparation-file-registry-v1",
        tags=["Preparation File Registry"],
    )

    @router.get("/employees")
    async def list_employees(
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        reviewer = _require_reviewer(user)
        user_id = _merchant_user_id(reviewer)
        employees = await _assignable_employees(
            db,
            user_id=user_id,
            reviewer=reviewer,
        )
        return {"items": employees}

    @router.post("/drafts")
    async def create_draft(
        payload: PreparationFileDraftRequest,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        reviewer = _require_reviewer(user)
        user_id = _merchant_user_id(reviewer)
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
            (row for row in employees if row["id"] == payload.responsible_employee_id),
            None,
        )
        if not employee:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "responsible_employee_unavailable",
                    "message": "اختر موظفًا نشطًا لديه وصول لتطبيق أماسي وصفحة إدارة منتجاتي.",
                },
            )

        existing = await db[REGISTRY].find_one(
            {"user_id": user_id, "client_request_id": payload.client_request_id},
            {"_id": 0},
        )
        if existing:
            same = (
                _text(existing.get("file_title")) == title
                and _text(existing.get("responsible_employee_id")) == employee["id"]
                and int(existing.get("expected_quantity") or 0) == payload.expected_quantity
                and int(existing.get("selected_product_count") or 0) == payload.selected_product_count
            )
            if not same:
                raise HTTPException(
                    status_code=409,
                    detail={"code": "preparation_file_draft_conflict"},
                )
            return preparation_file_view(existing)

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
            "created_at": now,
            "created_by": _text(reviewer.get("id")),
            "created_by_name": _text(reviewer.get("name") or reviewer.get("email")),
            "expires_at": now + timedelta(hours=DRAFT_TTL_HOURS),
            "mezan_only": True,
            "salla_updated": False,
            "qoyod_updated": False,
        }
        try:
            await db[REGISTRY].insert_one(row)
        except DuplicateKeyError:
            duplicate = await db[REGISTRY].find_one(
                {"user_id": user_id, "client_request_id": payload.client_request_id},
                {"_id": 0},
            )
            if duplicate:
                return preparation_file_view(duplicate)
            raise
        return preparation_file_view(row)

    @router.post("/finalize/{client_request_id}")
    async def finalize_file(
        client_request_id: str,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        reviewer = _require_reviewer(user)
        user_id = _merchant_user_id(reviewer)
        row = await _finalize_registry_row(
            db,
            user_id=user_id,
            client_request_id=client_request_id,
            actor=reviewer,
        )
        return preparation_file_view(row)

    @router.get("/files")
    async def list_files(
        limit: int = Query(30, ge=1, le=200),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        reviewer = _require_reviewer(user)
        user_id = _merchant_user_id(reviewer)
        await ensure_preparation_file_registry_indexes(db)

        drafts = await db[REGISTRY].find(
            {"user_id": user_id, "status": "draft"},
            {"_id": 0, "client_request_id": 1},
        ).limit(100).to_list(100)
        for draft in drafts:
            try:
                await _finalize_registry_row(
                    db,
                    user_id=user_id,
                    client_request_id=_text(draft.get("client_request_id")),
                    actor=reviewer,
                )
            except HTTPException:
                continue

        rows = await db[REGISTRY].find(
            {"user_id": user_id, "status": "ready"},
            {"_id": 0},
        ).sort("registered_at", -1).limit(limit).to_list(limit)
        return {"items": [preparation_file_view(row) for row in rows]}

    return router


__all__ = [
    "COUNTERS",
    "REGISTRY",
    "PreparationFileDraftRequest",
    "ensure_preparation_file_registry_indexes",
    "make_preparation_file_registry_router",
    "normalize_file_title",
    "preparation_file_name",
    "preparation_file_number",
    "preparation_file_view",
]
