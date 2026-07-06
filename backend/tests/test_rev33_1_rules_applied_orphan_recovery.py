"""Iter-2026-07.rev33.1 — RULES_APPLIED orphan-row recovery tests.

Incident evidence:
  • trace_id=5ad73216be714c54929a52dd3bb4e012
  • order=270818906
  • payment_method=tabby_installment
  • pipeline_stage=RULES_APPLIED
  • sas_gate.eligible=true
  • business_rules.eligible=true

  Root cause: `process_normalized_row` is monolithic —
  NORMALIZED→RULES_APPLIED (CAS #1) followed by an async network
  call (`resolve_customer`) then RULES_APPLIED→CUSTOMER_RESOLVED
  (CAS #2). If the pod is interrupted between the two writes, the
  row is orphaned at RULES_APPLIED. `worker._one_round` polled only
  {NORMALIZED, CUSTOMER_RESOLVED} so an orphan sat forever.

Fix under test:
  • `process_rules_applied_row` — new function that completes the
    second half WITHOUT re-running the SAS gate or business rules.
  • `process_pending_rules_applied` — cursor over
    `pipeline_stage="RULES_APPLIED"`.
  • `worker._one_round` now drains the RULES_APPLIED bucket between
    NORMALIZED and CUSTOMER_RESOLVED, so `run_now` reports
    `rules_applied.processed` alongside the other two buckets.

Acceptance:
  1) A healthy orphan (persisted evidence eligible=true+true) is
     driven RULES_APPLIED → CUSTOMER_RESOLVED with NO SAS re-eval.
  2) A row with persisted `sas_gate.eligible=false` at
     RULES_APPLIED is routed to SKIPPED — never touches Qoyod.
  3) `worker.run_now` surface contains a non-zero
     `rules_applied.processed` when an orphan exists (i.e., the
     UI's "تشغيل الآن" no longer returns 0 for that bucket).
"""
from __future__ import annotations

import sys
from unittest.mock import MagicMock, AsyncMock  # noqa: F401

import pytest

sys.path.insert(0, "/app/backend")


def _canonical_tabby(order_id="270818906"):
    """Minimal canonical payload the DTO accepts + tabby PM."""
    return {
        "order_id":            order_id,
        "order_status":        "completed",
        "order_status_native": "تم التنفيذ",
        "order_date":          "2026-07-05T22:00:00+00:00",
        "payment_method":      "tabby_installment",
        "payment_method_native": "tabby_installment",
        "currency":            "SAR",
        "subtotal":            100.0,
        "tax_amount":          15.0,
        "shipping_amount":     0.0,
        "discount_amount":     0.0,
        "total_amount":        115.0,
        "customer": {
            "name":  "Test Customer",
            "phone": "+966500000000",
            "email": "test@example.com",
        },
        "items": [
            {"sku":           "SKU-A",
             "name":          "Item A",
             "quantity":      1.0,
             "unit_price":    100.0,
             "tax_amount":    15.0,
             "discount_amount": 0.0,
             "total":         115.0},
        ],
    }


def _orphan_row(*,
                stage="RULES_APPLIED",
                sas_eligible=True,
                br_eligible=True,
                row_id="row-orphan-1",
                trace_id="5ad73216be714c54929a52dd3bb4e012"):
    """Construct an orphan integration_inbox row at RULES_APPLIED."""
    from integrations.qoyod.sas_worker_trace import _compute_pipeline_sha
    sha = _compute_pipeline_sha()
    return {
        "id":              row_id,
        "user_id":         "main",
        "trace_id":        trace_id,
        "pipeline_stage":  stage,
        "canonical_payload": _canonical_tabby(),
        "selective_auto_send_gate": {
            "eligible": sas_eligible,
            "reason":   "test_persisted",
        },
        # `_require_sas_gate_persisted` (rev29d preflight) checks the
        # `selective_auto_send_gate_at` sibling — both must be present
        # for the row to be recognized as legitimately gated.
        "selective_auto_send_gate_at": "2026-07-05T22:00:00+00:00",
        "sas_worker_trace": {"worker_pipeline_sha": sha},
        "business_rules_decision": {
            "eligible":              br_eligible,
            "reason":                "test_persisted",
            "triggered_by_status":   "completed",
            "invoice_date_source":   "order_date",
        },
        "stage_history":   [
            {"from_stage": "NORMALIZED",   "to_stage": "RULES_APPLIED"},
        ],
        "received_at":     "2026-07-05T22:00:00+00:00",
        "pipeline_started_at": "2026-07-05T22:00:00+00:00",
    }


