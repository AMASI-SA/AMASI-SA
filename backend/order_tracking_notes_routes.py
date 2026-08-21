"""Unified customer-service order tracking and cross-stage instructions."""
from __future__ import annotations

import hashlib
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Literal

from bson.binary import Binary
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile
from pydantic import BaseModel, ConfigDict, Field, field_validator
from pymongo import ASCENDING, DESCENDING, ReturnDocument

from alerts_routes import _fp, _upsert_alert
from carrier_handoff import EVENTS as CARRIER_EVENTS
from fulfillment_experiment_routes import (
    ACTIVE_PIECE_STATUSES,
    FULFILLMENT_HOLDS,
    STOP_TYPE_LABELS,
    _can_manage_stops,
    hold_piece_patch,
    release_piece_update,
)
from fulfillment_v2_routes import _actor_context
from order_engine.repository import MongoOrderRepository
from order_engine.service import OrderNotFoundError, get_order
from order_review_routes import EVENTS as REVIEW_EVENTS, WORKFLOWS
from order_tracking_notes import (
    BLOCKING_ACTION_TYPES,
    ENFORCEMENT_LEVELS,
    ORDER_TRACKING_INSTRUCTIONS,
    ORDER_TRACKING_INSTRUCTION_EVENTS,
    TARGET_STAGES,
    instruction_snapshot,
    instruction_targets,
    public_instruction,
    text,
)
from preparation_piece_operations import PIECES, PIECE_EVENTS
from store_delivery_customer_instruction_routes import (
    STORE_DELIVERY_INSTRUCTIONS,
    STORE_DELIVERY_INSTRUCTION_EVENTS,
    _require_customer_service,
)
from supplier_receiving_routes import RECEIVING_EVENTS


STORE_DELIVERY_EVENTS = "store_delivery_events"
ORDER_TRACKING_EVIDENCE = "mezan_order_tracking_instruction_evidence_v1"
MAX_EVIDENCE_IMAGE_BYTES = 8 * 1024 * 1024
MAX_EVIDENCE_IMAGES = 8
ALLOWED_EVIDENCE_TYPES = {"image/jpeg", "image/png", "image/webp"}
NOTE_PRIORITIES = {"normal", "high", "urgent"}
REQUIRED_ACTIONS = {
    "none",
    "arrival_confirmation",
    "write_result",
    "upload_photos",
    "upload_photos_and_result",
}
ACTION_TYPES = {
    "general",
    "edit_product",
    "edit_order",
    "delete_product",
    "cancel_order",
    "urgent_preparation",
    "delivery_instruction",
}
SCOPES = {"order", "item", "piece"}
TRACKING_MANAGE_PERMISSION = "customer_intelligence.inbox.read"

ACTION_LABELS = {
    "general": "ملاحظة عامة",
    "edit_product": "تعديل منتج",
    "edit_order": "تعديل الطلب كاملًا",
    "delete_product": "حذف منتج",
    "cancel_order": "إلغاء الطلب كاملًا",
    "urgent_preparation": "تجهيز مستعجل",
    "delivery_instruction": "تعليمات توصيل",
}

STAGE_LABELS = {
    "pending_review": "انتظار المراجعة",
    "reviewed": "تم المراجعة",
    "preparation": "تجهيز الموظف",
    "supplier_dispatch": "الإرسال إلى المورد",
    "supplier_receiving": "الاستلام من المورد",
    "preparation_receiving": "الاستلام من موظف التجهيز",
    "assembly_labeling": "التجميع والعنونة",
    "carrier_handoff": "تسليم الشحنة لشركة الشحن",
    "store_courier": "مندوب توصيل المتجر",
}

