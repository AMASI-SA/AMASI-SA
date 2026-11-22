"""Small employee directory over current unfinished physical preparation units.

Mongo groups units before they cross the read boundary. Only stage/service
quantity facts and bounded current-order/proof projections are used; no products,
images, customer options, dispatch history, workspace hydration or writes.
"""
from __future__ import annotations

import asyncio
import re
from collections import defaultdict
from typing import Any

from fastapi import HTTPException
from pymongo.errors import PyMongoError

from assembly_board_read import projected_order
from order_engine.repository import _NATIVE_PAYMENT_FIELDS, _STATUS_PATTERNS
from order_engine.service import _provider_status_native
from review_local_policy import (
    LOCAL_COMPLETION_MODE, WORKFLOWS, is_known_review_mode, load_local_review_workflows,
)
from supplier_dispatch_waiting_policy import order_waiting_fields


PIECES = "mezan_preparation_pieces_v1"
BATCH_SIZE = 100
READ_TIMEOUT_SECONDS = 8
RAW = "raw_by_source.salla_direct"
SOURCE_PROJECTION = {
    "_id": 0, **{field: 1 for field in (
        "user_id", "order_number", "order_date", "order_status", "order_status_slug",
        "salla_order_id", "g47_salla_snapshot", "last_salla_direct_status_source_updated_at",
        "payment_method", "paid_amount", "remaining_amount", "has_remaining_amount", "payment_collection_status",
    )},
    **{f"{RAW}.{field}": 1 for field in (
        "id", "reference_id", "order_number", "date", "created_at", "order_date",
        "updated_at", "date_updated", "fetched_at", "received_at", "status_slug", *_NATIVE_PAYMENT_FIELDS,
    )},
}
WORKFLOW_PROJECTION = {"_id": 0, **{field: 1 for field in (
    "user_id", "order_number", "stage", "completion_mode", "review_completion_operation_id",
)}}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _count_pipeline(owner_id: str, account_ids: list[str]) -> list[dict]:
    # The existing owner/employee/status index narrows active assignment first.
    # Ordinary fully received historical rows do not enter aggregation at all.
    query = {
        "user_id": owner_id, "responsible_employee_id": {"$in": account_ids},
        "experiment_archived_at": None, "virtual_kind": None,
        "assignment_status": {"$nin": ["unassigned", "unassigned_after_rejection"]},
        "preparation_receipt_status": {"$ne": "received"}, "assembly_status": {"$ne": "ready"},
        "branch_handoff_at": {"$in": [None, re.compile(r"^\s*$")]},
        "$or": [
            {"status": {"$in": ["assigned", "in_progress", "blocked", "ready_for_employee_receipt", "ready_for_receipt"]}},
            {"status": "received", "$or": [
                {"supplier_dispatch_status": {"$ne": "received"}},
                {"received_at": {"$in": [None, ""]}},
                {"services": {"$elemMatch": {"status": {"$not": re.compile(r"^\s*completed\s*$", re.I)}}}},
            ]},
        ],
    }
    fields = {field: {"$ifNull": [f"${field}", ""]} for field in (
        "responsible_employee_id", "order_number", "status", "supplier_dispatch_status", "supplier_receiving_session_id",
    )}
    fields["received"] = {"$cond": [{"$ifNull": ["$received_at", False]}, True, False]}
    fields["services"] = {"$map": {
        "input": {"$ifNull": ["$services", []]}, "as": "service",
        "in": {field: {"$ifNull": [f"$$service.{field}", None]} for field in (
            "status", "required_quantity", "completed_quantity",
        )},
    }}
    return [
        {"$match": query}, {"$project": {"_id": 0, **fields}},
        {"$group": {"_id": {field: f"${field}" for field in fields}, "count": {"$sum": 1}}},
    ]


def _unfinished_phase(group: dict[str, Any]) -> str | None:
    from preparation_piece_operations import _preparation_incomplete_services

    dispatch = _text(group.get("supplier_dispatch_status"))
    services = group.get("services") or []
    pending = _preparation_incomplete_services({"services": services})
    if dispatch == "received" and group.get("received") and not pending:
        return None  # Physical product and all services are prepared; custody is separate.
    if dispatch == "partial_received" and services and not pending:
        return None  # Legacy quantity-complete service records use the same predicate.
    if (dispatch or _text(group.get("supplier_receiving_session_id"))
            or group.get("status") in {"received", "ready_for_employee_receipt", "ready_for_receipt"}):
        return "underway"
    # Held/blocked assigned work remains outstanding; dispatch permission is not
    # inferred here. Current review/payment eligibility still governs waiting.
    return "waiting"


