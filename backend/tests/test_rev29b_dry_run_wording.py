"""Iter-2026-02.rev29b — Dry-run wording enforcement.

After production order 270196668 (trace baa0383c...) reached
INVOICE_CREATED cleanly under all rev27/rev28/rev29 invariants — YET
the persisted stage_history still contained the misleading strings
"customer created in Qoyod" and "product(s) created" while the actual
Qoyod ids were `DRY:*` — we enforce:

  1.  Pipeline emits DRY-RUN wording at customer / product / invoice
      stages when the resolved id carries a DRY:/PREVIEW: sentinel.
  2.  Diagnostics surfaces a new `dry_run_wording_violation` invariant
      that fires when the row has DRY evidence but stage_history still
      carries the pre-rev29b wording.
  3.  A new build marker `rev29b_dry_run_wording` proves the deploy
      includes this fix.
  4.  rev27 live-write gate, rev28 sas gate persistence, rev29 CAS
      transitions are UNCHANGED.
"""
from __future__ import annotations

import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, "/app/backend")


# ── Test 1: source contains all rev29b wordings + marker ─────────
def test_1_pipeline_source_has_rev29b_wordings():
    import inspect
    from integrations.qoyod import pipeline as pmod
    src = inspect.getsource(pmod)

    # Customer branch — dry payload build.
    assert "DRY-RUN: customer payload built, no POST" in src
    # Customer branch — dry mapping (rev29b added ", no POST" suffix).
    # Python adjacent-string concat: source may split the literal
    # across two lines. Check for either the fully-joined form or
    # the split-across-lines pattern the current file uses.
    joined = "DRY-RUN: customer mapped from local store, no POST"
    split = (
        'DRY-RUN: customer mapped from local store, "\n'
        '                               "no POST'
    )
    assert (joined in src) or (split in src), (
        "rev29b requires the mapped-dry customer note to end with "
        "', no POST' — pre-rev29b wording found instead.")
    # Product branch — dry payload build.
    assert "DRY-RUN: " in src and "product payload(s) built" in src
    assert "no POST" in src
    # Invoice branch — dry payload build.
    assert "DRY-RUN: invoice payload built, no POST" in src
    # The rev29b marker anchor MUST appear at least once (marker check).
    assert "rev29b — Dry-run wording enforcement" in src


# ── Test 2: rev29b marker is registered in build diagnostics ─────
def test_2_rev29b_marker_registered_and_present():
    from integrations.qoyod.sas_build_diagnostics import (
        REQUIRED_MARKERS, build_diagnostics_report,
    )
    assert "rev29b_dry_run_wording" in REQUIRED_MARKERS
    r = build_diagnostics_report()
    m = r["marker_check"]["markers"]["rev29b_dry_run_wording"]
    assert m["present"] is True, (
        "rev29b marker not detected in running pipeline.py — "
        "deploy is stale.")
    assert m["count"] >= 1
    # Full acceptance: ALL registered markers present.
    assert r["acceptance"]["code_matches_expected"] is True, (
        f"missing markers: {m}")


# ── helper: run diagnostics on a synthetic row ───────────────────
async def _diag(row, settings=None):
    from integrations.qoyod.sas_build_diagnostics import row_diagnostics
    db = MagicMock()
    db.integration_inbox.find_one = AsyncMock(return_value=row)
    db.qoyod_settings.find_one   = AsyncMock(return_value=settings or {})
    return await row_diagnostics(db, row["trace_id"])


