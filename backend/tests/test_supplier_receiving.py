import inspect
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

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
from supplier_invoice_pdf import generate_supplier_invoice_pdf
from supplier_receiving_routes import (
    ADD_PRODUCT_SERVICE_PERMISSION,
    EDIT_PRODUCT_PRICE_PERMISSION,
    EDIT_SERVICE_PRICE_PERMISSION,
    SUPPLIER_INVOICES,
    SupplierPieceScanRequest,
    SupplierReceivingInvoiceLineRequest,
    SupplierReceivingInvoiceServiceRequest,
    _share_evidence_signature_matches,
    _supplier_invoice_filename,
    build_supplier_receiving_invoice,
    _post_supplier_invoice_ledger,
    make_supplier_receiving_router,
    piece_scan_blocker,
    supplier_piece_product_charge_eligible,
    supplier_piece_service_blocker,
    supplier_piece_reference_price,
    supplier_scan_group_candidates,
    supplier_receipt_piece_patch,
    supplier_receipt_piece_rollback_update,
    supplier_receipt_previous_piece_state,
    supplier_service_completion_update,
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


def test_multi_quantity_batch_card_uses_first_piece_as_its_scan_anchor():
    line = _line_from_batch_storage(
        {
            "order_number": "276218536",
            "order_item_id": "item-7",
            "unit_indices": [1, 2, 3],
            "quantity": 3,
            "product_name": "سلسال بالاسم",
        },
        {"id": "batch-1", "user_id": "owner-1"},
    )

    assert line.barcode_payload == preparation_piece_barcode(
        **{**_identity(), "unit_index": 1}
    )


def test_supplier_reassignment_requires_an_explicit_scan_confirmation():
    default_request = SupplierPieceScanRequest(barcode="piece-1")
    confirmed_request = SupplierPieceScanRequest(
        barcode="piece-1",
        confirm_supplier_reassignment=True,
    )

    assert default_request.confirm_supplier_reassignment is False
    assert confirmed_request.confirm_supplier_reassignment is True


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


def test_scan_only_reserves_piece_without_recording_supplier_completion():
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

    assert "status" not in patch
    assert patch["execution_status"] == "supplier_receiving_draft"
    assert patch["supplier_receiving_session_id"] == "session-1"
    assert "received_at" not in patch
    assert "supplier_id" not in patch
    assert "supplier_service_ids" not in patch
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
    assert "supplier_reassigned_from_id" in rollback["$unset"]
    assert "supplier_reassignment_session_id" in rollback["$unset"]


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
    assert mismatch["required_services"] == [
        {"service_id": "print", "service_name": "طباعة"}
    ]

    missing_plan = supplier_piece_service_blocker({"services": []}, session)
    assert missing_plan["code"] == "supplier_piece_services_missing"
    assert supplier_piece_service_blocker(
        {"services": []},
        session,
        allow_service_addition=True,
    ) is None


def test_supplier_may_perform_one_service_from_a_product_group():
    session = {
        "supplier_snapshot": {
            "id": "supplier-1",
            "service_links": [{"service_id": "cut"}],
        }
    }
    assert supplier_piece_service_blocker(
        {
            "services": [
                {"service_id": "cut", "service_name": "قص"},
                {"service_id": "paint", "service_name": "طلاء"},
            ]
        },
        session,
    ) is None


@pytest.mark.asyncio
async def test_quantity_candidates_are_limited_to_the_same_exact_card():
    rows = [
        {
            "piece_id": "piece-2",
            "batch_id": "batch-1",
            "order_item_id": "item-7",
            "group_key": "product:1",
            "unit_index": 2,
            "status": PIECE_STATUS_IN_PROGRESS,
            "services": [{"service_id": "engrave", "required_quantity": 1}],
        },
        {
            "piece_id": "piece-3",
            "batch_id": "batch-1",
            "order_item_id": "item-7",
            "group_key": "product:1",
            "unit_index": 3,
            "status": PIECE_STATUS_IN_PROGRESS,
            "services": [{"service_id": "engrave", "required_quantity": 1}],
        },
    ]

    class _Cursor:
        def __init__(self, values):
            self.values = values

        def sort(self, *_args):
            return self

        def limit(self, *_args):
            return self

        async def to_list(self, _limit):
            return self.values

    class _Collection:
        def __init__(self):
            self.query = None

        def find(self, query, _projection):
            self.query = query
            return _Cursor(rows)

    collection = _Collection()

    class _DB:
        def __getitem__(self, _name):
            return collection

    candidates = await supplier_scan_group_candidates(
        _DB(),
        user_id="owner-1",
        scanned_piece={
            "piece_id": "piece-1",
            "batch_id": "batch-1",
            "order_item_id": "item-7",
            "group_key": "product:1",
            "unit_index": 1,
            "status": PIECE_STATUS_RECEIVED,
            "services": [{"service_id": "engrave", "required_quantity": 1}],
        },
        session={"supplier_snapshot": {
            "id": "supplier-1",
            "service_links": [{"service_id": "engrave"}],
        }},
        allow_service_addition=False,
    )

    assert [row["piece_id"] for row in candidates] == ["piece-2", "piece-3"]
    assert collection.query["batch_id"] == "batch-1"
    assert collection.query["order_item_id"] == "item-7"


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


def test_invoice_draft_separates_product_and_service_prices():
    saved_at = datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc)
    base = {
        "product_id": "product-1",
        "product_name": "سلسال بالاسم",
        "sku": "N-1",
        "services": [{"service_id": "engrave", "required_quantity": 1}],
        "invoice_services": [{
            "service_id": "engrave",
            "service_name": "حفر الاسم",
            "required_quantity": 1,
            "reference_unit_price_halalas": 350,
        }],
        "reference_product_unit_price_halalas": 500,
    }
    invoice = build_supplier_receiving_invoice(
        session={
            "reference": "SR-20260805-ABC123",
            "supplier_snapshot": {
                "service_links": [{"service_id": "engrave"}],
            },
        },
        scans=[
            {**base, "piece_id": "piece-1"},
            {**base, "piece_id": "piece-2"},
        ],
        requested_lines=[SupplierReceivingInvoiceLineRequest(
            piece_ids=["piece-1", "piece-2"],
            product_unit_price_halalas=500,
            services=[SupplierReceivingInvoiceServiceRequest(
                service_id="engrave",
                unit_price_halalas=350,
            )],
        )],
        saved_at=saved_at,
    )

    assert invoice["piece_count"] == 2
    assert invoice["line_count"] == 1
    assert invoice["lines"][0]["quantity"] == 2
    assert invoice["lines"][0]["product_total_halalas"] == 1000
    assert invoice["lines"][0]["services_total_halalas"] == 700
    assert invoice["lines"][0]["total_halalas"] == 1700
    assert invoice["total_halalas"] == 1700
    assert invoice["financial_invoice_created"] is False
    assert invoice["liability_created"] is False


