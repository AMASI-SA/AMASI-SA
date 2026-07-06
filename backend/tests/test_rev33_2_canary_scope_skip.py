"""Iter-2026-07.rev33.2 — Canary-scope BUSINESS-decision skip tests.

Incident evidence:
  • order=269997994
  • trace_id=cf802d6f28444fea942e815bb590bf8c
  • payment_method=mada
  • Canary state at time of incident:
      selective_live_send_enabled=true
      auto_send=false
      allowed=["tabby_installment"]
  • Observed: RULES_APPLIED → FAILED_CUSTOMER → DEAD_LETTER
  • Error: qoyod_write_locked on create_contact
  • Expected: SKIPPED (business decision), no write attempted.

Fix under test:
  A pre-check in `process_normalized_row` and
  `process_rules_applied_row` (BEFORE the Rev32 create_customer
  guard, BEFORE any Qoyod client is exercised) that:

    • Fires ONLY when `selective_live_send_enabled=true`.
    • Uses `_live_write_permitted(settings, payment_method=…)`
      already in the codebase — no new logic.
    • On `payment_method not in allowlist`: transitions the row
      to SKIPPED via CAS, stamps `canary_scope_skip` evidence,
      and returns outcome=SKIPPED with reason
      `canary_scope_skip_pm_not_in_allowlist`.

Acceptance:
  1) mada order under Tabby-only canary → SKIPPED, no
     `resolve_customer` invocation, no Qoyod write attempted.
  2) tabby_installment order under Tabby-only canary → proceeds
     to `resolve_customer` (behavior unchanged).
  3) mada order when canary is OFF → the pre-check is a no-op;
     row proceeds through the pre-existing SAS-gate path.
"""
from __future__ import annotations

import sys
from unittest.mock import MagicMock, AsyncMock

import pytest

sys.path.insert(0, "/app/backend")

# Reuse shared fixtures.
from tests.test_rev33_1_rules_applied_orphan_recovery import (  # noqa: E402
    _canonical_tabby, _orphan_row, _mk_full_db, _FakeInboxColl,
)


def _canonical_pm(payment_method="mada", order_id="269997994"):
    """Same as `_canonical_tabby` but with a configurable
    payment_method — the DTO already accepts any string in that
    field, so we surface a mada canonical to exercise the check."""
    c = _canonical_tabby(order_id=order_id)
    c["payment_method"]        = payment_method
    c["payment_method_native"] = payment_method
    return c


def _canary_active_settings(*, allow=("tabby_installment",)):
    return {
        "user_id":                                    "main",
        # Canary ACTIVE — the exact state at the incident.
        "dry_run_mode":                               False,
        "production_writes_locked":                   False,
        "selective_live_send_enabled":                True,
        "selective_auto_send_enabled":                True,
        "selective_auto_send_allowed_payment_methods":
            list(allow),
        # `auto_send` is separate — irrelevant to this check but
        # kept to mirror the enable-tabby precondition surface.
        "auto_send":                                  False,
    }


# ═════════════════════════════════════════════════════════════════
# TEST 1 — mada under Tabby-only canary → SKIPPED, no write
# ═════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_mada_under_tabby_canary_routes_to_skipped(monkeypatch):
    """A `RULES_APPLIED` row with `payment_method=mada` under a
    canary with `allowlist=["tabby_installment"]`:
      • MUST NOT call `resolve_customer`.
      • MUST NOT attempt any Qoyod HTTP write.
      • MUST transition to `pipeline_stage=SKIPPED` via CAS.
      • MUST return outcome=SKIPPED with reason
        `canary_scope_skip_pm_not_in_allowlist`.
      • MUST stamp `canary_scope_skip` evidence on the row.
    """
    from integrations.qoyod import pipeline

    # Row at RULES_APPLIED but with mada canonical.
    row = _orphan_row(row_id="row-mada-269997994")
    row["canonical_payload"] = _canonical_pm(
        payment_method="mada", order_id="269997994")
    row["trace_id"] = "cf802d6f28444fea942e815bb590bf8c"

    db = _mk_full_db(
        settings=_canary_active_settings(),
        rows=[row])

    # If `resolve_customer` is called, this test fails.
    async def _fail_resolve(*a, **kw):
        raise AssertionError(
            "rev33.2 invariant broken: resolve_customer was called "
            "for a non-allowlisted payment_method under active "
            "canary — no Qoyod write should be attempted.")
    monkeypatch.setattr(pipeline, "resolve_customer", _fail_resolve)

    result = await pipeline.process_rules_applied_row(db, row)

    assert result["outcome"] == "SKIPPED", result
    assert result["reason"] == "canary_scope_skip_pm_not_in_allowlist"
    assert result["payment_method"] == "mada"

    # DB state: SKIPPED + evidence.
    updated = await db.integration_inbox.find_one({"id": row["id"]})
    assert updated["pipeline_stage"] == "SKIPPED"
    evidence = updated.get("canary_scope_skip") or {}
    assert evidence.get("payment_method") == "mada"
    assert evidence.get("allowlist") == ["tabby_installment"]
    assert evidence.get("stage_when_skipped") == "RULES_APPLIED"
    assert evidence.get("detected_by") == "process_rules_applied_row"


