"""Iter-2026-02.rev32 — Fail-closed hardening tests (GitHub Issue #5).

Root Cause: after Rev31 Tabby-only Live Canary was enabled on Prod,
mada orders 269922590 (invoice #189) and 270091836 (invoice #190)
leaked into قيود via a stale worker whose SHA didn't match the
current pipeline. Diagnostics showed:
    worker_code_mismatch=true, control_flow_violation=true
    (SKIPPED → PRODUCT_RESOLVED → INVOICE_CREATED → COMPLETED)

Rev32 adds 5 hardening layers:
    (1) stale-worker POST block
    (2) terminal-stage hard stop
    (3) unified pre-POST guard (8 conditions)
    (4) auto kill-switch
    (5) diagnostic flags

These tests verify all layers per Issue #5 acceptance checklist.
"""
from __future__ import annotations

import asyncio
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

sys.path.insert(0, "/app/backend")


# ── helpers ───────────────────────────────────────────────────────
def _mk_db(*, settings=None, row=None, existing_kill_events=None):
    """Build an AsyncMock DB with qoyod_settings, integration_inbox,
    rev32_kill_switch_events collections. Captures writes for
    assertion."""
    settings = dict(settings or {})
    settings.setdefault("user_id", "main")
    row = dict(row or {})
    captured = {
        "settings_patches":     [],
        "inbox_patches":        [],
        "kill_switch_inserts":  [],
    }

    async def _settings_find_one(f, proj=None):
        return dict(settings)
    async def _settings_update_one(f, u, upsert=False):
        patch = (u or {}).get("$set") or {}
        settings.update(patch)
        captured["settings_patches"].append({"filter": f, "set": patch, "upsert": upsert})
        return MagicMock(matched_count=1, modified_count=1)

    async def _inbox_find_one(f, proj=None):
        if not row:
            return None
        return dict(row)
    async def _inbox_update_one(f, u):
        patch = (u or {}).get("$set") or {}
        # flatten dotted keys into the row (best-effort).
        for k, v in patch.items():
            row[k] = v
        captured["inbox_patches"].append({"filter": f, "set": patch})
        return MagicMock(matched_count=1, modified_count=1)

    async def _kse_insert_one(doc):
        captured["kill_switch_inserts"].append(doc)
        return MagicMock(inserted_id="fake")

    db = MagicMock()
    db.qoyod_settings         = MagicMock()
    db.qoyod_settings.find_one = _settings_find_one
    db.qoyod_settings.update_one = _settings_update_one

    db.integration_inbox         = MagicMock()
    db.integration_inbox.find_one = _inbox_find_one
    db.integration_inbox.update_one = _inbox_update_one

    db.rev32_kill_switch_events = MagicMock()
    db.rev32_kill_switch_events.insert_one = _kse_insert_one

    db._captured  = captured
    db._settings  = settings
    db._row       = row
    return db


def _live_permitted_settings(payment_methods=("tabby_installment",)):
    """Settings that permit live POST for the given payment methods."""
    return {
        "user_id":                                      "main",
        "dry_run_mode":                                 False,
        "production_writes_locked":                     False,
        "selective_live_send_enabled":                  True,
        "selective_auto_send_enabled":                  True,
        "selective_auto_send_allowed_payment_methods":  list(payment_methods),
    }


def _happy_row(*, pipeline_stage="PRODUCT_RESOLVED",
               payment_method="tabby_installment",
               worker_sha=None):
    """Row that would pass all rev32 checks."""
    from integrations.qoyod.sas_worker_trace import _compute_pipeline_sha
    sha = worker_sha or _compute_pipeline_sha()
    return {
        "id":                        "row-happy",
        "user_id":                   "main",
        "trace_id":                  "trace-happy",
        "pipeline_stage":            pipeline_stage,
        "selective_auto_send_gate":  {"eligible": True,
                                       "reason":   "all_checks_passed"},
        "sas_worker_trace":          {"worker_pipeline_sha": sha},
        "canonical_payload":         {"payment_method": payment_method},
    }


# ── Test 1: rev32 marker present in build diagnostics ───────────
def test_1_rev32_marker_registered_and_present():
    from integrations.qoyod.sas_build_diagnostics import (
        REQUIRED_MARKERS, build_diagnostics_report,
    )
    assert "rev32_fail_closed_hardening" in REQUIRED_MARKERS
    r = build_diagnostics_report()
    m = r["marker_check"]["markers"]["rev32_fail_closed_hardening"]
    assert m["present"] is True
    assert m["count"] >= 1
    assert r["acceptance"]["code_matches_expected"] is True


