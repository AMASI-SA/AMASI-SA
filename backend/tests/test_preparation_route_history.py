from datetime import datetime, timezone

from preparation_route_history import (
    ROUTE_STATE_EMPLOYEE,
    ROUTE_STATE_OUTSIDE,
    _effective_status_from_order,
    order_status_allows_employee_preparation,
    outside_reason_for_order_status,
    piece_route_snapshot,
    route_state_for_order_status,
)
from supplier_receipt_employee_custody import (
    apply_supplier_receipt_employee_custody,
    supplier_receipt_assignment_history_row,
)


def test_employee_preparation_status_contract():
    for status in (
        "reviewed",
        "تم المراجعة",
        "تمت المراجعة",
        "تم المراجعه",
        "processing",
        "in_progress",
        "قيد التنفيذ",
        "جاري التنفيذ",
    ):
        assert order_status_allows_employee_preparation(status) is True
        assert route_state_for_order_status(status) == ROUTE_STATE_EMPLOYEE


def test_outside_preparation_status_contract():
    expected = {
        "بانتظار المراجعة": "order_returned_to_review_queue",
        "تم التنفيذ": "order_completed_before_preparation_route_finished",
        "جاري التوصيل": "order_moved_to_shipping",
        "تم التوصيل": "order_delivered",
        "ملغي": "order_cancelled",
        "مسترجع": "order_refunded_or_returned",
        "unknown": "order_status_not_eligible_for_preparation",
        "": "order_status_missing_or_unknown",
    }
    for status, reason in expected.items():
        assert route_state_for_order_status(status) == ROUTE_STATE_OUTSIDE
        assert outside_reason_for_order_status(status) == reason


def test_top_level_non_review_status_overrides_stale_customized_reviewed():
    row = {
        "order_status": "تم التنفيذ",
        "raw_by_source": {
            "salla_direct": {
                "status": {"customized": {"name": "تم المراجعة"}},
            }
        },
    }
    assert _effective_status_from_order(row) == "تم التنفيذ"
    assert route_state_for_order_status(_effective_status_from_order(row)) == ROUTE_STATE_OUTSIDE


def test_customized_reviewed_advances_while_parent_is_review():
    row = {
        "order_status": "بانتظار المراجعة",
        "raw_by_source": {
            "salla_direct": {
                "status": {"customized": {"name": "تم المراجعة"}},
            }
        },
    }
    assert _effective_status_from_order(row) == "تم المراجعة"
    assert route_state_for_order_status(_effective_status_from_order(row)) == ROUTE_STATE_EMPLOYEE


def test_piece_snapshot_keeps_employee_supplier_and_stage_history():
    now = datetime.now(timezone.utc)
    piece = {
        "piece_id": "piece-1",
        "status": "in_progress",
        "execution_status": "sent_to_supplier",
        "responsible_employee_id": "employee-a",
        "responsible_employee_name": "A",
        "supplier_id": "supplier-1",
        "supplier_name": "Supplier",
        "supplier_dispatch_id": "dispatch-1",
        "sent_to_supplier_at": now,
        "assignment_history": [{"assignment_id": "old-assignment"}],
        "supplier_receiving_history": [{"invoice_id": "old-invoice"}],
    }
    snapshot = piece_route_snapshot(piece)
    assert snapshot["responsible_employee_id"] == "employee-a"
    assert snapshot["supplier_id"] == "supplier-1"
    assert snapshot["execution_status"] == "sent_to_supplier"
    assert snapshot["assignment_history"] == [{"assignment_id": "old-assignment"}]
    assert snapshot["supplier_receiving_history"] == [{"invoice_id": "old-invoice"}]


def test_supplier_receipt_reassigns_to_actual_receiver_and_preserves_previous_employee():
    now = datetime.now(timezone.utc)
    piece = {
        "piece_id": "piece-1",
        "order_number": "1001",
        "responsible_employee_id": "employee-a",
        "responsible_employee_name": "Employee A",
        "status": "ready_for_employee_receipt",
        "execution_status": "supplier_ready_for_receipt",
        "supplier_id": "supplier-1",
        "supplier_name": "Supplier One",
    }
    session = {
        "id": "session-1",
        "reference": "SR-1",
        "supplier_snapshot": {"id": "supplier-1", "company_name": "Supplier One"},
    }
    actor = {
        "id": "merchant-owner",
        "_mobile_actor_id": "employee-b",
        "_mobile_actor_name": "Employee B",
        "_mobile_actor_email": "b@example.test",
    }
    update = {"$set": {"received_by_id": "merchant-owner"}, "$push": {"supplier_receiving_history": {"invoice_id": "invoice-1"}}}
    result = apply_supplier_receipt_employee_custody(
        update,
        piece=piece,
        session=session,
        actor=actor,
        invoice_id="invoice-1",
        completed_at=now,
    )
    assert result["$set"]["received_by_id"] == "employee-b"
    assert result["$set"]["responsible_employee_id"] == "employee-b"
    assert result["$set"]["responsible_employee_name"] == "Employee B"
    assert result["$set"]["previous_responsible_employee_id"] == "employee-a"
    assert result["$push"]["supplier_receiving_history"]["received_by_id"] == "employee-b"
    history = result["$push"]["assignment_history"]
    assert history["reason"] == "supplier_receipt"
    assert history["previous_responsible_employee_id"] == "employee-a"
    assert history["responsible_employee_id"] == "employee-b"
    assert history["supplier_receiving_session_id"] == "session-1"
    assert history["supplier_invoice_id"] == "invoice-1"


def test_supplier_receipt_same_employee_does_not_create_fake_reassignment():
    piece = {"piece_id": "piece-1", "responsible_employee_id": "employee-a"}
    actor = {"_mobile_actor_id": "employee-a", "_mobile_actor_name": "Employee A"}
    session = {"id": "session-1"}
    assert supplier_receipt_assignment_history_row(
        piece=piece,
        session=session,
        actor=actor,
        invoice_id="invoice-1",
        completed_at=datetime.now(timezone.utc),
    ) is None
