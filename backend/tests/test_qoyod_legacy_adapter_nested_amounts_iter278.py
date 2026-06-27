"""Iter-278 — legacy_adapter must propagate Salla's nested item amounts.

Smoking gun from production order 268632361 (AMS11980):
  Raw payload had `items[0].amounts.price_without_tax.amount = 199`,
  `amounts.total_discount.amount = 11.94`, `amounts.tax.amount.amount = 14.96`,
  `amounts.total.amount = 202.02`.

But the canonical DTO showed:
  unit_price=0, tax_amount=0, discount_amount=0, total=202.02

Forensics revealed the bug was in `_adapt_item` (legacy adapter, NOT
the normalizer). The adapter looked only at top-level `raw.price`
and one-level-deep `amounts.tax.amount`. Since Salla 2026 webhooks
ship items without a top-level `price` field and with double-nested
tax, the adapter emitted `price_without_tax: null` and a wrong tax
amount. The normalizer then dutifully reported zeros.

These tests lock in the fix:
  • Adapter extracts unit_price from `amounts.price_without_tax.amount`
  • Adapter recurses through `amounts.tax.amount.amount`
  • Adapter surfaces `amounts.total_discount` (new — was dropped entirely)
  • Adapter still works for legacy flat-price payloads (back-compat)
"""
from __future__ import annotations

from integrations.qoyod.legacy_adapter import (
    _adapt_item, _extract_money_value, adapt,
)
from integrations.qoyod.normalizer import _normalize_item


# ─── Real production payload — order 268632361 / AMS11980 ───────────
RAW_268632361_ITEM = {
    "sku": "AMS11980",
    "name": "عباية ستيتش بناتي - تصميم أنيق مع طرحة",
    "quantity": 1,
    "amounts": {
        "original_price":    {"amount": 199,   "currency": "SAR"},
        "price_without_tax": {"amount": 199,   "currency": "SAR"},
        "total_discount":    {"amount": 11.94, "currency": "SAR"},
        "tax": {
            "percent": "8.00",
            "amount":  {"amount": 14.96, "currency": "SAR"},
        },
        "total": {"amount": 202.02, "currency": "SAR"},
    },
}


def test_adapter_extracts_unit_price_from_nested_amounts():
    out = _adapt_item(RAW_268632361_ITEM, "SAR")
    assert out is not None
    pwt = out["amounts"]["price_without_tax"]
    assert pwt is not None, \
        "adapter must produce a non-null price_without_tax node"
    assert pwt["amount"] == 199.0
    assert pwt["currency"] == "SAR"


def test_adapter_recurses_through_double_nested_tax_amount():
    out = _adapt_item(RAW_268632361_ITEM, "SAR")
    tax = out["amounts"]["tax"]
    assert tax is not None, "adapter must emit tax node"
    assert tax["amount"] == 14.96, \
        "adapter must recurse through {percent, amount: {amount}}"


def test_adapter_surfaces_total_discount_column():
    out = _adapt_item(RAW_268632361_ITEM, "SAR")
    disc = out["amounts"].get("total_discount")
    assert disc is not None, \
        "Iter-276 column `total_discount` must propagate, not be dropped"
    assert disc["amount"] == 11.94


def test_adapter_preserves_total_amount():
    out = _adapt_item(RAW_268632361_ITEM, "SAR")
    assert out["amounts"]["total"]["amount"] == 202.02


# ─── End-to-end through normalizer ──────────────────────────────────
def test_adapter_then_normalizer_produces_correct_canonical_dto():
    """The full webhook → adapter → normalizer chain for the real
    production payload must produce the canonical DTO the operator
    expects:
        unit_price       = 199
        tax_amount       = 14.96
        discount_amount  = 11.94
        total            = 202.02
    Line math: 199 − 11.94 + 14.96 = 202.02 ✓
    """
    adapted = _adapt_item(RAW_268632361_ITEM, "SAR")
    dto = _normalize_item(adapted)
    assert dto.sku             == "AMS11980"
    assert dto.unit_price      == 199.0,  f"got {dto.unit_price}"
    assert dto.tax_amount      == 14.96,  f"got {dto.tax_amount}"
    assert dto.discount_amount == 11.94,  f"got {dto.discount_amount}"
    assert dto.total           == 202.02, f"got {dto.total}"
    # Line math reconciles.
    derived = (dto.unit_price * dto.quantity
               - dto.discount_amount + dto.tax_amount)
    assert round(derived, 2) == 202.02