# ── Test 3: order 270219411 replay — old wording + DRY ids → true ─
@pytest.mark.asyncio
async def test_3_old_dry_row_with_pre_rev29b_wording_flags_violation():
    """Replay of the exact pre-rev29b symptom the user documented.
    A row with DRY: ids AND legacy stage_history wording MUST flag
    `dry_run_wording_violation=true`."""
    row = {
        "id":              "row-270219411",
        "trace_id":        "8676b9e0c564456496ba2ec6dfc9f994",
        "user_id":         "main",
        "pipeline_stage":  "INVOICE_CREATED",
        "qoyod_customer_id": "DRY:contact:abcd1234",
        "qoyod_invoice_id":  "DRY:invoice:9f8e7d6c",
        "selective_auto_send_gate": {"eligible": True, "reason": "eligible"},
        "stage_history": [
            {"from_stage": "NORMALIZED",       "to_stage": "RULES_APPLIED",
             "note": "eligible · triggered_by=completed"},
            {"from_stage": "RULES_APPLIED",    "to_stage": "CUSTOMER_RESOLVED",
             "note": "customer created in Qoyod"},
            {"from_stage": "CUSTOMER_RESOLVED","to_stage": "PRODUCT_RESOLVED",
             "note": "0 product(s) created · 1 mapped"},
            {"from_stage": "PRODUCT_RESOLVED", "to_stage": "INVOICE_CREATED",
             "note": "invoice 188 created"},
        ],
    }
    out = await _diag(row, settings={"dry_run_mode": True,
                                     "selective_live_send_enabled": False,
                                     "selective_auto_send_enabled": True})
    d = out["diagnosis"]
    assert d["dry_run_wording_violation"] is True
    assert d["dry_run_wording_reason"] is not None
    # All three forbidden phrases should surface.
    phrases = {o["phrase"] for o in d["dry_run_wording_offending"]}
    assert "customer created in Qoyod" in phrases
    assert "product(s) created" in phrases
    assert "invoice created" in phrases
    # Independence — other invariants remain FALSE on this replay.
    assert d["live_write_gate_violation"] is False
    assert d["duplicate_stage_transition_violation"] is False


# ── Test 4: fresh dry row with rev29b wording → violation=false ──
@pytest.mark.asyncio
async def test_4_fresh_rev29b_dry_row_no_violation():
    row = {
        "id":              "row-fresh-dry",
        "trace_id":        "fresh-dry-trace",
        "user_id":         "main",
        "pipeline_stage":  "INVOICE_CREATED",
        "qoyod_customer_id": "DRY:contact:7da95cf5",
        "qoyod_invoice_id":  "DRY:invoice:975a3601",
        "selective_auto_send_gate": {"eligible": True, "reason": "eligible"},
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
             "note": "DRY-RUN: 0 product payload(s) built · 1 mapped · "
                     "no POST"},
            {"from_stage": "PRODUCT_RESOLVED", "to_stage": "INVOICE_CREATED",
             "note": "DRY-RUN: invoice payload built, no POST"},
        ],
    }
    out = await _diag(row, settings={"dry_run_mode": True,
                                     "selective_live_send_enabled": False,
                                     "selective_auto_send_enabled": True})
    d = out["diagnosis"]
    assert d["dry_run_wording_violation"] is False
    assert d["dry_run_wording_offending"] == []
    # No regression on the other invariants either.
    assert d["live_write_gate_violation"] is False
    assert d["sas_gate_missing_violation"] is False
    assert d["duplicate_stage_transition_violation"] is False


