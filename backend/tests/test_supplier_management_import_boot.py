"""Regression coverage for supplier router import compatibility."""


def test_supplier_management_wrapper_reexports_audit_and_mobile_router_imports():
    import mezan_supplier_management_routes as routes

    assert callable(routes._audit)

    import supplier_mobile_routes  # noqa: F401