def test_invoice_draft_allows_normally_priced_product_without_services():
    invoice = build_supplier_receiving_invoice(
        session={
            "reference": "SR-20260811-NORMAL",
            "supplier_snapshot": {"service_links": []},
        },
        scans=[{
            "piece_id": "piece-normal-1",
            "product_id": "product-normal-1",
            "product_name": "منتج عادي",
            "sku": "NORMAL-1",
            "services": [],
            "invoice_services": [],
            "reference_product_unit_price_halalas": 1500,
        }],
        requested_lines=[SupplierReceivingInvoiceLineRequest(
            piece_ids=["piece-normal-1"],
            product_unit_price_halalas=1500,
            services=[],
        )],
        saved_at=datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc),
    )

    assert invoice["piece_count"] == 1
    assert invoice["lines"][0]["product_total_halalas"] == 1500
    assert invoice["lines"][0]["services_total_halalas"] == 0
    assert invoice["lines"][0]["services"] == []
    assert invoice["total_halalas"] == 1500


def test_second_supplier_invoice_rejects_product_price_and_charges_service_only():
    scan = {
        "piece_id": "piece-1",
        "product_id": "product-1",
        "product_name": "سلسال",
        "product_charge_eligible": False,
        "reference_product_unit_price_halalas": 0,
        "invoice_services": [{
            "service_id": "paint",
            "service_name": "طلاء",
            "required_quantity": 1,
            "reference_unit_price_halalas": 200,
        }],
    }
    assert supplier_piece_product_charge_eligible({
        "supplier_receiving_history": [{"invoice_id": "invoice-old"}],
    }) is False

    with pytest.raises(Exception) as error:
        build_supplier_receiving_invoice(
            session={"supplier_snapshot": {"service_links": [{"service_id": "paint"}]}},
            scans=[scan],
            requested_lines=[SupplierReceivingInvoiceLineRequest(
                piece_ids=["piece-1"],
                product_unit_price_halalas=500,
                services=[SupplierReceivingInvoiceServiceRequest(
                    service_id="paint",
                    unit_price_halalas=200,
                )],
            )],
            saved_at=datetime.now(timezone.utc),
        )
    assert error.value.detail["code"] == "supplier_receiving_product_already_charged"

    invoice = build_supplier_receiving_invoice(
        session={"supplier_snapshot": {"service_links": [{"service_id": "paint"}]}},
        scans=[scan],
        requested_lines=[SupplierReceivingInvoiceLineRequest(
            piece_ids=["piece-1"],
            product_unit_price_halalas=0,
            services=[SupplierReceivingInvoiceServiceRequest(
                service_id="paint",
                unit_price_halalas=200,
            )],
        )],
        saved_at=datetime.now(timezone.utc),
    )
    assert invoice["lines"][0]["product_total_halalas"] == 0
    assert invoice["lines"][0]["services_total_halalas"] == 200
    assert invoice["total_halalas"] == 200


