"""Iter-270 — Qoyod product create_payload field-name fix.

Production order 268670571 leaked `DRY:product:e4d875d7` because the
DRY mapping wasn't quarantined (Iter-267 fix shipped). On retry the
resolver tried to CREATE the product fresh — and Qoyod refused with:

    {"base": ["enter at least a purchase price or a sales price to continue."]}

Root cause: `_build_product_payload` sent `selling_price` but the
Qoyod V2 API expects `sale_price`. This test locks in the correct
field name so a future refactor cannot silently re-introduce the bug.
"""
from __future__ import annotations

from integrations.qoyod.product_resolver import _build_product_payload


def test_create_product_payload_uses_qoyod_v2_sale_price_field():
    item = {
        "sku":         "AMS11961",
        "name":        "تغليف انيق مع الورد",
        "unit_price":  5.0,
        "quantity":    1,
        "tax_amount":  0,
        "total":       5,
    }
    settings = {"default_product_type": "service"}

    payload = _build_product_payload(item, settings)

    # The bug: previous version sent `selling_price` which Qoyod
    # silently ignored, leading to the "enter at least a purchase
    # price or a sales price to continue" rejection.
    assert "sale_price" in payload["product"], \
        "Qoyod V2 expects `sale_price`, not `selling_price`"
    assert "selling_price" not in payload["product"], \
        "stale field name `selling_price` must not be sent"
    assert payload["product"]["sale_price"] == 5.0


def test_create_product_payload_coerces_string_price_to_float():
    """Defence in depth — historical canonical payloads may carry
    `unit_price` as a string (early Salla adapter quirk)."""
    item = {"sku": "X", "unit_price": "12.5"}
    payload = _build_product_payload(item, {})
    assert payload["product"]["sale_price"] == 12.5
    assert isinstance(payload["product"]["sale_price"], float)


def test_create_product_payload_handles_missing_price_gracefully():
    """Free gifts / packaging items may legitimately ship with no
    unit_price. We must still build a valid payload (sale_price=0)
    rather than send `None` and trip Qoyod's price validator."""
    item = {"sku": "FREE-GIFT", "name": "هدية مجانية"}
    payload = _build_product_payload(item, {})
    assert payload["product"]["sale_price"] == 0.0


def test_create_product_payload_preserves_name_sku_type_fields():
    """Field-name fix must not regress the rest of the schema."""
    item = {"sku": "K-1", "name": "بند", "unit_price": 99}
    settings = {"default_product_type": "service"}
    payload = _build_product_payload(item, settings)
    p = payload["product"]
    assert p["name"] == "بند"
    assert p["sku"] == "K-1"
    assert p["type"] == "service"
    assert p["is_non_stock"] is True