# ── Test 2: module public surface exists ────────────────────────
def test_2_rev32_module_public_surface_exists():
    from integrations.qoyod.rev32_hardening import (
        Rev32Violation, TERMINAL_STAGES, GUARDED_WRITE_ACTIONS,
        assert_final_write_permitted, assert_not_at_terminal_stage,
        is_stale_worker_row, trigger_kill_switch,
    )
    assert issubclass(Rev32Violation, Exception)
    # Six terminal stages per Issue #5 §3.
    assert "SKIPPED"                       in TERMINAL_STAGES
    assert "COMPLETED"                     in TERMINAL_STAGES
    assert "DEAD_LETTER"                   in TERMINAL_STAGES
    assert "PARTIAL_FAILURE"               in TERMINAL_STAGES
    assert "COMPLETED_WITH_ROUNDING_WARNING" in TERMINAL_STAGES
    assert "COMPLETED_INVOICE_ONLY"        in TERMINAL_STAGES
    # Four guarded write actions per Issue #5 §4.
    assert GUARDED_WRITE_ACTIONS == {
        "create_customer", "create_product",
        "create_invoice",  "create_invoice_payment"}
    assert callable(assert_final_write_permitted)
    assert callable(assert_not_at_terminal_stage)
    assert callable(is_stale_worker_row)
    assert callable(trigger_kill_switch)


# ── Test 3: mada + tabby-only allow-list => no POST + kill-switch
@pytest.mark.asyncio
async def test_3_mada_outside_allowlist_blocks_and_triggers_kill_switch():
    from integrations.qoyod.rev32_hardening import (
        Rev32Violation, assert_final_write_permitted,
    )
    settings = _live_permitted_settings(("tabby_installment",))
    row = _happy_row(payment_method="mada")
    db = _mk_db(settings=settings, row=row)
    with pytest.raises(Rev32Violation) as exc_info:
        await assert_final_write_permitted(
            db, "row-happy",
            action="create_invoice",
            payment_method="mada",
            user_id="main")
    assert exc_info.value.violation_type == \
        "live_non_allowlisted_payment_method_violation"
    # Auto kill-switch fired.
    assert db._settings["production_writes_locked"] is True
    assert db._settings["selective_live_send_enabled"] is False
    assert db._settings["kill_switch_triggered"] is True
    # Audit event persisted.
    assert len(db._captured["kill_switch_inserts"]) == 1
    ev = db._captured["kill_switch_inserts"][0]
    assert ev["violation_type"] == \
        "live_non_allowlisted_payment_method_violation"
    # Row flags persisted.
    flags_writes = [p["set"] for p in db._captured["inbox_patches"]
                    if any(k.startswith("rev32_flags.") for k in p["set"])]
    assert flags_writes, "rev32_flags should be persisted on the row"


# ── Test 4: sas_gate.eligible=false => no POST + kill-switch ────
@pytest.mark.asyncio
async def test_4_sas_gate_ineligible_blocks_post():
    from integrations.qoyod.rev32_hardening import (
        Rev32Violation, assert_final_write_permitted,
    )
    settings = _live_permitted_settings()
    row = _happy_row()
    row["selective_auto_send_gate"] = {
        "eligible": False,
        "reason":   "order_created_before_cutover",
    }
    db = _mk_db(settings=settings, row=row)
    with pytest.raises(Rev32Violation) as exc_info:
        await assert_final_write_permitted(
            db, "row-happy",
            action="create_invoice",
            payment_method="tabby_installment",
            user_id="main")
    assert exc_info.value.violation_type == "skipped_then_posted_violation"
    assert db._settings["production_writes_locked"] is True


# ── Test 5: terminal-stage POST attempt blocked + kill-switch ───
@pytest.mark.asyncio
async def test_5_terminal_stage_post_attempt_blocks_and_kills():
    from integrations.qoyod.rev32_hardening import (
        Rev32Violation, assert_final_write_permitted,
    )
    settings = _live_permitted_settings()
    for term in ("SKIPPED", "COMPLETED", "DEAD_LETTER",
                 "PARTIAL_FAILURE", "COMPLETED_WITH_ROUNDING_WARNING",
                 "COMPLETED_INVOICE_ONLY"):
        row = _happy_row(pipeline_stage=term)
        row["id"] = f"row-{term.lower()}"
        db = _mk_db(settings=dict(settings), row=row)
        with pytest.raises(Rev32Violation) as exc_info:
            await assert_final_write_permitted(
                db, row["id"],
                action="create_invoice_payment",
                payment_method="tabby_installment",
                user_id="main")
        assert exc_info.value.violation_type == \
            "post_terminal_stage_downstream_violation", term
        assert db._settings["production_writes_locked"] is True, term


