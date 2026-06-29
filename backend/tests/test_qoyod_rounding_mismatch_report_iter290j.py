"""Iter-290j-rounding-fix · Phase 1 — Classifier tests.

Pins the five-bucket classifier so a future "switch payment to قيود
total" fix can be tested against the same fixtures and we don't
regress the bucket-detection logic.

The classifier is the ONLY public surface in Phase 1 that has
behaviour to validate — `build_rounding_mismatch_report` itself is
a thin scan-and-filter wrapper around it.
"""
from __future__ import annotations

import pytest

from integrations.qoyod.rounding_mismatch_report import (
    _classify_row, build_rounding_mismatch_report,
)


def _row(**overrides) -> dict:
    """Default-good row — every test only overrides what it cares
    about, so a test for SHIPPING_ROUNDING_MISMATCH only specifies
    the shipping-line drift."""
    return {
        "id":        "row-1",
        "trace_id":  "t-1",
        "salla_order_id": "ORD-1",
        "pipeline_stage": "COMPLETED",
        "qoyod_invoice_id":         "63",
        "qoyod_invoice_payment_id": "51",
        "canonical_payload": {
            "order_id":     "ORD-1",
            "order_number": "ORD-1",
            "total_amount": 312.47,
        },
        "qoyod_payloads": {
            "invoice_payment": {"invoice_payment": {"amount": 312.47}},
            "invoice_diagnostics": {
                "expected_qoyod_total": 312.47,
                "line_diagnostics": [
                    {"sku": "AMS-1",      "salla_total": 286.47,
                     "computed_qoyod_gross": 286.47},
                    {"sku": "_SHIPPING_", "salla_total": 26.00,
                     "computed_qoyod_gross": 26.00},
                ],
            },
        },
        "qoyod_responses": {
            "invoice":         {"body": {"invoice": {"total": 312.47}}},
            "invoice_payment": {"body": {"invoice_payment": {"amount": 312.47}}},
        },
        **overrides,
    }


# ─── NO_MISMATCH (the report filters this out) ───────────────────────
def test_classifier_no_mismatch_when_all_totals_align():
    out = _classify_row(_row())
    assert out["bucket"]        == "NO_MISMATCH"
    assert out["invoice_diff"]  == 0.0
    assert out["payment_diff"]  == 0.0


# ─── PAYMENT_MISMATCH_ONLY ───────────────────────────────────────────
def test_classifier_payment_mismatch_only_when_invoice_total_ties_but_payment_drifts():
    """قيود's invoice total matches Salla — but our payment was 1
    halala short. The fix is to send قيود's invoice total as the
    payment amount."""
    row = _row()
    row["qoyod_responses"]["invoice_payment"]["body"]["invoice_payment"]["amount"] = 312.46
    row["qoyod_payloads"]["invoice_payment"]["invoice_payment"]["amount"] = 312.46
    out = _classify_row(row)
    assert out["bucket"]            == "PAYMENT_MISMATCH_ONLY"
    assert out["invoice_diff"]      == 0.0
    assert out["payment_diff"]      == pytest.approx(-0.01, abs=1e-9)
    assert out["payment_amount_sent"] == 312.46
    assert out["qoyod_invoice_total"] == 312.47


# ─── SHIPPING_ROUNDING_MISMATCH ──────────────────────────────────────
def test_classifier_shipping_drift_when_only_shipping_line_diverges():
    row = _row()
    # قيود's invoice total drifted up by 1 halala — all of it
    # originates on the shipping line.
    row["qoyod_responses"]["invoice"]["body"]["invoice"]["total"] = 312.48
    row["qoyod_payloads"]["invoice_diagnostics"]["line_diagnostics"][1][
        "computed_qoyod_gross"] = 26.01
    out = _classify_row(row)
    assert out["bucket"]       == "SHIPPING_ROUNDING_MISMATCH"
    assert out["invoice_diff"] == pytest.approx(0.01, abs=1e-9)


# ─── DISCOUNT_ALLOCATION_MISMATCH ────────────────────────────────────
def test_classifier_discount_allocation_when_one_product_line_carries_diff():
    """A single product line carries the entire gap — typical pattern
    when Mezan's per-line discount rounded a hair differently from
    قيود's recomputation."""
    row = _row()
    row["qoyod_responses"]["invoice"]["body"]["invoice"]["total"] = 312.46
    row["qoyod_payloads"]["invoice_diagnostics"]["line_diagnostics"][0][
        "computed_qoyod_gross"] = 286.46
    out = _classify_row(row)
    assert out["bucket"]       == "DISCOUNT_ALLOCATION_MISMATCH"
    assert "AMS-1" in (out["rationale"] or "")