EVENT_LABELS = {
    "order_reviewed": "تمت مراجعة الطلب",
    "order_review_completed": "تمت مراجعة الطلب",
    "preparation_batch_created": "أُنشئ ملف التجهيز",
    "order_preparation_fully_assigned": "اكتمل إسناد قطع الطلب",
    "order_moved_to_in_progress": "انتقل الطلب إلى قيد التنفيذ",
    "order_ready_to_ship": "انتقل الطلب إلى التجميع والعنونة",
    "order_fulfillment_completed": "اكتمل التجهيز وأصبح الطلب جاهزًا للشحن",
    "preparation_file_started": "بدأ موظف التجهيز الملف",
    "preparation_pieces_sent_to_supplier": "أُرسلت القطع إلى المورد",
    "supplier_piece_scanned": "استُلمت القطعة من المورد",
    "supplier_receiving_session_closed": "أُغلق استلام المورد",
    "preparation_piece_ready_for_receipt": "جهّز الموظف القطعة",
    "preparation_piece_received_for_assembly": "استُلمت القطعة من موظف التجهيز",
    "carrier_label_print_confirmed": "طُبعت بوليصة شركة الشحن",
    "carrier_shipment_received_by_handoff_employee": "استلم موظف تسليم الشحن الشحنة",
    "carrier_status_delivering": "سلّمت الشحنة لشركة الشحن وبدأ التوصيل",
    "carrier_status_delivered": "تم توصيل الشحنة",
    "store_courier_shipment_assigned": "أُسندت الشحنة إلى مندوب المتجر",
    "store_courier_shipment_picked_up": "استلم مندوب المتجر الشحنة",
    "store_courier_shipment_delivered": "سلّم مندوب المتجر الطلب",
    "fulfillment_stop_created": "أوقفت خدمة العملاء المسار",
    "fulfillment_stop_released": "استأنف المسار بعد الإيقاف",
    "customer_service_instruction_created": "أضافت خدمة العملاء تعليمات",
    "customer_service_instruction_acknowledged": "أكد الموظف الاطلاع على التعليمات",
    "customer_service_instruction_submitted_for_approval": "أرسل الموظف التنفيذ إلى خدمة العملاء للموافقة",
    "customer_service_instruction_rejected": "رفضت خدمة العملاء التنفيذ وأعادته للموظف",
    "customer_service_instruction_completed": "تم تنفيذ تعليمات خدمة العملاء",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except (TypeError, ValueError):
            pass
    return text(value) or None


def _model_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        return dump(mode="json")
    legacy = getattr(value, "dict", None)
    return legacy() if callable(legacy) else {}


def _can_manage_tracking_instructions(
    user: dict[str, Any],
    context: dict[str, Any],
) -> bool:
    """Customer service owns these gates even without fulfillment-stop ACLs."""
    role = text(user.get("role")).casefold()
    granted = (
        set(user.get("extra_permissions") or [])
        | set(user.get("permissions") or [])
        | set(user.get("effective_permissions") or [])
    )
    denied = set(user.get("denied_permissions") or [])
    if TRACKING_MANAGE_PERMISSION in denied:
        return False
    return bool(
        context.get("is_owner")
        or user.get("is_owner") is True
        or role in {"owner", "admin", "customer_service"}
        or TRACKING_MANAGE_PERMISSION in granted
        or _can_manage_stops(context)
    )


class TrackingInstructionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scope: Literal["order", "item", "piece"] = "order"
    target_id: str | None = Field(default=None, max_length=180)
    target_ids: list[str] = Field(default_factory=list, max_length=200)
    action_type: str = Field(min_length=1, max_length=40)
    priority: str = Field(default="normal", max_length=16)
    note: str = Field(min_length=3, max_length=1500)
    target_stages: list[str] = Field(min_length=1, max_length=10)
    enforcement: str = Field(default="notice", max_length=40)
    required_action: str = Field(default="none", max_length=40)
    approval_required: bool = False
    delivery_date: str | None = Field(default=None, max_length=10)
    delivery_time: str | None = Field(default=None, max_length=5)

    @field_validator("target_stages")
    @classmethod
    def validate_stages(cls, value: list[str]) -> list[str]:
        normalized = list(dict.fromkeys(text(row) for row in value if text(row)))
        allowed = TARGET_STAGES | {"current_stage"}
        if not normalized or any(row not in allowed for row in normalized):
            raise ValueError("customer_service_instruction_stage_invalid")
        return normalized


class InstructionActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    note: str | None = Field(default=None, max_length=1000)


class InstructionApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    note: str | None = Field(default=None, max_length=1000)


def _detected_image_type(data: bytes) -> str | None:
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _current_stage(workflow: dict[str, Any], pieces: list[dict[str, Any]]) -> str:
    workflow_stage = text(workflow.get("stage"))
    if workflow_stage in {"completed", "delivering", "delivered"}:
        if text(workflow.get("carrier_label_type")) == "store_courier":
            return "store_courier"
        return "carrier_handoff"
    active = [row for row in pieces if row.get("experiment_archived_at") is None]
    if active:
        if any(text(row.get("status")) == "ready_for_assembly" for row in active):
            return "assembly_labeling"
        if any(text(row.get("status")) == "ready_for_employee_receipt" for row in active):
            return "preparation_receiving"
        if any(text(row.get("supplier_receiving_session_id")) for row in active):
            return "supplier_receiving"
        if any(text(row.get("supplier_dispatch_status")) in {"sent", "ready"} for row in active):
            return "supplier_receiving"
        if any(text(row.get("status")) in {"assigned", "in_progress", "blocked"} for row in active):
            return "preparation"
    stage = workflow_stage
    if stage in {"", "pending_review"}:
        return "pending_review"
    if stage == "reviewed":
        return "reviewed"
    if stage == "in_progress":
        return "preparation"
    if stage == "ready_to_ship":
        return "assembly_labeling"
    return "pending_review"


def _current_stage_label(workflow: dict[str, Any], pieces: list[dict[str, Any]]) -> str:
    workflow_stage = text(workflow.get("stage"))
    if workflow_stage == "delivering":
        return "جاري التوصيل"
    if workflow_stage == "delivered":
        return "تم التوصيل"
    stage = _current_stage(workflow, pieces)
    return STAGE_LABELS.get(stage, stage)


def _piece_stage(
    piece: dict[str, Any],
    workflow: dict[str, Any] | None = None,
) -> str:
    workflow = workflow or {}
    workflow_stage = text(workflow.get("stage"))
    if workflow_stage in {"completed", "delivering", "delivered"}:
        if text(workflow.get("carrier_label_type")) == "store_courier":
            return "store_courier"
        return "carrier_handoff"
    status = text(piece.get("status")) or "assigned"
    if status == "ready_for_assembly":
        return "assembly_labeling"
    if status == "ready_for_employee_receipt":
        return "preparation_receiving"
    if text(piece.get("supplier_receiving_session_id")):
        return "supplier_receiving"
    if text(piece.get("supplier_dispatch_status")) in {"sent", "ready"}:
        return "supplier_receiving"
    if status in {"assigned", "in_progress", "blocked", "cancelled"}:
        return "preparation"
    return "preparation"


def _piece_custody(
    piece: dict[str, Any],
    workflow: dict[str, Any] | None = None,
    order: dict[str, Any] | None = None,
) -> dict[str, Any]:
    workflow = workflow or {}
    order = order or {}
    stage = _piece_stage(piece, workflow)
    workflow_stage = text(workflow.get("stage"))
    if workflow_stage == "delivered":
        customer = order.get("customer") if isinstance(order.get("customer"), dict) else {}
        return {
            "type": "customer",
            "name": text(customer.get("name") or order.get("customer_name")) or "تم التسليم للعميل",
        }
    if stage == "store_courier":
        return {
            "type": "store_courier",
            "name": text(workflow.get("store_courier_assignee_name")) or "مندوب المتجر",
        }
    if stage == "carrier_handoff":
        handoff_state = text(workflow.get("carrier_handoff_state"))
        if workflow_stage == "delivering" or handoff_state == "carrier_in_delivery":
            shipping = order.get("shipping") if isinstance(order.get("shipping"), dict) else {}
            return {
                "type": "carrier",
                "name": text(workflow.get("carrier_name") or shipping.get("company")) or "شركة الشحن",
            }
        if handoff_state == "with_handoff_employee":
            return {
                "type": "handoff_employee",
                "name": text(workflow.get("carrier_handoff_employee_name")) or "موظف تسليم الشحن",
            }
        return {"type": "assembly", "name": "قسم التجميع والعنونة"}
    if stage == "supplier_receiving":
        return {
            "type": "supplier",
            "name": text(piece.get("supplier_name")) or "المورد",
        }
    if stage == "assembly_labeling":
        return {
            "type": "assembly",
            "name": text(piece.get("preparation_received_by_name")) or "قسم التجميع والعنونة",
        }
    return {
        "type": "employee",
        "name": text(piece.get("responsible_employee_name")) or "غير مسند",
    }


def _event_public(row: dict[str, Any], source: str) -> dict[str, Any]:
    event_type = text(row.get("event_type") or row.get("type"))
    return {
        "id": text(row.get("id")) or uuid.uuid4().hex,
        "event_type": event_type,
        "label": EVENT_LABELS.get(event_type, event_type.replace("_", " ") or "تحديث"),
        "occurred_at": _iso(row.get("occurred_at") or row.get("created_at") or row.get("updated_at")),
        "actor_id": text(row.get("actor_id")) or None,
        "actor_name": text(row.get("actor_name") or row.get("created_by_name")) or None,
        "piece_id": text(row.get("piece_id")) or None,
        "piece_ids": list(row.get("piece_ids") or []),
        "instruction_id": text(row.get("instruction_id")) or None,
        "note": text(row.get("note") or row.get("message")) or None,
        "source": source,
    }


def _workflow_events(workflow: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    fields = [
        ("reviewed_at", "order_reviewed", "reviewed_by_name"),
        ("in_progress_at", "order_moved_to_in_progress", "in_progress_by_name"),
        ("ready_to_ship_at", "order_ready_to_ship", None),
        ("completed_at", "order_fulfillment_completed", "completed_by_name"),
        ("carrier_label_print_confirmed_at", "carrier_label_print_confirmed", "carrier_label_print_confirmed_by_name"),
        ("carrier_handoff_scanned_at", "carrier_shipment_received_by_handoff_employee", "carrier_handoff_employee_name"),
        ("store_courier_assigned_at", "store_courier_shipment_assigned", "store_courier_assignee_name"),
        ("store_courier_picked_up_at", "store_courier_shipment_picked_up", "store_courier_assignee_name"),
        ("delivering_at", "carrier_status_delivering", None),
        ("delivered_at", "carrier_status_delivered", None),
    ]
    for field, event_type, actor_field in fields:
        if workflow.get(field):
            rows.append(_event_public({
                "id": f"workflow:{event_type}:{_iso(workflow.get(field))}",
                "event_type": event_type,
                "occurred_at": workflow.get(field),
                "actor_name": workflow.get(actor_field) if actor_field else None,
            }, "workflow"))
    return rows


async def _events_for_order(
    db: Any,
    *,
    user_id: str,
    order_number: str,
    piece_ids: list[str],
) -> list[dict[str, Any]]:
    piece_query = {
        "user_id": user_id,
        "$or": [
            {"order_number": order_number},
            {"order_numbers": order_number},
            {"piece_id": {"$in": piece_ids or ["__none__"]}},
            {"piece_ids": {"$in": piece_ids or ["__none__"]}},
        ],
    }
    sources = [
        (REVIEW_EVENTS, {"user_id": user_id, "$or": [{"order_number": order_number}, {"order_numbers": order_number}]}, "review"),
        (PIECE_EVENTS, piece_query, "preparation"),
        (RECEIVING_EVENTS, piece_query, "supplier_receiving"),
        (CARRIER_EVENTS, {"user_id": user_id, "order_number": order_number}, "delivery"),
        (STORE_DELIVERY_EVENTS, {"user_id": user_id, "$or": [{"order_number": order_number}, {"order_id": order_number}]}, "store_delivery"),
        (ORDER_TRACKING_INSTRUCTION_EVENTS, {"user_id": user_id, "order_number": order_number}, "customer_service"),
    ]
    events: list[dict[str, Any]] = []
    for collection, query, source in sources:
        rows = await db[collection].find(query, {"_id": 0}).sort("occurred_at", 1).limit(1000).to_list(1000)
        events.extend(_event_public(row, source) for row in rows)
    unique: dict[str, dict[str, Any]] = {}
    for row in events:
        unique[row["id"]] = row
    return sorted(unique.values(), key=lambda row: row.get("occurred_at") or "")


async def ensure_order_tracking_instruction_indexes(db: Any) -> None:
    await db[ORDER_TRACKING_INSTRUCTIONS].create_index(
        [("user_id", ASCENDING), ("id", ASCENDING)], unique=True,
        name="uq_order_tracking_instruction_v1",
    )
    await db[ORDER_TRACKING_INSTRUCTIONS].create_index(
        [("user_id", ASCENDING), ("order_number", ASCENDING), ("status", ASCENDING), ("created_at", DESCENDING)],
        name="ix_order_tracking_instruction_order_v1",
    )
    await db[ORDER_TRACKING_INSTRUCTIONS].create_index(
        [("user_id", ASCENDING), ("target_stages", ASCENDING), ("status", ASCENDING)],
        name="ix_order_tracking_instruction_stage_v1",
    )
    await db[ORDER_TRACKING_INSTRUCTION_EVENTS].create_index(
        [("user_id", ASCENDING), ("instruction_id", ASCENDING), ("occurred_at", DESCENDING)],
        name="ix_order_tracking_instruction_events_v1",
    )
    await db[ORDER_TRACKING_EVIDENCE].create_index(
        [("user_id", ASCENDING), ("id", ASCENDING)], unique=True,
        name="uq_order_tracking_instruction_evidence_v1",
    )
    await db[ORDER_TRACKING_EVIDENCE].create_index(
        [("user_id", ASCENDING), ("instruction_id", ASCENDING), ("created_at", DESCENDING)],
        name="ix_order_tracking_instruction_evidence_instruction_v1",
    )


async def _notify_current_responsibilities(
    db: Any,
    *,
    merchant_id: str,
    workflow: dict[str, Any],
    pieces: list[dict[str, Any]],
    instruction: dict[str, Any],
) -> int:
    target_ids = {merchant_id}
    for piece in pieces:
        if text(piece.get("responsible_employee_id")):
            target_ids.add(text(piece.get("responsible_employee_id")))
    for field in (
        "preparation_received_by",
        "claimed_by",
        "carrier_handoff_employee_id",
        "store_courier_assignee_id",
    ):
        if text(workflow.get(field)):
            target_ids.add(text(workflow.get(field)))
    delivered = 0
    for target_id in target_ids:
        try:
            await _upsert_alert(db, target_id, {
                "alert_type": "customer_service_instruction",
                "severity": "critical" if instruction["priority"] == "urgent" else "warning",
                "title": f"تعليمات خدمة العملاء للطلب #{instruction['order_number']}",
                "message": instruction["note"],
                "related_entity_type": "order",
                "related_entity_id": instruction["order_number"],
                "related_entity_url": "/order-tracking-notes",
                "fingerprint": _fp("customer_service_instruction", "instruction", instruction["id"]),
                "metadata": {
                    "instruction_id": instruction["id"],
                    "target_stages": instruction["target_stages"],
                    "enforcement": instruction["enforcement"],
                },
            })
            delivered += 1
        except Exception:
            continue
    return delivered


async def _sync_instruction_snapshot(
    db: Any,
    *,
    merchant_id: str,
    instruction: dict[str, Any],
) -> None:
    """Refresh denormalized copies without making them authoritative."""
    snapshot = instruction_snapshot(instruction)
    identity = {"id": instruction["id"]}
    workflow_query = {
        "user_id": merchant_id,
        "order_number": instruction["order_number"],
    }
    order_piece_query = {
        "user_id": merchant_id,
        "order_number": instruction["order_number"],
        "customer_service_instructions.id": instruction["id"],
    }
    # MongoDB rejects $pull and $addToSet on the same field in one update.
    await db[WORKFLOWS].update_one(
        workflow_query,
        {"$pull": {"customer_service_instructions": identity}},
    )
    await db[WORKFLOWS].update_one(
        workflow_query,
        {"$addToSet": {"customer_service_instructions": snapshot}, "$set": {"updated_at": _now()}},
    )
    await db[PIECES].update_many(
        order_piece_query,
        {"$pull": {"customer_service_instructions": identity}},
    )
    target_values = [
        text(value)
        for value in (
            list(instruction.get("target_ids") or [])
            + [instruction.get("target_id")]
        )
        if text(value)
    ]
    target_query: dict[str, Any] = {
        "user_id": merchant_id,
        "order_number": instruction["order_number"],
    }
    if instruction.get("scope") == "piece":
        target_query["piece_id"] = {"$in": target_values}
    elif instruction.get("scope") == "item":
        target_query["order_item_id"] = {"$in": target_values}
    await db[PIECES].update_many(
        target_query,
        {"$addToSet": {"customer_service_instructions": snapshot}, "$set": {"updated_at": _now()}},
    )


async def _notify_customer_service_approval(
    db: Any,
    *,
    merchant_id: str,
    instruction: dict[str, Any],
) -> int:
    """Alert the owner and every active customer-service team account."""
    target_ids = {merchant_id}
    try:
        rows = await db.users.find(
            {
                "$or": [
                    {"id": merchant_id},
                    {
                        "created_by": merchant_id,
                        "$or": [
                            {"role": "customer_service"},
                            {"extra_permissions": "customer_intelligence.inbox.read"},
                        ],
                    },
                ],
                "disabled": {"$ne": True},
                "is_active": {"$ne": False},
                "deleted_at": {"$in": [None, ""]},
            },
            {"_id": 0, "id": 1},
        ).to_list(5000)
    except Exception:
        rows = []
    target_ids.update(text(row.get("id")) for row in rows if text(row.get("id")))
    delivered = 0
    for target_id in target_ids:
        try:
            await _upsert_alert(db, target_id, {
                "alert_type": "customer_service_instruction_approval",
                "severity": "critical" if instruction.get("priority") == "urgent" else "warning",
                "title": f"تنفيذ بانتظار الموافقة للطلب #{instruction['order_number']}",
                "message": (
                    f"أرسل {text(instruction.get('submitted_by_name')) or 'الموظف'} تنفيذ المهمة: "
                    f"{instruction['note']}"
                ),
                "related_entity_type": "order",
                "related_entity_id": instruction["order_number"],
                "related_entity_url": f"/order-tracking-notes?order={instruction['order_number']}",
                "fingerprint": _fp(
                    "customer_service_instruction_approval",
                    "instruction",
                    instruction["id"],
                ),
                "metadata": {
                    "instruction_id": instruction["id"],
                    "order_number": instruction["order_number"],
                    "submitted_at": _iso(instruction.get("submitted_at")),
                },
            })
            delivered += 1
        except Exception:
            continue
    return delivered


async def _record_approval_submission(
    db: Any,
    *,
    merchant_id: str,
    instruction: dict[str, Any],
    actor_id: str,
    actor_name: str,
) -> None:
    await _sync_instruction_snapshot(
        db,
        merchant_id=merchant_id,
        instruction=instruction,
    )
    await db[ORDER_TRACKING_INSTRUCTION_EVENTS].insert_one({
        "id": uuid.uuid4().hex,
        "user_id": merchant_id,
        "order_number": instruction["order_number"],
        "instruction_id": instruction["id"],
        "event_type": "customer_service_instruction_submitted_for_approval",
        "actor_id": actor_id,
        "actor_name": actor_name,
        "note": instruction.get("submitted_result"),
        "occurred_at": instruction.get("submitted_at") or _now(),
    })
    await _notify_customer_service_approval(
        db,
        merchant_id=merchant_id,
        instruction=instruction,
    )


async def _resolve_instruction_alerts(
    db: Any,
    *,
    instruction_id: str,
    approval_only: bool = False,
) -> None:
    alert_types = ["customer_service_instruction_approval"]
    if not approval_only:
        alert_types.append("customer_service_instruction")
    fingerprints = [
        _fp(alert_type, "instruction", instruction_id)
        for alert_type in alert_types
    ]
    now = _iso(_now())
    try:
        await db.settlement_alerts.update_many(
            {
                "fingerprint": {"$in": fingerprints},
                "status": {"$in": ["new", "snoozed"]},
            },
            {"$set": {"status": "resolved", "resolved_at": now, "updated_at": now}},
        )
    except Exception:
        return


async def _create_operational_hold(
    db: Any,
    *,
    merchant_id: str,
    order_number: str,
    pieces: list[dict[str, Any]],
    instruction: dict[str, Any],
    actor: dict[str, Any],
) -> tuple[str | None, list[dict[str, Any]]]:
    eligible = [row for row in pieces if text(row.get("status")) in ACTIVE_PIECE_STATUSES]
    if not eligible:
        return None, []
    conflicting = [text(row.get("piece_id")) for row in eligible if text(row.get("active_hold_id"))]
    if conflicting:
        raise HTTPException(status_code=409, detail={
            "code": "fulfillment_stop_already_active",
            "piece_ids": conflicting,
        })
    hold_id = f"fulfillment-hold-{uuid.uuid4().hex}"
    now = _now()
    stop_type = "cancel" if instruction["action_type"] in {"delete_product", "cancel_order"} else "edit"
    before_states = [{
        "piece_id": text(row.get("piece_id")),
        "status": text(row.get("status")) or "assigned",
        "execution_status": text(row.get("execution_status")) or "assigned",
    } for row in eligible]
    piece_ids = [row["piece_id"] for row in before_states]
    patch = hold_piece_patch(
        hold_id=hold_id,
        stop_type=stop_type,
        note=instruction["note"],
        actor=actor,
        stopped_at=now,
    )
    updated = await db[PIECES].update_many(
        {
            "user_id": merchant_id,
            "piece_id": {"$in": piece_ids},
            "$or": [
                {"active_hold_id": {"$exists": False}},
                {"active_hold_id": None},
                {"active_hold_id": ""},
            ],
        },
        {"$set": patch},
    )
    if int(updated.modified_count) != len(piece_ids):
        before_by_id = {row["piece_id"]: row for row in before_states}
        for piece_id in piece_ids:
            await db[PIECES].update_one(
                {"user_id": merchant_id, "piece_id": piece_id, "active_hold_id": hold_id},
                release_piece_update(before_by_id[piece_id], now),
            )
        raise HTTPException(status_code=409, detail={"code": "fulfillment_stop_piece_conflict"})
    hold = {
        "id": hold_id,
        "user_id": merchant_id,
        "order_number": order_number,
        "scope": instruction["scope"],
        "target_id": instruction.get("target_id") or order_number,
        "stop_type": stop_type,
        "stop_label": STOP_TYPE_LABELS[stop_type],
        "note": instruction["note"],
        "status": "active",
        "piece_ids": piece_ids,
        "employee_ids": sorted({text(row.get("responsible_employee_id")) for row in eligible if text(row.get("responsible_employee_id"))}),
        "before_states": before_states,
        "created_at": now,
        "created_by": text(actor.get("id")),
        "created_by_name": text(actor.get("name") or actor.get("email")),
        "source": "customer_service_order_tracking",
        "instruction_id": instruction["id"],
        "mezan_only": True,
        "salla_updated": False,
        "qoyod_updated": False,
    }
    try:
        await db[FULFILLMENT_HOLDS].insert_one(dict(hold))
    except Exception:
        before_by_id = {row["piece_id"]: row for row in before_states}
        for piece_id in piece_ids:
            await db[PIECES].update_one(
                {"user_id": merchant_id, "piece_id": piece_id, "active_hold_id": hold_id},
                release_piece_update(before_by_id[piece_id], now),
            )
        raise
    return hold_id, before_states


async def _release_linked_hold(
    db: Any,
    *,
    merchant_id: str,
    instruction: dict[str, Any],
    actor: dict[str, Any],
    note: str,
) -> None:
    hold_id = text(instruction.get("hold_id"))
    if not hold_id:
        return
    hold = await db[FULFILLMENT_HOLDS].find_one(
        {"user_id": merchant_id, "id": hold_id}, {"_id": 0},
    )
    if not hold or text(hold.get("status")) != "active":
        return
    now = _now()
    restored: list[str] = []
    for before in hold.get("before_states") or []:
        piece_id = text(before.get("piece_id"))
        result = await db[PIECES].update_one(
            {"user_id": merchant_id, "piece_id": piece_id, "active_hold_id": hold_id},
            release_piece_update(before, now),
        )
        if result.modified_count:
            restored.append(piece_id)
    if len(restored) != len(hold.get("piece_ids") or []):
        raise HTTPException(status_code=409, detail={"code": "fulfillment_hold_release_piece_conflict"})
    await db[FULFILLMENT_HOLDS].update_one(
        {"user_id": merchant_id, "id": hold_id, "status": "active"},
        {"$set": {
            "status": "released",
            "released_at": now,
            "released_by": text(actor.get("id")),
            "released_by_name": text(actor.get("name") or actor.get("email")),
            "release_note": note or None,
            "updated_at": now,
        }},
    )


async def _materialize_driver_instruction(
    db: Any,
    *,
    merchant_id: str,
    instruction: dict[str, Any],
) -> str | None:
    if "store_courier" not in instruction["target_stages"]:
        return None
    assignment = await db.store_delivery_assignments.find_one(
        {
            "user_id": merchant_id,
            "$or": [
                {"order_id": instruction["order_number"]},
                {"order_number": instruction["order_number"]},
            ],
            "active": True,
            "status": {"$in": ["assigned", "out_for_delivery"]},
        },
        {"_id": 0},
    )
    if not assignment:
        return None
    instruction_id = f"tracking-{instruction['id']}"
    now = _iso(_now())
    await db[STORE_DELIVERY_INSTRUCTIONS].update_one(
        {"user_id": merchant_id, "id": instruction_id},
        {"$setOnInsert": {
            "id": instruction_id,
            "user_id": merchant_id,
            "order_id": assignment.get("order_id") or instruction["order_number"],
            "driver_id": assignment.get("driver_id"),
            "driver_name_snapshot": assignment.get("driver_name_snapshot"),
            "instruction_type": (
                "scheduled"
                if instruction.get("delivery_date")
                else "urgent"
                if instruction.get("priority") == "urgent"
                else "general"
            ),
            "priority": instruction["priority"],
            "note": instruction["note"],
            "delivery_date": instruction.get("delivery_date"),
            "delivery_time": instruction.get("delivery_time"),
            "status": "active",
            "acknowledged_at": None,
            "acknowledged_by_driver_id": None,
            "version": 1,
            "created_at": now,
            "created_by": instruction["created_by"],
            "updated_at": now,
            "source_tracking_instruction_id": instruction["id"],
        }},
        upsert=True,
    )
    return instruction_id


def make_order_tracking_notes_router(
    db: Any,
    current_user: Callable[..., Any],
) -> APIRouter:
    router = APIRouter(
        prefix="/order-tracking-notes",
        tags=["Order Tracking and Customer Service Instructions"],
    )
    repository = MongoOrderRepository(db)

    @router.get("/search")
    async def search_orders(
        q: str = Query(min_length=2, max_length=120),
        limit: int = Query(default=20, ge=1, le=50),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        actor = _require_customer_service(user)
        context = await _actor_context(db, actor)
        query = text(q).lstrip("#")
        pattern = re.compile(re.escape(query), re.IGNORECASE)
        rows = await db.unified_orders.find(
            {
                "user_id": context["merchant_id"],
                "raw_by_source.salla_direct": {"$exists": True},
                "$or": [
                    {"order_number": pattern},
                    {"customer_name": pattern},
                    {"customer_mobile": pattern},
                ],
            },
            {
                "_id": 0,
                "order_number": 1,
                "order_date": 1,
                "order_status": 1,
                "customer_name": 1,
                "customer_mobile": 1,
                "shipping_city": 1,
            },
        ).sort("order_date", -1).limit(limit).to_list(limit)
        return {"items": rows, "total": len(rows), "query": query}

    @router.get("/orders/{order_number}")
    async def order_tracking(
        order_number: str,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        actor = _require_customer_service(user)
        context = await _actor_context(db, actor)
        normalized = text(order_number).lstrip("#")
        try:
            order = await get_order(repository, user_id=context["merchant_id"], order_number=normalized)
        except OrderNotFoundError as exc:
            raise HTTPException(status_code=404, detail={"code": "order_tracking_order_not_found"}) from exc
        workflow = await db[WORKFLOWS].find_one(
            {"user_id": context["merchant_id"], "order_number": normalized}, {"_id": 0},
        ) or {"order_number": normalized, "stage": "pending_review"}
        pieces = await db[PIECES].find(
            {"user_id": context["merchant_id"], "order_number": normalized}, {"_id": 0, "image_b64": 0},
        ).sort([("experiment_archived_at", 1), ("order_item_id", 1), ("unit_index", 1)]).to_list(10000)
        active_pieces = [row for row in pieces if row.get("experiment_archived_at") is None]
        piece_ids = [text(row.get("piece_id")) for row in pieces if text(row.get("piece_id"))]
        events = await _events_for_order(
            db,
            user_id=context["merchant_id"],
            order_number=normalized,
            piece_ids=piece_ids,
        )
        events.extend(_workflow_events(workflow))
        unique_events = {row["id"]: row for row in events}
        events = sorted(unique_events.values(), key=lambda row: row.get("occurred_at") or "")
        instruction_rows = await db[ORDER_TRACKING_INSTRUCTIONS].find(
            {"user_id": context["merchant_id"], "order_number": normalized}, {"_id": 0},
        ).sort("created_at", -1).to_list(500)
        instruction_ids = [text(row.get("id")) for row in instruction_rows if text(row.get("id"))]
        evidence_rows = (
            await db[ORDER_TRACKING_EVIDENCE].find(
                {
                    "user_id": context["merchant_id"],
                    "instruction_id": {"$in": instruction_ids},
                },
                {"_id": 0, "content": 0, "user_id": 0},
            ).sort("created_at", 1).to_list(1000)
            if instruction_ids
            else []
        )
        evidence_by_instruction: dict[str, list[dict[str, Any]]] = {}
        for evidence in evidence_rows:
            evidence["url"] = f"/api/order-tracking-notes/evidence/{text(evidence.get('id'))}"
            evidence_by_instruction.setdefault(text(evidence.get("instruction_id")), []).append(evidence)
        for instruction in instruction_rows:
            instruction["evidence"] = evidence_by_instruction.get(text(instruction.get("id")), [])
        instruction_by_id = {
            text(row.get("id")): row
            for row in instruction_rows
            if text(row.get("id"))
        }
        holds = await db[FULFILLMENT_HOLDS].find(
            {"user_id": context["merchant_id"], "order_number": normalized}, {"_id": 0, "before_states": 0},
        ).sort("created_at", -1).to_list(200)
        order_dict = _model_dict(order)
        item_by_id = {
            text(row.get("order_item_id") or row.get("id")): row
            for row in order_dict.get("items") or []
            if text(row.get("order_item_id") or row.get("id"))
        }
        piece_views = []
        for piece in active_pieces:
            piece_id = text(piece.get("piece_id"))
            order_item_id = text(piece.get("order_item_id"))
            piece_event_rows = [
                row for row in events
                if (
                    row.get("piece_id") == piece_id
                    or piece_id in set(row.get("piece_ids") or [])
                    or (
                        not row.get("piece_id")
                        and not row.get("piece_ids")
                        and (
                            row.get("source") != "customer_service"
                            or instruction_targets(
                                instruction_by_id.get(text(row.get("instruction_id"))) or {"scope": "order"},
                                piece_id=piece_id,
                                order_item_id=order_item_id,
                            )
                        )
                    )
                )
            ]
            piece_views.append({
                "piece_id": piece_id,
                "order_item_id": order_item_id,
                "unit_index": piece.get("unit_index"),
                "product_id": piece.get("product_id"),
                "product_name": piece.get("product_name") or (item_by_id.get(order_item_id) or {}).get("name") or "منتج",
                "sku": piece.get("sku"),
                "image_url": piece.get("selected_image_url") or piece.get("resolved_image_url") or piece.get("image_url"),
                "status": piece.get("status"),
                "stage": _piece_stage(piece, workflow),
                "stage_label": (
                    "تم التوصيل"
                    if text(workflow.get("stage")) == "delivered"
                    else "جاري التوصيل"
                    if text(workflow.get("stage")) == "delivering"
                    else STAGE_LABELS.get(
                        _piece_stage(piece, workflow),
                        _piece_stage(piece, workflow),
                    )
                ),
                "custody": _piece_custody(piece, workflow, order_dict),
                "responsible_employee_id": piece.get("responsible_employee_id"),
                "responsible_employee_name": piece.get("responsible_employee_name"),
                "supplier_id": piece.get("supplier_id"),
                "supplier_name": piece.get("supplier_name"),
                "file_number": piece.get("file_number"),
                "batch_id": piece.get("batch_id"),
                "active_hold_id": piece.get("active_hold_id"),
                "hold_note": piece.get("hold_note"),
                "timeline": piece_event_rows,
                "instructions": [
                    public_instruction(row) for row in instruction_rows
                    if (
                        text(row.get("status")) in {"active", "waiting_customer_service_approval"}
                        and instruction_targets(
                            row,
                            piece_id=piece_id,
                            order_item_id=order_item_id,
                        )
                    )
                ],
            })
        return {
            "order": order_dict,
            "workflow": {
                key: value for key, value in workflow.items()
                if key not in {"_id", "user_id"}
            },
            "current_stage": _current_stage(workflow, active_pieces),
            "current_stage_label": _current_stage_label(workflow, active_pieces),
            "timeline": events,
            "pieces": piece_views,
            "archived_piece_count": len(pieces) - len(active_pieces),
            "instructions": [public_instruction(row) for row in instruction_rows],
            "holds": holds,
            "stage_options": [{"value": key, "label": value} for key, value in STAGE_LABELS.items()],
            "action_options": [{"value": key, "label": value} for key, value in ACTION_LABELS.items()],
            "capabilities": {
                "can_manage": _can_manage_tracking_instructions(actor, context),
            },
        }

    @router.post("/orders/{order_number}/instructions", status_code=201)
    async def create_instruction(
        order_number: str,
        payload: TrackingInstructionCreate,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        actor = _require_customer_service(user)
        context = await _actor_context(db, actor)
        if not _can_manage_tracking_instructions(actor, context):
            raise HTTPException(status_code=403, detail={"code": "customer_service_instruction_manage_permission_required"})
        if payload.action_type not in ACTION_TYPES:
            raise HTTPException(status_code=422, detail={"code": "customer_service_instruction_action_invalid"})
        if payload.priority not in NOTE_PRIORITIES:
            raise HTTPException(status_code=422, detail={"code": "customer_service_instruction_priority_invalid"})
        if payload.enforcement not in ENFORCEMENT_LEVELS:
            raise HTTPException(status_code=422, detail={"code": "customer_service_instruction_enforcement_invalid"})
        if payload.required_action not in REQUIRED_ACTIONS:
            raise HTTPException(status_code=422, detail={"code": "customer_service_instruction_required_action_invalid"})
        requested_target_ids = list(dict.fromkeys(
            [text(payload.target_id)] + [text(value) for value in payload.target_ids]
        ))
        requested_target_ids = [value for value in requested_target_ids if value]
        if payload.scope != "order" and not requested_target_ids:
            raise HTTPException(status_code=422, detail={"code": "customer_service_instruction_target_required"})
        if (
            payload.approval_required
            and payload.required_action == "none"
            and payload.action_type not in BLOCKING_ACTION_TYPES
        ):
            raise HTTPException(status_code=422, detail={"code": "customer_service_instruction_approval_action_required"})
        if payload.delivery_date:
            try:
                datetime.strptime(payload.delivery_date, "%Y-%m-%d")
            except ValueError as exc:
                raise HTTPException(status_code=422, detail={"code": "delivery_date_invalid"}) from exc
        if payload.delivery_time:
            try:
                datetime.strptime(payload.delivery_time, "%H:%M")
            except ValueError as exc:
                raise HTTPException(status_code=422, detail={"code": "delivery_time_invalid"}) from exc
        await ensure_order_tracking_instruction_indexes(db)
        normalized = text(order_number).lstrip("#")
        workflow = await db[WORKFLOWS].find_one(
            {"user_id": context["merchant_id"], "order_number": normalized}, {"_id": 0},
        ) or {"order_number": normalized, "stage": "pending_review"}
        try:
            order = await get_order(repository, user_id=context["merchant_id"], order_number=normalized)
        except OrderNotFoundError as exc:
            raise HTTPException(status_code=404, detail={"code": "order_tracking_order_not_found"}) from exc
        pieces = await db[PIECES].find(
            {
                "user_id": context["merchant_id"],
                "order_number": normalized,
                "$or": [{"experiment_archived_at": {"$exists": False}}, {"experiment_archived_at": None}],
            },
            {"_id": 0},
        ).to_list(10000)
        target_id_set = set(requested_target_ids)
        order_item_ids = {
            text(getattr(row, "order_item_id", None))
            for row in getattr(order, "items", None) or []
            if text(getattr(row, "order_item_id", None))
        }
        piece_ids = {
            text(row.get("piece_id")) for row in pieces if text(row.get("piece_id"))
        }
        if payload.scope == "item" and not target_id_set.issubset(order_item_ids):
            raise HTTPException(status_code=404, detail={"code": "customer_service_instruction_target_not_found"})
        if payload.scope == "piece" and not target_id_set.issubset(piece_ids):
            raise HTTPException(status_code=404, detail={"code": "customer_service_instruction_target_not_found"})
        if payload.scope == "piece":
            target_pieces = [row for row in pieces if text(row.get("piece_id")) in target_id_set]
        elif payload.scope == "item":
            target_pieces = [row for row in pieces if text(row.get("order_item_id")) in target_id_set]
        else:
            target_pieces = pieces
        if payload.scope != "order" and pieces and not target_pieces:
            raise HTTPException(status_code=404, detail={"code": "customer_service_instruction_target_not_found"})
        target_stages = list(payload.target_stages)
        if "current_stage" in target_stages:
            target_stages = [row for row in target_stages if row != "current_stage"]
            target_stages.insert(0, _current_stage(workflow, target_pieces or pieces))
            target_stages = list(dict.fromkeys(target_stages))
        now = _now()
        instruction_id = f"customer-instruction-{uuid.uuid4().hex}"
        effective_enforcement = (
            "completion_required"
            if (
                payload.required_action != "none"
                or payload.action_type in BLOCKING_ACTION_TYPES
            )
            else payload.enforcement
        )
        instruction = {
            "id": instruction_id,
            "user_id": context["merchant_id"],
            "order_number": normalized,
            "scope": payload.scope,
            "target_id": requested_target_ids[0] if requested_target_ids else normalized,
            "target_ids": requested_target_ids,
            "action_type": payload.action_type,
            "action_label": ACTION_LABELS[payload.action_type],
            "priority": payload.priority,
            "note": text(payload.note),
            "target_stages": target_stages,
            "target_stage_labels": [STAGE_LABELS[row] for row in target_stages],
            "enforcement": effective_enforcement,
            "required_action": payload.required_action,
            "approval_required": bool(
                payload.approval_required
                or payload.required_action == "arrival_confirmation"
                or payload.action_type in BLOCKING_ACTION_TYPES
            ),
            "delivery_date": payload.delivery_date,
            "delivery_time": payload.delivery_time,
            "status": "active",
            "operational_hold": payload.action_type in BLOCKING_ACTION_TYPES,
            "target_piece_ids": [text(row.get("piece_id")) for row in target_pieces if text(row.get("piece_id"))],
            "acknowledged_by_ids": [],
            "acknowledgment_history": [],
            "created_at": now,
            "created_by": context["actor_id"],
            "created_by_name": text(actor.get("name") or actor.get("email")) or "خدمة العملاء",
            "updated_at": now,
            "mezan_only": True,
            "salla_updated": False,
            "qoyod_updated": False,
        }
        await db[ORDER_TRACKING_INSTRUCTIONS].insert_one(dict(instruction))
        hold_id = None
        driver_instruction_id = None
        before_states: list[dict[str, Any]] = []
        try:
            if instruction["operational_hold"]:
                hold_id, before_states = await _create_operational_hold(
                    db,
                    merchant_id=context["merchant_id"],
                    order_number=normalized,
                    pieces=target_pieces,
                    instruction=instruction,
                    actor=actor,
                )
                if hold_id:
                    instruction["hold_id"] = hold_id
                    await db[ORDER_TRACKING_INSTRUCTIONS].update_one(
                        {"user_id": context["merchant_id"], "id": instruction_id},
                        {"$set": {"hold_id": hold_id, "updated_at": _now()}},
                    )
            snapshot = instruction_snapshot(instruction)
            workflow_update: dict[str, Any] = {
                "$addToSet": {"customer_service_instructions": snapshot},
                "$set": {"updated_at": _now()},
            }
            if instruction["operational_hold"]:
                workflow_update["$addToSet"]["customer_service_blocking_instruction_ids"] = instruction_id
                workflow_update["$set"].update({
                    "customer_service_hold_active": True,
                    "customer_service_hold_reason": instruction["note"],
                })
            await db[WORKFLOWS].update_one(
                {"user_id": context["merchant_id"], "order_number": normalized},
                workflow_update,
                upsert=True,
            )
            if target_pieces:
                await db[PIECES].update_many(
                    {"user_id": context["merchant_id"], "piece_id": {"$in": instruction["target_piece_ids"]}},
                    {"$addToSet": {"customer_service_instructions": snapshot}, "$set": {"updated_at": _now()}},
                )
            driver_instruction_id = await _materialize_driver_instruction(
                db,
                merchant_id=context["merchant_id"],
                instruction=instruction,
            )
            if driver_instruction_id:
                instruction["driver_instruction_id"] = driver_instruction_id
                await db[ORDER_TRACKING_INSTRUCTIONS].update_one(
                    {"user_id": context["merchant_id"], "id": instruction_id},
                    {"$set": {"driver_instruction_id": driver_instruction_id}},
                )
            event = {
                "id": uuid.uuid4().hex,
                "user_id": context["merchant_id"],
                "order_number": normalized,
                "instruction_id": instruction_id,
                "event_type": "customer_service_instruction_created",
                "actor_id": context["actor_id"],
                "actor_name": instruction["created_by_name"],
                "note": instruction["note"],
                "target_stages": target_stages,
                "enforcement": instruction["enforcement"],
                "occurred_at": now,
            }
            await db[ORDER_TRACKING_INSTRUCTION_EVENTS].insert_one(event)
            notification_count = await _notify_current_responsibilities(
                db,
                merchant_id=context["merchant_id"],
                workflow=workflow,
                pieces=target_pieces,
                instruction=instruction,
            )
        except Exception:
            if hold_id:
                before_by_id = {text(row.get("piece_id")): row for row in before_states}
                for piece_id, before in before_by_id.items():
                    await db[PIECES].update_one(
                        {"user_id": context["merchant_id"], "piece_id": piece_id, "active_hold_id": hold_id},
                        release_piece_update(before, _now()),
                    )
                await db[FULFILLMENT_HOLDS].delete_one({"user_id": context["merchant_id"], "id": hold_id})
            await db[ORDER_TRACKING_INSTRUCTIONS].delete_one({"user_id": context["merchant_id"], "id": instruction_id})
            await db[WORKFLOWS].update_one(
                {"user_id": context["merchant_id"], "order_number": normalized},
                {"$pull": {
                    "customer_service_instructions": {"id": instruction_id},
                    "customer_service_blocking_instruction_ids": instruction_id,
                }},
            )
            await db[PIECES].update_many(
                {"user_id": context["merchant_id"], "order_number": normalized},
                {"$pull": {"customer_service_instructions": {"id": instruction_id}}},
            )
            if instruction["operational_hold"]:
                remaining_holds = await db[ORDER_TRACKING_INSTRUCTIONS].count_documents({
                    "user_id": context["merchant_id"],
                    "order_number": normalized,
                    "status": {"$in": ["active", "waiting_customer_service_approval"]},
                    "operational_hold": True,
                })
                if not remaining_holds:
                    await db[WORKFLOWS].update_one(
                        {"user_id": context["merchant_id"], "order_number": normalized},
                        {"$set": {"customer_service_hold_active": False}, "$unset": {"customer_service_hold_reason": ""}},
                    )
            if driver_instruction_id:
                await db[STORE_DELIVERY_INSTRUCTIONS].delete_one({
                    "user_id": context["merchant_id"],
                    "id": driver_instruction_id,
                    "source_tracking_instruction_id": instruction_id,
                })
            raise
        return {
            "ok": True,
            "instruction": public_instruction(instruction),
            "notification_count": notification_count,
            "current_stage_resolved": _current_stage(workflow, target_pieces or pieces),
        }

    @router.post("/instructions/{instruction_id}/acknowledge")
    async def acknowledge_instruction(
        instruction_id: str,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        context = await _actor_context(db, user)
        instruction = await db[ORDER_TRACKING_INSTRUCTIONS].find_one(
            {"user_id": context["merchant_id"], "id": text(instruction_id), "status": "active"},
            {"_id": 0},
        )
        if not instruction:
            raise HTTPException(status_code=404, detail={"code": "customer_service_instruction_not_found"})
        now = _now()
        history = {
            "actor_id": context["actor_id"],
            "actor_name": text(user.get("name") or user.get("email")),
            "acknowledged_at": now,
        }
        updated = await db[ORDER_TRACKING_INSTRUCTIONS].find_one_and_update(
            {
                "user_id": context["merchant_id"],
                "id": instruction["id"],
                "status": {"$in": ["active", "waiting_customer_service_approval"]},
            },
            {
                "$addToSet": {"acknowledged_by_ids": context["actor_id"]},
                "$push": {"acknowledgment_history": history},
                "$set": {"last_acknowledged_at": now, "updated_at": now},
            },
            return_document=ReturnDocument.AFTER,
            projection={"_id": 0, "user_id": 0},
        )
        await _sync_instruction_snapshot(
            db,
            merchant_id=context["merchant_id"],
            instruction=updated,
        )
        await db[ORDER_TRACKING_INSTRUCTION_EVENTS].insert_one({
            "id": uuid.uuid4().hex,
            "user_id": context["merchant_id"],
            "order_number": instruction["order_number"],
            "instruction_id": instruction["id"],
            "event_type": "customer_service_instruction_acknowledged",
            "actor_id": context["actor_id"],
            "actor_name": history["actor_name"],
            "occurred_at": now,
        })
        return {"ok": True, "instruction": updated}

    @router.post("/instructions/{instruction_id}/evidence")
    async def upload_instruction_evidence(
        instruction_id: str,
        files: list[UploadFile] = File(default=[]),
        result_note: str = Form(default=""),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        context = await _actor_context(db, user)
        instruction = await db[ORDER_TRACKING_INSTRUCTIONS].find_one(
            {
                "user_id": context["merchant_id"],
                "id": text(instruction_id),
                "status": {"$in": ["active", "waiting_customer_service_approval"]},
            },
            {"_id": 0},
        )
        if not instruction:
            raise HTTPException(status_code=404, detail={"code": "customer_service_instruction_not_found"})
        required_action = text(instruction.get("required_action")) or "none"
        if required_action not in {"upload_photos", "upload_photos_and_result"}:
            raise HTTPException(status_code=409, detail={"code": "customer_service_instruction_photo_evidence_not_requested"})
        if not files or len(files) > MAX_EVIDENCE_IMAGES:
            raise HTTPException(status_code=422, detail={
                "code": "customer_service_instruction_photo_count_invalid",
                "max_images": MAX_EVIDENCE_IMAGES,
            })
        if required_action == "upload_photos_and_result" and not text(result_note):
            raise HTTPException(status_code=422, detail={"code": "customer_service_instruction_result_required"})
        await ensure_order_tracking_instruction_indexes(db)
        now = _now()
        saved = []
        try:
            for upload in files:
                declared = text(upload.content_type).casefold()
                if declared not in ALLOWED_EVIDENCE_TYPES:
                    raise HTTPException(status_code=415, detail={"code": "customer_service_instruction_evidence_image_required"})
                data = await upload.read(MAX_EVIDENCE_IMAGE_BYTES + 1)
                if not data or len(data) > MAX_EVIDENCE_IMAGE_BYTES:
                    raise HTTPException(status_code=413, detail={"code": "customer_service_instruction_evidence_size_invalid"})
                detected = _detected_image_type(data)
                if detected != declared:
                    raise HTTPException(status_code=415, detail={"code": "customer_service_instruction_evidence_signature_invalid"})
                evidence_id = f"customer-evidence-{uuid.uuid4().hex}"
                row = {
                    "id": evidence_id,
                    "user_id": context["merchant_id"],
                    "instruction_id": instruction["id"],
                    "order_number": instruction["order_number"],
                    "piece_ids": list(instruction.get("target_piece_ids") or []),
                    "filename": text(upload.filename)[:180],
                    "content_type": detected,
                    "size": len(data),
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "content": Binary(data),
                    "status": "submitted",
                    "created_at": now,
                    "created_by": context["actor_id"],
                    "created_by_name": text(user.get("name") or user.get("email")),
                }
                await db[ORDER_TRACKING_EVIDENCE].insert_one(row)
                saved.append({
                    "id": evidence_id,
                    "url": f"/api/order-tracking-notes/evidence/{evidence_id}",
                    "content_type": detected,
                    "size": len(data),
                })
        except Exception:
            if saved:
                await db[ORDER_TRACKING_EVIDENCE].delete_many({
                    "user_id": context["merchant_id"],
                    "id": {"$in": [row["id"] for row in saved]},
                })
            raise
        status_value = (
            "waiting_customer_service_approval"
            if instruction.get("approval_required")
            else "active"
        )
        updated = await db[ORDER_TRACKING_INSTRUCTIONS].find_one_and_update(
            {"user_id": context["merchant_id"], "id": instruction["id"]},
            {
                "$set": {
                    "status": status_value,
                    "submitted_at": now,
                    "submitted_by": context["actor_id"],
                    "submitted_by_name": text(user.get("name") or user.get("email")),
                    "submitted_result": text(result_note) or None,
                    "updated_at": now,
                },
                "$push": {"evidence_ids": {"$each": [row["id"] for row in saved]}},
            },
            return_document=ReturnDocument.AFTER,
            projection={"_id": 0, "user_id": 0},
        )
        if not instruction.get("approval_required"):
            return await complete_instruction(
                instruction["id"],
                InstructionActionRequest(note=text(result_note) or "تم رفع الصور المطلوبة"),
                user,
            )
        await _record_approval_submission(
            db,
            merchant_id=context["merchant_id"],
            instruction=updated,
            actor_id=context["actor_id"],
            actor_name=text(user.get("name") or user.get("email")),
        )
        return {
            "ok": True,
            "waiting_customer_service_approval": True,
            "instruction": updated,
            "evidence": saved,
        }

    @router.get("/evidence/{evidence_id}")
    async def get_instruction_evidence(
        evidence_id: str,
        user: dict = Depends(current_user),
    ) -> Response:
        context = await _actor_context(db, user)
        row = await db[ORDER_TRACKING_EVIDENCE].find_one({
            "user_id": context["merchant_id"],
            "id": text(evidence_id),
        })
        if not row:
            raise HTTPException(status_code=404, detail={"code": "customer_service_instruction_evidence_not_found"})
        return Response(
            content=bytes(row["content"]),
            media_type=text(row.get("content_type")) or "image/jpeg",
            headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
        )

    @router.post("/instructions/{instruction_id}/approve")
    async def approve_instruction(
        instruction_id: str,
        payload: InstructionApprovalRequest,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        actor = _require_customer_service(user)
        context = await _actor_context(db, actor)
        if not _can_manage_tracking_instructions(actor, context):
            raise HTTPException(status_code=403, detail={"code": "customer_service_instruction_manage_permission_required"})
        instruction = await db[ORDER_TRACKING_INSTRUCTIONS].find_one(
            {
                "user_id": context["merchant_id"],
                "id": text(instruction_id),
                "status": "waiting_customer_service_approval",
            },
            {"_id": 0},
        )
        if not instruction:
            raise HTTPException(status_code=404, detail={"code": "customer_service_instruction_waiting_approval_not_found"})
        return await complete_instruction(
            instruction["id"],
            InstructionActionRequest(note=text(payload.note) or "وافقت خدمة العملاء على التنفيذ"),
            actor,
        )

    @router.post("/instructions/{instruction_id}/reject")
    async def reject_instruction(
        instruction_id: str,
        payload: InstructionApprovalRequest,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        actor = _require_customer_service(user)
        context = await _actor_context(db, actor)
        if not _can_manage_tracking_instructions(actor, context):
            raise HTTPException(status_code=403, detail={"code": "customer_service_instruction_manage_permission_required"})
        reason = text(payload.note)
        if not reason:
            raise HTTPException(status_code=422, detail={"code": "customer_service_instruction_rejection_reason_required"})
        now = _now()
        updated = await db[ORDER_TRACKING_INSTRUCTIONS].find_one_and_update(
            {
                "user_id": context["merchant_id"],
                "id": text(instruction_id),
                "status": "waiting_customer_service_approval",
            },
            {"$set": {
                "status": "active",
                "rejected_at": now,
                "rejected_by": context["actor_id"],
                "rejected_by_name": text(actor.get("name") or actor.get("email")),
                "rejection_note": reason,
                "updated_at": now,
            }},
            return_document=ReturnDocument.AFTER,
            projection={"_id": 0, "user_id": 0},
        )
        if not updated:
            raise HTTPException(status_code=404, detail={"code": "customer_service_instruction_waiting_approval_not_found"})
        await _sync_instruction_snapshot(
            db,
            merchant_id=context["merchant_id"],
            instruction=updated,
        )
        await db[ORDER_TRACKING_INSTRUCTION_EVENTS].insert_one({
            "id": uuid.uuid4().hex,
            "user_id": context["merchant_id"],
            "order_number": updated["order_number"],
            "instruction_id": updated["id"],
            "event_type": "customer_service_instruction_rejected",
            "actor_id": context["actor_id"],
            "actor_name": text(actor.get("name") or actor.get("email")),
            "note": reason,
            "occurred_at": now,
        })
        await _resolve_instruction_alerts(
            db,
            instruction_id=updated["id"],
            approval_only=True,
        )
        return {"ok": True, "instruction": updated}

    @router.post("/instructions/{instruction_id}/complete")
    async def complete_instruction(
        instruction_id: str,
        payload: InstructionActionRequest,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        context = await _actor_context(db, user)
        instruction = await db[ORDER_TRACKING_INSTRUCTIONS].find_one(
            {"user_id": context["merchant_id"], "id": text(instruction_id)}, {"_id": 0},
        )
        if not instruction:
            raise HTTPException(status_code=404, detail={"code": "customer_service_instruction_not_found"})
        can_manage_instruction = _can_manage_tracking_instructions(user, context)
        if text(instruction.get("status")) not in {"active", "waiting_customer_service_approval"}:
            if text(instruction.get("status")) == "completed":
                repaired_at = _now()
                await db[WORKFLOWS].update_one(
                    {"user_id": context["merchant_id"], "order_number": instruction["order_number"]},
                    {
                        "$pull": {
                            "customer_service_instructions": {"id": instruction["id"]},
                            "customer_service_blocking_instruction_ids": instruction["id"],
                        },
                        "$set": {"updated_at": repaired_at},
                    },
                )
                await db[PIECES].update_many(
                    {"user_id": context["merchant_id"], "order_number": instruction["order_number"]},
                    {"$pull": {"customer_service_instructions": {"id": instruction["id"]}}, "$set": {"updated_at": repaired_at}},
                )
                remaining_holds = await db[ORDER_TRACKING_INSTRUCTIONS].count_documents({
                    "user_id": context["merchant_id"],
                    "order_number": instruction["order_number"],
                    "status": {"$in": ["active", "waiting_customer_service_approval"]},
                    "operational_hold": True,
                })
                if not remaining_holds:
                    await db[WORKFLOWS].update_one(
                        {"user_id": context["merchant_id"], "order_number": instruction["order_number"]},
                        {"$set": {"customer_service_hold_active": False}, "$unset": {"customer_service_hold_reason": ""}},
                    )
                if text(instruction.get("driver_instruction_id")):
                    await db[STORE_DELIVERY_INSTRUCTIONS].update_one(
                        {"user_id": context["merchant_id"], "id": instruction["driver_instruction_id"]},
                        {"$set": {"status": "completed", "updated_at": _iso(repaired_at)}},
                    )
                await _resolve_instruction_alerts(db, instruction_id=instruction["id"])
            return {"ok": True, "instruction": public_instruction(instruction), "idempotent_replay": True}
        required_action = text(instruction.get("required_action")) or "none"
        completion_note = text(payload.note)
        if required_action in {"write_result", "upload_photos_and_result"} and not completion_note:
            raise HTTPException(status_code=422, detail={"code": "customer_service_instruction_result_required"})
        if required_action in {"upload_photos", "upload_photos_and_result"}:
            evidence_count = await db[ORDER_TRACKING_EVIDENCE].count_documents({
                "user_id": context["merchant_id"],
                "instruction_id": instruction["id"],
                "status": "submitted",
            })
            if not evidence_count:
                raise HTTPException(status_code=409, detail={"code": "customer_service_instruction_photo_evidence_required"})
        if instruction.get("approval_required") and not can_manage_instruction:
            now = _now()
            updated = await db[ORDER_TRACKING_INSTRUCTIONS].find_one_and_update(
                {"user_id": context["merchant_id"], "id": instruction["id"], "status": "active"},
                {"$set": {
                    "status": "waiting_customer_service_approval",
                    "submitted_at": now,
                    "submitted_by": context["actor_id"],
                    "submitted_by_name": text(user.get("name") or user.get("email")),
                    "submitted_result": completion_note or None,
                    "updated_at": now,
                }},
                return_document=ReturnDocument.AFTER,
                projection={"_id": 0, "user_id": 0},
            )
            if updated:
                await _record_approval_submission(
                    db,
                    merchant_id=context["merchant_id"],
                    instruction=updated,
                    actor_id=context["actor_id"],
                    actor_name=text(user.get("name") or user.get("email")),
                )
            return {"ok": True, "waiting_customer_service_approval": True, "instruction": updated}
        if instruction.get("operational_hold") and not can_manage_instruction:
            raise HTTPException(status_code=403, detail={"code": "fulfillment_hold_release_permission_required"})
        await _release_linked_hold(
            db,
            merchant_id=context["merchant_id"],
            instruction=instruction,
            actor=user,
            note=completion_note,
        )
        now = _now()
        updated = await db[ORDER_TRACKING_INSTRUCTIONS].find_one_and_update(
            {
                "user_id": context["merchant_id"],
                "id": instruction["id"],
                "status": {"$in": ["active", "waiting_customer_service_approval"]},
            },
            {"$set": {
                "status": "completed",
                "completed_at": now,
                "completed_by": context["actor_id"],
                "completed_by_name": text(user.get("name") or user.get("email")),
                "completion_note": completion_note or None,
                "updated_at": now,
            }},
            return_document=ReturnDocument.AFTER,
            projection={"_id": 0, "user_id": 0},
        )
        await db[WORKFLOWS].update_one(
            {"user_id": context["merchant_id"], "order_number": instruction["order_number"]},
            {
                "$pull": {
                    "customer_service_instructions": {"id": instruction["id"]},
                    "customer_service_blocking_instruction_ids": instruction["id"],
                },
                "$set": {"updated_at": now},
            },
        )
        remaining_blocks = await db[ORDER_TRACKING_INSTRUCTIONS].count_documents({
            "user_id": context["merchant_id"],
            "order_number": instruction["order_number"],
            "status": {"$in": ["active", "waiting_customer_service_approval"]},
            "operational_hold": True,
        })
        if not remaining_blocks:
            await db[WORKFLOWS].update_one(
                {"user_id": context["merchant_id"], "order_number": instruction["order_number"]},
                {"$set": {"customer_service_hold_active": False}, "$unset": {"customer_service_hold_reason": ""}},
            )
        await db[PIECES].update_many(
            {"user_id": context["merchant_id"], "order_number": instruction["order_number"]},
            {"$pull": {"customer_service_instructions": {"id": instruction["id"]}}, "$set": {"updated_at": now}},
        )
        if text(instruction.get("driver_instruction_id")):
            await db[STORE_DELIVERY_INSTRUCTIONS].update_one(
                {"user_id": context["merchant_id"], "id": instruction["driver_instruction_id"]},
                {"$set": {"status": "completed", "updated_at": _iso(now)}},
            )
        await db[ORDER_TRACKING_INSTRUCTION_EVENTS].insert_one({
            "id": uuid.uuid4().hex,
            "user_id": context["merchant_id"],
            "order_number": instruction["order_number"],
            "instruction_id": instruction["id"],
            "event_type": "customer_service_instruction_completed",
            "actor_id": context["actor_id"],
            "actor_name": text(user.get("name") or user.get("email")),
            "note": completion_note or None,
            "occurred_at": now,
        })
        await _resolve_instruction_alerts(
            db,
            instruction_id=instruction["id"],
        )
        return {"ok": True, "instruction": updated}

    return router


__all__ = [
    "TrackingInstructionCreate",
    "ensure_order_tracking_instruction_indexes",
    "make_order_tracking_notes_router",
]
