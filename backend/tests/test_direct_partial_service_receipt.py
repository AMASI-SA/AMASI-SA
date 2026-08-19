from preparation_supplier_dispatch import (
    DISPATCH_STATUS_PARTIAL,
    DISPATCH_STATUS_RECEIVED,
    DISPATCH_STATUS_SENT,
    piece_allows_direct_service_receipt,
    supplier_receiving_dispatch_blocker,
)


def _partial_piece() -> dict:
    return {
        "piece_id": "piece-1",
        "status": "in_progress",
        "execution_status": "awaiting_remaining_services",
        "supplier_id": "supplier-1",
        "supplier_name": "المورد الأول",
        "supplier_dispatch_status": DISPATCH_STATUS_PARTIAL,
        "remaining_service_count": 1,
        "supplier_receiving_history": [{
            "invoice_id": "invoice-1",
            "supplier_id": "supplier-1",
            "service_ids": ["service-1"],
        }],
        "services": [
            {"service_id": "service-1", "status": "completed"},
            {"service_id": "service-2", "status": "pending"},
        ],
    }


def test_partial_piece_is_received_directly_from_second_supplier() -> None:
    piece = _partial_piece()
    assert piece_allows_direct_service_receipt(piece) is True
    assert supplier_receiving_dispatch_blocker(piece, "supplier-2") is None


def test_prior_supplier_assignment_does_not_block_remaining_service_receipt() -> None:
    piece = _partial_piece()
    piece["supplier_dispatch_status"] = DISPATCH_STATUS_SENT
    assert supplier_receiving_dispatch_blocker(piece, "supplier-2") is None


def test_first_supplier_still_requires_original_dispatch_assignment() -> None:
    piece = {
        "piece_id": "piece-new",
        "status": "in_progress",
        "supplier_id": "supplier-1",
        "supplier_name": "المورد الأول",
        "supplier_dispatch_status": DISPATCH_STATUS_SENT,
        "remaining_service_count": 1,
        "supplier_receiving_history": [],
        "services": [{"service_id": "service-1", "status": "pending"}],
    }
    blocker = supplier_receiving_dispatch_blocker(piece, "supplier-2")
    assert piece_allows_direct_service_receipt(piece) is False
    assert blocker is not None
    assert blocker["code"] == "supplier_piece_dispatched_to_different_supplier"


def test_unproven_partial_status_does_not_bypass_dispatch() -> None:
    piece = _partial_piece()
    piece["supplier_receiving_history"] = []
    blocker = supplier_receiving_dispatch_blocker(piece, "supplier-2")
    assert piece_allows_direct_service_receipt(piece) is False
    assert blocker is not None
    assert blocker["code"] == "supplier_piece_not_dispatched"


def test_completed_piece_is_not_a_direct_partial_receipt() -> None:
    piece = _partial_piece()
    piece.update({
        "status": "received",
        "execution_status": "received_from_supplier",
        "supplier_dispatch_status": DISPATCH_STATUS_RECEIVED,
        "remaining_service_count": 0,
        "services": [{"service_id": "service-1", "status": "completed"}],
    })
    assert piece_allows_direct_service_receipt(piece) is False
