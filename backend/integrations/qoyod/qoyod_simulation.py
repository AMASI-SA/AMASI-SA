"""Iter-001k+ — Read-Only Mezan VAT 15% Simulation vs Salla Gross.

Directive (operator, 2026-02-27)
────────────────────────────────
    Salla tax is NEVER source of truth (Salla may report 0/8/15/…%).
    Mezan VAT = FIXED 15%.
    Qoyod invoice is BUILT BY MEZAN using Salla's Gross customer
    total as the single anchor.

Contract (STRICT):
    • Salla `canonical.tax_amount` is IGNORED for the simulation.
    • `canonical.total_amount` is the ONLY trusted anchor.
    • Mezan derives net + VAT internally at 15%.
    • Simulated Qoyod gross MUST equal Salla gross (0.00 or ≤0.01
      rounding warning) — otherwise the operator has a real
      normalization bug to fix.
    • Read-Only. Zero DB writes. Zero Qoyod API calls. No policy
      or gate changes. No send.

Notes:
    • Line/shipping proportion math mirrors
      `invoice_builder.py::_line_for_match_salla_total` — which
      already reverse-engineers a Qoyod discount so that
      `Σ(qoyod_gross) = salla.total_amount` by construction.
    • This simulation is a Read-Only mirror of that math; it never
      mutates the actual invoice_builder.
"""
from __future__ import annotations

from typing import Any, Optional

from integrations.qoyod.order_totals_breakdown import _to_float, _r2


# ── Mezan VAT SSOT ──────────────────────────────────────────────────
MEZAN_VAT_RATE:  float = 0.15
MEZAN_TAX_FACTOR: float = 1.0 + MEZAN_VAT_RATE   # 1.15

# Contract marker for the send-timestamp policy (Iter-001k).
PAYLOAD_DATE_SOURCE: str = "send_date"


def _line_gross_target(it: dict) -> tuple[float, str]:
    """Determine the per-line customer-paid gross that Salla shows.

    Priority (mirrors `_extract_item_total` in normalizer.py):
      1. `item.total` when present and > 0 (Salla's authoritative
         line-gross field).
      2. Fallback: `unit_price × qty − discount + tax_amount`
         (LineItemDTO invariant: total = unit×qty − disc + tax).
      3. Last-resort: `unit_price × qty − discount`.

    Returns (gross, source_label).
    """
    total = _to_float(it.get("total"))
    if total is not None and total > 0:
        return (round(total, 4), "canonical.item.total")

    unit = _to_float(it.get("unit_price")) \
        or _to_float(it.get("price")) or 0.0
    qty  = _to_float(it.get("quantity")) or 1.0
    disc = _to_float(it.get("discount_amount")) or 0.0
    tax  = _to_float(it.get("tax_amount")) or 0.0
    if tax != 0:
        return (round(unit * qty - disc + tax, 4),
                "derived_from_unit_qty_discount_plus_salla_line_tax")
    return (round(unit * qty - disc, 4),
            "derived_from_unit_qty_discount")


def _current_check_from_canonical(canonical: dict) -> tuple[float, float]:
    """Faithfully mirror `eligible_orders._check_totals` — the
    formula we are diagnosing."""
    items_sum = 0.0
    for it in canonical.get("items") or []:
        if not isinstance(it, dict):
            continue
        qty  = _to_float(it.get("quantity")) or 1.0
        unit = _to_float(it.get("unit_price")) \
            or _to_float(it.get("price")) or 0.0
        items_sum += qty * unit
    shipping = _to_float(canonical.get("shipping_amount")) or 0.0
    tax      = _to_float(canonical.get("tax_amount")) or 0.0
    total    = _to_float(canonical.get("total_amount")) or 0.0
    expected = round(items_sum + shipping + tax, 2)
    diff     = round(total - expected, 2)
    return (expected, diff)


