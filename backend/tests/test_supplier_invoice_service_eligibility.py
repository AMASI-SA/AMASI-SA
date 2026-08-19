from __future__ import annotations

from preparation_piece_operations import inherit_required_services
from supplier_receiving_routes import (
    _invoice_group_key,
    _permanent_supplier_service_snapshot,
    supplier_piece_invoice_services,
    supplier_piece_service_blocker,
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
