from fastapi import HTTPException
import pytest

from product_control_center_routes import (
    _before_value,
    _clean_patch,
    _salla_payload,
    _salla_payload_with_preserved_prices,
    _verify_salla_prices,
)


def test_salla_cost_price_is_publishable_alias_only():
    patch = _clean_patch({"salla_cost_price": 22.5})
    assert patch == {"salla_cost_price": 22.5}
    assert _salla_payload(patch) == {"cost_price": 22.5}


def test_mezan_cost_fields_remain_protected():
    for field in ("cost_price", "cost_price_from_salla", "base_cost", "unit_cost"):
        with pytest.raises(HTTPException) as exc:
            _clean_patch({field: 99})
        assert exc.value.status_code == 422
        assert exc.value.detail["code"] == "protected_mezan_cost_fields"


def test_cost_only_publish_preserves_existing_regular_and_sale_prices():
    payload, expected = _salla_payload_with_preserved_prices(
        {"salla_cost_price": 31.75},
        {"price": {"amount": 120}, "sale_price": {"amount": 99}, "cost_price": 20},
    )
    assert payload == {"cost_price": 31.75, "price": 120.0, "sale_price": 99.0}
    assert expected == {"price": 120.0, "sale_price": 99.0, "cost_price": 31.75}


def test_cost_verification_checks_salla_cost_without_touching_mezan_cost():
    _verify_salla_prices(
        {"price": 120, "sale_price": 99, "cost_price": 31.75},
        {"price": 120.0, "sale_price": 99.0, "cost_price": 31.75},
    )
    with pytest.raises(HTTPException) as exc:
        _verify_salla_prices(
            {"price": 120, "sale_price": 99, "cost_price": 20},
            {"price": 120.0, "sale_price": 99.0, "cost_price": 31.75},
        )
    assert exc.value.detail["code"] == "salla_price_verification_failed"
    assert "cost_price" in exc.value.detail["mismatches"]


def test_draft_before_value_reads_canonical_salla_cost_snapshot():
    product = {"cost_price_from_salla": 18.25, "price": 100}
    assert _before_value(product, "salla_cost_price") == 18.25
    assert _before_value(product, "price") == 100
