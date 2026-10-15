from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from component_workspace_cost_compat_routes import (
    generated_group_name,
    make_component_workspace_cost_compat_router,
    validate_group_members,
)


def resources():
    return [
        {
            "id": "paint",
            "name": "طلاء",
            "track_inventory": False,
            "category_ids": ["metal"],
        },
        {
            "id": "cut",
            "name": "قص",
            "track_inventory": False,
            "category_ids": ["metal"],
        },
        {
            "id": "engrave",
            "name": "نحت",
            "track_inventory": False,
            "category_ids": ["metal"],
        },
        {
            "id": "bag",
            "name": "كيس",
            "track_inventory": True,
            "category_ids": ["metal", "clothes"],
        },
        {
            "id": "box",
            "name": "علبة",
            "track_inventory": True,
            "category_ids": ["metal", "clothes"],
        },
    ]


def test_group_name_inherits_selected_resource_order():
    assert generated_group_name(
        resources(),
        ["paint", "cut", "engrave"],
    ) == "طلاء - قص - نحت"


def test_shared_components_can_belong_to_multiple_categories():
    selected = validate_group_members(
        resources(),
        resource_ids=["bag", "box"],
        category_id="clothes",
        group_kind="component",
    )
    assert [row["id"] for row in selected] == ["bag", "box"]


def test_group_rejects_less_than_two_unique_members():
    with pytest.raises(HTTPException) as error:
        validate_group_members(
            resources(),
            resource_ids=["bag", "bag"],
            category_id="clothes",
            group_kind="component",
        )
    assert error.value.detail["code"] == "component_group_requires_two_members"


def test_group_rejects_cross_category_or_cross_kind_members():
    with pytest.raises(HTTPException) as category_error:
        validate_group_members(
            resources(),
            resource_ids=["paint", "cut"],
            category_id="clothes",
            group_kind="service",
        )
    assert category_error.value.detail["code"] == "component_group_category_mismatch"

    with pytest.raises(HTTPException) as kind_error:
        validate_group_members(
            resources(),
            resource_ids=["paint", "bag"],
            category_id="metal",
            group_kind="service",
        )
    assert kind_error.value.detail["code"] == "component_group_kind_mismatch"


def test_router_exposes_category_assignment_and_group_contracts():
    router = make_component_workspace_cost_compat_router(
        SimpleNamespace(),
        lambda: {"id": "owner-1"},
    )
    paths = {(route.path, method) for route in router.routes for method in route.methods}
    assert ("/components-v2/workspace", "GET") in paths
    assert ("/components-v2/categories", "POST") in paths
    assert ("/components-v2/{resource_id}/categories", "PUT") in paths
    assert ("/components-v2/groups", "POST") in paths
    assert ("/components-v2/groups/{group_id}", "PUT") in paths
