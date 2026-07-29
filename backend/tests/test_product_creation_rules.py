from product_creation_routes import (
    ProductCreationDraftRequest,
    build_salla_product_payload,
    normalize_creation_input,
)
from product_fulfillment_routes import ProductOperationProfileRequest


def _request(**overrides):
    values = {
        "name": "خاتم جاهز",
        "sku": "ring-100",
        "price": 250,
        "quantity": 3,
        "description": "خاتم جاهز للشحن",
        "product_type": "product",
        "fulfillment_type": "instant",
    }
    values.update(overrides)
    return ProductCreationDraftRequest(**values)


def test_creation_input_normalizes_sku_and_fulfillment():
    result = normalize_creation_input(_request())
    assert result["sku"] == "RING-100"
    assert result["fulfillment_type"] == "instant"
    assert "warehouse_id" not in result


def test_legacy_product_warehouse_input_is_ignored_not_stored():
    payload = _request()
    values = payload.model_dump()
    values["warehouse_id"] = "legacy-warehouse"

    result = normalize_creation_input(ProductCreationDraftRequest(**values))

    assert "warehouse_id" not in result


def test_legacy_operation_profile_warehouse_input_is_ignored():
    profile = ProductOperationProfileRequest(
        fulfillment_type="instant",
        warehouse_id="legacy-warehouse",
    )

    assert profile.model_dump() == {"fulfillment_type": "instant"}


def test_salla_payload_is_create_only_and_hidden():
    draft = normalize_creation_input(_request())
    payload = build_salla_product_payload(draft)
    assert payload["name"] == "خاتم جاهز"
    assert payload["sku"] == "RING-100"
    assert payload["status"] == "hidden"
    assert payload["product_type"] == "product"
    assert "id" not in payload
    assert "salla_product_id" not in payload


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