# ── Test 6: worker_code_mismatch=true blocks POST ───────────────
@pytest.mark.asyncio
async def test_6_stale_worker_blocks_post():
    from integrations.qoyod.rev32_hardening import (
        Rev32Violation, assert_final_write_permitted,
    )
    settings = _live_permitted_settings()
    row = _happy_row(worker_sha="deadbeef00000000")
    db = _mk_db(settings=settings, row=row)
    with pytest.raises(Rev32Violation) as exc_info:
        await assert_final_write_permitted(
            db, "row-happy",
            action="create_invoice",
            payment_method="tabby_installment",
            user_id="main")
    assert exc_info.value.violation_type == \
        "stale_worker_live_write_violation"
    assert db._settings["production_writes_locked"] is True


# ── Test 7: happy path — allow list + eligible + sha match => OK
@pytest.mark.asyncio
async def test_7_happy_path_permits_post():
    from integrations.qoyod.rev32_hardening import (
        assert_final_write_permitted,
    )
    settings = _live_permitted_settings()
    row = _happy_row()
    db = _mk_db(settings=settings, row=row)
    # Should NOT raise.
    await assert_final_write_permitted(
        db, "row-happy",
        action="create_invoice",
        payment_method="tabby_installment",
        user_id="main")
    # Settings must NOT have flipped.
    assert db._settings["production_writes_locked"] is False
    assert db._settings["selective_live_send_enabled"] is True
    assert db._settings.get("kill_switch_triggered") in (None, False)
    # No audit events.
    assert db._captured["kill_switch_inserts"] == []


# ── Test 8: dry_run_mode=true blocks POST ───────────────────────
@pytest.mark.asyncio
async def test_8_dry_run_settings_blocks_post():
    from integrations.qoyod.rev32_hardening import (
        Rev32Violation, assert_final_write_permitted,
    )
    settings = _live_permitted_settings()
    settings["dry_run_mode"] = True
    row = _happy_row()
    db = _mk_db(settings=settings, row=row)
    with pytest.raises(Rev32Violation) as exc_info:
        await assert_final_write_permitted(
            db, "row-happy",
            action="create_invoice",
            payment_method="tabby_installment",
            user_id="main")
    assert exc_info.value.violation_type == "live_write_gate_violation"


# ── Test 9: assert_not_at_terminal_stage refuses terminal rows ──
@pytest.mark.asyncio
async def test_9_assert_not_at_terminal_stage_refuses_terminal():
    from integrations.qoyod.rev32_hardening import (
        Rev32Violation, assert_not_at_terminal_stage, TERMINAL_STAGES,
    )
    for term in TERMINAL_STAGES:
        row = _happy_row(pipeline_stage=term)
        db = _mk_db(row=row)
        with pytest.raises(Rev32Violation) as exc_info:
            await assert_not_at_terminal_stage(
                db, row["id"], expected_stage="CUSTOMER_RESOLVED")
        assert exc_info.value.violation_type == \
            "post_terminal_stage_downstream_violation", term


# ── Test 10: expected_stage matches DB => no raise ──────────────
@pytest.mark.asyncio
async def test_10_assert_not_at_terminal_stage_allows_match():
    from integrations.qoyod.rev32_hardening import (
        assert_not_at_terminal_stage,
    )
    row = _happy_row(pipeline_stage="CUSTOMER_RESOLVED")
    db = _mk_db(row=row)
    # Should NOT raise.
    await assert_not_at_terminal_stage(
        db, row["id"], expected_stage="CUSTOMER_RESOLVED")


# ── Test 11: kill_switch is idempotent (safe to re-trigger) ─────
@pytest.mark.asyncio
async def test_11_kill_switch_is_idempotent():
    from integrations.qoyod.rev32_hardening import trigger_kill_switch
    db = _mk_db(settings=_live_permitted_settings())
    out1 = await trigger_kill_switch(
        db, user_id="main", reason="test1",
        violation_type="test", evidence={"a": 1})
    out2 = await trigger_kill_switch(
        db, user_id="main", reason="test2",
        violation_type="test", evidence={"a": 2})
    assert out1["kill_switch_triggered"] is True
    assert out2["kill_switch_triggered"] is True
    # Two audit events written.
    assert len(db._captured["kill_switch_inserts"]) == 2
    # Settings ARE in the flipped state.
    assert db._settings["production_writes_locked"] is True
    assert db._settings["selective_live_send_enabled"] is False


