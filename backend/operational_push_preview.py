"""Bounded Arabic phone previews from an authorized, durable inbox snapshot.

Direction marks guide Unicode paragraph ordering; the OS controls the actual
notification layout. A preview never hydrates the current instruction or task.
"""
from __future__ import annotations

import unicodedata

from mobile_app_permissions import mobile_app_access_for_user
from review_session_fence import merchant_for_actor


NOTIFICATIONS = "operational_instruction_notifications_v1"
RLM, LRI, FSI, PDI = "\u200f", "\u2066", "\u2068", "\u2069"
TITLE_BYTES, BODY_BYTES = 240, 720
SYSTEM = "تحديثات النظام"

ACTION_LABELS = {
    "general": "ملاحظة عامة",
    "urgent_preparation": "استعجال التجهيز",
    "delivery_instruction": "تعليمات التوصيل",
    "edit_product": "إيقاف لتعديل منتج",
    "edit_order": "إيقاف لتعديل الطلب",
    "delete_product": "إيقاف لحذف منتج",
    "cancel_order": "إلغاء الطلب",
}
EVENT_LABELS = {
    "created": "تعليمات جديدة", "assigned": "مهمة جديدة", "comment": "تعليق جديد",
    "stop_acknowledged": "تم إيقاف التجهيز", "edit_sent": "تعديل جديد",
    "submitted": "تأكيد تنفيذ", "approved": "تمت الموافقة", "rejected": "رُفض التنفيذ",
    "completed": "اكتملت المهمة", "closed": "أُغلقت الملاحظة",
    "acknowledged": "تم الاطلاع", "progress": "تحديث التجهيز",
}
EVENT_SUMMARIES = {
    "created": "تعليمات جديدة للطلب", "assigned": "تعليمات تخص مهمتك الحالية",
    "comment": "تعليق جديد على المنتج", "stop_acknowledged": "تم إيقاف التجهيز",
    "edit_sent": "وصل تعديل جديد للطلب", "submitted": "تنفيذ بانتظار موافقة خدمة العملاء",
    "approved": "تمت الموافقة وفك الإيقاف؛ استكمل التجهيز",
    "rejected": "رُفض التنفيذ؛ راجع سبب الرفض", "completed": "اكتملت التعليمات وفُك الإيقاف",
    "closed": "أُغلقت الملاحظة", "acknowledged": "تم الاطلاع على التعليمات",
    "progress": "تحديث تقدم تجهيز المنتج",
}
STAGE_LABELS = {
    "pending_review": "انتظار المراجعة", "reviewed": "تمت المراجعة", "preparation": "التجهيز",
    "supplier_dispatch": "الإرسال إلى المورد", "supplier_receiving": "الاستلام من المورد",
    "preparation_receiving": "الاستلام من موظف التجهيز", "assembly_labeling": "التجميع والعنونة",
    "carrier_handoff": "تسليم الشحنة لشركة الشحن", "store_courier": "موصل المتجر",
}
RESPONSE_EVENTS = {"submitted", "approved", "completed", "closed"}
COMPLETION_EVENTS = {"approved", "completed", "closed"}


def _plain(value):
    if not isinstance(value, str):
        return ""
    # Strip input formatting/control characters before introducing our own
    # balanced isolates. Never stringify dictionaries or arbitrary metadata.
    value = unicodedata.normalize("NFC", value[:8192])
    return " ".join("".join(
        " " if char.isspace() else char
        for char in value if char.isspace() or unicodedata.category(char) not in {"Cc", "Cf", "Cs"}
    ).split())


def _clip(value, byte_limit):
    raw = value.encode("utf-8")
    if len(raw) <= byte_limit:
        return value
    return raw[:byte_limit - 3].decode("utf-8", errors="ignore").rstrip() + "…"


def _progress_summary(row):
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    previous = metadata.get("previous") if isinstance(metadata.get("previous"), dict) else {}
    current = metadata.get("current") if isinstance(metadata.get("current"), dict) else {}
    before = STAGE_LABELS.get(_plain(previous.get("stage")))
    after = STAGE_LABELS.get(_plain(current.get("stage")))
    if before and after and before != after:
        return f"انتقل المنتج من {before} إلى {after}"
    return EVENT_SUMMARIES["progress"]


