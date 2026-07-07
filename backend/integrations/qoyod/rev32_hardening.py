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


# ── Iter-2026-02.rev32.1 — Dead-letter hardening ─────────────────
# BLOCKED_FOR_WRITE_STAGES = TERMINAL_STAGES ∪ FAILED_* stages.
# A row at any of these stages MUST NOT accept a new Qoyod write
# (create_customer / create_product / create_invoice /
# create_invoice_payment) — even from a legitimate retry path.
#
# Rationale (RCA of order 270589798, invoice #192, payment #163):
# The row transitioned CUSTOMER_RESOLVED → FAILED_PRODUCT →
# DEAD_LETTER; then a SEPARATE code path (retry/reprocess/manual
# send) instantiated `QoyodAPIClient` directly and ran
# PRODUCT_RESOLVED → INVOICE_CREATED → INVOICE_PAYMENT_CREATED →
# COMPLETED, bypassing Rev32 v2 which only guarded the pipeline
# `_get_api_client` entry point. rev32.1 pushes the guard down to
# the api_client write methods so ANY code path is fenced.
#
# Note: FAILED_* stages are NOT truly terminal in state_machine.py
# (they can resume via FAILURE_TO_RESUME). But for WRITE purposes,
# they are hard-blocked — the state_machine's RETRY path re-enters
# the pipeline which builds a fresh row-context. Any code that
# tries to POST while the current stage is FAILED_* is either a
# stale worker or a legacy path that must be revoked.
BLOCKED_FOR_WRITE_STAGES: frozenset[str] = frozenset({
    # Original terminal set.
    "SKIPPED",
    "COMPLETED",
    "DEAD_LETTER",
    "PARTIAL_FAILURE",
    "COMPLETED_WITH_ROUNDING_WARNING",
    "COMPLETED_INVOICE_ONLY",
    # FAILED_* stages — no downstream write until state_machine
    # resumes them explicitly.
    "FAILED_VALIDATION",
    "FAILED_NORMALIZATION",
    "FAILED_ENRICHMENT",
    "FAILED_CUSTOMER",
    "FAILED_PRODUCT",
    "FAILED_INVOICE",
    "FAILED_RECEIPT",
    "PAYMENT_LINK_FAILED",   # canonical name in state_machine.py
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
# rev47 — SKIPPED-history veto exemption (user approval 2026-07)
# ─────────────────────────────────────────────────────────────────
# RCA of prod order 270939808 (credit_card canary, trace
# ad0c8807452b4fe5b4a1c764738e9c6d): the row was TRANSIENTLY skipped
# (payment_method_not_in_allow_list, before credit_card was enabled),
# legitimately resumed via the audited one-shot (rev44), yet the
# rev33(X) blanket veto still killed the write at create_customer and
# tripped the kill switch. rev47 teaches the veto the rev44 rule:
# a historical SKIPPED entry is exempt ONLY when BOTH hold:
#   (a) its note maps to a rev44 TRANSIENT reason AND the order is
#       not cancelled/refunded (classify_skip — the ONE rule);
#   (b) the very next stage_history entry is the audited resume
#       SKIPPED → RETRYING (the one-shot path, which itself refuses
#       non-transient rows).
# EVERYTHING ELSE stays an absolute veto (fail-closed): fatal skips,
# duplicate blocks, unknown/legacy notes, unresumed skips.
_SKIP_NOTE_PREFIXES: tuple = (
    "selective_auto_send_gate re-eval failed: ",
    "selective_auto_send_gate: ",
    "business_rule: ",
    "manual_recovery_hold: ",
)


def skip_reason_from_history_note(note) -> Optional[str]:
    """Map a SKIPPED stage_history note back to its skip reason.
    Unknown/legacy formats return None → the veto stays (fail-closed)."""
    n = str(note or "")
    for prefix in _SKIP_NOTE_PREFIXES:
        if n.startswith(prefix):
            return n[len(prefix):].strip() or None
    if n.startswith("rev33.2 canary_scope_skip:"):
        return "canary_scope_skip_pm_not_in_allowlist"
    return None


def skipped_history_entry_exempt(entry, next_entry, row: dict) -> bool:
    """rev47 — True ONLY for a transient-classified SKIPPED entry that
    was immediately resumed via the audited SKIPPED → RETRYING hop."""
    from integrations.qoyod.skip_classification import (
        TRANSIENT, classify_skip,
    )
    if not isinstance(entry, dict):
        return False
    reason = skip_reason_from_history_note(entry.get("note"))
    if not reason:
        return False
    canonical = row.get("canonical_payload") or {}
    cls = classify_skip(
        reason,
        status_native=canonical.get("order_status_native"),
        status_canon=canonical.get("order_status"))
    if cls != TRANSIENT:
        return False
    if not isinstance(next_entry, dict):
        return False
    nxt_from = next_entry.get("from_stage") or next_entry.get("from")
    nxt_to = next_entry.get("to_stage") or next_entry.get("to")
    return nxt_from == "SKIPPED" and nxt_to == "RETRYING"


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
         "salla_order_number":        1,
         "selective_auto_send_gate":  1,
         "sas_worker_trace":          1,
         # rev33 — full stage_history so we can veto any row that
         # was EVER marked SKIPPED. SKIPPED is absolute-terminal;
         # even if a caller successfully reset the row to another
         # stage (which rev33 also blocks upstream in
         # one_shot_reprocess), the historical SKIPPED transition
         # is proof that the SAS gate rejected the row and MUST
         # forever prevent live writes.
         "stage_history":             1,
         # rev32.1 — dead_lettered_at is a separate signal from
         # pipeline_stage. Even if state_machine rolls stage back
         # (e.g., FAILED_PRODUCT → CUSTOMER_RESOLVED via retry), the
         # `dead_lettered_at` timestamp is only set when the row was
         # DEAD_LETTERed; once set, it MUST NOT be cleared. Any
         # subsequent write attempt is a rev32.1 violation.
         "dead_lettered_at":          1,
         "canonical_payload.payment_method":        1,
         "canonical_payload.payment_method_native": 1,
         # rev47 — statuses feed classify_skip for the history-veto
         # exemption (cancelled/refunded stays an absolute veto).
         "canonical_payload.order_status":          1,
         "canonical_payload.order_status_native":   1,
         "_id":                       0},
    ) or {}

    row_pm = payment_method or (
        (row.get("canonical_payload") or {}).get("payment_method")
        or (row.get("canonical_payload") or {}).get("payment_method_native"))
    trace_id = row.get("trace_id")
    stage = row.get("pipeline_stage")
    dead_lettered_at = row.get("dead_lettered_at")
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
        "dead_lettered_at":            dead_lettered_at,
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

    # ── rev33 (X): SKIPPED in stage_history is a hard veto ──────
    # RCA of leaks 269747616 (credit_card → invoice #193) and
    # 270054904 (tamara_installment → invoice #194): a row that
    # was marked SKIPPED by the SAS gate was later resurrected and
    # driven through to a live write. rev33 makes SKIPPED absolute-
    # terminal: even if the row's current pipeline_stage was reset
    # to something else, the historical SKIPPED transition proves
    # the SAS gate rejected the row and MUST forever prevent any
    # Qoyod write. This is defense-in-depth on top of the
    # `pipeline_stage in BLOCKED_FOR_WRITE_STAGES` check (which
    # only looks at the CURRENT stage).
    stage_history = row.get("stage_history") or []
    if isinstance(stage_history, list):
        for _idx, _entry in enumerate(stage_history):
            _to = None
            if isinstance(_entry, dict):
                _to = _entry.get("to_stage") or _entry.get("to")
            if _to == "SKIPPED":
                # rev47 — transient + audited-resume entries are exempt
                # (see skipped_history_entry_exempt above). Any other
                # SKIPPED entry keeps the absolute rev33 veto.
                _next = (stage_history[_idx + 1]
                         if _idx + 1 < len(stage_history) else None)
                if skipped_history_entry_exempt(_entry, _next, row):
                    continue
                await _persist_violation_flag(
                    db, row_id,
                    flag_key="post_skipped_history_write_violation",
                    evidence=evidence_common,
                )
                await trigger_kill_switch(
                    db, user_id=user_id,
                    reason=(f"Row {row_id!r} has SKIPPED in stage_history; "
                            f"attempted {action!r} — "
                            "post_skipped_history_write_violation (rev33)"),
                    violation_type="post_skipped_history_write_violation",
                    evidence=evidence_common,
                )
                raise Rev32Violation(
                    row_id=row_id, action=action,
                    violation_type="post_skipped_history_write_violation",
                    reason=("row was marked SKIPPED at least once in "
                            "stage_history — rev33 makes SKIPPED "
                            "absolute-terminal; no writes permitted"),
                    evidence=evidence_common)

    # ── rev33 (Y): Canary scope invariant ───────────────────────
    # When `selective_live_send_enabled=True`, the operator is in
    # a Live-Canary window. rev33 requires the allowlist to be
    # EXACTLY ["tabby_installment"] at write-time — not just at
    # enable-time. This closes the drift window where a caller
    # could widen the allowlist AFTER canary enable (via
    # POST /admin/expand-selective-auto-send or direct MongoDB
    # update) and slip a non-Tabby write through.
    if bool(settings.get("selective_live_send_enabled", False)):
        from integrations.qoyod.canary_budget import CANARY_SCOPE_ALLOWLIST
        if list(allow_list) != list(CANARY_SCOPE_ALLOWLIST):
            await _persist_violation_flag(
                db, row_id,
                flag_key="canary_scope_drift_violation",
                evidence=evidence_common,
            )
            await trigger_kill_switch(
                db, user_id=user_id,
                reason=(f"Live Canary is ACTIVE but allowlist="
                        f"{list(allow_list)!r} != {CANARY_SCOPE_ALLOWLIST!r}; "
                        f"attempted {action!r} — canary_scope_drift_violation "
                        "(rev33/rev39)"),
                violation_type="canary_scope_drift_violation",
                evidence=evidence_common,
            )
            raise Rev32Violation(
                row_id=row_id, action=action,
                violation_type="canary_scope_drift_violation",
                reason=(f"Live Canary requires allowlist "
                        f"== {CANARY_SCOPE_ALLOWLIST!r} at write time; "
                        f"found {list(allow_list)!r} — write forbidden "
                        "(rev33/rev39)"),
                evidence=evidence_common)

    # ── rev35 (Z): Canary order budget (max_orders=1) ────────────
    # During a Live-Canary window with an ARMED budget, EVERY
    # guarded write must belong to an order that already holds a
    # reserved slot (reserved atomically in pipeline._get_api_client).
    # A write for an unreserved order means some path bypassed the
    # reservation gate → hard violation + kill switch. This covers
    # one-shot / retry / manual writers that mint their own client.
    #
    # Scope note: when NO budget doc exists (operator never armed),
    # this layer logs and defers to the pre-rev35 guard chain —
    # the AUTO pipeline is still fail-closed because
    # `_get_api_client` refuses to mint a live client at all when
    # the budget is not armed (CanaryBudgetHold).
    if bool(settings.get("selective_live_send_enabled", False)):
        from integrations.qoyod.canary_budget import is_order_reserved
        _budget_doc = None
        _budget_layer_ok = True
        try:
            _budget_doc = await db.qoyod_canary_budget.find_one(
                {"user_id": user_id}, {"_id": 1})
        except Exception as _budget_infra_err:  # noqa: BLE001
            # Infrastructure/read error (or a legacy test stub without
            # this collection). Log LOUDLY and defer to the pre-rev35
            # guard chain — the authoritative budget gate is layer 1
            # in pipeline._get_api_client which already refused to
            # mint a live client unless the reservation succeeded.
            _budget_layer_ok = False
            logger.error(
                "rev35 canary_budget_layer_read_failed user_id=%s "
                "row_id=%s action=%s err=%s — layer skipped",
                user_id, row_id, action, _budget_infra_err)
        if _budget_layer_ok and _budget_doc is None:
            logger.warning(
                "rev35 canary_window_without_armed_budget user_id=%s "
                "row_id=%s action=%s — budget layer skipped "
                "(pipeline layer still fail-closed)",
                user_id, row_id, action)
        elif _budget_layer_ok:
            _order_no = row.get("salla_order_number")
            _reserved = await is_order_reserved(
                db, user_id=user_id, order_number=_order_no)
            if not _reserved:
                _ev = {**evidence_common,
                       "salla_order_number": _order_no}
                await _persist_violation_flag(
                    db, row_id,
                    flag_key="canary_budget_violation",
                    evidence=_ev,
                )
                await trigger_kill_switch(
                    db, user_id=user_id,
                    reason=(f"Live Canary is ACTIVE but order "
                            f"{_order_no!r} holds NO reserved budget "
                            f"slot; attempted {action!r} — "
                            "canary_budget_violation "
                            "(rev35, max_orders=1)"),
                    violation_type="canary_budget_violation",
                    evidence=_ev,
                )
                raise Rev32Violation(
                    row_id=row_id, action=action,
                    violation_type="canary_budget_violation",
                    reason=(f"order {_order_no!r} is not reserved in "
                            "the canary budget (max_orders=1) — write "
                            "forbidden (rev35)"),
                    evidence=_ev)

    # ── rev32.1 (A): dead_lettered_at signal ─────────────────────
    # Independent of pipeline_stage. Once a row was DEAD_LETTERed,
    # any subsequent write is forbidden — even if state_machine
    # rolled the stage back via a retry path.
    if dead_lettered_at:
        await _persist_violation_flag(
            db, row_id,
            flag_key="post_dead_letter_write_violation",
            evidence=evidence_common,
        )
        await trigger_kill_switch(
            db, user_id=user_id,
            reason=(f"Row {row_id!r} was DEAD_LETTERed at "
                    f"{dead_lettered_at!r}; attempted {action!r} — "
                    "post_dead_letter_write_violation"),
            violation_type="post_dead_letter_write_violation",
            evidence=evidence_common,
        )
        raise Rev32Violation(
            row_id=row_id, action=action,
            violation_type="post_dead_letter_write_violation",
            reason=(f"row was DEAD_LETTERed at {dead_lettered_at!r} — "
                    "no further writes permitted (rev32.1)"),
            evidence=evidence_common)

    # ── rev32.1 (B): blocked-for-write stage set ─────────────────
    # Wider than TERMINAL_STAGES — includes all FAILED_* stages so
    # a row currently at FAILED_PRODUCT cannot be re-driven to
    # create_invoice by a stale/legacy worker.
    if stage in BLOCKED_FOR_WRITE_STAGES:
        # Classify: terminal → post_terminal; failed → post_failed.
        vio = ("post_terminal_stage_downstream_violation"
               if stage in TERMINAL_STAGES
               else "post_failed_stage_downstream_violation")
        await _persist_violation_flag(
            db, row_id,
            flag_key=vio,
            evidence=evidence_common,
        )
        await trigger_kill_switch(
            db, user_id=user_id,
            reason=(f"Row {row_id!r} at blocked-for-write stage "
                    f"{stage!r} attempted {action!r} — {vio}"),
            violation_type=vio,
            evidence=evidence_common,
        )
        raise Rev32Violation(
            row_id=row_id, action=action,
            violation_type=vio,
            reason=(f"row at blocked-for-write stage {stage!r} — "
                    "write forbidden (rev32.1)"),
            evidence=evidence_common)

    # ── rev32.1 (C): worker sha checks — fail-CLOSED ─────────────
    # v1 permitted missing sha (fail-open). v2 fixed
    # `is_stale_worker_row` but forgot to fix the inline check here.
    # rev32.1 unifies: missing OR mismatched OR missing current →
    # blocker.
    if not current_sha:
        await _persist_violation_flag(
            db, row_id,
            flag_key="missing_current_pipeline_sha_violation",
            evidence=evidence_common,
        )
        await trigger_kill_switch(
            db, user_id=user_id,
            reason=(f"current_pipeline_sha unavailable during {action!r} "
                    f"— missing_current_pipeline_sha_violation"),
            violation_type="missing_current_pipeline_sha_violation",
            evidence=evidence_common,
        )
        raise Rev32Violation(
            row_id=row_id, action=action,
            violation_type="missing_current_pipeline_sha_violation",
            reason=("cannot compute current_pipeline_sha — infra bug; "
                    "fail-closed to prevent unverified writes"),
            evidence=evidence_common)
    if not row_worker_sha:
        await _persist_violation_flag(
            db, row_id,
            flag_key="stale_worker_live_write_violation",
            evidence=evidence_common,
        )
        await trigger_kill_switch(
            db, user_id=user_id,
            reason=(f"row_worker_pipeline_sha missing during {action!r} "
                    "— stale_worker_live_write_violation (rev32.1 "
                    "fail-closed on missing sha)"),
            violation_type="stale_worker_live_write_violation",
            evidence=evidence_common,
        )
        raise Rev32Violation(
            row_id=row_id, action=action,
            violation_type="stale_worker_live_write_violation",
            reason=("row_worker_pipeline_sha missing — row was built "
                    "by a stale/legacy code path with no sas_worker_"
                    "trace; live write forbidden (rev32.1)"),
            evidence=evidence_common)
    if row_worker_sha != current_sha:
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


