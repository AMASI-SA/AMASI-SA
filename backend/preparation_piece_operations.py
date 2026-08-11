"""Durable piece operations for preparation files.

Every physical unit committed to a preparation file becomes one work record.
The file is assigned to one preparation employee, required services are
inherited from Product V2 product/option service links, and execution does not
start until the assigned employee (or an authorised manager) starts the file.

This module is Mezan-only. It performs no Salla, Qoyod, supplier, WhatsApp, or
accounting writes.
"""
from __future__ import annotations

import statistics
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from pymongo import ASCENDING, DESCENDING

from fulfillment_v2_routes import _actor_context
from order_review_export_controls import user_can_manage_preparation
from order_review_routes import (
    EVENTS,
    WORKFLOWS,
    _merchant_user_id,
    _normalized,
    _require_reviewer,
    _text,
)
from product_fulfillment_rules import PRODUCT_RESOURCE_BINDINGS
from product_option_cost_routes import BINDINGS, RESOURCES
from reviewed_products_catalog import (
    MAX_REVIEWED_ORDERS,
    PREPARATION_UNIT_ALLOCATIONS,
    load_reviewed_product_context,
)
from reviewed_preparation_batches import BATCHES
from preparation_file_registry import REGISTRY
from preparation_piece_barcode import preparation_piece_id
from tz_utils import riyadh_now_aware


PIECES = "mezan_preparation_pieces_v1"
PIECE_EVENTS = "mezan_preparation_piece_events_v1"
DEFAULT_ESTIMATED_DURATION_MINUTES = 2 * 24 * 60
MAX_HISTORY_ROWS = 5000

PIECE_STATUS_ASSIGNED = "assigned"
PIECE_STATUS_IN_PROGRESS = "in_progress"
PIECE_STATUS_READY_FOR_RECEIPT = "ready_for_employee_receipt"
PIECE_STATUS_RECEIVED = "received"
PIECE_STATUS_BLOCKED = "blocked"
PIECE_STATUS_CANCELLED = "cancelled"

_INSTALLED = False
_ORIGINAL_FINALIZE = None
_ORIGINAL_RECONCILE_ORDER_STAGE = None


class FileSchedulePatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["automatic", "required"]
    required_due_at: datetime | None = None


class StartPreparationFileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: str | None = Field(default=None, max_length=1000)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _piece_id(
    *,
    user_id: str,
    batch_id: str,
    order_number: str,
    order_item_id: str,
    unit_index: int,
) -> str:
    return preparation_piece_id(
        user_id=user_id,
        batch_id=batch_id,
        order_number=order_number,
        order_item_id=order_item_id,
        unit_index=unit_index,
    )


def _positive_unit_indices(line: dict[str, Any]) -> list[int]:
    raw_values = (
        [line.get("unit_index")]
        if line.get("unit_index") not in (None, "")
        else line.get("unit_indices") or []
    )
    values: list[int] = []
    for value in raw_values:
        try:
            number = int(value)
        except (TypeError, ValueError, OverflowError):
            continue
        if number > 0 and number not in values:
            values.append(number)
    if values:
        return values
    try:
        quantity = max(1, int(line.get("quantity") or 1))
    except (TypeError, ValueError, OverflowError):
        quantity = 1
    return list(range(1, quantity + 1))


def validate_materialized_piece_count(
    *,
    batch: dict[str, Any],
    registry: dict[str, Any],
    pieces: list[dict[str, Any]],
) -> int:
    """Fail closed when a ready file did not produce every physical piece."""
    expected_piece_count = int(
        batch.get("allocated_quantity")
        or registry.get("allocated_quantity")
        or 0
    )
    if expected_piece_count <= 0 or len(pieces) != expected_piece_count:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "preparation_piece_count_mismatch",
                "expected_piece_count": expected_piece_count,
                "actual_piece_count": len(pieces),
            },
        )
    return expected_piece_count


def _selected_spec_pairs(line: dict[str, Any]) -> set[tuple[str, str]]:
    pairs: set[tuple[str, str]] = set()
    for row in line.get("file_spec_fields") or []:
        if not isinstance(row, dict):
            continue
        name = _normalized(row.get("name"))
        value = _normalized(row.get("value"))
        if name and value:
            pairs.add((name, value))
    for name, value in (line.get("product_options") or {}).items():
        normalized_name = _normalized(name)
        normalized_value = _normalized(value)
        if normalized_name and normalized_value:
            pairs.add((normalized_name, normalized_value))
    for name, value in (
        ("المقاس", line.get("size")),
        ("اللون", line.get("color")),
        ("الاسم", line.get("customer_name")),
    ):
        normalized_value = _normalized(value)
        if normalized_value:
            pairs.add((_normalized(name), normalized_value))
    return pairs


def _resource_is_service(resource: dict[str, Any]) -> bool:
    return _normalized(resource.get("kind")) == "service"


