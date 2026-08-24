from order_review_routes import _can_review


def test_native_app_manager_can_review_without_mezan_orders_manage():
    user = {
        "role": "viewer",
        "extra_permissions": [],
        "denied_permissions": [],
        "mobile_app_access": {"permissions": ["app.role.manager"]},
    }
    assert _can_review(user) is True


def test_pending_review_page_can_review_without_mezan_orders_manage():
    user = {
        "role": "viewer",
        "extra_permissions": [],
        "denied_permissions": [],
        "mobile_app_access": {"permissions": ["app.page.pending_review"]},
    }
    assert _can_review(user) is True


def test_reviewed_preparation_page_can_review_without_mezan_orders_manage():
    user = {
        "role": "viewer",
        "extra_permissions": [],
        "denied_permissions": [],
        "mobile_app_permissions": ["app.page.reviewed_preparation"],
    }
    assert _can_review(user) is True


def test_unrelated_mobile_page_does_not_gain_review_mutations():
    user = {
        "role": "viewer",
        "extra_permissions": [],
        "denied_permissions": [],
        "mobile_app_access": {"permissions": ["app.page.products"]},
    }
    assert _can_review(user) is False


def test_legacy_orders_manage_path_remains_supported():
    user = {
        "role": "viewer",
        "extra_permissions": ["orders.manage"],
        "denied_permissions": [],
    }
    assert _can_review(user) is True
