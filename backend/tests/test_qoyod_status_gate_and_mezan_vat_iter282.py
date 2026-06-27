"""Iter-282 — Status Gate Before Totals Guard + Mezan VAT 15% SSOT.

User-reported scenario (2026-02-27)
───────────────────────────────────
Production order `268746039` reached DEAD_LETTER with code
`line_items_total_mismatch` (header math) even though:
  • Its real Salla status was `under_review` (not invoice-eligible).
  • The mismatch was purely a Salla-vs-Mezan tax math divergence
    (Salla reported `tax.percent=8.00`; Mezan owns VAT at 15%).

Iter-282 fixes
──────────────
1. **Ordering fix in pipeline.py**: status eligibility gate
   (`business_rules.evaluate`) now runs BEFORE totals_guard. Orders
   that are not in `invoice_trigger_statuses` go to SKIPPED. Totals
   guard never sees them.
2. **Header math check downgraded** in `totals_guard.py`. Salla's
   `total_amount` may legitimately differ from `subtotal + tax +
   shipping − discount` because Mezan owns the VAT policy. The diff
   is surfaced as `mezan_vat_diagnostics.tax_difference` (warning)
   but NEVER moves the row to DEAD_LETTER.
3. **New `mezan_vat.py` module** is SSOT for the VAT rate (0.15).
   Embeds side-by-side Salla vs Mezan diagnostics in the canonical
   payload so the operator always sees both views.

These tests lock in the contract for order 268746039.
"""
from __future__ import annotations

import pytest

from integrations.qoyod.mezan_vat import (
    VAT_RATE, compute_mezan_totals, expected_line_tax, TAX_SOURCE_LABEL,
)
from integrations.qoyod.totals_guard import validate_totals


# ─── VAT_RATE constant ──────────────────────────────────────────────
def test_vat_rate_is_15_percent():
    assert VAT_RATE == 0.15


def test_tax_source_label_identifies_mezan():
    assert TAX_SOURCE_LABEL == "mezan_fixed_15"


# ─── expected_line_tax helper ───────────────────────────────────────
def test_expected_line_tax_applies_15_percent():
    # 180 × 1 − 10.8 = 169.20 × 0.15 = 25.38
    assert expected_line_tax(180, 1, 10.8) == 25.38


def test_expected_line_tax_zero_when_net_is_zero():
    assert expected_line_tax(0, 1, 0) == 0.0


def test_expected_line_tax_handles_none_gracefully():
    assert expected_line_tax(None, None, None) == 0.0


# ─── compute_mezan_totals — the order 268746039 scenario ────────────
def _order_268746039_canonical():
    """Mirrors the canonical DTO produced by the production pipeline
    for order 268746039 (per user's diagnosis 2026-02-27)."""
    return {
        "order_number": "268746039",
        "order_id":     "538111111",
        "currency":     "SAR",
        "subtotal":     169.20,         # net (post-discount)
        "tax_amount":   13.54,          # Salla's 8% calc (NOT used by Mezan)
        "shipping_amount": 24.07,
        "discount_amount": 10.80,
        "total_amount":  208.74,        # Salla's declared total
        "items": [{
            "sku":             "AMS-test",
            "name":            "بند اختبار",
            "quantity":        1.0,
            "unit_price":      180.0,
            "discount_amount": 10.80,
            "tax_amount":      13.54,    # Salla 8% — Mezan ignores
            "total":           182.74,
        }],
    }


def test_mezan_totals_recomputes_at_15_percent_for_order_268746039():
    canonical = _order_268746039_canonical()
    diag = compute_mezan_totals(canonical)
    # Items net: 180 − 10.80 = 169.20
    assert diag["net_items_total"] == 169.20
    # Items tax @ 15%: 169.20 × 0.15 = 25.38
    assert diag["mezan_items_tax"] == 25.38
    # Shipping tax @ 15%: 24.07 × 0.15 = 3.61 (3.6105 rounded)
    assert diag["mezan_shipping_tax"] == 3.61
    # Expected total: 169.20 + 25.38 + 24.07 + 3.61 = 222.26
    assert diag["mezan_expected_total"] == 222.26
    # Diff Salla 208.74 − Mezan 222.26 = −13.52
    assert diag["tax_difference"] == -13.52
    # Diagnostic label so the UI can colour the badge.
    assert diag["tax_source"] == "mezan_fixed_15"


def test_mezan_totals_surfaces_per_line_breakdown():
    canonical = _order_268746039_canonical()
    diag = compute_mezan_totals(canonical)
    assert len(diag["items"]) == 1
    line = diag["items"][0]
    assert line["sku"]              == "AMS-test"
    assert line["net_line"]         == 169.20
    assert line["mezan_tax_line"]   == 25.38   # 169.20 × 0.15
    assert line["salla_tax_line"]   == 13.54
    # Salla under-computed by 25.38 − 13.54 = 11.84 → diff = 13.54 − 25.38
    assert line["tax_difference_line"] == round(13.54 - 25.38, 2)