# ─── MULTI_LINE_CUMULATIVE_ROUNDING ──────────────────────────────────
def test_classifier_multi_line_cumulative_when_each_line_drifts_a_little():
    """Two or more lines each carry a tiny per-line gap; the totals
    diverge but no single line is the culprit."""
    row = _row()
    # Salla total stays at 312.47; قيود says 312.48; both lines drift
    # by half a halala each (above LINE_EPS but below the per-line EPS).
    row["canonical_payload"]["total_amount"] = 312.47
    row["qoyod_responses"]["invoice"]["body"]["invoice"]["total"] = 312.48
    row["qoyod_payloads"]["invoice_diagnostics"]["line_diagnostics"] = [
        {"sku": "AMS-A", "salla_total": 100.00, "computed_qoyod_gross": 100.005},
        {"sku": "AMS-B", "salla_total": 100.00, "computed_qoyod_gross": 100.005},
        {"sku": "_SHIPPING_", "salla_total": 112.47, "computed_qoyod_gross": 112.47},
    ]
    out = _classify_row(row)
    assert out["bucket"] == "MULTI_LINE_CUMULATIVE_ROUNDING"
    assert "2" in (out["rationale"] or "") or "3" in (out["rationale"] or "")


# ─── INVOICE_TOTAL_ROUNDING_MISMATCH catch-all ───────────────────────
def test_classifier_invoice_total_catch_all_when_no_specific_line_culprit():
    row = _row()
    # Invoice total drifted but the line diagnostics block is empty —
    # so the classifier can't pin it on any specific line.
    row["qoyod_responses"]["invoice"]["body"]["invoice"]["total"] = 312.48
    row["qoyod_payloads"]["invoice_diagnostics"]["line_diagnostics"] = []
    out = _classify_row(row)
    assert out["bucket"] == "INVOICE_TOTAL_ROUNDING_MISMATCH"


# ─── INSUFFICIENT_DATA when Qoyod response wasn't captured ───────────
def test_classifier_insufficient_data_when_no_qoyod_invoice_response():
    row = _row()
    row["qoyod_responses"] = {}
    row["qoyod_payloads"]["invoice_payment"] = {}
    out = _classify_row(row)
    assert out["bucket"] == "INSUFFICIENT_DATA"


# ─── Tolerance — half a halala counts as zero ────────────────────────
def test_classifier_treats_half_halala_drifts_as_no_mismatch():
    """0.005 SAR is the threshold — anything strictly above counts."""
    row = _row()
    row["qoyod_responses"]["invoice"]["body"]["invoice"]["total"] = 312.473
    row["qoyod_responses"]["invoice_payment"]["body"]["invoice_payment"][
        "amount"] = 312.473
    out = _classify_row(row)
    assert out["bucket"] == "NO_MISMATCH"


# ─── End-to-end report scan filters out clean rows ───────────────────
class _FakeInboxCol:
    def __init__(self, rows):
        self.rows = rows

    def find(self, query, projection=None, sort=None, limit=None):
        rows = list(self.rows)
        return _AsyncIter(rows)


class _AsyncIter:
    def __init__(self, rows):
        self.rows = list(rows)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self.rows:
            raise StopAsyncIteration
        return self.rows.pop(0)


class _FakeDB:
    def __init__(self, rows):
        self.integration_inbox = _FakeInboxCol(rows)


@pytest.mark.asyncio
async def test_report_returns_only_mismatch_rows_plus_histogram():
    """Clean rows are filtered out of `rows`, but they still count
    toward the bucket histogram so the operator can see what
    fraction of the scanned set was healthy."""
    clean_row = _row(id="clean-1")
    drift_row = _row(id="drift-1")
    drift_row["qoyod_responses"]["invoice_payment"][
        "body"]["invoice_payment"]["amount"] = 312.46
    drift_row["qoyod_payloads"]["invoice_payment"][
        "invoice_payment"]["amount"] = 312.46

    db = _FakeDB([clean_row, drift_row])
    out = await build_rounding_mismatch_report(
        db, user_id="tenant-a", limit=100)
    assert out["ok"]              is True
    assert out["scanned_count"]   == 2
    assert out["mismatch_count"]  == 1
    assert out["by_bucket"]["NO_MISMATCH"]            == 1
    assert out["by_bucket"]["PAYMENT_MISMATCH_ONLY"]  == 1
    # Only the drift row surfaces in `rows`.
    assert {r["row_id"] for r in out["rows"]} == {"drift-1"}
