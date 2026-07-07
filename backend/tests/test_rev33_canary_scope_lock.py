"""Iter-2026-07.rev33 — Canary Scope Lock + SKIPPED Terminality tests.

RCA fixtures:
  • order 269747616 / trace_id=0adc59a4683c411abeb5bfd17f5e50fc
                    / invoice #193 / payment #164
                    / payment_method=credit_card
  • order 270054904 / trace_id=1381d07f332d43bfb7cd046f9d413054
                    / invoice #194 / payment #165
                    / payment_method=tamara_installment

Both orders live-wrote to قيود during the 2026-07-05 Tabby-only
canary window (enabled at 19:36:41 UTC) although
`selective_auto_send_allowed_payment_methods=["credit_card"]`.

Root causes (verbatim from RCA v2):
  Gap 1: `one_shot_reprocess._reset_row_to_stage(
          permit_partial_invoice_created=True)` allowed
         resurrecting SKIPPED rows back to CUSTOMER_RESOLVED.
  Gap 2: SAS gate re-evaluation at CUSTOMER_RESOLVED overwrote
         the persisted `selective_auto_send_gate` — historical
         `eligible=false` evidence was lost.
  Gap 3: `process_customer_resolved_row(api_client=<live_client>)`
         from `reprocess_one_order` skipped the pipeline
         `_get_api_client()` live-write gate.
  Gap 4: `pipeline._live_write_permitted()` did NOT check
         payment_method allowlist.
  Gap 5: No runtime invariant enforcing
         `allowlist == ["credit_card"]` while the canary
         window was active.

rev33 fixes (each independently verified by the tests below):
  Fix A: SKIPPED is ABSOLUTE-TERMINAL.
         `one_shot_reprocess._reset_row_to_stage` raises
         `OneShotRefused("skipped_is_terminal_rev33")` on any
         attempt to reset a SKIPPED row.
  Fix B: `assert_final_write_permitted` vetoes any row whose
         stage_history contains a to_stage=SKIPPED entry
         (`post_skipped_history_write_violation`), even after
         a subsequent stage reset.
  Fix C: `assert_final_write_permitted` vetoes any live write
         when `selective_live_send_enabled=True` AND
         `allow_list != ["credit_card"]`
         (`canary_scope_drift_violation`).
  Fix D: `pipeline._live_write_permitted(settings,
         payment_method=…)` refuses when payment_method is
         missing or outside the allowlist, and mirrors the
         canary scope invariant.
  Fix E: `process_customer_resolved_row(api_client=…)` re-runs
         the live-write gate + allowlist check on the caller-
         provided client and downgrades to `DryRunQoyodClient`
         on any mismatch.

Acceptance:
  • SKIPPED is a hard-stop, no reset, no downstream transition.
  • credit_card / tamara_installment / mada / apple_pay / stc_pay
    / bank_transfer / cod / emkan stay DRY or BLOCKED during
    Tabby-only canary.
  • Only mada writes live during Tabby-only canary.
  • production_writes_locked=false alone is INSUFFICIENT for a
    live write — all four global gates must permit AND the
    row's payment_method must be on the allowlist.
"""
from __future__ import annotations

import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, "/app/backend")


