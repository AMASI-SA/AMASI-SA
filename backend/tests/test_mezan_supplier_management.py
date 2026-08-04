import inspect
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from mezan_supplier_management_routes import (
    MEZAN_SUPPLIERS_V2,
    MezanSupplierWriteRequest,
    _is_service,
    make_mezan_supplier_management_router,
)


def test_mezan_supplier_directory_is_independent_from_legacy_and_accounting():
    source = inspect.getsource(make_mezan_supplier_management_router)

    assert MEZAN_SUPPLIERS_V2 == "mezan_suppliers_v2"
    assert '"suppliers"' not in source
    assert "counterparties" not in source
    assert "financial_movements" not in source
    assert '"legacy_supplier_data_used": False' in source
    assert '"accounting_linked": False' in source


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


def test_supplier_v2_router_exposes_workspace_create_and_update_only():
    router = make_mezan_supplier_management_router(
        SimpleNamespace(),
        lambda: {"id": "owner-1", "role": "owner"},
    )
    routes = {
        (route.path, method) for route in router.routes for method in route.methods
    }

    assert ("/suppliers-v2/workspace", "GET") in routes
    assert ("/suppliers-v2", "POST") in routes
    assert ("/suppliers-v2/{supplier_id}", "PUT") in routes
    assert not any(method == "DELETE" for _, method in routes)
