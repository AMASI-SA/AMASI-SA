import pytest

import product_creation_routes as creation_routes
from product_creation_routes import (
    ProductCreationDraftRequest,
    _apply_salla_inventory_policy,
    build_salla_product_payload,
    creation_verification_errors,
    normalize_creation_input,
    _serialize_draft,
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
        "weight": 0.25,
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


def test_service_product_type_is_preserved_in_salla_payload():
    draft = normalize_creation_input(_request(product_type="service"))
    payload = build_salla_product_payload(draft)
    assert payload["product_type"] == "service"
    assert payload["require_shipping"] is True
    assert payload["weight"] == 0.25
    assert payload["weight_type"] == "kg"


def test_shippable_service_requires_explicit_weight_before_publish():
    with pytest.raises(ValueError, match="shipping_weight_required"):
        normalize_creation_input(_request(product_type="service", weight=None))


def test_unsupported_product_types_remain_rejected():
    try:
        normalize_creation_input(_request(product_type="digital"))
    except ValueError as exc:
        assert str(exc) == "unsupported_product_type"
    else:
        raise AssertionError("unsupported immutable product type was accepted")


@pytest.mark.parametrize("product_type", ["product", "service"])
def test_creation_carries_customer_text_and_age_choices(product_type):
    ages = [f"{age} سنة" for age in range(1, 11)]
    draft = normalize_creation_input(_request(product_type=product_type, options=[
        {"name": " المقاس ", "type": "choice", "required": True, "values": ages},
        {"name": "الاسم المطلوب", "type": "text", "required": False},
    ]))
    payload = build_salla_product_payload(draft)
    assert draft["options"][0]["name"] == "المقاس"
    # Salla Merchant API uses radio for a single customer choice; select is
    # a frontend control name and is rejected by POST /products with 422.
    assert payload["options"][0]["type"] == "radio"
    assert payload["options"][0]["required"] is True
    assert [value["name"] for value in payload["options"][0]["values"]] == ages
    assert payload["options"][1] == {
        "name": "الاسم المطلوب", "type": "text", "required": False,
        "display_type": "text",
    }


def test_legacy_draft_without_options_still_builds_original_payload():
    draft = normalize_creation_input(_request())
    draft.pop("options", None)
    assert "options" not in build_salla_product_payload(draft)


@pytest.mark.parametrize("frozen", [True, False])
def test_legacy_published_policy_result_can_be_read_and_reconciled(monkeypatch, frozen):
    monkeypatch.setattr(creation_routes, "PRODUCT_OPERATION_CHOICES_FROZEN", frozen)
    draft = normalize_creation_input(_request())
    draft["inventory_policy"] = inventory_policy_details("branch_stock_required")
    assert _serialize_draft(draft)["inventory_policy"] == (
        "finished_goods_inventory_not_tracked" if frozen else "branch_stock_required"
    )
    assert build_salla_product_payload(draft)["status"] == ("sale" if frozen else "out")


@pytest.mark.parametrize("options", [
    [{"name": " ", "type": "text"}],
    [{"name": "المقاس", "type": "choice", "values": ["سنة"]}],
    [{"name": "المقاس", "type": "choice", "values": ["سنة", " سنة "]}],
    [{"name": "الاسم", "type": "text", "values": ["قيمة"]}],
    [{"name": "الاسم", "type": "text"}, {"name": " الاسم ", "type": "text"}],
])
def test_invalid_options_fail_before_external_creation(options):
    with pytest.raises(ValueError):
        normalize_creation_input(_request(options=options))


def test_readback_must_confirm_type_options_values_and_required_flags():
    draft = normalize_creation_input(_request(product_type="service", options=[
        {"name": "المقاس", "type": "choice", "required": True, "values": ["سنة", "سنتان"]},
        {"name": "الاسم", "type": "text"},
    ]))
    raw = {"id": 123, "type": "service", "options": [
        {"id": 1, "name": "المقاس", "type": "select", "required": True,
         "values": [{"id": 10, "name": "سنة"}, {"id": 11, "name": "سنتان"}]},
        {"id": 2, "name": "الاسم", "type": "text", "required": False, "values": []},
    ]}
    assert creation_verification_errors(draft, raw) == []
    assert creation_verification_errors(draft, {**raw, "options": []}) == ["product_options_mismatch"]
    assert "product_type_mismatch" in creation_verification_errors(draft, {**raw, "type": "product"})
    raw["options"][0]["values"].pop()
    assert creation_verification_errors(draft, raw) == ["product_option_mismatch:المقاس"]
    raw["options"][0]["values"].append({"id": 11, "name": "سنتان"})
    raw["options"][0]["required"] = False
    assert creation_verification_errors(draft, raw) == ["product_option_mismatch:المقاس"]


def test_gift_description_and_condition_round_trip_and_deferred_references():
    draft = normalize_creation_input(_request(product_type="service", options=[
        {"name": "هل تريد إضافة كرت إهداء", "type": "choice", "required": True,
         "description": " اختر ", "values": ["نعم", "لا بدون كرت إهداء"]},
        {"name": "كرت الإهداء", "type": "text", "required": True,
         "description": " اكتب اللي تبيه على كرت الإهداء ",
         "condition": {"option_index": 0, "operator": "=", "value": "نعم"}},
    ]))
    payload = build_salla_product_payload(draft)
    assert payload["status"] == "hidden"
    assert payload["quantity"] == 0
    assert payload["unlimited_quantity"] is False
    assert payload["options"][1]["description"] == "اكتب اللي تبيه على كرت الإهداء"
    assert "visibility_condition_option" not in payload["options"][1]
    raw = {"id": 123, "type": "service", "options": [
        {**payload["options"][0], "id": 101, "values": [{"id": 11, "name": "نعم"}, {"id": 12, "name": "لا بدون كرت إهداء"}]},
        {**payload["options"][1], "id": 102, "visibility": "on_condition",
         "visibility_condition_type": "=", "visibility_condition_option": 101, "visibility_condition_value": 11},
    ]}
    assert creation_verification_errors(draft, raw) == []
    raw["options"][1]["visibility_condition_value"] = 12
    assert creation_verification_errors(draft, raw) == ["product_option_condition_mismatch:كرت الإهداء"]
    raw["options"][1]["visibility_condition_value"] = 11
    raw["options"][1]["description"] = ""
    assert creation_verification_errors(draft, raw) == ["product_option_mismatch:كرت الإهداء"]


@pytest.mark.parametrize("condition", [
    {"option_index": 1, "value": "نعم"},
    {"option_index": 9, "value": "نعم"},
    {"option_index": 0, "value": "قيمة غير موجودة"},
])
def test_invalid_condition_references_are_rejected(condition):
    with pytest.raises(ValueError):
        normalize_creation_input(_request(options=[
            {"name": "إهداء", "type": "choice", "values": ["نعم", "لا"]},
            {"name": "النص", "type": "text", "condition": condition},
        ]))


def test_cyclic_conditions_are_rejected_before_salla_calls():
    with pytest.raises(ValueError, match="cyclic_product_option_condition"):
        normalize_creation_input(_request(options=[
            {"name": "أ", "type": "choice", "values": ["نعم", "لا"], "condition": {"option_index": 1, "value": "نعم"}},
            {"name": "ب", "type": "choice", "values": ["نعم", "لا"], "condition": {"option_index": 0, "value": "نعم"}},
        ]))


def test_image_urls_require_https():
    try:
        normalize_creation_input(_request(
            image_urls=["http://cdn.example/ring.jpg"],
        ))
    except ValueError as exc:
        assert str(exc) == "image_url_must_use_https"
    else:
        raise AssertionError("insecure image URL was accepted")
