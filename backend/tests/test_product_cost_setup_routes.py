from __future__ import annotations

from types import SimpleNamespace

import pytest

import product_cost_setup_routes as module
from product_cost_setup_routes import (
    ProductCostCompletionRequest,
    ProductCostCategoryRequest,
    _view,
    make_product_cost_setup_router,
)


def test_cost_setup_requests_keep_cost_and_resources_optional() -> None:
    assert ProductCostCompletionRequest().complete is True
    assert ProductCostCompletionRequest(complete=False).complete is False
    assert ProductCostCategoryRequest(category_id="clothes").category_id == "clothes"


@pytest.mark.asyncio
async def test_operations_view_exposes_one_product_category_and_manual_completion(monkeypatch):
    async def fake_extended(_db, *, user_id, product):
        assert user_id == "owner-1"
        return {
            "product": {"mezan_product_id": product["mezan_product_id"]},
            "categories": [
                {"id": "clothes", "name": "ملابس", "status": "active"},
                {"id": "plated", "name": "مطليات", "status": "active"},
            ],
            "resources": [],
            "groups": [],
            "rules": {},
        }

    monkeypatch.setattr(module, "_extended_operations_view", fake_extended)
    result = await _view(
        SimpleNamespace(),
        user_id="owner-1",
        product={
            "mezan_product_id": "product-1",
            "cost_category_id": "clothes",
            "cost_setup_complete": True,
            "cost_setup_completed_by": "employee-1",
        },
    )

    assert result["product"]["cost_category_id"] == "clothes"
    assert result["product"]["cost_category_name"] == "ملابس"
    assert result["product"]["cost_setup_complete"] is True
    assert result["cost_setup"]["base_cost_required"] is False
    assert result["cost_setup"]["components_required"] is False
    assert result["cost_setup"]["services_required"] is False
    assert result["cost_setup"]["completion_is_explicit"] is True
    assert result["cost_setup"]["edits_do_not_reopen_completion"] is True


def test_router_exposes_category_completion_and_canonical_operations() -> None:
    router = make_product_cost_setup_router(
        SimpleNamespace(),
        lambda: {"id": "owner-1"},
    )
    keys = {(route.path, method) for route in router.routes for method in route.methods}
    assert ("/products-v2/{product_id}/operations", "GET") in keys
    assert ("/products-v2/{product_id}/cost-category", "PUT") in keys
    assert ("/products-v2/{product_id}/cost-completion", "PUT") in keys
