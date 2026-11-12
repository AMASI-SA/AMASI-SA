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


def _noncash_dependency(state, projection, matching):
    from operational_driver_review_report import validated_review_facts

    valid, conflicts = validated_review_facts(state)
    linked_orders = {str(row['order_id']) for row in valid}
    engine = state.get('engine') or {}
    facts = engine.get('facts') or {}
    expected = set()
    for key, row in facts.items():
        if not str(key).startswith('shipping:') or not isinstance(row, Mapping) or row.get('party_type') != 'store_driver':
            continue
        oid = str(key).removeprefix('shipping:')
        evidence = (facts.get('cod_collection:' + oid) or {}).get('evidence') or row.get('cod_evidence') or {}
        if evidence.get('payment_method') in {'bank_transfer', 'card_terminal'}:
            expected.add(oid)
    for key, row in (engine.get('cod_reports') or {}).items():
        oid = str(row.get('order_id') or str(key).removeprefix('cod:'))
        party_type = row.get('party_type') or (facts.get('shipping:' + oid) or {}).get('party_type')
        if party_type == 'store_driver' and row.get('payment_method') in {'bank_transfer', 'card_terminal'}:
            expected.add(oid)
    source_issues = projection.get('issues')
    issue_known = isinstance(source_issues, (list, tuple))
    relevant_issues = [row for row in source_issues if isinstance(row, Mapping) and (
        str(row.get('code') or '').startswith(('operational_driver_review_', 'driver_collection_'))
        or row.get('component') == 'driver_custody')] if issue_known else []
    pending = expected - linked_orders
    if conflicts or pending or relevant_issues or not issue_known:
        status, message = 'incomplete', BANK_REVIEW_GAP
    elif matching:
        status = 'ready'
        message = ('الاعتمادات التشغيلية المسجلة مطابقة لحركة البنك أو ذمة الشبكة، ولا تضيف عهدة كاش على المندوب.'
                   if valid else 'مسار اعتماد التحصيل غير النقدي متاح؛ لا توجد في هذه اللقطة تحصيلات مندوب غير نقدية تنتظر الربط.')
    else:
        status = 'unavailable'
        message = 'يلزم تحديث لقطة التسليم للتحقق من تغطية تحصيل المندوب غير النقدي.'
    return {'code': 'operational_driver_noncash_posting', 'status': status, 'message': message,
            'confirmed_review_count': len(valid), 'pending_order_count': len(pending),
            'effect_conflict_count': len(conflicts)}


def delivery_coverage_diagnostics(
    state: Mapping[str, Any], projection: Mapping[str, Any]
) -> dict[str, Any]:
    """Describe the exact saved snapshot without promoting unknown money.

    ``ready`` means reader support at this snapshot, not completeness of all
    business records. Existing projection issues retain an incomplete status.
    The noncash dependency validates sealed reviews against their saved effects.
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
            _noncash_dependency(state, projection, matching),
        ],
    }
