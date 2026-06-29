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


# ─── Iter-290k.2 — Header VAT modeling (the missing piece) ───────────
#
# قيود's invoice page displays the HEADER total, which is built as:
#
#     displayed_net_sum = round_half_up_2(Σ exact_line_net)
#     header_vat        = round_half_up_2(Σ exact_line_net × tax%/100)
#     header_total      = displayed_net_sum + header_vat
#
# This DIFFERS from the per-line-grossed sum:
#
#     line_gross_sum    = Σ round_half_up_2(exact_line_net × (1 + tax%/100))
#
# The two diverge by 0.01 whenever (Σ exact_line_net) × tax% sits on
# the half-up boundary (.005). That's the root of every PARITY_GAP
# row in production — قيود returns the header_total but our older
# simulator was computing the line_gross_sum.
def simulate_header_vat(payload_lines: list[dict]) -> dict:
    """Full قيود-side simulation including both the line-level and
    header-level VAT computations. Returns every metric the
    operator needs to reason about which constraint is satisfied.

    Notes on tax_percent
    ────────────────────
    قيود's invoice header VAT uses a SINGLE tax rate (a per-invoice
    rate, not a per-line one — قيود's UI shows one VAT column at the
    bottom). In our payloads every product line carries the same
    `tax_percent` (15 by default), so we read the rate from the
    first non-shipping line. Lines with `tax_percent=0` are still
    summed into `exact_net_sum` so the bottom-line math matches.
    """
    exact_net_sum = Decimal("0")
    line_gross_sum = Decimal("0")
    header_tax_pct: Optional[Decimal] = None

    for li in payload_lines or []:
        unit_price = _q(li.get("unit_price"))
        qty        = _q(li.get("quantity"))
        discount   = _q(li.get("discount"))
        tax_pct    = _q(li.get("tax_percent"))
        line_net   = (unit_price * qty) - discount
        exact_net_sum += line_net
        tax_factor   = Decimal("1") + (tax_pct / Decimal("100"))
        line_gross_sum += _round_half_up_2(line_net * tax_factor)
        # First non-zero rate wins — قيود uses a single header rate.
        if header_tax_pct is None and tax_pct > Decimal("0"):
            header_tax_pct = tax_pct

    if header_tax_pct is None:
        header_tax_pct = Decimal("15")  # safety default

    displayed_net_sum = _round_half_up_2(exact_net_sum)
    header_vat = _round_half_up_2(
        exact_net_sum * header_tax_pct / Decimal("100"))
    header_total = _round_half_up_2(displayed_net_sum + header_vat)

    return {
        "exact_net_sum":     exact_net_sum,
        "displayed_net_sum": displayed_net_sum,
        "header_vat":        header_vat,
        "header_total":      header_total,
        "line_gross_sum":    _round_half_up_2(line_gross_sum),
        "header_tax_percent": header_tax_pct,
    }


