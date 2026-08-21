"""Durable piece operations for preparation files.

Every physical unit committed to a preparation file becomes one work record.
The file is assigned to one preparation employee, required services are
inherited from Product V2 product/option service links, and execution does not
start until the assigned employee (or an authorised manager) starts the file.

Piece execution stays in Mezan.  Two deliberate order-status transitions
are verified in Salla: a fully allocated order moves to ``قيد التنفيذ``, and
an order whose pieces are all ready later moves to ``تم التنفيذ`` so its
configured courier can issue the official AWB.  No Qoyod, supplier, WhatsApp,
or accounting writes are made here.
"""
from __future__ import annotations

import asyncio
import hashlib
import statistics
import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from pymongo import ASCENDING, DESCENDING

from fulfillment_v2_routes import (
    BATCHES as SHIPPING_BATCHES,
    _actor_context,
    _require_permission,
)
from fulfillment_carrier_label import sync_completed_carrier_label
from order_engine.shipping_label_service import ShippingLabelError
from order_review_export_controls import user_can_manage_preparation
from order_review_routes import (
    EVENTS,
    WORKFLOWS,
    _merchant_user_id,
    _normalized,
    _require_reviewer,
    _text,
)
from order_tracking_notes import enforce_stage_instructions
from product_fulfillment_rules import PRODUCT_RESOURCE_BINDINGS
from product_option_cost_routes import BINDINGS, RESOURCES
from reviewed_products_catalog import (
    MAX_REVIEWED_ORDERS,
    PREPARATION_UNIT_ALLOCATIONS,
    load_reviewed_product_context,
)
from reviewed_preparation_batches import BATCHES
from salla_integration.service import SallaError, call_salla
from preparation_file_registry import REGISTRY
from preparation_piece_barcode import (
    parse_preparation_piece_barcode,
    preparation_piece_id,
)
from tz_utils import riyadh_now_aware


PIECES = "mezan_preparation_pieces_v1"
PIECE_EVENTS = "mezan_preparation_piece_events_v1"
DEFAULT_ESTIMATED_DURATION_MINUTES = 2 * 24 * 60
MAX_HISTORY_ROWS = 5000

PIECE_STATUS_ASSIGNED = "assigned"
PIECE_STATUS_IN_PROGRESS = "in_progress"
PIECE_STATUS_READY_FOR_RECEIPT = "ready_for_employee_receipt"
PIECE_STATUS_RECEIVED = "received"
PIECE_STATUS_READY_FOR_ASSEMBLY = "ready_for_assembly"
PIECE_STATUS_BLOCKED = "blocked"
PIECE_STATUS_CANCELLED = "cancelled"
PREPARATION_RECEIPT_PERMISSION = "inventory.preparation.receive"

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


class ReceivePreparationPieceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_request_id: str = Field(min_length=8, max_length=160)


class MarkAssemblyPieceReadyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_request_id: str = Field(min_length=8, max_length=160)


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


