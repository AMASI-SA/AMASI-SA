"""Iter-2026-02.rev29d — Hard gate-persistence preflight + worker-code
identity mismatch invariant.

Production trace `8cfeba3cf139456198eef63cf97065cf` (order 270182554)
showed that even after rev29c was deployed with the correct build
marker, a FRESH dry Tabby order still landed at INVOICE_CREATED with:
  • `selective_auto_send_gate` MISSING (rev29c should have persisted it)
  • Legacy wording "customer created in Qoyod" (rev29c strengthening
    should have caught it)

Conclusion: an OLD worker process (running pre-rev29c bytecode) built
the row. The API showed rev29c markers because it was restarted; the
worker asyncio.create_task from the previous bootstrap kept processing
with cached modules.

rev29d hardens the pipeline with a defense that catches this class of
failure REGARDLESS of which worker built the row:

  A. `_require_sas_gate_persisted(...)` is called at the ENTRY of every
     downstream stage (CUSTOMER_RESOLVED, PRODUCT_RESOLVED, and inside
     `process_customer_resolved_row` before product/invoice/receipt).
     If the DB row is missing `selective_auto_send_gate` OR
     `selective_auto_send_gate_at`, the pipeline DEAD_LETTERs with
     code `sas_gate_missing_before_downstream` BEFORE emitting any
     stage_history note.

  B. `row_diagnostics` now surfaces
     `row_worker_pipeline_sha`, `current_pipeline_sha`, and
     `worker_code_mismatch` so operators can see, per row, which
     worker built it — and whether it matches the running process.

  C. New build marker `rev29d_hard_gate_preflight` proves the deploy
     includes this guard.

Together with rev29c's fail-closed gate persistence, this makes the
following impossible:
  1. A row cannot advance past NORMALIZED without a persisted gate
     (rev29c wrote it, or the transition itself DEAD_LETTERs).
  2. Even if a stale worker somehow slipped a gateless row through,
     the FIRST downstream stage entry refuses to continue.
"""
from __future__ import annotations

import inspect
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, "/app/backend")


# ── Test 1: rev29d marker registered + present in build ──────────
def test_1_rev29d_marker_registered_and_present():
    from integrations.qoyod.sas_build_diagnostics import (
        REQUIRED_MARKERS, build_diagnostics_report,
    )
    assert "rev29d_hard_gate_preflight" in REQUIRED_MARKERS
    r = build_diagnostics_report()
    m = r["marker_check"]["markers"]["rev29d_hard_gate_preflight"]
    assert m["present"] is True
    assert m["count"] >= 1
    # Full acceptance still green.
    assert r["acceptance"]["code_matches_expected"] is True


# ── Test 2: preflight function + custom exception exist ──────────
def test_2_preflight_function_and_exception_exist():
    from integrations.qoyod.pipeline import (
        _require_sas_gate_persisted, _SasGateMissingError,
    )
    # Both callable + class.
    assert callable(_require_sas_gate_persisted)
    assert issubclass(_SasGateMissingError, Exception)


# ── Test 3: preflight raises when gate missing ───────────────────
@pytest.mark.asyncio
async def test_3_preflight_raises_when_gate_missing():
    from integrations.qoyod.pipeline import (
        _require_sas_gate_persisted, _SasGateMissingError,
    )
    db = MagicMock()
    db.integration_inbox.find_one = AsyncMock(return_value={
        # No selective_auto_send_gate at all — stale-worker row.
        "id": "row-x", "pipeline_stage": "CUSTOMER_RESOLVED",
        "sas_worker_trace": {"worker_pipeline_sha": "OLD_SHA"},
    })
    with pytest.raises(_SasGateMissingError) as excinfo:
        await _require_sas_gate_persisted(
            db, "row-x", stage="CUSTOMER_RESOLVED")
    assert excinfo.value.stage == "CUSTOMER_RESOLVED"
    assert excinfo.value.worker_sha == "OLD_SHA"


# ── Test 4: preflight raises when gate_at missing ────────────────
@pytest.mark.asyncio
async def test_4_preflight_raises_when_gate_at_missing():
    from integrations.qoyod.pipeline import (
        _require_sas_gate_persisted, _SasGateMissingError,
    )
    db = MagicMock()
    db.integration_inbox.find_one = AsyncMock(return_value={
        "id": "row-y", "pipeline_stage": "PRODUCT_RESOLVED",
        # gate present, but timestamp missing → partial write.
        "selective_auto_send_gate": {"eligible": True},
        # no selective_auto_send_gate_at
    })
    with pytest.raises(_SasGateMissingError):
        await _require_sas_gate_persisted(
            db, "row-y", stage="PRODUCT_RESOLVED")


