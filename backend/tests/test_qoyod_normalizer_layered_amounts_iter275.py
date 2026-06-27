"""Iter-275 — Normalizer accepts Salla's layered `amounts` per-item shape.

The user discovered that Make.com's Array Aggregator cannot synthesise
new fields like `unit_price` / `tax_amount`. The natural shape emitted
by Make (passing Salla's bundle straight through) is:

    {
      "sku": "AMS13000",
      "name": "عباية جنان",
      "quantity": 1,
      "amounts": {
        "price_without_tax": { "amount": 180, "currency": "SAR" },
        "tax":               { "amount": { "amount": 12.86, "currency": "SAR" } },
        "total":             { "amount": 173.6, "currency": "SAR" }
      }
    }

The normalizer must accept this directly with the priority chain:
  unit_price: it.unit_price → it.price.amount → it.amounts.price_without_tax.amount
  tax_amount: it.tax_amount → it.amounts.tax.amount.amount → 0
  total:      it.total → it.amounts.total.amount → unit_price * quantity
  currency:   it.price.currency → it.amounts.price_without_tax.currency → SAR
"""
from __future__ import annotations

import pytest

from integrations.qoyod.normalizer import (
    _money, _normalize_item, _extract_item_currency,
)


# ── User-reported shape — the canonical Make output ─────────────────
def test_normalizes_make_layered_amounts_shape_to_user_expectation():
    raw = {
        "sku": "AMS13000",
        "name": "عباية جنان",
        "quantity": 1,
        "amounts": {
            "price_without_tax": {"amount": 180, "currency": "SAR"},
            "tax":   {"amount": {"amount": 12.86, "currency": "SAR"}},
            "total": {"amount": 173.6, "currency": "SAR"},
        },
    }
    dto = _normalize_item(raw)
    assert dto.sku        == "AMS13000"
    assert dto.name       == "عباية جنان"
    assert dto.quantity   == 1.0
    assert dto.unit_price == 180.0
    assert dto.tax_amount == 12.86
    assert dto.total      == 173.6


# ── _money recursion through double-nested {amount: {amount: N}} ────
def test_money_recurses_through_double_nested_amount():
    node = {"amount": {"amount": 12.86, "currency": "SAR"}}
    assert _money(node) == 12.86


def test_money_still_works_for_flat_money_node():
    assert _money({"amount": 100.5, "currency": "SAR"}) == 100.5


def test_money_still_works_for_bare_number():
    assert _money(50) == 50.0
    assert _money("75.50") == 75.5


def test_money_returns_default_when_absent():
    assert _money(None) == 0.0
    assert _money({}, default=99) == 99


# ── Priority chain — unit_price ─────────────────────────────────────
def test_unit_price_priority_1_direct_field_wins():
    raw = {
        "sku": "X", "quantity": 1,
        "unit_price": 50.0,
        "price": {"amount": 999, "currency": "SAR"},
        "amounts": {"price_without_tax": {"amount": 777}},
    }
    assert _normalize_item(raw).unit_price == 50.0


def test_unit_price_priority_2_price_amount_wins_over_amounts():
    raw = {
        "sku": "X", "quantity": 1,
        "price": {"amount": 100, "currency": "SAR"},
        "amounts": {"price_without_tax": {"amount": 777}},
    }
    assert _normalize_item(raw).unit_price == 100.0


def test_unit_price_priority_3_falls_back_to_amounts_price_without_tax():
    raw = {
        "sku": "X", "quantity": 1,
        "amounts": {"price_without_tax": {"amount": 180}},
    }
    assert _normalize_item(raw).unit_price == 180.0


def test_unit_price_zero_when_nothing_provided():
    raw = {"sku": "X", "quantity": 1}
    assert _normalize_item(raw).unit_price == 0.0


# ── Priority chain — tax_amount ─────────────────────────────────────
def test_tax_priority_1_direct_field_wins():
    raw = {
        "sku": "X", "quantity": 1, "unit_price": 100,
        "tax_amount": 7.5,
        "amounts": {"tax": {"amount": {"amount": 99, "currency": "SAR"}}},
    }
    assert _normalize_item(raw).tax_amount == 7.5


def test_tax_priority_2_falls_back_to_double_nested_amounts_tax():
    raw = {
        "sku": "X", "quantity": 1, "unit_price": 100,
        "amounts": {"tax": {"amount": {"amount": 12.86, "currency": "SAR"}}},
    }
    assert _normalize_item(raw).tax_amount == 12.86


def test_tax_priority_3_falls_back_to_zero():
    raw = {"sku": "X", "quantity": 1, "unit_price": 100}
    assert _normalize_item(raw).tax_amount == 0.0


