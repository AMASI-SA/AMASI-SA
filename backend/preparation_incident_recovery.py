"""Strict, auditable recovery for the 2026-08-25 AMS11353 lost batch.

The endpoint is intentionally incident-specific.  It refuses to mutate unless
the exact eight orders still contain exactly eleven physical AMS11353 units.
It only releases allocations for those item identities and never touches a
registered preparation file.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable
import uuid

from fastapi import APIRouter, Depends, HTTPException

from order_engine.repository import MongoOrderRepository
from order_engine.service import OrderNotFoundError, get_order
from order_review_routes import EVENTS, WORKFLOWS, _merchant_user_id, _require_reviewer
from reviewed_products_catalog import PREPARATION_UNIT_ALLOCATIONS
from reviewed_preparation_batches import BATCHES
from preparation_file_registry import REGISTRY
from preparation_piece_operations import PIECES


INCIDENT_ID = "ams11353-lost-11-20260825"
SKU = "AMS11353"
EXPECTED = {
    "279756840": 1,
    "279809610": 1,
    "279778158": 2,
    "279820694": 1,
    "279803951": 2,
    "279787662": 1,
    "279773618": 2,
    "279726749": 1,
}
ACTIVE_ALLOCATION_STATUSES = ("reserved", "committed")


def _text(value: Any) -> str:
    return str(value or "").strip()


def _qty(value: Any) -> int:
    try:
        return max(0, int(round(float(value or 0))))
    except (TypeError, ValueError, OverflowError):
        return 0


async def _preview(db: Any, user_id: str) -> dict[str, Any]:
    repo = MongoOrderRepository(db)
    target_items: list[dict[str, Any]] = []
    workflow_rows: list[dict[str, Any]] = []
    problems: list[str] = []
    for order_number, expected_quantity in EXPECTED.items():
        try:
            order = await get_order(repo, user_id=user_id, order_number=order_number)
        except OrderNotFoundError:
            problems.append(f"order_missing:{order_number}")
            continue
        matches = [item for item in order.items if _text(item.sku).upper() == SKU]
        actual = sum(_qty(item.quantity) for item in matches)
        if actual != expected_quantity:
            problems.append(f"quantity_mismatch:{order_number}:{actual}:{expected_quantity}")
        for item in matches:
            target_items.append({
                "order_number": order_number,
                "order_item_id": _text(item.order_item_id),
                "quantity": _qty(item.quantity),
            })
        workflow = await db[WORKFLOWS].find_one(
            {"user_id": user_id, "order_number": order_number}, {"_id": 0}
        )
        if not workflow:
            problems.append(f"workflow_missing:{order_number}")
        else:
            workflow_rows.append(workflow)

    target_keys = {
        (row["order_number"], row["order_item_id"])
        for row in target_items if row["order_item_id"]
    }
    allocations = await db[PREPARATION_UNIT_ALLOCATIONS].find(
        {
            "user_id": user_id,
            "order_number": {"$in": list(EXPECTED)},
            "status": {"$in": list(ACTIVE_ALLOCATION_STATUSES)},
        },
        {"_id": 0},
    ).to_list(10000)
    target_allocations = [
        row for row in allocations
        if (_text(row.get("order_number")), _text(row.get("order_item_id"))) in target_keys
    ]
    batch_ids = sorted({_text(row.get("batch_id")) for row in target_allocations if _text(row.get("batch_id"))})
    batches = await db[BATCHES].find(
        {"user_id": user_id, "id": {"$in": batch_ids}}, {"_id": 0, "pdf_bytes": 0}
    ).to_list(1000) if batch_ids else []
    registry = await db[REGISTRY].find(
        {"user_id": user_id, "$or": [{"batch_id": {"$in": batch_ids}}, {"source_batch_id": {"$in": batch_ids}}]},
        {"_id": 0},
    ).to_list(1000) if batch_ids else []
    if registry:
        problems.append("target_allocation_has_registered_file")
    unsafe_batches = []
    for batch in batches:
        foreign_lines = [
            row for row in batch.get("lines") or []
            if (_text(row.get("order_number")), _text(row.get("order_item_id"))) not in target_keys
        ]
        if foreign_lines:
            unsafe_batches.append(_text(batch.get("id")))
    if unsafe_batches:
        problems.append("target_batch_contains_other_products")
    recovered_event = await db[EVENTS].find_one(
        {"user_id": user_id, "event_type": "preparation_incident_recovered", "incident_id": INCIDENT_ID},
        {"_id": 0, "id": 1, "occurred_at": 1},
    )

    return {
        "ok": not problems,
        "incident_id": INCIDENT_ID,
        "sku": SKU,
        "expected_order_count": len(EXPECTED),
        "expected_quantity": sum(EXPECTED.values()),
        "resolved_quantity": sum(row["quantity"] for row in target_items),
        "orders": [
            {
                "order_number": number,
                "expected_quantity": quantity,
                "workflow_stage": next(
                    (_text(row.get("stage")) for row in workflow_rows if _text(row.get("order_number")) == number),
                    None,
                ),
            }
            for number, quantity in EXPECTED.items()
        ],
        "target_items": target_items,
        "target_allocations": target_allocations,
        "related_batches": batches,
        "registered_files": registry,
        "already_recovered": bool(recovered_event),
        "recovery_event": recovered_event,
        "problems": problems,
    }


def make_preparation_incident_recovery_router(db: Any, current_user: Callable) -> APIRouter:
    router = APIRouter(prefix="/preparation-recovery-v1", tags=["Preparation Incident Recovery"])

    @router.get(f"/incidents/{INCIDENT_ID}")
    async def preview(user: dict = Depends(current_user)) -> dict[str, Any]:
        reviewer = _require_reviewer(user)
        return await _preview(db, _merchant_user_id(reviewer))

    @router.post(f"/incidents/{INCIDENT_ID}/apply")
    async def apply(user: dict = Depends(current_user)) -> dict[str, Any]:
        reviewer = _require_reviewer(user)
        user_id = _merchant_user_id(reviewer)
        before = await _preview(db, user_id)
        if before.get("already_recovered"):
            raise HTTPException(status_code=409, detail={"code": "incident_already_recovered", "preview": before})
        if not before["ok"] or before["resolved_quantity"] != 11:
            raise HTTPException(status_code=409, detail={"code": "incident_preconditions_failed", "preview": before})

        target_keys = {
            (row["order_number"], row["order_item_id"])
            for row in before["target_items"]
        }
        target_allocations = before["target_allocations"]
        allocation_ids = [_text(row.get("id")) for row in target_allocations if _text(row.get("id"))]
        batch_ids = sorted({_text(row.get("batch_id")) for row in target_allocations if _text(row.get("batch_id"))})
        now = datetime.now(timezone.utc)
        recovery_id = uuid.uuid4().hex

        client = db.client
        async with await client.start_session() as session:
            async def transaction(_: Any) -> None:
                if allocation_ids:
                    deleted = await db[PREPARATION_UNIT_ALLOCATIONS].delete_many(
                        {"user_id": user_id, "id": {"$in": allocation_ids}, "status": {"$in": list(ACTIVE_ALLOCATION_STATUSES)}},
                        session=session,
                    )
                    if int(deleted.deleted_count) != len(allocation_ids):
                        raise RuntimeError("incident_allocation_conflict")

                await db[PIECES].update_many(
                    {
                        "user_id": user_id,
                        "batch_id": {"$in": batch_ids},
                        "$or": [
                            {"order_number": number, "order_item_id": item_id}
                            for number, item_id in sorted(target_keys)
                        ],
                    },
                    {"$set": {"incident_recovery_id": recovery_id, "incident_archived_at": now, "status": "cancelled", "updated_at": now}},
                    session=session,
                ) if batch_ids else None

                for order_number in EXPECTED:
                    other_allocations = await db[PREPARATION_UNIT_ALLOCATIONS].find(
                        {"user_id": user_id, "order_number": order_number, "status": {"$in": list(ACTIVE_ALLOCATION_STATUSES)}},
                        {"_id": 0, "batch_id": 1}, session=session,
                    ).to_list(10000)
                    remaining_batches = sorted({_text(row.get("batch_id")) for row in other_allocations if _text(row.get("batch_id"))})
                    result = await db[WORKFLOWS].update_one(
                        {"user_id": user_id, "order_number": order_number, "stage": {"$in": ["reviewed", "in_progress"]}},
                        {
                            "$set": {
                                "stage": "reviewed",
                                "preparation_assignment_status": "partially_assigned" if other_allocations else "unassigned",
                                "preparation_batch_ids": remaining_batches,
                                "incident_recovery_id": recovery_id,
                                "incident_recovered_at": now,
                                "updated_at": now,
                            },
                            "$unset": {"preparation_progress": "", "preparation_fully_allocated_at": "", "in_progress_at": "", "in_progress_by": "", "in_progress_by_name": ""},
                            "$inc": {"revision": 1},
                        },
                        session=session,
                    )
                    if int(result.modified_count) != 1:
                        raise RuntimeError(f"incident_workflow_conflict:{order_number}")

                if batch_ids:
                    await db[BATCHES].update_many(
                        {"user_id": user_id, "id": {"$in": batch_ids}, "status": {"$ne": "recovered_cancelled"}},
                        {"$set": {"status": "recovered_cancelled", "incident_recovery_id": recovery_id, "recovered_at": now, "updated_at": now}},
                        session=session,
                    )
                await db[EVENTS].insert_one({
                    "id": recovery_id,
                    "user_id": user_id,
                    "event_type": "preparation_incident_recovered",
                    "incident_id": INCIDENT_ID,
                    "sku": SKU,
                    "order_numbers": list(EXPECTED),
                    "quantity": 11,
                    "released_allocation_count": len(allocation_ids),
                    "batch_ids": batch_ids,
                    "actor_id": _text(reviewer.get("id")),
                    "occurred_at": now,
                    "mezan_only": True,
                    "salla_updated": False,
                    "qoyod_updated": False,
                }, session=session)

            await session.with_transaction(transaction)

        after = await _preview(db, user_id)
        return {"ok": True, "recovery_id": recovery_id, "before": before, "after": after}

    return router


__all__ = ["INCIDENT_ID", "EXPECTED", "SKU", "make_preparation_incident_recovery_router"]