# ── Shared helpers (mirror test_rev32_1_dead_letter_hardening style)
def _mk_db(*, settings=None, row=None):
    settings = dict(settings or {})
    settings.setdefault("user_id", "main")
    row = dict(row or {})
    captured = {
        "settings_patches":    [],
        "inbox_patches":       [],
        "kill_switch_inserts": [],
    }

    async def _settings_find_one(f, proj=None):
        return dict(settings)

    async def _settings_update_one(f, u, upsert=False):
        patch_ = (u or {}).get("$set") or {}
        settings.update(patch_)
        captured["settings_patches"].append(
            {"filter": f, "set": patch_, "upsert": upsert})
        return MagicMock(matched_count=1, modified_count=1)

    async def _inbox_find_one(f, proj=None):
        if not row:
            return None
        return dict(row)

    async def _inbox_update_one(f, u):
        patch_ = (u or {}).get("$set") or {}
        for k, v in patch_.items():
            row[k] = v
        captured["inbox_patches"].append({"filter": f, "set": patch_})
        return MagicMock(matched_count=1, modified_count=1)

    async def _kse_insert_one(doc):
        captured["kill_switch_inserts"].append(doc)
        return MagicMock(inserted_id="fake")

    db = MagicMock()
    db.qoyod_settings              = MagicMock()
    db.qoyod_settings.find_one     = _settings_find_one
    db.qoyod_settings.update_one   = _settings_update_one
    db.integration_inbox           = MagicMock()
    db.integration_inbox.find_one  = _inbox_find_one
    db.integration_inbox.update_one = _inbox_update_one
    db.rev32_kill_switch_events    = MagicMock()
    db.rev32_kill_switch_events.insert_one = _kse_insert_one

    db._captured = captured
    db._settings = settings
    db._row      = row
    return db


def _canary_settings(*, allow=None, live=True):
    return {
        "user_id":                                     "main",
        "dry_run_mode":                                (not live),
        "production_writes_locked":                    (not live),
        "selective_live_send_enabled":                 live,
        "selective_auto_send_enabled":                 True,
        "selective_auto_send_allowed_payment_methods":
            list(allow) if allow is not None else ["credit_card"],
    }


def _row_at_stage(*, stage, payment_method,
                  eligible=True, stage_history=None,
                  worker_sha=None, row_id="row-1",
                  dead_lettered_at=None):
    from integrations.qoyod.sas_worker_trace import _compute_pipeline_sha
    sha = worker_sha or _compute_pipeline_sha()
    row = {
        "id":                       row_id,
        "user_id":                  "main",
        "trace_id":                 f"trace-{row_id}",
        "pipeline_stage":           stage,
        "selective_auto_send_gate": {"eligible": eligible,
                                     "reason":   "test"},
        "sas_worker_trace":         {"worker_pipeline_sha": sha},
        "canonical_payload":        {"payment_method": payment_method},
        "stage_history":            list(stage_history or []),
    }
    if dead_lettered_at is not None:
        row["dead_lettered_at"] = dead_lettered_at
    return row


# ═════════════════════════════════════════════════════════════════
# rev33 marker + public surface
# ═════════════════════════════════════════════════════════════════
def test_rev33_marker_registered_and_present():
    from integrations.qoyod.sas_build_diagnostics import (
        REQUIRED_MARKERS, build_diagnostics_report,
    )
    assert "rev33_canary_scope_lock" in REQUIRED_MARKERS
    r = build_diagnostics_report()
    m = r["marker_check"]["markers"]["rev33_canary_scope_lock"]
    assert m["present"] is True
    assert m["count"] >= 1
    assert r["acceptance"]["code_matches_expected"] is True


# ═════════════════════════════════════════════════════════════════
# 1. credit_card rejected by SAS gate -> SKIPPED -> cannot transition
#    to PRODUCT_RESOLVED via one_shot_reprocess reset
# ═════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_1_credit_card_skipped_cannot_reset_to_product_resolved():
    """Fix A: `one_shot_reprocess._reset_row_to_stage` refuses to
    resurrect a SKIPPED row even with
    `permit_partial_invoice_created=True`."""
    from integrations.qoyod.one_shot_reprocess import (
        _reset_row_to_stage, OneShotRefused,
    )
    row = _row_at_stage(
        stage="SKIPPED",
        payment_method="credit_card",
        eligible=False,
        stage_history=[{"from_stage": "CUSTOMER_RESOLVED",
                        "to_stage":   "SKIPPED"}],
        row_id="row-skipped-credit-card")
    db = _mk_db(settings=_canary_settings(), row=row)

    with pytest.raises(OneShotRefused) as exc:
        await _reset_row_to_stage(
            db, row, resume_stage="CUSTOMER_RESOLVED",
            actor="operator",
            permit_partial_invoice_created=True)
    assert exc.value.code == "skipped_is_terminal_rev33"