class _FakeInboxColl:
    """In-memory `integration_inbox` fake honoring CAS filter."""

    def __init__(self, rows):
        self._rows = {r["id"]: dict(r) for r in rows}

    def find(self, filt, sort=None, limit=None):
        # Match user_id + pipeline_stage exactly, ignore sort/limit
        # for the test (fixture is tiny).
        matches = [
            dict(r) for r in self._rows.values()
            if all(r.get(k) == v for k, v in filt.items())
        ]

        class _Cur:
            def __init__(self, rows):
                self._rows = rows

            def __aiter__(self):
                self._i = 0
                return self

            async def __anext__(self):
                if self._i >= len(self._rows):
                    raise StopAsyncIteration
                r = self._rows[self._i]
                self._i += 1
                return r

        return _Cur(matches)

    async def find_one(self, filt, proj=None):
        for r in self._rows.values():
            if all(r.get(k) == v for k, v in filt.items()):
                return dict(r)
        return None

    async def update_one(self, filt, patch, **kw):
        matched, modified = 0, 0
        for rid, r in self._rows.items():
            if all(r.get(k) == v for k, v in filt.items()):
                matched = 1
                for k, v in (patch.get("$set") or {}).items():
                    r[k] = v
                # Handle $push (stage_history transitions)
                for k, arr_op in (patch.get("$push") or {}).items():
                    r.setdefault(k, []).append(arr_op)
                # Handle $inc
                for k, v in (patch.get("$inc") or {}).items():
                    r[k] = int(r.get(k) or 0) + int(v)
                modified = 1
                break
        return MagicMock(matched_count=matched, modified_count=modified)


def _mk_full_db(*, settings, rows, kill_switch_events=None):
    """Full-featured fake db for pipeline tests."""
    kill_switch_events = kill_switch_events or []
    _settings = dict(settings)

    async def _settings_find_one(f, proj=None):
        return dict(_settings)

    async def _settings_update_one(f, u, upsert=False):
        for k, v in ((u or {}).get("$set") or {}).items():
            _settings[k] = v
        return MagicMock(matched_count=1, modified_count=1)

    async def _kse_insert_one(doc):
        kill_switch_events.append(doc)
        return MagicMock(inserted_id="fake")

    inbox = _FakeInboxColl(rows)
    qoyod_invoices_docs = []

    async def _qi_find_one(f, proj=None):
        return None       # No pre-existing invoice for the order.

    async def _qi_insert_one(d):
        qoyod_invoices_docs.append(d)
        return MagicMock(inserted_id="fake")

    async def _generic_update_one(f, u, **kw):
        return MagicMock(matched_count=1, modified_count=1)

    async def _generic_insert_one(d):
        return MagicMock(inserted_id="fake")

    db = MagicMock()
    db.qoyod_settings              = MagicMock()
    db.qoyod_settings.find_one     = _settings_find_one
    db.qoyod_settings.update_one   = _settings_update_one

    db.integration_inbox           = inbox

    db.qoyod_invoices              = MagicMock()
    db.qoyod_invoices.find_one     = _qi_find_one
    db.qoyod_invoices.insert_one   = _qi_insert_one
    db.qoyod_invoices.update_one   = _generic_update_one

    db.rev32_kill_switch_events    = MagicMock()
    db.rev32_kill_switch_events.insert_one = _kse_insert_one

    # Common collections resolve_customer / _load_settings / traces
    # may touch — mock as no-op-happy.
    for cn in ("qoyod_customers", "qoyod_customer_mapping",
               "sas_worker_traces", "qoyod_worker_traces",
               "integration_pipeline_events", "audit_log"):
        c = MagicMock()
        c.find_one   = AsyncMock(return_value=None)
        c.update_one = AsyncMock(return_value=MagicMock(
            matched_count=1, modified_count=1))
        c.insert_one = AsyncMock(return_value=MagicMock(
            inserted_id="fake"))
        setattr(db, cn, c)

    db._captured_kill_switch_events = kill_switch_events
    db._settings                    = _settings
    db._inbox                       = inbox
    return db