# ─── Full webhook body → adapter → normalizer ───────────────────────
def test_full_make_webhook_for_order_268632361_produces_correct_dto():
    """The complete Make.com body shape — adapt() must hand off a
    payload whose items[0] survives _normalize_item with correct
    numerical fields."""
    raw_body = {
        "tax": 0,
        "items": [RAW_268632361_ITEM],
        "currency": "SAR",
        "order_id": "536444300",
        "subtotal": 199,
        "created_at": "2026-06-27 01:09:26.000000",
        "event_type": "order_completed",
        "completed_at": "2026-06-27 20:10:45",
        "order_number": "268632361",
        "order_status": "تم التنفيذ",
        "total_amount": 228.02,
        "customer_name": "محمد العتيبي",
        "received_from": "make",
        "shipping_cost": 24.07,
        "payment_method": "tamara_installment",
        "customer_mobile": "505589357",
        "order_status_slug": "completed",
    }
    adapted, meta = adapt(raw_body)
    assert meta["adapter_applied"] is True
    assert meta["items_source"] == "items"

    # Drill into the adapted canonical Salla shape: should have
    # `data.items` (or similar). Let's locate items.
    items = (adapted.get("data") or {}).get("items") if isinstance(
        adapted.get("data"), dict) else adapted.get("items")
    assert items is not None and len(items) == 1
    it = items[0]
    assert it["amounts"]["price_without_tax"]["amount"] == 199.0
    assert it["amounts"]["tax"]["amount"] == 14.96
    assert it["amounts"]["total_discount"]["amount"] == 11.94
    assert it["amounts"]["total"]["amount"] == 202.02


# ─── Backward-compat: legacy flat-price payload still works ─────────
def test_legacy_flat_price_payload_still_adapts_cleanly():
    """Old-style Make scenarios that DO send a top-level `price` must
    not break. Coverage to prevent regression of the existing path."""
    raw = {
        "sku": "OLD-SKU", "name": "بند قديم",
        "quantity": 2,
        "price": {"amount": 50.0, "currency": "SAR"},
        "tax": {"amount": 7.5},
    }
    out = _adapt_item(raw, "SAR")
    assert out["amounts"]["price_without_tax"]["amount"] == 50.0
    assert out["amounts"]["tax"]["amount"] == 7.5
    # total computed: 50 * 2 = 100
    assert out["amounts"]["total"]["amount"] == 100.0


def test_legacy_flat_unit_price_field_is_honored():
    raw = {"sku": "X", "name": "y", "quantity": 1, "unit_price": 12.5}
    out = _adapt_item(raw, "SAR")
    assert out["amounts"]["price_without_tax"]["amount"] == 12.5


# ─── _extract_money_value direct tests ──────────────────────────────
def test_extract_money_value_handles_flat_money_node():
    assert _extract_money_value({"amount": 50, "currency": "SAR"}) == 50.0


def test_extract_money_value_recurses_through_double_nested():
    assert _extract_money_value(
        {"amount": {"amount": 14.96, "currency": "SAR"}, "percent": "8.00"}
    ) == 14.96


def test_extract_money_value_handles_bare_number():
    assert _extract_money_value(99) == 99.0
    assert _extract_money_value("12.5") == 12.5


def test_extract_money_value_returns_none_for_missing():
    assert _extract_money_value(None) is None
    assert _extract_money_value("") is None
    assert _extract_money_value({}) is None