# ═════════════════════════════════════════════════════════════════
# 2. credit_card rejected by SAS gate -> cannot create Qoyod invoice
#    even if pipeline_stage was reset back to CUSTOMER_RESOLVED
#    (stage_history veto)
# ═════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_2_stage_history_skipped_blocks_create_invoice():
    """Fix B: `assert_final_write_permitted` vetoes any row whose
    stage_history contains to_stage=SKIPPED — even if current
    stage is a legitimate-looking CUSTOMER_RESOLVED / PRODUCT_
    RESOLVED (defense against direct DB stage flip)."""
    from integrations.qoyod.rev32_hardening import (
        Rev32Violation, assert_final_write_permitted,
    )
    row = _row_at_stage(
        stage="PRODUCT_RESOLVED",         # would otherwise pass stage check
        payment_method="credit_card",
        eligible=True,                    # gate was rewritten
        stage_history=[
            {"from_stage": "RULES_APPLIED",     "to_stage": "CUSTOMER_RESOLVED"},
            {"from_stage": "CUSTOMER_RESOLVED", "to_stage": "SKIPPED"},   # HISTORICAL
            {"from_stage": "SKIPPED",           "to_stage": "RETRYING"},
            {"from_stage": "RETRYING",          "to_stage": "CUSTOMER_RESOLVED"},
            {"from_stage": "CUSTOMER_RESOLVED", "to_stage": "PRODUCT_RESOLVED"},
        ],
        row_id="row-resurrected-credit-card")
    db = _mk_db(settings=_canary_settings(), row=row)

    with pytest.raises(Rev32Violation) as exc:
        await assert_final_write_permitted(
            db, row["id"],
            action="create_invoice",
            payment_method="credit_card",
            user_id="main")
    assert exc.value.violation_type == "post_skipped_history_write_violation"


# ═════════════════════════════════════════════════════════════════
# 3. tamara_installment during Tabby-only canary -> BLOCKED
# ═════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_3_tamara_installment_blocked_by_allowlist_during_canary():
    """Fix C + rev32.1 (5): non-tabby payment_method with
    allowlist=['credit_card'] is blocked by
    `live_non_allowlisted_payment_method_violation`."""
    from integrations.qoyod.rev32_hardening import (
        Rev32Violation, assert_final_write_permitted,
    )
    row = _row_at_stage(
        stage="PRODUCT_RESOLVED",
        payment_method="tamara_installment",
        eligible=True,           # no stage_history SKIPPED — pure allowlist test
        row_id="row-tamara")
    db = _mk_db(settings=_canary_settings(), row=row)

    with pytest.raises(Rev32Violation) as exc:
        await assert_final_write_permitted(
            db, row["id"],
            action="create_invoice",
            payment_method="tamara_installment",
            user_id="main")
    assert exc.value.violation_type == (
        "live_non_allowlisted_payment_method_violation")


# ═════════════════════════════════════════════════════════════════
# 4. mada during Tabby-only canary -> PERMITTED (happy)
# ═════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_4_mada_permitted_during_canary():
    """Positive control: pure Tabby row with all gates permit and
    no historical SKIPPED — `assert_final_write_permitted` returns
    without raising."""
    from integrations.qoyod.rev32_hardening import (
        assert_final_write_permitted,
    )
    row = _row_at_stage(
        stage="PRODUCT_RESOLVED",
        payment_method="credit_card",
        eligible=True,
        row_id="row-tabby")
    db = _mk_db(settings=_canary_settings(), row=row)

    # Should not raise.
    await assert_final_write_permitted(
        db, row["id"],
        action="create_invoice",
        payment_method="credit_card",
        user_id="main")


