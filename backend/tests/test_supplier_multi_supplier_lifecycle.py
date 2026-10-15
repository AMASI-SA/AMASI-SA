from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from preparation_piece_operations import PIECE_STATUS_IN_PROGRESS
from preparation_supplier_dispatch import (
    DISPATCH_STATUS_PARTIAL,
    piece_is_available_for_supplier_dispatch,
    supplier_receiving_dispatch_blocker,
)
from supplier_receiving_routes import (
    SupplierReceivingInvoiceLineRequest,
    SupplierReceivingInvoiceServiceRequest,
    build_supplier_receiving_invoice,
    supplier_partial_receipt_assignment_patch,
    supplier_piece_product_charge_eligible,
)


def _partially_completed_piece() -> dict:
    return {
        "piece_id": "piece-multi-supplier-1",
        "product_id": "product-1",
        "product_name": "منتج متعدد الخدمات",
        "sku": "MULTI-1",
        "group_key": "product-1",
        "order_number": "100001",
        "status": PIECE_STATUS_IN_PROGRESS,
        "execution_status": "awaiting_remaining_services",
        "supplier_dispatch_status": DISPATCH_STATUS_PARTIAL,
        "supplier_id": "supplier-1",
        "supplier_name": "المورد الأول",
        "supplier_receiving_history": [
            {
                "invoice_id": "invoice-supplier-1",
                "supplier_id": "supplier-1",
                "service_ids": ["cut"],
            }
        ],
        "services": [
            {
                "service_id": "cut",
                "service_name": "قص",
                "status": "completed",
                "required_quantity": 1,
                "completed_quantity": 1,
                "supplier_invoice_id": "invoice-supplier-1",
            },
            {
                "service_id": "paint",
                "service_name": "طلاء",
                "status": "pending",
                "required_quantity": 1,
                "completed_quantity": 0,
                "source": "option",
                "customer_selected": True,
            },
        ],
    }


def test_partially_completed_piece_is_assigned_during_second_supplier_receipt() -> None:
    piece = _partially_completed_piece()
    session = {
        "id": "session-supplier-2",
        "supplier_id": "supplier-2",
        "supplier_snapshot": {
            "id": "supplier-2",
            "company_name": "المورد الثاني",
            "service_links": [
                {"service_id": "paint", "service_name": "طلاء"},
            ],
        },
    }

    # No second supplier dispatch file is created. The receiving scan itself is
    # the explicit assignment action for supplier 2 and the remaining service.
    assert piece_is_available_for_supplier_dispatch(piece) is False
    assert supplier_receiving_dispatch_blocker(piece, "supplier-2") is None

    patch = supplier_partial_receipt_assignment_patch(
        piece=piece,
        session=session,
        actor={"id": "receiver-2", "name": "موظف الاستلام"},
        assigned_at=datetime.now(timezone.utc),
    )
    assert patch["supplier_id"] == "supplier-2"
    assert patch["supplier_assigned_at_receipt"] is True
    assert patch["supplier_assignment_mode"] == "direct_at_partial_receipt"
    assert patch["supplier_assigned_from_id"] == "supplier-1"
    assert supplier_piece_product_charge_eligible(piece) is False


def test_second_supplier_invoice_charges_selected_service_only() -> None:
    piece = _partially_completed_piece()
    scan = {
        **piece,
        "reference_product_unit_price_halalas": 0,
        "product_charge_eligible": supplier_piece_product_charge_eligible(piece),
        "invoice_services": [
            {
                "service_id": "paint",
                "service_name": "طلاء",
                "required_quantity": 1,
                "reference_unit_price_halalas": 250,
                "customer_selected": True,
            }
        ],
    }
    session = {
        "reference": "SR-SECOND-SUPPLIER",
        "supplier_snapshot": {
            "id": "supplier-2",
            "company_name": "المورد الثاني",
            "service_links": [
                {"service_id": "paint", "service_name": "طلاء"},
            ],
        },
    }

    with pytest.raises(HTTPException) as captured:
        build_supplier_receiving_invoice(
            session=session,
            scans=[scan],
            requested_lines=[
                SupplierReceivingInvoiceLineRequest(
                    piece_ids=[piece["piece_id"]],
                    product_unit_price_halalas=5000,
                    services=[
                        SupplierReceivingInvoiceServiceRequest(
                            service_id="paint",
                            unit_price_halalas=250,
                        )
                    ],
                )
            ],
            saved_at=datetime.now(timezone.utc),
        )
    assert captured.value.detail["code"] == "supplier_receiving_product_already_charged"

    invoice = build_supplier_receiving_invoice(
        session=session,
        scans=[scan],
        requested_lines=[
            SupplierReceivingInvoiceLineRequest(
                piece_ids=[piece["piece_id"]],
                product_unit_price_halalas=0,
                services=[
                    SupplierReceivingInvoiceServiceRequest(
                        service_id="paint",
                        unit_price_halalas=250,
                    )
                ],
            )
        ],
        saved_at=datetime.now(timezone.utc),
    )

    line = invoice["lines"][0]
    assert line["product_total_halalas"] == 0
    assert line["services_total_halalas"] == 250
    assert line["total_halalas"] == 250
    assert invoice["total_halalas"] == 250
