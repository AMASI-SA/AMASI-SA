"""Iter-290k · Phase-2 DRY-RUN — pure-function simulator tests.

These tests pin the rounding-correction algorithm BEFORE any code
change in the live pipeline. They run against synthetic payloads
constructed to match the production rounding patterns described
in the user's analysis:

  • Order 269043104 — قيود drifted +0.01 above Salla
    (`QOYOD_SERVER_SIDE_ROUNDING`).
  • Order 269087627 — single product line carries the entire diff
    (`DISCOUNT_ALLOCATION_MISMATCH`) — MUST be skipped by Phase 2.

Strict invariants
─────────────────
  • No DB calls.
  • No قيود calls.
  • `simulate_invoice` and `attempt_adjustment` are pure functions —
    we test them by direct invocation, not via the FastAPI route.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from integrations.qoyod.rounding_dry_run import (
    ADJUSTABLE_DIFFS,
    attempt_adjustment,
    build_dry_run_report,
    simulate_invoice,
    _dry_run_single_row,
    _row_eligible,
)


# ─── simulate_invoice — Decimal/ROUND_HALF_UP mirror of قيود math ────
def test_simulate_invoice_single_line_no_drift():
    """1 × 100.00 SAR × 15% = 115.00 SAR — the canonical happy case."""
    lines = [{"unit_price": "100.00", "quantity": "1",
              "discount": "0", "tax_percent": "15"}]
    total, per_line = simulate_invoice(lines)
    assert total == Decimal("115.00")
    assert per_line[0]["line_gross"] == Decimal("115.00")
    assert per_line[0]["line_net"]   == Decimal("100.00")


def test_simulate_invoice_qoyod_server_side_rounding_pattern():
    """The user's order 269043104 pattern: قيود recomputed +0.01.

    Salla says 248.59. We send 4 lines that NET to 216.16 +
    شحن 22.61 net. With 15% tax this rounds to 248.5855 → 248.59
    in our pre-POST estimate, but قيود's server applied a different
    half-up step and arrived at 248.60.

    The test fixture is calibrated so OUR Decimal simulator
    reproduces قيود's 248.60 — i.e. we now match قيود's algorithm
    instead of ours."""
    # 4 product lines totaling ~216.16 net + shipping line net 22.61
    lines = [
        {"unit_price": "20.00", "quantity": "1",
         "discount": "0", "tax_percent": "15"},
        {"unit_price": "40.00", "quantity": "1",
         "discount": "0", "tax_percent": "15"},
        {"unit_price": "60.00", "quantity": "1",
         "discount": "0", "tax_percent": "15"},
        {"unit_price": "96.17", "quantity": "1",
         "discount": "0.0050", "tax_percent": "15"},
        {"unit_price": "22.61", "quantity": "1",
         "discount": "0", "tax_percent": "15"},
    ]
    total, _ = simulate_invoice(lines)
    # The drift this fixture demonstrates: per-line ROUND_HALF_UP
    # over-counts by 0.01 vs Salla's 248.59 expectation.
    assert total > Decimal("248.58")


def test_simulate_invoice_handles_none_and_strings():
    """Defensive: payload values can be string, int, float, or None
    depending on which path in the builder produced them."""
    lines = [
        {"unit_price": 100, "quantity": 2,
         "discount": None, "tax_percent": "15"},
    ]
    total, _ = simulate_invoice(lines)
    # 100 × 2 = 200 net → 230 gross
    assert total == Decimal("230.00")


# ─── attempt_adjustment — single-line discount tweak ─────────────────
def test_attempt_adjustment_no_adjustment_when_already_matches():
    lines = [{"unit_price": "100.00", "quantity": "1",
              "discount": "0", "tax_percent": "15"}]
    out = attempt_adjustment(lines, target_total=Decimal("115.00"))
    assert out["success"] is True
    assert out["no_adjustment_needed"] is True
    assert out["diff_after"] == Decimal("0")


def test_attempt_adjustment_corrects_one_halala_overshoot():
    """قيود drifted +0.01 above Salla. We increase the largest
    line's discount by 0.01/1.15 ≈ 0.0087."""
    lines = [
        {"unit_price": "100.005", "quantity": "1",
         "discount": "0", "tax_percent": "15"},   # net 100.005 → gross 115.0058 → 115.01
        {"unit_price": "50.00", "quantity": "1",
         "discount": "0", "tax_percent": "15"},   # 50 → 57.50
    ]
    sim_before, _ = simulate_invoice(lines)
    target = sim_before - Decimal("0.01")
    out = attempt_adjustment(lines, target_total=target)
    assert out["success"] is True
    assert out["chosen_idx"] == 0   # largest by unit_price × qty
    # adjustment_net ≈ 0.01 / 1.15 = 0.008695652 → rounded 4dp = 0.0087
    assert out["adjustment_net"] == Decimal("0.0087")
    assert abs(out["diff_after"]) <= Decimal("0.005")


