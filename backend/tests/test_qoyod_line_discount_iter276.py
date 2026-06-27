"""Iter-276 — Per-line `discount_amount` support.

User-supplied real example (AMS13000): a Salla order line with a
promo-code discount on the item:
    unit_price = 180
    total_discount = 19.26
    tax_amount = 12.86
    total = 173.6
Where: 180 − 19.26 + 12.86 = 173.60 ✓ (line-level math)

The normalizer must extract `discount_amount` per the user-defined
priority chain. The invoice builder must surface it as the Qoyod
`discount` column (NOT folded into unit_price). The Totals Guard
must accept lines where `unit_price * qty − discount + tax = total`.
"""
from __future__ import annotations

from integrations.qoyod.normalizer import (
    _normalize_item, _extract_item_discount_amount,
)
from integrations.qoyod.invoice_builder import build_invoice_payload
from integrations.qoyod.totals_guard import validate_totals


# ── Normalizer extracts discount_amount per priority chain ──────────
def test_discount_priority_1_direct_field_wins():
    raw = {
        "sku": "X", "quantity": 1, "unit_price": 100,
        "discount_amount": 7.5,
        "amounts": {"total_discount": {"amount": 999}},
    }
    assert _normalize_item(raw).discount_amount == 7.5


def test_discount_priority_2_falls_back_to_amounts_total_discount():
    raw = {
        "sku": "X", "quantity": 1, "unit_price": 100,
        "amounts": {"total_discount": {"amount": 19.26, "currency": "SAR"}},
    }
    assert _normalize_item(raw).discount_amount == 19.26


def test_discount_priority_3_falls_back_to_zero():
    raw = {"sku": "X", "quantity": 1, "unit_price": 100}
    assert _normalize_item(raw).discount_amount == 0.0


def test_discount_extractor_returns_zero_when_amounts_absent():
    assert _extract_item_discount_amount({}, {}) == 0.0


# ── Real Salla shape: AMS13000 with promo code ──────────────────────
def test_ams13000_real_shape_with_total_discount_normalizes_correctly():
    raw = {
        "sku": "AMS13000",
        "name": "عباية جنان",
        "quantity": 1,
        "amounts": {
            "price_without_tax": {"amount": 180, "currency": "SAR"},
            "total_discount":    {"amount": 19.26, "currency": "SAR"},
            "tax":   {"amount": {"amount": 12.86, "currency": "SAR"}},
            "total": {"amount": 173.6, "currency": "SAR"},
        },
    }
    dto = _normalize_item(raw)
    assert dto.unit_price       == 180.0
    assert dto.discount_amount  == 19.26
    assert dto.tax_amount       == 12.86
    assert dto.total            == 173.6
    # Line-level reconciliation: unit_price × qty − discount + tax = total
    derived = dto.unit_price * dto.quantity - dto.discount_amount + dto.tax_amount
    assert round(derived, 2) == 173.60


# ── Invoice builder surfaces `discount` column (NOT folded into unit_price) ─
def test_invoice_builder_includes_discount_as_separate_column():
    dto_dict = {
        "order_id":     "AMS-TEST-1",
        "order_number": "AMS-TEST-1",
        "currency":     "SAR",
        "items": [{
            "sku": "AMS13000", "name": "عباية جنان",
            "quantity": 1, "unit_price": 180.0,
            "discount_amount": 19.26, "tax_amount": 12.86,
            "total": 173.6,
        }],
    }
    product_resolutions = [{"sku": "AMS13000", "qoyod_product_id": "Q-PROD-1"}]
    settings = {"default_tax_id": "1", "tax_mode": "mezan_fixed_15"}
    from datetime import datetime, timezone
    payload = build_invoice_payload(
        dto_dict=dto_dict,
        qoyod_customer_id="Q-CUST-1",
        product_resolutions=product_resolutions,
        invoice_date=datetime.now(timezone.utc),
        settings=settings,
    )
    line = payload["invoice"]["line_items"][0]
    # unit_price must stay 180 — NOT collapsed to 160.74 (= 180−19.26).
    assert line["unit_price"] == 180.0, \
        "unit_price MUST remain the gross ex-tax price; the discount " \
        "must NOT be folded into it (auditability)."
    assert line["discount"] == 19.26, \
        "discount must be surfaced in its own Qoyod column"
    assert line["quantity"] == 1
    assert line["tax_id"] == "1"