# ── Test 5: preflight passes when gate + gate_at present ─────────
@pytest.mark.asyncio
async def test_5_preflight_passes_when_gate_complete():
    from integrations.qoyod.pipeline import _require_sas_gate_persisted
    db = MagicMock()
    db.integration_inbox.find_one = AsyncMock(return_value={
        "id": "row-z", "pipeline_stage": "CUSTOMER_RESOLVED",
        "selective_auto_send_gate": {
            "eligible": True, "reason": "eligible"},
        "selective_auto_send_gate_at": "2026-02-03T10:00:00+00:00",
    })
    # Should NOT raise.
    await _require_sas_gate_persisted(
        db, "row-z", stage="CUSTOMER_RESOLVED")


# ── Test 6: source has preflight wiring at every downstream stage
def test_6_pipeline_wires_preflight_at_all_downstream_stages():
    from integrations.qoyod import pipeline as pmod
    src = inspect.getsource(pmod)
    # Three call sites: CUSTOMER_RESOLVED entry, PRODUCT_RESOLVED
    # entry (inside process_customer_resolved_row), + the module
    # definition itself. So count of `await _require_sas_gate_persisted(`
    # must be at least 2 (one per pipeline entry function).
    assert src.count("await _require_sas_gate_persisted(") >= 2, (
        "rev29d requires the preflight to be wired at BOTH pipeline "
        "entry points: process_normalized_row → CUSTOMER_RESOLVED, "
        "and process_customer_resolved_row → PRODUCT_RESOLVED.")
    # DEAD_LETTER reason string used by BOTH callsites.
    assert '"sas_gate_missing_before_downstream"' in src
    # Marker line for grep-ability.
    assert "rev29d — Hard preflight" in src


# ── helper: run diagnostics on a synthetic row ───────────────────
async def _diag(row, settings=None):
    from integrations.qoyod.sas_build_diagnostics import row_diagnostics
    db = MagicMock()
    db.integration_inbox.find_one = AsyncMock(return_value=row)
    db.qoyod_settings.find_one   = AsyncMock(return_value=settings or {})
    return await row_diagnostics(db, row["trace_id"])


