"""Execution-start guard for preparation files.

Starting a partial file while other units of the same order are still unassigned
would move the order out of the reviewed catalogue and strand those units. This
installer fails closed until every related reviewed order has zero remaining
units in its preparation assignment progress.
"""
from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from order_review_routes import _text


_INSTALLED = False
_ORIGINAL_START_FILE = None


def incomplete_assignment_orders(workflows: list[dict[str, Any]]) -> list[str]:
    blocked: list[str] = []
    for workflow in workflows:
        if _text(workflow.get("stage")) != "reviewed":
            continue
        order_number = _text(workflow.get("order_number"))
        progress = workflow.get("preparation_progress")
        assignment_status = _text(workflow.get("preparation_assignment_status"))
        try:
            remaining = int((progress or {}).get("remaining_quantity"))
        except (TypeError, ValueError, OverflowError):
            remaining = -1
        if not progress or remaining != 0 or assignment_status != "assigned":
            if order_number:
                blocked.append(order_number)
    return sorted(set(blocked))


async def _start_file_after_full_assignment(
    db: Any,
    *,
    user_id: str,
    registry: dict[str, Any],
    actor: dict[str, Any],
    note: str | None,
    permissions: set[str] | None = None,
) -> dict[str, Any]:
    import preparation_piece_operations as base

    assert _ORIGINAL_START_FILE is not None
    batch_id = _text(registry.get("batch_id"))
    rows = await db[base.PIECES].find(
        {"user_id": user_id, "batch_id": batch_id},
        {"_id": 0, "order_number": 1},
    ).to_list(10000)
    order_numbers = sorted({
        _text(row.get("order_number"))
        for row in rows
        if _text(row.get("order_number"))
    })
    if not order_numbers:
        raise HTTPException(
            status_code=409,
            detail={"code": "preparation_file_pieces_missing"},
        )
    workflows = await db[base.WORKFLOWS].find(
        {"user_id": user_id, "order_number": {"$in": order_numbers}},
        {
            "_id": 0,
            "order_number": 1,
            "stage": 1,
            "preparation_progress": 1,
            "preparation_assignment_status": 1,
        },
    ).to_list(max(1, len(order_numbers)))
    found = {_text(row.get("order_number")) for row in workflows}
    missing = sorted(set(order_numbers) - found)
    blocked = sorted(set([
        *missing,
        *incomplete_assignment_orders(workflows),
    ]))
    if blocked:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "preparation_file_start_requires_full_assignment",
                "order_numbers": blocked,
            },
        )
    return await _ORIGINAL_START_FILE(
        db,
        user_id=user_id,
        registry=registry,
        actor=actor,
        note=note,
        permissions=permissions,
    )


def install_preparation_piece_execution_guard() -> None:
    global _INSTALLED, _ORIGINAL_START_FILE
    if _INSTALLED:
        return
    import preparation_piece_operations as base

    _ORIGINAL_START_FILE = base._start_file_execution
    base._start_file_execution = _start_file_after_full_assignment
    _INSTALLED = True


__all__ = [
    "incomplete_assignment_orders",
    "install_preparation_piece_execution_guard",
]