# ═════════════════════════════════════════════════════════════════
# TEST 2 — tabby_installment under Tabby-only canary → unchanged
# ═════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_tabby_under_tabby_canary_behavior_unchanged(monkeypatch):
    """A `RULES_APPLIED` row with `payment_method=tabby_installment`
    under a Tabby-only canary must NOT be skipped — the rev33.2
    pre-check is a no-op for allowlisted payment methods. The row
    must proceed through `resolve_customer` and advance to
    CUSTOMER_RESOLVED (positive control that rev33.2 does not
    regress the Tabby flow)."""
    from integrations.qoyod import pipeline
    from integrations.qoyod.customer_resolver import ResolutionResult

    row = _orphan_row(row_id="row-tabby-happy")
    # DTO already has tabby_installment via _canonical_tabby().

    db = _mk_full_db(
        settings=_canary_active_settings(),
        rows=[row])

    called = {"n": 0}

    async def _happy_resolve(db_, uid, customer, *,
                             trace_id=None,
                             default_customer_id=None,
                             api_client=None):
        called["n"] += 1
        return ResolutionResult(
            success=True,
            qoyod_customer_id="DRY:cust:1",
            created_new=True)
    monkeypatch.setattr(pipeline, "resolve_customer", _happy_resolve)

    # Provide a DryRunQoyodClient explicitly so `_get_api_client`
    # isn't invoked (it would need a mocked credential encryption
    # path). This exercises the pipeline path where the caller
    # passed a client — same as `worker._one_round` for a live
    # canary tick.
    from integrations.qoyod.invoice_builder import DryRunQoyodClient
    result = await pipeline.process_rules_applied_row(
        db, row, api_client=DryRunQoyodClient())

    assert result["outcome"] == "CUSTOMER_RESOLVED", result
    assert called["n"] == 1, (
        "resolve_customer must be called exactly once for the "
        "allowlisted Tabby row")

    updated = await db.integration_inbox.find_one({"id": row["id"]})
    assert updated["pipeline_stage"] == "CUSTOMER_RESOLVED"
    # No canary_scope_skip evidence (that's for the mada-style path).
    assert "canary_scope_skip" not in updated


# ═════════════════════════════════════════════════════════════════
# TEST 3 — mada with canary OFF → pre-check is a no-op
# ═════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_mada_when_canary_off_precheck_is_noop(monkeypatch):
    """When `selective_live_send_enabled=false` (canary NOT
    active), the rev33.2 pre-check MUST NOT fire, even for a mada
    row. Behavior falls back to the pre-existing SAS-gate path
    at NORMALIZED — this test proves the pre-check is scoped
    strictly to the canary window."""
    from integrations.qoyod import pipeline
    from integrations.qoyod.customer_resolver import ResolutionResult

    row = _orphan_row(row_id="row-mada-canary-off")
    row["canonical_payload"] = _canonical_pm(
        payment_method="mada", order_id="269997995")

    settings = _canary_active_settings()
    # Canary OFF: pre-check must not fire.
    settings["selective_live_send_enabled"] = False
    settings["dry_run_mode"]                = True
    settings["production_writes_locked"]    = True

    db = _mk_full_db(settings=settings, rows=[row])

    # resolve_customer may or may not be called depending on the
    # SAS-gate-off code path — either is acceptable, so return
    # success. What we ASSERT is that rev33.2 does NOT route to
    # SKIPPED under this configuration.
    async def _happy_resolve(*a, **kw):
        return ResolutionResult(
            success=True, qoyod_customer_id="DRY:cust:x",
            created_new=True)
    monkeypatch.setattr(pipeline, "resolve_customer", _happy_resolve)

    result = await pipeline.process_rules_applied_row(db, row)

    # rev33.2 MUST NOT have fired — outcome should be
    # CUSTOMER_RESOLVED (dry-run happy path) or DEAD_LETTER (some
    # other reason), but NEVER SKIPPED with reason
    # `canary_scope_skip_pm_not_in_allowlist`.
    assert result.get("reason") != (
        "canary_scope_skip_pm_not_in_allowlist"), (
        f"rev33.2 pre-check must be scoped to canary window "
        f"only; fired incorrectly when canary is OFF: {result!r}")


# ═════════════════════════════════════════════════════════════════
# TEST 4 — Evidence stamped on the SKIPPED row (RCA trail)
# ═════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_mada_skip_stamps_forensic_evidence(monkeypatch):
    """The `canary_scope_skip` document persisted on the SKIPPED
    row must carry every field needed for a post-incident RCA:
      • reason (from _live_write_permitted)
      • at (timestamp)
      • payment_method
      • allowlist (full snapshot)
      • stage_when_skipped
      • detected_by (which function fired the skip)
    """
    from integrations.qoyod import pipeline

    row = _orphan_row(row_id="row-evidence-mada")
    row["canonical_payload"] = _canonical_pm(
        payment_method="mada", order_id="269997996")

    db = _mk_full_db(
        settings=_canary_active_settings(),
        rows=[row])

    async def _fail_resolve(*a, **kw):
        raise AssertionError("no Qoyod write allowed")
    monkeypatch.setattr(pipeline, "resolve_customer", _fail_resolve)

    await pipeline.process_rules_applied_row(db, row)

    updated = await db.integration_inbox.find_one({"id": row["id"]})
    ev = updated.get("canary_scope_skip") or {}
    # Verify every forensic field is present.
    for key in ("reason", "at", "payment_method",
                "allowlist", "stage_when_skipped", "detected_by"):
        assert key in ev, (
            f"forensic field {key!r} missing from "
            f"canary_scope_skip evidence: {ev!r}")
    # And the values match expectation.
    assert ev["payment_method"]      == "mada"
    assert ev["allowlist"]           == ["tabby_installment"]
    assert ev["stage_when_skipped"]  == "RULES_APPLIED"
    assert ev["detected_by"] in {
        "process_normalized_row",
        "process_rules_applied_row",
    }
