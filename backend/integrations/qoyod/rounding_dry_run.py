"""Iter-290k · Phase 2 DRY-RUN — Decimal-based rounding-correction simulator.

ZERO-WRITE invariant
────────────────────
  • No DB writes.
  • No قيود calls.
  • No mutation of invoice / payment math anywhere in the live pipeline.

What this does
──────────────
For each inbox row in the eligible set:

  1. Reads the EXACT قيود invoice payload that was sent
     (`qoyod_payloads.invoice.line_items`).
  2. Recomputes the per-line gross using Decimal + ROUND_HALF_UP —
     `line_gross = round_half_up((unit_price * qty - discount) * (1 + tax%/100), 2)`
     — i.e. the math قيود is expected to apply server-side.
  3. Sums the per-line gross into `simulated_qoyod_invoice_total`.
  4. Computes `diff_before = simulated_total - salla_total`.
  5. If |diff_before| is in {0.01, 0.02} (and the row is in the
     eligible bucket+severity set), proposes an adjustment to the
     LARGEST product line's net discount:
        adjustment_net = diff_before / tax_factor
        new_discount   = current_discount + adjustment_net
     (positive adjustment_net when قيود drifted ABOVE Salla; i.e.
     we INCREASE the discount to bring the total DOWN.)
  6. Re-simulates and reports `diff_after`.
  7. Refuses any adjustment that would make a line's discount go
     negative — flags those as `negative_discount_blocked`.

Eligibility (per the user's explicit narrowing for Phase 2)
───────────────────────────────────────────────────────────
  INCLUDE:
    • severity == MINOR_ROUNDING (|invoice_diff| ≤ 0.02)
    • bucket in {QOYOD_SERVER_SIDE_ROUNDING,
                 MULTI_LINE_CUMULATIVE_ROUNDING,
                 SHIPPING_ROUNDING_MISMATCH,
                 INVOICE_TOTAL_ROUNDING_MISMATCH}
  EXCLUDE:
    • DISCOUNT_ALLOCATION_MISMATCH      — needs its own RCA first
    • PAYMENT_MISMATCH_ONLY             — invoice already correct
    • INSUFFICIENT_DATA / NO_MISMATCH   — nothing to simulate
    • MATERIAL_MISMATCH                 — |diff| > 0.05 is NOT rounding
    • Rows without `qoyod_payloads.invoice.line_items`
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Optional

from integrations.qoyod.rounding_mismatch_report import _classify_row

# Phase-2 narrow scope per user: only 0.01 / 0.02 drifts.
ADJUSTABLE_DIFFS = {Decimal("0.01"), Decimal("0.02")}
ZERO_TOL_EPS = Decimal("0.005")

ELIGIBLE_BUCKETS = {
    "QOYOD_SERVER_SIDE_ROUNDING",
    "MULTI_LINE_CUMULATIVE_ROUNDING",
    "SHIPPING_ROUNDING_MISMATCH",
    "INVOICE_TOTAL_ROUNDING_MISMATCH",
}

# Rationale: we explicitly exclude these even when they have a 0.01
# drift. The user has split the remediation paths and Phase-2 only
# touches the قيود-internal rounding family. DISCOUNT_ALLOCATION
# needs its own RCA before any payload mutation.
EXCLUDED_BUCKETS = {
    "DISCOUNT_ALLOCATION_MISMATCH",
    "PAYMENT_MISMATCH_ONLY",
    "INSUFFICIENT_DATA",
    "NO_MISMATCH",
}


def _q(v: Any) -> Decimal:
    """Coerce any numeric-ish value to Decimal. None / non-numeric → 0."""
    if v is None or v == "":
        return Decimal("0")
    try:
        return Decimal(str(v))
    except Exception:
        return Decimal("0")


def _round_half_up_2(v: Decimal) -> Decimal:
    """Quantize to 2 decimal places using banker-FREE rounding —
    the user explicitly asked for ROUND_HALF_UP (the rule قيود uses)."""
    return v.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _round_half_up_4(v: Decimal) -> Decimal:
    """4dp precision for discount/net-level adjustments (the precision
    the قيود payload accepts before قيود itself rounds)."""
    return v.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def simulate_invoice(payload_lines: list[dict]) -> tuple[Decimal, list[dict]]:
    """Mirror قيود's server-side per-line math using Decimal +
    ROUND_HALF_UP. Returns `(invoice_total, per_line[{line_net,
    line_gross, tax_factor}])`."""
    total = Decimal("0")
    per_line: list[dict] = []
    for li in payload_lines or []:
        unit_price = _q(li.get("unit_price"))
        qty        = _q(li.get("quantity"))
        discount   = _q(li.get("discount"))
        tax_pct    = _q(li.get("tax_percent"))
        tax_factor = Decimal("1") + (tax_pct / Decimal("100"))
        net   = (unit_price * qty) - discount
        gross = net * tax_factor
        gross_r = _round_half_up_2(gross)
        per_line.append({
            "line_net":   _round_half_up_2(net),
            "line_gross": gross_r,
            "tax_factor": tax_factor,
        })
        total += gross_r
    return _round_half_up_2(total), per_line


def attempt_adjustment(payload_lines: list[dict],
                       target_total: Decimal,
                       *, max_diff: Decimal = Decimal("0.025")) -> dict:
    """Propose a single-line discount adjustment that pulls the
    simulated قيود total onto `target_total` (= Salla total).

    Rules
    ─────
      • Eligibility   : the largest line by (unit_price × qty).
      • Magnitude     : adjustment_net = diff / tax_factor of that line.
      • Sign          : if قيود > Salla → INCREASE the discount.
                        if قيود < Salla → DECREASE the discount.
      • Safety        : never let the new discount go negative.
      • Out-of-scope  : if |diff| > max_diff, refuse to touch — Phase 2
                        is bounded to halala-scale drift only.

    Returns
    ───────
      `{success: bool, reason: str?, chosen_idx: int?, …}` —  caller
      should treat `success=False` as a row that needs manual handling.
    """
    simulated_before, _ = simulate_invoice(payload_lines)
    diff_before = simulated_before - target_total

    if diff_before == Decimal("0"):
        return {
            "success":            True,
            "no_adjustment_needed": True,
            "simulated_before":   simulated_before,
            "diff_before":        diff_before,
            "simulated_after":    simulated_before,
            "diff_after":         Decimal("0"),
        }

    if abs(diff_before) > max_diff:
        return {
            "success":            False,
            "reason":             "out_of_phase2_scope",
            "simulated_before":   simulated_before,
            "diff_before":        diff_before,
        }

    if not payload_lines:
        return {
            "success":            False,
            "reason":             "no_payload_lines",
            "simulated_before":   simulated_before,
            "diff_before":        diff_before,
        }

    # Largest line by gross line value (unit_price × quantity).
    by_value = sorted(
        enumerate(payload_lines),
        key=lambda kv: _q(kv[1].get("unit_price")) * _q(kv[1].get("quantity")),
        reverse=True,
    )
    chosen_idx, chosen_line = by_value[0]
    tax_pct    = _q(chosen_line.get("tax_percent"))
    tax_factor = Decimal("1") + (tax_pct / Decimal("100"))

    # discount delta = diff_before / tax_factor.
    # If diff_before > 0 → قيود-side total too high → increase discount.
    # If diff_before < 0 → قيود-side total too low  → decrease discount.
    adjustment_net = _round_half_up_4(diff_before / tax_factor)
    current_discount = _q(chosen_line.get("discount"))
    new_discount = _round_half_up_4(current_discount + adjustment_net)

    if new_discount < Decimal("0"):
        return {
            "success":          False,
            "reason":           "negative_discount_blocked",
            "chosen_idx":       chosen_idx,
            "chosen_line_description": chosen_line.get("description"),
            "current_discount": current_discount,
            "attempted_adjustment_net": adjustment_net,
            "simulated_before": simulated_before,
            "diff_before":      diff_before,
        }

    adjusted = list(payload_lines)
    adjusted[chosen_idx] = {**chosen_line, "discount": new_discount}
    simulated_after, _ = simulate_invoice(adjusted)
    diff_after = simulated_after - target_total

    return {
        "success":            abs(diff_after) <= ZERO_TOL_EPS,
        "chosen_idx":         chosen_idx,
        "chosen_line_description": chosen_line.get("description"),
        "current_discount":   current_discount,
        "adjustment_net":     adjustment_net,
        "new_discount":       new_discount,
        "simulated_before":   simulated_before,
        "diff_before":        diff_before,
        "simulated_after":    simulated_after,
        "diff_after":         diff_after,
    }


def _extract_payload_line_items(row: dict) -> list[dict]:
    """`qoyod_payloads.invoice.line_items` carries the EXACT payload
    we sent (post-builder, post-diagnostics-pop). That's what قيود
    received and what its server-side math acted on."""
    payloads = row.get("qoyod_payloads") or {}
    inv = payloads.get("invoice") or {}
    if isinstance(inv, dict):
        # The pipeline stores the body shape `{invoice: {...}}` OR
        # the inner shape `{...}` depending on whether the dump
        # captured the wrapper. Try both.
        inner = inv.get("invoice") if isinstance(inv.get("invoice"), dict) else inv
        lis = inner.get("line_items") if isinstance(inner, dict) else None
        if isinstance(lis, list):
            return lis
    return []


def _row_eligible(classified: dict, payload_lines: list[dict]) -> tuple[bool, str]:
    """Return (eligible, reason_if_not)."""
    bucket   = classified.get("bucket")
    severity = classified.get("severity")
    inv_diff = classified.get("invoice_diff")

    if bucket in EXCLUDED_BUCKETS:
        return False, f"excluded_bucket:{bucket}"
    if bucket not in ELIGIBLE_BUCKETS:
        return False, f"non_phase2_bucket:{bucket}"
    if severity != "MINOR_ROUNDING":
        return False, f"non_minor_severity:{severity}"
    if inv_diff is None:
        return False, "no_invoice_diff"
    diff_q = _round_half_up_2(_q(inv_diff))
    if abs(diff_q) not in ADJUSTABLE_DIFFS:
        return False, f"diff_out_of_phase2_set:{diff_q}"
    if not payload_lines:
        return False, "no_payload_line_items"
    return True, ""


def _build_payload_columns(row: dict) -> list[dict]:
    """Per-line column view of the قيود-payload (separate from the
    Salla-source columns in the existing report). The user asked
    to clearly separate the COLUMN ORIGIN so Phase-2 reasoning isn't
    polluted by display-only numbers."""
    lis = _extract_payload_line_items(row)
    out: list[dict] = []
    for li in lis:
        out.append({
            "description":              li.get("description"),
            "qoyod_payload_quantity":   float(_q(li.get("quantity"))),
            "qoyod_payload_unit_price": float(_q(li.get("unit_price"))),
            "qoyod_payload_discount":   float(_q(li.get("discount"))),
            "qoyod_payload_tax_percent": float(_q(li.get("tax_percent"))),
        })
    return out


def _dry_run_single_row(row: dict) -> dict:
    """Classify + simulate + propose adjustment for one inbox row.
    Returns a structured `result` even for ineligible rows so the
    operator can see WHY a row was skipped."""
    classified = _classify_row(row)
    payload_lines = _extract_payload_line_items(row)

    eligible, skip_reason = _row_eligible(classified, payload_lines)

    salla_total = _q(classified.get("salla_total"))
    qoyod_total = classified.get("qoyod_invoice_total")
    sim_before, sim_lines = simulate_invoice(payload_lines)

    base = {
        "row_id":          classified.get("row_id"),
        "order_id":        classified.get("order_id"),
        "order_number":    classified.get("order_number"),
        "bucket":          classified.get("bucket"),
        "severity":        classified.get("severity"),
        "salla_total":     float(salla_total),
        "reported_qoyod_total": qoyod_total,
        "simulated_qoyod_invoice_total": float(sim_before),
        "simulated_minus_salla":         float(sim_before - salla_total),
        "simulated_minus_reported_qoyod":
            (float(sim_before - _q(qoyod_total))
             if qoyod_total is not None else None),
        "payload_columns": _build_payload_columns(row),
        "simulated_lines": [
            {"line_net":   float(li["line_net"]),
             "line_gross": float(li["line_gross"])}
            for li in sim_lines
        ],
        "eligible":        eligible,
        "skip_reason":     skip_reason,
    }

    if not eligible:
        base["adjustment"] = None
        base["outcome"]    = "skipped"
        return base

    adj = attempt_adjustment(payload_lines, salla_total)
    base["adjustment"] = {
        k: (float(v) if isinstance(v, Decimal) else v)
        for k, v in adj.items()
    }
    if adj.get("success"):
        base["outcome"] = ("no_adjustment_needed"
                           if adj.get("no_adjustment_needed")
                           else "adjustment_succeeded")
    else:
        base["outcome"] = f"adjustment_failed:{adj.get('reason') or 'unknown'}"
    return base


async def build_dry_run_report(
    db, *, user_id: str, limit: int = 200,
) -> dict:
    """Read-only dry-run scan. NO writes. Returns per-row simulation
    + a summary of how the proposed Phase-2 algorithm would behave
    on the operator's actual recent invoices."""
    cursor = db.integration_inbox.find(
        {"user_id": user_id,
         "pipeline_stage": {"$in": [
             "COMPLETED", "PARTIAL_FAILURE",
             "INVOICE_PAYMENT_CREATED", "INVOICE_CREATED",
         ]}},
        {"_id": 0,
         "id": 1, "trace_id": 1, "salla_order_id": 1,
         "pipeline_stage": 1, "pipeline_outcome": 1,
         "qoyod_invoice_id": 1, "qoyod_invoice_payment_id": 1,
         "qoyod_responses.invoice.body": 1,
         "qoyod_responses.invoice_payment.body": 1,
         "qoyod_payloads.invoice": 1,
         "qoyod_payloads.invoice_payment": 1,
         "qoyod_payloads.invoice_diagnostics": 1,
         "canonical_payload.total_amount":    1,
         "canonical_payload.order_id":        1,
         "canonical_payload.order_number":    1,
         "canonical_payload.shipping_amount": 1,
         "canonical_payload.items":           1,
         },
        sort=[("received_at", -1)],
        limit=limit,
    )

    rows = []
    async for r in cursor:
        rows.append(r)

    results = [_dry_run_single_row(r) for r in rows]
    eligible_results = [r for r in results if r["eligible"]]

    # Summary slices.
    n_succeeded = sum(1 for r in eligible_results
                      if r["outcome"] == "adjustment_succeeded")
    n_no_adj    = sum(1 for r in eligible_results
                      if r["outcome"] == "no_adjustment_needed")
    n_failed    = sum(1 for r in eligible_results
                      if r["outcome"].startswith("adjustment_failed"))

    skip_histogram: dict[str, int] = {}
    for r in results:
        if not r["eligible"] and r["skip_reason"]:
            # bucket the skip reason — strip after ':' for histogram clarity
            key = r["skip_reason"].split(":")[0]
            skip_histogram[key] = skip_histogram.get(key, 0) + 1

    return {
        "ok":              True,
        "scanned_count":   len(results),
        "eligible_count":  len(eligible_results),
        "succeeded_count": n_succeeded,
        "no_adjustment_needed_count": n_no_adj,
        "failed_count":    n_failed,
        "skipped_count":   len(results) - len(eligible_results),
        "skip_histogram":  skip_histogram,
        "results":         results,
    }
