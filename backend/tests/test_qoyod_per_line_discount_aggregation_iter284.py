"""Iter-284 — Per-line discount aggregation + clearer Make.com error.

User-reported scenario (production, 2026-02-27)
───────────────────────────────────────────────
Order `268756329` (trace `7dbc5a102614484699b976392f6d2e62`) failed
the totals_guard with `line_items_total_mismatch` even though the
order math is internally consistent:

    items (3 lines):
       sku=A  qty=1  unit_price=5    discount=5     tax=0
       sku=B  qty=1  unit_price=199  discount=19.9  tax=14.33
       sku=C  qty=1  unit_price=100  discount=10    tax=7.20

    Σ unit_price × qty       = 304   (= Salla subtotal)
    Σ discount_amount        = 34.9
    Σ items net (post-disc)  = 269.10
    Σ item tax (Salla)       = 21.53
    shipping                  = 0
    expected_total           = 290.63 (= Salla total_amount)

The bug
───────
1. The normalizer emitted `canonical.discount_amount=0` because Salla
   reported the order-level discount as 0 — the per-line discounts
   were ONLY on each item's `amounts.total_discount`. As a result the
   `mezan_vat_diagnostics.net_items_total` and downstream invariants
   couldn't see the discount, even though every item had it.

2. The totals_guard's `line_items_total_mismatch` UI message read as
   "Make.com is dropping items / items[] incomplete", which is
   misleading for orders that have all items present but use
   item-level discounts.

Iter-284 fixes
──────────────
1. `normalizer._aggregate_discount(top, items)` — if the order-level
   discount is 0/missing but items carry their own `discount_amount`,
   the canonical's `discount_amount` is set to `Σ item.discount_amount`.
2. `totals_guard.validate_totals` surfaces `items_discount_sum`,
   `items_tax_sum`, `has_item_level_discounts` and a more accurate
   error code `subtotal_mismatch_with_item_discounts` (Arabic message)
   when the failure happens with multi-SKU + item-level discounts —
   so the UI never blames Make.com for what is actually our
   convention mismatch.
3. New diagnostic `expected_total_salla` + `header_total_diff` +
   `header_total_reconciled` surfaced on EVERY pass result so the
   operator can audit Salla's declared total against the per-line
   breakdown.

Lock-in test for order 268756329
"""
from __future__ import annotations

from integrations.qoyod.totals_guard import validate_totals


def _order_268756329_canonical():
    """Mirrors the canonical DTO for the production order, AFTER
    Iter-284's discount aggregation runs in the normalizer."""
    return {
        "order_number":    "268756329",
        "order_id":        "538555555",
        "currency":        "SAR",
        "subtotal":        304.00,
        "tax_amount":      21.53,
        "shipping_amount": 0.0,
        "discount_amount": 34.9,    # ← Iter-284 aggregation
        "total_amount":    290.63,
        "items": [
            {"sku": "A", "name": "x", "quantity": 1,
             "unit_price": 5.0,   "discount_amount": 5.0,
             "tax_amount": 0.0,   "total": 0.0},
            {"sku": "B", "name": "y", "quantity": 1,
             "unit_price": 199.0, "discount_amount": 19.9,
             "tax_amount": 14.33, "total": 193.43},
            {"sku": "C", "name": "z", "quantity": 1,
             "unit_price": 100.0, "discount_amount": 10.0,
             "tax_amount": 7.20,  "total": 97.20},
        ],
    }


# ─── Headline regression: order 268756329 must pass ─────────────────
def test_order_268756329_totals_guard_passes_after_iter284():
    """The user-reported production failure must now PASS."""
    result = validate_totals(_order_268756329_canonical())
    assert result.ok is True, (
        f"Iter-284 should accept order with per-line discounts; "
        f"got code={result.code} msg={result.message}")


def test_order_268756329_matched_convention_is_gross():
    """Σ unit_price × qty = 5 + 199 + 100 = 304 = subtotal → gross."""
    result = validate_totals(_order_268756329_canonical())
    assert result.details["matched_convention"] == "gross"
    assert result.details["items_sum_gross"] == 304.0


def test_order_268756329_surfaces_items_discount_sum_and_tax_sum():
    result = validate_totals(_order_268756329_canonical())
    d = result.details
    assert d["items_discount_sum"] == 34.9
    assert d["items_tax_sum"]      == 21.53
    assert d["has_item_level_discounts"] is True
    assert d["scanned_sku_count"]  == 3
    assert d["items_count"]        == 3


def test_order_268756329_header_total_reconciles_with_salla_breakdown():
    """Σ (up*q − disc) + Σ tax + shipping = 269.10 + 21.53 + 0 = 290.63
    which equals Salla's declared total. So the new
    `header_total_reconciled` flag must be true."""
    result = validate_totals(_order_268756329_canonical())
    d = result.details
    assert d["expected_total_salla"] == 290.63
    assert d["header_total_diff"] == 0.0
    assert d["header_total_reconciled"] is True


