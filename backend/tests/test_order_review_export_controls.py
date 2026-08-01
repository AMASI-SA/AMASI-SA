from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from order_review_export_controls import (
    INTERNAL_PREPARATION_ROUTE,
    SUPPLIER_FILE_ROUTE,
    ReviewExportControlPatch,
    apply_export_control_patch,
    item_export_control_view,
    normalize_spec_keys,
    partition_review_items_for_preparation,
    preparation_assignment_product_key,
    resolve_responsible_employee_id,
    user_can_manage_preparation,
)


def test_normalize_spec_keys_deduplicates_and_canonicalizes_spacing():
    assert normalize_spec_keys([
        " اسحب وافلت الصورة هنا ",
        "اسحب_وافلت_الصورة_هنا",
        "",
    ]) == ["اسحب وافلت الصورة هنا"]


def test_manual_hidden_fields_are_union_with_operational_fields():
    current = {
        "order_item_id": "item-1",
        "supplier_export_excluded_spec_keys": ["الاسم"],
    }
    operational_items = [{
        "source_order_item_id": "item-1",
        "linked_specs": [{"key": "الاسم", "name": "الاسم"}],
    }]
    next_state = apply_export_control_patch(
        current,
        ReviewExportControlPatch(
            manual_hidden_spec_keys=["اسحب وافلت الصورة هنا"],
        ),
        operational_items=operational_items,
        order_item_id="item-1",
        actor_id="owner-1",
    )

    assert next_state["manual_supplier_export_excluded_spec_keys"] == [
        "اسحب وافلت الصورة هنا"
    ]
    assert next_state["supplier_export_excluded_spec_keys"] == [
        "اسحب وافلت الصورة هنا",
        "الاسم",
    ]

    view = item_export_control_view(
        next_state,
        operational_items=operational_items,
        order_item_id="item-1",
    )
    assert view["manual_hidden_spec_keys"] == ["اسحب وافلت الصورة هنا"]
    assert view["operational_hidden_spec_keys"] == ["الاسم"]
    assert view["hidden_spec_keys"] == ["اسحب وافلت الصورة هنا", "الاسم"]


def test_internal_route_never_enters_supplier_file_partition():
    internal = apply_export_control_patch(
        {"order_item_id": "packaging"},
        ReviewExportControlPatch(
            preparation_route=INTERNAL_PREPARATION_ROUTE,
            assigned_employee_id="employee-1",
        ),
        operational_items=[],
        order_item_id="packaging",
        actor_id="owner-1",
    )
    supplier = apply_export_control_patch(
        {"order_item_id": "necklace"},
        ReviewExportControlPatch(preparation_route=SUPPLIER_FILE_ROUTE),
        operational_items=[],
        order_item_id="necklace",
        actor_id="owner-1",
    )

    assert internal["supplier_export"] is False
    assert internal["preparation_status"] == "in_progress"
    assert internal["assigned_employee_id"] == "employee-1"
    assert supplier["supplier_export"] is True
    assert supplier["preparation_status"] == "pending_file"

    partition = partition_review_items_for_preparation([supplier, internal])
    assert [row["order_item_id"] for row in partition["supplier_file_items"]] == [
        "necklace"
    ]
    assert [
        row["order_item_id"]
        for row in partition["internal_preparation_items"]
    ] == ["packaging"]


def test_returning_to_supplier_file_clears_the_order_assignment():
    state = apply_export_control_patch(
        {
            "order_item_id": "packaging",
            "preparation_route": INTERNAL_PREPARATION_ROUTE,
            "assigned_employee_id": "employee-1",
        },
        ReviewExportControlPatch(preparation_route=SUPPLIER_FILE_ROUTE),
        operational_items=[],
        order_item_id="packaging",
        actor_id="owner-1",
    )

    assert state["preparation_route"] == SUPPLIER_FILE_ROUTE
    assert state["assigned_employee_id"] is None


def test_assignment_product_key_is_stable_across_future_orders():
    first = SimpleNamespace(
        order_item_id="order-1:item-1",
        product_id="1008190362",
        parent_product_id=None,
        sku="AMS11889",
        name="قلادة روز",
        source=SimpleNamespace(source_product_id="1008190362"),
    )
    second = SimpleNamespace(
        order_item_id="order-2:item-8",
        product_id="1008190362",
        parent_product_id=None,
        sku="AMS11889",
        name="قلادة روز",
        source=SimpleNamespace(source_product_id="1008190362"),
    )

    assert preparation_assignment_product_key(first) == "product:1008190362"
    assert preparation_assignment_product_key(second) == "product:1008190362"


def test_only_users_with_preparation_manage_are_assignable():
    assert user_can_manage_preparation({"role": "operations"})
    assert user_can_manage_preparation({"role": "admin"})
    assert not user_can_manage_preparation({"role": "accountant"})
    assert user_can_manage_preparation({
        "role": "viewer",
        "extra_permissions": ["preparation.manage"],
    })
    assert not user_can_manage_preparation({
        "role": "operations",
        "denied_permissions": ["preparation.manage"],
    })


def test_internal_route_requires_an_eligible_responsible_employee():
    with pytest.raises(HTTPException) as missing:
        resolve_responsible_employee_id(
            target_route=INTERNAL_PREPARATION_ROUTE,
            payload_employee_id=None,
            current_employee_id=None,
            default_employee_id=None,
            eligible_employee_ids={"employee-1"},
        )
    assert missing.value.status_code == 422
    assert missing.value.detail["code"] == "responsible_employee_required"

    with pytest.raises(HTTPException) as unavailable:
        resolve_responsible_employee_id(
            target_route=INTERNAL_PREPARATION_ROUTE,
            payload_employee_id="employee-disabled",
            current_employee_id=None,
            default_employee_id=None,
            eligible_employee_ids={"employee-1"},
        )
    assert unavailable.value.detail["code"] == "responsible_employee_unavailable"


def test_future_default_is_used_when_the_reviewer_does_not_override_it():
    employee_id = resolve_responsible_employee_id(
        target_route=INTERNAL_PREPARATION_ROUTE,
        payload_employee_id=None,
        current_employee_id=None,
        default_employee_id="employee-1",
        eligible_employee_ids={"employee-1"},
    )
    assert employee_id == "employee-1"

    view = item_export_control_view(
        {"order_item_id": "item-1"},
        operational_items=[],
        order_item_id="item-1",
        product_key="product:100",
        default_assignment={"assigned_employee_id": "employee-1"},
        employees_by_id={
            "employee-1": {
                "id": "employee-1",
                "name": "موظف التغليف",
                "role": "operations",
            },
        },
    )
    assert view["assigned_employee_id"] == "employee-1"
    assert view["assigned_employee_name"] == "موظف التغليف"
    assert view["assignment_source"] == "default"
    assert view["assigned_employee_valid"] is True
