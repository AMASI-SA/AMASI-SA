"""Regression tests for AMASI Android page-level access permissions."""

import server
from order_status_policy import (
    MOBILE_APP_PERMISSION_LABELS,
    _register_mobile_app_permissions,
)


def test_mobile_app_permissions_are_registered_in_central_catalogue():
    _register_mobile_app_permissions()

    assert set(MOBILE_APP_PERMISSION_LABELS).issubset(server.PERMISSIONS_CATALOGUE)
    assert set(MOBILE_APP_PERMISSION_LABELS).issubset(
        set(server.ROLE_DEFAULT_PERMS["owner"])
    )


def test_mobile_app_permissions_do_not_expand_non_owner_role_defaults():
    _register_mobile_app_permissions()

    app_keys = set(MOBILE_APP_PERMISSION_LABELS)
    for role in ("admin", "accountant", "operations", "viewer"):
        assert app_keys.isdisjoint(set(server.ROLE_DEFAULT_PERMS[role]))


def test_app_pages_configured_marker_and_all_page_keys_are_known():
    _register_mobile_app_permissions()

    expected = {
        "app.pages.configured",
        "app.page.orders",
        "app.page.pending_review",
        "app.page.reviewed_preparation",
        "app.page.my_products",
        "app.page.preparation_receiving",
        "app.page.assembly_shipping",
        "app.page.carrier_handoff",
        "app.page.products",
    }
    assert expected == set(MOBILE_APP_PERMISSION_LABELS)
