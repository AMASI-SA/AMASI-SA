"""Barcode-governed custody between Mezan labeling and external carriers."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from order_tracking_notes import enforce_stage_instructions

WORKFLOWS = "order_review_workflows"
EVENTS = "mezan_fulfillment_events_v2"

DELIVERING_STATUS_SLUGS = {
    "delivering",
    "in_delivery",
    "in_transit",
    "out_for_delivery",
}
DELIVERED_STATUS_SLUGS = {"delivered"}


class CarrierHandoffError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 409,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.details = details or {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _text(value: Any) -> str:
    return str(value or "").strip()


def normalize_shipping_barcode(value: Any) -> str:
    """Normalize camera/scanner output without accepting a partial AWB."""
    return "".join(
        character for character in _text(value).upper() if character.isalnum()
    )


def workflow_stage_for_salla_status(
    status_slug: Any,
    status_name: Any = None,
) -> str | None:
    slug = _text(status_slug).casefold().replace("-", "_").replace(" ", "_")
    name = _text(status_name).casefold()
    if slug in DELIVERED_STATUS_SLUGS or name == "تم التوصيل":
        return "delivered"
    if slug in DELIVERING_STATUS_SLUGS or name == "جاري التوصيل":
        return "delivering"
    return None


def _snapshot(workflow: dict[str, Any]) -> dict[str, Any]:
    return {
        "order_number": workflow.get("order_number"),
        "stage": workflow.get("stage"),
        "carrier_label_print_confirmed": bool(
            workflow.get("carrier_label_print_confirmed")
        ),
        "carrier_label_print_confirmed_at": workflow.get(
            "carrier_label_print_confirmed_at"
        ),
        "carrier_label_print_confirmed_by": workflow.get(
            "carrier_label_print_confirmed_by"
        ),
        "carrier_label_print_confirmed_by_name": workflow.get(
            "carrier_label_print_confirmed_by_name"
        ),
        "carrier_handoff_state": workflow.get("carrier_handoff_state"),
        "carrier_handoff_employee_id": workflow.get("carrier_handoff_employee_id"),
        "carrier_handoff_employee_name": workflow.get("carrier_handoff_employee_name"),
        "carrier_handoff_scanned_at": workflow.get("carrier_handoff_scanned_at"),
        "carrier_handoff_custody_active": bool(
            workflow.get("carrier_handoff_custody_active")
        ),
        "carrier_tracking_number": workflow.get("carrier_tracking_number"),
        "carrier_name": workflow.get("carrier_name"),
    }


async def confirm_carrier_label_print(
    db: Any,
    *,
    user_id: str,
    order_number: str,
    scanned_barcode: str,
    actor_id: str,
    actor_name: str,
) -> dict[str, Any]:
    normalized_order = _text(order_number)
    barcode = normalize_shipping_barcode(scanned_barcode)
    if not barcode:
        raise CarrierHandoffError(
            "carrier_label_barcode_required",
            "صوّر باركود بوليصة الشحن أولًا.",
        )
    workflow = await db[WORKFLOWS].find_one(
        {
            "user_id": user_id,
            "order_number": normalized_order,
            "stage": "completed",
            "assembly_status": "completed",
        },
        {"_id": 0},
    )
    if not workflow:
        raise CarrierHandoffError(
            "completed_order_not_found",
            "الطلب غير موجود في مرحلة تم التنفيذ.",
            status_code=404,
        )
    if _text(workflow.get("carrier_label_type")) == "store_courier":
        raise CarrierHandoffError(
            "store_courier_separate_flow",
            "طلبات مندوب المتجر لها مسار تسليم مستقل.",
        )
    if not workflow.get("carrier_label_ready"):
        raise CarrierHandoffError(
            "carrier_label_not_ready",
            "انتظر حتى تصبح بوليصة شركة الشحن جاهزة.",
        )
    expected = normalize_shipping_barcode(workflow.get("carrier_tracking_number"))
    if not expected:
        raise CarrierHandoffError(
            "carrier_tracking_number_missing",
            "رقم تتبع البوليصة غير محفوظ؛ أعد التحقق من سلة.",
        )
    if barcode != expected:
        raise CarrierHandoffError(
            "carrier_label_barcode_mismatch",
            "هذه ليست بوليصة الشحن الخاصة بهذا الطلب.",
        )
    if workflow.get("carrier_handoff_employee_id"):
        raise CarrierHandoffError(
            "carrier_shipment_already_received",
            "استلم موظف تسليم الشحن هذه الشحنة مسبقًا.",
            details={
                "employee_name": workflow.get("carrier_handoff_employee_name"),
                "scanned_at": workflow.get("carrier_handoff_scanned_at"),
            },
        )
    await enforce_stage_instructions(
        db,
        user_id=user_id,
        order_number=normalized_order,
        stage="carrier_handoff",
        actor_id=actor_id,
        order_wide=True,
    )
    if workflow.get("carrier_label_print_confirmed"):
        return {"ok": True, "already_confirmed": True, **_snapshot(workflow)}

    now = _now()
    result = await db[WORKFLOWS].update_one(
        {
            "user_id": user_id,
            "order_number": normalized_order,
            "stage": "completed",
            "$or": [
                {"carrier_label_print_confirmed": {"$exists": False}},
                {"carrier_label_print_confirmed": False},
            ],
        },
        {
            "$set": {
                "carrier_label_print_confirmed": True,
                "carrier_label_print_confirmed_at": now,
                "carrier_label_print_confirmed_by": actor_id,
                "carrier_label_print_confirmed_by_name": actor_name,
                "carrier_label_barcode": barcode,
                "carrier_handoff_state": "awaiting_carrier_handoff",
                "completed_operational_status": "label_printed",
                "updated_at": now,
            }
        },
    )
    if not result.modified_count:
        raise CarrierHandoffError(
            "carrier_label_print_conflict_refresh_required",
            "تغيّرت حالة الطلب؛ حدّث الصفحة ثم حاول مرة أخرى.",
        )
    await db[EVENTS].insert_one(
        {
            "id": uuid.uuid4().hex,
            "user_id": user_id,
            "order_number": normalized_order,
            "event_type": "carrier_label_print_confirmed",
            "tracking_number": workflow.get("carrier_tracking_number"),
            "actor_id": actor_id,
            "actor_name": actor_name,
            "occurred_at": now,
        }
    )
    updated = {
        **workflow,
        "carrier_label_print_confirmed": True,
        "carrier_label_print_confirmed_at": now,
        "carrier_label_print_confirmed_by": actor_id,
        "carrier_label_print_confirmed_by_name": actor_name,
        "carrier_label_barcode": barcode,
        "carrier_handoff_state": "awaiting_carrier_handoff",
    }
    return {"ok": True, "already_confirmed": False, **_snapshot(updated)}


async def receive_carrier_shipment(
    db: Any,
    *,
    user_id: str,
    scanned_barcode: str,
    actor_id: str,
    actor_name: str,
) -> dict[str, Any]:
    barcode = normalize_shipping_barcode(scanned_barcode)
    if not barcode:
        raise CarrierHandoffError(
            "carrier_label_barcode_required",
            "صوّر باركود بوليصة الشحن أولًا.",
        )
    workflow = await db[WORKFLOWS].find_one(
        {
            "user_id": user_id,
            "carrier_label_barcode": barcode,
            "carrier_label_print_confirmed": True,
        },
        {"_id": 0},
    )
    if not workflow:
        raise CarrierHandoffError(
            "carrier_shipment_not_confirmed_by_labeling",
            "هذه الشحنة لم يؤكد موظف العنونة طباعتها، أو أن الباركود غير صحيح.",
            status_code=404,
        )
    if _text(workflow.get("carrier_label_type")) == "store_courier":
        raise CarrierHandoffError(
            "store_courier_separate_flow",
            "طلبات مندوب المتجر لا تدخل حساب تسليم شركات الشحن.",
        )
    if workflow.get("carrier_handoff_employee_id"):
        raise CarrierHandoffError(
            "carrier_shipment_already_received",
            "أُضيفت هذه الشحنة مسبقًا إلى حساب موظف تسليم الشحن.",
            details={
                "employee_name": workflow.get("carrier_handoff_employee_name"),
                "scanned_at": workflow.get("carrier_handoff_scanned_at"),
            },
        )
    if _text(workflow.get("stage")) != "completed":
        raise CarrierHandoffError(
            "carrier_shipment_no_longer_waiting",
            "هذه الشحنة لم تعد بانتظار التسليم لشركة الشحن.",
        )
    await enforce_stage_instructions(
        db,
        user_id=user_id,
        order_number=_text(workflow.get("order_number")),
        stage="carrier_handoff",
        actor_id=actor_id,
        order_wide=True,
    )

    now = _now()
    result = await db[WORKFLOWS].update_one(
        {
            "user_id": user_id,
            "order_number": workflow.get("order_number"),
            "stage": "completed",
            "carrier_label_barcode": barcode,
            "carrier_label_print_confirmed": True,
            "$or": [
                {"carrier_handoff_employee_id": {"$exists": False}},
                {"carrier_handoff_employee_id": None},
                {"carrier_handoff_employee_id": ""},
            ],
        },
        {
            "$set": {
                "carrier_handoff_state": "with_handoff_employee",
                "carrier_handoff_employee_id": actor_id,
                "carrier_handoff_employee_name": actor_name,
                "carrier_handoff_scanned_at": now,
                "carrier_handoff_custody_active": True,
                "updated_at": now,
            }
        },
    )
    if not result.modified_count:
        raise CarrierHandoffError(
            "carrier_shipment_receive_conflict",
            "استلم موظف آخر الشحنة في اللحظة نفسها؛ حدّث الصفحة.",
        )
    await db[EVENTS].insert_one(
        {
            "id": uuid.uuid4().hex,
            "user_id": user_id,
            "order_number": workflow.get("order_number"),
            "event_type": "carrier_shipment_received_by_handoff_employee",
            "tracking_number": workflow.get("carrier_tracking_number"),
            "actor_id": actor_id,
            "actor_name": actor_name,
            "occurred_at": now,
        }
    )
    updated = {
        **workflow,
        "carrier_handoff_state": "with_handoff_employee",
        "carrier_handoff_employee_id": actor_id,
        "carrier_handoff_employee_name": actor_name,
        "carrier_handoff_scanned_at": now,
        "carrier_handoff_custody_active": True,
    }
    return {"ok": True, **_snapshot(updated)}


async def advance_carrier_handoff_from_salla_status(
    db: Any,
    *,
    user_id: str,
    order_number: str,
    status_slug: Any,
    status_name: Any = None,
    source: str = "salla_status_sync",
) -> dict[str, Any]:
    stage = workflow_stage_for_salla_status(status_slug, status_name)
    if not stage:
        return {"advanced": False, "reason": "status_not_delivery_stage"}
    now = _now()
    patch: dict[str, Any] = {
        "stage": stage,
        "salla_order_status": _text(status_slug) or _text(status_name),
        "salla_order_status_name": _text(status_name) or None,
        "carrier_handoff_released_at": now,
        "carrier_handoff_release_source": _text(source) or "salla_status_sync",
        "carrier_handoff_state": (
            "carrier_in_delivery" if stage == "delivering" else "delivered"
        ),
        "carrier_handoff_custody_active": False,
        "updated_at": now,
    }
    if stage == "delivering":
        patch["delivering_at"] = now
    else:
        patch["delivered_at"] = now
    result = await db[WORKFLOWS].update_one(
        {
            "user_id": user_id,
            "order_number": _text(order_number),
            "stage": {"$in": ["completed", "delivering"]},
            "carrier_label_print_confirmed": True,
            "carrier_handoff_state": {
                "$in": [
                    "awaiting_carrier_handoff",
                    "with_handoff_employee",
                    "carrier_in_delivery",
                ]
            },
        },
        {"$set": patch},
    )
    if not result.modified_count:
        return {"advanced": False, "reason": "workflow_not_waiting"}
    await db[EVENTS].insert_one(
        {
            "id": uuid.uuid4().hex,
            "user_id": user_id,
            "order_number": _text(order_number),
            "event_type": f"carrier_status_{stage}",
            "salla_status_slug": _text(status_slug) or None,
            "salla_status_name": _text(status_name) or None,
            "occurred_at": now,
        }
    )
    return {"advanced": True, "stage": stage}


__all__ = [
    "CarrierHandoffError",
    "advance_carrier_handoff_from_salla_status",
    "confirm_carrier_label_print",
    "normalize_shipping_barcode",
    "receive_carrier_shipment",
    "workflow_stage_for_salla_status",
]