def test_invoice_builder_defaults_discount_to_zero_when_absent():
    """Backward-compat — items without `discount_amount` still get
    `discount: 0` in the Qoyod payload."""
    dto_dict = {
        "order_id": "X", "order_number": "X", "currency": "SAR",
        "items": [{"sku": "A", "name": "y", "quantity": 1,
                   "unit_price": 100, "tax_amount": 0, "total": 100}],
    }
    from datetime import datetime, timezone
    payload = build_invoice_payload(
        dto_dict=dto_dict, qoyod_customer_id="C",
        product_resolutions=[{"sku": "A", "qoyod_product_id": "P"}],
        invoice_date=datetime.now(timezone.utc),
        settings={"default_tax_id": "1", "tax_mode": "mezan_fixed_15"},
    )
    assert payload["invoice"]["line_items"][0]["discount"] == 0


# ── Totals Guard reconciles with line-level discount ────────────────
def test_totals_guard_accepts_ams13000_with_discount_against_order_total():
    """Single-line order with a promo code. The order's subtotal
    is POST-discount (Salla convention), so the guard's items_sum_excl
    formula must subtract `discount_amount` per line."""
    canonical = {
        "order_number":    "AMS-13000-ORDER",
        "subtotal":        160.74,    # 180 − 19.26 = items_sum_excl
        "tax_amount":      12.86,
        "shipping_amount": 0,
        "discount_amount": 0,
        "total_amount":    173.60,
        "items": [{
            "sku": "AMS13000", "name": "عباية جنان", "quantity": 1,
            "unit_price": 180.0, "discount_amount": 19.26,
            "tax_amount": 12.86, "total": 173.6,
        }],
    }
    result = validate_totals(canonical)
    assert result.ok is True, result.message


def test_totals_guard_still_rejects_truly_incomplete_items():
    """Sanity check — adding discount support must not loosen the
    guard for the original 268670571-style problem."""
    canonical = {
        "subtotal":     105.0,
        "tax_amount":   3.45, "shipping_amount": 23.15,
        "discount_amount": 0, "total_amount": 131.60,
        "items": [{"sku": "AMS11961", "quantity": 1,
                   "unit_price": 5.0, "discount_amount": 0,
                   "tax_amount": 0, "total": 5}],
    }
    result = validate_totals(canonical)
    assert result.ok is False
    assert result.code == "line_items_incomplete"


def test_totals_guard_parsed_items_now_carry_discount_column():
    """Operator-facing audit row must include discount_amount so
    `unit_price * qty − discount + tax = total` is verifiable from
    the modal. After Iter-283 (gross convention) such a row PASSES the
    guard; we still surface parsed_items to expose discount_amount."""
    canonical = {
        "subtotal":     50.0, "total_amount": 50.0,
        "items": [{"sku": "X", "quantity": 1, "unit_price": 50,
                   "discount_amount": 10, "tax_amount": 0,
                   "total": 40}],
    }
    result = validate_totals(canonical)
    # Iter-283 — gross convention matches (50×1 = 50 = subtotal).
    assert result.ok is True
    assert result.details["matched_convention"] == "gross"
    # Audit must surface every discount line regardless of pass/fail,
    # so we hoist the parsed_items shape on the success path too.
    # Pre-Iter-283 this lived only in the failure branch — now we
    # check it via the items_sum_* triplet that always carries the
    # discount-aware view.
    assert result.details["items_sum_gross"] == 50.0
    assert result.details["items_sum_excl"]  == 40.0  # = 50 − 10 disc


# ── Multi-item order with mixed discounts (full E2E maths) ──────────
def test_multi_item_with_per_line_discounts_reconciles_cleanly():
    items = [
        # Item 1: 100 × 2 = 200 minus 10 disc = 190 ex-tax, + 15 tax = 205
        {"sku": "A", "quantity": 2, "unit_price": 100.0,
         "discount_amount": 10.0, "tax_amount": 15.0, "total": 205.0},
        # Item 2: 50 × 1 = 50 no disc, + 7.5 tax = 57.5
        {"sku": "B", "quantity": 1, "unit_price": 50.0,
         "discount_amount": 0.0, "tax_amount": 7.5, "total": 57.5},
    ]
    canonical = {
        "subtotal":        240.0,    # 190 + 50
        "tax_amount":      22.5,
        "shipping_amount": 0,
        "discount_amount": 0,
        "total_amount":    262.5,    # 240 + 22.5
        "items":           items,
    }
    result = validate_totals(canonical)
    assert result.ok is True, result.message