def attempt_header_vat_alignment(
    payload_lines: list[dict],
    salla_total: Decimal,
    *, max_diff: Decimal = Decimal("0.025"),
) -> dict:
    """Iter-290k.2 — Smart adjustment targeting BOTH:

        (a) header_total_after  == salla_total
        (b) line_gross_sum_after == salla_total

    Strategy
    ────────
    قيود's header_vat flips by 0.01 at boundaries of the form
    `exact_net × rate = .005 above an integer cent`. To pull
    header_total down by 0.01 (or up by 0.01) we must move
    exact_net_sum across the NEAREST half-up boundary — that's the
    MINIMUM adjustment needed. Smaller adjustments don't move the
    rounded VAT; larger adjustments risk also moving a line's
    individual gross (and breaking constraint b).

    The function:
      1. Picks the largest line by `unit_price × quantity`.
      2. Computes the boundary-crossing adjustment to flip header_vat.
      3. Applies it to that line's discount (rounded to 4 dp).
      4. Re-simulates and reports whether BOTH (a) and (b) landed
         within 0.005 of `salla_total`.

    Returns a dict with `before` and `after` snapshots of the full
    5-metric simulation, plus alignment flags.
    """
    before = simulate_header_vat(payload_lines)
    diff = before["header_total"] - salla_total

    out: dict = {
        "before":           {k: (float(v) if isinstance(v, Decimal) else v)
                             for k, v in before.items()},
        "salla_total":      float(salla_total),
        "header_total_diff": float(diff),
    }

    # Already aligned — nothing to do.
    if abs(diff) <= ZERO_TOL_EPS:
        out.update({
            "success":          True,
            "no_adjustment_needed": True,
            "header_aligned":   True,
            "lines_aligned":
                abs(before["line_gross_sum"] - salla_total) <= ZERO_TOL_EPS,
        })
        return out

    if abs(diff) > max_diff:
        out["success"] = False
        out["reason"]  = "header_diff_out_of_phase2_scope"
        return out

    if not payload_lines:
        out["success"] = False
        out["reason"]  = "no_payload_lines"
        return out

    # Boundary-crossing math. The current header_vat rounds to N. We
    # want it to round to N - diff (which is exactly 0.01 or 0.02
    # closer to salla). The new exact_net_sum must land STRICTLY
    # inside the new rounding region, just over the boundary.
    tax_factor = before["header_tax_percent"] / Decimal("100")
    target_header_vat = before["header_vat"] - diff

    if diff > Decimal("0"):
        # Need to LOWER exact_net_sum so that exact × rate is JUST
        # below (target_header_vat + 0.005). One halala below the
        # boundary is enough to flip the rounded VAT.
        boundary = (target_header_vat + Decimal("0.005")) / tax_factor
        target_exact_net = boundary - Decimal("0.00005")
    else:
        # Need to RAISE exact_net_sum to the lower boundary of the
        # new rounding region.
        boundary = (target_header_vat - Decimal("0.005")) / tax_factor
        target_exact_net = boundary

    adjustment_net_raw = before["exact_net_sum"] - target_exact_net
    adjustment_net = _round_half_up_4(adjustment_net_raw)

    # Pick the largest line by value (= unit_price × quantity).
    by_value = sorted(
        enumerate(payload_lines),
        key=lambda kv: _q(kv[1].get("unit_price")) * _q(kv[1].get("quantity")),
        reverse=True,
    )
    chosen_idx, chosen_line = by_value[0]
    current_discount = _q(chosen_line.get("discount"))
    new_discount = _round_half_up_4(current_discount + adjustment_net)

    if new_discount < Decimal("0"):
        out.update({
            "success":          False,
            "reason":           "negative_discount_blocked",
            "chosen_idx":       chosen_idx,
            "chosen_line_description": chosen_line.get("description"),
            "current_discount": float(current_discount),
            "attempted_adjustment_net": float(adjustment_net),
        })
        return out

    adjusted = list(payload_lines)
    adjusted[chosen_idx] = {**chosen_line, "discount": new_discount}
    after = simulate_header_vat(adjusted)

    header_aligned = abs(after["header_total"] - salla_total) <= ZERO_TOL_EPS
    lines_aligned  = abs(after["line_gross_sum"] - salla_total) <= ZERO_TOL_EPS

    out.update({
        "success":            header_aligned and lines_aligned,
        "header_aligned":     header_aligned,
        "lines_aligned":      lines_aligned,
        "chosen_idx":         chosen_idx,
        "chosen_line_description": chosen_line.get("description"),
        "current_discount":   float(current_discount),
        "adjustment_net":     float(adjustment_net),
        "new_discount":       float(new_discount),
        "after":              {k: (float(v) if isinstance(v, Decimal) else v)
                               for k, v in after.items()},
    })
    return out


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


# ── Iter-290k.1 · Parity Probe — read-only قيود-response extraction ──
def _safe_field(obj: dict, keys: list[str]) -> Optional[float]:
    """Return the first numeric field found among `keys`. Used to
    smooth over قيود's response key variance across API versions."""
    for k in keys:
        if obj.get(k) is not None:
            try:
                return float(obj.get(k))
            except (TypeError, ValueError):
                continue
    return None


