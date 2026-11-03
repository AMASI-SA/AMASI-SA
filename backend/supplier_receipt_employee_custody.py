"""Transfer preparation custody to the employee who approves supplier receipt.

Supplier scanning is only a draft reservation. The durable employee assignment
moves at invoice approval, inside the existing supplier-receiving transaction.
The previous employee is never erased: a full before/after snapshot is appended
to assignment_history for each physical piece.
"""
from __future__ import annotations

import uuid
from typing import Any


_INSTALLED = False


def _text(value: Any) -> str:
    return str(value or "").strip()


def _actor_id(actor: dict[str, Any]) -> str:
    return _text(actor.get("_mobile_actor_id") or actor.get("id"))


def _actor_name(actor: dict[str, Any]) -> str:
    return _text(
        actor.get("_mobile_actor_name")
        or actor.get("_mobile_actor_email")
        or actor.get("name")
        or actor.get("email")
    ) or "موظف"


def supplier_receipt_assignment_history_row(
    *,
    piece: dict[str, Any],
    session: dict[str, Any],
    actor: dict[str, Any],
    invoice_id: str,
    completed_at: Any,
) -> dict[str, Any] | None:
    previous_id = _text(piece.get("responsible_employee_id"))
    previous_name = _text(piece.get("responsible_employee_name"))
    next_id = _actor_id(actor)
    next_name = _actor_name(actor)
    if not next_id or next_id == previous_id:
        return None
    piece_id = _text(piece.get("piece_id") or piece.get("id"))
    session_id = _text(session.get("id"))
    return {
        "assignment_id": uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"supplier-receipt-assignment:{piece_id}:{session_id}:{invoice_id}:{next_id}",
        ).hex,
        "reason": "supplier_receipt",
        "source": "supplier_receiving_invoice_approval",
        "piece_id": piece_id or None,
        "order_number": _text(piece.get("order_number")) or None,
        "order_item_id": _text(piece.get("order_item_id")) or None,
        "file_number": _text(piece.get("file_number")) or None,
        "batch_id": _text(piece.get("batch_id")) or None,
        "previous_responsible_employee_id": previous_id or None,
        "previous_responsible_employee_name": previous_name or None,
        "responsible_employee_id": next_id,
        "responsible_employee_name": next_name,
        "supplier_id": _text((session.get("supplier_snapshot") or {}).get("id") or session.get("supplier_id")) or None,
        "supplier_name": _text((session.get("supplier_snapshot") or {}).get("company_name")) or None,
        "supplier_receiving_session_id": session_id or None,
        "supplier_receiving_reference": _text(session.get("reference")) or None,
        "supplier_invoice_id": _text(invoice_id) or None,
        "transferred_at": completed_at,
        "transferred_by_id": next_id,
        "transferred_by_name": next_name,
        "previous_piece_status": _text(piece.get("status")) or None,
        "previous_execution_status": _text(piece.get("execution_status")) or None,
        "previous_assignment_status": _text(piece.get("assignment_status")) or None,
        "previous_supplier_id": _text(piece.get("supplier_id")) or None,
        "previous_supplier_name": _text(piece.get("supplier_name")) or None,
        "previous_sent_to_supplier_by_id": _text(piece.get("sent_to_supplier_by_id")) or None,
        "previous_sent_to_supplier_by_name": _text(piece.get("sent_to_supplier_by_name")) or None,
        "previous_assigned_at": piece.get("assigned_at"),
        "previous_started_at": piece.get("started_at"),
        "previous_sent_to_supplier_at": piece.get("sent_to_supplier_at"),
    }


def apply_supplier_receipt_employee_custody(
    update: dict[str, dict[str, Any]],
    *,
    piece: dict[str, Any],
    session: dict[str, Any],
    actor: dict[str, Any],
    invoice_id: str,
    completed_at: Any,
) -> dict[str, dict[str, Any]]:
    """Augment final supplier receipt with an auditable employee transfer."""
    next_id = _actor_id(actor)
    next_name = _actor_name(actor)
    # The actual receiver identity must be the employee, not the merchant owner
    # principal used internally to scope the native request to store data.
    if next_id:
        update.setdefault("$set", {}).update({
            "received_by_id": next_id,
            "received_by_name": next_name,
        })
        supplier_history = update.setdefault("$push", {}).get("supplier_receiving_history")
        if isinstance(supplier_history, dict):
            supplier_history["received_by_id"] = next_id
            supplier_history["received_by_name"] = next_name

    history = supplier_receipt_assignment_history_row(
        piece=piece,
        session=session,
        actor=actor,
        invoice_id=invoice_id,
        completed_at=completed_at,
    )
    if not history:
        return update

    previous_id = _text(piece.get("responsible_employee_id"))
    previous_name = _text(piece.get("responsible_employee_name"))
    set_values = update.setdefault("$set", {})
    set_values.update({
        "responsible_employee_id": next_id,
        "responsible_employee_name": next_name,
        "previous_responsible_employee_id": previous_id or None,
        "previous_responsible_employee_name": previous_name or None,
        "assignment_status": "assigned",
        "reassigned_at": completed_at,
        "reassigned_by": next_id,
        "reassigned_by_name": next_name,
        "reassignment_reason": "supplier_receipt",
        "reassignment_session_id": _text(session.get("id")) or None,
        "reassignment_invoice_id": _text(invoice_id) or None,
    })
    update.setdefault("$push", {})["assignment_history"] = history
    return update


def install_supplier_receipt_employee_custody() -> None:
    """Wrap the final supplier service completion function once per process."""
    global _INSTALLED
    if _INSTALLED:
        return
    import supplier_receiving_routes as base

    original = base.supplier_service_completion_update

    def wrapped_supplier_service_completion_update(
        *,
        piece: dict[str, Any],
        invoice_line: dict[str, Any],
        session: dict[str, Any],
        actor: dict[str, Any],
        invoice_id: str,
        completed_at: Any,
    ) -> dict[str, dict[str, Any]]:
        update = original(
            piece=piece,
            invoice_line=invoice_line,
            session=session,
            actor=actor,
            invoice_id=invoice_id,
            completed_at=completed_at,
        )
        return apply_supplier_receipt_employee_custody(
            update,
            piece=piece,
            session=session,
            actor=actor,
            invoice_id=invoice_id,
            completed_at=completed_at,
        )

    base.supplier_service_completion_update = wrapped_supplier_service_completion_update
    _INSTALLED = True


__all__ = [
    "apply_supplier_receipt_employee_custody",
    "install_supplier_receipt_employee_custody",
    "supplier_receipt_assignment_history_row",
]