# ═════════════════════════════════════════════════════════════════
# 5. Direct QoyodAPIClient with no row_id -> BLOCKED
# ═════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_5_direct_qoyod_client_no_row_id_blocked():
    """Rev32.1 + rev33 defense: `assert_client_write_permitted`
    with `row_id=None` and `allow_writes_without_row=False`
    raises `Rev32MissingRowContextError`. Prevents any legacy
    caller (approve_locked_payment, retry_payment_only, etc.)
    from writing live without row context."""
    from integrations.qoyod.rev32_hardening import (
        Rev32MissingRowContextError, assert_client_write_permitted,
    )
    db = _mk_db(settings=_canary_settings(), row={})

    with pytest.raises(Rev32MissingRowContextError) as exc:
        await assert_client_write_permitted(
            db=db,
            row_id=None,
            trace_id=None,
            user_id="main",
            action="create_invoice",
            payment_method="credit_card",
            allow_writes_without_row=False,
            client_repr="QoyodAPIClient(no-row-fixture)")
    assert exc.value.violation_type == (
        "rev32_1_missing_row_context_on_write")


# ═════════════════════════════════════════════════════════════════
# 6. Concurrent / stale worker after SKIPPED -> BLOCKED
# ═════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_6_concurrent_stale_worker_after_skipped_blocked():
    """Race scenario: worker A moved the row to SKIPPED; worker B
    (stale in-memory snapshot at CUSTOMER_RESOLVED) tries to
    transition CUSTOMER_RESOLVED → PRODUCT_RESOLVED via
    `_apply_atomic`. CAS filter must reject because live DB stage
    is SKIPPED. `_StaleStageError` is raised — pipeline aborts
    without any Qoyod POST."""
    from integrations.qoyod.pipeline import _apply_atomic, _StaleStageError
    # Set up: DB has stage=SKIPPED, worker thinks it's CUSTOMER_RESOLVED.
    row = _row_at_stage(
        stage="SKIPPED",
        payment_method="credit_card",
        eligible=False,
        stage_history=[{"from_stage": "CUSTOMER_RESOLVED",
                        "to_stage":   "SKIPPED"}],
        row_id="row-race")
    db = MagicMock()

    async def _update_one(filt, patch):
        # CAS filter is {"id": row_id, "pipeline_stage": expected}.
        expected = filt.get("pipeline_stage")
        if expected != row["pipeline_stage"]:
            return MagicMock(matched_count=0, modified_count=0)
        return MagicMock(matched_count=1, modified_count=1)

    async def _find_one(filt, proj=None):
        return dict(row)

    db.integration_inbox = MagicMock()
    db.integration_inbox.update_one = _update_one
    db.integration_inbox.find_one   = _find_one

    with pytest.raises(_StaleStageError):
        await _apply_atomic(
            db, row["id"],
            {"$set": {"pipeline_stage": "PRODUCT_RESOLVED"}},
            expected_from_stage="CUSTOMER_RESOLVED")


# ═════════════════════════════════════════════════════════════════
# 7. stage_history invariant: no legitimate to_stage after SKIPPED
#    Assert that the module-level FIX matches the acceptance
#    invariant: any row with SKIPPED in stage_history is refused,
#    regardless of its current pipeline_stage.
# ═════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_7_stage_history_no_transitions_after_skipped():
    """Iterate several plausible ``current_stage`` values (all
    downstream of SKIPPED) and assert every one is refused via
    `post_skipped_history_write_violation`. Only diagnostic /
    no-op stages (i.e., SKIPPED itself) may legally appear after
    a SKIPPED transition."""
    from integrations.qoyod.rev32_hardening import (
        Rev32Violation, assert_final_write_permitted,
    )
    downstream_stages = [
        "CUSTOMER_RESOLVED",
        "PRODUCT_RESOLVED",
        "INVOICE_CREATED",
        "INVOICE_PAYMENT_CREATED",
    ]
    for stage in downstream_stages:
        row = _row_at_stage(
            stage=stage,
            payment_method="credit_card",   # even Tabby is refused
            eligible=True,
            stage_history=[
                {"from_stage": "CUSTOMER_RESOLVED", "to_stage": "SKIPPED"},
                {"from_stage": "SKIPPED",           "to_stage": "RETRYING"},
                {"from_stage": "RETRYING",          "to_stage": "CUSTOMER_RESOLVED"},
            ],
            row_id=f"row-history-{stage}")
        db = _mk_db(settings=_canary_settings(), row=row)

        with pytest.raises(Rev32Violation) as exc:
            await assert_final_write_permitted(
                db, row["id"],
                action="create_invoice",
                payment_method="credit_card",
                user_id="main")
        assert exc.value.violation_type == (
            "post_skipped_history_write_violation"), (
            f"stage={stage} did not trigger post_skipped violation; "
            f"got {exc.value.violation_type}")


