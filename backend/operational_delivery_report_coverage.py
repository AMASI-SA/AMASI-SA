"""Read-only delivery-source diagnostics; never post or recalculate money.

The v1 marker proves that the saved snapshot was produced by the delivery-event
reader. It is not evidence of a bank receipt, a complete reconciliation, or a
production release. Monetary values stay owned by operational_balance_service.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any


UNKNOWN_SCOPE = (
    "تغطية التسليم بعد بدء التشغيل غير مثبتة في هذه اللقطة؛ "
    "لا يستبدل تاريخ إنشاء الطلب أو تحديثه تاريخ التسليم المثبت."
)
BANK_REVIEW_GAP = (
    "ربط تحصيل الشبكة والتحويل بالبنك أو ذمة الشبكة التشغيلية غير مكتمل؛ "
    "اعتماد الإيصال وحده لا يثبت إضافة رصيد، ولا يحول المبلغ إلى عهدة كاش على المندوب."
)


def _instant(value: Any) -> datetime | None:
    """Only explicit, timezone-aware source instants are accepted."""
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and ("T" in value or " " in value):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def delivery_coverage_diagnostics(
    state: Mapping[str, Any], projection: Mapping[str, Any]
) -> dict[str, Any]:
    """Describe the exact saved snapshot without promoting unknown money.

    ``ready`` means reader support at this snapshot, not completeness of all
    business records. Existing projection issues retain an incomplete status.
    A separate, explicit dependency keeps the unfinished noncash bridge visible.
    The public result contains no account IDs, personal details or raw evidence.
    """
    if not isinstance(state, Mapping) or not isinstance(projection, Mapping):
        raise TypeError("operational_snapshot_mapping_required")

    engine = state.get("engine")
    marker = engine.get("delivery_event_coverage") if isinstance(engine, Mapping) else None
    start = _instant(projection.get("started_at"))
    end = _instant(projection.get("as_of"))
    state_start = _instant(state.get("started_at"))
    active = state.get("status") == projection.get("status") == "active"
    valid_window = start is not None and end is not None and start <= end
    matching = (
        active
        and valid_window
        and state_start == start
        and isinstance(marker, Mapping)
        and type(marker.get("version")) is int
        and marker["version"] == 1
        and _instant(marker.get("started_at")) == start
        and _instant(marker.get("as_of")) == end
    )
    issues = projection.get("issues")
    # A malformed/omitted issue collection cannot establish clean coverage.
    issue_state_known = isinstance(issues, (list, tuple))
    clean = issue_state_known and not issues
    if matching:
        event_status = "ready" if clean else "incomplete"
        note = (
            "تتضمن اللقطة مسار احتساب التسليم المثبت بعد بداية التشغيل للطلبات القديمة والجديدة؛ "
            "تاريخ إنشاء الطلب ليس شرط استحقاق أجرة التوصيل. مسار الطلبات القديمة يدعم SAR."
        )
        if not clean:
            note += " توجد مصادر تحتاج إلى مراجعة؛ دعم المسار لا يثبت اكتمال جميع الأرصدة."
    else:
        event_status = "unavailable" if marker is None else "incomplete"
        note = UNKNOWN_SCOPE
        if not active:
            note += " النظام غير نشط أو اللقطة للقراءة التاريخية فقط."
        elif marker is not None:
            note += " علامة المصدر مفقودة التفاصيل أو لا تطابق وقت بداية واحتساب هذه اللقطة."

    return {
        "event_reader_supported": bool(matching),
        "event_note": note,
        "dependencies": [
            {"code": "operational_delivery_coverage", "status": event_status, "message": note},
            {"code": "operational_driver_noncash_posting", "status": "incomplete", "message": BANK_REVIEW_GAP},
        ],
    }
