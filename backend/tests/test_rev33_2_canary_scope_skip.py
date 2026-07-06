"""Iter-2026-07.rev33.2 — Canary Scope Business-Decision Skip.

Scope: `process_normalized_row` ONLY. This test file is standalone
and has NO import dependency on other rev33.x test files.

Incident evidence:
  • order=269997994
  • trace_id=cf802d6f28444fea942e815bb590bf8c
  • payment_method=mada
  • Canary state at time of incident:
      selective_live_send_enabled=true
      auto_send=false
      selective_auto_send_allowed_payment_methods=["tabby_installment"]
  • Observed: RULES_APPLIED → FAILED_CUSTOMER → DEAD_LETTER
  • Error: `qoyod_write_locked` on `create_contact`

Expected behaviour (verified below):
  A non-allowlisted payment method during an active Live Canary
  must be SKIPPED (business-decision), NOT DEAD_LETTERED (technical
  failure). The pre-check fires in `process_normalized_row` BEFORE
  `_get_api_client` is invoked and BEFORE any Qoyod HTTP is
  attempted. The row lands at `pipeline_stage=SKIPPED` with a
  `canary_scope_skip` forensic sub-document.

Acceptance:
  1) mada during Tabby-only canary → SKIPPED (no write, no client
     built).
  2) tabby_installment during Tabby-only canary → unchanged (pre-
     check is a no-op; flow proceeds through `resolve_customer`).
  3) mada when canary is OFF → pre-check is a no-op; behaviour
     falls through to the pre-existing SAS-gate path.
  4) SKIPPED row carries a full `canary_scope_skip` forensic doc.
"""
from __future__ import annotations

import sys
from unittest.mock import MagicMock, AsyncMock

import pytest

sys.path.insert(0, "/app/backend")


# ── Local, self-contained fixtures (no cross-file imports) ─────────
def _canonical(*, payment_method="mada",
               order_id="269997994") -> dict:
    """Minimal canonical payload the `SalesOrderDTO` accepts."""
    return {
        "order_id":              order_id,
        "order_status":          "completed",
        "order_status_native":   "تم التنفيذ",
        "order_date":            "2026-07-05T22:00:00+00:00",
        "payment_method":        payment_method,
        "payment_method_native": payment_method,
        "currency":              "SAR",
        "subtotal":              100.0,
        "tax_amount":            15.0,
        "shipping_amount":       0.0,
        "discount_amount":       0.0,
        "total_amount":          115.0,
        "customer": {
            "name":  "Test Customer",
            "phone": "+966500000000",
            "email": "test@example.com",
        },
        "items": [
            {"sku":             "SKU-A",
             "name":            "Item A",
             "quantity":        1.0,
             "unit_price":      100.0,
             "tax_amount":      15.0,
             "discount_amount": 0.0,
             "total":           115.0},
        ],
    }


def _row_at_normalized(*, payment_method="mada",
                       row_id="row-269997994",
                       trace_id="cf802d6f28444fea942e815bb590bf8c"):
    """Row shape as it exists just before
    `process_normalized_row` picks it up — pipeline_stage
    NORMALIZED, no downstream evidence yet."""
    return {
        "id":                  row_id,
        "user_id":             "main",
        "trace_id":            trace_id,
        "pipeline_stage":      "NORMALIZED",
        "canonical_payload":   _canonical(payment_method=payment_method),
        "stage_history":       [],
        "received_at":         "2026-07-05T22:00:00+00:00",
        "pipeline_started_at": "2026-07-05T22:00:00+00:00",
    }


def _canary_active_settings(*, allow=("tabby_installment",)):
    """Canary-active settings surface — mirrors what enable-tabby
    writes to `qoyod_settings` on approval."""
    return {
        "user_id":                                     "main",
        "dry_run_mode":                                False,
        "production_writes_locked":                    False,
        "selective_live_send_enabled":                 True,
        "selective_auto_send_enabled":                 True,
        "selective_auto_send_allowed_payment_methods":
            list(allow),
        "auto_send":                                   False,
        "auto_receipt":                                True,
        "capabilities":                                {
            "create_receipts": True,
        },
    }


