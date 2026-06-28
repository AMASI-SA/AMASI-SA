"""Iter-289 (SUPERSEDED by Iter-290g) — Qoyod /products `tax_id` shape.

History
───────
Iter-289 initially shipped `tax_id: ["15"]` (array) based on an early
2026-02 misread of Qoyod's `:taxes has_many` validator. Live production
evidence on 2026-02-28 (order 268784455 SKU=AMS11542) proved the
opposite — Qoyod's `/products` validator returns
`{'tax_id': ['Please select taxes']}` for an array. The correct shape
is a SCALAR (integer when numeric, string otherwise).

Iter-290g
─────────
Now wraps a regression guard around the *scalar* contract. Any future
refactor that re-introduces an array around `tax_id` will trip this
test before reaching production. The previous Iter-289 array
expectations have been inverted accordingly.
"""
from __future__ import annotations

from integrations.qoyod.product_resolver import (
    _build_product_payload,
    _build_product_payload_fallback,
)


_SETTINGS_NUMERIC = {
    "default_product_category_id":   "1",
    "default_product_tax_id":        "1",
    "default_product_unit_type_id":  "1",
    "default_sales_account_id":      "17",
}

_SETTINGS_STRING_ID = {
    "default_product_category_id":   "CAT-99",
    "default_product_tax_id":        "15",
    "default_product_unit_type_id":  "1",
    "default_sales_account_id":      "17",
}

_ITEM = {"sku": "AMS11961", "name": "تغليف", "unit_price": 5.0}


def test_full_payload_sends_tax_id_as_scalar_int_when_numeric():
    """Iter-290g — production case (order 268784455). All four ids
    coerce to scalar integers."""
    body = _build_product_payload(_ITEM, _SETTINGS_NUMERIC)["product"]
    assert body["tax_id"] == 1, (
        f"tax_id must be a SCALAR int (Iter-290g, not array per "
        f"superseded Iter-289); got {body['tax_id']!r}"
    )
    assert body["category_id"]          == 1
    assert body["product_unit_type_id"] == 1
    assert body["sales_account_id"]     == 17
    assert not isinstance(body["tax_id"], list)


def test_fallback_payload_sends_tax_id_as_scalar_int_when_numeric():
    """Iter-290g — fallback payload follows the same shape contract."""
    body = _build_product_payload_fallback(_ITEM, _SETTINGS_NUMERIC)["product"]
    assert body["tax_id"] == 1
    assert not isinstance(body["tax_id"], list)
    assert body["category_id"]          == 1
    assert body["product_unit_type_id"] == 1
    assert body["sales_account_id"]     == 17


def test_string_id_passes_through_unchanged_as_scalar():
    """Legacy compatibility — when an operator configures a non-numeric
    string id (e.g. test fixtures use 'CAT-99'), we pass it through
    AS A SCALAR STRING. Never wrapped in a list."""
    body = _build_product_payload(_ITEM, _SETTINGS_STRING_ID)["product"]
    assert body["tax_id"] == 15        # "15" → int 15
    assert body["category_id"] == "CAT-99"   # non-numeric → scalar str
    assert not isinstance(body["category_id"], list)


def test_multielement_multiselect_input_collapses_to_first_element():
    """Iter-290g — defends against a Settings UI multiselect delivering
    `["1", "2"]`. The payload builder UNWRAPS to the first usable element.
    A preflight (validate_product_id_shapes) catches the multi-element
    case earlier and refuses the row — this is the belt-and-suspenders
    layer in case the preflight is bypassed."""
    settings = {**_SETTINGS_NUMERIC, "default_product_tax_id": ["1", "2"]}
    body = _build_product_payload(_ITEM, settings)["product"]
    assert body["tax_id"] == 1
    assert not isinstance(body["tax_id"], list)


def test_missing_tax_setting_omits_field_entirely():
    """When the setting is blank we must NOT send an empty value /
    empty array. Preflight is responsible for refusing upstream — the
    payload builder just drops the key cleanly."""
    settings_no_tax = {**_SETTINGS_NUMERIC, "default_product_tax_id": "   "}
    body = _build_product_payload(_ITEM, settings_no_tax)["product"]
    assert "tax_id" not in body


def test_empty_array_treated_as_missing():
    """`tax_id = []` from a UI multiselect with no selection → key
    dropped. (Same outcome as the string-empty case.)"""
    settings_empty_array = {**_SETTINGS_NUMERIC,
                            "default_product_tax_id": []}
    body = _build_product_payload(_ITEM, settings_empty_array)["product"]
    assert "tax_id" not in body
