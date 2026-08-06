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
    assert result["workplace_warehouse_id"] is None


def test_validate_assignment_requires_workplace_to_be_assigned():
    with pytest.raises(
        ValueError,
        match="workplace_warehouse_not_assigned",
    ):
        validate_assignment({
            "role_key": "warehouse_operator",
            "warehouse_ids": ["wh-1"],
            "workplace_warehouse_id": "wh-2",
        })


def test_validate_assignment_keeps_employee_workplace():
    assignment = validate_assignment({
        "role_key": "warehouse_operator",
        "warehouse_ids": ["wh-2", "wh-1"],
        "workplace_warehouse_id": "wh-2",
    })

    assert assignment["workplace_warehouse_id"] == "wh-2"


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


def test_warehouse_and_cost_roles_can_receive_purchase_inventory():
    warehouse_permissions = effective_permissions({
        "role_key": "warehouse_operator",
        "enabled": True,
    })
    cost_permissions = effective_permissions({
        "role_key": "cost_manager",
        "enabled": True,
    })

    for permissions in (warehouse_permissions, cost_permissions):
        assert "inventory.receipts.read" in permissions
        assert "inventory.receipts.write" in permissions


def test_stock_preparation_permissions_follow_operational_roles():
    warehouse_permissions = effective_permissions({
        "role_key": "warehouse_operator",
        "enabled": True,
    })

    assert "inventory.preparation.create" in warehouse_permissions
    assert "inventory.preparation.work" in warehouse_permissions
    assert "inventory.preparation.receive" in warehouse_permissions


def test_supplier_invoice_edit_permissions_can_be_granted_individually():
    assignment = validate_assignment({
        "role_key": "warehouse_operator",
        "extra_permissions": [
            "supplier_receiving.product_price.edit",
            "supplier_receiving.service_price.edit",
            "supplier_receiving.service.add",
        ],
    })
    permissions = effective_permissions(assignment)

    assert "supplier_receiving.product_price.edit" in permissions
    assert "supplier_receiving.service_price.edit" in permissions
    assert "supplier_receiving.service.add" in permissions


def test_stock_preparation_responsibility_is_assignable():
    assignment = validate_assignment({
        "role_key": "warehouse_operator",
        "warehouse_ids": ["wh-1"],
        "fulfillment_responsibilities": ["stock_preparation"],
    })

    assert assignment["fulfillment_responsibilities"] == [
        "stock_preparation"
    ]


def test_salla_inventory_sync_is_read_only_for_operational_roles():
    warehouse_permissions = effective_permissions({
        "role_key": "warehouse_operator",
        "enabled": True,
    })
    cost_permissions = effective_permissions({
        "role_key": "cost_manager",
        "enabled": True,
    })
    owner_permissions = effective_permissions({
        "role_key": "owner",
        "enabled": True,
    })

    for permissions in (warehouse_permissions, cost_permissions):
        assert "inventory.salla_sync.read" in permissions
        assert "inventory.salla_sync.manage_mappings" not in permissions
        assert "inventory.salla_sync.publish" not in permissions
    assert "inventory.salla_sync.manage_mappings" in owner_permissions
    assert "inventory.salla_sync.publish" in owner_permissions
