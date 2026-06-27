"""Iter-272 — Qoyod product create: `selling_price` + `is_sold: true` fix.

Production order 268670571 kept failing with:
    {"base": ["enter at least a purchase price or a sales price to continue."]}
even after Iter-270b renamed `selling_price` → `sale_price`.

Root cause (corrected): Qoyod's legacy /products endpoint REQUIRES:
    1. Field name `selling_price` (NOT `sale_price`).
    2. Activation flag `is_sold: true`.
Without `is_sold: true`, Qoyod's validator ignores the price field and
rejects the create with the same "enter at least a price" message.

These tests lock in the corrected payload shape.
"""
from __future__ import annotations

from integrations.qoyod.product_resolver import _build_product_payload


def test_create_product_payload_uses_selling_price_not_sale_price():
    item = {"sku": "AMS11961", "name": "تغليف", "unit_price": 5.0}
    payload = _build_product_payload(item, {"default_product_type": "service"})
    p = payload["product"]
    assert "selling_price" in p, \
        "Qoyod legacy /products uses `selling_price`, not `sale_price`"
    assert "sale_price" not in p, \
        "Iter-270b incorrectly renamed to `sale_price` — must be reverted"
    assert p["selling_price"] == 5.0


def test_create_product_payload_includes_is_sold_activation_flag():
    """Without `is_sold: true`, Qoyod ignores `selling_price` and
    rejects the create with `enter at least a purchase price...`."""
    item = {"sku": "X", "unit_price": 10}
    payload = _build_product_payload(item, {})
    assert payload["product"]["is_sold"] is True, \
        "`is_sold` must be True to activate selling_price validation"


def test_create_product_payload_marks_is_bought_false():
    """Mezan sells, doesn't track purchases. `is_bought: false`
    keeps Qoyod from requiring `buying_price`."""
    payload = _build_product_payload({"sku": "X", "unit_price": 1}, {})
    assert payload["product"]["is_bought"] is False


def test_create_product_payload_coerces_string_price_to_float():
    payload = _build_product_payload({"sku": "X", "unit_price": "12.5"}, {})
    assert payload["product"]["selling_price"] == 12.5
    assert isinstance(payload["product"]["selling_price"], float)


def test_create_product_payload_falls_back_to_zero_when_missing():
    payload = _build_product_payload({"sku": "FREE-GIFT"}, {})
    # Even free items must have the field present for Qoyod to accept the create.
    assert payload["product"]["selling_price"] == 0.0
    assert payload["product"]["is_sold"] is True


def test_create_product_payload_preserves_name_sku_type_fields():
    payload = _build_product_payload(
        {"sku": "K-1", "name": "بند", "unit_price": 99},
        {"default_product_type": "service"},
    )
    p = payload["product"]
    assert p["name"] == "بند"
    assert p["sku"] == "K-1"
    assert p["type"] == "service"
    assert p["is_non_stock"] is True
    # New activation flags don't displace the old ones.
    assert p["is_sold"] is True
    assert p["is_bought"] is False
