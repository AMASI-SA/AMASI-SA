from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from preparation_piece_operations import inherit_required_services
from supplier_receiving_routes import (
    ADD_PRODUCT_SERVICE_PERMISSION,
    SupplierReceivingInvoiceLineRequest,
    SupplierReceivingInvoiceServiceRequest,
    _invoice_group_key,
    build_supplier_receiving_invoice,
    _permanent_supplier_service_snapshot,
    supplier_piece_invoice_services,
    supplier_piece_service_blocker,
    supplier_service_completion_update,
    supplier_uninvoiced_piece_selector,
)


def _session() -> dict:
    return {
        "supplier_snapshot": {
            "id": "supplier-1",
            "company_name": "مورد",
            "service_links": [
                {"service_id": "ordinary", "service_name": "عادية"},
                {"service_id": "option", "service_name": "خيار العميل"},
                {"service_id": "permanent", "service_name": "دائمة"},
            ],
        }
    }


def test_product_link_not_selected_by_customer_is_hidden_from_invoice() -> None:
    rows = supplier_piece_invoice_services(
        {
            "services": [
                {
                    "service_id": "ordinary",
                    "service_name": "عادية",
                    "source": "product",
                    "status": "pending",
                    "required_quantity": 1,
                },
                {
                    "service_id": "option",
                    "service_name": "خيار العميل",
                    "source": "option",
                    "status": "pending",
                    "required_quantity": 1,
                },
                {
                    "service_id": "permanent",
                    "service_name": "دائمة",
                    "source": "supplier_receiving_permanent",
                    "supplier_invoice_required": True,
                    "status": "pending",
                    "required_quantity": 1,
                },
            ]
        },
        _session(),
        {},
    )

    assert [row["service_id"] for row in rows] == ["option", "permanent"]
    assert rows[0]["customer_selected"] is True
    assert rows[1]["supplier_invoice_required"] is True


def test_explicit_empty_invoice_services_never_falls_back_to_product_services() -> None:
    key = _invoice_group_key(
        {
            "product_id": "p1",
            "sku": "sku",
            "product_name": "منتج",
            "invoice_services": [],
            "services": [{"service_id": "ordinary", "required_quantity": 1}],
        }
    )
    assert key[-1] == ()


def test_service_selection_is_optional_for_supplier_receipt() -> None:
    assert supplier_piece_service_blocker({}, _session()) is None


def test_future_piece_marks_permanent_service_but_not_ordinary_product_link() -> None:
    resources = {
        "ordinary": {
            "id": "ordinary",
            "name": "عادية",
            "kind": "service",
            "unit_cost": 10,
        },
        "permanent": {
            "id": "permanent",
            "name": "دائمة",
            "kind": "service",
            "unit_cost": 15,
        },
    }
    rows = inherit_required_services(
        line={},
        product_links=[
            {"resource_id": "ordinary", "quantity": 1},
            {
                "resource_id": "permanent",
                "quantity": 1,
                "supplier_invoice_required": True,
            },
        ],
        option_bindings=[],
        resources_by_id=resources,
    )
    by_id = {row["service_id"]: row for row in rows}
    assert by_id["ordinary"]["source"] == "product"
    assert by_id["ordinary"]["supplier_invoice_required"] is False
    assert by_id["permanent"]["source"] == "supplier_receiving_permanent"
    assert by_id["permanent"]["supplier_invoice_required"] is True


def test_permanent_snapshot_is_pending_and_invoice_required() -> None:
    row = _permanent_supplier_service_snapshot(
        {
            "id": "service-1",
            "name": "تطريز",
            "kind": "service",
            "unit_cost": 12.5,
        }
    )
    assert row["source"] == "supplier_receiving_permanent"
    assert row["supplier_invoice_required"] is True
    assert row["customer_selected"] is False
    assert row["status"] == "pending"



def test_customer_selected_service_does_not_require_supplier_assignment() -> None:
    rows = supplier_piece_invoice_services(
        {
            "services": [{
                "service_id": "option",
                "service_name": "خيار العميل",
                "source": "option",
                "customer_selected": True,
                "status": "pending",
                "required_quantity": 1,
            }]
        },
        {"supplier_snapshot": {"id": "supplier-1", "service_links": []}},
        {
            "option": {
                "id": "option",
                "name": "خيار العميل",
                "unit": "job",
                "unit_cost": 10,
            }
        },
    )
    assert [row["service_id"] for row in rows] == ["option"]
    assert rows[0]["reference_unit_price_halalas"] == 1000


def test_hidden_product_service_remains_pending_when_not_invoiced() -> None:
    completed_at = datetime.now(timezone.utc)
    update = supplier_service_completion_update(
        piece={
            "services": [{
                "service_id": "ordinary",
                "service_name": "خدمة تشغيلية",
                "source": "product",
                "status": "pending",
                "required_quantity": 1,
                "completed_quantity": 0,
            }]
        },
        invoice_line={"services": []},
        session={
            "id": "session-1",
            "reference": "SR-1",
            "supplier_snapshot": {
                "id": "supplier-1",
                "company_name": "مورد",
            },
        },
        actor={"id": "employee-1", "name": "موظف"},
        invoice_id="invoice-1",
        completed_at=completed_at,
    )
    assert [row["service_id"] for row in update["$set"]["services"]] == [
        "ordinary"
    ]
    assert update["$set"]["remaining_service_count"] == 1
    assert update["$set"]["status"] == "in_progress"
    assert update["$set"]["execution_status"] == "awaiting_remaining_services"


def test_permanent_service_selector_excludes_invoiced_and_received_pieces() -> None:
    selector = supplier_uninvoiced_piece_selector(
        merchant_id="merchant-1",
        product_identifiers=["product-1"],
        service_id="service-1",
    )
    assert selector["status"]["$nin"] == ["cancelled", "received"]
    history_clause = selector["$and"][0]["$or"]
    assert {"supplier_receiving_history": {"$exists": False}} in history_clause
    assert {"supplier_receiving_history": []} in history_clause
    assert selector["$and"][1]["services"]["$not"]["$elemMatch"] == {
        "service_id": "service-1"
    }



def test_invoice_close_cannot_bypass_permanent_service_endpoint() -> None:
    with pytest.raises(HTTPException) as captured:
        build_supplier_receiving_invoice(
            session={"reference": "SR-1", "supplier_snapshot": {"service_links": []}},
            scans=[{
                "piece_id": "piece-1",
                "product_id": "product-1",
                "product_name": "منتج",
                "sku": "SKU-1",
                "product_charge_eligible": True,
                "reference_product_unit_price_halalas": 5000,
                "invoice_services": [],
            }],
            requested_lines=[SupplierReceivingInvoiceLineRequest(
                piece_ids=["piece-1"],
                product_unit_price_halalas=5000,
                services=[SupplierReceivingInvoiceServiceRequest(
                    service_id="new-service",
                    unit_price_halalas=1000,
                    add_to_product=True,
                )],
            )],
            saved_at=datetime.now(timezone.utc),
            permissions={ADD_PRODUCT_SERVICE_PERMISSION},
            service_catalog={
                "new-service": {
                    "id": "new-service",
                    "name": "خدمة جديدة",
                    "unit": "job",
                    "unit_cost": 10,
                }
            },
        )
    assert captured.value.status_code == 409
    assert captured.value.detail["code"] == (
        "supplier_receiving_service_add_via_product_required"
    )