def _extract_qoyod_invoice_inner_local(row: dict) -> dict:
    """Re-implements the lookup locally to avoid a cross-module
    import cycle with rounding_mismatch_report — same lookup logic."""
    responses = row.get("qoyod_responses") or {}
    inv = ((responses.get("invoice") or {}).get("body") or {})
    if not isinstance(inv, dict):
        return {}
    inner = inv.get("invoice") if isinstance(inv.get("invoice"), dict) else inv
    return inner if isinstance(inner, dict) else {}


def _extract_qoyod_response_summary(row: dict) -> dict:
    """Pull the قيود-side invoice metadata we have on file. The
    parity probe needs this to compare local simulation against
    قيود's ACTUAL totals — not Salla's expected totals."""
    inner = _extract_qoyod_invoice_inner_local(row)
    responses = row.get("qoyod_responses") or {}
    payment_body = ((responses.get("invoice_payment") or {}).get("body") or {})
    payment_inner = (payment_body.get("invoice_payment")
                     if isinstance(payment_body.get("invoice_payment"), dict)
                     else payment_body)

    return {
        "invoice_id":      row.get("qoyod_invoice_id"),
        "invoice_total":   _safe_field(inner, ["total", "total_amount",
                                               "amount", "amount_due"]),
        "invoice_balance": _safe_field(inner, ["balance", "remaining",
                                               "due_amount",
                                               "remaining_amount"]),
        "invoice_status":  inner.get("status"),
        "payment_amount":  (_safe_field(payment_inner, ["amount"])
                            if isinstance(payment_inner, dict) else None),
        "payment_id":      row.get("qoyod_invoice_payment_id"),
    }


def _extract_qoyod_response_lines_detailed(
    row: dict, sim_lines: list[dict],
) -> list[dict]:
    """Per-line view from قيود's response body. We map قيود's
    echoed `line_items[i]` to our simulated `sim_lines[i]` BY INDEX
    (the قيود pipeline builds both in the same order). For each
    line we compute `local_vs_qoyod_line_gap` so the operator can
    see WHERE the rounding diverges, not just the total drift."""
    inner = _extract_qoyod_invoice_inner_local(row)
    raw = inner.get("line_items") or []
    if not isinstance(raw, list):
        return []
    out: list[dict] = []
    for i, li in enumerate(raw):
        if not isinstance(li, dict):
            continue
        # قيود's API uses different field names across versions —
        # try every common shape so we don't silently drop signal.
        net   = _safe_field(li, ["subtotal_before_taxes", "subtotal",
                                 "amount_before_tax", "net",
                                 "amount", "line_net"])
        tax   = _safe_field(li, ["tax_amount", "vat_amount",
                                 "tax", "tax_value"])
        total = _safe_field(li, ["subtotal_after_taxes",
                                 "total_after_tax", "total_with_tax",
                                 "total_amount", "line_total",
                                 "gross_amount", "total"])
        sim_li = sim_lines[i] if i < len(sim_lines) else None
        sim_gross = float(sim_li["line_gross"]) if sim_li else None
        sim_net   = float(sim_li["line_net"])   if sim_li else None
        gap = None
        if total is not None and sim_gross is not None:
            gap = round(total - sim_gross, 4)
        out.append({
            "qoyod_response_line_net":   net,
            "qoyod_response_tax":        tax,
            "qoyod_response_line_total": total,
            "local_sim_line_net":        sim_net,
            "local_sim_line_gross":      sim_gross,
            "local_vs_qoyod_line_gap":   gap,
        })
    return out


def _parity_status(local_sim_matches_qoyod_actual: bool,
                   local_sim_matches_salla: bool,
                   qoyod_actual_matches_salla: bool,
                   qoyod_actual_available: bool) -> str:
    """Compact label for the parity column in the UI table."""
    if not qoyod_actual_available:
        return "NO_QOYOD_ACTUAL"
    if local_sim_matches_qoyod_actual and qoyod_actual_matches_salla:
        return "ALIGNED"
    if local_sim_matches_qoyod_actual and not qoyod_actual_matches_salla:
        return "MODEL_OK_NEEDS_ADJUSTMENT"
    if local_sim_matches_salla and not local_sim_matches_qoyod_actual:
        return "PARITY_GAP_LOCAL_MATCHES_SALLA"
    return "PARITY_GAP_MODEL_OFF"


