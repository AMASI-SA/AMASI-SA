"""Cross-stage customer-service instructions for fulfillment operations.

The instruction is created once and follows the order (or one physical piece)
until the configured operational stage is reached.  Operational screens use
the helpers in this module both to render the instruction and to fail closed
when acknowledgement or completion is mandatory.
"""
from __future__ import annotations

from typing import Any

from fastapi import HTTPException


ORDER_TRACKING_INSTRUCTIONS = "mezan_order_tracking_instructions_v1"
ORDER_TRACKING_INSTRUCTION_EVENTS = "mezan_order_tracking_instruction_events_v1"

TARGET_STAGES = {
    "pending_review",
    "reviewed",
    "preparation",
    "supplier_dispatch",
    "supplier_receiving",
    "preparation_receiving",
    "assembly_labeling",
    "carrier_handoff",
    "store_courier",
}

ENFORCEMENT_LEVELS = {
    "notice",
    "acknowledgement_required",
    "completion_required",
}

BLOCKING_ACTION_TYPES = {
    "edit_product",
    "edit_order",
    "delete_product",
    "cancel_order",
}


def text(value: Any) -> str:
    return str(value or "").strip()


def public_instruction(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in row.items()
        if key not in {"_id", "user_id"}
    }


def instruction_snapshot(row: dict[str, Any]) -> dict[str, Any]:
    """Small snapshot embedded in workflow and piece read models.

    The instruction itself is the source of truth.  Keeping the current status
    in this read-model copy lets operational screens render an approval wait or
    a customer-service rejection correctly after a full page refresh.
    """
    return {
        "id": text(row.get("id")),
        "order_number": text(row.get("order_number")),
        "scope": text(row.get("scope")),
        "target_id": text(row.get("target_id")) or None,
        "target_ids": list(row.get("target_ids") or []),
        "action_type": text(row.get("action_type")),
        "priority": text(row.get("priority")) or "normal",
        "note": text(row.get("note")),
        "target_stages": list(row.get("target_stages") or []),
        "enforcement": text(row.get("enforcement")) or "notice",
        "required_action": text(row.get("required_action")) or "none",
        "approval_required": bool(row.get("approval_required")),
        "status": text(row.get("status")) or "active",
        "submitted_at": row.get("submitted_at"),
        "submitted_by_name": text(row.get("submitted_by_name")) or None,
        "submitted_result": text(row.get("submitted_result")) or None,
        "rejection_note": text(row.get("rejection_note")) or None,
        "acknowledged_by_ids": list(row.get("acknowledged_by_ids") or []),
        "delivery_date": row.get("delivery_date"),
        "delivery_time": row.get("delivery_time"),
        "created_at": row.get("created_at"),
        "created_by_name": text(row.get("created_by_name")) or None,
    }


def instruction_applies_to(
    row: dict[str, Any],
    *,
    stage: str,
    piece_id: str = "",
    order_item_id: str = "",
    order_wide: bool = False,
) -> bool:
    if text(row.get("status")) not in {"active", "waiting_customer_service_approval"}:
        return False
    if stage not in set(row.get("target_stages") or []):
        return False
    return instruction_targets(
        row,
        piece_id=piece_id,
        order_item_id=order_item_id,
        order_wide=order_wide,
    )


def instruction_targets(
    row: dict[str, Any],
    *,
    piece_id: str = "",
    order_item_id: str = "",
    order_wide: bool = False,
) -> bool:
    """Return whether an instruction belongs to an order transition or piece."""
    scope = text(row.get("scope")) or "order"
    if order_wide:
        return scope in {"order", "item", "piece"}
    target_id = text(row.get("target_id"))
    target_ids = {
        text(value) for value in row.get("target_ids") or [] if text(value)
    }
    if target_id:
        target_ids.add(target_id)
    if scope == "piece":
        return bool(piece_id and piece_id in target_ids)
    if scope == "item":
        return bool(order_item_id and order_item_id in target_ids)
    return scope == "order"


async def active_stage_instructions(
    db: Any,
    *,
    user_id: str,
    order_number: str,
    stage: str,
    piece_id: str = "",
    order_item_id: str = "",
    order_wide: bool = False,
) -> list[dict[str, Any]]:
    if stage not in TARGET_STAGES:
        return []
    rows = await db[ORDER_TRACKING_INSTRUCTIONS].find(
        {
            "user_id": text(user_id),
            "order_number": text(order_number),
            "status": {"$in": ["active", "waiting_customer_service_approval"]},
            "target_stages": stage,
        },
        {"_id": 0},
    ).sort("created_at", 1).to_list(200)
    return [
        row
        for row in rows
        if instruction_applies_to(
            row,
            stage=stage,
            piece_id=text(piece_id),
            order_item_id=text(order_item_id),
            order_wide=order_wide,
        )
    ]


def actor_has_acknowledged(row: dict[str, Any], actor_id: str) -> bool:
    return text(actor_id) in {
        text(value) for value in row.get("acknowledged_by_ids") or [] if text(value)
    }


async def enforce_stage_instructions(
    db: Any,
    *,
    user_id: str,
    order_number: str,
    stage: str,
    actor_id: str,
    piece_id: str = "",
    order_item_id: str = "",
    order_wide: bool = False,
) -> list[dict[str, Any]]:
    """Return notices or reject when the selected stage cannot continue."""
    rows = await active_stage_instructions(
        db,
        user_id=user_id,
        order_number=order_number,
        stage=stage,
        piece_id=piece_id,
        order_item_id=order_item_id,
        order_wide=order_wide,
    )
    blocking: list[dict[str, Any]] = []
    for row in rows:
        enforcement = text(row.get("enforcement")) or "notice"
        if text(row.get("status")) == "waiting_customer_service_approval":
            blocking.append(row)
        elif enforcement == "completion_required":
            blocking.append(row)
        elif (
            enforcement == "acknowledgement_required"
            and not actor_has_acknowledged(row, actor_id)
        ):
            blocking.append(row)
    if blocking:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "customer_service_instruction_action_required",
                "stage": stage,
                "message": "توجد تعليمات إلزامية من خدمة العملاء قبل إكمال هذه المرحلة.",
                "instructions": [public_instruction(row) for row in blocking],
            },
        )
    return [public_instruction(row) for row in rows]


__all__ = [
    "BLOCKING_ACTION_TYPES",
    "ENFORCEMENT_LEVELS",
    "ORDER_TRACKING_INSTRUCTIONS",
    "ORDER_TRACKING_INSTRUCTION_EVENTS",
    "TARGET_STAGES",
    "active_stage_instructions",
    "actor_has_acknowledged",
    "enforce_stage_instructions",
    "instruction_snapshot",
    "instruction_targets",
    "public_instruction",
    "text",
]
