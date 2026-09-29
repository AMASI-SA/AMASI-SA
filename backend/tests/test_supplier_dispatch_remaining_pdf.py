from supplier_dispatch_pdf import (
    _piece_received_from_dispatch_supplier,
    _remaining_dispatch_pieces,
)


def _dispatch():
    return {
        "id": "sdv1_demo",
        "supplier_id": "supplier-a",
        "piece_ids": ["p1", "p2", "p3"],
    }


def test_receipt_history_for_same_supplier_removes_piece_from_remaining_pdf():
    piece = {
        "piece_id": "p1",
        "supplier_receiving_history": [{
            "supplier_id": "supplier-a",
            "invoice_id": "inv-1",
            "received_at": "2026-09-30T01:00:00+03:00",
        }],
    }
    assert _piece_received_from_dispatch_supplier(piece, _dispatch()) is True


def test_receipt_history_for_other_supplier_does_not_close_original_supplier_without_match():
    piece = {
        "piece_id": "p1",
        "supplier_receiving_history": [{
            "supplier_id": "supplier-b",
            "invoice_id": "inv-2",
            "received_at": "2026-09-30T01:00:00+03:00",
        }],
    }
    assert _piece_received_from_dispatch_supplier(piece, _dispatch()) is False


def test_legacy_received_marker_for_same_dispatch_is_accepted():
    piece = {
        "piece_id": "p1",
        "supplier_dispatch_id": "sdv1_demo",
        "supplier_dispatch_status": "received",
    }
    assert _piece_received_from_dispatch_supplier(piece, _dispatch()) is True


def test_received_marker_from_another_dispatch_does_not_hide_piece():
    piece = {
        "piece_id": "p1",
        "supplier_dispatch_id": "sdv1_other",
        "supplier_dispatch_status": "received",
    }
    assert _piece_received_from_dispatch_supplier(piece, _dispatch()) is False


def test_remaining_dispatch_pieces_keeps_only_unreceived_physical_pieces():
    pieces = [
        {
            "piece_id": "p1",
            "supplier_receiving_history": [{
                "supplier_id": "supplier-a",
                "invoice_id": "inv-1",
            }],
        },
        {
            "piece_id": "p2",
            "supplier_dispatch_id": "sdv1_demo",
            "supplier_dispatch_status": "sent",
        },
        {
            "piece_id": "p3",
            "supplier_dispatch_id": "sdv1_demo",
            "supplier_dispatch_status": "ready",
        },
    ]
    remaining = _remaining_dispatch_pieces(_dispatch(), pieces)
    assert [row["piece_id"] for row in remaining] == ["p2", "p3"]


def test_completed_dispatch_has_no_remaining_pieces():
    pieces = [
        {
            "piece_id": piece_id,
            "supplier_receiving_history": [{
                "supplier_id": "supplier-a",
                "invoice_id": f"inv-{piece_id}",
            }],
        }
        for piece_id in ("p1", "p2", "p3")
    ]
    assert _remaining_dispatch_pieces(_dispatch(), pieces) == []