# ── Test 12: diagnostic flags surface in row_diagnostics ────────
@pytest.mark.asyncio
async def test_12_diagnostic_flags_surface_in_row_diagnostics():
    from integrations.qoyod.sas_build_diagnostics import row_diagnostics
    # Craft a row with populated rev32_flags.
    row = _happy_row(pipeline_stage="INVOICE_CREATED")
    row["trace_id"] = "trace-diag"
    row["rev32_flags"] = {
        "live_non_allowlisted_payment_method_violation": True,
        "kill_switch_triggered": True,
        "kill_switch_reason": "mada outside allowlist",
        "last_violation_type": "live_non_allowlisted_payment_method_violation",
    }

    # row_diagnostics reads by trace_id and needs qoyod_settings.
    async def _find_one_by_trace(f, proj=None):
        if f.get("trace_id") == "trace-diag":
            return dict(row)
        return None
    async def _settings_find_one(f, proj=None):
        return {"selective_auto_send_enabled": True, "dry_run_mode": False}

    db = MagicMock()
    db.integration_inbox = MagicMock()
    db.integration_inbox.find_one = _find_one_by_trace
    db.qoyod_settings = MagicMock()
    db.qoyod_settings.find_one = _settings_find_one

    result = await row_diagnostics(db, "trace-diag")
    assert result["ok"] is True
    d = result["diagnosis"]
    assert d["live_non_allowlisted_payment_method_violation"] is True
    assert d["kill_switch_triggered"] is True
    assert d["kill_switch_reason"] == "mada outside allowlist"
    assert d["rev32_last_violation_type"] == \
        "live_non_allowlisted_payment_method_violation"


# ── Test 13: pipeline._get_api_client downgrades stale-worker row
@pytest.mark.asyncio
async def test_13_get_api_client_downgrades_stale_worker_to_dry():
    from integrations.qoyod.pipeline import _get_api_client
    from integrations.qoyod.invoice_builder import DryRunQoyodClient

    settings = _live_permitted_settings()
    row = _happy_row(worker_sha="deadbeef00000000")
    db = _mk_db(settings=settings, row=row)

    client, is_dry = await _get_api_client(
        db, "main", settings,
        scoped_write_allowance=True,
        row_id="row-happy",
    )
    assert isinstance(client, DryRunQoyodClient)
    assert is_dry is True


# ── Test 14: process_customer_resolved_row hard-stops on terminal
@pytest.mark.asyncio
async def test_14_process_customer_resolved_hard_stops_on_terminal():
    """Even if caller passes a stale in-memory row with
    pipeline_stage=CUSTOMER_RESOLVED, if the DB row has moved to a
    terminal stage (SKIPPED / DEAD_LETTER / ...) the pipeline MUST
    abort BEFORE any product/invoice/receipt work."""
    from integrations.qoyod.pipeline import process_customer_resolved_row

    # In-memory row snapshot says CUSTOMER_RESOLVED.
    row = _happy_row(pipeline_stage="CUSTOMER_RESOLVED")
    row["id"] = "row-terminal-race"

    # But DB row says SKIPPED (concurrent worker moved it).
    db_row = dict(row)
    db_row["pipeline_stage"] = "SKIPPED"
    settings = _live_permitted_settings()
    db = _mk_db(settings=settings, row=db_row)

    out = await process_customer_resolved_row(db, dict(row))
    assert out["outcome"] == "REV32_TERMINAL_STAGE_ABORT"
    assert out["violation_type"] == \
        "post_terminal_stage_downstream_violation"


# ── Test 15: guard is a no-op for actions outside the guarded set
@pytest.mark.asyncio
async def test_15_guard_no_op_for_non_write_action():
    from integrations.qoyod.rev32_hardening import (
        assert_final_write_permitted,
    )
    # A guarded=false action must not touch settings or raise.
    db = _mk_db(settings=_live_permitted_settings(),
                row=_happy_row())
    await assert_final_write_permitted(
        db, "row-happy",
        action="get_customer",  # not in GUARDED_WRITE_ACTIONS
        payment_method="mada",
        user_id="main")
    assert db._settings.get("kill_switch_triggered") in (None, False)