def test_supplier_invoice_pdf_contains_a_valid_pdf_document():
    pdf = generate_supplier_invoice_pdf({
        "invoice_number": "SI-20260809-1",
        "approved_at": datetime(2026, 8, 9, tzinfo=timezone.utc),
        "supplier_approved_by_name": "خالد",
        "supplier_snapshot": {"company_name": "مورد أماسي"},
        "lines": [{
            "product_name": "سلسال",
            "quantity": 2,
            "product_unit_price_halalas": 500,
            "product_total_halalas": 1000,
            "services": [{
                "service_name": "حفر",
                "total_quantity": 2,
                "unit_price_halalas": 200,
                "total_halalas": 400,
            }],
            "total_halalas": 1400,
        }],
        "total_halalas": 1400,
    })

    assert pdf.startswith(b"%PDF-")
    assert len(pdf) > 1000


def test_share_artifacts_use_supplier_name_and_validate_image_signature():
    filename = _supplier_invoice_filename({
        "invoice_number": "SI-1",
        "supplier_snapshot": {"company_name": "مورد/أماسي"},
    })

    assert filename == "فاتورة-مورد-أماسي-SI-1.pdf"
    assert _share_evidence_signature_matches(
        "image/png", b"\x89PNG\r\n\x1a\ncontent"
    )
    assert not _share_evidence_signature_matches(
        "image/png", b"not-an-image"
    )