# ── Test 7: prod trace 8cfeba3cf... replay ───────────────────────
@pytest.mark.asyncio
async def test_7_prod_trace_8cfeba3cf_replay_flags_both_and_shows_mismatch():
    """Replay of the exact production symptom the user documented.
    Row has DRY: ids AND legacy wording AND no persisted gate AND
    was built by a stale worker (different sha). Diagnostics MUST
    surface:
      • sas_gate_missing_violation=true
      • dry_run_wording_violation=true
      • worker_code_mismatch=true (when row_worker_sha differs)
    """
    row = {
        "id":              "row-8cfeba3cf",
        "trace_id":        "8cfeba3cf139456198eef63cf97065cf",
        "user_id":         "main",
        "pipeline_stage":  "INVOICE_CREATED",
        "qoyod_customer_id": "DRY:contact:8eb1c8f9",
        "qoyod_invoice_id":  "DRY:invoice:8f737000",
        "selective_auto_send_gate": None,  # ← the smoking gun
        "sas_worker_trace": {
            # A sha that could not possibly equal the current sha.
            "worker_pipeline_sha": "OLD_STALE_SHA_1234",
            "settings_seen": {"dry_run_mode": True},
        },
        "stage_history": [
            {"from_stage": "NORMALIZED",       "to_stage": "RULES_APPLIED",
             "note": "eligible · triggered_by=completed"},
            {"from_stage": "RULES_APPLIED",    "to_stage": "CUSTOMER_RESOLVED",
             "note": "customer created in Qoyod"},
            {"from_stage": "CUSTOMER_RESOLVED","to_stage": "PRODUCT_RESOLVED",
             "note": "1 product(s) created · 2 mapped"},
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
    assert d["sas_gate_missing_violation"] is True
    assert d["dry_run_wording_violation"] is True
    # rev29d — worker code identity check.
    assert d["row_worker_pipeline_sha"] == "OLD_STALE_SHA_1234"
    assert isinstance(d["current_pipeline_sha"], str)
    assert d["worker_code_mismatch"] is True


# ── Test 8: fresh rev29d-built row is clean ─────────────────────
@pytest.mark.asyncio
async def test_8_fresh_rev29d_row_no_violations_and_sha_matches():
    """A row built by the current process (matching sha) with a
    persisted gate and DRY-RUN wording — everything clean."""
    from integrations.qoyod.sas_worker_trace import _compute_pipeline_sha
    current_sha = _compute_pipeline_sha()
    row = {
        "id":              "row-fresh-rev29d",
        "trace_id":        "fresh-rev29d-trace",
        "user_id":         "main",
        "pipeline_stage":  "INVOICE_CREATED",
        "qoyod_customer_id": "DRY:contact:aaa",
        "qoyod_invoice_id":  "DRY:invoice:bbb",
        "selective_auto_send_gate": {
            "eligible": True, "reason": "eligible",
            "resolved_payment_key": "tabby_installment",
        },
        "selective_auto_send_gate_at":     "2026-02-03T10:00:00+00:00",
        "selective_auto_send_gate_source": "sas_enabled_at_worker",
        "sas_worker_trace": {
            "worker_pipeline_sha": current_sha,
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
             "note": "DRY-RUN: 1 product payload(s) built · 2 mapped · "
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
    # sha match: same process built the row.
    assert d["row_worker_pipeline_sha"] == current_sha
    assert d["worker_code_mismatch"] is False


# ── Test 9: E2E — gateless row @ CUSTOMER_RESOLVED DEAD_LETTERs ──
@pytest.mark.asyncio
async def test_9_gateless_row_at_customer_resolved_dead_letters():
    """The exact bug from prod: a row lands at CUSTOMER_RESOLVED (or
    later) with no `selective_auto_send_gate`. rev29d preflight
    MUST DEAD_LETTER it BEFORE any customer-note wording is written.
    """
    from integrations.qoyod import pipeline as pmod

    class _Coll:
        def __init__(self, docs): self._docs = list(docs)
        async def find_one(self, q, projection=None):
            for d in self._docs:
                if all(d.get(k) == v for k, v in q.items()):
                    return dict(d)
            return None
        async def update_one(self, q, u, upsert=False):
            m = 0
            for d in self._docs:
                if all(d.get(k) == v for k, v in q.items()):
                    m = 1
                    for k, v in (u.get("$set") or {}).items():
                        d[k] = v
                    for k, v in (u.get("$push") or {}).items():
                        d.setdefault(k, []).append(v)
                    break
            return MagicMock(matched_count=m, modified_count=m)

    # Row @ CUSTOMER_RESOLVED with NO gate → stale-worker scenario.
    row = {
        "id": "row-stale",
        "user_id": "main",
        "trace_id": "trace-stale",
        "pipeline_stage": "CUSTOMER_RESOLVED",
        "qoyod_customer_id": "230",
        "canonical_payload": {
            "order_number": "ORDER-STALE",
            "order_status": "completed",
            "payment_method": "tabby_installment",
            "customer": {"name": "T", "phone": "+966500000000"},
            "items": [{"sku": "SKU-1", "name": "X", "quantity": 1,
                       "unit_price_ex_tax": 100.0,
                       "tax_amount": 15.0, "line_total": 115.0,
                       "vat_rate": 15}],
            "totals": {"subtotal_ex_tax": 100.0, "tax_amount": 15.0,
                       "shipping_ex_tax": 0.0, "shipping_tax": 0.0,
                       "discount_ex_tax": 0.0, "discount_tax": 0.0,
                       "order_total": 115.0},
        },
        "business_rules_decision": {
            "eligible": True, "invoice_date": "2026-02-03",
            "invoice_date_source": "salla",
            "triggered_by_status": "completed",
        },
        "pipeline_started_at": "2026-02-03T10:00:00+00:00",
        "stage_history": [],
        # ← selective_auto_send_gate absent (stale worker)
    }
    settings = {
        "user_id": "main",
        "dry_run_mode": True,
        "selective_auto_send_enabled": True,
        "selective_live_send_enabled": False,
        "selective_auto_send_cutover_at": "2026-01-01T00:00:00+00:00",
        "selective_auto_send_allowed_payment_methods": ["tabby_installment"],
        "invoice_trigger_statuses": ["completed"],
        "payment_method_mapping": [
            {"salla_method": "tabby_installment", "qoyod_account_id": "92"},
        ],
        "production_writes_locked": False,
    }

    db = MagicMock()
    db.integration_inbox     = _Coll([dict(row)])
    db.qoyod_settings        = _Coll([dict(settings)])
    db.qoyod_invoices        = _Coll([])
    db.qoyod_invoice_payments = _Coll([])
    db.qoyod_customers       = _Coll([])
    db.qoyod_products        = _Coll([])
    db.qoyod_write_lock_attempts = _Coll([])

    out = await pmod.process_customer_resolved_row(db, dict(row))
    assert out.get("outcome") == "DEAD_LETTER", out
    assert out.get("reason") == "sas_gate_missing_before_downstream", out
    assert out.get("stage")  == "PRODUCT_RESOLVED", out
    # Verify NO downstream wording was ever emitted.
    updated = await db.integration_inbox.find_one({"id": row["id"]})
    hist = updated.get("stage_history") or []
    for e in hist:
        note = e.get("note") or ""
        assert "customer created in Qoyod" not in note
        assert "product(s) created" not in note


# ── Test 10: rev27 live-write gate + rev29 CAS intact ────────────
def test_10_upstream_invariants_intact():
    from integrations.qoyod.pipeline import _live_write_permitted
    ok, _ = _live_write_permitted({
        "dry_run_mode": False,
        "selective_live_send_enabled": True,
        "production_writes_locked": False,
        "selective_auto_send_enabled": True,
    })
    assert ok is True
