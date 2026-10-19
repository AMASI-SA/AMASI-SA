"""Safe status-specific receiving explanations; no eligibility or writes."""
from typing import Any

LABELS = {
    "in_progress": "قيد التنفيذ", "under_review": "بانتظار المراجعة",
    "completed": "تم التنفيذ", "shipped": "تم الشحن",
    "delivering": "جاري التوصيل", "delivered": "تم التوصيل",
    "cancelled": "ملغى", "refunded": "مسترجع", "deleted": "محذوف",
}
ALIASES = {**{label: key for key, label in LABELS.items()},
    "جاري التنفيذ": "in_progress", "جاري الشحن": "shipped", "مشحون": "shipped",
    "قيد التوصيل": "delivering", "ملغي": "cancelled", "canceled": "cancelled",
    "pending_review": "under_review", "waiting_review": "under_review"}


def _values(value: Any) -> set[str]:
    if isinstance(value, dict):
        effective = value.get("customized")
        if effective:
            return _values(effective)
        result = set()
        for key in ("slug", "name", "label"):
            result |= _values(value.get(key))
        return result
    text = str(value or "").strip().lower()
    return {ALIASES.get(text, text)} if text else set()


async def observed_salla_status(db: Any, *, user_id: str, order_number: str) -> str | None:
    """Return consistent known status evidence for messages, never eligibility."""
    source = await db["unified_orders"].find_one({"user_id": user_id, "order_number": order_number}, {
        "_id": 0, "order_status": 1, "order_status_slug": 1,
        "raw_by_source.salla_direct.status": 1, "raw_by_source.salla_direct.status_slug": 1,
        "g47_salla_snapshot.requires_authoritative_refresh": 1,
    }) or {}
    raw = (source.get("raw_by_source") or {}).get("salla_direct") or {}
    provider = _values(raw.get("status")) | _values(raw.get("status_slug"))
    values = provider | _values(source.get("order_status")) | _values(source.get("order_status_slug"))
    stale = (source.get("g47_salla_snapshot") or {}).get("requires_authoritative_refresh")
    status = next(iter(values)) if provider and len(values) == 1 and not stale else None
    return status if status in LABELS else None


async def receipt_status_rejection(db: Any, *, user_id: str, order_number: str, code: str) -> dict:
    status = await observed_salla_status(db, user_id=user_id, order_number=order_number)
    label = LABELS.get(status)
    if label and status != "in_progress":
        message = f"حالة الطلب في سلة: «{label}». الطلب غير مؤهل للاستلام أو التجهيز؛ يلزم أن تكون حالته «قيد التنفيذ»."
    elif status == "in_progress":
        message = "حالة الطلب في سلة «قيد التنفيذ»، لكن اعتماد المراجعة أو متطلبات التجهيز في ميزان لم تكتمل. حدّث الطلب وتحقق من سجل المراجعة قبل الاستلام."
    else:
        message = "تعذّر التأكد من حالة الطلب الحالية في سلة. لم يتم استلام القطعة؛ حدّث بيانات الطلب ثم أعد المحاولة."
    return {"code": code, "message": message, "order_number": order_number,
            "order_status": status if label else None, "order_status_label": label}
