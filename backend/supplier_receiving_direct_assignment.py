"""Explicit, reversible supplier/employee assignment during a receiving draft.

This module has no provider or financial I/O. Caller must enforce merchant,
employee permission, session ownership, piece readiness and atomic scan CAS.
"""
from __future__ import annotations

from typing import Any

_DIRECT_DISPATCH_STATES = frozenset({"", "not_sent", "not_dispatched", "pending"})


def text(value: Any) -> str:
    return str(value or "").strip()


def direct_assignment_offer(
    piece: dict[str, Any],
    session: dict[str, Any],
    *,
    actor_id: str,
    actor_name: str,
) -> dict[str, Any] | None:
    """Describe a currently supplier-unassigned piece without changing it.

    A historical previous supplier invoice, a current supplier id, or an
    existing physical dispatch must never be reinterpreted as unassigned.
    """
    if text(piece.get("supplier_id")) or list(piece.get("supplier_receiving_history") or []):
        return None
    dispatch_state = text(piece.get("supplier_dispatch_status")).lower()
    if dispatch_state not in _DIRECT_DISPATCH_STATES:
        return None
    supplier = dict(session.get("supplier_snapshot") or {})
    supplier_id = text(supplier.get("id") or session.get("supplier_id"))
    if not supplier_id or not text(actor_id):
        return None
    before_id = text(piece.get("responsible_employee_id"))
    before_name = text(piece.get("responsible_employee_name"))
    new_id = text(actor_id)
    other_employee = bool(before_id and before_id != new_id)
    return {
        "code": "supplier_receiving_direct_assignment_confirmation_required",
        "message": "يلزم تأكيد إسناد القطعة إلى المورد ومسودة فاتورته.",
        "piece_id": text(piece.get("piece_id")),
        "requires_direct_assignment_confirmation": True,
        "current_supplier_id": supplier_id,
        "current_supplier_name": text(supplier.get("company_name")) or "المورد الحالي",
        "previous_responsible_employee_id": before_id or None,
        "previous_responsible_employee_name": before_name or (
            "موظف آخر" if other_employee else None
        ),
        "new_responsible_employee_id": new_id,
        "new_responsible_employee_name": text(actor_name) or "الموظف الحالي",
        "transfers_employee_responsibility": other_employee,
    }


def direct_assignment_patch(
    *,
    piece: dict[str, Any],
    offer: dict[str, Any],
    session_id: str,
    actor_id: str,
    actor_name: str,
    at: Any,
) -> dict[str, Any]:
    """Apply only a user-confirmed assignment to an open receiving draft."""
    supplier_id = text(offer.get("current_supplier_id"))
    actor_id = text(actor_id)
    if not supplier_id or not actor_id or not session_id:
        raise ValueError("supplier_receiving_direct_assignment_identity_required")
    patch = {
        "supplier_id": supplier_id,
        "supplier_name": text(offer.get("current_supplier_name")) or "المورد الحالي",
        "supplier_dispatch_status": "sent",
        "supplier_assignment_mode": "direct_at_receiving_draft",
        "supplier_assigned_at_receipt": True,
        "supplier_assigned_from_id": None,
        "supplier_assigned_from_name": None,
        "supplier_assigned_at": at,
        "supplier_assigned_by_id": actor_id,
        "supplier_assigned_by_name": text(actor_name) or "الموظف الحالي",
        "supplier_assignment_session_id": session_id,
        "sent_to_supplier_at": at,
        "sent_to_supplier_by_id": actor_id,
        "sent_to_supplier_by_name": text(actor_name) or "الموظف الحالي",
    }
    previous_id = text(piece.get("responsible_employee_id"))
    if previous_id != actor_id:
        patch.update({
            "responsible_employee_id": actor_id,
            "responsible_employee_name": text(actor_name) or "الموظف الحالي",
            "assignment_status": "assigned",
            "assigned_at": at,
            "reassigned_at": at,
            "reassigned_by": actor_id,
            "reassigned_by_name": text(actor_name) or "الموظف الحالي",
            "reassignment_reason": "supplier_receiving_direct_assignment",
            "reassignment_session_id": session_id,
            "supplier_receiving_draft_employee_transfer": True,
            "supplier_receiving_previous_responsible_employee_id": previous_id or None,
            "supplier_receiving_previous_responsible_employee_name": (
                text(piece.get("responsible_employee_name")) or None
            ),
            "supplier_receiving_previous_assigned_at": piece.get("assigned_at"),
        })
    return patch


__all__ = ["direct_assignment_offer", "direct_assignment_patch"]
