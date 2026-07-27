import pytest
from fastapi import HTTPException

from product_control_center_routes import _clean_patch, _salla_payload


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
