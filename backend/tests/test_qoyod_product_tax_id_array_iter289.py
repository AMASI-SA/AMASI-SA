"""Iter-289 — Qoyod /products requires `tax_id` as a JSON array.

Why this test exists
────────────────────
Production order 268756329 failed with:
    POST /products  →  422
    {'errors': {'tax_id': ['Please select taxes']}}

Even though `tax_id` was set in Mezan settings to a valid Qoyod tax id
("15"), Qoyod's product validator runs a `:taxes` has_many check on
the incoming payload — a scalar value fails the check despite being
present. The fix wraps the configured id in a list so Qoyod receives:

    "tax_id": ["15"]

NOT:

    "tax_id": "15"

Regression guard: any future refactor of `_stamp_required_ids` that
reverts to a scalar will trip this test before reaching production.
"""
from __future__ import annotations

from integrations.qoyod.product_resolver import (
    _build_product_payload,
    _build_product_payload_fallback,
)


_SETTINGS = {
    "default_product_category_id":   "CAT-99",
    "default_product_tax_id":        "15",
    "default_product_unit_type_id":  "1",
    "default_sales_account_id":      "17",
}

_ITEM = {"sku": "AMS11961", "name": "تغليف", "unit_price": 5.0}


def test_full_payload_sends_tax_id_as_array():
    body = _build_product_payload(_ITEM, _SETTINGS)["product"]
    assert body["tax_id"] == ["15"], (
        f"tax_id must be an array per Qoyod has_many :taxes validator; "
        f"got {body['tax_id']!r}"
    )
    # The other three required ids stay scalar (belongs_to relationships).
    assert body["category_id"]          == "CAT-99"
    assert body["product_unit_type_id"] == "1"
    assert body["sales_account_id"]     == "17"


def test_fallback_payload_sends_tax_id_as_array():
    body = _build_product_payload_fallback(_ITEM, _SETTINGS)["product"]
    assert body["tax_id"] == ["15"]
    assert body["category_id"]          == "CAT-99"
    assert body["product_unit_type_id"] == "1"
    assert body["sales_account_id"]     == "17"


def test_tax_id_array_contains_exactly_the_configured_value():
    """No silent splitting / no extra ids — exactly one element."""
    body = _build_product_payload(_ITEM, _SETTINGS)["product"]
    assert isinstance(body["tax_id"], list)
    assert len(body["tax_id"]) == 1
    assert body["tax_id"][0] == "15"


def test_missing_tax_setting_omits_field_entirely():
    """When the setting is blank we must NOT send an empty array
    (Qoyod would still reject with 'Please select taxes'). Preflight
    is responsible for refusing the row upstream — the payload builder
    just drops the key cleanly."""
    settings_no_tax = {**_SETTINGS, "default_product_tax_id": "   "}
    body = _build_product_payload(_ITEM, settings_no_tax)["product"]
    assert "tax_id" not in body
