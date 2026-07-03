"""Iter-2026-02.rev29c — Fail-closed gate persistence + dry-run
wording strengthened via `_pipeline_is_dry_mode`.

Production trace `b09392fb2a1047fa89ca52b39cbcfe65` (order 270227236)
surfaced two independent bugs:

  1. `sas_gate_missing_violation=true`
     The row advanced past NORMALIZED without `selective_auto_send_gate`
     persisted. Root cause: gate was only persisted when
     `selective_auto_send_enabled=true` at worker time. If the operator
     flipped SAS on AFTER the row was processed, the historical row
     lit up the invariant.

  2. `dry_run_wording_violation=true`
     stage_history contained "customer created in Qoyod" and
     "1 product(s) created · 0 mapped" while ids were `DRY:*`. Root
     cause: wording check keyed on RESOLVED id prefixes only —
     products previously mapped to REAL Qoyod ids from a prior live
     sync bypassed the `_any_dry_product` check even though the
     current run used `DryRunQoyodClient`.

Rev29c fixes:

  A. `selective_auto_send_gate` is now persisted on EVERY row,
     regardless of `selective_auto_send_enabled`. When SAS is
     disabled a synthetic record with
     `reason=sas_disabled_by_settings` is written. The write is
     ALSO included in the RULES_APPLIED atomic CAS. Fail-closed:
     if the persist buffer is empty at RULES_APPLIED time the
     pipeline DEAD_LETTERs the row rather than advancing.

  B. Customer / product / invoice notes now use
     `_pipeline_is_dry_mode` (a canonical signal computed from
     `isinstance(api_client, DryRunQoyodClient)` OR
     `settings.dry_run_mode`) as the PRIMARY dry signal. ID prefix
     remains the fallback.

  C. New build marker `rev29c_fail_closed_gate` proves the deploy
     includes the fix.
"""
from __future__ import annotations

import inspect
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, "/app/backend")


# ── Test A1: rev29c marker registered + present in build ─────────
def test_a1_rev29c_marker_registered_and_present():
    from integrations.qoyod.sas_build_diagnostics import (
        REQUIRED_MARKERS, build_diagnostics_report,
    )
    assert "rev29c_fail_closed_gate" in REQUIRED_MARKERS
    r = build_diagnostics_report()
    m = r["marker_check"]["markers"]["rev29c_fail_closed_gate"]
    assert m["present"] is True
    assert m["count"] >= 1
    # Full acceptance still green.
    assert r["acceptance"]["code_matches_expected"] is True


# ── Test A2: source has `_pipeline_is_dry_mode` computed ─────────
def test_a2_pipeline_is_dry_mode_signal_computed():
    from integrations.qoyod import pipeline as pmod
    src = inspect.getsource(pmod)
    # Signal computed at NORMALIZED stage AND at CUSTOMER_RESOLVED
    # stage (the two entry points that build wordings).
    assert src.count("_pipeline_is_dry_mode = bool(") >= 2, (
        "rev29c requires _pipeline_is_dry_mode to be computed in "
        "BOTH process_normalized_row and process_customer_resolved_row.")
    # Consumed by customer + product + invoice wording sites.
    assert src.count("_pipeline_is_dry_mode") >= 5


# ── Test A3: source has fail-closed gate abort ───────────────────
def test_a3_fail_closed_gate_abort_present():
    from integrations.qoyod import pipeline as pmod
    src = inspect.getsource(pmod)
    assert "sas_gate_persist_buffer_empty" in src, (
        "rev29c fail-closed requires an explicit DEAD_LETTER when "
        "the gate persist buffer is empty at RULES_APPLIED.")
    assert "gate persist buffer empty at RULES_APPLIED" in src


# ── helper: run diagnostics on a synthetic row ───────────────────
async def _diag(row, settings=None):
    from integrations.qoyod.sas_build_diagnostics import row_diagnostics
    db = MagicMock()
    db.integration_inbox.find_one = AsyncMock(return_value=row)
    db.qoyod_settings.find_one   = AsyncMock(return_value=settings or {})
    return await row_diagnostics(db, row["trace_id"])