def build_qoyod_simulation(*, inbox_row: dict) -> dict:
    """Read-Only diagnostic that shows, side-by-side:

        • The CURRENT `_check_totals` output (buggy).
        • A simulated Qoyod invoice using Mezan VAT 15%, anchored
          on Salla's Gross customer total.

    Faithful mirror of `invoice_builder.py::_line_for_match_salla_total`
    for the per-line math; anchored on `salla.total_amount` at the
    order level so the simulated gross ALWAYS reconciles.
    """
    canonical = inbox_row.get("canonical_payload") or {}
    salla_official_total = _to_float(
        canonical.get("total_amount")) or 0.0

    # 1. Baseline: current buggy check.
    current_expected, current_diff = \
        _current_check_from_canonical(canonical)

    # 2. Simulated per-line reconstruction.
    lines_out: list[dict] = []
    items_gross_sum_raw = 0.0
    per_line_gross_source_labels: set[str] = set()
    items = canonical.get("items") or []
    for it in items:
        if not isinstance(it, dict):
            continue
        qty  = _to_float(it.get("quantity")) or 1.0
        unit = _to_float(it.get("unit_price")) \
            or _to_float(it.get("price")) or 0.0
        disc = _to_float(it.get("discount_amount")) or 0.0
        line_gross, gross_source = _line_gross_target(it)
        per_line_gross_source_labels.add(gross_source)
        items_gross_sum_raw += line_gross

        # Mezan 15% inverse split (customer paid line_gross, split
        # into net + VAT at 15%).
        line_net_mezan = round(line_gross / MEZAN_TAX_FACTOR, 4)
        line_vat_mezan = round(line_gross - line_net_mezan, 4)
        # Faithful mirror of `_line_for_match_salla_total`:
        original_base    = round(unit * qty, 4)
        qoyod_discount   = round(original_base - line_net_mezan, 4)
        fallback_used = False
        if qoyod_discount < 0:
            # `invoice_builder` fallback: shrink unit_price, no disc.
            adj_unit_price = round(line_net_mezan / qty, 4) \
                if qty else line_net_mezan
            computed_gross = round(
                (adj_unit_price * qty) * MEZAN_TAX_FACTOR, 2)
            fallback_used = True
        else:
            computed_gross = round(
                (unit * qty - qoyod_discount) * MEZAN_TAX_FACTOR, 2)

        lines_out.append({
            "sku":                (it.get("sku") or "")[:64],
            "quantity":           qty,
            "unit_price":         unit,
            "salla_line_discount": _r2(disc),
            "target_gross":       _r2(line_gross),
            "target_gross_source": gross_source,
            "mezan_net":          _r2(line_net_mezan),
            "mezan_vat_at_15":    _r2(line_vat_mezan),
            "qoyod_discount":     _r2(qoyod_discount),
            "qoyod_computed_gross": _r2(computed_gross),
            "fallback_used":      fallback_used,
        })

    items_gross_sum = round(items_gross_sum_raw, 2)

    # 3. Shipping — invoice_builder derives shipping_target_gross
    #    as `total_amount − Σ(item.total)`. We faithfully reproduce.
    shipping_amount_canonical = _to_float(
        canonical.get("shipping_amount")) or 0.0
    cod_fee_amount = _to_float(
        canonical.get("cod_fee_amount")) or 0.0
    shipping_target_gross = round(
        salla_official_total - items_gross_sum - cod_fee_amount, 2)
    shipping_diag: Optional[dict] = None
    shipping_qoyod_gross = 0.0
    if shipping_amount_canonical > 0 or shipping_target_gross > 0:
        # If canonical.shipping_amount is 0 but target > 0, use target
        # as the shipping unit_price (Salla may have folded shipping
        # into totals without an explicit line).
        shipping_unit_price = round(
            shipping_amount_canonical if shipping_amount_canonical > 0
            else shipping_target_gross, 4)
        shipping_target_net = round(
            shipping_target_gross / MEZAN_TAX_FACTOR, 4) \
            if shipping_target_gross > 0 else 0.0
        shipping_discount = round(
            shipping_unit_price - shipping_target_net, 4)
        fallback = False
        if shipping_discount < 0:
            shipping_unit_price = shipping_target_net
            shipping_discount = 0.0
            fallback = True
        shipping_qoyod_gross = round(
            (shipping_unit_price - shipping_discount)
            * MEZAN_TAX_FACTOR, 2)
        shipping_diag = {
            "canonical_shipping_amount":  _r2(shipping_amount_canonical),
            "shipping_target_gross":      _r2(shipping_target_gross),
            "shipping_mezan_net":         _r2(shipping_target_net),
            "shipping_mezan_vat_at_15":   _r2(
                shipping_target_gross - shipping_target_net),
            "shipping_qoyod_discount":    _r2(shipping_discount),
            "shipping_qoyod_gross":       _r2(shipping_qoyod_gross),
            "fallback_used":              fallback,
            "handling": ("shipping_target_gross = salla.total_amount "
                         "− Σ(item.target_gross) − cod_fee. Same "
                         "Mezan-15% inverse split as items."),
        }

    # 4. COD fee.
    cod_diag: Optional[dict] = None
    cod_qoyod_gross = 0.0
    if cod_fee_amount > 0:
        cod_net = round(cod_fee_amount / MEZAN_TAX_FACTOR, 4)
        cod_qoyod_gross = round(
            (cod_fee_amount - (cod_fee_amount - cod_net))
            * MEZAN_TAX_FACTOR, 2)
        cod_diag = {
            "cod_fee_amount":     _r2(cod_fee_amount),
            "cod_mezan_net":      _r2(cod_net),
            "cod_mezan_vat_at_15": _r2(cod_fee_amount - cod_net),
            "cod_qoyod_gross":    _r2(cod_qoyod_gross),
        }

    # 5. Simulated Qoyod gross total.
    simulated_qoyod_gross_total = round(
        sum(li["qoyod_computed_gross"] for li in lines_out)
        + shipping_qoyod_gross
        + cod_qoyod_gross, 2)

    # 6. Diff.
    simulated_diff = round(
        salla_official_total - simulated_qoyod_gross_total, 2)
    abs_diff = abs(simulated_diff)
    if abs_diff == 0:
        rounding_warning = None
        would_pass_mezan_vat_guard = True
    elif abs_diff <= 0.01:
        rounding_warning = (
            f"Rounding drift of {simulated_diff} SAR within "
            f"tolerance (≤0.01). Qoyod invoice will still post the "
            f"exact Salla gross.")
        would_pass_mezan_vat_guard = True
    else:
        rounding_warning = (
            f"Simulated Qoyod gross diverges from Salla by "
            f"{simulated_diff} SAR (>0.01). This is NOT a Salla-tax "
            f"issue — it means the normalizer failed to capture "
            f"`item.total` on one or more lines. Inspect "
            f"`simulated_lines[*].target_gross_source`: any line "
            f"labelled `derived_from_unit_qty_discount` means the "
            f"gross is missing tax that was baked into Salla's own "
            f"item.total (which normalizer dropped).")
        would_pass_mezan_vat_guard = False

    # 7. Mezan VAT split at the order level (informational).
    net_amount_derived_from_gross = round(
        salla_official_total / MEZAN_TAX_FACTOR, 2)
    vat_amount_derived_at_15 = round(
        salla_official_total - net_amount_derived_from_gross, 2)

    # 8. Handling summaries (operator-facing strings).
    discount_handling = (
        "Line discounts are preserved from canonical.items[]."
        "discount_amount. They REDUCE Salla's per-line gross "
        "before Mezan's inverse-15% split. Order-level "
        "discount_amount is NOT double-subtracted (already reflected "
        "in item.total when Salla emits it).")
    shipping_handling = (
        "Shipping is treated as an additional invoice line whose "
        "target gross = salla.total_amount − Σ(item.target_gross) − "
        "cod_fee. Same Mezan-15% inverse split as items so that "
        "Σ(qoyod_gross) = salla.total_amount by construction.")

    formula_notes = [
        f"mezan_vat_rate = {MEZAN_VAT_RATE} (FIXED — Mezan SSOT, "
        f"never Salla).",
        ("Salla `canonical.tax_amount` is IGNORED. The only Salla "
         "field trusted is `total_amount` (customer-paid gross)."),
        ("Per line: target_gross ← item.total (Salla). Mezan then "
         "computes net = target_gross / 1.15, vat = target_gross − "
         "net. Qoyod payload uses unit_price verbatim and a "
         "reverse-engineered `qoyod_discount = unit×qty − net` so "
         "that Qoyod's server-side computation `((unit×qty − "
         "discount) × 1.15) = target_gross`."),
        ("current_check_expected mirrors "
         "`eligible_orders._check_totals` — the buggy formula that "
         "USES canonical.tax_amount and does NOT subtract discount."),
        (f"payload_date_source = '{PAYLOAD_DATE_SOURCE}' — Qoyod "
         f"payload dates are stamped from the frozen "
         f"`send_timestamp_riyadh` decision (Iter-001k)."),
        ("would_pass_mezan_vat_guard is TRUE when the simulated "
         "Qoyod gross reconciles to Salla's official gross within "
         "0.01 — the ONLY condition that should gate a real send."),
    ]

    return {
        "order_number":
            str((canonical.get("order_number")
                 or inbox_row.get("salla_order_number") or "")),
        "salla_order_id":
            str(canonical.get("order_id")
                or inbox_row.get("salla_order_id") or ""),
        "trace_id":                inbox_row.get("trace_id"),

        # ── Baselines (current buggy check) ─────────────────────
        "salla_official_total":    _r2(salla_official_total),
        "current_check_expected":  current_expected,
        "current_check_diff":      current_diff,

        # ── Mezan-VAT simulation ────────────────────────────────
        "mezan_vat_rate":                       MEZAN_VAT_RATE,
        "net_amount_derived_from_gross":        net_amount_derived_from_gross,
        "vat_amount_derived_at_15":             vat_amount_derived_at_15,
        "simulated_qoyod_total_using_mezan_vat_15":
                                                simulated_qoyod_gross_total,
        "simulated_qoyod_diff_vs_salla_total":  simulated_diff,
        "rounding_warning":                     rounding_warning,
        "would_pass_mezan_vat_guard":           would_pass_mezan_vat_guard,

        # ── Handling summaries ──────────────────────────────────
        "discount_handling":     discount_handling,
        "shipping_handling":     shipping_handling,

        # ── Line-by-line breakdown ──────────────────────────────
        "simulated_lines":       lines_out,
        "simulated_shipping":    shipping_diag,
        "simulated_cod_fee":     cod_diag,
        "line_gross_sources_seen": sorted(
            per_line_gross_source_labels),

        # ── Contract markers ────────────────────────────────────
        "payload_date_source":   PAYLOAD_DATE_SOURCE,
        "formula_notes":         formula_notes,

        # ── Read-Only guarantees ────────────────────────────────
        "read_only":             True,
        "no_qoyod_api_calls":    True,
        "no_db_writes":          True,
    }


async def fetch_qoyod_simulation(
    db,
    *,
    user_id: str,
    order_number: str,
) -> dict:
    """Look up the LATEST inbox row for `order_number` and return the
    simulation. Read-Only end-to-end."""
    q = {
        "user_id": user_id,
        "$or": [
            {"salla_order_number": order_number},
            {"salla_order_number": _to_float(order_number)},
            {"canonical_payload.order_number": order_number},
        ],
    }
    rows = await db.integration_inbox.find(
        q, {"_id": 0}).sort(
        [("received_at", -1), ("pipeline_started_at", -1)]).to_list(
        length=5)
    if not rows:
        return {
            "order_number":      order_number,
            "found":             False,
            "read_only":         True,
            "no_qoyod_api_calls": True,
            "no_db_writes":      True,
            "note": ("No integration_inbox row found for this "
                     "order_number in this tenant."),
        }
    sim = build_qoyod_simulation(inbox_row=rows[0])
    sim["found"] = True
    sim["traces_available"] = len(rows)
    return sim
