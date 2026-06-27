"""Iter-273 — Totals Guard.

Production order `268670571` reached PRODUCT_RESOLVED carrying a
single-line canonical payload (sku=AMS11961, unit_price=5) while
the order's own subtotal was 105 and total was 131.60. Make.com had
silently truncated items[] to one row.

These tests lock in the guard that catches this BEFORE any Qoyod
side-effects. The guard must:
  1. Refuse rows where items_sum is much less than subtotal
     (`line_items_incomplete`).
  2. Refuse rows where items_sum mismatches subtotal in any direction
     (`line_items_total_mismatch`).
  3. Refuse rows where the header math (subtotal+tax+ship−disc)
     doesn't equal declared total (`order_total_mismatch`).
  4. Accept clean rows with rounding tolerance ±0.05 SAR.
  5. Accept either tax-EXCLUSIVE or tax-INCLUSIVE item subtotal
     conventions (Salla mostly exclusive, but adapters vary).
"""
from __future__ import annotations

from integrations.qoyod.totals_guard import (
    validate_totals, TotalsGuardResult, DEFAULT_TOLERANCE,
)


# ── 1. Production failure case — order 268670571 ────────────────────
def test_production_order_268670571_is_refused_as_incomplete():
    """Exact shape that slipped through to PRODUCT_RESOLVED before the
    guard existed. items_sum=5 vs subtotal=105 ⇒ Make dropped rows."""
    canonical = {
        "order_number":     "268670571",
        "subtotal":         105.0,
        "shipping_amount":  23.15,
        "tax_amount":       3.45,
        "discount_amount":  0.0,
        "total_amount":     131.60,
        "items": [
            {"sku": "AMS11961", "name": "تغليف انيق معا الورد",
             "quantity": 1, "unit_price": 5.0, "tax_amount": 0,
             "total": 5},
        ],
    }
    result = validate_totals(canonical)
    assert result.ok is False
    assert result.code == "line_items_incomplete"
    assert result.details["items_sum_excl"] == 5.0
    assert result.details["subtotal"] == 105.0
    assert result.details["shortfall"] == 100.0


# ── 2. Happy path — subtotal == items_sum (tax exclusive) ───────────
def test_clean_order_with_matching_totals_passes():
    canonical = {
        "subtotal":         105.0,
        "tax_amount":       3.45,
        "shipping_amount":  23.15,
        "discount_amount":  0.0,
        "total_amount":     131.60,
        "items": [
            {"sku": "A", "quantity": 2, "unit_price": 50.0, "tax_amount": 0},
            {"sku": "B", "quantity": 1, "unit_price": 5.0,  "tax_amount": 0},
        ],
    }
    result = validate_totals(canonical)
    assert result.ok is True, result.message
    assert result.details["items_count"] == 2
    assert result.details["matched_convention"] == "exclusive"


# ── 3. Tax-INCLUSIVE convention also accepted ───────────────────────
def test_tax_inclusive_convention_is_accepted():
    """Some adapters (or Salla variants) report subtotal INCLUSIVE of
    item-level tax. The guard mustn't reject those."""
    canonical = {
        "subtotal":         115.0,    # = items_sum_incl
        "tax_amount":       0.0,      # tax baked into items
        "shipping_amount":  0.0,
        "discount_amount":  0.0,
        "total_amount":     115.0,
        "items": [
            {"sku": "A", "quantity": 1, "unit_price": 100.0,
             "tax_amount": 15.0},
        ],
    }
    result = validate_totals(canonical)
    assert result.ok is True, result.message
    assert result.details["matched_convention"] == "inclusive"


# ── 4. Rounding tolerance — ±0.05 SAR is OK ─────────────────────────
def test_rounding_within_tolerance_passes():
    canonical = {
        "subtotal":         33.34,    # 33.33 rounded → 33.34 due to float
        "tax_amount":       0,
        "shipping_amount":  0,
        "discount_amount":  0,
        "total_amount":     33.33,
        "items": [
            {"sku": "A", "quantity": 3, "unit_price": 11.11},
        ],
    }
    # items_sum = 33.33, subtotal=33.34 → diff = 0.01 < 0.05
    result = validate_totals(canonical)
    assert result.ok is True


def test_rounding_beyond_tolerance_fails():
    canonical = {
        "subtotal":         50.0,
        "tax_amount":       0,
        "shipping_amount":  0,
        "discount_amount":  0,
        "total_amount":     50.0,
        "items": [
            {"sku": "A", "quantity": 1, "unit_price": 49.5},
        ],
    }
    # items_sum = 49.5, subtotal=50 → diff = 0.5 > 0.05
    result = validate_totals(canonical)
    assert result.ok is False
    # 49.5 is more than 50% of 50, so this is "mismatch" not "incomplete".
    assert result.code == "line_items_total_mismatch"


