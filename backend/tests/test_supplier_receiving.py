import inspect
from datetime import datetime, timezone
from types import SimpleNamespace

from preparation_piece_barcode import (
    BARCODE_PREFIX,
    parse_preparation_piece_barcode,
    preparation_piece_barcode,
)
from preparation_piece_operations import (
    PIECE_STATUS_BLOCKED,
    PIECE_STATUS_CANCELLED,
    PIECE_STATUS_IN_PROGRESS,
    PIECE_STATUS_RECEIVED,
    _piece_id,
)
from reviewed_preparation_batches import _line_from_batch_storage
from supplier_receiving_routes import (
    make_supplier_receiving_router,
    piece_scan_blocker,
    supplier_receipt_piece_patch,
)


def _identity():
    return {
        "user_id": "owner-1",
        "batch_id": "batch-1",
        "order_number": "276218536",
        "order_item_id": "item-7",
        "unit_index": 2,
    }


def test_piece_barcode_round_trips_the_materialized_piece_identity():
    payload = preparation_piece_barcode(**_identity())
    piece_id = _piece_id(**_identity())

    assert payload == f"{BARCODE_PREFIX}{piece_id}"
    assert parse_preparation_piece_barcode(payload) == piece_id
    assert parse_preparation_piece_barcode(piece_id.upper()) == piece_id
    assert parse_preparation_piece_barcode("276218536") is None


def test_batch_pdf_line_uses_unique_piece_barcode_not_order_number():
    line = _line_from_batch_storage(
        {
            "order_number": "276218536",
            "order_item_id": "item-7",
            "unit_index": 2,
            "quantity": 1,
            "product_name": "سلسال بالاسم",
        },
        {"id": "batch-1", "user_id": "owner-1"},
    )

    assert line.barcode_payload == preparation_piece_barcode(**_identity())
    assert line.barcode_payload != line.order_number


def test_receiving_rejects_duplicate_cancelled_and_blocked_pieces():
    assert piece_scan_blocker({"status": PIECE_STATUS_IN_PROGRESS}) is None
    assert (
        piece_scan_blocker(
            {
                "status": PIECE_STATUS_RECEIVED,
                "received_by_name": "محمد",
            }
        )["code"]
        == "supplier_piece_already_received"
    )
    assert piece_scan_blocker(
        {
            "status": PIECE_STATUS_CANCELLED,
            "cancellation_reason": "ألغاه العميل",
        }
    ) == {
        "code": "supplier_piece_cancelled",
        "message": "ألغاه العميل",
        "reason": "ألغاه العميل",
    }
    assert (
        piece_scan_blocker(
            {
                "status": PIECE_STATUS_BLOCKED,
                "block_reason": "متوقف من خدمة العملاء",
            }
        )["reason"]
        == "متوقف من خدمة العملاء"
    )


def test_receipt_records_preparer_and_receiver_without_formal_supplier_link():
    received_at = datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc)
    patch = supplier_receipt_piece_patch(
        session={
            "id": "session-1",
            "reference": "SR-20260804-ABC123",
            "user_id": "owner-1",
            "supplier_id": "supplier-1",
        },
        actor={"id": "receiver-2", "name": "موظف الاستلام"},
        piece_id="piece-1",
        barcode="MEZAN-PIECE:abc",
        received_at=received_at,
    )

    assert patch["status"] == PIECE_STATUS_RECEIVED
    assert patch["received_by_id"] == "receiver-2"
    assert patch["supplier_receiving_session_id"] == "session-1"
    assert patch["supplier_service_link_status"] == "pending_service_approval"
    assert "supplier_id" not in patch
    assert "supplier_name" not in patch
    assert "invoice_id" not in patch
    assert patch["salla_updated"] is False
    assert patch["qoyod_updated"] is False


def test_router_exposes_catalog_open_scan_get_and_close_contracts():
    router = make_supplier_receiving_router(
        SimpleNamespace(),
        lambda: {"id": "owner-1", "role": "owner"},
    )
    routes = {
        (route.path, method) for route in router.routes for method in route.methods
    }

    assert ("/supplier-receiving-v1/catalog", "GET") in routes
    assert ("/supplier-receiving-v1/sessions", "POST") in routes
    assert ("/supplier-receiving-v1/sessions/{session_id}", "GET") in routes
    assert ("/supplier-receiving-v1/sessions/{session_id}/scan", "POST") in routes
    assert ("/supplier-receiving-v1/sessions/{session_id}/close", "POST") in routes


def test_scan_contract_is_atomic_and_never_completes_services_or_accounting():
    source = inspect.getsource(make_supplier_receiving_router)

    assert "find_one_and_update" in source
    assert '"status": {"$in": sorted(ELIGIBLE_PIECE_STATUSES)}' in source
    assert '"supplier_piece_already_received"' not in source  # central blocker owns it
    assert '"financial_invoice_created": False' in source
    assert '"liability_created": False' in source
    assert '"supplier_service_link_created": False' in source
    assert '"completed_at"' not in source
