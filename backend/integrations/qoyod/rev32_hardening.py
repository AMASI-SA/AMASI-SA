"""Iter-2026-02.rev32 — Fail-closed hardening (BLOCKER hotfix).

Root Cause (GitHub Issue #5)
────────────────────────────
After Rev31 Tabby-only Live Canary was enabled on Prod, two `mada`
orders (269922590 → invoice #189, 270091836 → invoice #190) leaked
through into قيود even though:
    • selective_auto_send_allowed_payment_methods=["tabby_installment"]
    • selective_auto_send_gate.eligible=false
    • reason=order_created_before_cutover

Diagnostics captured:
    • worker_code_mismatch=true
    • row_worker_pipeline_sha != current_pipeline_sha (stale worker)
    • control_flow_violation: CUSTOMER_RESOLVED → SKIPPED, then
      SKIPPED continued to PRODUCT_RESOLVED → INVOICE_CREATED →
      COMPLETED (SKIPPED must be terminal).

Rev32 adds the following FAIL-CLOSED hardening layers so no future
Live Canary or scoped-live can ever leak outside its allow-list:

    (1) Stale worker POST block
        Any row whose `sas_worker_trace.worker_pipeline_sha` mismatches
        the currently-running process is forbidden from any real POST.
    (2) Terminal-stage hard stop
        A fresh DB read at every downstream stage-entry refuses to
        continue if the row already sits at a terminal stage
        (SKIPPED / COMPLETED / DEAD_LETTER / PARTIAL_FAILURE /
        COMPLETED_WITH_ROUNDING_WARNING / COMPLETED_INVOICE_ONLY).
    (3) Final pre-POST guard
        A single, unified guard called before every
        create_customer / create_product / create_invoice /
        create_invoice_payment. Reads FRESH settings + row from DB
        and enforces all 8 preconditions from Issue #5.
    (4) Auto kill-switch
        Any violation flips
        `production_writes_locked=true` +
        `selective_live_send_enabled=false` on `qoyod_settings` AND
        writes an audit row to `rev32_kill_switch_events` so the
        operator can review after the fact.
    (5) Diagnostics flags
        Persists explicit boolean flags on the offending row so
        `/admin/diagnostics/row` surfaces them at a glance.

Invariants
──────────
    • READ-ONLY reads (no cross-tenant writes).
    • Kill-switch flips are IDEMPOTENT — repeated triggers append
      audit events but never write conflicting settings.
    • Every violation records the row_id, trace_id, reason,
      pipeline_stage, worker sha pair, and settings snapshot so the
      RCA is self-contained.
    • Never raises unless the caller is about to POST. Terminal-
      stage guard is the only non-POST place we abort (before
      product/invoice/receipt payload build).
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional


logger = logging.getLogger("qoyod.rev32.hardening")


# ── Terminal stages ──────────────────────────────────────────────
# Rows already at any of these MUST NOT accept any downstream
# transition. SKIPPED is the most-critical entry — the whole reason
# Rev32 exists is that Prod trace `f19b609e...` transitioned
# CUSTOMER_RESOLVED → SKIPPED and then continued to PRODUCT_RESOLVED
# → INVOICE_CREATED → COMPLETED under a stale worker.
TERMINAL_STAGES: frozenset[str] = frozenset({
    "SKIPPED",
    "COMPLETED",
    "DEAD_LETTER",
    "PARTIAL_FAILURE",
    "COMPLETED_WITH_ROUNDING_WARNING",
    "COMPLETED_INVOICE_ONLY",
})


# ── Write actions guarded by `assert_final_write_permitted` ──────
GUARDED_WRITE_ACTIONS: frozenset[str] = frozenset({
    "create_customer",
    "create_product",
    "create_invoice",
    "create_invoice_payment",
})


class Rev32Violation(Exception):
    """Raised by rev32 guards when a violation is detected.

    Attributes let the caller (pipeline) persist evidence on the row
    and short-circuit BEFORE any Qoyod POST.
    """

    def __init__(
        self,
        *,
        row_id: Optional[str],
        action: str,
        violation_type: str,
        reason: str,
        evidence: dict,
    ):
        self.row_id = row_id
        self.action = action
        self.violation_type = violation_type
        self.reason = reason
        self.evidence = evidence
        super().__init__(
            f"rev32 violation: action={action!r} type={violation_type!r} "
            f"reason={reason!r} row_id={row_id!r}")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ─────────────────────────────────────────────────────────────────
# (4) Auto kill-switch
# ─────────────────────────────────────────────────────────────────
async def trigger_kill_switch(
    db,
    *,
    user_id: str,
    reason: str,
    violation_type: str,
    evidence: dict,
    actor: str = "rev32_auto_kill_switch",
) -> dict:
    """Flip the two fail-closed switches and append an audit row.

    Sets on `qoyod_settings`:
        • `production_writes_locked=True`
        • `selective_live_send_enabled=False`
        • `kill_switch_triggered=True`
        • `kill_switch_reason=<reason>`
        • `kill_switch_triggered_at=<iso>`

    Appends to `rev32_kill_switch_events` collection so operators
    can review history and reconcile invoices/payments.

    IDEMPOTENT: repeated triggers append audit rows but the settings
    flip is `$set` (already-locked settings are re-set to the same
    values without harm).
    """
    now = _now_iso()
    event_id = uuid.uuid4().hex
    audit_row = {
        "id":              event_id,
        "user_id":         user_id,
        "reason":          reason,
        "violation_type":  violation_type,
        "evidence":        evidence,
        "actor":           actor,
        "triggered_at":    now,
    }
    try:
        await db.rev32_kill_switch_events.insert_one(audit_row)
    except Exception as e:  # noqa: BLE001
        # Auditing failure MUST NOT block the actual kill-switch. Log
        # loudly so the operator still sees it in journalctl.
        logger.error(
            "rev32 kill_switch_audit_failed reason=%s err=%s",
            reason, e)

    # Idempotent settings flip.
    try:
        await db.qoyod_settings.update_one(
            {"user_id": user_id},
            {"$set": {
                "production_writes_locked":     True,
                "selective_live_send_enabled":  False,
                "kill_switch_triggered":        True,
                "kill_switch_reason":           reason,
                "kill_switch_triggered_at":     now,
                "kill_switch_violation_type":   violation_type,
                "kill_switch_last_event_id":    event_id,
            }},
            upsert=True,
        )
    except Exception as e:  # noqa: BLE001
        logger.error(
            "rev32 kill_switch_settings_flip_failed reason=%s err=%s",
            reason, e)

    logger.error(
        "rev32 KILL_SWITCH_TRIGGERED user_id=%s reason=%s type=%s "
        "event_id=%s",
        user_id, reason, violation_type, event_id)
    return {
        "kill_switch_triggered":     True,
        "kill_switch_reason":        reason,
        "kill_switch_event_id":      event_id,
        "kill_switch_triggered_at":  now,
    }


# ─────────────────────────────────────────────────────────────────
# (2) Terminal-stage hard stop
# ─────────────────────────────────────────────────────────────────
async def assert_not_at_terminal_stage(
    db, row_id: str, *,
    expected_stage: str,
    user_id: str = "main",
) -> None:
    """FRESH read of `pipeline_stage` from the DB. If it does not
    match `expected_stage` AND it IS in TERMINAL_STAGES, raise
    Rev32Violation AND fire the auto kill-switch + persist rev32
    diagnostic flags. Prevents a stale-worker snapshot from continuing
    past SKIPPED / DEAD_LETTER / etc.

    Non-terminal mismatch is NOT raised here — that's the domain of
    `_apply_atomic` CAS. This guard only cares about the specific
    stale-worker-past-terminal class of failure the issue documents.

    ChatGPT review blocker (Rev32 v2): terminal hard stop MUST fire
    kill-switch + rev32_flags. Silent abort was insufficient.
    """
    if not row_id:
        return
    doc = await db.integration_inbox.find_one(
        {"id": row_id},
        {"pipeline_stage": 1, "trace_id": 1, "_id": 0},
    )
    if not doc:
        return
    current = doc.get("pipeline_stage")
    if current == expected_stage:
        return
    if current in TERMINAL_STAGES:
        evidence = {
            "row_id":            row_id,
            "expected_stage":    expected_stage,
            "actual_stage":      current,
            "trace_id":          doc.get("trace_id"),
            "detected_by":       "assert_not_at_terminal_stage",
            "checked_at":        _now_iso(),
        }
        # Persist row-level violation flags first (best-effort).
        await _persist_violation_flag(
            db, row_id,
            flag_key="post_terminal_stage_downstream_violation",
            evidence=evidence,
        )
        # Auto kill-switch: flip settings + audit trail.
        await trigger_kill_switch(
            db, user_id=user_id,
            reason=(f"Row {row_id!r} already at terminal "
                    f"{current!r}; downstream transition from "
                    f"{expected_stage!r} refused (rev32)."),
            violation_type="post_terminal_stage_downstream_violation",
            evidence=evidence,
        )
        raise Rev32Violation(
            row_id=row_id,
            action="downstream_transition",
            violation_type="post_terminal_stage_downstream_violation",
            reason=(
                f"Row already at terminal stage {current!r}; "
                f"downstream transition from {expected_stage!r} is "
                "forbidden (rev32)."),
            evidence=evidence,
        )


# ─────────────────────────────────────────────────────────────────
# (1) Stale-worker POST block  +  (3) Final pre-POST guard
# ─────────────────────────────────────────────────────────────────
async def assert_final_write_permitted(
    db,
    row_id: str,
    *,
    action: str,
    payment_method: Optional[str],
    user_id: str = "main",
) -> None:
    """Called BEFORE every real Qoyod write.

    Reads settings + row FRESH from DB (no cached copies) and enforces
    the 8 conditions listed in Issue #5. On failure, triggers the auto
    kill-switch, persists diagnostic flags on the row, and raises
    `Rev32Violation` so the caller aborts BEFORE any HTTP call.

    Conditions (verbatim from Issue #5 §4):
        1. dry_run_mode = False
        2. production_writes_locked = False
        3. selective_live_send_enabled = True
        4. selective_auto_send_enabled = True
        5. payment_method ∈ selective_auto_send_allowed_payment_methods
        6. sas_gate.eligible = True
        7. current pipeline_stage NOT in TERMINAL_STAGES
        8. row_worker_pipeline_sha == current_pipeline_sha
    """
    if action not in GUARDED_WRITE_ACTIONS:
        return  # only guard the four write actions listed

    # Fresh settings.
    settings = await db.qoyod_settings.find_one(
        {"user_id": user_id}, {"_id": 0}) or {}
    # Fresh row.
    row = await db.integration_inbox.find_one(
        {"id": row_id},
        {"pipeline_stage":            1,
         "trace_id":                  1,
         "selective_auto_send_gate":  1,
         "sas_worker_trace":          1,
         "canonical_payload.payment_method":        1,
         "canonical_payload.payment_method_native": 1,
         "_id":                       0},
    ) or {}

    row_pm = payment_method or (
        (row.get("canonical_payload") or {}).get("payment_method")
        or (row.get("canonical_payload") or {}).get("payment_method_native"))
    trace_id = row.get("trace_id")
    stage = row.get("pipeline_stage")
    swt = row.get("sas_worker_trace") or {}
    row_worker_sha = (
        swt.get("worker_pipeline_sha") if isinstance(swt, dict) else None)

    # Compute current pipeline sha lazily (cached in the module).
    from integrations.qoyod.sas_worker_trace import _compute_pipeline_sha
    current_sha = _compute_pipeline_sha()

    sas_gate = row.get("selective_auto_send_gate") or {}
    if not isinstance(sas_gate, dict):
        sas_gate = {}
    allow_list = list(settings.get(
        "selective_auto_send_allowed_payment_methods") or [])

    evidence_common = {
        "row_id":                      row_id,
        "trace_id":                    trace_id,
        "action":                      action,
        "pipeline_stage":              stage,
        "payment_method":              row_pm,
        "allow_list":                  allow_list,
        "settings_snapshot": {
            "dry_run_mode":                 bool(settings.get("dry_run_mode", False)),
            "production_writes_locked":     bool(settings.get("production_writes_locked", False)),
            "selective_live_send_enabled":  bool(settings.get("selective_live_send_enabled", False)),
            "selective_auto_send_enabled":  bool(settings.get("selective_auto_send_enabled", False)),
        },
        "sas_gate": {
            "eligible": sas_gate.get("eligible"),
            "reason":   sas_gate.get("reason"),
        },
        "row_worker_pipeline_sha": row_worker_sha,
        "current_pipeline_sha":    current_sha,
        "checked_at":              _now_iso(),
    }

    # (7) terminal-stage check first — cheap, no downstream flag flip.
    if stage in TERMINAL_STAGES:
        await _persist_violation_flag(
            db, row_id,
            flag_key="post_terminal_stage_downstream_violation",
            evidence=evidence_common,
        )
        await trigger_kill_switch(
            db, user_id=user_id,
            reason=(f"Row {row_id!r} at terminal {stage!r} attempted "
                    f"{action!r} — post_terminal_stage_downstream_violation"),
            violation_type="post_terminal_stage_downstream_violation",
            evidence=evidence_common,
        )
        raise Rev32Violation(
            row_id=row_id, action=action,
            violation_type="post_terminal_stage_downstream_violation",
            reason=(f"row at terminal stage {stage!r} — write forbidden"),
            evidence=evidence_common)

    # (8) Stale worker check — critical: any mismatch means the row
    # was built by pre-rev32 code and MUST NOT trigger a live POST.
    if row_worker_sha and current_sha and row_worker_sha != current_sha:
        await _persist_violation_flag(
            db, row_id,
            flag_key="stale_worker_live_write_violation",
            evidence=evidence_common,
        )
        await trigger_kill_switch(
            db, user_id=user_id,
            reason=(f"Stale worker attempted {action!r}: row_sha="
                    f"{row_worker_sha!r} != current_sha={current_sha!r}"),
            violation_type="stale_worker_live_write_violation",
            evidence=evidence_common,
        )
        raise Rev32Violation(
            row_id=row_id, action=action,
            violation_type="stale_worker_live_write_violation",
            reason=("worker_code_mismatch=true; row built by a stale "
                    "worker — real write forbidden"),
            evidence=evidence_common)

    # (1) & (2) & (3) & (4) Live-write settings gates.
    live_permitted, live_reason = _live_settings_permitted(settings)
    if not live_permitted:
        await _persist_violation_flag(
            db, row_id,
            flag_key="live_write_gate_violation",
            evidence=evidence_common,
        )
        await trigger_kill_switch(
            db, user_id=user_id,
            reason=(f"Attempted {action!r} while {live_reason} — "
                    "live_write_gate_violation"),
            violation_type="live_write_gate_violation",
            evidence=evidence_common,
        )
        raise Rev32Violation(
            row_id=row_id, action=action,
            violation_type="live_write_gate_violation",
            reason=(f"live-write gate refused: {live_reason}"),
            evidence=evidence_common)

    # (6) SAS gate must be eligible.
    if not bool(sas_gate.get("eligible")):
        await _persist_violation_flag(
            db, row_id,
            flag_key="skipped_then_posted_violation",
            evidence=evidence_common,
        )
        await trigger_kill_switch(
            db, user_id=user_id,
            reason=(f"Attempted {action!r} while sas_gate.eligible="
                    f"{sas_gate.get('eligible')!r} reason="
                    f"{sas_gate.get('reason')!r} — "
                    "skipped_then_posted_violation"),
            violation_type="skipped_then_posted_violation",
            evidence=evidence_common,
        )
        raise Rev32Violation(
            row_id=row_id, action=action,
            violation_type="skipped_then_posted_violation",
            reason=(f"sas_gate.eligible={sas_gate.get('eligible')!r}, "
                    f"reason={sas_gate.get('reason')!r} — POST forbidden"),
            evidence=evidence_common)

    # (5) payment method must be on the allow-list.
    if not row_pm or row_pm not in allow_list:
        await _persist_violation_flag(
            db, row_id,
            flag_key="live_non_allowlisted_payment_method_violation",
            evidence=evidence_common,
        )
        await trigger_kill_switch(
            db, user_id=user_id,
            reason=(f"Attempted {action!r} with payment_method="
                    f"{row_pm!r} outside allow-list={allow_list!r} — "
                    "live_non_allowlisted_payment_method_violation"),
            violation_type="live_non_allowlisted_payment_method_violation",
            evidence=evidence_common,
        )
        raise Rev32Violation(
            row_id=row_id, action=action,
            violation_type="live_non_allowlisted_payment_method_violation",
            reason=(f"payment_method={row_pm!r} not in allow-list "
                    f"{allow_list!r}"),
            evidence=evidence_common)

    # All 8 conditions satisfied — POST permitted.
    return


def _live_settings_permitted(settings: dict) -> tuple[bool, str]:
    """Return (permitted, reason). Mirrors pipeline `_live_write_permitted`
    but re-implemented locally so rev32 has no import cycle."""
    if bool(settings.get("dry_run_mode", False)):
        return False, "dry_run_mode=true"
    if not bool(settings.get("selective_live_send_enabled", False)):
        return False, "selective_live_send_enabled=false"
    if bool(settings.get("production_writes_locked", False)):
        return False, "production_writes_locked=true"
    if not bool(settings.get("selective_auto_send_enabled", False)):
        return False, "selective_auto_send_enabled=false"
    return True, "all_gates_permit_live_write"


async def _persist_violation_flag(
    db, row_id: str, *, flag_key: str, evidence: dict,
) -> None:
    """Persist a violation flag + evidence snapshot on the row so
    `/admin/diagnostics/row` surfaces it at a glance. Idempotent —
    repeated calls overwrite the same field."""
    if not row_id:
        return
    try:
        await db.integration_inbox.update_one(
            {"id": row_id},
            {"$set": {
                f"rev32_flags.{flag_key}":            True,
                f"rev32_flags.{flag_key}_evidence":   evidence,
                f"rev32_flags.{flag_key}_at":         _now_iso(),
                "rev32_flags.kill_switch_triggered":  True,
                "rev32_flags.kill_switch_reason":     evidence.get("reason") or flag_key,
                "rev32_flags.last_violation_type":    flag_key,
            }},
        )
    except Exception as e:  # noqa: BLE001
        logger.error(
            "rev32 persist_violation_flag_failed row_id=%s flag=%s err=%s",
            row_id, flag_key, e)


async def is_stale_worker_row(
    db, row_id: str, *,
    live_context: bool = True,
) -> tuple[bool, Optional[str], Optional[str]]:
    """Cheap check used by pipeline `_get_api_client` to force
    DryRun when the row was built by a stale worker.

    Returns (is_stale, row_sha, current_sha).

    ChatGPT review blocker (Rev32 v2): missing worker sha in LIVE
    context MUST be treated as stale (fail-CLOSED). Previously we
    returned False on missing sha which allowed unmarked rows to
    proceed to live POST — that's exactly how the Prod incident
    happened (row had no sas_worker_trace but reached live).

    Semantics:
      • Both shas present + mismatch  → stale=True.
      • row_sha missing in live_context=True → stale=True (fail-closed).
      • row_sha == current_sha         → stale=False.
      • current_sha missing            → stale=False (can't compare;
        this is an infra bug, not a row problem).
      • live_context=False (dry-run)   → stale=False (dry-run is
        already safe; no point flagging).
    """
    if not row_id:
        return False, None, None
    try:
        doc = await db.integration_inbox.find_one(
            {"id": row_id},
            {"sas_worker_trace.worker_pipeline_sha": 1, "_id": 0},
        )
    except Exception:  # noqa: BLE001
        return False, None, None
    if not doc:
        return False, None, None
    swt = doc.get("sas_worker_trace") or {}
    if not isinstance(swt, dict):
        swt = {}
    row_sha = swt.get("worker_pipeline_sha")
    from integrations.qoyod.sas_worker_trace import _compute_pipeline_sha
    current_sha = _compute_pipeline_sha()
    if not current_sha:
        # Cannot compare — infra bug, permit to avoid false positives.
        return False, row_sha, current_sha
    if not row_sha:
        # Missing sha in live = BLOCKER (fail-closed). In dry-run
        # context we permit because no real POST is possible anyway.
        return (bool(live_context), row_sha, current_sha)
    return (row_sha != current_sha), row_sha, current_sha


async def flag_stale_worker_downgrade(
    db, row_id: str, *,
    row_sha: Optional[str],
    current_sha: Optional[str],
    user_id: str = "main",
    trace_id: Optional[str] = None,
    action_context: str = "get_api_client",
) -> None:
    """Called by `pipeline._get_api_client` when a stale-worker row
    is downgraded to DryRun on the LIVE path. Fires the auto
    kill-switch and persists rev32 diagnostic flags on the row.

    ChatGPT review blocker (Rev32 v2): silent downgrade was
    insufficient — the operator must see the incident AND the fail-
    closed switches must flip so no other stale worker can slip
    through until the deploy has been verified.
    """
    if not row_id:
        return
    evidence = {
        "row_id":                  row_id,
        "trace_id":                trace_id,
        "action_context":          action_context,
        "row_worker_pipeline_sha": row_sha,
        "current_pipeline_sha":    current_sha,
        "detected_by":             "flag_stale_worker_downgrade",
        "sha_missing":             not bool(row_sha),
        "checked_at":              _now_iso(),
    }
    await _persist_violation_flag(
        db, row_id,
        flag_key="stale_worker_live_write_violation",
        evidence=evidence,
    )
    await trigger_kill_switch(
        db, user_id=user_id,
        reason=(f"Live-path row downgraded to DryRun (rev32): "
                f"row_sha={row_sha!r} current_sha={current_sha!r} "
                f"action_context={action_context!r}"),
        violation_type="stale_worker_live_write_violation",
        evidence=evidence,
    )