# ── 5. Empty items[] with non-zero subtotal is hard-refuse ──────────
def test_empty_items_with_nonzero_subtotal_is_refused():
    canonical = {
        "subtotal":     100.0,
        "total_amount": 100.0,
        "items":        [],
    }
    result = validate_totals(canonical)
    assert result.ok is False
    assert result.code == "line_items_incomplete"
    assert result.details["items_count"] == 0


# ── 6. Empty items[] with zero subtotal passes (pathological) ───────
def test_empty_items_with_zero_subtotal_passes():
    canonical = {
        "subtotal":     0.0,
        "total_amount": 0.0,
        "items":        [],
    }
    result = validate_totals(canonical)
    assert result.ok is True


# ── 7. Header math check — Iter-282: DOWNGRADED FROM BLOCKER TO WARNING.
# Salla's declared total may legitimately differ from Mezan's expected
# total because Mezan enforces a fixed 15% VAT while Salla may report
# different tax rates per storefront promo config. The mismatch is
# now surfaced via `mezan_vat_diagnostics.tax_difference` (warning)
# but NEVER moves the row to DEAD_LETTER.
def test_order_total_mismatch_is_now_warning_not_blocker():
    """Items match subtotal, declared total diverges from Salla math.
    Pre-Iter-282 this DEAD_LETTERed the order; now it passes and the
    diff is surfaced in mezan_vat_diagnostics."""
    canonical = {
        "subtotal":         100.0,
        "tax_amount":       15.0,
        "shipping_amount":  20.0,
        "discount_amount":  0.0,
        "total_amount":     999.0,    # diverges from Salla math
        "items": [
            {"sku": "A", "quantity": 1, "unit_price": 100.0},
        ],
    }
    result = validate_totals(canonical)
    assert result.ok is True   # No longer a blocker
    diag = result.details["mezan_vat_diagnostics"]
    assert diag["mezan_expected_total"] == 138.0   # 100 + 15 + 20 + 3
    assert diag["salla_declared_total"] == 999.0
    # tax_difference is surfaced for the operator to review.
    assert diag["tax_difference"] == 861.0


# ── 8. Header math reconciles with shipping + discount ──────────────
def test_header_math_reconciles_with_shipping_and_discount():
    canonical = {
        "subtotal":         100.0,
        "tax_amount":       15.0,
        "shipping_amount":  20.0,
        "discount_amount":  10.0,
        "total_amount":     125.0,    # 100 + 15 + 20 - 10
        "items": [
            {"sku": "A", "quantity": 2, "unit_price": 50.0},
        ],
    }
    result = validate_totals(canonical)
    assert result.ok is True, result.message


# ── 9. Items_sum >> subtotal (caller convention diverged) ───────────
def test_items_sum_substantially_greater_than_subtotal_is_mismatch():
    canonical = {
        "subtotal":     50.0,
        "total_amount": 50.0,
        "items": [
            {"sku": "A", "quantity": 1, "unit_price": 100.0},
        ],
    }
    result = validate_totals(canonical)
    assert result.ok is False
    # items_sum > subtotal → not "incomplete", it's a convention mismatch.
    assert result.code == "line_items_total_mismatch"


# ── 10. String-typed prices are coerced ─────────────────────────────
def test_string_typed_prices_are_coerced_safely():
    canonical = {
        "subtotal":     "100.00",
        "total_amount": "100.00",
        "items": [
            {"sku": "A", "quantity": "1", "unit_price": "100"},
        ],
    }
    result = validate_totals(canonical)
    assert result.ok is True


# ── 11. Tolerance is configurable ───────────────────────────────────
def test_custom_tolerance_can_widen_acceptance():
    canonical = {
        "subtotal":     100.0,
        "total_amount": 100.0,
        "items": [
            {"sku": "A", "quantity": 1, "unit_price": 99.5},
        ],
    }
    # diff = 0.5 — fails default tolerance (0.05) ...
    assert validate_totals(canonical).ok is False
    # ...passes a deliberately widened tolerance (1.00 SAR).
    assert validate_totals(canonical, tolerance=1.0).ok is True


# ── 12. Result.to_log_dict shape is stable ──────────────────────────
def test_result_to_log_dict_shape():
    r = TotalsGuardResult(ok=False, code="x", message="y",
                          details={"k": 1})
    d = r.to_log_dict()
    assert d == {"ok": False, "code": "x", "message": "y",
                 "details": {"k": 1}}


def test_default_tolerance_is_documented():
    assert DEFAULT_TOLERANCE == 0.05
