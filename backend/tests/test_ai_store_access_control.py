import pytest

from ai_store_access_control import effective_permissions, validate_assignment


def test_validate_assignment_normalizes_permissions():
    result = validate_assignment({
        "role_key": "product_operator",
        "extra_permissions": ["products.media.ai_edit", "products.media.ai_edit"],
        "denied_permissions": ["products.media.reorder"],
        "enabled": True,
    })
    assert result["role_key"] == "product_operator"
    assert result["extra_permissions"] == ["products.media.ai_edit"]
    assert result["denied_permissions"] == ["products.media.reorder"]


def test_unknown_role_is_rejected():
    with pytest.raises(ValueError, match="invalid_role_key"):
        validate_assignment({"role_key": "super_admin"})


def test_permission_conflict_is_rejected():
    with pytest.raises(ValueError, match="permission_conflict"):
        validate_assignment({
            "role_key": "product_operator",
            "extra_permissions": ["products.media.edit"],
            "denied_permissions": ["products.media.edit"],
        })


def test_effective_permissions_apply_extra_and_denied():
    permissions = effective_permissions({
        "role_key": "product_operator",
        "extra_permissions": ["products.media.ai_edit"],
        "denied_permissions": ["products.media.reorder"],
        "enabled": True,
    })
    assert "products.media.ai_edit" in permissions
    assert "products.media.reorder" not in permissions
    assert "products.read" in permissions


def test_disabled_assignment_has_no_permissions():
    assert effective_permissions({"role_key": "product_manager", "enabled": False}) == []


def test_shipping_assignment_scopes_employee_to_warehouses_and_responsibilities():
    assignment = validate_assignment({
        "role_key": "shipping_operator",
        "warehouse_ids": ["wh-2", "wh-1", "wh-1"],
        "fulfillment_responsibilities": [
            "shipping_labeling",
            "instant_ready",
        ],
    })
    permissions = effective_permissions(assignment)

    assert assignment["warehouse_ids"] == ["wh-1", "wh-2"]
    assert assignment["fulfillment_responsibilities"] == [
        "instant_ready",
        "shipping_labeling",
    ]
    assert "fulfillment.ready.read" in permissions
    assert "fulfillment.labels.print" in permissions
    assert "fulfillment.labels.reprint" not in permissions