# ── Test B1: b09392fb trace replay flags BOTH invariants ─────────
@pytest.mark.asyncio
async def test_b1_prod_trace_b09392fb_flags_both_invariants():
    """Replay of the exact production symptom the user documented.
    Row has DRY: ids AND legacy wording AND no persisted gate —
    diagnostics MUST flag sas_gate_missing_violation AND
    dry_run_wording_violation together."""
    row = {
        "id":              "row-b09392fb",
        "trace_id":        "b09392fb2a1047fa89ca52b39cbcfe65",
        "user_id":         "main",
        "pipeline_stage":  "INVOICE_CREATED",
        "qoyod_customer_id": "DRY:contact:c8ee948d",
        "qoyod_invoice_id":  "DRY:invoice:90b8ba1b",
        "selective_auto_send_gate": None,  # ← missing
        "stage_history": [
            {"from_stage": "NORMALIZED",       "to_stage": "RULES_APPLIED",
             "note": "eligible · triggered_by=completed"},
            {"from_stage": "RULES_APPLIED",    "to_stage": "CUSTOMER_RESOLVED",
             "note": "customer created in Qoyod"},
            {"from_stage": "CUSTOMER_RESOLVED","to_stage": "PRODUCT_RESOLVED",
             "note": "1 product(s) created · 0 mapped"},
            {"from_stage": "PRODUCT_RESOLVED", "to_stage": "INVOICE_CREATED",
             "note": "DRY-RUN: invoice payload built, no POST"},
        ],
    }
    out = await _diag(row, settings={
        "dry_run_mode": True,
        "selective_live_send_enabled": False,
        "selective_auto_send_enabled": True,
    })
    d = out["diagnosis"]
    # BOTH invariants fire on the historical row.
    assert d["sas_gate_missing_violation"] is True
    assert d["dry_run_wording_violation"] is True
    # Ids are still dry → no live-write leak.
    assert d["live_write_gate_violation"] is False


# ── Test B2: rev29c fresh dry-created path — both invariants clean
@pytest.mark.asyncio
async def test_b2_rev29c_fresh_dry_created_path_no_violation():
    """After the fix, a fresh dry-created path shows: gate persisted,
    stage_history uses DRY-RUN wording, and all three invariants clean."""
    row = {
        "id":              "row-fresh-rev29c",
        "trace_id":        "fresh-rev29c-trace",
        "user_id":         "main",
        "pipeline_stage":  "INVOICE_CREATED",
        "qoyod_customer_id": "DRY:contact:aaa",
        "qoyod_invoice_id":  "DRY:invoice:bbb",
        "selective_auto_send_gate": {
            "eligible": True, "reason": "eligible",
            "resolved_payment_key": "tabby_installment",
        },
        "selective_auto_send_gate_source": "sas_enabled_at_worker",
        "sas_worker_trace": {
            "settings_seen": {"dry_run_mode": True,
                              "selective_live_send_enabled": False,
                              "selective_auto_send_enabled": True},
        },
        "stage_history": [
            {"from_stage": "NORMALIZED",       "to_stage": "RULES_APPLIED",
             "note": "eligible · triggered_by=completed"},
            {"from_stage": "RULES_APPLIED",    "to_stage": "CUSTOMER_RESOLVED",
             "note": "DRY-RUN: customer payload built, no POST"},
            {"from_stage": "CUSTOMER_RESOLVED","to_stage": "PRODUCT_RESOLVED",
             "note": "DRY-RUN: 1 product payload(s) built · 0 mapped · "
                     "no POST"},
            {"from_stage": "PRODUCT_RESOLVED", "to_stage": "INVOICE_CREATED",
             "note": "DRY-RUN: invoice payload built, no POST"},
        ],
    }
    out = await _diag(row, settings={
        "dry_run_mode": True,
        "selective_live_send_enabled": False,
        "selective_auto_send_enabled": True,
    })
    d = out["diagnosis"]
    assert d["sas_gate_missing_violation"] is False
    assert d["dry_run_wording_violation"] is False
    assert d["live_write_gate_violation"] is False
    assert d["duplicate_stage_transition_violation"] is False