def test_tax_accepts_flat_money_node_too():
    raw = {
        "sku": "X", "quantity": 1, "unit_price": 100,
        "amounts": {"tax": {"amount": 15.0, "currency": "SAR"}},  # not double-nested
    }
    assert _normalize_item(raw).tax_amount == 15.0


# ── Priority chain — total ──────────────────────────────────────────
def test_total_priority_1_direct_field_wins():
    raw = {
        "sku": "X", "quantity": 2, "unit_price": 100,
        "total": 250.0,
        "amounts": {"total": {"amount": 999}},
    }
    assert _normalize_item(raw).total == 250.0


def test_total_priority_2_falls_back_to_amounts_total_amount():
    raw = {
        "sku": "X", "quantity": 1, "unit_price": 180,
        "amounts": {"total": {"amount": 173.6}},
    }
    assert _normalize_item(raw).total == 173.6


def test_total_priority_3_falls_back_to_unit_price_times_quantity():
    raw = {"sku": "X", "quantity": 3, "unit_price": 25.0}
    assert _normalize_item(raw).total == 75.0


# ── Currency extraction ─────────────────────────────────────────────
def test_currency_priority_1_price_currency_wins():
    raw = {"price": {"amount": 100, "currency": "USD"}}
    assert _extract_item_currency(raw, raw.get("amounts") or {}) == "USD"


def test_currency_priority_2_amounts_price_without_tax_currency():
    raw = {"amounts": {"price_without_tax": {"amount": 100, "currency": "EUR"}}}
    assert _extract_item_currency(raw, raw["amounts"]) == "EUR"


def test_currency_priority_3_falls_back_to_sar():
    assert _extract_item_currency({"sku": "X"}, {}) == "SAR"


# ── Backward compatibility — Mezan canonical shape still parses ─────
def test_canonical_shape_still_normalizes_identically():
    raw = {
        "sku": "A", "name": "بند", "quantity": 2,
        "unit_price": 50.0, "tax_amount": 15.0, "total": 115.0,
    }
    dto = _normalize_item(raw)
    assert dto.unit_price == 50.0
    assert dto.tax_amount == 15.0
    assert dto.total      == 115.0


# ── String-typed numbers (Make tends to stringify) ──────────────────
def test_string_typed_numbers_are_coerced_in_layered_shape():
    raw = {
        "sku": "X", "name": "y", "quantity": "1",
        "amounts": {
            "price_without_tax": {"amount": "180", "currency": "SAR"},
            "tax":   {"amount": {"amount": "12.86"}},
            "total": {"amount": "173.6"},
        },
    }
    dto = _normalize_item(raw)
    assert dto.quantity   == 1.0
    assert dto.unit_price == 180.0
    assert dto.tax_amount == 12.86
    assert dto.total      == 173.6


# ── Hostile-shape robustness ────────────────────────────────────────
def test_non_dict_item_raises_normalization_error():
    from integrations.qoyod.normalizer import NormalizationError
    with pytest.raises(NormalizationError):
        _normalize_item("not-a-dict")


def test_empty_amounts_does_not_crash():
    raw = {"sku": "X", "quantity": 1, "amounts": {}}
    dto = _normalize_item(raw)
    assert dto.unit_price == 0.0
    assert dto.tax_amount == 0.0
    assert dto.total      == 0.0     # quantity*0 = 0


# ── Real Salla bundle (multi-item) goes through cleanly ─────────────
def test_multi_item_layered_amounts_normalizes_consistently():
    """Two items, both in layered shape; ensure both get parsed and
    item_sums add up so the Totals Guard would pass."""
    items = [
        {
            "sku": "A", "name": "بند 1", "quantity": 1,
            "amounts": {
                "price_without_tax": {"amount": 100, "currency": "SAR"},
                "tax":   {"amount": {"amount": 15, "currency": "SAR"}},
                "total": {"amount": 115, "currency": "SAR"},
            },
        },
        {
            "sku": "B", "name": "بند 2", "quantity": 2,
            "amounts": {
                "price_without_tax": {"amount": 50, "currency": "SAR"},
                "tax":   {"amount": {"amount": 15, "currency": "SAR"}},
                "total": {"amount": 115, "currency": "SAR"},
            },
        },
    ]
    dtos = [_normalize_item(it) for it in items]
    assert dtos[0].unit_price == 100 and dtos[0].tax_amount == 15
    assert dtos[1].unit_price == 50  and dtos[1].tax_amount == 15
    # items_sum_excl = 100 + 100 = 200 — what the Totals Guard would see.
    items_sum_excl = sum(d.unit_price * d.quantity for d in dtos)
    assert items_sum_excl == 200.0