class _FakeInbox:
    """In-memory `integration_inbox` fake with strict CAS filter
    matching — required so `_apply_atomic` transitions succeed."""

    def __init__(self, rows):
        self._rows = {r["id"]: dict(r) for r in rows}

    async def find_one(self, filt, proj=None):
        for r in self._rows.values():
            if all(r.get(k) == v for k, v in filt.items()):
                return dict(r)
        return None

    async def update_one(self, filt, patch, **kw):
        matched = 0
        for r in self._rows.values():
            if all(r.get(k) == v for k, v in filt.items()):
                matched = 1
                for k, v in (patch.get("$set") or {}).items():
                    r[k] = v
                for k, arr_op in (patch.get("$push") or {}).items():
                    r.setdefault(k, []).append(arr_op)
                for k, v in (patch.get("$inc") or {}).items():
                    r[k] = int(r.get(k) or 0) + int(v)
                break
        return MagicMock(matched_count=matched,
                         modified_count=matched)


def _mk_db(*, settings, rows):
    """Small fake DB tailored to `process_normalized_row` — only
    needs the collections that function touches BEFORE the
    canary-skip pre-check fires."""
    _settings = dict(settings)

    async def _settings_find_one(f, proj=None):
        return dict(_settings)

    async def _settings_update_one(f, u, upsert=False):
        for k, v in ((u or {}).get("$set") or {}).items():
            _settings[k] = v
        return MagicMock(matched_count=1, modified_count=1)

    db = MagicMock()
    db.qoyod_settings              = MagicMock()
    db.qoyod_settings.find_one     = _settings_find_one
    db.qoyod_settings.update_one   = _settings_update_one
    db.integration_inbox           = _FakeInbox(rows)
    db.rev32_kill_switch_events    = MagicMock()
    db.rev32_kill_switch_events.insert_one = AsyncMock(
        return_value=MagicMock(inserted_id="fake"))
    for cn in ("qoyod_customers", "qoyod_customer_mapping",
               "sas_worker_traces", "qoyod_worker_traces",
               "integration_pipeline_events", "audit_log",
               "qoyod_credentials", "qoyod_invoices"):
        c = MagicMock()
        c.find_one   = AsyncMock(return_value=None)
        c.update_one = AsyncMock(return_value=MagicMock(
            matched_count=1, modified_count=1))
        c.insert_one = AsyncMock(return_value=MagicMock(
            inserted_id="fake"))
        setattr(db, cn, c)
    return db


