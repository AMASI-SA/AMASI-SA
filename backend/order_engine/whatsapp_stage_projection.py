"""Minimal customer-safe Mezan stage facts from a canonical OrderDTO.

This pure function NEVER authenticates Chatwoot or issues an HTTP request.
Caller must derive sender from trusted live Chatwoot inbound event and
authenticate the service request before lookup. Do not feed raw OrderDTO to AI.
"""
from datetime import datetime, timezone
import re
from .customer_history import normalize_saudi_mobile

ORDER_REF = re.compile(r"[1-9][0-9]{5,17}\Z", re.ASCII)
STAGES = {
    "انتظار الدفع": ("awaiting_payment", "الطلب بانتظار الدفع."),
    "بانتظار الدفع": ("awaiting_payment", "الطلب بانتظار الدفع."),
    "pending_payment": ("awaiting_payment", "الطلب بانتظار الدفع."),
    "انتظار المراجعة": ("awaiting_review", "الطلب ينتظر المراجعة."),
    "بانتظار المراجعة": ("awaiting_review", "الطلب ينتظر المراجعة."),
    "بإنتظار المراجعة": ("awaiting_review", "الطلب ينتظر المراجعة."),
    "تم المراجعة": ("reviewed", "الطلب تمت مراجعته."),
    "تمت المراجعة": ("reviewed", "الطلب تمت مراجعته."),
    "قيد التنفيذ": ("preparing", "الطلب قيد التجهيز."),
    "جاري التنفيذ": ("preparing", "الطلب قيد التجهيز."),
    "تم التنفيذ": ("prepared", "الطلب اكتمل تجهيزه، ولم يثبت شحنه بعد."),
    "تم الشحن": ("shipped", "تم شحن الطلب."),
    "جاري التوصيل": ("out_for_delivery", "الطلب جارٍ توصيله."),
    "تم التوصيل": ("delivered", "الطلب مسجل على أنه تم توصيله."),
    "طلب إلغاء": ("cancellation_requested", "طلب الإلغاء قيد المراجعة."),
    "طلب الغاء": ("cancellation_requested", "طلب الإلغاء قيد المراجعة."),
    "ملغي": ("cancelled", "الطلب ملغي."),
    "ملغى": ("cancelled", "الطلب ملغي."),
    "مسترجع": ("returned", "الطلب مسترجع."),
    "تم الاسترجاع": ("returned", "الطلب مسترجع."),
}
FINAL = frozenset(("delivered", "cancelled", "returned"))
MAX_FRESHNESS = 86400
SKEW = 300


class StageNotVerified(ValueError):
    pass


def stage_fact(dto, *, order_number, verified_sender, source_checked_at, now):
    """Emit a narrowly-scoped stage only for an exact verified buyer match.

    This does not authorize via contact fields or order number supplied by
    the LLM. Those MUST originate from validated Chatwoot/Mezan boundaries.
    """
    def deny():
        raise StageNotVerified("verification_required")
    if (type(order_number) is not str or ORDER_REF.fullmatch(order_number) is None
            or type(verified_sender) is not str
            or type(now) is not int or type(source_checked_at) is not int
            or source_checked_at > now + SKEW
            or now - source_checked_at > MAX_FRESHNESS):
        deny()
    if dto is None or getattr(dto, "order_number", None) != order_number:
        deny()
    customer = getattr(dto, "customer", None)
    buyer = normalize_saudi_mobile(getattr(customer, "mobile", None))
    sender = normalize_saudi_mobile(verified_sender)
    if not buyer or not sender or sender != buyer:
        deny()
    native = getattr(dto, "status_native", None)
    if type(native) is not str or native not in STAGES:
        raise StageNotVerified("status_unavailable")
    stage, detail = STAGES[native]
    updated = getattr(dto, "engine_updated_at", None)
    # engine_updated_at is a Mezan projection time, NOT proof of upstream
    # Salla freshness. The caller must separately verify source freshness.
    if not isinstance(updated, datetime) or updated.tzinfo is None:
        raise StageNotVerified("status_unavailable")
    stamp = int(updated.astimezone(timezone.utc).timestamp())
    if stamp > now + SKEW or (stage not in FINAL and now - stamp > MAX_FRESHNESS):
        raise StageNotVerified("status_unavailable")
    return {
        "schema": "amasi_verified_order_stage_v1",
        "stage": stage,
        "stage_description_ar": detail,
        "last_mezan_update_utc": updated.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "has_verified_tracking": False,
        "eta_verified": False,
        "order_changes_allowed": False,
    }