def test_invoice_rejects_grouping_pieces_with_different_pending_services():
    base = {
        "product_id": "product-1",
        "product_name": "سلسال",
        "sku": "N-1",
        "services": [
            {"service_id": "engrave", "required_quantity": 1},
            {"service_id": "paint", "required_quantity": 1},
        ],
        "reference_product_unit_price_halalas": 500,
    }
    scans = [
        {
            **base,
            "piece_id": "piece-1",
            "invoice_services": [{
                "service_id": "engrave",
                "service_name": "حفر",
                "required_quantity": 1,
                "reference_unit_price_halalas": 300,
            }],
        },
        {
            **base,
            "piece_id": "piece-2",
            "invoice_services": [{
                "service_id": "paint",
                "service_name": "طلاء",
                "required_quantity": 1,
                "reference_unit_price_halalas": 200,
            }],
        },
    ]
    try:
        build_supplier_receiving_invoice(
            session={
                "reference": "SR-1",
                "supplier_snapshot": {
                    "service_links": [
                        {"service_id": "engrave"},
                        {"service_id": "paint"},
                    ],
                },
            },
            scans=scans,
            requested_lines=[SupplierReceivingInvoiceLineRequest(
                piece_ids=["piece-1", "piece-2"],
                product_unit_price_halalas=500,
                services=[SupplierReceivingInvoiceServiceRequest(
                    service_id="engrave",
                    unit_price_halalas=300,
                )],
            )],
            saved_at=datetime.now(timezone.utc),
        )
        assert False, "different pending service sets must not share one line"
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 409
        assert exc.detail["code"] == "supplier_receiving_invoice_group_mismatch"


def test_price_overrides_and_service_additions_require_their_exact_permissions():
    scan = {
        "piece_id": "piece-1",
        "product_id": "product-1",
        "product_name": "سلسال",
        "sku": "N-1",
        "services": [{"service_id": "engrave", "required_quantity": 1}],
        "invoice_services": [{
            "service_id": "engrave",
            "service_name": "حفر",
            "required_quantity": 1,
            "reference_unit_price_halalas": 300,
        }],
        "reference_product_unit_price_halalas": 500,
    }
    session = {
        "reference": "SR-1",
        "supplier_snapshot": {
            "service_links": [
                {"service_id": "engrave"},
                {"service_id": "paint"},
            ],
        },
    }
    request = [SupplierReceivingInvoiceLineRequest(
        piece_ids=["piece-1"],
        product_unit_price_halalas=550,
        services=[
            SupplierReceivingInvoiceServiceRequest(
                service_id="engrave",
                unit_price_halalas=325,
            ),
            SupplierReceivingInvoiceServiceRequest(
                service_id="paint",
                unit_price_halalas=200,
                add_to_product=True,
            ),
        ],
    )]
    try:
        build_supplier_receiving_invoice(
            session=session,
            scans=[scan],
            requested_lines=request,
            saved_at=datetime.now(timezone.utc),
            permissions=set(),
            service_catalog={"paint": {"id": "paint", "name": "طلاء", "unit_cost": 2}},
        )
        assert False, "permission guard must reject the override"
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 403

    invoice = build_supplier_receiving_invoice(
        session=session,
        scans=[scan],
        requested_lines=request,
        saved_at=datetime.now(timezone.utc),
        permissions={
            EDIT_PRODUCT_PRICE_PERMISSION,
            EDIT_SERVICE_PRICE_PERMISSION,
            ADD_PRODUCT_SERVICE_PERMISSION,
        },
        service_catalog={"paint": {"id": "paint", "name": "طلاء", "unit_cost": 2}},
    )
    assert invoice["total_halalas"] == 1075
    assert len(invoice["price_changes"]) == 2
    assert invoice["added_product_services"][0]["service_id"] == "paint"


def test_service_completion_keeps_unfinished_group_services_open():
    update = supplier_service_completion_update(
        piece={
            "services": [
                {"service_id": "cut", "service_name": "قص", "required_quantity": 1, "status": "pending"},
                {"service_id": "paint", "service_name": "طلاء", "required_quantity": 1, "status": "pending"},
            ]
        },
        invoice_line={
            "services": [{
                "service_id": "cut",
                "service_name": "قص",
                "quantity_per_piece": 1,
                "unit_price_halalas": 200,
            }]
        },
        session={
            "id": "session-1",
            "reference": "SR-1",
            "supplier_snapshot": {"id": "supplier-1", "company_name": "مورد القص"},
        },
        actor={"id": "receiver-1", "name": "المستلم"},
        invoice_id="invoice-1",
        completed_at=datetime.now(timezone.utc),
    )
    assert update["$set"]["status"] == PIECE_STATUS_IN_PROGRESS
    assert update["$set"]["supplier_dispatch_status"] == "partial_received"
    assert update["$set"]["remaining_service_count"] == 1
    assert update["$set"]["services"][0]["status"] == "completed"
    assert update["$set"]["services"][1]["status"] == "pending"
    assert "supplier_receiving_session_id" in update["$unset"]


