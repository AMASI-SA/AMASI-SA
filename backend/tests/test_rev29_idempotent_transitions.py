"""Iter-2026-02.rev29 — Idempotent transitions via atomic CAS.

After order 270219411 (trace 8676b9e0...) showed duplicate stage
transitions in `stage_history` — even though live-write and gate
persist were correct — we enforce ATOMIC compare-and-set on every
post-NORMALIZED transition. A stale worker snapshot can no longer
re-emit a stage transition.
"""
from __future__ import annotations

import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, "/app/backend")


class _AtomicColl:
    def __init__(self, docs):
        self._docs = list(docs)

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
                for k, v in (u.get("$set") or {}).items(): d[k] = v
                for k, v in (u.get("$push") or {}).items():
                    arr = d.setdefault(k, [])
                    if isinstance(v, dict) and "$each" in v:
                        arr.extend(v["$each"])
                    else: arr.append(v)
                break
        return MagicMock(matched_count=matched, modified_count=matched)


# ── Test 1: _apply_atomic works for every transition pair ────────
@pytest.mark.asyncio
@pytest.mark.parametrize("expected_from,to_stage", [
    ("NORMALIZED",              "RULES_APPLIED"),
    ("RULES_APPLIED",           "CUSTOMER_RESOLVED"),
    ("CUSTOMER_RESOLVED",       "PRODUCT_RESOLVED"),
    ("PRODUCT_RESOLVED",        "INVOICE_CREATED"),
    ("INVOICE_CREATED",         "INVOICE_PAYMENT_CREATED"),
    ("INVOICE_PAYMENT_CREATED", "COMPLETED"),
])
async def test_1_atomic_transition_succeeds_when_stage_matches(
    expected_from, to_stage,
):
    from integrations.qoyod.pipeline import _apply_atomic
    row = {"id": "r1", "pipeline_stage": expected_from}
    db = MagicMock()
    db.integration_inbox = _AtomicColl([row])
    await _apply_atomic(
        db, "r1",
        {"$set": {"pipeline_stage": to_stage}},
        expected_from_stage=expected_from,
    )
    updated = await db.integration_inbox.find_one({"id": "r1"})
    assert updated["pipeline_stage"] == to_stage


# ── Test 2: stale worker cannot rewrite already-advanced stage ───
@pytest.mark.asyncio
@pytest.mark.parametrize("expected_from,to_stage", [
    ("RULES_APPLIED",     "CUSTOMER_RESOLVED"),
    ("CUSTOMER_RESOLVED", "PRODUCT_RESOLVED"),
    ("PRODUCT_RESOLVED",  "INVOICE_CREATED"),
    ("INVOICE_CREATED",   "INVOICE_PAYMENT_CREATED"),
])
async def test_2_stale_worker_cannot_replay_transition(
    expected_from, to_stage,
):
    """The row is ALREADY at `to_stage` (advanced by another worker).
    A stale worker with a snapshot at `expected_from` MUST fail CAS
    and raise `_StaleStageError`."""
    from integrations.qoyod.pipeline import (
        _apply_atomic, _StaleStageError,
    )
    row = {"id": "r2", "pipeline_stage": to_stage}
    db = MagicMock()
    db.integration_inbox = _AtomicColl([row])
    with pytest.raises(_StaleStageError) as ei:
        await _apply_atomic(
            db, "r2",
            {"$set": {"pipeline_stage": to_stage}},
            expected_from_stage=expected_from,
        )
    assert ei.value.expected_from == expected_from
    assert ei.value.actual        == to_stage