def _service_context_key(line: dict[str, Any]) -> str:
    """Identify one order line so option-linked services cannot cross-contaminate.

    A Salla product can appear more than once in the same order with different
    customer options.  Product id alone is therefore not a safe service-plan
    key: the later line would overwrite the earlier line's services.
    """
    order_number = _text(line.get("order_number"))
    order_item_id = _text(line.get("order_item_id"))
    if order_number and order_item_id:
        return f"order:{order_number}:item:{order_item_id}"
    return (
        _text(line.get("group_key"))
        or _text(line.get("product_id"))
    )


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
    """Inherit services while preserving why each service is invoice-eligible."""
    selected_pairs = _selected_spec_pairs(line)
    inherited: dict[str, dict[str, Any]] = {}

    def add(
        resource_id: Any,
        quantity: Any,
        source: str,
        condition=None,
        *,
        customer_selected: bool = False,
        supplier_invoice_required: bool = False,
    ) -> None:
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
            "customer_selected": customer_selected,
            "supplier_invoice_required": supplier_invoice_required,
            "status": "pending",
            "completed_quantity": 0.0,
        }

    for link in product_links:
        permanent_invoice_service = (
            link.get("supplier_invoice_required") is True
        )
        add(
            link.get("resource_id"),
            link.get("quantity"),
            (
                "supplier_receiving_permanent"
                if permanent_invoice_service
                else "product"
            ),
            customer_selected=False,
            supplier_invoice_required=permanent_invoice_service,
        )

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
            customer_selected=True,
            supplier_invoice_required=False,
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
        line_service_context = (
            services_by_product.get(_service_context_key(line))
            or services_by_product.get(product_id)
            or {}
        )
        services = list(line_service_context.get("services") or [])
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
            context[_service_context_key(line)] = {"services": inherit_required_services(
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
    runtime_workflows = await db[WORKFLOWS].find(
        {
            "user_id": user_id,
            "order_number": {"$in": order_numbers},
        },
        {
            "_id": 0,
            "order_number": 1,
            "customer_service_instructions": 1,
            "customer_service_hold_active": 1,
            "customer_service_hold_reason": 1,
            "experiment_run_id": 1,
            "experiment_generation": 1,
            "experiment_mode": 1,
        },
    ).to_list(max(len(order_numbers), 1)) if order_numbers else []
    runtime_by_order = {
        _text(row.get("order_number")): row
        for row in runtime_workflows
        if _text(row.get("order_number"))
    }
    for piece in pieces:
        runtime = runtime_by_order.get(_text(piece.get("order_number"))) or {}
        matching_instructions = []
        for instruction in runtime.get("customer_service_instructions") or []:
            scope = _text(instruction.get("scope")) or "order"
            target_ids = {
                _text(value)
                for value in (
                    list(instruction.get("target_ids") or [])
                    + [instruction.get("target_id")]
                )
                if _text(value)
            }
            if (
                scope == "order"
                or (
                    scope == "item"
                    and _text(piece.get("order_item_id")) in target_ids
                )
                or (
                    scope == "piece"
                    and _text(piece.get("piece_id")) in target_ids
                )
            ):
                matching_instructions.append(instruction)
        if matching_instructions:
            piece["customer_service_instructions"] = matching_instructions
        matching_hold = next((
            instruction
            for instruction in matching_instructions
            if _text(instruction.get("action_type"))
            in {"edit_product", "edit_order", "delete_product", "cancel_order"}
            and _text(instruction.get("status"))
            in {"active", "waiting_customer_service_approval"}
        ), None)
        if runtime.get("customer_service_hold_active") and matching_hold:
            piece.update({
                "customer_service_hold_active": True,
                "customer_service_hold_reason": _text(
                    matching_hold.get("note")
                ) or None,
            })
        if runtime.get("experiment_mode") and _text(runtime.get("experiment_run_id")):
            piece.update({
                "experiment_mode": True,
                "experiment_run_id": _text(runtime.get("experiment_run_id")),
                "experiment_generation": int(runtime.get("experiment_generation") or 1),
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


_IN_PROGRESS_STATUS_NAME = "قيد التنفيذ"
_IN_PROGRESS_STATUS_SLUG = "in_progress"


def _walk_salla_status_dicts(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_salla_status_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_salla_status_dicts(child)


def _salla_in_progress_status_id(response: Any) -> Any:
    """Resolve the store's exact custom ``قيد التنفيذ`` status id."""
    for row in _walk_salla_status_dicts(response):
        if _text(row.get("name")) != _IN_PROGRESS_STATUS_NAME:
            continue
        status_id = row.get("id") or row.get("status_id")
        if status_id not in (None, ""):
            return int(status_id) if _text(status_id).isdigit() else status_id
    return None


def _salla_order_is_in_progress(order: Any) -> bool:
    status = order.get("status") if isinstance(order, dict) else None
    for row in _walk_salla_status_dicts(status):
        if _text(row.get("name")) == _IN_PROGRESS_STATUS_NAME:
            return True
        slug = _normalized(row.get("slug")).replace(" ", "_")
        if slug == _IN_PROGRESS_STATUS_SLUG:
            return True
    return False


async def _sync_salla_in_progress(
    db: Any,
    *,
    user_id: str,
    order: Any,
) -> tuple[str, str | None]:
    """Set and verify the order's exact custom in-progress status in Salla."""
    source = getattr(order, "source", None)
    internal_order_id = (
        _text(getattr(source, "source_order_id", None))
        or _text(getattr(order, "order_id", None))
    )
    if not internal_order_id:
        return "pending", "missing_salla_order_id"

    try:
        current_response = await call_salla(
            db,
            user_id,
            "GET",
            f"/orders/{internal_order_id}",
        )
        current = (
            current_response.get("data")
            if isinstance(current_response, dict)
            else None
        )
        if _salla_order_is_in_progress(current):
            return "sent", None
    except SallaError:
        # Continue to the authoritative status discovery/write attempt.
        pass

    try:
        statuses = await call_salla(db, user_id, "GET", "/orders/statuses")
        status_id = _salla_in_progress_status_id(statuses)
        if status_id is None:
            return "pending", "in_progress_status_not_found"
        await call_salla(
            db,
            user_id,
            "POST",
            f"/orders/{internal_order_id}/status",
            json={"status_id": status_id},
        )
    except SallaError as exc:
        return "pending", f"salla_{exc.status_code}"

    try:
        for attempt in range(6):
            if attempt:
                await asyncio.sleep(0.5)
            response = await call_salla(
                db,
                user_id,
                "GET",
                f"/orders/{internal_order_id}",
            )
            latest = response.get("data") if isinstance(response, dict) else None
            if _salla_order_is_in_progress(latest):
                return "sent", None
    except SallaError as exc:
        return "pending", f"salla_verify_{exc.status_code}"
    return "pending", "salla_in_progress_not_confirmed"


async def _assigned_reconcile_order_stage(
    db: Any,
    *,
    user_id: str,
    order_number: str,
    batch_id: str,
    actor: dict[str, Any],
) -> tuple[bool, int]:
    """Move a fully allocated order to in-progress in Salla and Mezan."""
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
    salla_status_allowed = (
        workflow.get("experiment_mode") is not True
        or workflow.get("salla_status_writes_allowed") is True
    )
    salla_updated = False
    if fully_allocated and salla_status_allowed:
        sync_status, sync_error = await _sync_salla_in_progress(
            db,
            user_id=user_id,
            order=order,
        )
        if sync_status != "sent":
            await db[EVENTS].insert_one({
                "user_id": user_id,
                "order_number": order_number,
                "batch_id": batch_id,
                "event_type": "order_in_progress_salla_sync_failed",
                "error_code": sync_error,
                "occurred_at": now,
                "actor_id": _text(actor.get("id")),
                "mezan_only": True,
                "salla_updated": False,
                "qoyod_updated": False,
            })
            raise RuntimeError(
                f"salla_in_progress_status_sync_failed:{sync_error or 'unknown'}"
            )
        salla_updated = True

    update = {
        "$set": {
            "preparation_progress": {
                "required_quantity": required,
                "allocated_quantity": allocated,
                "remaining_quantity": remaining,
                "updated_at": now,
                "last_batch_id": batch_id,
            },
            "preparation_assignment_status": (
                "assigned" if fully_allocated else "partially_assigned"
            ),
            "updated_at": now,
            "updated_by": _text(actor.get("id")),
        },
        "$addToSet": {"preparation_batch_ids": batch_id},
        "$inc": {"revision": 1},
    }
    if fully_allocated:
        update["$set"]["preparation_fully_allocated_at"] = now
    if fully_allocated and salla_updated:
        update["$set"].update({
            "stage": "in_progress",
            "in_progress_at": now,
            "in_progress_by": _text(actor.get("id")),
            "in_progress_by_name": _text(actor.get("name") or actor.get("email")),
            "salla_status_sync_state": "sent",
            "salla_status_name": _IN_PROGRESS_STATUS_NAME,
            "salla_status_slug": _IN_PROGRESS_STATUS_SLUG,
            "salla_status_synced_at": now,
        })
    await db[WORKFLOWS].update_one(
        {"user_id": user_id, "order_number": order_number, "stage": "reviewed"},
        update,
    )
    if fully_allocated:
        moved = bool(salla_updated)
        await db[EVENTS].insert_one({
            "user_id": user_id,
            "order_number": order_number,
            "batch_id": batch_id,
            "event_type": (
                "order_moved_to_in_progress"
                if moved
                else "order_preparation_fully_assigned"
            ),
            "occurred_at": now,
            "actor_id": _text(actor.get("id")),
            "mezan_only": not moved,
            "salla_updated": moved,
            "qoyod_updated": False,
        })
        return moved, remaining
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
    gate_pieces = await db[PIECES].find(
        {
            "user_id": user_id,
            "batch_id": batch_id,
            "$or": [
                {"experiment_archived_at": {"$exists": False}},
                {"experiment_archived_at": None},
            ],
        },
        {"_id": 0, "piece_id": 1, "order_number": 1, "order_item_id": 1},
    ).to_list(10000)
    for piece in gate_pieces:
        await enforce_stage_instructions(
            db,
            user_id=user_id,
            order_number=_text(piece.get("order_number")),
            order_item_id=_text(piece.get("order_item_id")),
            piece_id=_text(piece.get("piece_id")),
            stage="preparation",
            actor_id=_text(actor.get("id")),
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


def _can_receive_from_preparation(
    user: dict[str, Any],
    context: dict[str, Any],
) -> bool:
    return bool(
        context.get("is_owner")
        or PREPARATION_RECEIPT_PERMISSION in set(context.get("permissions") or [])
        or user_can_manage_preparation(user)
    )


def preparation_receipt_blocker(piece: dict[str, Any]) -> str | None:
    """Return why a piece cannot be handed from preparation to assembly."""
    status = _text(piece.get("status"))
    receipt_status = _text(piece.get("preparation_receipt_status"))
    supplier_dispatch_status = _text(piece.get("supplier_dispatch_status"))
    if (
        receipt_status == "received"
        or status == PIECE_STATUS_READY_FOR_ASSEMBLY
    ):
        return "preparation_piece_already_received"
    if status == PIECE_STATUS_CANCELLED:
        return "preparation_piece_cancelled"
    if piece.get("active_hold_id") or status == PIECE_STATUS_BLOCKED:
        return "preparation_piece_stopped"
    if (
        _text(piece.get("assignment_status")) == "unassigned"
        or not _text(piece.get("responsible_employee_id"))
    ):
        return "preparation_piece_employee_required"
    if _text(piece.get("supplier_receiving_session_id")):
        return "preparation_piece_supplier_receiving_in_progress"
    if _preparation_incomplete_services(piece):
        return "preparation_piece_services_incomplete"
    if supplier_dispatch_status and supplier_dispatch_status != "received":
        return "preparation_piece_supplier_receipt_required"
    if status == PIECE_STATUS_ASSIGNED:
        return "preparation_piece_not_started"
    if status not in {
        PIECE_STATUS_IN_PROGRESS,
        PIECE_STATUS_READY_FOR_RECEIPT,
        PIECE_STATUS_RECEIVED,
    }:
        return "preparation_piece_not_ready_for_receipt"
    return None


def _preparation_service_is_complete(service: dict[str, Any]) -> bool:
    """Use the same completion contract as supplier receiving without imports.

    Keeping this check local avoids a circular import while still accepting
    both the durable ``completed`` status and older quantity-complete records.
    """
    if _text(service.get("status")).casefold() == "completed":
        return True
    try:
        required = float(service.get("required_quantity") or 1)
    except (TypeError, ValueError, OverflowError):
        required = 1.0
    if required <= 0:
        required = 1.0
    try:
        completed = float(service.get("completed_quantity") or 0)
    except (TypeError, ValueError, OverflowError):
        completed = 0.0
    return completed >= required


def _preparation_incomplete_services(
    piece: dict[str, Any],
) -> list[dict[str, Any]]:
    """Return unfinished required services from the authoritative snapshot."""
    return [
        dict(service)
        for service in piece.get("services") or []
        if isinstance(service, dict)
        and not _preparation_service_is_complete(service)
    ]


def _preparation_receipt_specs(piece: dict[str, Any]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def add(name: Any, value: Any) -> None:
        label = _text(name)
        display_value = _text(value)
        key = (_normalized(label), _normalized(display_value))
        if not label or not display_value or key in seen:
            return
        seen.add(key)
        rows.append({"name": label, "value": display_value})

    for row in piece.get("specifications_snapshot") or []:
        if isinstance(row, dict):
            add(
                row.get("name") or row.get("label") or row.get("title"),
                row.get("value") or row.get("answer") or row.get("text"),
            )
    for name, value in (piece.get("product_options_snapshot") or {}).items():
        add(name, value)
    return rows


def _preparation_receipt_piece_public(
    piece: dict[str, Any],
    *,
    matched_piece_id: str = "",
) -> dict[str, Any]:
    blocker = preparation_receipt_blocker(piece)
    status = _text(piece.get("status"))
    status_labels = {
        PIECE_STATUS_ASSIGNED: "لم يبدأ التجهيز",
        PIECE_STATUS_IN_PROGRESS: "قيد التجهيز",
        PIECE_STATUS_READY_FOR_RECEIPT: "بانتظار استلام المورد",
        PIECE_STATUS_RECEIVED: "تم استلام المورد",
        PIECE_STATUS_READY_FOR_ASSEMBLY: "جاهز للتجميع والعنونة",
        PIECE_STATUS_BLOCKED: "متوقف",
        PIECE_STATUS_CANCELLED: "ملغى",
    }
    image_url = (
        _text(piece.get("selected_image_url"))
        or _text(piece.get("resolved_image_url"))
        or _text(piece.get("image_url"))
        or None
    )
    piece_id = _text(piece.get("piece_id") or piece.get("id"))
    incomplete_services = _preparation_incomplete_services(piece)
    return {
        "piece_id": piece_id,
        "order_number": _text(piece.get("order_number")),
        "order_item_id": _text(piece.get("order_item_id")) or None,
        "unit_index": piece.get("unit_index"),
        "product_id": _text(piece.get("product_id")) or None,
        "product_name": _text(piece.get("product_name")) or "منتج",
        "sku": _text(piece.get("sku")) or None,
        "image_url": image_url,
        "specifications": _preparation_receipt_specs(piece),
        "responsible_employee_id": _text(piece.get("responsible_employee_id")) or None,
        "responsible_employee_name": _text(piece.get("responsible_employee_name")) or "—",
        "status": status,
        "status_label": status_labels.get(status, status or "غير معروف"),
        "can_receive": blocker is None,
        "blocker_code": blocker,
        "remaining_service_count": len(incomplete_services),
        "pending_service_names": [
            _text(service.get("service_name") or service.get("service_code"))
            for service in incomplete_services
            if _text(service.get("service_name") or service.get("service_code"))
        ],
        "search_match": bool(matched_piece_id and piece_id == matched_piece_id),
        "preparation_received_at": piece.get("preparation_received_at"),
        "preparation_received_by_name": _text(
            piece.get("preparation_received_by_name")
        ) or None,
        "customer_service_instructions": list(
            piece.get("customer_service_instructions") or []
        ),
    }


def _preparation_receipt_order_number(value: Any) -> str:
    raw = _text(value).removeprefix("#").strip()
    if raw.casefold().startswith("طلب"):
        raw = raw[3:].strip().removeprefix("#").strip()
    return raw


async def _preparation_receipt_search(
    db: Any,
    *,
    user_id: str,
    query: str,
) -> dict[str, Any]:
    matched_piece_id = parse_preparation_piece_barcode(query) or ""
    order_number = ""
    if matched_piece_id:
        matched_piece = await db[PIECES].find_one(
            {"user_id": user_id, "piece_id": matched_piece_id},
            {"_id": 0, "image_b64": 0},
        )
        if not matched_piece:
            raise HTTPException(
                status_code=404,
                detail={"code": "preparation_piece_not_found"},
            )
        order_number = _text(matched_piece.get("order_number"))
    else:
        order_number = _preparation_receipt_order_number(query)
    if not order_number:
        raise HTTPException(
            status_code=422,
            detail={"code": "preparation_receipt_search_required"},
        )

    pieces = await db[PIECES].find(
        {
            "user_id": user_id,
            "order_number": order_number,
            "$or": [
                {"experiment_archived_at": {"$exists": False}},
                {"experiment_archived_at": None},
            ],
        },
        {"_id": 0, "image_b64": 0},
    ).to_list(1000)
    if not pieces:
        raise HTTPException(
            status_code=404,
            detail={"code": "preparation_receipt_order_not_found"},
        )

    rows = [
        _preparation_receipt_piece_public(
            piece,
            matched_piece_id=matched_piece_id,
        )
        for piece in pieces
        if _text(piece.get("status")) != PIECE_STATUS_CANCELLED
    ]
    rows.sort(key=lambda row: (
        0 if row["search_match"] else 1,
        0 if row["can_receive"] else 1,
        int(row.get("unit_index") or 0),
        _text(row.get("piece_id")),
    ))
    return {
        "order_number": order_number,
        "matched_piece_id": matched_piece_id or None,
        "pieces": rows,
        "summary": {
            "total": len(rows),
            "ready_to_receive": sum(1 for row in rows if row["can_receive"]),
            "received": sum(
                1
                for row in rows
                if row["status"] == PIECE_STATUS_READY_FOR_ASSEMBLY
            ),
        },
    }


def _piece_has_completed_preparation_receipt(piece: dict[str, Any]) -> bool:
    return bool(
        _text(piece.get("preparation_receipt_status")) == "received"
        or _text(piece.get("status")) == PIECE_STATUS_READY_FOR_ASSEMBLY
    )


async def _refresh_preparation_receipt_progress(
    db: Any,
    *,
    user_id: str,
    piece: dict[str, Any],
    actor_id: str,
    actor_name: str,
    now: datetime,
) -> dict[str, Any]:
    order_number = _text(piece.get("order_number"))
    order_pieces = await db[PIECES].find(
        {
            "user_id": user_id,
            "order_number": order_number,
            "status": {"$ne": PIECE_STATUS_CANCELLED},
            "$or": [
                {"experiment_archived_at": {"$exists": False}},
                {"experiment_archived_at": None},
            ],
        },
        {"_id": 0},
    ).to_list(10000)
    received_count = sum(
        1 for row in order_pieces if _piece_has_completed_preparation_receipt(row)
    )
    total_count = len(order_pieces)
    completed = bool(total_count and received_count == total_count)
    workflow_patch: dict[str, Any] = {
        "preparation_receipt_status": "completed" if completed else "partial",
        "preparation_received_piece_count": received_count,
        "preparation_piece_count": total_count,
        "preparation_receipt_updated_at": now,
        "updated_at": now,
        "updated_by": actor_id,
    }
    if completed:
        workflow_patch.update({
            "stage": "ready_to_ship",
            "ready_to_ship_at": now,
            "ready_to_ship_source": "preparation_receipt",
            "preparation_completed_at": now,
            "preparation_completed_by": actor_id,
            "preparation_completed_by_name": actor_name,
        })
    await db[WORKFLOWS].update_one(
        {
            "user_id": user_id,
            "order_number": order_number,
            "stage": {"$nin": ["completed", "delivering", "delivered"]},
        },
        {"$set": workflow_patch, "$inc": {"revision": 1}},
    )

    batch_id = _text(piece.get("batch_id"))
    file_number = _text(piece.get("file_number"))
    file_identity: dict[str, Any] = {"user_id": user_id}
    if batch_id:
        file_identity["batch_id"] = batch_id
    elif file_number:
        file_identity["file_number"] = file_number
    else:
        return {
            "order_number": order_number,
            "received_count": received_count,
            "total_count": total_count,
            "order_ready_for_assembly": completed,
            "file_completed": False,
        }
    file_pieces = await db[PIECES].find(
        {
            **file_identity,
            "status": {"$ne": PIECE_STATUS_CANCELLED},
        },
        {"_id": 0, "status": 1, "preparation_receipt_status": 1},
    ).to_list(10000)
    file_received_count = sum(
        1 for row in file_pieces if _piece_has_completed_preparation_receipt(row)
    )
    file_completed = bool(
        file_pieces and file_received_count == len(file_pieces)
    )
    file_patch: dict[str, Any] = {
        "preparation_received_count": file_received_count,
        "preparation_receipt_updated_at": now,
        "updated_at": now,
    }
    if file_completed:
        file_patch.update({
            "execution_status": "completed",
            "completed_at": now,
            "completed_by": actor_id,
            "completed_by_name": actor_name,
        })
    registry_identity: dict[str, Any] = {"user_id": user_id}
    if batch_id:
        registry_identity["batch_id"] = batch_id
    else:
        registry_identity["file_number"] = file_number
    await db[REGISTRY].update_one(registry_identity, {"$set": file_patch})
    return {
        "order_number": order_number,
        "received_count": received_count,
        "total_count": total_count,
        "order_ready_for_assembly": completed,
        "file_completed": file_completed,
    }


async def _receive_preparation_piece(
    db: Any,
    *,
    user_id: str,
    piece_id: str,
    client_request_id: str,
    actor_id: str,
    actor_name: str,
) -> dict[str, Any]:
    normalized_piece_id = _text(piece_id).lower()
    piece = await db[PIECES].find_one(
        {
            "user_id": user_id,
            "$or": [
                {"piece_id": normalized_piece_id},
                {"id": normalized_piece_id},
            ],
        },
        {"_id": 0},
    )
    if not piece:
        raise HTTPException(
            status_code=404,
            detail={"code": "preparation_piece_not_found"},
        )

    if _piece_has_completed_preparation_receipt(piece):
        progress = await _refresh_preparation_receipt_progress(
            db,
            user_id=user_id,
            piece=piece,
            actor_id=actor_id,
            actor_name=actor_name,
            now=_now(),
        )
        return {
            "ok": True,
            "idempotent": True,
            "piece": _preparation_receipt_piece_public(piece),
            "progress": progress,
        }

    blocker = preparation_receipt_blocker(piece)
    if blocker:
        detail: dict[str, Any] = {"code": blocker}
        if blocker == "preparation_piece_services_incomplete":
            incomplete_services = _preparation_incomplete_services(piece)
            detail.update({
                "remaining_service_count": len(incomplete_services),
                "pending_service_names": [
                    _text(
                        service.get("service_name")
                        or service.get("service_code")
                    )
                    for service in incomplete_services
                    if _text(
                        service.get("service_name")
                        or service.get("service_code")
                    )
                ],
            })
        raise HTTPException(status_code=409, detail=detail)
    await enforce_stage_instructions(
        db,
        user_id=user_id,
        order_number=_text(piece.get("order_number")),
        order_item_id=_text(piece.get("order_item_id")),
        piece_id=normalized_piece_id,
        stage="preparation_receiving",
        actor_id=actor_id,
    )

    now = _now()
    update_result = await db[PIECES].update_one(
        {
            "user_id": user_id,
            "piece_id": normalized_piece_id,
            "status": _text(piece.get("status")),
            "preparation_receipt_status": {"$ne": "received"},
        },
        {"$set": {
            "status": PIECE_STATUS_READY_FOR_ASSEMBLY,
            "execution_status": PIECE_STATUS_READY_FOR_ASSEMBLY,
            "preparation_receipt_status": "received",
            "preparation_receipt_client_request_id": client_request_id,
            "preparation_received_at": now,
            "preparation_received_by": actor_id,
            "preparation_received_by_name": actor_name,
            "branch_handoff_at": now,
            "branch_handoff_by": actor_id,
            "branch_handoff_by_name": actor_name,
            "branch_handoff_status": "received_by_branch",
            "preparation_employee_custody_status": "handed_to_branch",
            "completed_at": now,
            "updated_at": now,
            "mezan_only": True,
            "salla_updated": False,
            "qoyod_updated": False,
        }},
    )
    if not update_result.modified_count:
        latest = await db[PIECES].find_one(
            {"user_id": user_id, "piece_id": normalized_piece_id},
            {"_id": 0},
        )
        if latest and _piece_has_completed_preparation_receipt(latest):
            progress = await _refresh_preparation_receipt_progress(
                db,
                user_id=user_id,
                piece=latest,
                actor_id=actor_id,
                actor_name=actor_name,
                now=now,
            )
            return {
                "ok": True,
                "idempotent": True,
                "piece": _preparation_receipt_piece_public(latest),
                "progress": progress,
            }
        raise HTTPException(
            status_code=409,
            detail={"code": "preparation_piece_receipt_conflict"},
        )

    updated = await db[PIECES].find_one(
        {"user_id": user_id, "piece_id": normalized_piece_id},
        {"_id": 0},
    ) or {**piece, "status": PIECE_STATUS_READY_FOR_ASSEMBLY}
    await db[PIECE_EVENTS].insert_one({
        "id": uuid.uuid4().hex,
        "user_id": user_id,
        "piece_id": normalized_piece_id,
        "batch_id": _text(piece.get("batch_id")) or None,
        "file_number": _text(piece.get("file_number")) or None,
        "order_number": _text(piece.get("order_number")),
        "event_type": "preparation_piece_received_for_assembly",
        "client_request_id": client_request_id,
        "actor_id": actor_id,
        "actor_name": actor_name,
        "occurred_at": now,
        "mezan_only": True,
        "salla_updated": False,
        "qoyod_updated": False,
    })
    progress = await _refresh_preparation_receipt_progress(
        db,
        user_id=user_id,
        piece=updated,
        actor_id=actor_id,
        actor_name=actor_name,
        now=now,
    )
    return {
        "ok": True,
        "idempotent": False,
        "piece": _preparation_receipt_piece_public(updated),
        "progress": progress,
    }


def assembly_piece_blocker(piece: dict[str, Any]) -> str | None:
    """Return why a received piece cannot be completed in assembly."""
    if _text(piece.get("assembly_status")) == "ready":
        return "assembly_piece_already_ready"
    if _text(piece.get("status")) != PIECE_STATUS_READY_FOR_ASSEMBLY:
        return "assembly_piece_preparation_receipt_required"
    if piece.get("active_hold_id"):
        return "assembly_piece_stopped"
    return None


def _assembly_piece_public(
    piece: dict[str, Any],
    *,
    matched_piece_id: str = "",
) -> dict[str, Any]:
    piece_id = _text(piece.get("piece_id") or piece.get("id"))
    assembly_ready = _text(piece.get("assembly_status")) == "ready"
    blocker = assembly_piece_blocker(piece)
    services = []
    for service in piece.get("services") or []:
        if not isinstance(service, dict):
            continue
        name = _text(service.get("service_name") or service.get("name"))
        if not name:
            continue
        services.append({
            "name": name,
            "status": _text(service.get("status")) or None,
        })
    return {
        **_preparation_receipt_piece_public(
            piece,
            matched_piece_id=matched_piece_id,
        ),
        "piece_id": piece_id,
        "services": services,
        "assembly_ready": assembly_ready,
        "assembly_status": "ready" if assembly_ready else "pending",
        "assembly_ready_at": piece.get("assembly_ready_at"),
        "assembly_ready_by_name": (
            _text(piece.get("assembly_ready_by_name")) or None
        ),
        "can_mark_ready": blocker is None,
        "assembly_blocker_code": blocker,
        "search_match": bool(
            matched_piece_id and piece_id == matched_piece_id
        ),
    }


def _assembly_batch_id(user_id: str, order_number: str) -> str:
    digest = hashlib.sha256(
        f"{user_id}:{order_number}".encode("utf-8")
    ).hexdigest()[:24]
    return f"ship_assembly_{digest}"


async def _assembly_progress(
    db: Any,
    *,
    user_id: str,
    order_number: str,
    actor_id: str,
    actor_name: str,
    now: datetime,
) -> dict[str, Any]:
    pieces = await db[PIECES].find(
        {
            "user_id": user_id,
            "order_number": order_number,
            "status": {"$ne": PIECE_STATUS_CANCELLED},
            "$or": [
                {"experiment_archived_at": {"$exists": False}},
                {"experiment_archived_at": None},
            ],
        },
        {"_id": 0, "piece_id": 1, "assembly_status": 1},
    ).to_list(10000)
    total_count = len(pieces)
    ready_count = sum(
        1 for piece in pieces if _text(piece.get("assembly_status")) == "ready"
    )
    completed = bool(total_count and total_count == ready_count)
    workflow = await db[WORKFLOWS].find_one(
        {"user_id": user_id, "order_number": order_number},
        {"_id": 0},
    ) or {}
    batch_id = _text(
        workflow.get("shipping_print_batch_id")
    )
    workflow_patch: dict[str, Any] = {
        "assembly_status": "completed" if completed else "in_progress",
        "assembly_ready_piece_count": ready_count,
        "assembly_piece_count": total_count,
        "assembly_updated_at": now,
        "updated_at": now,
    }
    if completed:
        # One completed order gets one deterministic shipment file. Never
        # reuse a legacy multi-order claim batch because the button says
        # "طباعة الشحنة" for this exact order.
        batch_id = batch_id or _assembly_batch_id(user_id, order_number)
        warehouse_ids = sorted({
            _text(value)
            for value in (
                (workflow.get("fulfillment_decision") or {}).get(
                    "warehouse_ids"
                )
                or []
            )
            if _text(value)
        })
        await db[SHIPPING_BATCHES].update_one(
            {"id": batch_id, "user_id": user_id},
            {"$setOnInsert": {
                "id": batch_id,
                "user_id": user_id,
                "status": "claimed",
                "source": "assembly_completion",
                "order_numbers": [order_number],
                "warehouse_ids": warehouse_ids,
                "warehouse_resolution_sources": [
                    "inventory_location" if warehouse_ids
                    else "preparation_receipt"
                ],
                "claimed_by": actor_id,
                "claimed_by_name": actor_name,
                "claimed_at": now,
                "print_count": 0,
                "created_at": now,
                "updated_at": now,
            }},
            upsert=True,
        )
        workflow_patch.update({
            "stage": "completed",
            "completed_at": now,
            "completed_by": actor_id,
            "completed_by_name": actor_name,
            "assembly_completed_at": now,
            "assembly_completed_by": actor_id,
            "assembly_completed_by_name": actor_name,
            "shipping_print_batch_id": batch_id,
            "claim_batch_id": batch_id,
            "claimed_by": actor_id,
            "claimed_by_name": actor_name,
            "claimed_at": workflow.get("claimed_at") or now,
        })
    await db[WORKFLOWS].update_one(
        {
            "user_id": user_id,
            "order_number": order_number,
            "stage": {"$in": ["ready_to_ship", "completed"]},
        },
        {"$set": workflow_patch, "$inc": {"revision": 1}},
    )
    return {
        "order_number": order_number,
        "ready_count": ready_count,
        "total_count": total_count,
        "order_completed": completed,
        "stage": "completed" if completed else "ready_to_ship",
        "print_batch_id": batch_id or None,
    }


async def _assembly_search(
    db: Any,
    *,
    user_id: str,
    query: str,
) -> dict[str, Any]:
    matched_piece_id = parse_preparation_piece_barcode(query) or ""
    order_number = ""
    if matched_piece_id:
        matched_piece = await db[PIECES].find_one(
            {"user_id": user_id, "piece_id": matched_piece_id},
            {"_id": 0, "order_number": 1},
        )
        if not matched_piece:
            raise HTTPException(
                status_code=404,
                detail={"code": "assembly_piece_not_found"},
            )
        order_number = _text(matched_piece.get("order_number"))
    else:
        order_number = _preparation_receipt_order_number(query)
    if not order_number:
        raise HTTPException(
            status_code=422,
            detail={"code": "assembly_search_required"},
        )
    workflow = await db[WORKFLOWS].find_one(
        {
            "user_id": user_id,
            "order_number": order_number,
            "$or": [
                {"stage": "ready_to_ship"},
                {"stage": "completed", "assembly_status": "completed"},
            ],
        },
        {"_id": 0},
    )
    if not workflow:
        raise HTTPException(
            status_code=409,
            detail={"code": "assembly_order_not_ready"},
        )
    pieces = await db[PIECES].find(
        {
            "user_id": user_id,
            "order_number": order_number,
            "status": {"$ne": PIECE_STATUS_CANCELLED},
            "$or": [
                {"experiment_archived_at": {"$exists": False}},
                {"experiment_archived_at": None},
            ],
        },
        {"_id": 0, "image_b64": 0},
    ).to_list(1000)
    if not pieces:
        raise HTTPException(
            status_code=404,
            detail={"code": "assembly_order_products_not_found"},
        )
    rows = [
        _assembly_piece_public(
            piece,
            matched_piece_id=matched_piece_id,
        )
        for piece in pieces
    ]
    rows.sort(key=lambda row: (
        0 if row["search_match"] else 1,
        0 if not row["assembly_ready"] else 1,
        int(row.get("unit_index") or 0),
        _text(row.get("piece_id")),
    ))
    ready_count = sum(1 for row in rows if row["assembly_ready"])
    completed = bool(rows and ready_count == len(rows))
    return {
        "order_number": order_number,
        "stage": _text(workflow.get("stage")),
        "matched_piece_id": matched_piece_id or None,
        "print_batch_id": _text(
            workflow.get("shipping_print_batch_id")
            or workflow.get("claim_batch_id")
        ) or None,
        "pieces": rows,
        "summary": {
            "total": len(rows),
            "ready": ready_count,
            "remaining": len(rows) - ready_count,
            "all_ready": completed,
        },
    }


async def _mark_assembly_piece_ready(
    db: Any,
    *,
    user_id: str,
    piece_id: str,
    client_request_id: str,
    actor_id: str,
    actor_name: str,
) -> dict[str, Any]:
    normalized_piece_id = _text(piece_id).lower()
    piece = await db[PIECES].find_one(
        {
            "user_id": user_id,
            "$or": [
                {"piece_id": normalized_piece_id},
                {"id": normalized_piece_id},
            ],
        },
        {"_id": 0},
    )
    if not piece:
        raise HTTPException(
            status_code=404,
            detail={"code": "assembly_piece_not_found"},
        )
    order_number = _text(piece.get("order_number"))
    workflow = await db[WORKFLOWS].find_one(
        {
            "user_id": user_id,
            "order_number": order_number,
            "stage": {"$in": ["ready_to_ship", "completed"]},
        },
        {"_id": 0, "stage": 1, "assembly_status": 1},
    )
    if not workflow or (
        _text(workflow.get("stage")) == "completed"
        and _text(workflow.get("assembly_status")) != "completed"
    ):
        raise HTTPException(
            status_code=409,
            detail={"code": "assembly_order_not_ready"},
        )
    now = _now()
    if _text(piece.get("assembly_status")) == "ready":
        progress = await _assembly_progress(
            db,
            user_id=user_id,
            order_number=order_number,
            actor_id=actor_id,
            actor_name=actor_name,
            now=now,
        )
        return {
            "ok": True,
            "idempotent": True,
            "piece": _assembly_piece_public(piece),
            "progress": progress,
        }
    blocker = assembly_piece_blocker(piece)
    if blocker:
        raise HTTPException(status_code=409, detail={"code": blocker})
    await enforce_stage_instructions(
        db,
        user_id=user_id,
        order_number=order_number,
        order_item_id=_text(piece.get("order_item_id")),
        piece_id=normalized_piece_id,
        stage="assembly_labeling",
        actor_id=actor_id,
    )
    result = await db[PIECES].update_one(
        {
            "user_id": user_id,
            "piece_id": normalized_piece_id,
            "status": PIECE_STATUS_READY_FOR_ASSEMBLY,
            "assembly_status": {"$ne": "ready"},
        },
        {"$set": {
            "assembly_status": "ready",
            "assembly_ready_at": now,
            "assembly_ready_by": actor_id,
            "assembly_ready_by_name": actor_name,
            "assembly_client_request_id": client_request_id,
            "updated_at": now,
            "mezan_only": True,
            "salla_updated": False,
            "qoyod_updated": False,
        }},
    )
    if not result.modified_count:
        latest = await db[PIECES].find_one(
            {"user_id": user_id, "piece_id": normalized_piece_id},
            {"_id": 0},
        )
        if latest and _text(latest.get("assembly_status")) == "ready":
            progress = await _assembly_progress(
                db,
                user_id=user_id,
                order_number=order_number,
                actor_id=actor_id,
                actor_name=actor_name,
                now=now,
            )
            return {
                "ok": True,
                "idempotent": True,
                "piece": _assembly_piece_public(latest),
                "progress": progress,
            }
        raise HTTPException(
            status_code=409,
            detail={"code": "assembly_piece_ready_conflict"},
        )
    updated = await db[PIECES].find_one(
        {"user_id": user_id, "piece_id": normalized_piece_id},
        {"_id": 0},
    ) or {**piece, "assembly_status": "ready", "assembly_ready_at": now}
    await db[PIECE_EVENTS].insert_one({
        "id": uuid.uuid4().hex,
        "user_id": user_id,
        "piece_id": normalized_piece_id,
        "batch_id": _text(piece.get("batch_id")) or None,
        "file_number": _text(piece.get("file_number")) or None,
        "order_number": order_number,
        "event_type": "assembly_piece_marked_ready",
        "client_request_id": client_request_id,
        "actor_id": actor_id,
        "actor_name": actor_name,
        "occurred_at": now,
        "mezan_only": True,
        "salla_updated": False,
        "qoyod_updated": False,
    })
    progress = await _assembly_progress(
        db,
        user_id=user_id,
        order_number=order_number,
        actor_id=actor_id,
        actor_name=actor_name,
        now=now,
    )
    return {
        "ok": True,
        "idempotent": False,
        "piece": _assembly_piece_public(updated),
        "progress": progress,
    }


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
        "assembly_ready_count": counts.get(PIECE_STATUS_READY_FOR_ASSEMBLY, 0),
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
            "preparation_receipt_status": {"$ne": "received"},
            "$and": [
                {"$or": [
                    {"experiment_archived_at": {"$exists": False}},
                    {"experiment_archived_at": None},
                ]},
                {"$or": [
                    {"status": {"$nin": [
                        PIECE_STATUS_CANCELLED,
                        PIECE_STATUS_READY_FOR_ASSEMBLY,
                    ]}},
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

    @router.get("/receiving/search")
    async def search_preparation_receipt(
        q: str = Query(min_length=1, max_length=160),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        context = await _actor_context(db, user)
        if not _can_receive_from_preparation(user, context):
            raise HTTPException(
                status_code=403,
                detail={"code": "preparation_receipt_permission_required"},
            )
        await ensure_piece_operation_indexes(db)
        return await _preparation_receipt_search(
            db,
            user_id=context["merchant_id"],
            query=q,
        )

    @router.post("/receiving/pieces/{piece_id}/receive")
    async def receive_preparation_piece(
        piece_id: str,
        payload: ReceivePreparationPieceRequest,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        context = await _actor_context(db, user)
        if not _can_receive_from_preparation(user, context):
            raise HTTPException(
                status_code=403,
                detail={"code": "preparation_receipt_permission_required"},
            )
        actor_name = _text(user.get("name") or user.get("email")) or "مستخدم ميزان"
        return await _receive_preparation_piece(
            db,
            user_id=context["merchant_id"],
            piece_id=piece_id,
            client_request_id=payload.client_request_id,
            actor_id=context["actor_id"],
            actor_name=actor_name,
        )

    @router.get("/assembly/search")
    async def search_assembly_order(
        q: str = Query(min_length=1, max_length=160),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        context = await _actor_context(db, user)
        _require_permission(
            context,
            "fulfillment.ready.read",
            responsibility="instant_ready",
        )
        await ensure_piece_operation_indexes(db)
        return await _assembly_search(
            db,
            user_id=context["merchant_id"],
            query=q,
        )

    @router.post("/assembly/pieces/{piece_id}/ready")
    async def mark_assembly_piece_ready(
        piece_id: str,
        payload: MarkAssemblyPieceReadyRequest,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        context = await _actor_context(db, user)
        _require_permission(
            context,
            "fulfillment.pack.confirm",
            responsibility="packing",
        )
        actor_name = _text(user.get("name") or user.get("email")) or "مستخدم ميزان"
        response = await _mark_assembly_piece_ready(
            db,
            user_id=context["merchant_id"],
            piece_id=piece_id,
            client_request_id=payload.client_request_id,
            actor_id=context["actor_id"],
            actor_name=actor_name,
        )
        if (response.get("progress") or {}).get("order_completed"):
            order_number = _text(
                (response.get("progress") or {}).get("order_number")
            )
            try:
                response["carrier_label"] = await sync_completed_carrier_label(
                    db,
                    user_id=context["merchant_id"],
                    order_number=order_number,
                    actor_id=context["actor_id"],
                    actor_name=actor_name,
                    action="issue",
                )
            except ShippingLabelError as exc:
                # Product completion is durable even when Salla/iMile is
                # temporarily unavailable.  The completed-order card exposes
                # an explicit retry without asking the employee to redo work.
                response["carrier_label"] = {
                    "ok": False,
                    "ready": False,
                    "order_status_completed": False,
                    "error_code": exc.code,
                    "message": str(exc),
                }
        return response

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
    "ReceivePreparationPieceRequest",
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