# ── Test C1: dry-run wording — LOCALLY MAPPED REAL product ids ───
@pytest.mark.asyncio
async def test_c1_dry_run_mode_with_locally_mapped_real_product_ids():
    """The exact prod scenario: products were mapped locally to REAL
    Qoyod ids (prior live sync). The dry-run pipeline reuses those
    real ids WITHOUT any Qoyod POST. Under rev29b, the note fell
    through to "N product(s) created · M mapped" — false-positive
    audit signal. rev29c strengthens the check so
    `_pipeline_is_dry_mode` alone forces DRY-RUN wording."""
    from integrations.qoyod import pipeline as pmod
    src = inspect.getsource(pmod)
    # Product wording MUST use `_is_dry_product_stage` (which OR's
    # `_pipeline_is_dry_mode` with the id-prefix check).
    assert "_is_dry_product_stage = _pipeline_is_dry_mode or " in src
    # Customer wording MUST branch on `_pipeline_is_dry_mode` as the
    # PRIMARY signal (id-prefix is fallback).
    assert "_is_dry_customer = (" in src
    assert "_pipeline_is_dry_mode" in src
    # Verify wording: exact "created in Qoyod" line only under the
    # NON-dry branch (idempotent — always present as a fallback,
    # but only reachable when `_is_dry_customer` is False).
    assert 'else "customer created in Qoyod")' in src


# ── Test D1: SAS-disabled synthetic gate ─────────────────────────
def test_d1_sas_disabled_persists_synthetic_gate():
    from integrations.qoyod import pipeline as pmod
    src = inspect.getsource(pmod)
    # SAS-disabled branch: persist synthetic gate record.
    assert '"sas_disabled_by_settings"' in src
    assert '"selective_auto_send_gate_source": "sas_disabled_at_worker"' in src


# ── Test D2: fail-closed abort when persist buffer empty ─────────
def test_d2_fail_closed_when_persist_buffer_empty():
    from integrations.qoyod import pipeline as pmod
    src = inspect.getsource(pmod)
    # Must contain the fail-closed check: `if not _sas_gate_persist_set:`
    assert "if not _sas_gate_persist_set:" in src
    assert "sas_gate_persist_buffer_empty" in src


# ── Test E1: rev27 live-write gate remains intact ────────────────
def test_e1_rev27_live_write_gate_intact():
    from integrations.qoyod.pipeline import _live_write_permitted
    ok, _ = _live_write_permitted({
        "dry_run_mode": False,
        "selective_live_send_enabled": True,
        "production_writes_locked": False,
        "selective_auto_send_enabled": True,
    })
    assert ok is True
    for kill in (
        {"dry_run_mode": True},
        {"selective_live_send_enabled": False},
        {"production_writes_locked": True},
        {"selective_auto_send_enabled": False},
    ):
        s = {"dry_run_mode": False,
             "selective_live_send_enabled": True,
             "production_writes_locked": False,
             "selective_auto_send_enabled": True,
             **kill}
        ok, _ = _live_write_permitted(s)
        assert ok is False


# ── Test E2: rev29 atomic CAS remains intact ─────────────────────
@pytest.mark.asyncio
async def test_e2_rev29_atomic_cas_intact():
    from integrations.qoyod.pipeline import _apply_atomic, _StaleStageError

    class _Coll:
        def __init__(self, docs): self._docs = list(docs)
        async def find_one(self, q, projection=None):
            for d in self._docs:
                if all(d.get(k) == v for k, v in q.items()):
                    return dict(d)
            return None
        async def update_one(self, q, u, upsert=False):
            matched = 0
            for d in self._docs:
                if all(d.get(k) == v for k, v in q.items()):
                    matched = 1
                    for k, v in (u.get("$set") or {}).items():
                        d[k] = v
                    break
            return MagicMock(matched_count=matched, modified_count=matched)

    db = MagicMock()
    db.integration_inbox = _Coll([{"id": "r", "pipeline_stage": "NORMALIZED"}])
    await _apply_atomic(
        db, "r", {"$set": {"pipeline_stage": "RULES_APPLIED"}},
        expected_from_stage="NORMALIZED",
    )
    updated = await db.integration_inbox.find_one({"id": "r"})
    assert updated["pipeline_stage"] == "RULES_APPLIED"

    db2 = MagicMock()
    db2.integration_inbox = _Coll([{"id": "r", "pipeline_stage": "COMPLETED"}])
    with pytest.raises(_StaleStageError):
        await _apply_atomic(
            db2, "r", {"$set": {"x": 1}},
            expected_from_stage="INVOICE_CREATED",
        )
