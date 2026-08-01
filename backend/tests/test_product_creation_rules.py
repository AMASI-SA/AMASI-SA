import pytest

import product_creation_routes as creation_routes
from product_creation_routes import (
    ProductCreationDraftRequest,
    _apply_salla_inventory_policy,
    build_salla_product_payload,
    normalize_creation_input,
)
from product_fulfillment_routes import ProductOperationProfileRequest
from product_fulfillment_rules import inventory_policy_details


def _request(**overrides):
    values = {
        "name": "خاتم جاهز",
        "sku": "ring-100",
        "price": 250,
        "description": "خاتم جاهز للشحن",
        "product_type": "product",
        "fulfillment_type": "instant",
        "inventory_policy": "branch_stock_required",
    }
    values.update(overrides)
    return ProductCreationDraftRequest(**values)


def test_creation_input_normalizes_sku_and_fulfillment():
    result = normalize_creation_input(_request())
    assert result["sku"] == "RING-100"
    assert result["fulfillment_type"] == "requires_preparation"
    assert result["inventory_policy"] == (
        "finished_goods_inventory_not_tracked"
    )
    assert result["stockout_policy"] == "close_when_out_of_stock"
    assert result["low_stock_threshold"] == 3
    assert "warehouse_id" not in result
    assert "quantity" not in result


def test_legacy_product_warehouse_input_is_ignored_not_stored():
    payload = _request()
    values = payload.model_dump()
    values["warehouse_id"] = "legacy-warehouse"

    result = normalize_creation_input(ProductCreationDraftRequest(**values))

    assert "warehouse_id" not in result


def test_legacy_global_quantity_is_ignored_not_stored():
    values = _request().model_dump()
    values["quantity"] = 12

    result = normalize_creation_input(ProductCreationDraftRequest(**values))

    assert "quantity" not in result


def test_legacy_operation_profile_warehouse_input_is_ignored():
    profile = ProductOperationProfileRequest(
        fulfillment_type="instant",
        inventory_policy="branch_stock_required",
        warehouse_id="legacy-warehouse",
    )

    assert profile.model_dump() == {
        "fulfillment_type": "instant",
        "inventory_policy": "branch_stock_required",
        "stockout_policy": "close_when_out_of_stock",
        "low_stock_threshold": 3,
    }


def test_frozen_creation_payload_is_sellable_and_requires_preparation():
    draft = normalize_creation_input(_request())
    payload = build_salla_product_payload(draft)
    assert payload["name"] == "خاتم جاهز"
    assert payload["sku"] == "RING-100"
    assert payload["status"] == "sale"
    assert payload["product_type"] == "product"
    assert "quantity" not in payload
    assert "unlimited_quantity" not in payload
    assert "id" not in payload
    assert "salla_product_id" not in payload


def test_frozen_creation_ignores_branch_inventory_choice():
    draft = normalize_creation_input(_request(
        fulfillment_type="requires_preparation",
        inventory_policy="branch_stock_required",
    ))
    payload = build_salla_product_payload(draft)
    policy = inventory_policy_details(draft["inventory_policy"])

    assert payload["status"] == "sale"
    assert "quantity" not in payload
    assert policy["requires_branch_inventory"] is False
    assert policy["sell_without_finished_goods_inventory"] is True
    assert policy["unlimited_quantity"] is True


def test_untracked_finished_goods_policy_is_independent_from_fulfillment():
    draft = normalize_creation_input(_request(
        fulfillment_type="requires_preparation",
        inventory_policy="finished_goods_inventory_not_tracked",
    ))
    payload = build_salla_product_payload(draft)
    policy = inventory_policy_details(draft["inventory_policy"])

    assert payload["status"] == "sale"
    assert policy["requires_branch_inventory"] is False
    assert policy["unlimited_quantity"] is True


@pytest.mark.asyncio
async def test_preparation_policy_queues_unlimited_finished_goods(monkeypatch):
    calls = []

    async def fake_call_salla(
        db,
        merchant_id,
        method,
        path,
        **kwargs,
    ):
        calls.append((merchant_id, method, path, kwargs))
        return {"success": True}

    monkeypatch.setattr(creation_routes, "call_salla", fake_call_salla)

    policy = await _apply_salla_inventory_policy(
        object(),
        merchant_id="merchant-1",
        salla_product_id="product-1",
        inventory_policy="finished_goods_inventory_not_tracked",
    )

    assert policy["external_update_queued"] is True
    assert calls == [(
        "merchant-1",
        "POST",
        "/products/quantities/bulk",
        {
            "json": {
                "products": [{
                    "identifer_type": "id",
                    "identifer": "product-1",
                    "quantity": 0,
                    "mode": "overwrite",
                    "unlimited_quantity": True,
                }],
            },
        },
    )]


@pytest.mark.asyncio
async def test_instant_policy_never_invents_global_or_branch_stock(monkeypatch):
    async def unexpected_call(*args, **kwargs):
        raise AssertionError("instant product must not update global quantity")

    monkeypatch.setattr(creation_routes, "call_salla", unexpected_call)

    policy = await _apply_salla_inventory_policy(
        object(),
        merchant_id="merchant-1",
        salla_product_id="product-1",
        inventory_policy="branch_stock_required",
    )

    assert policy["requires_branch_inventory"] is True
    assert policy["external_update_required"] is False


def test_salla_payload_maps_https_images_without_import_fields():
    draft = normalize_creation_input(_request(
        image_urls=["https://cdn.example/ring.jpg"],
        category_ids=[10, 10, 20],
    ))
    payload = build_salla_product_payload(draft)
    assert payload["categories"] == [10, 20]
    assert payload["images"][0]["default"] is True
    assert payload["images"][0]["original"] == "https://cdn.example/ring.jpg"
    assert "bulk_import" not in payload


def test_product_type_is_intentionally_bounded_for_first_release():
    try:
        normalize_creation_input(_request(product_type="service"))
    except ValueError as exc:
        assert str(exc) == "unsupported_product_type"
    else:
        raise AssertionError("unsupported immutable product type was accepted")


def test_image_urls_require_https():
    try:
        normalize_creation_input(_request(
            image_urls=["http://cdn.example/ring.jpg"],
        ))
    except ValueError as exc:
        assert str(exc) == "image_url_must_use_https"
    else:
        raise AssertionError("insecure image URL was accepted")