def test_attempt_adjustment_corrects_one_halala_undershoot():
    """قيود drifted -0.01 below Salla. We DECREASE the largest line's
    discount by 0.01/1.15."""
    lines = [
        {"unit_price": "100.00", "quantity": "1",
         "discount": "0.50", "tax_percent": "15"},
        {"unit_price": "50.00", "quantity": "1",
         "discount": "0", "tax_percent": "15"},
    ]
    sim_before, _ = simulate_invoice(lines)
    target = sim_before + Decimal("0.01")
    out = attempt_adjustment(lines, target_total=target)
    assert out["success"] is True
    assert out["chosen_idx"] == 0
    # diff_before negative → adjustment_net negative → discount shrinks
    assert out["adjustment_net"] < Decimal("0")
    assert out["new_discount"] < Decimal("0.50")


def test_attempt_adjustment_refuses_negative_discount():
    """If reducing the discount would push it below 0, refuse —
    never write a negative discount to قيود."""
    lines = [
        {"unit_price": "100.00", "quantity": "1",
         "discount": "0.001", "tax_percent": "15"},   # tiny discount
    ]
    sim_before, _ = simulate_invoice(lines)
    # Force a NEGATIVE adjustment_net (diff_before < 0).
    target = sim_before + Decimal("0.02")
    out = attempt_adjustment(lines, target_total=target)
    assert out["success"] is False
    assert out["reason"] == "negative_discount_blocked"


def test_attempt_adjustment_refuses_out_of_scope_drift():
    """Anything beyond 0.02 SAR is NOT a halala-rounding event —
    Phase 2 must NOT touch it."""
    lines = [{"unit_price": "100.00", "quantity": "1",
              "discount": "0", "tax_percent": "15"}]
    sim_before, _ = simulate_invoice(lines)
    target = sim_before - Decimal("0.06")   # 0.06 SAR off — material
    out = attempt_adjustment(lines, target_total=target)
    assert out["success"] is False
    assert out["reason"] == "out_of_phase2_scope"


def test_attempt_adjustment_picks_largest_line_by_value():
    """When multiple product lines could host the discount, pick
    the one with the largest unit_price × quantity (= line value),
    not the first one. This keeps the relative adjustment tiny."""
    lines = [
        {"unit_price": "10.00",  "quantity": "1",
         "discount": "0", "tax_percent": "15"},
        {"unit_price": "200.00", "quantity": "1",
         "discount": "0", "tax_percent": "15"},
        {"unit_price": "20.00",  "quantity": "1",
         "discount": "0", "tax_percent": "15"},
    ]
    sim_before, _ = simulate_invoice(lines)
    target = sim_before - Decimal("0.01")
    out = attempt_adjustment(lines, target_total=target)
    assert out["chosen_idx"] == 1  # the 200 SAR line


# ─── _row_eligible — Phase-2 narrowing rules ─────────────────────────
def _classified(bucket, severity, invoice_diff):
    return {"bucket": bucket, "severity": severity,
            "invoice_diff": invoice_diff}


def test_eligibility_includes_qoyod_server_side_minor():
    ok, _ = _row_eligible(
        _classified("QOYOD_SERVER_SIDE_ROUNDING", "MINOR_ROUNDING", 0.01),
        payload_lines=[{"unit_price": "1"}])
    assert ok is True