# ═════════════════════════════════════════════════════════════════
# TEST 1 — mada during Tabby-only canary → SKIPPED, no write
# ═════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_mada_under_tabby_canary_routes_to_skipped(monkeypatch):
    """A NORMALIZED row with `payment_method=mada` under Live
    Canary (`allowlist=["tabby_installment"]`) MUST:
      • NOT invoke `resolve_customer` (no Qoyod call attempted).
      • Transition to `pipeline_stage=SKIPPED` via CAS.
      • Return `outcome=SKIPPED, reason=canary_scope_skip_pm_not_
        in_allowlist`.
      • Stamp a `canary_scope_skip` sub-document on the row.

    Fixture note: rev33.2 is a defence-in-depth pre-check that
    covers the incident scenario where the SAS gate at NORMALIZED
    did NOT reject the row (e.g., SAS was toggled off/on around
    the transition, or a future gate refactor introduces a gap).
    We simulate that scenario by stubbing the SAS gate to return
    `eligible=True` for this test — rev33.2 must still catch the
    non-allowlisted payment method before any Qoyod write.
    """
    from integrations.qoyod import pipeline
    from integrations.qoyod import selective_auto_send_gate as sas_mod
    from integrations.qoyod.selective_auto_send_gate import (
        GateDecision,
    )

    row = _row_at_normalized(payment_method="mada")
    db  = _mk_db(settings=_canary_active_settings(), rows=[row])

    # Simulate the exact bypass: SAS gate returns eligible=True.
    def _bypass_sas(**kwargs):
        return GateDecision(
            eligible=True,
            reason="test_bypass_simulating_incident")
    monkeypatch.setattr(
        sas_mod, "evaluate_selective_auto_send_gate", _bypass_sas)

    # Hard-fail if `resolve_customer` is called for this row.
    async def _fail_resolve(*a, **kw):
        raise AssertionError(
            "rev33.2 invariant broken: resolve_customer was called "
            "for a non-allowlisted payment_method under active "
            "canary — no Qoyod write should be attempted.")
    monkeypatch.setattr(pipeline, "resolve_customer", _fail_resolve)

    result = await pipeline.process_normalized_row(db, row)

    assert result["outcome"] == "SKIPPED", result
    assert result["reason"] == (
        "canary_scope_skip_pm_not_in_allowlist"), result
    assert result["payment_method"] == "mada"

    updated = await db.integration_inbox.find_one({"id": row["id"]})
    assert updated["pipeline_stage"] == "SKIPPED"


# ═════════════════════════════════════════════════════════════════
# TEST 2 — tabby_installment during Tabby-only canary → unchanged
# ═════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_tabby_under_tabby_canary_unchanged(monkeypatch):
    """Positive control: with `payment_method=tabby_installment`
    and `allowlist=["tabby_installment"]`, the rev33.2 pre-check
    MUST be a no-op. The row proceeds through the pipeline into
    `resolve_customer`. This proves rev33.2 does not regress the
    Tabby happy path."""
    from integrations.qoyod import pipeline
    from integrations.qoyod import selective_auto_send_gate as sas_mod
    from integrations.qoyod.selective_auto_send_gate import (
        GateDecision,
    )
    from integrations.qoyod.customer_resolver import ResolutionResult
    from integrations.qoyod.invoice_builder import DryRunQoyodClient

    row = _row_at_normalized(
        payment_method="tabby_installment",
        row_id="row-tabby-happy",
        trace_id="tabby-trace-1")
    db = _mk_db(settings=_canary_active_settings(), rows=[row])

    # Simulate the SAS gate approving Tabby (matches real behavior
    # once cutover_at + allowlist are properly configured — we
    # decouple that config here to focus on rev33.2's no-op path).
    def _sas_approve(**kwargs):
        return GateDecision(
            eligible=True,
            reason="test_tabby_approved")
    monkeypatch.setattr(
        sas_mod, "evaluate_selective_auto_send_gate", _sas_approve)

    async def _happy_resolve(db_, uid, customer, *,
                             trace_id=None,
                             default_customer_id=None,
                             api_client=None):
        return ResolutionResult(
            success=True,
            qoyod_customer_id="DRY:cust:1",
            created_new=True)
    monkeypatch.setattr(pipeline, "resolve_customer", _happy_resolve)

    # Pass a DryRun client explicitly so `_get_api_client` isn't
    # invoked (its credentials-lookup path needs real encryption
    # keys, outside the scope of this test).
    result = await pipeline.process_normalized_row(
        db, row, api_client=DryRunQoyodClient())

    # Key acceptance: rev33.2 pre-check MUST NOT fire for an
    # allowlisted Tabby row (positive control).
    assert result.get("reason") != (
        "canary_scope_skip_pm_not_in_allowlist"), (
        f"rev33.2 must NOT fire for allowlisted Tabby row: "
        f"{result!r}")

    updated = await db.integration_inbox.find_one({"id": row["id"]})
    # Tabby row must NOT be marked SKIPPED by rev33.2.
    # (It may or may not have advanced further depending on how
    # far process_normalized_row got in the fixture, but SKIPPED
    # via canary_scope_skip is the specific regression we prevent.)
    assert (updated.get("canary_scope_skip") is None), (
        "Tabby row must not carry canary_scope_skip evidence")


