"""Iter-2026-02.rev24 — Read-only build & row diagnostics.

Contract that the operator relies on to prove production is on the
correct build BEFORE running another live SAS test.
"""
from __future__ import annotations

import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, "/app/backend")


def test_all_five_markers_present_in_running_pipeline():
    """The loaded pipeline.py module MUST carry all Rev16/17/20/21
    markers. If any is missing, the SAS gate cannot bypass dry-run."""
    from integrations.qoyod.sas_build_diagnostics import (
        REQUIRED_MARKERS, build_diagnostics_report,
    )

    r = build_diagnostics_report()

    assert r["pipeline_module"]["loaded"] is True
    assert r["marker_check"]["all_markers_present"] is True
    for mid, needle in REQUIRED_MARKERS.items():
        m = r["marker_check"]["markers"][mid]
        assert m["present"] is True, (
            f"marker {mid!r} missing (needle={needle!r})")
        assert m["count"] >= 1
    assert r["acceptance"]["code_matches_expected"] is True


def test_report_never_leaks_secret_values():
    """The env_flags section MUST only contain booleans — never the raw
    values. Any regression here is a security leak."""
    from integrations.qoyod.sas_build_diagnostics import (
        build_diagnostics_report,
    )
    r = build_diagnostics_report()
    for k, v in r["env_flags"].items():
        assert isinstance(v, bool), (
            f"env_flags.{k} leaked non-boolean: {type(v).__name__}")


@pytest.mark.asyncio
async def test_row_diagnostics_returns_gate_and_diagnosis():
    """The row endpoint MUST surface:
      • sas_gate_persisted (bool)
      • sas_gate_eligible / sas_gate_reason
      • qoyod_invoice_id_is_dry / is_real
    """
    from integrations.qoyod.sas_build_diagnostics import row_diagnostics

    fake_row = {
        "id": "row-1",
        "trace_id": "trace-abc",
        "pipeline_stage": "INVOICE_CREATED",
        "selective_auto_send_gate": {
            "eligible": True, "reason": None,
        },
        "qoyod_customer_id":       "DRY:contact:aaaa1111",
        "qoyod_invoice_id":        "DRY:invoice:bbbb2222",
        "qoyod_invoice_payment_id": None,
        "canonical_payload": {
            "payment_method": "tabby_installment",
            "order_status":   "completed",
        },
        "stage_history": [{"stage": "NORMALIZED"} for _ in range(30)],
    }
    db = MagicMock()
    db.integration_inbox.find_one = AsyncMock(return_value=fake_row)

    out = await row_diagnostics(db, "trace-abc")

    assert out["ok"] is True
    assert out["found"] is True
    assert out["diagnosis"]["sas_gate_persisted"] is True
    assert out["diagnosis"]["sas_gate_eligible"] is True
    assert out["diagnosis"]["sas_gate_reason"] is None
    assert out["diagnosis"]["qoyod_invoice_id_is_dry"]  is True
    assert out["diagnosis"]["qoyod_invoice_id_is_real"] is False
    # stage_history truncated to last 20 for readability.
    assert len(out["row"]["stage_history"]) == 20
    assert out["row"]["_stage_history_truncated"]["total"] == 30


@pytest.mark.asyncio
async def test_row_diagnostics_absent_gate_signals_stale_build():
    """When the row has NO `selective_auto_send_gate` field at all,
    that is the diagnostic signal for 'worker on stale build OR SAS
    disabled at time of processing'."""
    from integrations.qoyod.sas_build_diagnostics import row_diagnostics

    fake_row = {
        "id": "row-2",
        "trace_id": "trace-xyz",
        "pipeline_stage": "INVOICE_CREATED",
        # selective_auto_send_gate INTENTIONALLY missing
        "qoyod_customer_id":       "DRY:contact:cccc3333",
        "qoyod_invoice_id":        "DRY:invoice:dddd4444",
        "stage_history": [],
    }
    db = MagicMock()
    db.integration_inbox.find_one = AsyncMock(return_value=fake_row)

    out = await row_diagnostics(db, "trace-xyz")

    assert out["ok"] is True
    assert out["diagnosis"]["sas_gate_persisted"] is False
    assert out["diagnosis"]["sas_gate_eligible"] is None
    assert out["diagnosis"]["qoyod_invoice_id_is_dry"] is True


@pytest.mark.asyncio
async def test_row_diagnostics_not_found():
    from integrations.qoyod.sas_build_diagnostics import row_diagnostics
    db = MagicMock()
    db.integration_inbox.find_one = AsyncMock(return_value=None)
    out = await row_diagnostics(db, "nope")
    assert out["ok"] is False
    assert out["found"] is False
    assert "no integration_inbox row" in out["reason"]


@pytest.mark.asyncio
async def test_row_diagnostics_empty_trace_rejected():
    from integrations.qoyod.sas_build_diagnostics import row_diagnostics
    out = await row_diagnostics(MagicMock(), "")
    assert out["ok"] is False
    assert "trace_id required" in out["reason"]