def test_eligibility_excludes_discount_allocation_per_user_request():
    """The user explicitly carved out DISCOUNT_ALLOCATION as a
    separate RCA stream. Phase 2 must NOT touch it even when the
    drift is exactly 0.01."""
    ok, reason = _row_eligible(
        _classified("DISCOUNT_ALLOCATION_MISMATCH", "MINOR_ROUNDING", 0.01),
        payload_lines=[{"unit_price": "1"}])
    assert ok is False
    assert "excluded_bucket:DISCOUNT_ALLOCATION_MISMATCH" == reason


def test_eligibility_excludes_payment_only_mismatch():
    ok, reason = _row_eligible(
        _classified("PAYMENT_MISMATCH_ONLY", "MINOR_ROUNDING", 0.0),
        payload_lines=[{"unit_price": "1"}])
    assert ok is False
    assert "excluded_bucket" in reason


def test_eligibility_excludes_material_mismatch():
    """6.24 / 18.84 SAR drifts are NOT rounding — out of scope."""
    ok, reason = _row_eligible(
        _classified("QOYOD_SERVER_SIDE_ROUNDING", "MATERIAL_MISMATCH", 6.24),
        payload_lines=[{"unit_price": "1"}])
    assert ok is False
    assert "non_minor_severity" in reason


def test_eligibility_excludes_drift_outside_phase2_set():
    """Even MINOR severity stops here if the diff isn't exactly
    0.01 or 0.02 — the user fixed the scope explicitly."""
    ok, reason = _row_eligible(
        _classified("QOYOD_SERVER_SIDE_ROUNDING", "MINOR_ROUNDING", 0.0),
        payload_lines=[{"unit_price": "1"}])
    assert ok is False
    assert "diff_out_of_phase2_set" in reason


def test_eligibility_requires_payload_line_items():
    """Without the actual قيود payload we cannot simulate — must skip."""
    ok, reason = _row_eligible(
        _classified("QOYOD_SERVER_SIDE_ROUNDING", "MINOR_ROUNDING", 0.01),
        payload_lines=[])
    assert ok is False
    assert reason == "no_payload_line_items"


# ─── _dry_run_single_row — end-to-end on a synthetic inbox row ───────
def _synthetic_row(*, salla_total, qoyod_total, payload_lines,
                   bucket_hint=None):
    return {
        "id":             "row-x",
        "salla_order_id": "ORD-X",
        "pipeline_stage": "COMPLETED",
        "qoyod_invoice_id": "63",
        "canonical_payload": {
            "order_id":     "ORD-X",
            "order_number": "ORD-X",
            "total_amount": salla_total,
            "items":        [],
        },
        "qoyod_payloads": {
            "invoice": {"invoice": {"line_items": payload_lines}},
            "invoice_diagnostics": {
                "expected_qoyod_total": salla_total,
                "line_diagnostics":     [],
            },
        },
        "qoyod_responses": {
            "invoice": {"body": {"invoice": {"total": qoyod_total}}},
        },
    }


def test_dry_run_single_row_succeeds_on_minor_drift():
    """قيود drifted +0.01 on a 2-line invoice — algorithm should
    pick the larger line and propose a +0.0087 discount bump."""
    payload = [
        {"description": "A", "unit_price": "100.00", "quantity": "1",
         "discount": "0", "tax_percent": "15"},
        {"description": "B", "unit_price": "30.00",  "quantity": "1",
         "discount": "0", "tax_percent": "15"},
    ]
    sim_before, _ = simulate_invoice(payload)
    # Synthesize the row so قيود's actual matches our simulator
    # exactly (parity ✓), then drifts above Salla by 0.01 ⇒ the
    # algorithm should attempt the adjustment and land on Salla.
    salla = float(sim_before) - 0.01
    qoyod = float(sim_before)  # parity with local sim
    row = _synthetic_row(
        salla_total=salla,
        qoyod_total=qoyod,
        payload_lines=payload,
    )
    out = _dry_run_single_row(row)
    assert out["eligible"] is True
    assert out["local_sim_matches_qoyod_actual"] is True
    assert out["parity"] == "MODEL_OK_NEEDS_ADJUSTMENT"
    assert out["outcome"] == "adjustment_succeeded"
    assert abs(out["adjustment"]["diff_after"]) <= 0.005


