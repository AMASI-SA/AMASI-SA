import inspect
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from mezan_supplier_management_routes import (
    MEZAN_SUPPLIER_INVOICES_V2,
    MEZAN_SUPPLIERS_V2,
    MezanSupplierWriteRequest,
    _halalas_from_riyals,
    _is_service,
    _public_financial_invoice,
    make_mezan_supplier_management_router,
)


def test_mezan_supplier_directory_stays_legacy_free_and_supports_v2_accounting():
    source = inspect.getsource(make_mezan_supplier_management_router)

    assert MEZAN_SUPPLIERS_V2 == "mezan_suppliers_v2"
    assert MEZAN_SUPPLIER_INVOICES_V2 == "mezan_supplier_invoices_v2"
    assert 'db["suppliers"]' not in source
    assert "db['suppliers']" not in source
    assert "counterparties" not in source
    assert "financial_movements" not in source
    assert '"legacy_supplier_data_used": False' in source
    assert '"accounting_linked": True' in source


def test_supplier_requires_at_least_one_existing_service_identifier():
    valid = MezanSupplierWriteRequest(
        company_name="مورد أماسي",
        service_ids=["engrave", "engrave", "print"],
    )
    assert valid.service_ids == ["engrave", "print"]

    with pytest.raises(ValidationError):
        MezanSupplierWriteRequest(
            company_name="مورد بلا خدمات",
            service_ids=[],
        )


def test_only_non_inventory_service_resources_can_bind_to_supplier():
    assert _is_service({"kind": "service", "track_inventory": False}) is True
    assert _is_service({"kind": "stock_component", "track_inventory": True}) is False
    assert _is_service({"kind": "service", "track_inventory": True}) is False


def test_supplier_v2_router_exposes_workspace_financials_create_and_update_only():
    router = make_mezan_supplier_management_router(
        SimpleNamespace(),
        lambda: {"id": "owner-1", "role": "owner"},
    )
    routes = {
        (route.path, method) for route in router.routes for method in route.methods
    }

    assert ("/suppliers-v2/workspace", "GET") in routes
    assert ("/suppliers-v2/financials", "GET") in routes
    assert ("/suppliers-v2", "POST") in routes
    assert ("/suppliers-v2/{supplier_id}", "PUT") in routes
    assert not any(method == "DELETE" for _, method in routes)


def test_supplier_financial_invoice_keeps_experiment_and_line_detail_explicit():
    invoice = _public_financial_invoice(
        {
            "id": "inv-test-1",
            "supplier_id": "supplier-v2-1",
            "invoice_number": "SI-TEST-001",
            "status": "experiment_completed",
            "experiment_mode": True,
            "total_halalas": 11_000,
            "liability_created": False,
            "financial_invoice_created": False,
            "lines": [
                {
                    "line_number": 1,
                    "product_name": "منتج تجريبي",
                    "quantity": 2,
                    "total_halalas": 11_000,
                    "services": [
                        {
                            "service_id": "embroidery",
                            "service_name": "تطريز",
                            "total_quantity": 1,
                            "unit_price_halalas": 1_000,
                            "total_halalas": 1_000,
                        }
                    ],
                }
            ],
        }
    )

    assert invoice["supplier_id"] == "supplier-v2-1"
    assert invoice["experiment_mode"] is True
    assert invoice["liability_created"] is False
    assert invoice["financial_invoice_created"] is False
    assert invoice["lines"][0]["services"][0]["service_name"] == "تطريز"
    assert invoice["lines"][0]["services"][0]["total_halalas"] == 1_000


@pytest.mark.parametrize(
    ("riyals", "halalas"),
    [(0, 0), (110, 11_000), ("10.25", 1_025), (None, 0)],
)
def test_supplier_financials_convert_ledger_riyals_to_halalas(riyals, halalas):
    assert _halalas_from_riyals(riyals) == halalas
