"""Iter-290j-rounding-fix · Phase 1.5 — Classifier tests.

Pins the bucket / severity / data-gap classifier so a future "switch
payment to قيود total" fix can be tested against the same fixtures
and we don't regress the detection logic.

What Phase 1.5 added on top of Phase 1
──────────────────────────────────────
  • New bucket: QOYOD_SERVER_SIDE_ROUNDING — replaces the old
    catch-all for the case where ميزان's expected total matched
    Salla but قيود recomputed differently.
  • Severity tag (MINOR_ROUNDING / MODERATE_DRIFT / MATERIAL_MISMATCH
    / UNKNOWN) so halala drift and multi-SAR mismatches are no
    longer lumped together.
  • `data_gaps[]` for INSUFFICIENT_DATA rows so the operator can
    see WHICH telemetry slice is missing.
  • Richer `lines[]` array fusing canonical items + diagnostics.
  • Per-row `summary{}` block with primary_cause.
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
    assert out["severity"]      == "MINOR_ROUNDING"  # diff = 0.0 → minor
    assert out["invoice_diff"]  == 0.0
    assert out["payment_diff"]  == 0.0
    assert out["summary"]["primary_cause"] == "none"


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
    assert out["summary"]["primary_cause"] == "payment_only"


# ─── SHIPPING_ROUNDING_MISMATCH ──────────────────────────────────────
def test_classifier_shipping_drift_when_only_shipping_line_diverges():
    row = _row()
    row["qoyod_responses"]["invoice"]["body"]["invoice"]["total"] = 312.48
    row["qoyod_payloads"]["invoice_diagnostics"]["line_diagnostics"][1][
        "computed_qoyod_gross"] = 26.01
    out = _classify_row(row)
    assert out["bucket"]       == "SHIPPING_ROUNDING_MISMATCH"
    assert out["invoice_diff"] == pytest.approx(0.01, abs=1e-9)
    assert out["summary"]["is_shipping_cause"] is True
    assert out["summary"]["primary_cause"]    == "shipping_line"


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
    assert out["summary"]["primary_cause"]      == "single_product_line"
    assert out["summary"]["is_single_line_cause"] is True


# ─── MULTI_LINE_CUMULATIVE_ROUNDING ──────────────────────────────────
def test_classifier_multi_line_cumulative_when_each_line_drifts_a_little():
    row = _row()
    row["canonical_payload"]["total_amount"] = 312.47
    row["qoyod_responses"]["invoice"]["body"]["invoice"]["total"] = 312.48
    row["qoyod_payloads"]["invoice_diagnostics"]["line_diagnostics"] = [
        {"sku": "AMS-A", "salla_total": 100.00, "computed_qoyod_gross": 100.005},
        {"sku": "AMS-B", "salla_total": 100.00, "computed_qoyod_gross": 100.005},
        {"sku": "_SHIPPING_", "salla_total": 112.47, "computed_qoyod_gross": 112.47},
    ]
    out = _classify_row(row)
    assert out["bucket"] == "MULTI_LINE_CUMULATIVE_ROUNDING"
    assert out["summary"]["is_multi_line_cumulative"] is True
    assert out["summary"]["offender_count"] >= 2


# ─── QOYOD_SERVER_SIDE_ROUNDING — NEW in Phase 1.5 ───────────────────
def test_classifier_qoyod_server_side_when_mezan_estimate_ties_but_qoyod_drifts():
    """ميزان's pre-POST estimate matched Salla exactly, but قيود's
    POST-POST total drifted — this is قيود's own rounding logic
    diverging from ours. Was previously misclassified as the catch-all
    INVOICE_TOTAL_ROUNDING_MISMATCH bucket."""
    row = _row()
    # Mezan thinks it matches Salla (expected = 312.47, lines tie),
    # but قيود's response says 312.48.
    row["qoyod_responses"]["invoice"]["body"]["invoice"]["total"] = 312.48
    # diagnostics stay clean: expected_qoyod_total = 312.47, lines tie.
    out = _classify_row(row)
    assert out["bucket"] == "QOYOD_SERVER_SIDE_ROUNDING"
    assert out["summary"]["primary_cause"] == "qoyod_server_rounding"
    assert out["severity"] == "MINOR_ROUNDING"


# ─── INVOICE_TOTAL_ROUNDING_MISMATCH catch-all ───────────────────────
def test_classifier_invoice_total_catch_all_when_diagnostics_block_missing():
    """Drift exists, but we have NO diagnostics AND Mezan's expected
    total is missing — so we can't even decide if it's قيود-side
    rounding. True catch-all."""
    row = _row()
    row["qoyod_responses"]["invoice"]["body"]["invoice"]["total"] = 312.48
    row["qoyod_payloads"]["invoice_diagnostics"] = {}
    out = _classify_row(row)
    assert out["bucket"] == "INVOICE_TOTAL_ROUNDING_MISMATCH"


# ─── INSUFFICIENT_DATA when قيود response wasn't captured ────────────
def test_classifier_insufficient_data_when_no_qoyod_invoice_response():
    row = _row()
    row["qoyod_responses"] = {}
    row["qoyod_payloads"]["invoice_payment"] = {}
    out = _classify_row(row)
    assert out["bucket"] == "INSUFFICIENT_DATA"
    assert "no_invoice_response" in out["data_gaps"]
    assert "no_payment_response" in out["data_gaps"]
    assert out["summary"]["primary_cause"] == "insufficient_data"


def test_classifier_data_gaps_flags_pre_logging_row():
    """Old rows that have a قيود invoice id on file but no body and
    no diagnostics — typical for orders processed before we started
    logging payloads/responses. Surface this as a distinct reason."""
    row = _row()
    row["qoyod_responses"] = {}
    row["qoyod_payloads"] = {"invoice_diagnostics": {}}
    out = _classify_row(row)
    assert out["bucket"] == "INSUFFICIENT_DATA"
    assert "pre_logging_row"     in out["data_gaps"]
    assert "no_line_diagnostics" in out["data_gaps"]


def test_classifier_data_gaps_flags_missing_invoice_id():
    row = _row()
    row["qoyod_invoice_id"] = None
    row["qoyod_responses"] = {}
    row["qoyod_payloads"]["invoice_payment"] = {}
    out = _classify_row(row)
    assert out["bucket"] == "INSUFFICIENT_DATA"
    assert "no_qoyod_invoice_id" in out["data_gaps"]


# ─── Severity ────────────────────────────────────────────────────────
def test_severity_minor_rounding_when_diff_is_one_halala():
    row = _row()
    row["qoyod_responses"]["invoice"]["body"]["invoice"]["total"] = 312.48
    out = _classify_row(row)
    assert out["severity"] == "MINOR_ROUNDING"


def test_severity_material_mismatch_when_diff_is_multi_sar():
    """A 6.24 SAR drift is NOT rounding — must be separated from
    halala-scale drift so it isn't accidentally "fixed" by a payment
    override patch."""
    row = _row()
    row["qoyod_responses"]["invoice"]["body"]["invoice"]["total"] = 318.71
    out = _classify_row(row)
    assert out["severity"] == "MATERIAL_MISMATCH"


def test_severity_moderate_drift_between_minor_and_material():
    row = _row()
    row["qoyod_responses"]["invoice"]["body"]["invoice"]["total"] = 312.51
    out = _classify_row(row)
    assert out["severity"] == "MODERATE_DRIFT"


# ─── Richer lines[] breakdown ────────────────────────────────────────
def test_lines_breakdown_fuses_canonical_items_with_diagnostics():
    row = _row()
    row["canonical_payload"]["items"] = [{
        "sku":             "AMS-1",
        "name":            "Camera",
        "quantity":        2,
        "unit_price":      130.00,
        "discount_amount": 0.0,
        "tax_amount":      37.37,
        "total":           286.47,
    }]
    row["canonical_payload"]["shipping_amount"] = 22.61
    out = _classify_row(row)
    product_lines = [l for l in out["lines"] if l["kind"] == "product"]
    shipping_lines = [l for l in out["lines"] if l["kind"] == "shipping"]
    assert len(product_lines) == 1
    assert product_lines[0]["sku"]               == "AMS-1"
    assert product_lines[0]["quantity"]          == 2.0
    assert product_lines[0]["unit_price"]        == 130.00
    assert product_lines[0]["tax_amount"]        == 37.37
    assert product_lines[0]["salla_target_gross"] == 286.47
    assert product_lines[0]["mezan_computed_gross"] == 286.47
    assert len(shipping_lines) == 1
    assert shipping_lines[0]["unit_price"]       == 22.61


def test_lines_breakdown_pulls_qoyod_line_gross_when_response_carries_it():
    row = _row()
    # قيود sometimes echoes the line items back — surface them.
    row["qoyod_responses"]["invoice"]["body"]["invoice"]["line_items"] = [
        {"sku": "AMS-1", "subtotal_after_taxes": 286.48},
    ]
    out = _classify_row(row)
    ams1 = next(l for l in out["lines"]
                if l["kind"] == "product" and l["sku"] == "AMS-1")
    assert ams1["qoyod_line_gross"] == 286.48


# ─── Tolerance — half a halala counts as zero ────────────────────────
def test_classifier_treats_half_halala_drifts_as_no_mismatch():
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
        return _AsyncIter(list(self.rows))


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
    # Severity histogram only counts rows that actually have a drift.
    assert "MINOR_ROUNDING" in out["by_severity"]
    assert out["by_severity"]["MINOR_ROUNDING"] == 1
    assert {r["row_id"] for r in out["rows"]} == {"drift-1"}


@pytest.mark.asyncio
async def test_report_emits_gap_reason_histogram_for_insufficient_data():
    gap_row = _row(id="gap-1")
    gap_row["qoyod_responses"] = {}
    gap_row["qoyod_payloads"]["invoice_payment"] = {}
    db = _FakeDB([gap_row])
    out = await build_rounding_mismatch_report(
        db, user_id="tenant-a", limit=100)
    assert out["by_bucket"]["INSUFFICIENT_DATA"] == 1
    assert out["by_gap_reason"].get("no_invoice_response", 0) >= 1
    assert out["by_gap_reason"].get("no_payment_response", 0) >= 1