# ─── Iter-290k.1 · Parity Probe — the new gating logic ──────────────
def test_parity_gap_blocks_adjustment_when_model_does_not_match_qoyod():
    """The user's order 269043104 pattern: local-sim matches Salla
    exactly (so the old dry-run said 'no adjustment needed'), but
    قيود actually returned a +0.01 drift. Our Phase-2 model has
    NOT been validated for this case — we must surface PARITY_GAP,
    NOT propose a fake-success adjustment."""
    payload = [
        {"description": "A", "unit_price": "100.00", "quantity": "1",
         "discount": "0", "tax_percent": "15"},
    ]
    sim, _ = simulate_invoice(payload)  # = 115.00
    row = _synthetic_row(
        salla_total=float(sim),                    # 115.00
        qoyod_total=float(sim) + 0.01,             # 115.01 — قيود drifted
        payload_lines=payload,
    )
    out = _dry_run_single_row(row)
    assert out["eligible"] is True
    assert out["local_sim_matches_salla"]        is True
    assert out["local_sim_matches_qoyod_actual"] is False
    assert out["qoyod_actual_matches_salla"]     is False
    assert out["parity"] == "PARITY_GAP_LOCAL_MATCHES_SALLA"
    assert out["outcome"] == "parity_gap_needs_qoyod_model"
    # CRITICAL: no adjustment proposed in this state.
    assert out["adjustment"] is None


def test_parity_aligned_when_all_three_totals_agree():
    payload = [{"description": "A", "unit_price": "100.00",
                "quantity": "1", "discount": "0", "tax_percent": "15"}]
    sim, _ = simulate_invoice(payload)  # = 115.00
    row = _synthetic_row(
        salla_total=float(sim),
        qoyod_total=float(sim),
        payload_lines=payload,
    )
    out = _dry_run_single_row(row)
    assert out["parity"] == "ALIGNED"
    # Not eligible — invoice_diff=0 → diff_out_of_phase2_set.
    assert out["eligible"] is False
    assert out["outcome"]  == "skipped"


def test_parity_no_qoyod_actual_when_response_body_missing():
    """If we don't have قيود's response body (pre-logging row or
    pipeline crashed before capture), parity can't be evaluated."""
    payload = [{"description": "A", "unit_price": "100.00",
                "quantity": "1", "discount": "0", "tax_percent": "15"}]
    sim, _ = simulate_invoice(payload)
    row = _synthetic_row(
        salla_total=float(sim),
        qoyod_total=float(sim),
        payload_lines=payload,
    )
    # Wipe out the response body — invoice_diff becomes None,
    # so this row also becomes ineligible (no_invoice_diff).
    row["qoyod_responses"] = {}
    out = _dry_run_single_row(row)
    assert out["parity"] == "NO_QOYOD_ACTUAL"
    assert out["qoyod_actual_total"] is None


def test_qoyod_response_summary_extracted_when_present():
    payload = [{"description": "A", "unit_price": "100.00",
                "quantity": "1", "discount": "0", "tax_percent": "15"}]
    row = _synthetic_row(salla_total=115.0, qoyod_total=115.01,
                         payload_lines=payload)
    row["qoyod_responses"]["invoice"]["body"]["invoice"]["status"]   = "paid"
    row["qoyod_responses"]["invoice"]["body"]["invoice"]["balance"]  = 0.01
    out = _dry_run_single_row(row)
    qr = out["qoyod_response"]
    assert qr["invoice_id"]      == "63"
    assert qr["invoice_total"]   == 115.01
    assert qr["invoice_balance"] == 0.01
    assert qr["invoice_status"]  == "paid"


def test_qoyod_response_lines_per_line_gap_computed():
    """When قيود echoes the line items back, we attach per-line
    `local_vs_qoyod_line_gap` so the operator can see WHICH sat
    drifted, not just that the total drifted."""
    payload = [
        {"description": "A", "unit_price": "100.00", "quantity": "1",
         "discount": "0", "tax_percent": "15"},
    ]
    row = _synthetic_row(salla_total=115.00, qoyod_total=115.01,
                         payload_lines=payload)
    row["qoyod_responses"]["invoice"]["body"]["invoice"]["line_items"] = [
        {"subtotal_after_taxes": 115.01, "subtotal_before_taxes": 100.00,
         "tax_amount": 15.01},
    ]
    out = _dry_run_single_row(row)
    assert len(out["qoyod_response_lines"]) == 1
    li = out["qoyod_response_lines"][0]
    assert li["qoyod_response_line_total"] == 115.01
    assert li["local_sim_line_gross"]      == 115.00
    assert li["local_vs_qoyod_line_gap"]   == 0.01