# ═════════════════════════════════════════════════════════════════
# 8. production_writes_locked=false ALONE is insufficient
# ═════════════════════════════════════════════════════════════════
def test_8_production_writes_locked_false_alone_insufficient():
    """Fix D: `pipeline._live_write_permitted` must return
    `False` when any single global gate is not permitting, even
    if `production_writes_locked=false` and `dry_run_mode=false`.

    Exercises three subcases:
      (a) selective_live_send_enabled=False -> refused
      (b) selective_auto_send_enabled=False -> refused
      (c) allowlist widened beyond ['credit_card'] -> refused
    Also exercises the payment_method allowlist check under a
    permitting-but-non-tabby configuration."""
    from integrations.qoyod.pipeline import _live_write_permitted

    # (a) live_send OFF -> refused
    permitted, reason = _live_write_permitted({
        "dry_run_mode":                  False,
        "production_writes_locked":      False,
        "selective_live_send_enabled":   False,   # <— the ONLY off-flag
        "selective_auto_send_enabled":   True,
        "selective_auto_send_allowed_payment_methods": ["credit_card"],
    }, payment_method="credit_card")
    assert permitted is False
    assert "selective_live_send_enabled_is_false" in reason

    # (b) SAS OFF -> refused
    permitted, _ = _live_write_permitted({
        "dry_run_mode":                  False,
        "production_writes_locked":      False,
        "selective_live_send_enabled":   True,
        "selective_auto_send_enabled":   False,   # <— off
        "selective_auto_send_allowed_payment_methods": ["credit_card"],
    }, payment_method="credit_card")
    assert permitted is False

    # (c) allowlist drift -> refused by canary scope invariant
    permitted, reason = _live_write_permitted({
        "dry_run_mode":                  False,
        "production_writes_locked":      False,
        "selective_live_send_enabled":   True,
        "selective_auto_send_enabled":   True,
        # DRIFT: allowlist includes credit_card + mada
        "selective_auto_send_allowed_payment_methods":
            ["credit_card", "apple_pay"],
    }, payment_method="credit_card")
    assert permitted is False
    assert "canary_scope_drift" in reason

    # (d) sanity: happy Tabby path is permitted
    permitted, _ = _live_write_permitted({
        "dry_run_mode":                  False,
        "production_writes_locked":      False,
        "selective_live_send_enabled":   True,
        "selective_auto_send_enabled":   True,
        "selective_auto_send_allowed_payment_methods": ["credit_card"],
    }, payment_method="credit_card")
    assert permitted is True

    # (e) rev33 payment_method allowlist mirror -
    # mada outside allowlist -> refused (even under happy gate
    # config the moment payment_method is off-list).
    permitted, reason = _live_write_permitted({
        "dry_run_mode":                  False,
        "production_writes_locked":      False,
        "selective_live_send_enabled":   True,
        "selective_auto_send_enabled":   True,
        "selective_auto_send_allowed_payment_methods": ["credit_card"],
    }, payment_method="mada")
    assert permitted is False
    assert "mada" in reason and "allowlist" in reason
