#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

path = Path(__file__).resolve().parents[1] / "backend" / "tests" / "test_supplier_receiving.py"
text = path.read_text(encoding="utf-8")


def replace_span(source: str, start: str, end: str, replacement: str, label: str) -> str:
    start_index = source.find(start)
    if start_index < 0:
        raise RuntimeError(f"{label}: start anchor not found")
    end_index = source.find(end, start_index)
    if end_index < 0:
        raise RuntimeError(f"{label}: end anchor not found")
    return source[:start_index] + replacement + source[end_index:]


text = replace_span(
    text,
    "def test_piece_services_must_be_covered_by_selected_mezan_supplier():\n",
    "def test_supplier_may_perform_one_service_from_a_product_group():\n",
    '''def test_supplier_service_selection_is_optional_and_not_limited_to_supplier_links():
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
    assert supplier_piece_service_blocker(
        {"services": [{"service_id": "print", "service_name": "طباعة"}]},
        session,
    ) is None
    assert supplier_piece_service_blocker({"services": []}, session) is None
    assert supplier_piece_service_blocker(
        {"services": []},
        session,
        allow_service_addition=True,
    ) is None


''',
    "optional supplier service blocker test",
)

text = replace_span(
    text,
    "def test_price_overrides_and_service_additions_require_their_exact_permissions():\n",
    "def test_service_completion_keeps_unfinished_group_services_open():\n",
    '''def test_price_overrides_require_permissions_and_additions_use_dedicated_route():
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
            "service_links": [{"service_id": "engrave"}],
        },
    }
    override_request = [SupplierReceivingInvoiceLineRequest(
        piece_ids=["piece-1"],
        product_unit_price_halalas=550,
        services=[SupplierReceivingInvoiceServiceRequest(
            service_id="engrave",
            unit_price_halalas=325,
        )],
    )]
    try:
        build_supplier_receiving_invoice(
            session=session,
            scans=[scan],
            requested_lines=override_request,
            saved_at=datetime.now(timezone.utc),
            permissions=set(),
            service_catalog={},
        )
        assert False, "permission guard must reject the override"
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 403

    invoice = build_supplier_receiving_invoice(
        session=session,
        scans=[scan],
        requested_lines=override_request,
        saved_at=datetime.now(timezone.utc),
        permissions={
            EDIT_PRODUCT_PRICE_PERMISSION,
            EDIT_SERVICE_PRICE_PERMISSION,
        },
        service_catalog={},
    )
    assert invoice["total_halalas"] == 875
    assert len(invoice["price_changes"]) == 2
    assert invoice["added_product_services"] == []

    addition_request = [SupplierReceivingInvoiceLineRequest(
        piece_ids=["piece-1"],
        product_unit_price_halalas=500,
        services=[
            SupplierReceivingInvoiceServiceRequest(
                service_id="engrave",
                unit_price_halalas=300,
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
            requested_lines=addition_request,
            saved_at=datetime.now(timezone.utc),
            permissions={ADD_PRODUCT_SERVICE_PERMISSION},
            service_catalog={
                "paint": {"id": "paint", "name": "طلاء", "unit_cost": 2}
            },
        )
        assert False, "new services must use the dedicated permanent endpoint"
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 409
        assert exc.detail["code"] == (
            "supplier_receiving_service_add_via_product_required"
        )


''',
    "dedicated service addition test",
)

path.write_text(text, encoding="utf-8")
print("Updated legacy supplier receiving tests for optional service policy.")