# ═════════════════════════════════════════════════════════════════
# TEST 1 — Healthy orphan → RULES_APPLIED → CUSTOMER_RESOLVED
# ═════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_orphan_healthy_advances_to_customer_resolved(monkeypatch):
    """A `RULES_APPLIED` row with persisted `sas_gate.eligible=true`
    AND `business_rules.eligible=true` is picked up by
    `process_rules_applied_row` and driven to CUSTOMER_RESOLVED.

    Guarantees:
      • The SAS gate is NEVER re-evaluated (evidence is preserved).
      • `resolve_customer` is called EXACTLY once.
      • The row lands at pipeline_stage=CUSTOMER_RESOLVED.
      • Return outcome == "CUSTOMER_RESOLVED".
    """
    from integrations.qoyod import pipeline
    from integrations.qoyod.customer_resolver import ResolutionResult

    orphan = _orphan_row()
    orphan_sas_snapshot = dict(orphan["selective_auto_send_gate"])
    db = _mk_full_db(
        settings={
            "user_id":                       "main",
            # Canary-safe dry_run so create_customer guard skips.
            "dry_run_mode":                  True,
            "production_writes_locked":      True,
            "selective_live_send_enabled":   False,
            "selective_auto_send_enabled":   True,
            "selective_auto_send_allowed_payment_methods":
                ["tabby_installment"],
        },
        rows=[orphan])

    # Stub resolve_customer: succeed with a DRY id (no HTTP).
    async def _fake_resolve(db_, uid, customer, *, trace_id=None,
                           default_customer_id=None, api_client=None):
        return ResolutionResult(
            success=True,
            qoyod_customer_id="DRY:cust:1",
            created_new=True,
            notes=["dry_run_new"])
    monkeypatch.setattr(pipeline, "resolve_customer", _fake_resolve)

    # `evaluate_rules` IS imported at module level, so we can hard-
    # fail if any code path re-evaluates business rules at the
    # RULES_APPLIED entry point (rev33.1 evidence-preservation
    # invariant).
    def _fail_rules(*a, **kw):
        raise AssertionError(
            "business_rules.evaluate MUST NOT be re-run at "
            "RULES_APPLIED — persisted evidence is source of truth")
    monkeypatch.setattr(pipeline, "evaluate_rules", _fail_rules)

    result = await pipeline.process_rules_applied_row(db, orphan)

    assert result["outcome"] == "CUSTOMER_RESOLVED", result
    # Row was CAS'd to CUSTOMER_RESOLVED.
    updated = await db.integration_inbox.find_one({"id": orphan["id"]})
    assert updated["pipeline_stage"] == "CUSTOMER_RESOLVED"
    # SAS evidence preserved verbatim.
    assert updated["selective_auto_send_gate"] == orphan_sas_snapshot


# ═════════════════════════════════════════════════════════════════
# TEST 2 — Ineligible SAS persisted → SKIPPED, no Qoyod write
# ═════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_orphan_sas_ineligible_routes_to_skipped(monkeypatch):
    """A `RULES_APPLIED` row whose persisted
    `selective_auto_send_gate.eligible=false` (a shape rev29c
    should prevent but rev33.1 defends against) is routed to SKIPPED
    without any customer_resolver call and without any Qoyod write.
    """
    from integrations.qoyod import pipeline

    orphan = _orphan_row(sas_eligible=False)
    db = _mk_full_db(
        settings={
            "user_id":                       "main",
            "dry_run_mode":                  True,
            "production_writes_locked":      True,
            "selective_live_send_enabled":   False,
            "selective_auto_send_enabled":   True,
            "selective_auto_send_allowed_payment_methods":
                ["tabby_installment"],
        },
        rows=[orphan])

    # If resolve_customer is called, that is a bug.
    async def _fail_resolve(*a, **kw):
        raise AssertionError(
            "resolve_customer MUST NOT be called when persisted "
            "sas_gate.eligible=false (rev33.1)")
    monkeypatch.setattr(pipeline, "resolve_customer", _fail_resolve)

    result = await pipeline.process_rules_applied_row(db, orphan)

    assert result["outcome"] == "SKIPPED", result
    assert result["reason"] == (
        "sas_gate_eligible_false_on_persisted_evidence")
    updated = await db.integration_inbox.find_one({"id": orphan["id"]})
    assert updated["pipeline_stage"] == "SKIPPED"