# ─────────────────────────────────────────────────────────────────
# rev32.1 — api_client-layer write guard
# ─────────────────────────────────────────────────────────────────
# Called from inside `QoyodAPIClient.create_customer/product/invoice/
# invoice_payment`. Ensures that ANY code path — pipeline, retry,
# one_shot_reprocess, manual send, approve_locked_payment, go_live,
# etc. — is fenced by rev32.1 before hitting Qoyod HTTP.
#
# Two contract shapes:
#   • With full row context (db + row_id): delegate to
#     `assert_final_write_permitted` (single source of truth).
#   • Without row context AND `allow_writes_without_row=True`
#     (probes / migration / cleanup): permit but LOG loudly for
#     later audit.
#   • Without row context AND allow flag False: FAIL-CLOSED with a
#     dedicated `rev32_1_missing_row_context_on_write` violation.
class Rev32MissingRowContextError(Rev32Violation):
    """Raised by `assert_client_write_permitted` when a QoyodAPIClient
    write method is called with neither row context nor an explicit
    escape hatch. Prevents legacy paths from silently POSTing."""


async def assert_client_write_permitted(
    *,
    db=None,
    row_id: Optional[str] = None,
    trace_id: Optional[str] = None,
    user_id: str = "main",
    action: str,
    payment_method: Optional[str] = None,
    allow_writes_without_row: bool = False,
    client_repr: Optional[str] = None,
) -> None:
    """rev32.1 unified pre-flight for `QoyodAPIClient` write methods.

    Fail-CLOSED semantics: any missing context (db, row_id) raises
    unless `allow_writes_without_row=True` — that flag is a
    deliberate escape hatch for admin probes (Fresh-Start Cleanup,
    integration diagnostics) that don't have a row.
    """
    if action not in GUARDED_WRITE_ACTIONS:
        return  # only guard the four write actions listed
    if not db or not row_id:
        if allow_writes_without_row:
            logger.warning(
                "rev32.1 api_client_write_without_row_context "
                "action=%s client=%s user_id=%s — permitted by "
                "explicit escape hatch. AUDIT trail recommended.",
                action, client_repr, user_id)
            return
        # Fail-closed: no db or no row_id AND no escape hatch → refuse.
        evidence = {
            "action":          action,
            "payment_method":  payment_method,
            "client_repr":     client_repr,
            "user_id":         user_id,
            "db_present":      bool(db),
            "row_id_present":  bool(row_id),
            "trace_id":        trace_id,
            "checked_at":      _now_iso(),
        }
        # Best-effort audit even without db (may fail silently).
        if db is not None:
            try:
                await trigger_kill_switch(
                    db, user_id=user_id,
                    reason=(f"rev32.1 api_client write attempted without "
                            f"row_id for action={action!r} client={client_repr!r}"),
                    violation_type="rev32_1_missing_row_context_on_write",
                    evidence=evidence,
                )
            except Exception as _e:  # noqa: BLE001
                logger.error(
                    "rev32.1 missing_row_context_kill_switch_failed "
                    "err=%s", _e)
        logger.error(
            "rev32.1 REV32_MISSING_ROW_CONTEXT action=%s client=%s "
            "user_id=%s — refusing write",
            action, client_repr, user_id)
        raise Rev32MissingRowContextError(
            row_id=row_id, action=action,
            violation_type="rev32_1_missing_row_context_on_write",
            reason=(f"QoyodAPIClient.{action}() called without row "
                    f"context (db+row_id) and without "
                    "allow_writes_without_row=True — rev32.1 fail-closed"),
            evidence=evidence)
    # Delegate to the full 8-condition guard (rev32.1-hardened).
    await assert_final_write_permitted(
        db, row_id,
        action=action, payment_method=payment_method, user_id=user_id)