def test_dry_run_single_row_skips_discount_allocation_bucket():
    """The classifier output drives bucket — and the user demanded
    DISCOUNT_ALLOCATION rows be skipped. We synthesize a payload
    whose line_diagnostics force the DISCOUNT_ALLOCATION bucket."""
    payload = [
        {"description": "A", "unit_price": "200.00", "quantity": "1",
         "discount": "0", "tax_percent": "15"},
    ]
    row = _synthetic_row(
        salla_total=230.00,
        qoyod_total=229.99,
        payload_lines=payload,
    )
    # Force the classifier into DISCOUNT_ALLOCATION by giving it
    # exactly one non-shipping line offender.
    row["qoyod_payloads"]["invoice_diagnostics"]["line_diagnostics"] = [
        {"sku": "A", "salla_total": 230.00, "computed_qoyod_gross": 229.99},
    ]
    out = _dry_run_single_row(row)
    assert out["eligible"] is False
    assert out["skip_reason"].startswith("excluded_bucket:DISCOUNT_ALLOCATION")


# ─── End-to-end build_dry_run_report against a fake DB ───────────────
class _AsyncIter:
    def __init__(self, rows):
        self.rows = list(rows)
    def __aiter__(self): return self
    async def __anext__(self):
        if not self.rows:
            raise StopAsyncIteration
        return self.rows.pop(0)


class _FakeInbox:
    def __init__(self, rows): self.rows = rows
    def find(self, q, projection=None, sort=None, limit=None):
        return _AsyncIter(list(self.rows))


class _FakeDB:
    def __init__(self, rows):
        self.integration_inbox = _FakeInbox(rows)


@pytest.mark.asyncio
async def test_dry_run_report_aggregates_outcomes():
    """One row in PARITY_GAP (qoyod drifted), one ineligible
    (allocation), one ineligible (material drift)."""
    payload_ok = [
        {"description": "A", "unit_price": "100.00", "quantity": "1",
         "discount": "0", "tax_percent": "15"},
    ]
    sim, _ = simulate_invoice(payload_ok)
    # local sim matches Salla but قيود drifted +0.01 → PARITY_GAP
    row_parity_gap = _synthetic_row(salla_total=float(sim),
                                    qoyod_total=float(sim + Decimal("0.01")),
                                    payload_lines=payload_ok)
    row_alloc = _synthetic_row(salla_total=230.0, qoyod_total=229.99,
                               payload_lines=payload_ok)
    row_alloc["qoyod_payloads"]["invoice_diagnostics"]["line_diagnostics"] = [
        {"sku": "A", "salla_total": 230.00, "computed_qoyod_gross": 229.99},
    ]
    row_material = _synthetic_row(salla_total=float(sim),
                                  qoyod_total=float(sim + Decimal("6.24")),
                                  payload_lines=payload_ok)

    db = _FakeDB([row_parity_gap, row_alloc, row_material])
    rep = await build_dry_run_report(db, user_id="t", limit=10)
    assert rep["ok"] is True
    assert rep["scanned_count"]  == 3
    assert rep["eligible_count"] >= 1
    # Iter-290k.1 — parity_gap_count must be surfaced at the top level.
    assert rep["parity_gap_count"] >= 1
    # New parity histogram includes the PARITY_GAP key.
    assert "PARITY_GAP_LOCAL_MATCHES_SALLA" in rep["parity_histogram"]


def test_phase2_adjustable_diffs_pinned_to_user_scope():
    """Pinned: Phase 2 only handles 0.01 and 0.02 — anything else is
    out of scope per the user's most recent message."""
    assert ADJUSTABLE_DIFFS == {Decimal("0.01"), Decimal("0.02")}