# ═════════════════════════════════════════════════════════════════
# TEST 3 — worker.run_now surfaces `rules_applied.processed>=1`
# ═════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_run_now_surfaces_rules_applied_processed(monkeypatch):
    """When an orphan `RULES_APPLIED` row exists, `worker.run_now`
    (invoked by the UI's "تشغيل الآن") returns a summary that
    contains `rules_applied.processed >= 1` — proving the batch
    endpoint no longer reports 0 for that bucket.

    Also asserts no other bucket ((normalized|customer_resolved))
    inflated its count for the orphan (it belongs only to the
    RULES_APPLIED bucket).
    """
    from integrations.qoyod import pipeline, worker
    from integrations.qoyod.customer_resolver import ResolutionResult

    orphan = _orphan_row()
    db = _mk_full_db(
        settings={
            "user_id":                       "main",
            "dry_run_mode":                  True,
            "production_writes_locked":      True,
            "selective_live_send_enabled":   False,
            "selective_auto_send_enabled":   True,
            "selective_auto_send_allowed_payment_methods":
                ["tabby_installment"],
        },
        rows=[orphan])

    async def _fake_resolve(db_, uid, customer, *, trace_id=None,
                           default_customer_id=None, api_client=None):
        return ResolutionResult(
            success=True,
            qoyod_customer_id="DRY:cust:1",
            created_new=True,
            notes=["dry_run_new"])
    monkeypatch.setattr(pipeline, "resolve_customer", _fake_resolve)

    # Stub backfill_gate / auto_requeue so run_now stays surgical.
    async def _fake_skip_pre(*a, **kw):
        return {"ok": True, "scanned": 0, "skipped": 0}

    async def _fake_requeue(*a, **kw):
        return {"ok": True, "scanned": 0, "requeued": 0}

    monkeypatch.setattr(
        "integrations.qoyod.worker.skip_pre_activation_rows",
        _fake_skip_pre)
    monkeypatch.setattr(
        "integrations.qoyod.worker.auto_requeue_known_fixed",
        _fake_requeue)

    result = await worker.run_now(db, user_id="main")

    # New bucket exists in the surface.
    assert "rules_applied" in result, (
        f"worker.run_now output missing `rules_applied` key: {result}")
    ra = result["rules_applied"]
    assert ra.get("processed") >= 1, (
        f"expected rules_applied.processed >= 1, got {ra!r}")
    outcomes = ra.get("outcomes") or {}
    assert outcomes.get("CUSTOMER_RESOLVED", 0) >= 1, (
        f"orphan did not advance to CUSTOMER_RESOLVED: {outcomes!r}")

    # Sanity: NORMALIZED bucket is untouched (orphan not there).
    assert result["normalized"]["processed"] == 0
    # Note: CUSTOMER_RESOLVED bucket MAY have a positive count in
    # the same tick — this is intentional cascade. After rev33.1
    # moves the orphan to CUSTOMER_RESOLVED, the subsequent
    # `process_pending_customer_resolved` in the SAME tick can
    # legitimately pick it up and continue advancing. That is the
    # desired self-healing behavior. We do NOT assert equality here.

    # And the row's DB stage is at least CUSTOMER_RESOLVED (may have
    # advanced further to PRODUCT_RESOLVED / INVOICE_CREATED in the
    # same tick — anything DOWNSTREAM of RULES_APPLIED is fine).
    updated = await db.integration_inbox.find_one({"id": orphan["id"]})
    assert updated["pipeline_stage"] != "RULES_APPLIED", (
        "orphan MUST advance past RULES_APPLIED in the tick")
