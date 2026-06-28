"""Iter-285 — Customer-First Tax Mode (trial Go-Live).

User decision (2026-02-27)
──────────────────────────
For the trial Qoyod cycle we use **Option A: customer_first**.
  • Invoice total MUST equal what the customer paid (Salla's declared
    total / `canonical.total_amount`).
  • Invoice total MUST equal Receipt amount.
  • Mezan 15% policy is DIAGNOSTIC ONLY (already surfaced via
    `mezan_vat_diagnostics` in totals_guard since Iter-282).

Implementation
──────────────
1. New setting `tax_mode` ∈ {`customer_first` (default), `mezan_fixed_15`}.
2. New setting `zero_tax_id` — Qoyod tax record id for the 0% rate
   used in customer_first mode.
3. `invoice_builder.build_invoice_payload` honors `tax_mode`:
   • customer_first → tax-inclusive `unit_price = up + tax/qty` and
     `tax_id = zero_tax_id` (so Qoyod doesn't add tax on top).
   • mezan_fixed_15 → original behavior (`tax_id = default_tax_id`).
4. `invoice_builder.estimated_invoice_total(dto_dict, settings)` —
   the total Qoyod will compute for the row.
5. `preflight.run` adds check #7 (`invoice_receipt_reconciliation`):
   when tax_mode=customer_first, estimated_invoice_total MUST equal
   receipt amount within tolerance (max 0.10 SAR or 0.5%). Otherwise
   block before any POST.
6. `preview_reprocess` surfaces a `reconciliation` block with
   tax_mode, salla_declared_total, mezan_expected_total,
   tax_difference, estimated_invoice_total, receipt_amount,
   invoice_receipt_reconciled.

Lock-in scenario: production order 268756329 (3 items, item-level
discounts, Salla declared total = 290.63 SAR).
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from integrations.qoyod.invoice_builder import (
    build_invoice_payload, estimated_invoice_total,
    _get_tax_mode, _line_unit_price_for_mode, _line_tax_id_for_mode,
    TAX_MODE_CUSTOMER_FIRST, TAX_MODE_MEZAN_FIXED_15, DEFAULT_TAX_MODE,
)
from integrations.qoyod.preflight import run as preflight_run


def _order_268756329_canonical():
    return {
        "order_number":    "268756329",
        "order_id":        "538555555",
        "currency":        "SAR",
        "subtotal":        304.00,
        "tax_amount":      21.53,
        "shipping_amount": 0.0,
        "discount_amount": 34.9,
        "total_amount":    290.63,
        "order_status":    "completed",
        "payment_method":  "tamara_installment",
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


def _settings_customer_first(**over):
    base = {
        "tax_mode":              TAX_MODE_CUSTOMER_FIRST,
        "zero_tax_id":           "TAX-ZERO",
        "default_tax_id":        "TAX-15",      # unused in customer_first
        # Iter-290e — Iter-285 invoice tests pre-date the
        # match_salla_total policy; keep them on legacy passthrough
        # to assert the canonical Iter-290c payload shape.
        "invoice_total_policy":  "legacy_passthrough",
        "invoice_trigger_statuses": ["completed"],
        "payment_method_mapping": [
            {"salla_method": "tamara", "qoyod_account_id": "ACCT-tamara"},
        ],
    }
    base.update(over)
    return base


def _settings_mezan_fixed_15(**over):
    base = _settings_customer_first(**over)
    base["tax_mode"] = TAX_MODE_MEZAN_FIXED_15
    return base


# ─── Constants + defaults ───────────────────────────────────────────
def test_default_tax_mode_is_customer_first():
    assert DEFAULT_TAX_MODE == TAX_MODE_CUSTOMER_FIRST


def test_get_tax_mode_falls_back_to_default_when_missing():
    assert _get_tax_mode({}) == TAX_MODE_CUSTOMER_FIRST


def test_get_tax_mode_rejects_unknown_values():
    assert _get_tax_mode({"tax_mode": "weird"}) == TAX_MODE_CUSTOMER_FIRST


def test_get_tax_mode_honors_mezan_fixed_15():
    assert _get_tax_mode({"tax_mode": "mezan_fixed_15"}) == TAX_MODE_MEZAN_FIXED_15


# ─── Line tax-inclusive price ───────────────────────────────────────
def test_line_unit_price_customer_first_is_tax_inclusive():
    # 199 + 14.33/1 = 213.33
    it = {"unit_price": 199, "quantity": 1, "tax_amount": 14.33}
    assert _line_unit_price_for_mode(it, TAX_MODE_CUSTOMER_FIRST) == 213.33


def test_line_unit_price_mezan_fixed_15_is_net():
    it = {"unit_price": 199, "quantity": 1, "tax_amount": 14.33}
    assert _line_unit_price_for_mode(it, TAX_MODE_MEZAN_FIXED_15) == 199.0


def test_line_unit_price_handles_multi_quantity():
    # tax 20 distributed across qty 2 → +10 per unit
    it = {"unit_price": 100, "quantity": 2, "tax_amount": 20}
    assert _line_unit_price_for_mode(it, TAX_MODE_CUSTOMER_FIRST) == 110.0


def test_line_tax_id_customer_first_uses_zero_tax_id():
    s = {"tax_mode": TAX_MODE_CUSTOMER_FIRST, "zero_tax_id": "TAX-ZERO"}
    assert _line_tax_id_for_mode({}, TAX_MODE_CUSTOMER_FIRST, s) == "TAX-ZERO"


def test_line_tax_id_customer_first_omits_when_no_zero_tax_id():
    s = {"tax_mode": TAX_MODE_CUSTOMER_FIRST}
    assert _line_tax_id_for_mode({}, TAX_MODE_CUSTOMER_FIRST, s) is None


def test_line_tax_id_mezan_fixed_15_uses_default_tax_id():
    s = {"tax_mode": TAX_MODE_MEZAN_FIXED_15, "default_tax_id": "TAX-15"}
    assert _line_tax_id_for_mode({}, TAX_MODE_MEZAN_FIXED_15, s) == "TAX-15"


# ─── estimated_invoice_total ─────────────────────────────────────────
def test_estimated_invoice_total_matches_customer_paid_for_268756329():
    """The headline contract — invoice == receipt == 290.63 SAR."""
    dto = _order_268756329_canonical()
    s = _settings_customer_first()
    est = estimated_invoice_total(dto, s)
    # (5+0)*1 - 5 + (199+14.33)*1 - 19.9 + (100+7.20)*1 - 10 + 0 shipping
    # = 0 + 193.43 + 97.20 = 290.63
    assert est == 290.63


def test_estimated_invoice_total_mezan_fixed_15_diverges():
    dto = _order_268756329_canonical()
    s = _settings_mezan_fixed_15()
    est = estimated_invoice_total(dto, s)
    # (5+199+100)*1 - 34.9 = 269.10; ×1.15 + 0 shipping ×1.15 = 309.465 ≈ 309.46-7
    assert abs(est - 309.47) <= 0.02


# ─── build_invoice_payload — customer_first mode ────────────────────
def test_invoice_builder_customer_first_uses_tax_inclusive_unit_prices():
    """Iter-290c — payload reshaped per Qoyod apidoc.

    `customer_first` no longer emits tax-inclusive unit_prices on the
    invoice payload (Qoyod's tax_percent model would double-tax them).
    Both tax_modes now send Salla's raw NET unit_price + tax_percent=15
    per line. The `_line_unit_price_for_mode` helper is kept for any
    callers that need the inclusive view (diagnostics, totals_guard).
    """
    dto = _order_268756329_canonical()
    s   = _settings_customer_first()
    payload = build_invoice_payload(
        dto_dict=dto, qoyod_customer_id="999",
        product_resolutions=[
            {"sku": "A", "qoyod_product_id": "1"},
            {"sku": "B", "qoyod_product_id": "2"},
            {"sku": "C", "qoyod_product_id": "3"},
        ],
        invoice_date=datetime(2026, 6, 27, tzinfo=timezone.utc),
        settings=s,
    )
    lines = payload["invoice"]["line_items"]
    assert len(lines) == 3
    # NET unit_prices straight from Salla.
    assert lines[0]["unit_price"] == 5.0
    assert lines[0]["tax_percent"] == 15
    assert lines[0]["discount"] == 5.0
    assert lines[0]["discount_type"] == "amount"
    assert "tax_id" not in lines[0]
    assert "inventory_id" not in lines[0]

    assert lines[1]["unit_price"] == 199.0
    assert lines[1]["tax_percent"] == 15
    assert lines[1]["discount"] == 19.9

    assert lines[2]["unit_price"] == 100.0
    assert lines[2]["tax_percent"] == 15
    assert lines[2]["discount"] == 10.0


def test_invoice_builder_customer_first_omits_tax_id_when_zero_tax_id_missing():
    """Iter-290c — tax_id is never emitted on lines anymore."""
    dto = _order_268756329_canonical()
    s   = _settings_customer_first(zero_tax_id="")
    payload = build_invoice_payload(
        dto_dict=dto, qoyod_customer_id="999",
        product_resolutions=[
            {"sku": "A", "qoyod_product_id": "1"},
            {"sku": "B", "qoyod_product_id": "2"},
            {"sku": "C", "qoyod_product_id": "3"},
        ],
        invoice_date=datetime(2026, 6, 27, tzinfo=timezone.utc),
        settings=s,
    )
    for line in payload["invoice"]["line_items"]:
        assert "tax_id" not in line
        assert line["tax_percent"] == 15


def test_invoice_builder_mezan_fixed_15_uses_default_tax_id():
    """Iter-290c — both modes now emit tax_percent (not tax_id) and
    NET unit_price. This test name is kept for traceability."""
    dto = _order_268756329_canonical()
    s   = _settings_mezan_fixed_15()
    payload = build_invoice_payload(
        dto_dict=dto, qoyod_customer_id="999",
        product_resolutions=[
            {"sku": "A", "qoyod_product_id": "1"},
            {"sku": "B", "qoyod_product_id": "2"},
            {"sku": "C", "qoyod_product_id": "3"},
        ],
        invoice_date=datetime(2026, 6, 27, tzinfo=timezone.utc),
        settings=s,
    )
    for line in payload["invoice"]["line_items"]:
        assert "tax_id" not in line
        assert line["tax_percent"] == 15
        assert line["discount_type"] == "amount"
    # Unit price is Salla's net (no inclusion).
    assert payload["invoice"]["line_items"][1]["unit_price"] == 199.0


def test_invoice_builder_notes_carry_tax_mode_for_audit():
    dto = _order_268756329_canonical()
    s   = _settings_customer_first()
    payload = build_invoice_payload(
        dto_dict=dto, qoyod_customer_id="CST-1",
        product_resolutions=[
            {"sku": "A", "qoyod_product_id": "P-A"},
            {"sku": "B", "qoyod_product_id": "P-B"},
            {"sku": "C", "qoyod_product_id": "P-C"},
        ],
        invoice_date=datetime(2026, 6, 27, tzinfo=timezone.utc),
        settings=s,
    )
    assert "tax_mode=customer_first" in payload["invoice"]["notes"]


# ─── Preflight — invoice/receipt reconciliation ─────────────────────
def test_preflight_customer_first_passes_when_invoice_matches_receipt():
    """End-to-end: with customer_first mode and a clean DTO, preflight
    succeeds because estimated_invoice_total == receipt_amount."""
    dto = _order_268756329_canonical()
    s   = _settings_customer_first()
    pf  = preflight_run(
        dto_dict=dto, settings=s,
        qoyod_customer_id="CST-1",
        product_resolutions=[
            {"sku": "A", "qoyod_product_id": "P-A"},
            {"sku": "B", "qoyod_product_id": "P-B"},
            {"sku": "C", "qoyod_product_id": "P-C"},
        ],
        existing_invoice_row=None,
    )
    # No reconciliation failure.
    recon_failures = [f for f in pf.failures
                      if f.get("check") == "invoice_receipt_reconciliation"]
    assert recon_failures == [], pf.failures


def test_preflight_customer_first_blocks_when_reconciliation_fails():
    """If receipt_amount is materially different from estimated invoice
    total, preflight must block."""
    dto = _order_268756329_canonical()
    dto["total_amount"] = 999.99     # poisoned — doesn't match items
    s   = _settings_customer_first()
    pf  = preflight_run(
        dto_dict=dto, settings=s,
        qoyod_customer_id="CST-1",
        product_resolutions=[
            {"sku": "A", "qoyod_product_id": "P-A"},
            {"sku": "B", "qoyod_product_id": "P-B"},
            {"sku": "C", "qoyod_product_id": "P-C"},
        ],
        existing_invoice_row=None,
    )
    recon = next(
        (f for f in pf.failures
         if f.get("check") == "invoice_receipt_reconciliation"),
        None,
    )
    assert recon is not None
    assert recon["code"] == "invoice_total_mismatch_with_receipt"
    assert recon["extra"]["estimated_invoice_total"] == 290.63
    assert recon["extra"]["receipt_amount"]          == 999.99
    assert pf.passed is False


def test_preflight_mezan_fixed_15_does_not_require_reconciliation():
    """In mezan_fixed_15 mode the receipt-vs-invoice gap is EXPECTED
    (Mezan invoices at 15%, customer paid Salla's rate). Preflight
    must NOT block on reconciliation in this mode — the gap is the
    operator's responsibility via an adjustment GL line."""
    dto = _order_268756329_canonical()
    s   = _settings_mezan_fixed_15()
    pf  = preflight_run(
        dto_dict=dto, settings=s,
        qoyod_customer_id="CST-1",
        product_resolutions=[
            {"sku": "A", "qoyod_product_id": "P-A"},
            {"sku": "B", "qoyod_product_id": "P-B"},
            {"sku": "C", "qoyod_product_id": "P-C"},
        ],
        existing_invoice_row=None,
    )
    recon_failures = [f for f in pf.failures
                      if f.get("check") == "invoice_receipt_reconciliation"]
    assert recon_failures == []


def test_preflight_customer_first_does_not_demand_default_tax_id():
    """customer_first doesn't use default_tax_id. Missing it is fine."""
    dto = _order_268756329_canonical()
    s   = _settings_customer_first(default_tax_id="")
    pf  = preflight_run(
        dto_dict=dto, settings=s,
        qoyod_customer_id="CST-1",
        product_resolutions=[
            {"sku": "A", "qoyod_product_id": "P-A"},
            {"sku": "B", "qoyod_product_id": "P-B"},
            {"sku": "C", "qoyod_product_id": "P-C"},
        ],
        existing_invoice_row=None,
    )
    tax_failures = [f for f in pf.failures if f.get("check") == "tax"]
    assert tax_failures == []
