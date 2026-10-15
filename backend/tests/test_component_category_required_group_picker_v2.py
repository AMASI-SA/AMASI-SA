from pathlib import Path

import pytest

from component_category_required_routes import _requires_preparation, _unique_ids
from product_group_link_routes import ProductGroupLinkRequest, _manual_link_value


def test_new_service_requires_preparation_by_default():
    assert _requires_preparation({}, kind="service", current=None) is True
    assert _requires_preparation(
        {"requires_preparation": False},
        kind="service",
        current=None,
    ) is False
    assert _requires_preparation({}, kind="stock_component", current=None) is False


def test_existing_service_keeps_its_saved_preparation_setting():
    assert _requires_preparation(
        {},
        kind="service",
        current={"requires_preparation": False},
    ) is False
    assert _requires_preparation(
        {},
        kind="service",
        current={"requires_preparation": True},
    ) is True


def test_category_and_group_ids_are_deduplicated_in_selected_order():
    assert _unique_ids(["clothes", "plating", "clothes", ""]) == [
        "clothes",
        "plating",
    ]
    assert ProductGroupLinkRequest(
        group_ids=["g2", "g1", "g2"],
    ).group_ids == ["g2", "g1"]


def test_product_group_request_requires_at_least_one_group():
    with pytest.raises(ValueError):
        ProductGroupLinkRequest(group_ids=[])


def test_existing_product_resource_links_are_treated_as_manual():
    assert _manual_link_value({"id": "legacy"}) is True
    assert _manual_link_value({"manual_link": True}) is True
    assert _manual_link_value({"manual_link": False, "group_ids": ["g1"]}) is False
    assert _manual_link_value(None) is False


def test_required_and_group_aware_routers_precede_legacy_routes():
    source = Path(__file__).parents[1] / "order_engine" / "__init__.py"
    text = source.read_text(encoding="utf-8")
    assert text.index("make_component_category_required_router(db, current_user)") < text.index(
        "make_component_workspace_cost_compat_router(db, current_user)"
    )
    assert text.index("make_product_group_link_router(db, current_user)") < text.index(
        "make_product_fulfillment_router(db, current_user)"
    )
