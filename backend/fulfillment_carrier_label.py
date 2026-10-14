"""Connect Mezan assembly completion to Salla's official carrier label."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Literal

from order_engine.shipping_label_service import (
    ShippingLabelError,
    issue_shipping_label,
    refresh_shipping_label,
)


WORKFLOWS = "order_review_workflows"
EVENTS = "mezan_fulfillment_events_v2"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _require_completed_workflow(
    db: Any,
    *,
    user_id: str,
    order_number: str,
) -> dict[str, Any]:
    workflow = await db[WORKFLOWS].find_one(
        {
            "user_id": user_id,
            "order_number": order_number,
            "stage": "completed",
            "assembly_status": "completed",
        },
        {"_id": 0},
    )
    if not workflow:
        raise ShippingLabelError(
            "assembly_completion_required",
            "أكمل جميع منتجات الطلب في التجميع والعنونة أولًا.",
            status_code=409,
        )
    return workflow


async def _require_print_completed_workflow(
    db: Any,
    *,
    user_id: str,
    order_number: str,
) -> dict[str, Any]:
    """Read the durable assembly completion proof, including after handoff.

    Printing must not reopen assembly or change the stricter issuance gate.
    Provider status, piece totals and print confirmation are not completion proof.
    """
    workflow = await db[WORKFLOWS].find_one(
        {
            "user_id": user_id,
            "order_number": order_number,
            "assembly_status": "completed",
        },
        {"_id": 0},
    )
    if not workflow or workflow.get("assembly_status") != "completed":
        raise ShippingLabelError(
            "assembly_completion_required",
            "أكمل جميع منتجات الطلب في التجميع والعنونة أولًا.",
            status_code=409,
        )
    return workflow


def _workflow_patch(result: dict[str, Any], *, now: str) -> dict[str, Any]:
    ready = bool(result.get("ready"))
    order_completed = bool(result.get("order_status_completed"))
    return {
        "salla_order_status": "completed" if order_completed else "unknown",
        "salla_order_status_verified_at": now if order_completed else None,
        "carrier_label_status": "ready" if ready else "pending",
        "carrier_label_ready": ready,
        "carrier_label_url": result.get("label_url"),
        "carrier_label_type": result.get("label_type") or "carrier",
        "carrier_name": result.get("courier_name"),
        "carrier_tracking_number": (
            result.get("tracking_number") or result.get("shipping_number")
        ),
        "carrier_shipment_id": result.get("shipment_id"),
        "carrier_label_message": result.get("message"),
        "carrier_label_error_code": None,
        "carrier_label_error_message": None,
        "carrier_label_print_data": result.get("print_data"),
        "carrier_label_verified_at": now,
        "updated_at": now,
    }


async def sync_completed_carrier_label(
    db: Any,
    *,
    user_id: str,
    order_number: str,
    actor_id: str,
    actor_name: str,
    action: Literal["issue", "refresh"] = "issue",
) -> dict[str, Any]:
    """Update Salla to completed, then obtain the official carrier label.

    ``issue`` is idempotent in the underlying Salla service: an existing
    verified AWB is reused.  ``refresh`` only checks Salla and never creates a
    shipment.  Both paths persist a small operational snapshot on the Mezan
    workflow so an already-completed order can resume safely.
    """
    normalized = _text(order_number)
    workflow = await _require_completed_workflow(
        db,
        user_id=user_id,
        order_number=normalized,
    )
    force_store_courier = bool(workflow.get("experiment_mode")) and (
        _text(workflow.get("experiment_delivery_flow")) == "store_courier"
    )
    now = _now()
    try:
        if force_store_courier:
            result = await issue_shipping_label(
                db,
                user_id,
                normalized,
                force_store_courier=True,
            )
        elif action == "refresh":
            result = await refresh_shipping_label(db, user_id, normalized)
        else:
            result = await issue_shipping_label(db, user_id, normalized)
    except ShippingLabelError as exc:
        await db[WORKFLOWS].update_one(
            {"user_id": user_id, "order_number": normalized},
            {"$set": {
                "carrier_label_status": "failed",
                "carrier_label_ready": False,
                "carrier_label_error_code": exc.code,
                "carrier_label_error_message": str(exc),
                "carrier_label_verified_at": now,
                "updated_at": now,
            }},
        )
        raise

    from operational_atomic import operational_owner
    from assembly_status_policy import source_status
    async def persist(scoped):
        current = await _require_completed_workflow(scoped, user_id=user_id, order_number=normalized)
        if current.get("shipping_status_epoch", 0) != workflow.get("shipping_status_epoch", 0):
            raise ShippingLabelError("assembly_canonical_status_blocked", "تغيرت حالة الطلب أثناء التحقق؛ أعد القراءة.")
        if await source_status(scoped, user_id, normalized) not in {"in_progress", "completed"}:
            raise ShippingLabelError("assembly_canonical_status_blocked", "تغيرت حالة الطلب؛ الطباعة متوقفة.")
        patch = _workflow_patch(result, now=now)
        await scoped[WORKFLOWS].update_one(
            {"user_id": user_id, "order_number": normalized},
            {"$set": patch},
        )
        await scoped[EVENTS].insert_one({
            "id": uuid.uuid4().hex,
            "user_id": user_id,
            "order_number": normalized,
            "event_type": (
                "carrier_label_ready"
                if result.get("ready")
                else "carrier_label_pending"
            ),
            "carrier_label_action": action,
            "experiment_delivery_flow": (
                "store_courier" if force_store_courier else None
            ),
            "salla_order_status_completed": bool(
                result.get("order_status_completed")
            ),
            "shipment_id": result.get("shipment_id"),
            "actor_id": actor_id,
            "actor_name": actor_name,
            "occurred_at": now,
        })
    await operational_owner(db, user_id, persist)
    return result


__all__ = ["sync_completed_carrier_label"]