def _dry_run_single_row(row: dict) -> dict:
    """Classify + simulate + parity-check + propose adjustment for
    one inbox row.

    Iter-290k.1 — Parity Probe
    ──────────────────────────
    Before proposing an adjustment, we now require that our local
    Decimal+ROUND_HALF_UP simulator REPRODUCES قيود's actual total
    (within 0.005). Without that parity, an adjustment is just
    guesswork — it might "succeed" in our model and still leave a
    0.01 drift in قيود.

    Outcomes
    ────────
      • skipped                          — out of Phase-2 scope.
      • no_adjustment_needed             — model & قيود & Salla all
                                            already agree.
      • parity_gap_needs_qoyod_model     — local-sim matches Salla
                                            (or doesn't) but does NOT
                                            match قيود-actual. We
                                            DO NOT propose any
                                            adjustment in this case.
      • adjustment_succeeded / failed    — only emitted AFTER parity
                                            with قيود-actual was
                                            established.
    """
    classified = _classify_row(row)
    payload_lines = _extract_payload_line_items(row)

    eligible, skip_reason = _row_eligible(classified, payload_lines)

    salla_total = _q(classified.get("salla_total"))
    qoyod_actual_raw = classified.get("qoyod_invoice_total")
    qoyod_actual = (_q(qoyod_actual_raw)
                    if qoyod_actual_raw is not None else None)
    sim_before, sim_lines = simulate_invoice(payload_lines)

    # Iter-290k.2 — Full Header VAT simulation. قيود displays the
    # header total (displayed_net + header_vat), NOT the line gross
    # sum. Comparing this against qoyod_actual closes the parity gap.
    hv_before = simulate_header_vat(payload_lines)

    # Three-way parity gauge — now compared against header_total
    # because that's what قيود returns in its invoice page.
    local_sim_matches_salla = (
        abs(hv_before["line_gross_sum"] - salla_total) <= ZERO_TOL_EPS)
    qoyod_actual_available  = qoyod_actual is not None
    local_sim_matches_qoyod_actual = (
        qoyod_actual_available
        and abs(hv_before["header_total"] - qoyod_actual) <= ZERO_TOL_EPS)
    qoyod_actual_matches_salla = (
        qoyod_actual_available
        and abs(qoyod_actual - salla_total) <= ZERO_TOL_EPS)

    parity = _parity_status(
        local_sim_matches_qoyod_actual,
        local_sim_matches_salla,
        qoyod_actual_matches_salla,
        qoyod_actual_available)

    qoyod_response = _extract_qoyod_response_summary(row)
    qoyod_response_lines = _extract_qoyod_response_lines_detailed(
        row, sim_lines)

    base = {
        "row_id":          classified.get("row_id"),
        "order_id":        classified.get("order_id"),
        "order_number":    classified.get("order_number"),
        "bucket":          classified.get("bucket"),
        "severity":        classified.get("severity"),
        "salla_total":     float(salla_total),
        "qoyod_actual_total":            (float(qoyod_actual)
                                          if qoyod_actual is not None
                                          else None),
        # Kept for backwards-compat with the v1 UI.
        "reported_qoyod_total":          qoyod_actual_raw,
        "simulated_qoyod_invoice_total": float(sim_before),
        "simulated_minus_salla":         float(sim_before - salla_total),
        "simulated_minus_qoyod_actual":  (
            float(sim_before - qoyod_actual)
            if qoyod_actual is not None else None),
        # Iter-290k.2 — full Header VAT model surfaced to the UI.
        "header_vat_before": {k: (float(v) if isinstance(v, Decimal) else v)
                              for k, v in hv_before.items()},
        # Iter-290k.1 — three-way parity flags surfaced to the UI.
        "local_sim_matches_salla":        local_sim_matches_salla,
        "local_sim_matches_qoyod_actual": local_sim_matches_qoyod_actual,
        "qoyod_actual_matches_salla":     qoyod_actual_matches_salla,
        "parity":                         parity,
        "payload_columns":     _build_payload_columns(row),
        "simulated_lines":     [
            {"line_net":   float(li["line_net"]),
             "line_gross": float(li["line_gross"])}
            for li in sim_lines
        ],
        "qoyod_response":      qoyod_response,
        "qoyod_response_lines": qoyod_response_lines,
        "eligible":            eligible,
        "skip_reason":         skip_reason,
    }

    if not eligible:
        base["adjustment"] = None
        base["header_vat_alignment"] = None
        base["outcome"]    = "skipped"
        return base

    # ── PARITY GATE (Iter-290k.1, refined by Iter-290k.2) ─────────
    # Now we compare header_total (not line_gross_sum) to قيود's
    # actual. If قيود's behavior is the half-up header VAT model,
    # this comparison should pass for the 0.01-drift cases.
    if qoyod_actual_available and not local_sim_matches_qoyod_actual:
        base["adjustment"] = None
        base["header_vat_alignment"] = None
        base["outcome"]    = "parity_gap_needs_qoyod_model"
        return base

    # ── Iter-290k.2 — Header VAT Alignment Simulation ─────────────
    # Parity confirmed (or no قيود-actual to compare): run the
    # smart discount adjustment that targets BOTH header_total ==
    # salla_total AND line_gross_sum == salla_total.
    align = attempt_header_vat_alignment(payload_lines, salla_total)
    base["header_vat_alignment"] = align

    # Backwards-compat — keep the legacy `adjustment` field populated
    # too so the older UI columns still render.
    legacy_adj = attempt_adjustment(payload_lines, salla_total)
    base["adjustment"] = {
        k: (float(v) if isinstance(v, Decimal) else v)
        for k, v in legacy_adj.items()
    }

    if align.get("success"):
        base["outcome"] = ("no_adjustment_needed"
                           if align.get("no_adjustment_needed")
                           else "adjustment_succeeded")
    elif align.get("header_aligned") and not align.get("lines_aligned"):
        # The smart adjustment landed the header on salla but a per-line
        # gross rounded the other way. Surface this distinctly — it's
        # a different remediation path than "model unknown".
        base["outcome"] = "header_aligned_but_lines_drifted"
    elif align.get("reason"):
        base["outcome"] = f"adjustment_failed:{align.get('reason')}"
    else:
        base["outcome"] = "adjustment_failed:unknown"
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
    # Iter-290k.1 — count rows where the local simulator failed to
    # reproduce قيود's actual total. These rows BLOCK any Phase-2
    # implementation until we get the simulator parity right.
    n_parity_gap = sum(1 for r in eligible_results
                       if r["outcome"] == "parity_gap_needs_qoyod_model")

    skip_histogram: dict[str, int] = {}
    for r in results:
        if not r["eligible"] and r["skip_reason"]:
            # bucket the skip reason — strip after ':' for histogram clarity
            key = r["skip_reason"].split(":")[0]
            skip_histogram[key] = skip_histogram.get(key, 0) + 1

    # Iter-290k.1 — parity histogram across ALL rows (eligible or not)
    # so the operator can see how often our simulator agrees with قيود
    # in absolute terms, not just within Phase-2 scope.
    parity_histogram: dict[str, int] = {}
    for r in results:
        key = r.get("parity") or "UNKNOWN"
        parity_histogram[key] = parity_histogram.get(key, 0) + 1

    return {
        "ok":              True,
        "scanned_count":   len(results),
        "eligible_count":  len(eligible_results),
        "succeeded_count": n_succeeded,
        "no_adjustment_needed_count": n_no_adj,
        "failed_count":    n_failed,
        "parity_gap_count": n_parity_gap,
        "skipped_count":   len(results) - len(eligible_results),
        "skip_histogram":  skip_histogram,
        "parity_histogram": parity_histogram,
        "results":         results,
    }
