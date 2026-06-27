"""Iter-283 — Totals Guard Discount Accounting Fix.

User-reported scenario (2026-02-27, production preview)
───────────────────────────────────────────────────────
Order 268632361 (the working trace `33c07a10a2994f6796a44fa386a33c0`)
returned PASS for normalize but FAIL on totals_guard:
    code        = "line_items_total_mismatch"
    items_sum_excl = 187.06
    items_sum_incl = 202.02
    subtotal       = 199.00

Root cause
──────────
Salla's `subtotal` is reported PRE-discount (gross). Pre-Iter-283
the guard only computed `items_sum_excl = Σ(unit_price × qty − disc)`
(POST-discount, pre-tax) and `items_sum_incl = ditto + tax`. For any
order with a discount these will never match Salla's gross subtotal.

Iter-283 fix
────────────
The guard now computes THREE conventions:
  • items_sum_gross  = Σ(unit_price × qty)             (pre-disc, pre-tax)
  • items_sum_excl   = Σ(unit_price × qty − disc)      (post-disc, pre-tax)
  • items_sum_incl   = items_sum_excl + Σ(tax)         (post-disc, with tax)
and accepts whichever matches `subtotal`. Salla default = `gross`.

Lock-in tests
─────────────
The exact values from the production preview that failed must now PASS.
"""
from __future__ import annotations

from integrations.qoyod.totals_guard import validate_totals


# ─── Exact production scenario — order 268632361 ────────────────────
def _order_268632361_canonical():
    return {
        "order_number":    "268632361",
        "order_id":        "536444300",
        "currency":        "SAR",
        "subtotal":        199.00,        # Salla reports GROSS
        "tax_amount":      14.96,
        "shipping_amount": 24.07,
        "discount_amount": 11.94,
        "total_amount":    228.02,
        "items": [{
            "sku":             "AMS11980",
            "name":            "عباية ستيتش بناتي",
            "quantity":        1,
            "unit_price":      199.0,
            "discount_amount": 11.94,
            "tax_amount":      14.96,
            "total":           202.02,
        }],
    }


def test_order_268632361_totals_guard_passes_after_iter283():
    """User-reported regression — must PASS now."""
    result = validate_totals(_order_268632361_canonical())
    assert result.ok is True, (
        f"Iter-283 should accept Salla's GROSS subtotal convention; "
        f"got code={result.code} msg={result.message}")


def test_order_268632361_matched_convention_is_gross():
    result = validate_totals(_order_268632361_canonical())
    assert result.details["matched_convention"] == "gross"


def test_order_268632361_sums_are_surfaced_for_all_three_conventions():
    result = validate_totals(_order_268632361_canonical())
    d = result.details
    assert d["items_sum_gross"] == 199.00    # = 199 × 1
    assert d["items_sum_excl"]  == 187.06    # = 199 − 11.94
    assert d["items_sum_incl"]  == 202.02    # = 187.06 + 14.96
    assert d["subtotal"]        == 199.00


# ─── Counter-tests (Iter-283 does not break the existing conventions) ─
def test_order_with_no_discount_still_matches_gross_convention():
    canonical = {
        "subtotal":        100.0,
        "tax_amount":      15.0,
        "shipping_amount": 0.0,
        "discount_amount": 0.0,
        "total_amount":    115.0,
        "items": [{"sku": "A", "quantity": 1, "unit_price": 100.0,
                    "tax_amount": 15.0, "discount_amount": 0}],
    }
    result = validate_totals(canonical)
    assert result.ok is True
    # All 3 conventions converge when there's no discount/no tax.
    assert result.details["items_sum_gross"] == 100.0
    assert result.details["items_sum_excl"]  == 100.0


def test_subtotal_post_discount_convention_still_accepted():
    """If Make.com flattens subtotal POST-discount, we still accept."""
    canonical = {
        "subtotal":        88.06,         # = 100 − 11.94
        "tax_amount":      14.96,
        "shipping_amount": 0.0,
        "discount_amount": 11.94,
        "total_amount":    103.02,
        "items": [{"sku": "A", "quantity": 1, "unit_price": 100.0,
                    "tax_amount": 14.96, "discount_amount": 11.94}],
    }
    result = validate_totals(canonical)
    assert result.ok is True
    assert result.details["matched_convention"] == "exclusive"


def test_actually_missing_items_still_blocks_after_iter283():
    """Sanity: Iter-283 must not over-relax the guard. Genuinely missing
    items (e.g. Make Array Aggregator emitting only the first row) must
    still hard-refuse."""
    canonical = {
        "subtotal":        500.0,         # claims 500
        "tax_amount":      0.0,
        "shipping_amount": 0.0,
        "discount_amount": 0.0,
        "total_amount":    500.0,
        "items": [{"sku": "X", "quantity": 1, "unit_price": 50.0}],  # only 50
    }
    result = validate_totals(canonical)
    assert result.ok is False
    assert result.code in (
        "line_items_incomplete", "line_items_total_mismatch")


def test_multi_item_order_with_per_line_discount_matches_gross():
    """Two items, each with its own discount. Gross convention must
    still aggregate correctly."""
    canonical = {
        "subtotal":        300.0,         # 100 + 200 GROSS
        "tax_amount":      30.0,
        "shipping_amount": 0.0,
        "discount_amount": 20.0,
        "total_amount":    310.0,
        "items": [
            {"sku": "A", "quantity": 1, "unit_price": 100.0,
             "discount_amount": 5.0,  "tax_amount": 10.0},
            {"sku": "B", "quantity": 2, "unit_price": 100.0,
             "discount_amount": 15.0, "tax_amount": 20.0},
        ],
    }
    # Gross = 100 + 200 = 300 = subtotal ✓
    result = validate_totals(canonical)
    assert result.ok is True
    assert result.details["matched_convention"] == "gross"
    assert result.details["items_sum_gross"] == 300.0


def test_mezan_vat_diagnostics_still_embedded_on_success():
    """Iter-282 contract preserved."""
    result = validate_totals(_order_268632361_canonical())
    assert "mezan_vat_diagnostics" in result.details
    diag = result.details["mezan_vat_diagnostics"]
    # Net items: 199 − 11.94 = 187.06; Mezan tax @ 15% = 28.06
    assert diag["net_items_total"] == 187.06
    assert diag["mezan_items_tax"] == 28.06