# ─── Clearer error message for multi-SKU + item-discount mismatch ──
def test_subtotal_mismatch_with_item_discounts_uses_specific_code():
    """When items_count > 1 AND scanned_sku_count > 1 AND items have
    item-level discounts, the failure code becomes the more accurate
    `subtotal_mismatch_with_item_discounts` — NOT
    `line_items_total_mismatch` (which implied Make.com dropped items)."""
    canonical = {
        "subtotal":        500.0,     # arbitrarily mismatched
        "tax_amount":      0,
        "shipping_amount": 0,
        "discount_amount": 30.0,
        "total_amount":    300.0,
        "items": [
            {"sku": "A", "quantity": 1, "unit_price": 100.0,
             "discount_amount": 10.0, "tax_amount": 0},
            {"sku": "B", "quantity": 1, "unit_price": 200.0,
             "discount_amount": 20.0, "tax_amount": 0},
        ],
    }
    result = validate_totals(canonical)
    assert result.ok is False
    assert result.code == "subtotal_mismatch_with_item_discounts"
    # The user-facing message is in Arabic.
    assert "خصومات" in result.message
    # Make.com isn't blamed.
    assert "Make.com" not in result.message


def test_single_item_mismatch_still_uses_line_items_total_mismatch():
    """Counter-test: a single-item failure with no item-level discount
    keeps the legacy code — Make.com is the most likely culprit."""
    canonical = {
        "subtotal":        500.0,
        "tax_amount":      0,
        "shipping_amount": 0,
        "discount_amount": 0.0,
        "total_amount":    500.0,
        "items": [{"sku": "X", "quantity": 1, "unit_price": 50.0,
                    "discount_amount": 0, "tax_amount": 0}],
    }
    result = validate_totals(canonical)
    assert result.ok is False
    assert result.code in (
        "line_items_incomplete", "line_items_total_mismatch")


# ─── Normalizer aggregates per-line discounts ───────────────────────
def test_normalizer_aggregates_per_line_discounts_when_top_level_is_zero():
    """When the Salla payload reports `amounts.discounts=0` but each
    item carries its own `amounts.total_discount`, the normalizer
    must aggregate them into `canonical.discount_amount`."""
    from integrations.qoyod.normalizer import normalize
    body = {
        "event": "order.completed",
        "data": {
            "id":           "538555555",
            "reference_id": "268756329",
            "status":       {"slug": "completed", "name": "تم التنفيذ"},
            "currency":     "SAR",
            "amounts": {
                "sub_total": {"amount": 304.0, "currency": "SAR"},
                "tax":       {"amount": 21.53, "currency": "SAR"},
                "shipping":  {"amount": 0.0, "currency": "SAR"},
                "discounts": {"amount": 0.0, "currency": "SAR"},  # ← 0 at root
                "total":     {"amount": 290.63, "currency": "SAR"},
            },
            "items": [
                {"sku": "A", "name": "x", "quantity": 1,
                 "amounts": {
                    "original_price":    {"amount": 5.0,   "currency": "SAR"},
                    "price_without_tax": {"amount": 5.0,   "currency": "SAR"},
                    "total_discount":    {"amount": 5.0,   "currency": "SAR"},
                    "tax":               {"percent": "0",  "amount": {"amount": 0, "currency": "SAR"}},
                    "total":             {"amount": 0.0,   "currency": "SAR"},
                 }},
                {"sku": "B", "name": "y", "quantity": 1,
                 "amounts": {
                    "original_price":    {"amount": 199.0, "currency": "SAR"},
                    "price_without_tax": {"amount": 199.0, "currency": "SAR"},
                    "total_discount":    {"amount": 19.9,  "currency": "SAR"},
                    "tax":               {"percent": "8",  "amount": {"amount": 14.33, "currency": "SAR"}},
                    "total":             {"amount": 193.43, "currency": "SAR"},
                 }},
                {"sku": "C", "name": "z", "quantity": 1,
                 "amounts": {
                    "original_price":    {"amount": 100.0, "currency": "SAR"},
                    "price_without_tax": {"amount": 100.0, "currency": "SAR"},
                    "total_discount":    {"amount": 10.0,  "currency": "SAR"},
                    "tax":               {"percent": "8",  "amount": {"amount": 7.20, "currency": "SAR"}},
                    "total":             {"amount": 97.20, "currency": "SAR"},
                 }},
            ],
        },
    }
    dto = normalize(body)
    # Per-line discounts aggregated to header.
    assert dto.discount_amount == 34.9
    assert dto.subtotal     == 304.0
    assert dto.total_amount == 290.63
    # Counter-check: per-line is preserved.
    assert dto.items[0].discount_amount == 5.0
    assert dto.items[1].discount_amount == 19.9
    assert dto.items[2].discount_amount == 10.0


def test_normalizer_keeps_top_level_discount_when_it_is_set():
    """If Salla DOES set the top-level discount, the normalizer must
    NOT override it (it remains canonical)."""
    from integrations.qoyod.normalizer import normalize
    body = {
        "event": "order.completed",
        "data": {
            "id":   "1", "reference_id": "x",
            "status": {"slug": "completed", "name": "x"},
            "currency": "SAR",
            "amounts": {
                "sub_total": {"amount": 100.0, "currency": "SAR"},
                "tax":       {"amount": 0,     "currency": "SAR"},
                "shipping":  {"amount": 0,     "currency": "SAR"},
                "discounts": {"amount": 25.0,  "currency": "SAR"},   # ← explicit
                "total":     {"amount": 75.0,  "currency": "SAR"},
            },
            "items": [{"sku": "A", "name": "x", "quantity": 1,
                       "amounts": {
                           "original_price":    {"amount": 100, "currency": "SAR"},
                           "price_without_tax": {"amount": 100, "currency": "SAR"},
                           # No total_discount on the line — top-level wins.
                           "tax":               {"percent": "0", "amount": {"amount": 0, "currency": "SAR"}},
                           "total":             {"amount": 100, "currency": "SAR"},
                       }}],
        },
    }
    dto = normalize(body)
    assert dto.discount_amount == 25.0  # ← top-level preserved
