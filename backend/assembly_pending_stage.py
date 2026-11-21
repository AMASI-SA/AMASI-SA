"""Read-only card copy; never an alternative Ready or receipt policy.

The search caller has already resolved actions, current source membership and
merchant settings. This projection uses those same loaded facts without I/O.
"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any


# Display aliases already used by Order Engine's status groups. Normalizing a
# private read copy preserves tracking's coherence checks without widening any
# canonical Ready/payment/source-status predicate.
_DISPLAY_ALIASES = {
    "out_for_delivery": "delivering", "out for delivery": "delivering", "قيد التوصيل": "delivering",
    "returned": "refunded", "restored": "refunded", "تم الاسترجاع": "refunded",
    "payment_pending": "pending_payment", "بانتظار الدفع": "pending_payment",
    "انتظار الدفع": "pending_payment", "بإنتظار الدفع": "pending_payment",
}


def _text(value: Any) -> str:
    return str(value or "").strip() if not isinstance(value, (dict, list)) else ""


def _hint(code: str, title: str, detail: str = "") -> dict[str, str]:
    return {"code": code, "title": title, **({"detail": detail} if detail else {})}


def _unknown(title: str = "بيانات اكتمال المنتج غير مؤكدة") -> dict[str, str]:
    return _hint("evidence_unknown", title)


def _display_status(source: dict[str, Any]) -> tuple[str, str]:
    from mobile_order_tracking import _STATUS_LABELS, _status_facts

    def normalize(value: Any) -> Any:
        if isinstance(value, dict):
            return {key: normalize(item) for key, item in value.items()}
        return _DISPLAY_ALIASES.get(_text(value).casefold(), value)

    raw = (source.get("raw_by_source") or {}).get("salla_direct") or {}
    status_source = {
        **source,
        "order_status": normalize(source.get("order_status")),
        "order_status_slug": normalize(source.get("order_status_slug")),
        "raw_by_source": {"salla_direct": {
            **raw, "status": normalize(raw.get("status")), "status_slug": normalize(raw.get("status_slug")),
        }},
    }
    status, _, _ = _status_facts(status_source)
    _, native_label, _ = _status_facts(source)
    label = native_label if (native_label != "حالة سلة غير مؤكدة"
                             and re.search(r"[\u0600-\u06ff]", native_label)) else (
        {**_STATUS_LABELS, "pending_payment": "بانتظار الدفع"}.get(status, native_label)
    )
    return status, label


def _partial_product_received(piece: dict[str, Any]) -> bool:
    # Approval removes received_at while services remain. A draft scan alone
    # is not a receipt, and history from before rejection/redispatch cannot
    # override the current assignment or supplier dispatch state.
    history = piece.get("supplier_receiving_history")
    return bool(
        piece.get("supplier_dispatch_status") == "partial_received"
        and piece.get("status") == "in_progress"
        and piece.get("execution_status") in {"awaiting_remaining_services", "supplier_receiving_draft"}
        and isinstance(history, list)
        and any(isinstance(receipt, dict) and _text(receipt.get("invoice_id"))
                and receipt.get("received_at") for receipt in history)
    )


def _current_line_covered(piece: dict[str, Any], current_units: dict[str, int]) -> bool:
    """A display hint must describe a unit still present in this source order."""
    try:
        unit = Decimal(str(piece.get("unit_index")))
        return bool(unit.is_finite() and unit == unit.to_integral_value()
                    and 1 <= unit <= current_units.get(_text(piece.get("order_item_id")), 0))
    except (InvalidOperation, ValueError, TypeError):
        return False


def assembly_pending_stage(
    piece: dict[str, Any],
    *,
    row: dict[str, Any],
    source: dict[str, Any],
    workflow: dict[str, Any],
    current_units: dict[str, int],
    preparation_optional: bool,
    salla_in_progress: bool,
) -> dict[str, str] | None:
    """Describe the earliest known missing stage, preserving all action gates."""
    # A hint cannot add or remove permission from a row policy already resolved.
    if row.get("can_mark_ready"):
        return None
    # Reuse native status naming/coherence, without a provider call. Cancellation
    # and advanced states must never instruct new receipt or reverting the order.
    status, label = _display_status(source)
    if status == "cancelled":
        return _hint("salla_status", "الطلب ملغي", "لا يحتاج شحن")
    if status in {"completed", "shipped", "delivering", "delivered", "refunded", "deleted"}:
        return _hint("salla_status", f"حالة الطلب: {label}")
    if row.get("assembly_ready"):
        return None
    blocker = _text(row.get("assembly_blocker_code"))
    if piece.get("active_hold_id") or piece.get("status") in {"blocked", "cancelled"}:
        return _hint("stopped", "المنتج متوقف")
    if blocker == "assembly_source_reconciliation_pending" or blocker.startswith("component_source_"):
        return _hint("source_reconciliation", "بانتظار مطابقة بيانات الطلب")
    if blocker.startswith("component_"):
        return _unknown()
    if blocker == "customer_service_instruction_action_required":
        return _hint("instructions", "بانتظار تنفيذ تعليمات الطلب")

    from preparation_piece_operations import (
        _preparation_incomplete_services, _verified_supplier_employee_custody,
    )
    from supplier_receipt_ready_policy import supplier_current_piece_blocker, supplier_piece_received

    virtual = bool(row.get("is_direct_assembly") or row.get("is_operational_item"))
    # Physical and direct rows belong to current Salla units. Operational work
    # is Mezan-only and does not require a physical source line.
    if not row.get("is_operational_item") and not _current_line_covered(piece, current_units):
        return _unknown("بيانات المنتج لا تطابق الطلب الحالي")
    if not virtual:
        if supplier_current_piece_blocker(source, workflow, piece, current_units=current_units):
            return _unknown("بيانات المنتج لا تطابق الطلب الحالي")
        employee_id = _text(piece.get("responsible_employee_id"))
        if piece.get("assignment_status") == "unassigned_after_rejection" and not employee_id:
            return _hint("employee_assignment", "لم يُسند المنتج لموظف التجهيز")
        if not employee_id or piece.get("assignment_status") == "unassigned_after_rejection":
            return _unknown("بيانات إسناد موظف التجهيز غير مؤكدة")
        employee = _text(piece.get("responsible_employee_name"))
        if not employee or employee in {"—", "-", "null", "None"}:
            employee = "موظف التجهيز"
        dispatch = _text(piece.get("supplier_dispatch_status"))
        if not dispatch and not _text(piece.get("supplier_id")):
            return _hint("supplier_assignment", "لم يُسند المنتج للمورد",
                         f"كلّم {employee} يسند المنتج للمورد")
        product_received = bool(dispatch == "received" and piece.get("received_at"))
        if product_received and (
            # Check physical receipt before services without treating service
            # completion as receipt evidence or changing the original snapshot.
            not supplier_piece_received({**piece, "services": []}, preparation_optional=True)
            or _text(piece.get("execution_status")) not in {"", "received_from_supplier", "ready_for_assembly"}
        ):
            return _unknown("بيانات استلام المنتج غير مؤكدة")
        partial_received = _partial_product_received(piece)
        if not product_received and not partial_received:
            if dispatch == "partial_received":
                return _unknown("بيانات استلام المنتج غير مؤكدة")
            return _hint("supplier_receipt", "لم يُستلم المنتج من المورد",
                         f"كلّم {employee} يستلم المنتج من المورد")

        services = piece.get("services")
        if not isinstance(services, list) or any(not isinstance(service, dict) for service in services):
            return _unknown("بيانات خدمات المنتج غير مكتملة")
        pending = _preparation_incomplete_services(piece)
        if pending:
            name = _text(pending[0].get("service_name") or pending[0].get("name") or pending[0].get("service_code"))
            title = f"لم تُستلم خدمة {name}" if name else "لم تُستلم خدمات المنتج"
            more = len(pending) - 1
            service = f"خدمة {name}" if name else "خدمات المنتج"
            detail = f"كلّم {employee} يستلم {service} من المورد"
            detail += " · وخدمة أخرى" if more == 1 else f" · و{more} خدمات أخرى" if more else ""
            return _hint("service_receipt", title, detail)
        # Complete service flags alone cannot manufacture a product receipt.
        # Reuse the existing receipt predicate, then the configured custody gate.
        if not supplier_piece_received(piece, preparation_optional=True):
            return _unknown()
        if not preparation_optional and not _verified_supplier_employee_custody(piece):
            return _hint("preparation_receipt", "لم يُستلم المنتج من موظف التجهيز")

    if salla_in_progress:
        return None  # An unrelated existing blocker retains its own explanation.
    if status == "pending_payment":
        return _hint("salla_status", f"حالة الطلب: {label}", "تحقق من حالة الدفع")
    if status not in {"under_review", "reviewed"}:
        return _unknown("حالة الطلب في سلة غير مؤكدة")
    return _hint(
        "salla_status", "المنتج مكتمل — حالة الطلب غير مؤهلة",
        f"سلة: {label} · حوّله لقيد التنفيذ",
    )