def format_notification_preview(row):
    """Allowlisted text only; the caller must authorize the saved event first."""
    event = _plain(row.get("event_type"))
    versioned = type(row.get("event_message_version")) is int and row["event_message_version"] == 1
    sender = _plain(row.get("sender_name")) or SYSTEM
    if event == "progress" or row.get("sender_id") == "__system__" or (event in COMPLETION_EVENTS and not versioned):
        sender = SYSTEM
    label = EVENT_LABELS.get(event, "تحديث مهمة")
    action = ACTION_LABELS.get(_plain(row.get("action_type")))
    if action and event in {"created", "assigned", "edit_sent"}:
        suffix = f" — {action}"
    else:
        suffix = f" — {label}" + (f" · {action}" if action else "")
    name_budget = min(96, TITLE_BYTES - len((RLM + FSI + PDI + suffix).encode("utf-8")))
    title = RLM + FSI + _clip(sender, name_budget) + PDI + suffix

    summary = EVENT_SUMMARIES.get(event, "لديك تحديث على مهمة")
    if event == "progress":
        message = _progress_summary(row)
    elif event == "acknowledged" or (event in RESPONSE_EVENTS and not versioned):
        message = summary
    else:
        message = _plain(row.get("message")) or summary
    order = _clip(_plain(row.get("order_number")), 64)
    order_context = "\n" + RLM + "الطلب " + LRI + order + PDI if order else ""
    message_budget = BODY_BYTES - len((RLM + FSI + PDI + order_context).encode("utf-8"))
    body = RLM + FSI + _clip(message, message_budget) + PDI + order_context
    return {"title": title, "body": body}


async def native_notification_payload(db, device, job, generic_payload):
    """Return a scoped preview, the legacy generic payload, or None to discard.

    The caller already checked active device/account/session and generation.
    Only a genuinely absent legacy snapshot retains generic dispatch without
    the additional rich-content grants. A read or mismatched existing event
    never uses that compatibility fallback. This function must not run when
    polling an existing provider ticket, nor for web notifications.
    """
    if job.get("actor_id") != device.get("actor_id"):
        return None
    if job.get("merchant_id") is not None and job["merchant_id"] != device.get("merchant_id"):
        return None
    # Durable inbox writes use the same immutable event hash for _id and id.
    # Both reads use that primary key; the unscoped existence check returns no
    # content and only distinguishes absence from an inaccessible/read event.
    identity = job["event_id"]
    row = await db[NOTIFICATIONS].find_one({
        "_id": identity, "id": identity, "merchant_id": device["merchant_id"],
        "recipient_id": device["actor_id"], "instruction_id": job["instruction_id"], "read_at": None,
    }, {
        "_id": 0, "sender_id": 1, "sender_name": 1, "event_type": 1, "action_type": 1,
        "event_message_version": 1, "order_number": 1, "message": 1,
        "metadata.previous.stage": 1, "metadata.current.stage": 1,
    })
    if row is None:
        exists = await db[NOTIFICATIONS].find_one({"_id": identity}, {"_id": 1})
        return None if exists else generic_payload
    if job.get("merchant_id") != device["merchant_id"]:
        return None
    actor = await db["users"].find_one({"id": device["actor_id"]}, {
        "_id": 0, "id": 1, "role": 1, "is_owner": 1, "created_by": 1,
        "disabled": 1, "is_active": 1, "deleted_at": 1,
    })
    if not actor or actor.get("disabled") or actor.get("is_active") is False or actor.get("deleted_at"):
        return None
    if await merchant_for_actor(db, actor) != device["merchant_id"]:
        return None
    # Exactly the native GET /order-tracking-notes/notifications/me policy:
    # owner and store-driver exceptions; other staff need enabled app access
    # and at least one effective grant, without a current-assignment restriction.
    role = str(actor.get("role") or "").strip().casefold()
    if role not in {"owner", "store_driver"} and actor.get("is_owner") is not True:
        access = await mobile_app_access_for_user(db, actor)
        if not access.get("enabled") or not access.get("permissions"):
            return None
    return {**generic_payload, **format_notification_preview(row)}
