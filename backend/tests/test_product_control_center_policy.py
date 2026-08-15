import pytest
from fastapi import HTTPException

from product_control_center_routes import (
    _clean_patch,
    _salla_payload,
    _salla_payload_with_preserved_prices,
    _verify_salla_prices,
)


def test_cost_fields_are_rejected_from_product_control_center():
    with pytest.raises(HTTPException) as exc:
        _clean_patch({"name": "منتج", "base_cost": 22})
    assert exc.value.detail["code"] == "protected_mezan_cost_fields"
    assert "base_cost" in exc.value.detail["fields"]


def test_content_and_seo_are_publishable_without_costs():
    patch = _clean_patch({
        "name": "سلسال بالاسم",
        "description": "وصف جديد",
        "seo": {"title": "عنوان SEO", "description": "وصف SEO"},
        "local_category": "هدايا مخصصة",
        "google_category": "Apparel & Accessories > Jewelry",
    })
    payload = _salla_payload(patch)
    assert payload["name"] == "سلسال بالاسم"
    assert payload["seo_title"] == "عنوان SEO"
    assert payload["google_product_category"] == "Apparel & Accessories > Jewelry"
    assert "base_cost" not in payload
    assert "local_category" not in payload


def test_unknown_fields_are_not_published():
    assert _clean_patch({"internal_secret": "x", "name": "آمن"}) == {"name": "آمن"}


def test_content_publish_preserves_salla_regular_and_sale_prices():
    payload, expected = _salla_payload_with_preserved_prices(
        {"name": "اسم جديد", "description": "وصف جديد"},
        {
            "price": {"amount": 139, "currency": "SAR"},
            "sale_price": {"amount": 139, "currency": "SAR"},
            "regular_price": {"amount": 180, "currency": "SAR"},
        },
    )
    assert payload["price"] == 180
    assert payload["sale_price"] == 139
    assert expected == {"price": 180, "sale_price": 139}


def test_price_verification_reads_regular_price_not_discounted_display_price():
    _verify_salla_prices(
        {
            "price": {"amount": 139, "currency": "SAR"},
            "sale_price": {"amount": 139, "currency": "SAR"},
            "regular_price": {"amount": 180, "currency": "SAR"},
        },
        {"price": 180, "sale_price": 139},
    )


def test_price_verification_rejects_a_reset_regular_price():
    with pytest.raises(HTTPException) as exc:
        _verify_salla_prices(
            {
                "price": {"amount": 139, "currency": "SAR"},
                "sale_price": {"amount": 139, "currency": "SAR"},
                "regular_price": {"amount": 139, "currency": "SAR"},
            },
            {"price": 180, "sale_price": 139},
        )
    assert exc.value.detail["code"] == "salla_price_verification_failed"
    assert exc.value.detail["mismatches"]["price"] == {"expected": 180, "actual": 139}


def test_content_publish_preserves_tax_inclusive_prices_without_compounding_tax():
    payload, expected = _salla_payload_with_preserved_prices(
        {"name": "اسم جديد"},
        {
            "price": {"amount": 150.12, "currency": "SAR"},
            "sale_price": {"amount": 150.12, "currency": "SAR"},
            "regular_price": {"amount": 194.40, "currency": "SAR"},
            "pre_tax_price": {"amount": 139, "currency": "SAR"},
            "tax": {"amount": 11.12, "currency": "SAR"},
            "with_tax": True,
        },
    )
    assert payload["price"] == pytest.approx(180)
    assert payload["sale_price"] == pytest.approx(139)
    assert expected == {"price": 194.40, "sale_price": 150.12}