@pytest.mark.asyncio
async def test_mezan_supplier_invoice_posts_balanced_payable_ledger_legs_in_transaction():
    class _AggregateCursor:
        async def to_list(self, _limit):
            return [{"mx": 41}]

    general_ledger = SimpleNamespace(
        aggregate=MagicMock(return_value=_AggregateCursor()),
        insert_many=AsyncMock(),
    )
    accounting_audit_log = SimpleNamespace(insert_many=AsyncMock())
    db = SimpleNamespace(
        general_ledger=general_ledger,
        accounting_audit_log=accounting_audit_log,
    )
    mongo_session = object()

    result = await _post_supplier_invoice_ledger(
        db,
        user_id="owner-1",
        actor={"id": "receiver-1", "name": "المستلم"},
        invoice={
            "id": "invoice-1",
            "invoice_number": "SI-1",
            "session_id": "session-1",
            "supplier_id": "supplier-v2-1",
            "total_halalas": 1075,
            "approved_at": datetime(2026, 8, 6, 12, 0, tzinfo=timezone.utc),
        },
        mongo_session=mongo_session,
    )

    entries = general_ledger.insert_many.await_args.args[0]
    assert [row["entry_no"] for row in entries] == [42, 43]
    assert [(row["side"], row["amount"]) for row in entries] == [
        ("debit", 10.75),
        ("credit", 10.75),
    ]
    assert entries[1]["entity_type"] == "supplier"
    assert entries[1]["entity_id"] == "supplier-v2-1"
    assert entries[1]["sub_account"] == "payable"
    assert entries[1]["metadata"]["mezan_supplier_v2"] is True
    assert general_ledger.insert_many.await_args.kwargs["session"] is mongo_session
    assert accounting_audit_log.insert_many.await_args.kwargs["session"] is mongo_session
    assert result["amount"] == 10.75


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
    assert ("/supplier-receiving-v1/invoices/{invoice_id}", "GET") in routes
    assert ("/supplier-receiving-v1/invoices/{invoice_id}/pdf", "GET") in routes
    assert ("/supplier-receiving-v1/invoices/{invoice_id}/share-evidence", "POST") in routes
    assert ("/supplier-receiving-v1/invoices/{invoice_id}/confirm-share", "POST") in routes


def test_scan_reserves_then_close_posts_one_atomic_mezan_accounting_invoice():
    source = inspect.getsource(make_supplier_receiving_router)

    assert "find_one_and_update" in source
    assert '"status": {"$in": sorted(ELIGIBLE_PIECE_STATUSES)}' in source
    assert '"supplier_piece_already_received"' not in source  # central blocker owns it
    assert '"financial_invoice_created": False' in source
    assert '"liability_created": False' in source
    assert "with_transaction(finalize)" in source
    assert SUPPLIER_INVOICES == "mezan_supplier_invoices_v2"
    assert '"supplier_invoice": invoice_summary' in source
    assert '"supplier_service_link_applied": not is_experiment' in source
    assert '"financial_invoice_created": not is_experiment' in source
    assert "supplier_receiving_piece_stopped_before_invoice" in source
    assert "supplier_piece_service_blocker(" in source
    assert "allow_service_addition=(" in source
    assert "supplier_receipt_previous_piece_state(original_piece)" in source
    assert '"status": "cancelled"' in source
    assert '"operational_invoice_created": False' in source
    assert "completed_at=now" in source
    assert '"qoyod_updated": False' in source