# ── Test 5: real live row (no DRY ids) with live wording → false ──
@pytest.mark.asyncio
async def test_5_live_row_with_live_wording_no_violation():
    """A genuinely live row uses the real wordings ("customer
    created in Qoyod", etc.). Since there's NO dry evidence, the
    invariant must not fire — otherwise every successful live send
    would light up the diagnostic."""
    row = {
        "id":              "row-live",
        "trace_id":        "tr-live",
        "user_id":         "main",
        "pipeline_stage":  "COMPLETED",
        "qoyod_customer_id": "42",
        "qoyod_invoice_id":  "199",
        "qoyod_invoice_payment_id": "77",
        "selective_auto_send_gate": {"eligible": True, "reason": "eligible"},
        "sas_worker_trace": {
            "settings_seen": {"dry_run_mode": False,
                              "selective_live_send_enabled": True,
                              "selective_auto_send_enabled": True,
                              "production_writes_locked": False},
        },
        "stage_history": [
            {"from_stage": "NORMALIZED",       "to_stage": "RULES_APPLIED",
             "note": "eligible"},
            {"from_stage": "RULES_APPLIED",    "to_stage": "CUSTOMER_RESOLVED",
             "note": "customer created in Qoyod"},
            {"from_stage": "CUSTOMER_RESOLVED","to_stage": "PRODUCT_RESOLVED",
             "note": "1 product(s) created · 0 mapped"},
            {"from_stage": "PRODUCT_RESOLVED", "to_stage": "INVOICE_CREATED",
             "note": "invoice 199 created"},
        ],
    }
    out = await _diag(row, settings={"dry_run_mode": False,
                                     "selective_live_send_enabled": True,
                                     "selective_auto_send_enabled": True,
                                     "production_writes_locked": False})
    d = out["diagnosis"]
    assert d["dry_run_wording_violation"] is False, (
        f"live row false-flagged as dry_run_wording_violation: "
        f"{d['dry_run_wording_reason']} · "
        f"{d['dry_run_wording_offending']}")


# ── Test 6: sas_worker_trace evidence alone is enough to trigger ──
@pytest.mark.asyncio
async def test_6_swt_dry_evidence_triggers_violation_even_without_dry_ids():
    """If a row's `sas_worker_trace.settings_seen.dry_run_mode=true`
    (worker saw dry-run when it processed) but its stage_history
    contains legacy wording — flag violation even if the ids don't
    (yet) carry `DRY:` (e.g. row aborted before ids stamped)."""
    row = {
        "id":              "row-swt-dry",
        "trace_id":        "tr-swt-dry",
        "user_id":         "main",
        "pipeline_stage":  "PRODUCT_RESOLVED",
        "qoyod_customer_id": None,
        "qoyod_invoice_id":  None,
        "sas_worker_trace": {
            "settings_seen": {"dry_run_mode": True},
        },
        "stage_history": [
            {"from_stage": "RULES_APPLIED", "to_stage": "CUSTOMER_RESOLVED",
             "note": "customer created in Qoyod"},
        ],
    }
    out = await _diag(row, settings={"dry_run_mode": False})
    d = out["diagnosis"]
    assert d["dry_run_wording_violation"] is True
    reason = d["dry_run_wording_reason"] or ""
    assert "sas_worker_trace.settings_seen.dry_run_mode=true" in reason


# ── Test 7: rev27 live-write gate remains unchanged ──────────────
def test_7_rev27_live_write_gate_intact():
    from integrations.qoyod.pipeline import _live_write_permitted
    # 4-clause AND — any missing clause blocks live writes.
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
        assert ok is False, f"live-write leaked for kill={kill!r}"


# ── Test 8: rev29 CAS transitions remain unchanged ──────────────
@pytest.mark.asyncio
async def test_8_rev29_atomic_cas_intact():
    """rev29b is a wording + diagnostics change; the atomic CAS
    semantics MUST still hold."""
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

    # Success path — expected_from matches.
    db = MagicMock()
    db.integration_inbox = _Coll([{"id": "r", "pipeline_stage": "NORMALIZED"}])
    await _apply_atomic(
        db, "r", {"$set": {"pipeline_stage": "RULES_APPLIED"}},
        expected_from_stage="NORMALIZED",
    )
    updated = await db.integration_inbox.find_one({"id": "r"})
    assert updated["pipeline_stage"] == "RULES_APPLIED"

    # Failure path — stale expected_from raises.
    db2 = MagicMock()
    db2.integration_inbox = _Coll([{"id": "r", "pipeline_stage": "COMPLETED"}])
    with pytest.raises(_StaleStageError):
        await _apply_atomic(
            db2, "r", {"$set": {"x": 1}},
            expected_from_stage="INVOICE_CREATED",
        )