def inherit_required_services(
    *,
    line: dict[str, Any],
    product_links: Iterable[dict[str, Any]],
    option_bindings: Iterable[dict[str, Any]],
    resources_by_id: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Inherit services from the product and the selected option values."""
    selected_pairs = _selected_spec_pairs(line)
    inherited: dict[str, dict[str, Any]] = {}

    def add(resource_id: Any, quantity: Any, source: str, condition=None) -> None:
        key = _text(resource_id)
        resource = resources_by_id.get(key)
        if not key or not resource or not _resource_is_service(resource):
            return
        try:
            required_quantity = float(quantity or 1)
        except (TypeError, ValueError, OverflowError):
            required_quantity = 1.0
        if required_quantity <= 0:
            required_quantity = 1.0
        inherited[key] = {
            "service_id": key,
            "service_name": _text(resource.get("name")) or key,
            "service_code": _text(resource.get("code")) or None,
            "required_quantity": required_quantity,
            "unit": _text(resource.get("unit")) or "job",
            "reference_unit_cost": resource.get("unit_cost"),
            "source": source,
            "condition": condition,
            "status": "pending",
            "completed_quantity": 0.0,
        }

    for link in product_links:
        add(link.get("resource_id"), link.get("quantity"), "product")

    for binding in option_bindings:
        if _text(binding.get("mode")) != "resource":
            continue
        option_name = _normalized(binding.get("option_name"))
        value_name = _normalized(binding.get("value_name"))
        if not option_name or not value_name:
            continue
        if (option_name, value_name) not in selected_pairs:
            continue
        add(
            binding.get("resource_id"),
            binding.get("quantity"),
            "option",
            {
                "option_id": binding.get("option_id"),
                "option_name": binding.get("option_name"),
                "value_id": binding.get("value_id"),
                "value_name": binding.get("value_name"),
            },
        )

    return sorted(
        inherited.values(),
        key=lambda row: (
            _normalized(row.get("service_name")),
            _text(row.get("service_id")),
        ),
    )


def _duration_minutes(started_at: Any, completed_at: Any) -> int | None:
    if not isinstance(started_at, datetime) or not isinstance(completed_at, datetime):
        return None
    seconds = (completed_at - started_at).total_seconds()
    if seconds <= 0:
        return None
    return max(1, int(round(seconds / 60)))


def build_duration_history(
    rows: Iterable[dict[str, Any]],
) -> dict[tuple[str, str, str], int]:
    """Build median previous preparation time by employee/product/services."""
    exact: dict[tuple[str, str, str], list[int]] = defaultdict(list)
    product: dict[tuple[str, str], list[int]] = defaultdict(list)
    global_values: list[int] = []
    for row in rows:
        duration = _duration_minutes(row.get("started_at"), row.get("completed_at"))
        if duration is None:
            continue
        employee_id = _text(row.get("responsible_employee_id"))
        product_id = _text(row.get("product_id"))
        signature = "|".join(sorted(
            _text(service.get("service_id"))
            for service in (row.get("services") or [])
            if _text(service.get("service_id"))
        ))
        exact[(employee_id, product_id, signature)].append(duration)
        product[(product_id, signature)].append(duration)
        global_values.append(duration)
    result: dict[tuple[str, str, str], int] = {}
    for key, values in exact.items():
        result[key] = int(statistics.median(values))
    for (product_id, signature), values in product.items():
        result[("", product_id, signature)] = int(statistics.median(values))
    result[("", "", "")] = (
        int(statistics.median(global_values))
        if global_values
        else DEFAULT_ESTIMATED_DURATION_MINUTES
    )
    return result


def build_piece_documents(
    *,
    user_id: str,
    registry: dict[str, Any],
    batch: dict[str, Any],
    services_by_product: dict[str, dict[str, Any]],
    assigned_at: datetime,
    duration_by_signature: dict[tuple[str, str, str], int] | None = None,
) -> list[dict[str, Any]]:
    """Expand batch lines to one durable record per physical piece."""
    duration_by_signature = duration_by_signature or {}
    batch_id = _text(batch.get("id"))
    employee_id = _text(registry.get("responsible_employee_id"))
    employee_name = _text(registry.get("responsible_employee_name"))
    file_number = _text(registry.get("file_number"))
    documents: list[dict[str, Any]] = []
    riyadh_tz = riyadh_now_aware().tzinfo

    for line in batch.get("lines") or []:
        if not isinstance(line, dict):
            continue
        product_id = _text(line.get("product_id"))
        services = list((services_by_product.get(product_id) or {}).get("services") or [])
        signature = "|".join(sorted(_text(row.get("service_id")) for row in services))
        duration_minutes = int(duration_by_signature.get(
            (employee_id, product_id, signature),
            duration_by_signature.get(
                ("", product_id, signature),
                duration_by_signature.get(
                    ("", "", ""),
                    DEFAULT_ESTIMATED_DURATION_MINUTES,
                ),
            ),
        ))
        due_at = assigned_at + timedelta(minutes=max(1, duration_minutes))
        for unit_index in _positive_unit_indices(line):
            piece_id = _piece_id(
                user_id=user_id,
                batch_id=batch_id,
                order_number=_text(line.get("order_number")),
                order_item_id=_text(line.get("order_item_id")),
                unit_index=unit_index,
            )
            documents.append({
                "id": piece_id,
                "piece_id": piece_id,
                "user_id": user_id,
                "batch_id": batch_id,
                "file_number": file_number,
                "file_title": _text(registry.get("file_title")),
                "order_number": _text(line.get("order_number")),
                "order_item_id": _text(line.get("order_item_id")),
                "unit_index": unit_index,
                "group_key": _text(line.get("group_key")),
                "product_id": product_id or None,
                "product_name": _text(line.get("product_name")) or None,
                "sku": _text(line.get("sku")) or None,
                "selected_image_url": _text(line.get("selected_image_url")) or None,
                "resolved_image_url": _text(line.get("resolved_image_url")) or None,
                "image_url": (
                    _text(line.get("image_url"))
                    or _text(line.get("resolved_image_url"))
                    or next(
                        (
                            _text(candidate)
                            for candidate in line.get("image_candidates") or []
                            if _text(candidate)
                        ),
                        "",
                    )
                    or None
                ),
                "specifications_snapshot": list(line.get("file_spec_fields") or []),
                "product_options_snapshot": dict(line.get("product_options") or {}),
                "preparation_note": _text(line.get("preparation_note")) or None,
                "responsible_employee_id": employee_id,
                "responsible_employee_name": employee_name,
                "status": PIECE_STATUS_ASSIGNED,
                "execution_status": "not_started",
                "services": [dict(row) for row in services],
                "service_count": len(services),
                "completed_service_count": 0,
                "remaining_service_count": len(services),
                "service_plan_status": "pending" if services else "no_external_services",
                "schedule_mode": "automatic",
                "estimated_duration_minutes": duration_minutes,
                "estimated_due_at": due_at,
                "required_due_at": None,
                "assigned_at": assigned_at,
                "assignment_date": assigned_at.astimezone(riyadh_tz).strftime("%Y-%m-%d"),
                "created_at": assigned_at,
                "updated_at": assigned_at,
                "mezan_only": True,
                "salla_updated": False,
                "qoyod_updated": False,
            })
    return documents


def _piece_upsert_update(
    piece: dict[str, Any],
    *,
    updated_at: datetime | None = None,
) -> dict[str, dict[str, Any]]:
    """Build a MongoDB upsert without conflicting update paths.

    MongoDB rejects an update when the same field appears in both ``$set`` and
    ``$setOnInsert``.  These mutable assignment fields must therefore be
    removed from the insert-only snapshot before an existing piece is
    reconciled with its current file and employee.
    """
    mutable_values = {
        "file_number": piece["file_number"],
        "file_title": piece["file_title"],
        "responsible_employee_id": piece["responsible_employee_id"],
        "responsible_employee_name": piece["responsible_employee_name"],
        "selected_image_url": piece.get("selected_image_url"),
        "resolved_image_url": piece.get("resolved_image_url"),
        "image_url": piece.get("image_url"),
        "updated_at": updated_at or _now(),
    }
    insert_values = {
        key: value
        for key, value in piece.items()
        if key not in mutable_values
    }
    return {
        "$setOnInsert": insert_values,
        "$set": mutable_values,
    }


async def ensure_piece_operation_indexes(db: Any) -> None:
    await db[PIECES].create_index(
        [
            ("user_id", ASCENDING),
            ("batch_id", ASCENDING),
            ("order_number", ASCENDING),
            ("order_item_id", ASCENDING),
            ("unit_index", ASCENDING),
        ],
        unique=True,
        name="uq_preparation_piece_physical_unit_v1",
    )
    await db[PIECES].create_index(
        [
            ("user_id", ASCENDING),
            ("responsible_employee_id", ASCENDING),
            ("status", ASCENDING),
            ("updated_at", DESCENDING),
        ],
        name="ix_preparation_piece_employee_status_v1",
    )
    await db[PIECES].create_index(
        [("user_id", ASCENDING), ("file_number", ASCENDING), ("unit_index", ASCENDING)],
        name="ix_preparation_piece_file_v1",
    )
    await db[PIECE_EVENTS].create_index(
        [("user_id", ASCENDING), ("piece_id", ASCENDING), ("occurred_at", DESCENDING)],
        name="ix_preparation_piece_events_v1",
    )


async def _service_context_for_batch(
    db: Any,
    *,
    user_id: str,
    batch: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    product_ids = sorted({
        _text(line.get("product_id"))
        for line in batch.get("lines") or []
        if isinstance(line, dict) and _text(line.get("product_id"))
    })
    if not product_ids:
        return {}
    product_links = await db[PRODUCT_RESOURCE_BINDINGS].find(
        {"user_id": user_id, "salla_product_id": {"$in": product_ids}},
        {"_id": 0},
    ).to_list(length=10000)
    option_bindings = await db[BINDINGS].find(
        {"user_id": user_id, "salla_product_id": {"$in": product_ids}, "mode": "resource"},
        {"_id": 0},
    ).to_list(length=20000)
    resource_ids = {
        _text(row.get("resource_id"))
        for row in [*product_links, *option_bindings]
        if _text(row.get("resource_id"))
    }
    resources = (
        await db[RESOURCES].find(
            {"user_id": user_id, "id": {"$in": sorted(resource_ids)}},
            {"_id": 0},
        ).to_list(length=max(1, len(resource_ids)))
        if resource_ids
        else []
    )
    resources_by_id = {_text(row.get("id")): row for row in resources if _text(row.get("id"))}
    links_by_product: dict[str, list[dict[str, Any]]] = defaultdict(list)
    bindings_by_product: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in product_links:
        links_by_product[_text(row.get("salla_product_id"))].append(row)
    for row in option_bindings:
        bindings_by_product[_text(row.get("salla_product_id"))].append(row)
    context: dict[str, dict[str, Any]] = {}
    for line in batch.get("lines") or []:
        if not isinstance(line, dict):
            continue
        product_id = _text(line.get("product_id"))
        if product_id:
            context[product_id] = {"services": inherit_required_services(
                line=line,
                product_links=links_by_product.get(product_id, []),
                option_bindings=bindings_by_product.get(product_id, []),
                resources_by_id=resources_by_id,
            )}
    return context


async def materialize_preparation_pieces(
    db: Any,
    *,
    user_id: str,
    registry: dict[str, Any],
) -> list[dict[str, Any]]:
    """Idempotently create physical pieces after file registration."""
    batch_id = _text(registry.get("batch_id"))
    if not batch_id:
        raise HTTPException(
            status_code=409,
            detail={"code": "preparation_file_batch_missing"},
        )
    await ensure_piece_operation_indexes(db)
    batch = await db[BATCHES].find_one(
        {"user_id": user_id, "id": batch_id, "status": "ready"},
        {"_id": 0},
    )
    if not batch:
        raise HTTPException(
            status_code=409,
            detail={"code": "preparation_batch_missing_for_piece_materialization"},
        )
    history = await db[PIECES].find(
        {
            "user_id": user_id,
            "completed_at": {"$ne": None},
            "started_at": {"$ne": None},
            "$or": [
                {"experiment_archived_at": {"$exists": False}},
                {"experiment_archived_at": None},
            ],
        },
        {
            "_id": 0,
            "responsible_employee_id": 1,
            "product_id": 1,
            "services": 1,
            "started_at": 1,
            "completed_at": 1,
        },
    ).sort("completed_at", -1).limit(MAX_HISTORY_ROWS).to_list(MAX_HISTORY_ROWS)
    duration_history = build_duration_history(history)
    service_context = await _service_context_for_batch(db, user_id=user_id, batch=batch)
    assigned_at = registry.get("registered_at")
    if not isinstance(assigned_at, datetime):
        assigned_at = _now()
    pieces = build_piece_documents(
        user_id=user_id,
        registry=registry,
        batch=batch,
        services_by_product=service_context,
        assigned_at=assigned_at,
        duration_by_signature=duration_history,
    )
    order_numbers = sorted({
        _text(piece.get("order_number"))
        for piece in pieces
        if _text(piece.get("order_number"))
    })
    experiment_workflows = await db[WORKFLOWS].find(
        {
            "user_id": user_id,
            "order_number": {"$in": order_numbers},
            "experiment_mode": True,
            "experiment_run_id": {"$nin": [None, ""]},
        },
        {
            "_id": 0,
            "order_number": 1,
            "experiment_run_id": 1,
            "experiment_generation": 1,
        },
    ).to_list(max(len(order_numbers), 1)) if order_numbers else []
    experiment_by_order = {
        _text(row.get("order_number")): row
        for row in experiment_workflows
        if _text(row.get("order_number"))
    }
    for piece in pieces:
        experiment = experiment_by_order.get(_text(piece.get("order_number")))
        if experiment:
            piece.update({
                "experiment_mode": True,
                "experiment_run_id": _text(experiment.get("experiment_run_id")),
                "experiment_generation": int(experiment.get("experiment_generation") or 1),
                "financial_writes_allowed": False,
                "supplier_payable_allowed": False,
            })
    validate_materialized_piece_count(
        batch=batch,
        registry=registry,
        pieces=pieces,
    )
    for piece in pieces:
        await db[PIECES].update_one(
            {
                "user_id": user_id,
                "batch_id": piece["batch_id"],
                "order_number": piece["order_number"],
                "order_item_id": piece["order_item_id"],
                "unit_index": piece["unit_index"],
            },
            _piece_upsert_update(piece),
            upsert=True,
        )
    now = _now()
    experiment_run_ids = sorted({
        _text(piece.get("experiment_run_id"))
        for piece in pieces if _text(piece.get("experiment_run_id"))
    })
    runtime_patch = {
        "execution_status": "assigned",
        "piece_count": len(pieces),
        "piece_registry_status": "ready",
        "piece_registry_materialized_at": now,
        "updated_at": now,
    }
    if len(experiment_run_ids) == 1:
        runtime_patch.update({
            "experiment_mode": True,
            "experiment_run_id": experiment_run_ids[0],
            "financial_writes_allowed": False,
        })
    runtime_unset = {
        "piece_registry_last_error": "",
        "piece_registry_last_error_code": "",
        "piece_registry_last_error_message": "",
        "piece_registry_last_error_at": "",
    }
    await db[REGISTRY].update_one(
        {"user_id": user_id, "batch_id": batch_id},
        {"$set": runtime_patch, "$unset": runtime_unset},
    )
    await db[BATCHES].update_one(
        {"user_id": user_id, "id": batch_id},
        {"$set": runtime_patch, "$unset": runtime_unset},
    )
    return pieces


async def _assigned_reconcile_order_stage(
    db: Any,
    *,
    user_id: str,
    order_number: str,
    batch_id: str,
    actor: dict[str, Any],
) -> tuple[bool, int]:
    """Keep fully allocated orders reviewed until actual execution starts."""
    workflow = await db[WORKFLOWS].find_one(
        {"user_id": user_id, "order_number": order_number},
        {"_id": 0},
    )
    if not workflow:
        return False, 0
    stage = _text(workflow.get("stage"))
    if stage not in {"reviewed", "in_progress"}:
        return stage == "in_progress", 0
    if stage == "in_progress":
        return True, 0
    context = await load_reviewed_product_context(
        db,
        user_id=user_id,
        limit=MAX_REVIEWED_ORDERS,
    )
    order = next(
        (
            candidate
            for candidate, _row in context.get("pairs") or []
            if _text(candidate.order_number) == order_number
        ),
        None,
    )
    if order is None:
        return False, 0
    states = {
        _text(row.get("order_item_id")): dict(row)
        for row in workflow.get("items") or []
        if isinstance(row, dict) and _text(row.get("order_item_id"))
    }
    allocations = await db[PREPARATION_UNIT_ALLOCATIONS].find(
        {"user_id": user_id, "order_number": order_number, "status": "committed"},
        {"_id": 0, "order_item_id": 1, "unit_index": 1},
    ).to_list(10000)
    allocated_by_item: dict[str, set[int]] = defaultdict(set)
    for row in allocations:
        item_id = _text(row.get("order_item_id"))
        try:
            unit_index = int(row.get("unit_index"))
        except (TypeError, ValueError, OverflowError):
            continue
        if item_id and unit_index > 0:
            allocated_by_item[item_id].add(unit_index)
    required = allocated = 0
    for item in getattr(order, "items", None) or []:
        item_id = _text(getattr(item, "order_item_id", None))
        if states.get(item_id, {}).get("supplier_export") is False:
            continue
        try:
            quantity = max(0, int(round(float(getattr(item, "quantity", 0)))))
        except (TypeError, ValueError, OverflowError):
            quantity = 0
        required += quantity
        allocated += min(quantity, len(allocated_by_item.get(item_id, set())))
    remaining = max(0, required - allocated)
    now = _now()
    fully_allocated = remaining == 0
    update = {
        "$set": {
            "preparation_progress": {
                "required_quantity": required,
                "allocated_quantity": allocated,
                "remaining_quantity": remaining,
                "updated_at": now,
                "last_batch_id": batch_id,
            },
            "preparation_assignment_status": "assigned" if fully_allocated else "partially_assigned",
            "updated_at": now,
            "updated_by": _text(actor.get("id")),
        },
        "$addToSet": {"preparation_batch_ids": batch_id},
        "$inc": {"revision": 1},
    }
    if fully_allocated:
        update["$set"]["preparation_fully_allocated_at"] = now
    await db[WORKFLOWS].update_one(
        {"user_id": user_id, "order_number": order_number, "stage": "reviewed"},
        update,
    )
    if fully_allocated:
        await db[EVENTS].insert_one({
            "user_id": user_id,
            "order_number": order_number,
            "batch_id": batch_id,
            "event_type": "order_preparation_fully_assigned",
            "occurred_at": now,
            "actor_id": _text(actor.get("id")),
            "mezan_only": True,
            "salla_updated": False,
            "qoyod_updated": False,
        })
    return False, remaining


def _can_start_assigned_file(
    user: dict[str, Any],
    registry: dict[str, Any],
    permissions: set[str] | None = None,
) -> bool:
    permissions = permissions or set()
    if not (
        user_can_manage_preparation(user)
        or "preparation.assigned.work" in permissions
    ):
        return False
    actor_id = _text(user.get("id"))
    if actor_id == _text(registry.get("responsible_employee_id")):
        return True
    role = _normalized(user.get("role"))
    return role in {"owner", "admin", "operations"} or user.get("is_owner") is True


async def _start_file_execution(
    db: Any,
    *,
    user_id: str,
    registry: dict[str, Any],
    actor: dict[str, Any],
    note: str | None,
    permissions: set[str] | None = None,
) -> dict[str, Any]:
    batch_id = _text(registry.get("batch_id"))
    file_number = _text(registry.get("file_number"))
    if not batch_id:
        raise HTTPException(status_code=409, detail={"code": "file_batch_missing"})
    if not _can_start_assigned_file(actor, registry, permissions):
        raise HTTPException(
            status_code=403,
            detail={"code": "assigned_file_start_permission_required"},
        )
    current_status = _text(registry.get("execution_status")) or "assigned"
    if current_status == "in_progress":
        return registry
    if current_status not in {"assigned", "not_started"}:
        raise HTTPException(
            status_code=409,
            detail={"code": "preparation_file_cannot_start", "status": current_status},
        )
    now = _now()
    actor_id = _text(actor.get("id"))
    actor_name = _text(actor.get("name") or actor.get("email"))
    result = await db[REGISTRY].update_one(
        {
            "user_id": user_id,
            "file_number": file_number,
            "status": "ready",
            "execution_status": {"$in": ["assigned", "not_started", None]},
        },
        {"$set": {
            "execution_status": "in_progress",
            "started_at": now,
            "started_by": actor_id,
            "started_by_name": actor_name,
            "start_note": _text(note) or None,
            "updated_at": now,
        }},
    )
    if not result.modified_count:
        latest = await db[REGISTRY].find_one(
            {"user_id": user_id, "file_number": file_number},
            {"_id": 0},
        )
        if _text((latest or {}).get("execution_status")) == "in_progress":
            return latest or registry
        raise HTTPException(status_code=409, detail={"code": "preparation_file_start_conflict"})
    await db[BATCHES].update_one(
        {"user_id": user_id, "id": batch_id},
        {"$set": {
            "execution_status": "in_progress",
            "started_at": now,
            "started_by": actor_id,
            "updated_at": now,
        }},
    )
    await db[PIECES].update_many(
        {
            "user_id": user_id,
            "batch_id": batch_id,
            "status": PIECE_STATUS_ASSIGNED,
            "$or": [
                {"experiment_archived_at": {"$exists": False}},
                {"experiment_archived_at": None},
            ],
        },
        {"$set": {
            "status": PIECE_STATUS_IN_PROGRESS,
            "execution_status": "in_progress",
            "started_at": now,
            "started_by": actor_id,
            "started_by_name": actor_name,
            "updated_at": now,
        }},
    )
    order_rows = await db[PIECES].find(
        {
            "user_id": user_id,
            "batch_id": batch_id,
            "$or": [
                {"experiment_archived_at": {"$exists": False}},
                {"experiment_archived_at": None},
            ],
        },
        {"_id": 0, "order_number": 1},
    ).to_list(10000)
    order_numbers = sorted({_text(row.get("order_number")) for row in order_rows if _text(row.get("order_number"))})
    if order_numbers:
        await db[WORKFLOWS].update_many(
            {"user_id": user_id, "order_number": {"$in": order_numbers}, "stage": "reviewed"},
            {"$set": {
                "stage": "in_progress",
                "in_progress_at": now,
                "in_progress_by": actor_id,
                "in_progress_by_name": actor_name,
                "preparation_assignment_status": "started",
                "updated_at": now,
            }, "$inc": {"revision": 1}},
        )
    await db[PIECE_EVENTS].insert_one({
        "id": uuid.uuid4().hex,
        "user_id": user_id,
        "batch_id": batch_id,
        "file_number": file_number,
        "event_type": "preparation_file_started",
        "order_numbers": order_numbers,
        "actor_id": actor_id,
        "actor_name": actor_name,
        "note": _text(note) or None,
        "occurred_at": now,
        "mezan_only": True,
        "salla_updated": False,
        "qoyod_updated": False,
    })
    return await db[REGISTRY].find_one(
        {"user_id": user_id, "file_number": file_number},
        {"_id": 0},
    ) or registry


def _piece_public(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if key not in {"_id", "user_id", "image_b64"}}


def _file_public(row: dict[str, Any], counts: dict[str, int]) -> dict[str, Any]:
    return {
        "file_number": _text(row.get("file_number")),
        "batch_id": _text(row.get("batch_id")),
        "file_title": _text(row.get("file_title")),
        "file_name": _text(row.get("file_name")),
        "responsible_employee_id": _text(row.get("responsible_employee_id")),
        "responsible_employee_name": _text(row.get("responsible_employee_name")),
        "execution_status": _text(row.get("execution_status")) or "assigned",
        "registered_at": row.get("registered_at"),
        "started_at": row.get("started_at"),
        "schedule_mode": _text(row.get("schedule_mode")) or "automatic",
        "required_due_at": row.get("required_due_at"),
        "expected_piece_count": int(row.get("allocated_quantity") or 0),
        "piece_registry_status": _text(row.get("piece_registry_status")) or None,
        "piece_registry_last_error": _text(row.get("piece_registry_last_error")) or None,
        "piece_registry_last_error_code": _text(row.get("piece_registry_last_error_code")) or None,
        "piece_registry_last_error_message": _text(row.get("piece_registry_last_error_message")) or None,
        "piece_count": counts.get("total", 0),
        "assigned_count": counts.get(PIECE_STATUS_ASSIGNED, 0),
        "in_progress_count": counts.get(PIECE_STATUS_IN_PROGRESS, 0),
        "ready_count": counts.get(PIECE_STATUS_READY_FOR_RECEIPT, 0),
        "received_count": counts.get(PIECE_STATUS_RECEIVED, 0),
        "blocked_count": counts.get(PIECE_STATUS_BLOCKED, 0),
        "remaining_count": (
            counts.get(PIECE_STATUS_ASSIGNED, 0)
            + counts.get(PIECE_STATUS_IN_PROGRESS, 0)
            + counts.get(PIECE_STATUS_BLOCKED, 0)
        ),
    }


async def _my_work_view(db: Any, *, user_id: str, employee_id: str, limit: int) -> dict[str, Any]:
    # Piece responsibility is authoritative after a manager reassigns a
    # rejected product.  The original PDF registry remains immutable and may
    # still name the first employee, so discover work from pieces first.
    pieces = await db[PIECES].find(
        {
            "user_id": user_id,
            "responsible_employee_id": employee_id,
            "$and": [
                {"$or": [
                    {"experiment_archived_at": {"$exists": False}},
                    {"experiment_archived_at": None},
                ]},
                {"$or": [
                    {"status": {"$ne": PIECE_STATUS_CANCELLED}},
                    {
                        "active_hold_id": {"$exists": True, "$nin": [None, ""]},
                        "status": PIECE_STATUS_CANCELLED,
                    },
                ]},
            ],
        },
        {"_id": 0},
    ).sort("updated_at", -1).limit(20000).to_list(20000)
    batch_ids = sorted({
        _text(row.get("batch_id"))
        for row in pieces
        if _text(row.get("batch_id"))
    })
    files = (
        await db[REGISTRY].find(
            {"user_id": user_id, "status": "ready", "batch_id": {"$in": batch_ids}},
            {"_id": 0},
        ).sort("registered_at", -1).limit(limit).to_list(limit)
        if batch_ids
        else []
    )
    visible_batch_ids = {
        _text(row.get("batch_id")) for row in files if _text(row.get("batch_id"))
    }
    pieces = [
        row for row in pieces if _text(row.get("batch_id")) in visible_batch_ids
    ]
    counts_by_batch: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for piece in pieces:
        batch_id = _text(piece.get("batch_id"))
        status = _text(piece.get("status")) or PIECE_STATUS_ASSIGNED
        counts_by_batch[batch_id]["total"] += 1
        counts_by_batch[batch_id][status] += 1
    return {
        "employee_id": employee_id,
        "summary": {
            "files": len(files),
            "pieces": len(pieces),
            "assigned": sum(1 for row in pieces if row.get("status") == PIECE_STATUS_ASSIGNED),
            "in_progress": sum(1 for row in pieces if row.get("status") == PIECE_STATUS_IN_PROGRESS),
            "ready": sum(1 for row in pieces if row.get("status") == PIECE_STATUS_READY_FOR_RECEIPT),
            "remaining": sum(
                1
                for row in pieces
                if row.get("status") in {PIECE_STATUS_ASSIGNED, PIECE_STATUS_IN_PROGRESS, PIECE_STATUS_BLOCKED}
            ),
        },
        "files": [_file_public(row, counts_by_batch[_text(row.get("batch_id"))]) for row in files],
        "pieces": [_piece_public(row) for row in pieces],
    }


async def _manager_summary(db: Any, *, user_id: str, date: str) -> dict[str, Any]:
    pieces = await db[PIECES].find(
        {
            "user_id": user_id,
            "$or": [
                {"experiment_archived_at": {"$exists": False}},
                {"experiment_archived_at": None},
            ],
        },
        {
            "_id": 0,
            "responsible_employee_id": 1,
            "responsible_employee_name": 1,
            "assignment_date": 1,
            "status": 1,
            "started_at": 1,
            "completed_at": 1,
        },
    ).to_list(50000)
    by_employee: dict[str, dict[str, Any]] = {}
    riyadh_tz = riyadh_now_aware().tzinfo

    def local_date(value: Any) -> str:
        if isinstance(value, datetime):
            return value.astimezone(riyadh_tz).strftime("%Y-%m-%d")
        return ""

    for row in pieces:
        employee_id = _text(row.get("responsible_employee_id"))
        if not employee_id:
            continue
        summary = by_employee.setdefault(employee_id, {
            "employee_id": employee_id,
            "employee_name": _text(row.get("responsible_employee_name")),
            "assigned_on_date": 0,
            "started_on_date": 0,
            "completed_on_date": 0,
            "remaining_current": 0,
            "ready_current": 0,
            "total_current": 0,
        })
        summary["total_current"] += 1
        if _text(row.get("assignment_date")) == date:
            summary["assigned_on_date"] += 1
        if local_date(row.get("started_at")) == date:
            summary["started_on_date"] += 1
        if local_date(row.get("completed_at")) == date:
            summary["completed_on_date"] += 1
        status = _text(row.get("status"))
        if status in {PIECE_STATUS_ASSIGNED, PIECE_STATUS_IN_PROGRESS, PIECE_STATUS_BLOCKED}:
            summary["remaining_current"] += 1
        if status == PIECE_STATUS_READY_FOR_RECEIPT:
            summary["ready_current"] += 1
    return {
        "date": date,
        "items": sorted(
            by_employee.values(),
            key=lambda row: (_normalized(row.get("employee_name")), row["employee_id"]),
        ),
    }


def make_preparation_piece_operations_router(db: Any, current_user: Callable) -> APIRouter:
    router = APIRouter(prefix="/preparation-work-v1", tags=["Preparation Piece Operations"])

    @router.get("/my-work")
    async def my_work(
        limit: int = Query(50, ge=1, le=200),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        context = await _actor_context(db, user)
        if not (
            context["is_owner"]
            or "preparation.assigned.read" in context["permissions"]
            or user_can_manage_preparation(user)
        ):
            raise HTTPException(
                status_code=403,
                detail={"code": "preparation_manage_permission_required"},
            )
        return await _my_work_view(
            db,
            user_id=context["merchant_id"],
            employee_id=context["actor_id"],
            limit=limit,
        )

    @router.get("/manager/summary")
    async def manager_summary(
        date: str | None = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        manager = _require_reviewer(user)
        return await _manager_summary(
            db,
            user_id=_merchant_user_id(manager),
            date=date or riyadh_now_aware().strftime("%Y-%m-%d"),
        )

    @router.post("/files/{file_number}/start")
    async def start_file(
        file_number: str,
        payload: StartPreparationFileRequest,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        context = await _actor_context(db, user)
        user_id = context["merchant_id"]
        registry = await db[REGISTRY].find_one(
            {"user_id": user_id, "file_number": file_number, "status": "ready"},
            {"_id": 0},
        )
        if not registry:
            raise HTTPException(status_code=404, detail={"code": "preparation_file_not_found"})
        updated = await _start_file_execution(
            db,
            user_id=user_id,
            registry=registry,
            actor=user,
            note=payload.note,
            permissions=set(context["permissions"]),
        )
        return {
            "ok": True,
            "file": _file_public(updated, {}),
            "mezan_only": True,
            "salla_updated": False,
            "qoyod_updated": False,
        }

    @router.put("/files/{file_number}/schedule")
    async def update_schedule(
        file_number: str,
        payload: FileSchedulePatchRequest,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        manager = _require_reviewer(user)
        user_id = _merchant_user_id(manager)
        if payload.mode == "required" and payload.required_due_at is None:
            raise HTTPException(status_code=422, detail={"code": "required_due_at_required"})
        now = _now()
        required_due_at = (
            payload.required_due_at.astimezone(timezone.utc)
            if payload.required_due_at
            else None
        )
        result = await db[REGISTRY].update_one(
            {"user_id": user_id, "file_number": file_number, "status": "ready"},
            {"$set": {
                "schedule_mode": payload.mode,
                "required_due_at": required_due_at,
                "schedule_updated_at": now,
                "schedule_updated_by": _text(manager.get("id")),
                "updated_at": now,
            }},
        )
        if not result.modified_count:
            exists = await db[REGISTRY].find_one(
                {"user_id": user_id, "file_number": file_number},
                {"_id": 0, "file_number": 1},
            )
            if not exists:
                raise HTTPException(status_code=404, detail={"code": "preparation_file_not_found"})
        await db[PIECES].update_many(
            {"user_id": user_id, "file_number": file_number},
            {"$set": {
                "schedule_mode": payload.mode,
                "required_due_at": required_due_at,
                "updated_at": now,
            }},
        )
        return {
            "ok": True,
            "file_number": file_number,
            "schedule_mode": payload.mode,
            "required_due_at": required_due_at,
        }

    return router


def install_preparation_piece_operations() -> None:
    """Install piece materialisation and corrected stage semantics once."""
    global _INSTALLED, _ORIGINAL_FINALIZE, _ORIGINAL_RECONCILE_ORDER_STAGE
    if _INSTALLED:
        return
    import preparation_file_registry as registry_module
    import reviewed_preparation_batches as batch_module

    _ORIGINAL_FINALIZE = registry_module._finalize_registry_row
    _ORIGINAL_RECONCILE_ORDER_STAGE = batch_module._reconcile_order_stage

    async def finalize_with_pieces(
        db: Any,
        *,
        user_id: str,
        client_request_id: str,
        actor: dict[str, Any],
    ) -> dict[str, Any]:
        assert _ORIGINAL_FINALIZE is not None
        row = await _ORIGINAL_FINALIZE(
            db,
            user_id=user_id,
            client_request_id=client_request_id,
            actor=actor,
        )
        await materialize_preparation_pieces(db, user_id=user_id, registry=row)
        refreshed = await db[REGISTRY].find_one(
            {"user_id": user_id, "client_request_id": client_request_id},
            {"_id": 0},
        )
        return refreshed or row

    registry_module._finalize_registry_row = finalize_with_pieces
    batch_module._reconcile_order_stage = _assigned_reconcile_order_stage
    _INSTALLED = True


__all__ = [
    "PIECES",
    "PIECE_EVENTS",
    "FileSchedulePatchRequest",
    "StartPreparationFileRequest",
    "build_duration_history",
    "build_piece_documents",
    "ensure_piece_operation_indexes",
    "inherit_required_services",
    "validate_materialized_piece_count",
    "install_preparation_piece_operations",
    "make_preparation_piece_operations_router",
    "materialize_preparation_pieces",
]