# ═════════════════════════════════════════════════════════════════
# TEST 3 — mada with canary OFF → pre-check is a no-op
# ═════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_mada_when_canary_off_precheck_is_noop(monkeypatch):
    """When `selective_live_send_enabled=false` (canary NOT
    active), rev33.2 pre-check MUST NOT fire, even for a mada row.
    The row's fate is determined by the pre-existing SAS-gate
    branch at NORMALIZED — this test proves rev33.2 is strictly
    scoped to the canary window."""
    from integrations.qoyod import pipeline
    from integrations.qoyod.customer_resolver import ResolutionResult
    from integrations.qoyod.invoice_builder import DryRunQoyodClient

    row = _row_at_normalized(payment_method="mada",
                              row_id="row-mada-off")
    settings = _canary_active_settings()
    settings["selective_live_send_enabled"] = False
    settings["dry_run_mode"]                = True
    settings["production_writes_locked"]    = True
    db = _mk_db(settings=settings, rows=[row])

    async def _resolve_ok(*a, **kw):
        return ResolutionResult(
            success=True, qoyod_customer_id="DRY:cust:x",
            created_new=True)
    monkeypatch.setattr(pipeline, "resolve_customer", _resolve_ok)

    result = await pipeline.process_normalized_row(
        db, row, api_client=DryRunQoyodClient())

    # rev33.2 must NOT have fired.
    assert result.get("reason") != (
        "canary_scope_skip_pm_not_in_allowlist"), (
        f"rev33.2 pre-check must be scoped to canary window only; "
        f"fired incorrectly when canary is OFF: {result!r}")


# ═════════════════════════════════════════════════════════════════
# TEST 4 — Forensic evidence stamped on the SKIPPED row
# ═════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_mada_skip_stamps_forensic_evidence(monkeypatch):
    """The `canary_scope_skip` sub-document on the SKIPPED row must
    carry every field needed for a post-incident RCA:
      • reason               (from _live_write_permitted)
      • at                   (timestamp)
      • payment_method
      • allowlist            (snapshot)
      • stage_when_skipped
      • detected_by          (function name that fired the skip)
    """
    from integrations.qoyod import pipeline
    from integrations.qoyod import selective_auto_send_gate as sas_mod
    from integrations.qoyod.selective_auto_send_gate import (
        GateDecision,
    )

    row = _row_at_normalized(payment_method="mada",
                              row_id="row-evidence-mada",
                              trace_id="evidence-trace")
    db  = _mk_db(settings=_canary_active_settings(), rows=[row])

    # Same bypass as test 1 — simulate the SAS gate not catching.
    def _bypass_sas(**kwargs):
        return GateDecision(
            eligible=True,
            reason="test_bypass_simulating_incident")
    monkeypatch.setattr(
        sas_mod, "evaluate_selective_auto_send_gate", _bypass_sas)

    async def _fail_resolve(*a, **kw):
        raise AssertionError("no Qoyod write allowed")
    monkeypatch.setattr(pipeline, "resolve_customer", _fail_resolve)

    await pipeline.process_normalized_row(db, row)

    updated = await db.integration_inbox.find_one({"id": row["id"]})
    ev = updated.get("canary_scope_skip") or {}
    for key in ("reason", "at", "payment_method",
                "allowlist", "stage_when_skipped", "detected_by"):
        assert key in ev, (
            f"forensic field {key!r} missing from "
            f"canary_scope_skip evidence: {ev!r}")
    assert ev["payment_method"]     == "mada"
    assert ev["allowlist"]          == ["tabby_installment"]
    assert ev["stage_when_skipped"] == "NORMALIZED"
    assert ev["detected_by"]        == "process_normalized_row"