def payment_method_from_payload(action: str, payload: Any) -> Optional[str]:
    """Best-effort extraction of payment method from a Qoyod payload.
    Used by `QoyodAPIClient` when the caller didn't pre-compute it.
    Only meaningful for invoice payloads; contact/product payloads
    don't carry a payment method — return None."""
    try:
        if not isinstance(payload, dict):
            return None
        # invoice: {"invoice": {..., "payment_method": "..."}}
        inv = payload.get("invoice")
        if isinstance(inv, dict):
            pm = inv.get("payment_method") or inv.get("payment_method_native")
            if pm:
                return pm
        # invoice_payment: nested under {"invoice_payment": {...}}
        inv_pay = payload.get("invoice_payment")
        if isinstance(inv_pay, dict):
            # Payment method is not typically on the payment payload;
            # the caller should pre-compute from the row. Return None
            # here so the guard uses the row-side canonical_payload.
            return None
    except Exception:  # noqa: BLE001
        return None
    return None


# ─────────────────────────────────────────────────────────────────
# rev32.1 — dead-letter evidence stamping (shared helper)
# ─────────────────────────────────────────────────────────────────
def stamp_dead_letter_evidence(
    patch: dict, *,
    fail_stage: str,
    error: Optional[dict] = None,
) -> dict:
    """Attach the dead-letter evidence trio onto a DEAD_LETTER
    transition patch (public, module-level).

      • `dead_lettered_at`        — ISO timestamp (independent of
        pipeline_stage; survives state_machine rollback).
      • `dead_letter_from_stage`  — precursor stage.
      • `dead_letter_reason`      — error.code / error.message /
        "unspecified".

    Called by BOTH `pipeline._dead_letter()` and `webhook._dead_letter()`
    (and every inline DEAD_LETTER transition) so `assert_final_write_
    permitted` (A) can veto ANY subsequent Qoyod POST regardless of
    which entry-point created the DEAD_LETTER state.

    Pure — does NOT touch the DB. The caller applies the patch via
    the same mechanism it uses for any other `transition(...)` patch.
    """
    patch.setdefault("$set", {})["dead_lettered_at"] = _now_iso()
    patch["$set"]["dead_letter_from_stage"] = fail_stage
    patch["$set"]["dead_letter_reason"] = (
        (error or {}).get("code")
        or (error or {}).get("message")
        or "unspecified")
    return patch
