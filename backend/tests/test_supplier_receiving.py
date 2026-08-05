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
    SupplierReceivingInvoiceLineRequest,
    build_supplier_receiving_invoice,
    make_supplier_receiving_router,
    piece_scan_blocker,
    supplier_piece_service_blocker,
    supplier_piece_reference_price,
    supplier_receipt_piece_patch,
    supplier_receipt_piece_rollback_update,
    supplier_receipt_previous_piece_state,
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


def test_receipt_records_operational_supplier_link_without_accounting_write():
    received_at = datetime(2026, 8, 4, 12, 0, tzinfo=timezone.utc)
    patch = supplier_receipt_piece_patch(
        session={
            "id": "session-1",
            "reference": "SR-20260804-ABC123",
            "user_id": "owner-1",
            "supplier_id": "supplier-1",
            "supplier_snapshot": {
                "id": "supplier-1",
                "company_name": "مورد أماسي",
                "service_links": [{"service_id": "engrave"}],
            },
        },
        actor={"id": "receiver-2", "name": "موظف الاستلام"},
        piece_id="piece-1",
        barcode="MEZAN-PIECE:abc",
        received_at=received_at,
    )

    assert patch["status"] == PIECE_STATUS_RECEIVED
    assert patch["received_by_id"] == "receiver-2"
    assert patch["supplier_receiving_session_id"] == "session-1"
    assert patch["supplier_service_link_status"] == "catalog_linked"
    assert patch["supplier_id"] == "supplier-1"
    assert patch["supplier_name"] == "مورد أماسي"
    assert patch["supplier_service_ids"] == ["engrave"]
    assert "invoice_id" not in patch
    assert patch["salla_updated"] is False
    assert patch["qoyod_updated"] is False


def test_receipt_cancel_snapshot_restores_the_exact_previous_piece_state():
    original_updated_at = datetime(2026, 8, 5, 9, 0, tzinfo=timezone.utc)
    previous_piece = {
        "status": PIECE_STATUS_IN_PROGRESS,
        "execution_status": "in_progress",
        "updated_at": original_updated_at,
        "mezan_only": True,
        "salla_updated": False,
        "qoyod_updated": False,
    }

    snapshot = supplier_receipt_previous_piece_state(previous_piece)
    rollback = supplier_receipt_piece_rollback_update(snapshot)

    assert rollback["$set"]["status"] == PIECE_STATUS_IN_PROGRESS
    assert rollback["$set"]["execution_status"] == "in_progress"
    assert rollback["$set"]["updated_at"] == original_updated_at
    assert "supplier_receiving_session_id" in rollback["$unset"]
    assert "receipt_event_id" in rollback["$unset"]
    assert "received_at" in rollback["$unset"]


def test_piece_services_must_be_covered_by_selected_mezan_supplier():
    session = {
        "supplier_snapshot": {
            "id": "supplier-1",
            "company_name": "مورد الحفر",
            "service_links": [{"service_id": "engrave"}],
        }
    }
    assert supplier_piece_service_blocker(
        {"services": [{"service_id": "engrave", "service_name": "حفر الاسم"}]},
        session,
    ) is None

    mismatch = supplier_piece_service_blocker(
        {"services": [{"service_id": "print", "service_name": "طباعة"}]},
        session,
    )
    assert mismatch["code"] == "supplier_piece_service_mismatch"
    assert mismatch["missing_services"] == [
        {"service_id": "print", "service_name": "طباعة"}
    ]

    missing_plan = supplier_piece_service_blocker({"services": []}, session)
    assert missing_plan["code"] == "supplier_piece_services_missing"


def test_supplier_reference_price_uses_service_quantity_and_flags_missing_costs():
    priced = supplier_piece_reference_price({
        "services": [
            {"service_id": "engrave", "reference_unit_cost": 4.25, "required_quantity": 2},
            {"service_id": "box", "reference_unit_cost": "1.50", "required_quantity": 1},
        ]
    })
    assert priced == {
        "reference_unit_price_halalas": 1000,
        "reference_price_complete": True,
        "missing_price_service_ids": [],
    }

    missing = supplier_piece_reference_price({
        "services": [{"service_id": "print", "reference_unit_cost": None}]
    })
    assert missing["reference_unit_price_halalas"] == 0
    assert missing["reference_price_complete"] is False
    assert missing["missing_price_service_ids"] == ["print"]


def test_operational_invoice_covers_every_scan_once_and_never_creates_liability():
    saved_at = datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc)
    base = {
        "product_id": "product-1",
        "product_name": "سلسال بالاسم",
        "sku": "N-1",
        "services": [{"service_id": "engrave", "required_quantity": 1}],
    }
    invoice = build_supplier_receiving_invoice(
        session={"reference": "SR-20260805-ABC123"},
        scans=[
            {**base, "piece_id": "piece-1"},
            {**base, "piece_id": "piece-2"},
        ],
        requested_lines=[SupplierReceivingInvoiceLineRequest(
            piece_ids=["piece-1", "piece-2"],
            unit_price_halalas=850,
        )],
        saved_at=saved_at,
    )

    assert invoice["piece_count"] == 2
    assert invoice["line_count"] == 1
    assert invoice["lines"][0]["quantity"] == 2
    assert invoice["lines"][0]["total_halalas"] == 1700
    assert invoice["total_halalas"] == 1700
    assert invoice["financial_invoice_created"] is False
    assert invoice["liability_created"] is False


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
    assert ("/supplier-receiving-v1/sessions/{session_id}/cancel", "POST") in routes
    assert ("/supplier-receiving-v1/sessions/{session_id}/close", "POST") in routes


def test_scan_contract_is_atomic_and_never_creates_accounting():
    source = inspect.getsource(make_supplier_receiving_router)

    assert "find_one_and_update" in source
    assert '"status": {"$in": sorted(ELIGIBLE_PIECE_STATUSES)}' in source
    assert '"supplier_piece_already_received"' not in source  # central blocker owns it
    assert '"financial_invoice_created": False' in source
    assert '"liability_created": False' in source
    assert '"operational_invoice": operational_invoice' in source
    assert '"supplier_service_link_applied": True' in source
    assert "supplier_piece_service_blocker(piece, session)" in source
    assert "supplier_receipt_previous_piece_state(piece)" in source
    assert '"status": "cancelled"' in source
    assert '"operational_invoice_created": False' in source
    assert '"completed_at"' not in source