def test_mezan_totals_keeps_salla_columns_for_diagnosis():
    """The whole point — Salla columns kept for forensics."""
    canonical = _order_268746039_canonical()
    diag = compute_mezan_totals(canonical)
    assert diag["salla_items_tax"]       == 13.54
    assert diag["salla_declared_total"]  == 208.74
    assert diag["salla_items_tax_amount"] == 13.54


# ─── totals_guard embeds the diagnostics ────────────────────────────
def test_totals_guard_passes_for_order_268746039_despite_tax_diff():
    """Iter-282: Salla math diverges from Mezan VAT, but items_sum
    matches subtotal — guard MUST pass. Operator sees the tax diff
    via `mezan_vat_diagnostics`."""
    canonical = _order_268746039_canonical()
    result = validate_totals(canonical)
    assert result.ok is True, result.message
    diag = result.details["mezan_vat_diagnostics"]
    assert diag["vat_rate"] == 0.15
    assert diag["mezan_expected_total"] == 222.26
    assert diag["salla_declared_total"] == 208.74
    assert diag["tax_difference"] == -13.52


def test_totals_guard_still_blocks_when_items_sum_breaks():
    """Sanity: the OTHER guards still bite when items are actually
    missing (Make.com data integrity)."""
    canonical = {
        "subtotal":     200.0,         # claims 200 of items
        "tax_amount":   0,
        "shipping_amount": 0,
        "total_amount": 200.0,
        "items": [{                    # but only sends 50
            "sku": "X", "quantity": 1, "unit_price": 50.0,
        }],
    }
    result = validate_totals(canonical)
    assert result.ok is False
    assert result.code in (
        "line_items_incomplete", "line_items_total_mismatch")
    # Still surfaces the Mezan view.
    assert "mezan_vat_diagnostics" in result.details


def test_totals_guard_always_emits_diagnostics_on_success():
    """Happy path also includes mezan_vat_diagnostics so the UI has
    consistent data."""
    canonical = {
        "subtotal":     100.0,
        "tax_amount":   15.0,
        "shipping_amount": 0,
        "total_amount": 115.0,
        "items": [{"sku": "A", "quantity": 1, "unit_price": 100.0,
                   "tax_amount": 15.0}],
    }
    result = validate_totals(canonical)
    assert result.ok is True
    assert "mezan_vat_diagnostics" in result.details


# ─── Status Gate before Totals Guard (in-process) ───────────────────
# E2E test runs in the totals_guard_e2e file. Here we test the
# decision contract: `under_review` is NOT eligible for invoicing.
def test_under_review_is_not_eligible():
    """business_rules.evaluate must report `under_review` as not
    eligible — this is what guarantees the status gate routes the
    order to SKIPPED, not to DEAD_LETTER via totals_guard."""
    from integrations.qoyod.business_rules import evaluate as evaluate_rules
    from integrations.qoyod.dto import (
        SalesOrderDTO, CustomerDTO, LineItemDTO,
    )
    from datetime import datetime, timezone
    dto = SalesOrderDTO(
        order_id="538111111",
        order_number="268746039",
        order_status="under_review",
        order_status_native="بإنتظار المراجعة",
        order_date=datetime(2026, 6, 27, tzinfo=timezone.utc),
        currency="SAR",
        customer=CustomerDTO(name="x"),
        items=[LineItemDTO(sku="A", name="x", quantity=1, unit_price=180,
                            tax_amount=13.54, discount_amount=10.8,
                            total=182.74)],
    )
    settings = {
        "invoice_trigger_statuses": ["completed"],
        "trigger_once_only": True,
    }
    decision = evaluate_rules(dto, settings, existing_invoice_row=None)
    assert decision.eligible is False
    assert "not_in_trigger_statuses" in (decision.reason or "")


def test_completed_status_is_eligible():
    """Sanity counter-test."""
    from integrations.qoyod.business_rules import evaluate as evaluate_rules
    from integrations.qoyod.dto import (
        SalesOrderDTO, CustomerDTO, LineItemDTO,
    )
    from datetime import datetime, timezone
    dto = SalesOrderDTO(
        order_id="538111111",
        order_number="268746039",
        order_status="completed",
        order_status_native="تم التنفيذ",
        order_date=datetime(2026, 6, 27, tzinfo=timezone.utc),
        completed_at=datetime(2026, 6, 27, tzinfo=timezone.utc),
        currency="SAR",
        customer=CustomerDTO(name="x"),
        items=[LineItemDTO(sku="A", name="x", quantity=1, unit_price=180,
                            tax_amount=13.54, discount_amount=10.8,
                            total=182.74)],
    )
    settings = {
        "invoice_trigger_statuses": ["completed"],
        "trigger_once_only": True,
    }
    decision = evaluate_rules(dto, settings, existing_invoice_row=None)
    assert decision.eligible is True