async def _current_orders(db: Any, owner_id: str, numbers: list[str]) -> dict[str, dict[str, Any]]:
    sources = await db["unified_orders"].find(
        {"user_id": owner_id, "order_number": {"$in": numbers}}, SOURCE_PROJECTION,
    ).to_list(len(numbers))
    by_number = {_text(row.get("order_number")): row for row in sources}
    result = {}
    for number in numbers:
        source = by_number.get(number) or {}
        raw = (source.get("raw_by_source") or {}).get("salla_direct") or {}
        native = (_provider_status_native(raw, current_status=source.get("order_status"))
                  or raw.get("status_slug") or source.get("order_status_slug"))
        effective = " ".join(_text(native).replace("_", " ").casefold().split())
        result[number] = {"source": source, "terminal": any(
            re.fullmatch(_STATUS_PATTERNS[key], effective, re.I)
            for key in ("completed", "shipping", "cancelled", "refunded")
        )}
    return result


async def _waiting_eligibility(db: Any, owner_id: str, numbers: list[str], current: dict[str, dict]) -> None:
    workflows = await db[WORKFLOWS].find(
        {"user_id": owner_id, "order_number": {"$in": numbers}}, WORKFLOW_PROJECTION,
    ).to_list(len(numbers))
    approved = await load_local_review_workflows(db, user_id=owner_id, order_numbers=numbers, workflows=workflows)
    blocked = {_text(row.get("order_number")) for row in workflows
               if not is_known_review_mode(row.get("completion_mode"))
               or (row.get("completion_mode") == LOCAL_COMPLETION_MODE and _text(row.get("order_number")) not in approved)}
    for number in numbers:
        order = projected_order(current[number]["source"])
        fields = order_waiting_fields(order, approved.get(number))
        current[number]["waiting"] = bool(fields["waiting_review_eligible"] and number not in blocked)


async def _read_counts(db: Any, owner_id: str, account_ids: list[str]) -> dict[str, int]:
    if not account_ids:
        return {}
    counts: dict[str, int] = defaultdict(int)
    eligibility: dict[str, dict[str, Any]] = {}
    cursor = db[PIECES].aggregate(_count_pipeline(owner_id, account_ids), maxTimeMS=5000, batchSize=BATCH_SIZE)
    try:
        while True:
            batch = await cursor.to_list(length=BATCH_SIZE)
            if not batch:
                break
            candidates = [(row["_id"], row["count"], _unfinished_phase(row["_id"])) for row in batch]
            candidates = [(row, count, phase) for row, count, phase in candidates if phase and _text(row.get("order_number"))]
            numbers = sorted({_text(row["order_number"]) for row, _, _ in candidates} - eligibility.keys())
            if numbers:
                eligibility.update(await _current_orders(db, owner_id, numbers))
            waiting = sorted({_text(row["order_number"]) for row, _, phase in candidates
                              if phase == "waiting" and not eligibility[_text(row["order_number"])]["terminal"]
                              and "waiting" not in eligibility[_text(row["order_number"])]})
            if waiting:
                await _waiting_eligibility(db, owner_id, waiting, eligibility)
            for group, count, phase in candidates:
                current = eligibility[_text(group["order_number"])]
                if not current["terminal"] and (phase == "underway" or current["waiting"]):
                    counts[_text(group["responsible_employee_id"])] += count
    finally:
        await cursor.close()
    return dict(counts)


async def preparation_pending_directory(db: Any, *, owner_id: str, employees: list[dict]) -> dict[str, Any]:
    from mobile_employee_monitoring_workspace_routes import monitored_work_account_id

    account_ids = sorted({monitored_work_account_id(row) for row in employees} - {""})
    try:
        counts = await asyncio.wait_for(_read_counts(db, owner_id, account_ids), timeout=READ_TIMEOUT_SECONDS)
    except (TimeoutError, PyMongoError) as exc:
        # A failed count is never a successful empty/partial employee directory.
        raise HTTPException(503, detail={"code": "monitoring_preparation_counts_unavailable",
                                        "message": "تعذر تحميل أعداد المنتجات. حاول مرة أخرى."}) from exc
    rows = []
    for employee in employees:
        count = counts.get(monitored_work_account_id(employee), 0)
        if count > 0:
            rows.append({
                "employee_id": _text(employee.get("id")),
                "employee_name": _text(employee.get("display_name")) or "موظف",
                "job_title": _text(employee.get("job_title")) or None,
                "department": _text(employee.get("department")) or None,
                "status": _text(employee.get("status")) or None,
                "outstanding_preparation_count": count,
            })
    rows.sort(key=lambda row: (-row["outstanding_preparation_count"], row["employee_name"].casefold(), row["employee_id"]))
    return {"ok": True, "read_only": True, "counts_only": True, "employees": rows}