# ── Test 3: diagnostics detects duplicate transitions ────────────
@pytest.mark.asyncio
async def test_3_diagnostics_reports_duplicate_stage_transition():
    """The exact repro from order 270219411."""
    from integrations.qoyod.sas_build_diagnostics import row_diagnostics
    dup_row = {
        "id":              "row-dup",
        "trace_id":        "8676b9e0c564456496ba2ec6dfc9f994",
        "pipeline_stage":  "INVOICE_CREATED",
        "user_id":         "main",
        "qoyod_invoice_id": "DRY:invoice:d811949d",
        "canonical_payload": {"payment_method": "tabby_installment"},
        "selective_auto_send_gate": {"eligible": True, "reason": "eligible"},
        "stage_history": [
            {"from_stage": "NORMALIZED",         "to_stage": "RULES_APPLIED"},
            {"from_stage": "NORMALIZED",         "to_stage": "RULES_APPLIED"},   # DUP!
            {"from_stage": "RULES_APPLIED",      "to_stage": "CUSTOMER_RESOLVED"},
            {"from_stage": "RULES_APPLIED",      "to_stage": "CUSTOMER_RESOLVED"}, # DUP!
            {"from_stage": "CUSTOMER_RESOLVED",  "to_stage": "PRODUCT_RESOLVED"},
            {"from_stage": "PRODUCT_RESOLVED",   "to_stage": "INVOICE_CREATED"},
        ],
    }
    fake_settings = {
        "selective_auto_send_enabled":  True,
        "dry_run_mode":                 True,
        "selective_live_send_enabled":  False,
    }
    db = MagicMock()
    db.integration_inbox.find_one = AsyncMock(return_value=dup_row)
    db.qoyod_settings.find_one   = AsyncMock(return_value=fake_settings)

    out = await row_diagnostics(db, dup_row["trace_id"])
    assert out["diagnosis"]["duplicate_stage_transition_violation"] is True
    reason = out["diagnosis"]["duplicate_stage_transition_reason"]
    assert reason is not None
    assert "trace_id" in reason
    assert "RULES_APPLIED" in reason or "CUSTOMER_RESOLVED" in reason


# ── Test 4: no duplicates → no violation ─────────────────────────
@pytest.mark.asyncio
async def test_4_diagnostics_no_violation_on_clean_history():
    from integrations.qoyod.sas_build_diagnostics import row_diagnostics
    clean_row = {
        "id":              "row-clean",
        "trace_id":        "tr-clean",
        "pipeline_stage":  "COMPLETED",
        "stage_history": [
            {"from_stage": "NORMALIZED",         "to_stage": "RULES_APPLIED"},
            {"from_stage": "RULES_APPLIED",      "to_stage": "CUSTOMER_RESOLVED"},
            {"from_stage": "CUSTOMER_RESOLVED",  "to_stage": "PRODUCT_RESOLVED"},
            {"from_stage": "PRODUCT_RESOLVED",   "to_stage": "INVOICE_CREATED"},
            {"from_stage": "INVOICE_CREATED",    "to_stage": "INVOICE_PAYMENT_CREATED"},
            {"from_stage": "INVOICE_PAYMENT_CREATED", "to_stage": "COMPLETED"},
        ],
    }
    db = MagicMock()
    db.integration_inbox.find_one = AsyncMock(return_value=clean_row)
    out = await row_diagnostics(db, "tr-clean")
    assert out["diagnosis"]["duplicate_stage_transition_violation"] is False
    assert out["diagnosis"]["duplicate_stage_transition_reason"] is None


# ── Test 5: markers include all rev29 atomic CAS points ──────────
def test_5_all_rev29_markers_registered():
    from integrations.qoyod.sas_build_diagnostics import (
        REQUIRED_MARKERS, build_diagnostics_report,
    )
    for mid in (
        "rev29_atomic_customer_resolved",
        "rev29_atomic_product_resolved",
        "rev29_atomic_invoice_created",
        "rev29_atomic_invoice_payment",
        "rev29_atomic_completed",
    ):
        assert mid in REQUIRED_MARKERS
    r = build_diagnostics_report()
    assert r["acceptance"]["code_matches_expected"] is True, (
        f"missing rev29 markers in RUNNING pipeline: "
        f"{r['marker_check']['markers']}")


# ── Test 6: DRY-RUN wording is present in pipeline source ────────
def test_6_dry_run_wording_in_source():
    """The three DRY-RUN notes MUST exist in pipeline.py source so
    that when the resolved id is DRY:*, the stage_history reflects
    reality (no false 'created in Qoyod' text)."""
    import inspect
    from integrations.qoyod import pipeline as pmod
    src = inspect.getsource(pmod)
    assert "DRY-RUN: customer payload built, no POST" in src
    assert "DRY-RUN: customer mapped from local store" in src
    assert "DRY-RUN: invoice payload built, no POST" in src
    assert "product payload(s) built" in src


# ── Test 7: previous invariants (rev27/28) still hold ────────────
def test_7_previous_invariants_intact():
    from integrations.qoyod.pipeline import _live_write_permitted
    ok, reason = _live_write_permitted({
        "dry_run_mode":                 False,
        "selective_live_send_enabled":  True,
        "production_writes_locked":     False,
        "selective_auto_send_enabled":  True,
    })
    assert ok is True and reason == "all_gates_permit_live_write"
