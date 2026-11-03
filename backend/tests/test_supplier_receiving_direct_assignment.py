"""Confirmed supplier receiving draft assignment and rollback regression tests."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from supplier_receiving_direct_assignment import direct_assignment_offer, direct_assignment_patch
from supplier_receipt_employee_custody import (
    apply_supplier_receipt_employee_custody, supplier_receipt_assignment_history_row,
)
from supplier_receiving_routes import (
    SupplierPieceScanRequest, _scan_request_shape, supplier_scan_group_candidates,
    supplier_receipt_previous_piece_state, supplier_receipt_piece_rollback_update,
)


NOW = datetime(2026, 11, 3, 20, 0, tzinfo=timezone.utc)
SESSION = {
    "id": "s1", "reference": "R-123",
    "supplier_id": "sup-2",
    "supplier_snapshot": {"id": "sup-2", "company_name": "المورد الجديد"},
}
OTHER = {
    "piece_id": "abcd" * 8,
    "status": "in_progress", "supplier_id": None,
    "responsible_employee_id": "emp-old",
    "responsible_employee_name": "محمد",
    "assigned_at": NOW,
}


def offer(piece):
    return direct_assignment_offer(
        piece, SESSION, actor_id="emp-current", actor_name="هلال",
    )


def test_unsent_piece_from_other_employee_requires_named_confirmation():
    proposed = offer(OTHER)
    assert proposed["requires_direct_assignment_confirmation"] is True
    assert proposed["transfers_employee_responsibility"] is True
    assert proposed["previous_responsible_employee_name"] == "محمد"
    assert proposed["current_supplier_name"] == "المورد الجديد"
    assert proposed["new_responsible_employee_id"] == "emp-current"
    # Pure preview must not assign, receive, create an invoice or change the input.
    assert OTHER["supplier_id"] is None
    assert OTHER["responsible_employee_id"] == "emp-old"


def test_unassigned_and_same_employee_still_offer_supplier_choice():
    missing_employee = {**OTHER, "responsible_employee_id": None, "responsible_employee_name": None}
    assert offer(missing_employee)["transfers_employee_responsibility"] is False
    assert offer(missing_employee)["previous_responsible_employee_name"] is None
    same_employee = {**OTHER, "responsible_employee_id": "emp-current"}
    assert offer(same_employee)["transfers_employee_responsibility"] is False


def test_other_supplier_and_historical_receipts_are_never_automatically_claimed():
    assert offer({**OTHER, "supplier_id": "sup-other"}) is None
    assert offer({**OTHER, "supplier_receiving_history": [{"invoice_id": "i-1"}]}) is None
    for status in ("sent", "ready", "received", "partial_received", "unknown"):
        assert offer({**OTHER, "supplier_dispatch_status": status}) is None
    assert offer({**OTHER, "supplier_dispatch_status": "pending"}) is not None


def test_confirmed_patch_assigns_supplier_but_not_employee_until_invoice():
    proposal = offer(OTHER)
    patch = direct_assignment_patch(
        piece=OTHER, offer=proposal, session_id="s1",
        actor_id="emp-current", actor_name="هلال", at=NOW,
    )
    assert patch["supplier_id"] == "sup-2"
    assert patch["supplier_assignment_mode"] == "direct_at_receiving_draft"
    assert patch["supplier_dispatch_status"] == "sent"
    assert "responsible_employee_id" not in patch
    assert "responsible_employee_name" not in patch
    assert "assignment_status" not in patch
    assert "supplier_receiving_draft_employee_transfer" not in patch
    assert "supplier_invoice_id" not in patch
    assert "received_at" not in patch
    assert "status" not in patch


def test_cancel_or_remove_restores_original_employee_and_supplier_exactly():
    proposal = offer(OTHER)
    snapshot = supplier_receipt_previous_piece_state(OTHER)
    patch = direct_assignment_patch(
        piece=OTHER, offer=proposal, session_id="s1",
        actor_id="emp-current", actor_name="هلال", at=NOW,
    )
    simulated = {**OTHER, **patch, "supplier_receiving_session_id": "s1"}
    rollback = supplier_receipt_piece_rollback_update(snapshot)
    restored = dict(simulated)
    restored.update(rollback.get("$set") or {})
    for field in (rollback.get("$unset") or {}):
        restored.pop(field, None)
    assert restored == OTHER
    assert restored["responsible_employee_id"] == "emp-old"
    assert restored["supplier_id"] is None
    assert "supplier_dispatch_status" not in restored


def test_employee_custody_transfers_only_at_supplier_invoice_approval():
    patch = direct_assignment_patch(
        piece=OTHER, offer=offer(OTHER), session_id="s1",
        actor_id="emp-current", actor_name="هلال", at=NOW,
    )
    in_draft = {**OTHER, **patch}
    actor = {"_mobile_actor_id": "emp-current", "_mobile_actor_name": "هلال"}
    history = supplier_receipt_assignment_history_row(
        piece=in_draft, session=SESSION, actor=actor, invoice_id="invoice-88",
        completed_at=NOW,
    )
    assert history["previous_responsible_employee_id"] == "emp-old"
    assert history["previous_responsible_employee_name"] == "محمد"
    assert history["responsible_employee_id"] == "emp-current"
    assert history["supplier_invoice_id"] == "invoice-88"
    update = apply_supplier_receipt_employee_custody(
        {"$set": {"received_by_id": "emp-current"}, "$push": {"supplier_receiving_history": {}}},
        piece=in_draft, session=SESSION, actor=actor, invoice_id="invoice-88",
        completed_at=NOW,
    )
    assert in_draft["responsible_employee_id"] == "emp-old"
    assert update["$set"]["responsible_employee_id"] == "emp-current"
    assert update["$set"]["responsible_employee_name"] == "هلال"
    assert update["$push"]["assignment_history"]["previous_responsible_employee_id"] == "emp-old"
    assert update["$push"]["assignment_history"]["supplier_invoice_id"] == "invoice-88"


def test_scan_confirmation_is_explicit_and_affects_attempt_identity():
    plain = SupplierPieceScanRequest(barcode="MEZAN-PIECE:" + "f" * 32)
    approved = SupplierPieceScanRequest(
        barcode=plain.barcode, confirm_direct_receiving_assignment=True,
    )
    assert _scan_request_shape(plain)["confirm_direct_receiving_assignment"] is False
    assert _scan_request_shape(approved)["confirm_direct_receiving_assignment"] is True
    assert _scan_request_shape(plain) != _scan_request_shape(approved)


@pytest.mark.asyncio
async def test_quantity_confirmation_cannot_transfer_other_employee_siblings():
    """Consent for one scanned physical piece is never a batch-wide takeover."""
    anchor = {
        **OTHER, "piece_id": "piece-1", "batch_id": "batch-a",
        "order_item_id": "line-1", "group_key": "product:1", "unit_index": 1,
        "supplier_dispatch_status": "pending", "services": [],
    }
    sibling = {
        **anchor, "piece_id": "piece-2", "unit_index": 2,
        "responsible_employee_id": "other-worker",
        "responsible_employee_name": "موظف آخر",
    }
    different_supplier = {
        **anchor, "piece_id": "piece-3", "unit_index": 3,
        "supplier_id": "sup-different", "supplier_dispatch_status": "sent",
    }

    class Cursor:
        def sort(self, *_args):
            return self
        def limit(self, *_args):
            return self
        async def to_list(self, _count):
            return [anchor, sibling, different_supplier]

    class Collection:
        def find(self, *_args):
            return Cursor()

    class DB:
        def __getitem__(self, _name):
            return Collection()

    candidates = await supplier_scan_group_candidates(
        DB(), user_id="merchant-1", scanned_piece=anchor,
        session={**SESSION, "opened_by": "emp-current", "opened_by_name": "هلال"},
        allow_service_addition=False, allow_direct_assignment=True,
    )
    assert [p["piece_id"] for p in candidates] == ["piece-1"]
    # A distinct scan of the other employee's barcode can offer a separate consent.
    assert offer(sibling)["previous_responsible_employee_name"] == "موظف آخر"
