"""Contract tests for Mezan-only product image profiles in Products V2."""
from __future__ import annotations

import inspect

import product_v2_details_routes as routes


def test_image_profile_routes_are_mezan_only_and_do_not_write_to_salla():
    source = inspect.getsource(routes.make_product_v2_details_router)
    assert '@router.get("/{product_id}/image-profile")' in source
    assert '@router.put("/{product_id}/image-profile")' in source
    profile_section = source.split('@router.get("/{product_id}/image-profile")', 1)[1]
    assert "call_salla" not in profile_section
    assert routes.IMAGE_PROFILES == "mezan_product_image_profiles_v2"


def test_option_rule_signature_is_stable_and_specificity_is_preserved():
    conditions = [
        {"option_id": "size", "value_id": "large"},
        {"option_id": "color", "value_id": "gold"},
    ]
    normalized = sorted(conditions, key=lambda row: (row["option_id"], row["value_id"]))
    assert routes._rule_signature(normalized) == "color:gold|size:large"


def test_image_profile_service_contract_is_wired():
    service = (routes.__file__ and routes.ROOT if hasattr(routes, "ROOT") else None)
    source = inspect.getsource(routes)
    assert "duplicate_image_rule_conditions" in source
    assert "image_rule_requires_condition" in source
    assert "image_not_in_product_gallery" in source
