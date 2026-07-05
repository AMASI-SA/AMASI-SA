"""Qoyod Pipeline Orchestrator — Day 4 segment.

Drives `integration_inbox` rows from `NORMALIZED` through:
    `RULES_APPLIED`  (or `SKIPPED` if not eligible)
    → `CUSTOMER_RESOLVED`  (or `FAILED_CUSTOMER` → `DEAD_LETTER`)

Day 4 STOPS at `CUSTOMER_RESOLVED`. Subsequent steps (products,
invoice, receipt) are intentionally NOT triggered — they land in
Day 5 after the merchant reviews the customer-resolution output.

Failure routing (per user directive — same pattern as Day 3):
    • Validation/structural failure in rules → not possible here,
      rules are pure and total.
    • Customer resolution failure → FAILED_CUSTOMER → DEAD_LETTER
      (the row is NOT deleted, NOT auto-retried).
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from integrations.qoyod.business_rules import (
    evaluate as evaluate_rules, RulesDecision,
)
from integrations.qoyod.customer_resolver import (
    resolve_customer, ResolutionResult,
)
from integrations.qoyod.product_resolver import (
    resolve_products, ProductsResolutionResult,
)
from integrations.qoyod.preflight import run as preflight_run, PreflightResult
from integrations.qoyod.invoice_builder import (
    build_invoice_payload, build_receipt_payload,
    build_invoice_payment_payload,
    DryRunQoyodClient, is_dry_run_mode,
)
from integrations.qoyod.api_client import QoyodAPIClient, QoyodAPIError
from integrations.qoyod.write_lock import (
    QoyodWriteLockedError, set_write_lock_context, reset_write_lock_context,
    is_locked, record_blocked_attempt,
)
from integrations.qoyod.credentials import get_api_key
from integrations.qoyod.dto import SalesOrderDTO
from integrations.qoyod.state_machine import transition, InvalidTransition
from integrations.qoyod.totals_guard import (
    validate_totals, TotalsGuardResult,
)
# Iter-001k — Selective Live Send guard (pipeline instrumentation).
from integrations.qoyod.selective_send_guard import (
    SelectiveSendPolicyBlocked,
    apply_send_date_to_qoyod_payload,
    assert_send_allowed,
)
# ── Iter-2026-02.rev32 — Fail-closed hardening (BLOCKER hotfix) ──
# GitHub Issue #5: stale worker leaked `mada` orders past a
# Tabby-only allow-list on Live Canary. rev32 enforces:
#   (1) stale-worker POST block
#   (2) terminal-stage hard stop
#   (3) unified pre-POST guard for the four write actions
#   (4) auto kill-switch on any violation
#   (5) row-level diagnostic flags
# Marker (scanned by sas_build_diagnostics.REQUIRED_MARKERS):
# "rev32 — Fail-closed hardening"
from integrations.qoyod.rev32_hardening import (
    Rev32Violation,
    TERMINAL_STAGES as _REV32_TERMINAL_STAGES,
    assert_final_write_permitted as _rev32_assert_final_write_permitted,
    assert_not_at_terminal_stage as _rev32_assert_not_at_terminal_stage,
    flag_stale_worker_downgrade as _rev32_flag_stale_worker_downgrade,
    is_stale_worker_row as _rev32_is_stale_worker_row,
    trigger_kill_switch as _rev32_trigger_kill_switch,
)
# ── Iter-2026-02.rev32.1 — Dead-letter hardening ─────────────────
# RCA of order 270589798 / invoice #192 / payment #163 showed that
# Rev32 v2 protected only the pipeline `_get_api_client` entry, but
# other code paths (retry_payment_only, one_shot_reprocess, manual
# send, approve_locked_payment, go_live) instantiate QoyodAPIClient
# directly and were still able to POST to قيود after the row hit
# FAILED_PRODUCT → DEAD_LETTER.  rev32.1 pushes the guard down to
# `QoyodAPIClient.create_{customer,product,invoice,invoice_payment}`
# and expands the blocked-stage set (see BLOCKED_FOR_WRITE_STAGES).
# Marker text scanned by sas_build_diagnostics.REQUIRED_MARKERS:
# "rev32.1 — Dead-letter hardening"
from integrations.qoyod.rev32_hardening import (
    BLOCKED_FOR_WRITE_STAGES as _REV32_1_BLOCKED_FOR_WRITE_STAGES,  # noqa: F401
    stamp_dead_letter_evidence as _stamp_dead_letter_evidence,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────
# Settings loader (single-tenant for MVP; matches routes._load_settings
# fall-backs so the orchestrator never sees a "half-old" doc).
# ─────────────────────────────────────────────────────────────────────
async def _load_settings(db, user_id: str) -> dict:
    doc = await db.qoyod_settings.find_one(
        {"user_id": user_id}, {"_id": 0})
    if not doc:
        return {
            "invoice_trigger_statuses": ["completed"],
            # Iter-293.4-rev9 — Default source is `send_date`.
            "invoice_date_source":      "send_date",
            "trigger_once_only":        True,
            "dry_run_mode":             False,
        }
    if not doc.get("invoice_trigger_statuses"):
        legacy = doc.get("invoice_trigger_status")
        doc["invoice_trigger_statuses"] = [legacy] if legacy else ["completed"]
    if "trigger_once_only" not in doc:
        doc["trigger_once_only"] = True
    if "dry_run_mode" not in doc:
        doc["dry_run_mode"] = False
    # Iter-293.4-rev9 — Auto-migrate legacy tenants whose
    # invoice_date_source still points at Salla-side timestamps. Both
    # `completed_at` and the historical `trigger_status_date` default
    # are silently upgraded to `send_date` so قيود's issue_date always
    # reflects the current Asia/Riyadh moment. Operators who WANT the
    # old behaviour can set it explicitly to a non-empty non-send value.
    if doc.get("invoice_date_source") in ("completed_at",
                                           "trigger_status_date",
                                           None, ""):
        doc["invoice_date_source"] = "send_date"
    # Iter-293.5 — Selective Live Send Gate feature flag. False by
    # default; MUST be explicitly enabled per tenant. When False the
    # gate stays in defensive read-only mode: `pending-orders`
    # endpoint surfaces categorised rows but no auto-send fires.
    if "selective_live_send_enabled" not in doc:
        doc["selective_live_send_enabled"] = False
    return doc


async def _apply(db, row_id: str, patch: dict) -> None:
    await db.integration_inbox.update_one({"id": row_id}, patch)


class _StaleStageError(Exception):
    """Iter-2026-02.rev26 — Raised (internally) when an atomic
    compare-and-set on `pipeline_stage` finds the row is NO LONGER
    at the stage the pipeline expects. This is how we prevent a
    stale in-memory row snapshot from advancing past SKIPPED /
    LOCKED_AWAITING_APPROVAL / any terminal.
    """
    def __init__(self, row_id: str, expected_from: str, actual: Optional[str]):
        self.row_id        = row_id
        self.expected_from = expected_from
        self.actual        = actual
        super().__init__(
            f"stale stage: row {row_id!r} expected={expected_from!r} "
            f"actual_in_db={actual!r}")


async def _apply_atomic(
    db, row_id: str, patch: dict,
    *,
    expected_from_stage: str,
) -> None:
    """Iter-2026-02.rev26 — Compare-And-Set on `pipeline_stage`.

    ONLY updates the row if `pipeline_stage == expected_from_stage`
    in the DB **at the moment of write**. If another handler already
    transitioned the row (e.g. SAS gate wrote SKIPPED, or a concurrent
    worker moved it), matched_count is 0 → we raise `_StaleStageError`
    so the pipeline aborts LOUDLY without any further side-effect.

    This is the atomic guarantee the user demanded after order
    270212453 advanced past SAS-rejected SKIPPED into INVOICE_CREATED.
    """
    res = await db.integration_inbox.update_one(
        {"id": row_id, "pipeline_stage": expected_from_stage},
        patch,
    )
    matched = getattr(res, "matched_count", None)
    if matched is None:
        matched = 1  # fake db in tests may not expose matched_count
    if matched == 0:
        # Re-read the actual stage for diagnostics.
        cur = await db.integration_inbox.find_one(
            {"id": row_id}, {"pipeline_stage": 1, "_id": 0})
        actual = (cur or {}).get("pipeline_stage")
        raise _StaleStageError(row_id, expected_from_stage, actual)


async def _assert_sas_not_rejected(db, row_id: str) -> None:
    """Iter-2026-02.rev26 — Hard guard called BEFORE every Qoyod
    side-effect (customer create, product create, invoice build,
    payment build). Reads the row's persisted
    `selective_auto_send_gate` field. If `eligible=false`, raises
    `_StaleStageError` so we never emit a Qoyod POST (real or dry)
    for a row the SAS gate already rejected.

    Defense-in-depth: even if the compare-and-set on pipeline_stage
    misses a corner-case, this guard prevents any Qoyod-side effect
    on rejected rows.
    """
    doc = await db.integration_inbox.find_one(
        {"id": row_id},
        {"selective_auto_send_gate": 1, "pipeline_stage": 1, "_id": 0},
    )
    if not doc:
        return  # nothing to guard against
    sas = doc.get("selective_auto_send_gate") or {}
    # rev29c — Skip the synthetic `sas_disabled_by_settings` record.
    # That gate was written by the pipeline itself as a placeholder
    # when SAS was OFF at worker time (fail-closed observability),
    # NOT because the SAS gate rejected the row. Treating it as a
    # rejection would break every non-auto-send path (manual send,
    # legacy Day-4 flow, etc.).
    if (isinstance(sas, dict)
            and sas.get("eligible") is False
            and sas.get("reason") != "sas_disabled_by_settings"):
        raise _StaleStageError(
            row_id,
            expected_from="sas_gate_eligible=true",
            actual=(f"sas_gate_eligible=false "
                    f"(reason={sas.get('reason')!r}, "
                    f"current_stage={doc.get('pipeline_stage')!r})"))


# ── rev29d — Hard gate-persistence preflight ──────────────────────
class _SasGateMissingError(Exception):
    """rev29d — Raised when a downstream stage entry sees no
    `selective_auto_send_gate` field on the DB row.

    A row landing at CUSTOMER_RESOLVED / PRODUCT_RESOLVED /
    INVOICE_CREATED / INVOICE_PAYMENT_CREATED without this field is
    IMPOSSIBLE under rev29c pipeline code — the presence of this
    error means an OLD worker built the row OR another code path
    bypassed the rev29c gate write. Either way: fail closed."""

    def __init__(self, row_id: str, stage: str, worker_sha: Optional[str]):
        super().__init__(
            f"rev29d gate-preflight failed at {stage} for row_id={row_id!r}; "
            f"selective_auto_send_gate missing. "
            f"row.sas_worker_trace.worker_pipeline_sha={worker_sha!r}")
        self.row_id = row_id
        self.stage = stage
        self.worker_sha = worker_sha


async def _require_sas_gate_persisted(
    db, row_id: str, *, stage: str,
) -> None:
    """rev29d — Hard preflight. Called at the ENTRY of every stage
    that could produce a Qoyod-shaped stage_history note. Refuses to
    let the pipeline continue if the row is missing
    `selective_auto_send_gate`. Fails BEFORE any wording is emitted
    so no misleading audit line is written.

    Also refuses when the row's `sas_worker_trace.worker_pipeline_sha`
    does not match the current process's pipeline sha (proves the row
    was built by a stale worker).
    """
    doc = await db.integration_inbox.find_one(
        {"id": row_id},
        {"selective_auto_send_gate":       1,
         "selective_auto_send_gate_at":    1,
         "sas_worker_trace":               1,
         "pipeline_stage":                 1,
         "_id":                            0},
    )
    if not doc:
        # Nothing to guard — caller will handle "row vanished".
        return
    sas = doc.get("selective_auto_send_gate")
    at  = doc.get("selective_auto_send_gate_at")
    if not isinstance(sas, dict) or not at:
        # Capture the worker sha so an operator can prove
        # "old-code-vs-new-code" from the DEAD_LETTER evidence.
        from integrations.qoyod.sas_worker_trace import (
            _compute_pipeline_sha,
        )
        row_swt = doc.get("sas_worker_trace") or {}
        row_worker_sha = (row_swt.get("worker_pipeline_sha")
                          if isinstance(row_swt, dict) else None)
        current_sha = _compute_pipeline_sha()
        # Log both for the operator.
        logger.error(
            "rev29d gate_missing_at_downstream row_id=%s stage=%s "
            "row_worker_sha=%s current_sha=%s",
            row_id, stage, row_worker_sha, current_sha)
        raise _SasGateMissingError(
            row_id=row_id, stage=stage, worker_sha=row_worker_sha)


# ─── rev30 — Payment stage expectation helper ────────────────────────
def _is_pm_expecting_payment(payment_method: Optional[str]) -> bool:
    """rev30 — Payment continuation. Return True if this payment
    method is one that should normally produce a Qoyod
    invoice_payment step (pre-paid at the store front). Used for
    diagnostic surfacing only; the actual control flow still goes
    through `resolve_posting_mode`. COD-family methods
    (cash_on_delivery, cod, bank_transfer) are intentionally
    EXCLUDED — they book as credit_invoice_only and never carry an
    invoice_payment.
    """
    if not payment_method:
        return False
    from integrations.qoyod.payment_methods import is_cod_family
    return not is_cod_family(payment_method)



def _writes_blocked(api_client: Any, settings: dict) -> bool:
    """Iter-293.4-rev5 — Single source of truth for the pipeline's
    PRE-flight lock check.

    Why this exists
    ───────────────
    The Global Write Lock has TWO independent enforcers:

      • `api_client._request` — defense-in-depth at the HTTP layer.
        Honoured by EVERY caller via `QoyodWriteLockedError`.

      • `pipeline.process_customer_resolved_row` — short-circuits
        BEFORE the network call so the row gets parked in
        `LOCKED_AWAITING_APPROVAL` cleanly (no exception traceback,
        no half-built audit). Previously this used `is_locked(settings)`
        which read the DB flag DIRECTLY and ignored the lock state of
        the api_client passed in.

    The bug
    ───────
    `one_shot_reprocess.reprocess_one_order` builds the api_client
    with `write_lock_enabled=False` when a valid per-order approval
    phrase is supplied (Iter-293.4-rev3). But the pipeline's pre-flight
    `is_locked(settings)` short-circuit returned True because the DB
    flag is intentionally still True. Result: the approved single
    order got parked at LOCKED_AWAITING_APPROVAL and never reached
    `create_invoice`.

    The fix
    ───────
    Trust the api_client's own lock state when one is supplied — that
    is the SOLE construct that knows whether the caller has been
    granted a per-order bypass. Fall back to `is_locked(settings)`
    only when no api_client is supplied (defensive — should not happen
    on the live paths, but keeps tests / direct invocations safe).
    """
    if api_client is not None:
        # An api_client was supplied — honour its lock state.
        # NOTE: Both real `QoyodAPIClient` and `DryRunQoyodClient`
        # carry a `write_lock_enabled` attribute (DryRun = always
        # False so the pre-check never trips in dry-run mode).
        return bool(getattr(api_client, "write_lock_enabled", False))
    return is_locked(settings)



def _build_policy_order_from_pipeline_scope(
    *,
    row: dict,
    canonical: dict,
    qoyod_customer_id: Any,
    products_resolution: Any = None,
    invoice_diagnostics: Optional[dict] = None,
    is_dry: bool = False,
) -> dict:
    """Iter-001k — Assemble the `order` dict expected by the Selective
    Send policy from what the pipeline already has in scope.

    Called at the invoice + payment write sites BEFORE any Qoyod API
    call. Purely-derived (no DB reads), synchronous, safe.
    """
    # Salla creation date — prefer canonical.order_date (normalised
    # YYYY-MM-DD), then raw_payload.data.date.date (Salla webhook).
    salla_created = canonical.get("order_date")
    if not salla_created:
        raw = (row.get("raw_payload") or {}).get("data") or {}
        dfield = raw.get("date") if isinstance(raw, dict) else None
        if isinstance(dfield, dict):
            salla_created = dfield.get("date")
        elif dfield:
            salla_created = dfield
        if not salla_created:
            salla_created = (raw.get("created_at")
                             if isinstance(raw, dict) else None)

    # Product status: pipeline reaches invoice site only past
    # PRODUCT_RESOLVED. Detect DRY-only mappings when in dry_run mode.
    # Iter-001k policy: TRUST upstream — if the pipeline transitioned
    # past PRODUCT_RESOLVED with no missing SKUs, treat as resolved
    # (products may have come from `default_product_id` settings
    # rather than the resolver's `resolved` list).
    resolved_count = 0
    dry_run_only = 0
    missing_skus: list[str] = []
    if products_resolution is not None:
        # `ProductsResolutionResult` shape (defensive access).
        resolved = getattr(products_resolution, "resolved", None) or []
        resolved_count = len(resolved)
        missing_skus = list(
            getattr(products_resolution, "missing", None) or [])
        if is_dry:
            dry_run_only = resolved_count

    totals_diff = float((invoice_diagnostics or {}).get("diff") or 0.0)
    totals_expected = float(
        (invoice_diagnostics or {}).get("expected_qoyod_total") or 0.0)
    totals_actual = float(
        (invoice_diagnostics or {}).get("salla_total")
        or canonical.get("total_amount") or 0.0)

    return {
        "order_number": (row.get("salla_order_number")
                         or canonical.get("order_number")),
        "salla_order_id": (row.get("salla_order_id")
                           or canonical.get("order_id")),
        "salla_order_created_at": salla_created,
        "status": canonical.get("order_status"),
        "payment_method": canonical.get("payment_method"),
        "existing_qoyod_invoice_id": row.get("qoyod_invoice_id"),
        "customer_status": {
            "resolved": qoyod_customer_id is not None,
            "qoyod_id": qoyod_customer_id,  # policy detects DRY:/PREVIEW:
            "reason": None,
        },
        "products_status": {
            # Trust upstream: if we reached the invoice site AND
            # there's no explicit `missing` list AND no DRY-only
            # flag, mark as resolved.
            "resolved": (not missing_skus and dry_run_only == 0),
            "resolved_count": resolved_count,
            "dry_run_only":   dry_run_only,
            "missing":        missing_skus,
        },
        "totals_status": {
            "valid":    abs(totals_diff) <= 0.01,
            "total":    totals_actual,
            "expected": totals_expected,
            "diff":     totals_diff,
        },
    }



async def _dead_letter(
    db, *, row_id: str, from_stage: str, fail_stage: str,
    error: dict, started_at: Optional[datetime] = None,
) -> str:
    """Two-hop transition: <from_stage> → <fail_stage> → DEAD_LETTER.

    Matches `webhook._dead_letter` so the operator sees identical
    semantics whether the failure happened in Day 3 or Day 4.

    Iter-2026-02.rev32.1 — MUST persist `dead_lettered_at` on the row
    at the DEAD_LETTER transition (via `_stamp_dead_letter_evidence`).
    This timestamp is the independent signal rev32.1 uses to refuse
    writes even if state_machine later rolls the stage back (e.g. a
    retry path resumes at CUSTOMER_RESOLVED but leaves
    `dead_lettered_at` set). Without this write, the whole rev32.1
    (A) dead_letter guard is inert.
    """
    p1 = transition(from_stage=from_stage, to_stage=fail_stage,
                    actor="worker", error=error)
    p1.setdefault("$set", {})["pipeline_error"] = error
    await _apply(db, row_id, p1)
    p2 = transition(from_stage=fail_stage, to_stage="DEAD_LETTER",
                    actor="worker",
                    note="auto-routed: no retry — manual review required",
                    existing_started_at=started_at)
    _stamp_dead_letter_evidence(p2, fail_stage=fail_stage, error=error)
    await _apply(db, row_id, p2)
    return "DEAD_LETTER"


def _stamp_dead_letter_evidence_local(
    patch: dict, *, fail_stage: str, error: Optional[dict] = None,
) -> dict:
    """DEPRECATED shim — retained for import-graph stability during
    the rev32.1 rollout. Prefer the module-level
    `stamp_dead_letter_evidence` from rev32_hardening (imported as
    `_stamp_dead_letter_evidence`). See rev32_hardening for the
    canonical docstring.
    """
    return _stamp_dead_letter_evidence(
        patch, fail_stage=fail_stage, error=error)


# ─────────────────────────────────────────────────────────────────────
# Per-row processor
# ─────────────────────────────────────────────────────────────────────
async def process_normalized_row(
    db, row: dict, *, api_client=None,
) -> dict:
    """Advance a single `NORMALIZED` row through rules → customer.

    Returns a small result dict for the orchestrating endpoint.

    Idempotency: this function checks `pipeline_stage` before each
    transition; calling it twice on the same row never double-advances.
    """
    if row.get("pipeline_stage") != "NORMALIZED":
        return {
            "row_id": row.get("id"),
            "skipped": True,
            "reason": "not_in_normalized_stage",
            "pipeline_stage": row.get("pipeline_stage"),
        }

    canonical = row.get("canonical_payload")
    if not canonical:
        # Shouldn't happen — NORMALIZED rows always carry the DTO.
        return await _dead_letter(
            db,
            row_id=row["id"],
            from_stage="NORMALIZED",
            fail_stage="FAILED_NORMALIZATION",
            error={"code": "canonical_payload_missing",
                   "message": "NORMALIZED row has no canonical_payload"},
            started_at=row.get("pipeline_started_at"),
        ) and {"row_id": row["id"], "outcome": "DEAD_LETTER"}

    user_id = row.get("user_id", "main")
    trace_id = row.get("trace_id")

    # Rehydrate the typed DTO so business_rules can use attribute access.
    try:
        dto = SalesOrderDTO(**canonical)
    except Exception as exc:   # defensive — corrupt persisted DTO
        await _dead_letter(
            db, row_id=row["id"], from_stage="NORMALIZED",
            fail_stage="FAILED_NORMALIZATION",
            error={"code": "canonical_payload_invalid",
                   "message": f"{exc.__class__.__name__}: {exc}"},
            started_at=row.get("pipeline_started_at"),
        )
        return {"row_id": row["id"], "outcome": "DEAD_LETTER",
                "reason": "canonical_payload_invalid"}

    settings = await _load_settings(db, user_id)

    # ── Iter-2026-02.rev25 — Worker-context observability trace ────
    # Write ONE compact document showing what THIS worker (this pid,
    # this pipeline.py sha) actually saw in `settings` at the moment
    # of processing THIS row. Non-mutating: never touches
    # pipeline_stage, qoyod_* ids, or settings. Fault-tolerant: any
    # write failure is swallowed so a trace bug can never abort a
    # live run. Read at:
    #   db.integration_inbox.findOne({trace_id: <t>}, {sas_worker_trace: 1})
    from integrations.qoyod.sas_worker_trace import write_worker_trace

    # ── Iter-2026-02.rev16 — Selective Auto-Send Gate ──────────────
    # When `selective_auto_send_enabled=true`, this gate is the ONE
    # control that opens automatic Qoyod writes. Enforces 9 hard
    # invariants (cutover, status allow-list, hard blocks, payment
    # method allow-list, mapping resolves, no real invoice yet).
    # A gate PASS is persisted on the row so subsequent stages
    # (CUSTOMER_RESOLVED → INVOICE_CREATED) reuse the decision
    # without re-computing. The gate PASS ALSO grants a SCOPED
    # write allowance for THIS ROW only — the DB
    # `production_writes_locked` flag is NEVER mutated.
    _sas_gate_passed = False
    _sas_enabled_setting = bool(
        settings.get("selective_auto_send_enabled", False))
    # rev28 + rev29c — Gate persist buffer. The gate decision is
    # persisted on EVERY row — even when SAS is disabled at settings.
    # rev29c: `rev29c — Fail-closed gate persistence`. Rationale:
    # `selective_auto_send_enabled` is toggleable at runtime. If a
    # row is processed while SAS is OFF and then the operator later
    # flips it ON, the diagnostic invariant would false-flag the
    # historical row as `sas_gate_missing_violation`. By ALWAYS
    # writing a synthetic `sas_disabled_by_settings` gate record,
    # we make the invariant deterministic: every row past
    # NORMALIZED carries an explicit gate decision.
    # rev29c fail-closed: if the gate write cannot be included in
    # the RULES_APPLIED atomic CAS, the pipeline aborts BEFORE any
    # customer/product/invoice stage runs.
    from integrations.qoyod.selective_auto_send_gate import (
        evaluate_selective_auto_send_gate,
    )
    _sas_gate_persist_set: dict = {}
    if _sas_enabled_setting:
        _sas = evaluate_selective_auto_send_gate(
            canonical=canonical, row=row, settings=settings)
        _sas_gate_persist_set = {
            "selective_auto_send_gate":    _sas.to_log_dict(),
            "selective_auto_send_gate_at":
                datetime.now(timezone.utc).isoformat(),
            "selective_auto_send_gate_source": "sas_enabled_at_worker",
        }
        # Persist decision immediately (audit / UI). Same fields will
        # also be included in the next atomic transition (rev28
        # belt-and-suspenders).
        await db.integration_inbox.update_one(
            {"id": row["id"]},
            {"$set": _sas_gate_persist_set})
        # rev25 — worker trace AFTER gate evaluated (so we see decision).
        await write_worker_trace(
            db, row,
            stage="NORMALIZED",
            settings=settings, user_id_used=user_id,
            gate_ran=True,
            gate_eligible=bool(_sas.eligible),
            gate_reason=_sas.reason,
            gate_detail=_sas.detail,
        )
        if not _sas.eligible:
            patch = transition(
                from_stage="NORMALIZED", to_stage="SKIPPED",
                actor="worker",
                note=f"selective_auto_send_gate: {_sas.reason}",
                existing_started_at=row.get("pipeline_started_at"),
            )
            patch.setdefault("$set", {})["selective_auto_send_gate"] = \
                _sas.to_log_dict()
            # rev26 — atomic CAS: only writes SKIPPED if row is still
            # at NORMALIZED. If another handler already moved it,
            # the pipeline aborts loudly (no further side-effect).
            try:
                await _apply_atomic(
                    db, row["id"], patch,
                    expected_from_stage="NORMALIZED")
            except _StaleStageError as e:
                logger.warning(
                    "sas_reject_stale_stage row_id=%s trace_id=%s %s",
                    row.get("id"), trace_id, e)
                return {
                    "row_id":   row["id"],
                    "outcome":  "STALE_STAGE_ABORT",
                    "reason":   "row_stage_changed_before_sas_reject_write",
                    "expected": "NORMALIZED",
                    "actual":   e.actual,
                    "trace_id": trace_id,
                }
            return {
                "row_id":   row["id"],
                "outcome":  "SKIPPED",
                "reason":   _sas.reason,
                "detail":   _sas.detail,
                "trace_id": trace_id,
                "selective_auto_send_gate": _sas.to_log_dict(),
            }
        _sas_gate_passed = True
    else:
        # rev29c — Persist a SYNTHETIC gate record even when SAS is
        # disabled. Rationale: `selective_auto_send_enabled` is a
        # runtime toggle. If a row is processed while SAS is OFF and
        # the operator later flips it ON, the historical row would
        # false-flag `sas_gate_missing_violation`. A synthetic record
        # with `reason=sas_disabled_by_settings` makes the invariant
        # deterministic: every row past NORMALIZED carries an explicit
        # gate decision that reflects what the worker actually saw at
        # processing time.
        _sas_gate_persist_set = {
            "selective_auto_send_gate": {
                "eligible": False,
                "reason":   "sas_disabled_by_settings",
                "detail":   (
                    "selective_auto_send_enabled=False when the worker "
                    f"processed this row (user_id={user_id!r}). The gate "
                    "function was not called; this synthetic record is "
                    "persisted so the diagnostic invariant "
                    "`sas_gate_missing_violation` stays deterministic "
                    "across settings toggles."),
                "resolved_payment_key": None,
            },
            "selective_auto_send_gate_at":
                datetime.now(timezone.utc).isoformat(),
            "selective_auto_send_gate_source": "sas_disabled_at_worker",
        }
        # Immediate write — belt-and-suspenders. Also included in the
        # RULES_APPLIED CAS below.
        await db.integration_inbox.update_one(
            {"id": row["id"]},
            {"$set": _sas_gate_persist_set})
        # rev25 — worker trace when SAS is OFF at the settings layer.
        # This is the exact diagnostic that surfaces "gate never ran"
        # while operator believes SAS is enabled — usually a user_id
        # mismatch between UI writes and worker reads.
        await write_worker_trace(
            db, row,
            stage="NORMALIZED",
            settings=settings, user_id_used=user_id,
            gate_ran=False,
            gate_reason="sas_enabled_setting_is_false",
            gate_detail=(
                "selective_auto_send_enabled read as False from "
                f"qoyod_settings for user_id={user_id!r}; gate "
                "function not called."),
        )

    # ── Status Eligibility Gate (Iter-282) ─────────────────────────
    # Status gate MUST run BEFORE totals_guard. Orders that are not
    # in an invoice-eligible status (e.g. `under_review`) must NEVER
    # touch the totals guard — otherwise a transient Salla payload
    # would DEAD_LETTER an order that is simply not finished yet.
    # The user directive (2026-02-27, Iter-282) is explicit:
    #   "إذا الحالة under_review يجب أن يذهب إلى SKIPPED، وليس DEAD_LETTER."
    # business_rules.evaluate() already encodes the eligibility
    # decision against `settings.invoice_trigger_statuses`.
    existing = await db.qoyod_invoices.find_one(
        {"user_id": user_id, "salla_order_id": dto.order_id},
        {"_id": 0, "status": 1, "qoyod_invoice_id": 1},
    )
    # Iter-2026-02.rev12 — already_sent guard applies ONLY when a
    # REAL قيود invoice id is present. `None`, `DRY:invoice:*`, and
    # `PREVIEW:invoice:*` sentinels are NOT real sends and MUST NOT
    # block a first live send. Mirrors `_is_real_invoice_id` used
    # elsewhere in the codebase (eligible_orders classifier).
    if existing is not None:
        from integrations.qoyod.eligible_orders import (
            _is_real_invoice_id,
        )
        if not _is_real_invoice_id(existing.get("qoyod_invoice_id")):
            existing = None
    decision: RulesDecision = evaluate_rules(
        dto, settings, existing_invoice_row=existing)
    if not decision.eligible:
        patch = transition(
            from_stage="NORMALIZED", to_stage="SKIPPED",
            actor="worker",
            note=f"business_rule: {decision.reason}",
            existing_started_at=row.get("pipeline_started_at"),
        )
        patch.setdefault("$set", {})["business_rules_decision"] = \
            decision.to_log_dict()
        await _apply(db, row["id"], patch)
        return {
            "row_id":   row["id"],
            "outcome":  "SKIPPED",
            "reason":   decision.reason,
            "trace_id": trace_id,
        }

    # ── Totals Guard (Iter-273, ordering fix Iter-282) ─────────────
    # Runs AFTER status eligibility. If Make.com / Salla silently
    # dropped line items (so `items_sum != subtotal`), refuse the
    # row outright. NO auto-retry: the fix lives upstream.
    # The guard now also embeds Mezan-VAT-15% diagnostics so the
    # operator sees salla_total vs mezan_expected_total side-by-side.
    totals = validate_totals(canonical)
    if not totals.ok:
        await db.integration_inbox.update_one(
            {"id": row["id"]},
            {"$set": {"totals_guard": totals.to_log_dict()}},
        )
        patch = transition(
            from_stage="NORMALIZED", to_stage="FAILED_VALIDATION",
            actor="worker",
            note=f"totals guard refused: {totals.code}",
        )
        patch.setdefault("$set", {})["pipeline_error"] = {
            "code":    totals.code,
            "message": totals.message,
            "details": totals.details,
        }
        await _apply(db, row["id"], patch)
        # Move straight to DEAD_LETTER — totals mismatch is upstream-misconfigured,
        # retrying without a Make.com fix would just fail again.
        dead_patch = transition(
            from_stage="FAILED_VALIDATION", to_stage="DEAD_LETTER",
            actor="worker",
            note="totals mismatch is upstream — no auto-retry",
        )
        # rev32.1 — stamp dead-letter evidence trio.
        _stamp_dead_letter_evidence(
            dead_patch, fail_stage="FAILED_VALIDATION",
            error={"code": totals.code, "message": totals.message})
        await _apply(db, row["id"], dead_patch)
        return {
            "row_id":   row["id"],
            "outcome":  "DEAD_LETTER",
            "reason":   totals.code,
            "trace_id": trace_id,
            "totals_guard": totals.to_log_dict(),
        }

    # Persist the Mezan VAT diagnostics on the inbox row even when
    # totals_guard passes — useful for audit + UI display.
    await db.integration_inbox.update_one(
        {"id": row["id"]},
        {"$set": {"totals_guard": totals.to_log_dict(),
                  "mezan_vat_diagnostics":
                      (totals.details or {}).get("mezan_vat_diagnostics")}},
    )

    # If caller didn't pre-build an API client, build one now —
    # honouring dry_run_mode so the customer resolver doesn't reach
    # Qoyod when the operator is testing.
    if api_client is None:
        api_client, _is_dry = await _get_api_client(
            db, user_id, settings,
            scoped_write_allowance=_sas_gate_passed,
            row_id=row.get("id"))
        if api_client is None:
            await _dead_letter(
                db, row_id=row["id"], from_stage="NORMALIZED",
                fail_stage="FAILED_CUSTOMER",
                error={"code": "no_credentials",
                       "message": "Qoyod API key not configured"},
                started_at=row.get("pipeline_started_at"),
            )
            return {"row_id": row["id"], "outcome": "DEAD_LETTER",
                    "reason": "no_credentials"}

    # rev29c — Canonical dry-run mode signal used for wording. Truthy
    # if ANY of:
    #   • api_client is DryRunQoyodClient (hard signal — no HTTP)
    #   • settings.dry_run_mode is True (operator toggle)
    # This is the PRIMARY dry signal for stage-note wording. Even if
    # a customer or product was mapped locally to a real Qoyod id
    # (prior live sync), a dry-run POST still MUST NOT be described
    # as "created in Qoyod". Belt-and-suspenders below combines this
    # with the resolved-id prefix check.
    from integrations.qoyod.invoice_builder import (
        DryRunQoyodClient as _DryRunQoyodClient_check,
    )
    _pipeline_is_dry_mode = bool(
        isinstance(api_client, _DryRunQoyodClient_check)
        or settings.get("dry_run_mode", False))

    # Existing invoice row — used by `trigger_once_only`.
    # Note: decision was already evaluated above (Iter-282 status gate
    # ordering). We now know the order is ELIGIBLE — proceed with
    # RULES_APPLIED transition and the rest of the pipeline.

    # rev26 — Hard guard: refuse to advance past NORMALIZED if the
    # SAS gate already rejected this row (defense-in-depth against
    # any code path that might have bypassed the earlier check).
    try:
        await _assert_sas_not_rejected(db, row["id"])
    except _StaleStageError as e:
        logger.warning(
            "sas_reject_guard_at_rules_applied row_id=%s trace_id=%s %s",
            row.get("id"), trace_id, e)
        return {
            "row_id":   row["id"],
            "outcome":  "STALE_STAGE_ABORT",
            "reason":   "sas_gate_rejected_row_no_further_side_effects",
            "detail":   str(e),
            "trace_id": trace_id,
        }

    # ── NORMALIZED → RULES_APPLIED ──────────────────────────────────
    # rev29c — Fail-closed gate persistence: `_sas_gate_persist_set`
    # MUST be non-empty by this point. Both the SAS-enabled and
    # SAS-disabled branches above populate it. If it's empty here,
    # the pipeline aborts before advancing so no row ever advances
    # past NORMALIZED without an explicit gate record.
    if not _sas_gate_persist_set:
        logger.error(
            "rev29c fail-closed: gate persist buffer empty "
            "row_id=%s trace_id=%s", row.get("id"), trace_id)
        # Move to DEAD_LETTER via atomic CAS so no side-effects
        # downstream can occur. The row stays visible with a clear
        # diagnostic pointing at the missing gate.
        dl_patch = transition(
            from_stage="NORMALIZED", to_stage="DEAD_LETTER",
            actor="worker",
            note="rev29c: gate persist buffer empty at RULES_APPLIED",
            existing_started_at=row.get("pipeline_started_at"),
        )
        dl_patch.setdefault("$set", {})["pipeline_error"] = {
            "code":    "sas_gate_persist_buffer_empty",
            "message": (
                "rev29c fail-closed: no gate decision to persist; "
                "refusing to advance past NORMALIZED."),
        }
        # rev32.1 — stamp dead-letter evidence trio.
        _stamp_dead_letter_evidence(
            dl_patch, fail_stage="NORMALIZED",
            error=dl_patch["$set"]["pipeline_error"])
        try:
            await _apply_atomic(
                db, row["id"], dl_patch,
                expected_from_stage="NORMALIZED")
        except _StaleStageError:
            pass
        return {
            "row_id":   row["id"],
            "outcome":  "DEAD_LETTER",
            "reason":   "sas_gate_persist_buffer_empty",
            "trace_id": trace_id,
        }
    patch = transition(
        from_stage="NORMALIZED", to_stage="RULES_APPLIED",
        actor="worker",
        note=f"eligible · triggered_by={decision.triggered_by_status} · "
             f"invoice_date={decision.invoice_date_source}",
    )
    patch.setdefault("$set", {})["business_rules_decision"] = \
        decision.to_log_dict()
    # rev28 — Include the SAS gate in the RULES_APPLIED write so
    # persist + stage-transition are ONE atomic op (rev29c: this
    # write ALSO carries `selective_auto_send_gate_source` so
    # historical rows can be distinguished by whether SAS was on
    # at processing time). Any row landing at RULES_APPLIED is
    # GUARANTEED to carry `selective_auto_send_gate`,
    # `selective_auto_send_gate_at`, and
    # `selective_auto_send_gate_source`.
    patch["$set"].update(_sas_gate_persist_set)
    # rev26 — atomic CAS on pipeline_stage=NORMALIZED. If the row
    # was already moved to SKIPPED / LOCKED_* by another handler,
    # this raises and the pipeline aborts without touching Qoyod.
    try:
        await _apply_atomic(
            db, row["id"], patch,
            expected_from_stage="NORMALIZED")
    except _StaleStageError as e:
        logger.warning(
            "rules_applied_stale_stage row_id=%s trace_id=%s %s",
            row.get("id"), trace_id, e)
        return {
            "row_id":   row["id"],
            "outcome":  "STALE_STAGE_ABORT",
            "reason":   "row_stage_changed_before_rules_applied_write",
            "expected": "NORMALIZED",
            "actual":   e.actual,
            "trace_id": trace_id,
        }

    # ── RULES_APPLIED → CUSTOMER_RESOLVED ───────────────────────────
    # rev26 — Second SAS guard just before the FIRST Qoyod side-effect
    # (customer create). Defense-in-depth against any bypass path.
    # rev29d — Hard preflight: refuse to advance if the DB row is
    # missing `selective_auto_send_gate`. This catches rows built by
    # a stale worker running pre-rev29c code (the exact failure the
    # user reported on prod trace `8cfeba3cf139456198eef63cf97065cf`).
    try:
        await _require_sas_gate_persisted(
            db, row["id"], stage="CUSTOMER_RESOLVED")
    except _SasGateMissingError as e:
        logger.error(
            "rev29d gate_missing_before_customer_resolved row_id=%s "
            "trace_id=%s row_worker_sha=%s",
            row.get("id"), trace_id, e.worker_sha)
        await _dead_letter(
            db, row_id=row["id"], from_stage="RULES_APPLIED",
            fail_stage="FAILED_CUSTOMER",
            error={
                "code":    "sas_gate_missing_before_downstream",
                "message": str(e),
                "stage":   "CUSTOMER_RESOLVED",
                "row_worker_sha": e.worker_sha,
            },
            started_at=row.get("pipeline_started_at"),
        )
        return {
            "row_id":   row["id"],
            "outcome":  "DEAD_LETTER",
            "reason":   "sas_gate_missing_before_downstream",
            "stage":    "CUSTOMER_RESOLVED",
            "trace_id": trace_id,
        }
    try:
        await _assert_sas_not_rejected(db, row["id"])
    except _StaleStageError as e:
        logger.warning(
            "sas_reject_guard_at_customer_resolver row_id=%s trace_id=%s %s",
            row.get("id"), trace_id, e)
        return {
            "row_id":   row["id"],
            "outcome":  "STALE_STAGE_ABORT",
            "reason":   "sas_gate_rejected_row_before_customer_resolver",
            "detail":   str(e),
            "trace_id": trace_id,
        }
    # ── Rev32 guard #1: before resolve_customer (create_customer path)
    # ChatGPT review blocker (Rev32 v2): guard MUST wrap ALL four
    # write actions (create_customer, create_product, create_invoice,
    # create_invoice_payment). Skipped when `_pipeline_is_dry_mode` is
    # True — same rationale as invoice/payment guards downstream.
    if not _pipeline_is_dry_mode:
        pm_for_guard = (canonical.get("payment_method")
                        or canonical.get("payment_method_native"))
        try:
            await _rev32_assert_final_write_permitted(
                db, row["id"],
                action="create_customer",
                payment_method=pm_for_guard,
                user_id=user_id,
            )
        except Rev32Violation as _v:
            logger.error(
                "rev32 create_customer_blocked row_id=%s trace_id=%s "
                "violation_type=%s reason=%s",
                row.get("id"), trace_id, _v.violation_type, _v.reason)
            await _dead_letter(
                db, row_id=row["id"],
                from_stage="RULES_APPLIED",
                fail_stage="FAILED_CUSTOMER",
                error={
                    "code":           "rev32_guard_blocked",
                    "violation_type": _v.violation_type,
                    "message":        _v.reason,
                    "evidence":       _v.evidence,
                },
                started_at=row.get("pipeline_started_at"))
            return {
                "row_id":         row["id"],
                "outcome":        "REV32_BLOCKED",
                "reason":         _v.violation_type,
                "violation_type": _v.violation_type,
                "step":           "create_customer",
                "trace_id":       trace_id,
            }
    res: ResolutionResult = await resolve_customer(
        db, user_id, dto.customer,
        trace_id=trace_id,
        default_customer_id=settings.get("default_customer_id"),
        api_client=api_client,
    )

    if not res.success:
        await _dead_letter(
            db, row_id=row["id"],
            from_stage="RULES_APPLIED",
            fail_stage="FAILED_CUSTOMER",
            error=res.error,
            started_at=row.get("pipeline_started_at"),
        )
        # Persist the EXACT payload we sent (or tried to send) to
        # Qoyod, plus the full customer_resolution log. This is the
        # only way the operator can verify post-mortem that the
        # `name` AND `contact_name` fields actually reached the API
        # — saves a debug round-trip and breaks any "did the fix
        # deploy?" doubt with concrete evidence.
        if res.qoyod_request_payload is not None:
            await db.integration_inbox.update_one(
                {"id": row["id"]},
                {"$set": {
                    "qoyod_payloads.customer_request":
                        res.qoyod_request_payload,
                    "qoyod_payloads.customer_request_at": _now(),
                    "customer_resolution": res.to_log_dict(),
                }},
            )
        return {
            "row_id":   row["id"],
            "outcome":  "DEAD_LETTER",
            "reason":   "FAILED_CUSTOMER",
            "trace_id": trace_id,
            "decision": decision.to_log_dict(),
            "customer": res.to_log_dict(),
        }

    # rev28 + rev29b — Dry-run wording enforcement (rev29c: strengthened
    # to use `_pipeline_is_dry_mode` as primary signal, not just id
    # prefix).
    # Primary signal is `_pipeline_is_dry_mode` (client type +
    # settings.dry_run_mode). Fallback signal is the resolved id
    # prefix. Either signal means the note MUST say "DRY-RUN".
    # rev29c fix for prod trace `b09392fb...`: a customer created
    # via DryRunQoyodClient in dry-run mode used to fall through to
    # the "customer created in Qoyod" branch when the local mapping
    # briefly returned a real id (racy sync) OR when the operator
    # inspected the row after re-processing.
    _is_dry_customer = (
        _pipeline_is_dry_mode
        or (isinstance(res.qoyod_customer_id, str)
            and res.qoyod_customer_id.startswith(("DRY:", "PREVIEW:"))))
    if _is_dry_customer:
        _customer_note = ("DRY-RUN: customer payload built, no POST"
                          if res.created_new
                          else "DRY-RUN: customer mapped from local store, "
                               "no POST")
    else:
        _customer_note = ("customer mapped from local store"
                          if not res.created_new
                          else "customer created in Qoyod")
    patch = transition(
        from_stage="RULES_APPLIED", to_stage="CUSTOMER_RESOLVED",
        actor="worker",
        note=_customer_note,
    )
    patch.setdefault("$set", {}).update({
        "customer_resolution": res.to_log_dict(),
        "qoyod_customer_id":   res.qoyod_customer_id,
    })
    # rev29 — Atomic CAS on RULES_APPLIED → CUSTOMER_RESOLVED. If the
    # row was already advanced by a concurrent worker or requeue, this
    # returns STALE_STAGE_ABORT with no side-effect.
    try:
        await _apply_atomic(
            db, row["id"], patch,
            expected_from_stage="RULES_APPLIED")
    except _StaleStageError as e:
        logger.warning(
            "rev29 customer_resolved_stale row_id=%s trace_id=%s %s",
            row.get("id"), trace_id, e)
        return {
            "row_id":   row["id"],
            "outcome":  "STALE_STAGE_ABORT",
            "reason":   "row_stage_changed_before_customer_resolved_write",
            "expected": "RULES_APPLIED",
            "actual":   e.actual,
            "trace_id": trace_id,
        }
    return {
        "reason":   None,
        "trace_id": trace_id,
        "decision": decision.to_log_dict(),
        "customer": res.to_log_dict(),
    }


# ─────────────────────────────────────────────────────────────────────
# Batch entry point — what the `/pipeline/process-normalized` endpoint
# calls. Strict Day-4 ceiling: stops at CUSTOMER_RESOLVED.
# ─────────────────────────────────────────────────────────────────────
def _live_write_permitted(settings: dict) -> tuple[bool, str]:
    """Iter-2026-02.rev27 — SINGLE source of truth for whether the
    pipeline may perform any REAL Qoyod POST.

    Returns `(permitted, reason)`. If `permitted=False`, EVERY
    downstream call MUST use DryRunQoyodClient — no exceptions,
    no scoped bypass, no per-row override.

    Live writes require ALL of these to be true (verbatim from user
    directive after order 270253311 leaked invoice 188):

      • dry_run_mode                  = False
      • selective_live_send_enabled   = True
      • production_writes_locked      = False
      • selective_auto_send_enabled   = True    (SAS is the ONLY
                                                 automatic writer)

    NOTE: The row-level SAS-gate `eligible=True` is a NECESSARY
    (already enforced upstream) but NOT SUFFICIENT condition. This
    function is the LAST line of defense before a real POST.
    """
    if bool(settings.get("dry_run_mode", False)):
        return False, "dry_run_mode_is_true"
    if not bool(settings.get("selective_live_send_enabled", False)):
        return False, "selective_live_send_enabled_is_false"
    if bool(settings.get("production_writes_locked", False)):
        return False, "production_writes_locked_is_true"
    if not bool(settings.get("selective_auto_send_enabled", False)):
        return False, "selective_auto_send_enabled_is_false"
    return True, "all_gates_permit_live_write"


async def _get_api_client(
    db, user_id: str, settings: dict,
    *,
    scoped_write_allowance: bool = False,  # kept for signature compat
    row_id: Optional[str] = None,
):
    """Return `(client, is_dry)`.

    Iter-2026-02.rev27 — STRICT Live-Write Gate (REPLACES rev17).
    The prior scoped-bypass semantics (SAS gate PASS → real live
    client regardless of `dry_run_mode` / `selective_live_send_enabled`)
    is REVOKED. It caused order 270253311 to leak a real Qoyod
    invoice #188 while the operator's settings said dry-run.

    New semantics:
      • Live client is returned ONLY if `_live_write_permitted(settings)`
        AND the pipeline explicitly requested it via
        `scoped_write_allowance=True` (i.e. SAS gate passed for this row).
      • ANY OTHER STATE → DryRunQoyodClient. The DB flags remain
        unchanged; the operator is in full control by toggling any
        one of the four global switches.

    The `scoped_write_allowance` flag is now a NECESSARY-BUT-NOT-
    SUFFICIENT condition — it can no longer bypass the global gates.

    Iter-294 — Real clients always carry the Global Write Lock snapshot
    so writes are refused at the api_client layer when
    `production_writes_locked=True` (defensive redundancy; the gate
    above already rejects this state).

    rev32 — When `row_id` is supplied, we ALSO refuse to build a live
    client if the row was built by a stale worker (worker_code_mismatch).
    This is the FIRST layer of Issue #5 Rev32 hardening: even if all
    settings permit a live POST, a stale-worker row is downgraded to
    DryRun so no leaked write can happen. The final pre-POST guard
    (`_rev32_assert_final_write_permitted`) is the second layer.
    """
    permitted, _reason = _live_write_permitted(settings)
    # rev32 — Stale worker POST block.
    #
    # v2 (post-ChatGPT-review): missing worker sha in live context
    # is treated as stale (fail-CLOSED). The downgrade now fires the
    # auto kill-switch and persists row-level rev32_flags — silent
    # downgrade was insufficient because operators had no signal
    # that a stale worker slipped through.
    if permitted and scoped_write_allowance and row_id:
        try:
            stale, row_sha, current_sha = await _rev32_is_stale_worker_row(
                db, row_id, live_context=True)
        except Exception as _e:  # noqa: BLE001
            # Never break the pipeline for a diagnostic read.
            logger.warning(
                "rev32 stale_worker_check_failed row_id=%s err=%s",
                row_id, _e)
            stale = False
            row_sha = None
            current_sha = None
        if stale:
            logger.error(
                "rev32 stale_worker_downgrade_to_dry row_id=%s "
                "row_sha=%s current_sha=%s",
                row_id, row_sha, current_sha)
            try:
                await _rev32_flag_stale_worker_downgrade(
                    db, row_id,
                    row_sha=row_sha, current_sha=current_sha,
                    user_id=user_id,
                    action_context="_get_api_client",
                )
            except Exception as _e2:  # noqa: BLE001
                logger.error(
                    "rev32 stale_worker_flag_persist_failed row_id=%s "
                    "err=%s", row_id, _e2)
            return DryRunQoyodClient(), True
    # Live-write requires BOTH permission AND an explicit scoped ask.
    # Falling into dry-run for either omission is the safe default.
    if permitted and scoped_write_allowance:
        key = await get_api_key(db, user_id)
        if not key:
            return None, False
        # rev32.1 — Pass row_id/trace_id + user_id so the api_client
        # write methods can invoke the rev32.1 pre-flight against a
        # FRESH DB read. Without this, direct api_client callers
        # bypass the guard.
        trace_id_for_client = None
        if row_id:
            try:
                _r = await db.integration_inbox.find_one(
                    {"id": row_id}, {"trace_id": 1, "_id": 0})
                trace_id_for_client = (_r or {}).get("trace_id")
            except Exception:  # noqa: BLE001
                trace_id_for_client = None
        return QoyodAPIClient(
            key,
            db=db, user_id=user_id,
            write_lock_enabled=False,
            row_id=row_id,
            trace_id=trace_id_for_client,
        ), False
    # All other paths → dry-run client. No real POST possible.
    return DryRunQoyodClient(), True


async def process_customer_resolved_row(
    db, row: dict, *, api_client=None,
) -> dict:
    """Advance a single CUSTOMER_RESOLVED row through:
        4b products → preflight → 4c invoice → 4d receipt → COMPLETED.

    Honours dry_run_mode (no Qoyod POST), records payload snapshots,
    routes receipt-only failures to PARTIAL_FAILURE.
    """
    if row.get("pipeline_stage") != "CUSTOMER_RESOLVED":
        return {"row_id": row.get("id"), "skipped": True,
                "reason": "not_in_customer_resolved_stage",
                "pipeline_stage": row.get("pipeline_stage")}

    # rev32 — Terminal-stage hard stop. FRESH DB read: even if the
    # in-memory `row` snapshot says CUSTOMER_RESOLVED, a concurrent
    # worker or requeue may have moved the DB row to SKIPPED /
    # DEAD_LETTER / etc. Refuse to continue in that case — this is
    # the primary fix for GitHub Issue #5 control_flow_violation
    # (SKIPPED → PRODUCT_RESOLVED → INVOICE_CREATED → COMPLETED).
    try:
        await _rev32_assert_not_at_terminal_stage(
            db, row["id"],
            expected_stage="CUSTOMER_RESOLVED",
            user_id=row.get("user_id") or "main")
    except Rev32Violation as _v:
        logger.error(
            "rev32 terminal_stage_hard_stop_at_customer_resolved "
            "row_id=%s trace_id=%s evidence=%s",
            row.get("id"), row.get("trace_id"), _v.evidence)
        return {
            "row_id":         row.get("id"),
            "outcome":        "REV32_TERMINAL_STAGE_ABORT",
            "reason":         _v.reason,
            "violation_type": _v.violation_type,
            "trace_id":       row.get("trace_id"),
            "evidence":       _v.evidence,
        }

    # rev29d — Hard preflight. Refuse to run product/invoice/receipt
    # stages if the row was built without `selective_auto_send_gate`.
    # Fixes prod trace `8cfeba3cf139456198eef63cf97065cf` where the
    # row landed at CUSTOMER_RESOLVED with no gate — indicating a
    # stale worker.
    try:
        await _require_sas_gate_persisted(
            db, row["id"], stage="PRODUCT_RESOLVED")
    except _SasGateMissingError as e:
        logger.error(
            "rev29d gate_missing_before_product_resolved row_id=%s "
            "row_worker_sha=%s", row.get("id"), e.worker_sha)
        await _dead_letter(
            db, row_id=row["id"], from_stage="CUSTOMER_RESOLVED",
            fail_stage="FAILED_PRODUCT",
            error={
                "code":    "sas_gate_missing_before_downstream",
                "message": str(e),
                "stage":   "PRODUCT_RESOLVED",
                "row_worker_sha": e.worker_sha,
            },
            started_at=row.get("pipeline_started_at"),
        )
        return {"row_id": row["id"], "outcome": "DEAD_LETTER",
                "reason": "sas_gate_missing_before_downstream",
                "stage":  "PRODUCT_RESOLVED"}

    user_id = row.get("user_id", "main")
    trace_id = row.get("trace_id")
    started_at = row.get("pipeline_started_at")
    canonical = row.get("canonical_payload") or {}
    qoyod_customer_id = row.get("qoyod_customer_id")
    settings = await _load_settings(db, user_id)

    # Iter-294 — Stamp write-lock audit context for this row so any
    # api_client write-block records this order_number + trace_id.
    # contextvars are task-isolated; the set value naturally goes out
    # of scope when this coroutine returns.
    set_write_lock_context(
        order_number=str(canonical.get("order_number")
                         or row.get("salla_order_number") or "") or None,
        trace_id=trace_id,
        callsite="pipeline.process_customer_resolved_row",
    )

    # Iter-2026-02.rev16 — Re-evaluate Selective Auto-Send gate at
    # this stage too. The gate PASS on entry to NORMALIZED does NOT
    # imply the row is still eligible now — the operator may have
    # disabled the switch mid-flight, or the payment_method mapping
    # may have been unmapped. Idempotent: safe to re-run.
    from integrations.qoyod.sas_worker_trace import write_worker_trace
    _sas_gate_passed = False
    _sas_enabled_setting = bool(
        settings.get("selective_auto_send_enabled", False))
    if _sas_enabled_setting:
        from integrations.qoyod.selective_auto_send_gate import (
            evaluate_selective_auto_send_gate,
        )
        _sas = evaluate_selective_auto_send_gate(
            canonical=canonical, row=row, settings=settings)
        # rev25 — trace AFTER gate evaluated.
        await write_worker_trace(
            db, row,
            stage="CUSTOMER_RESOLVED",
            settings=settings, user_id_used=user_id,
            gate_ran=True,
            gate_eligible=bool(_sas.eligible),
            gate_reason=_sas.reason,
            gate_detail=_sas.detail,
        )
        if not _sas.eligible:
            patch = transition(
                from_stage="CUSTOMER_RESOLVED", to_stage="SKIPPED",
                actor="worker",
                note=("selective_auto_send_gate re-eval failed: "
                      f"{_sas.reason}"),
            )
            patch.setdefault("$set", {})[
                "selective_auto_send_gate"] = _sas.to_log_dict()
            # ChatGPT review blocker (Rev32 v2): CUSTOMER_RESOLVED →
            # SKIPPED MUST be CAS. Previously used non-atomic `_apply`
            # which could stomp on a concurrent worker that already
            # transitioned the row (e.g. sas gate PARTIAL_FAILURE).
            # `_apply_atomic` guards from_stage=CUSTOMER_RESOLVED so
            # any concurrent race becomes a StaleStage abort, not a
            # silent overwrite.
            try:
                await _apply_atomic(
                    db, row["id"], patch,
                    expected_from_stage="CUSTOMER_RESOLVED")
            except _StaleStageError as _cas_e:
                logger.warning(
                    "sas_skipped_cas_lost row_id=%s trace_id=%s "
                    "reason=%s — concurrent stage change: %s",
                    row.get("id"), trace_id, _sas.reason, _cas_e)
                return {
                    "row_id":   row["id"],
                    "outcome":  "STALE_STAGE_ABORT",
                    "reason":   "sas_skipped_cas_lost",
                    "detail":   (f"could not transition "
                                 f"CUSTOMER_RESOLVED→SKIPPED: "
                                 f"{_sas.reason}"),
                    "trace_id": trace_id,
                }
            return {
                "row_id":  row["id"],
                "outcome": "SKIPPED",
                "reason":  _sas.reason,
                "detail":  _sas.detail,
                "trace_id": trace_id,
                "selective_auto_send_gate": _sas.to_log_dict(),
            }
        _sas_gate_passed = True
    else:
        # rev25 — SAS is OFF at CUSTOMER_RESOLVED stage. This trace
        # + the NORMALIZED trace together tell us whether the switch
        # is genuinely off in the tenant settings, or whether the
        # worker is reading a different settings doc than the UI wrote.
        await write_worker_trace(
            db, row,
            stage="CUSTOMER_RESOLVED",
            settings=settings, user_id_used=user_id,
            gate_ran=False,
            gate_reason="sas_enabled_setting_is_false",
            gate_detail=(
                "selective_auto_send_enabled read as False for "
                f"user_id={user_id!r} at CUSTOMER_RESOLVED stage."),
        )

    # Resolve client (real or dry-run).
    client_provided = api_client is not None
    is_dry = is_dry_run_mode(settings)
    if not client_provided:
        api_client, is_dry = await _get_api_client(
            db, user_id, settings,
            scoped_write_allowance=_sas_gate_passed,
            row_id=row.get("id"))
        if api_client is None:
            await _dead_letter(
                db, row_id=row["id"], from_stage="CUSTOMER_RESOLVED",
                fail_stage="FAILED_PRODUCT",
                error={"code": "credentials_missing",
                       "message": "Qoyod API key not configured"},
                started_at=started_at)
            return {"row_id": row["id"], "outcome": "DEAD_LETTER",
                    "reason": "credentials_missing"}

    # rev29c — Canonical dry-run mode signal for wording at this
    # stage. Same shape as the NORMALIZED-stage signal. Truthy if
    # api_client is DryRunQoyodClient OR settings.dry_run_mode=True.
    from integrations.qoyod.invoice_builder import (
        DryRunQoyodClient as _DryRunQoyodClient_check,
    )
    _pipeline_is_dry_mode = bool(
        isinstance(api_client, _DryRunQoyodClient_check)
        or settings.get("dry_run_mode", False))

    # ── 4b PRODUCTS ─────────────────────────────────────────────────
    # rev26 — SAS guard before ANY Qoyod side-effect at this stage
    # (product create/lookup). Refuses to touch Qoyod for a row whose
    # SAS gate decision is `eligible=false`.
    try:
        await _assert_sas_not_rejected(db, row["id"])
    except _StaleStageError as e:
        logger.warning(
            "sas_reject_guard_at_products row_id=%s trace_id=%s %s",
            row.get("id"), trace_id, e)
        return {
            "row_id":   row["id"],
            "outcome":  "STALE_STAGE_ABORT",
            "reason":   "sas_gate_rejected_row_before_product_resolver",
            "detail":   str(e),
            "trace_id": trace_id,
        }
    # ── Rev32 guard #2: before resolve_products (create_product path)
    # ChatGPT review blocker (Rev32 v2): guard MUST wrap ALL four
    # write actions. Skipped when `_pipeline_is_dry_mode` is True.
    if not _pipeline_is_dry_mode:
        pm_for_guard = (canonical.get("payment_method")
                        or canonical.get("payment_method_native"))
        try:
            await _rev32_assert_final_write_permitted(
                db, row["id"],
                action="create_product",
                payment_method=pm_for_guard,
                user_id=user_id,
            )
        except Rev32Violation as _v:
            logger.error(
                "rev32 create_product_blocked row_id=%s trace_id=%s "
                "violation_type=%s reason=%s",
                row.get("id"), trace_id, _v.violation_type, _v.reason)
            await _dead_letter(
                db, row_id=row["id"],
                from_stage="CUSTOMER_RESOLVED",
                fail_stage="FAILED_PRODUCT",
                error={
                    "code":           "rev32_guard_blocked",
                    "violation_type": _v.violation_type,
                    "message":        _v.reason,
                    "evidence":       _v.evidence,
                },
                started_at=started_at)
            return {
                "row_id":         row["id"],
                "outcome":        "REV32_BLOCKED",
                "reason":         _v.violation_type,
                "violation_type": _v.violation_type,
                "step":           "create_product",
                "trace_id":       trace_id,
            }
    prod_res: ProductsResolutionResult = await resolve_products(
        db, user_id, canonical.get("items") or [], settings,
        trace_id=trace_id, api_client=api_client)
    if not prod_res.success:
        await _dead_letter(
            db, row_id=row["id"], from_stage="CUSTOMER_RESOLVED",
            fail_stage="FAILED_PRODUCT", error=prod_res.error,
            started_at=started_at)
        return {"row_id": row["id"], "outcome": "DEAD_LETTER",
                "reason": "FAILED_PRODUCT",
                "products": prod_res.to_log_dict()}

    product_resolutions = [
        {"sku": i.sku, "qoyod_product_id": i.qoyod_product_id,
         "created_new": i.created_new}
        for i in prod_res.items
    ]
    # rev28 + rev29b — Dry-run wording enforcement for the product
    # stage (rev29c: strengthened via `_pipeline_is_dry_mode`).
    # Primary signal: `_pipeline_is_dry_mode`. Fallback: ANY product
    # id with DRY:/PREVIEW: prefix. rev29c fix for prod trace
    # `b09392fb...` where products were mapped locally to real ids
    # (prior live sync) — the note used to fall through to
    # "N product(s) created · M mapped" even under DryRunQoyodClient.
    _any_dry_product = any(
        (isinstance(i.qoyod_product_id, str)
         and i.qoyod_product_id.startswith(("DRY:", "PREVIEW:")))
        for i in prod_res.items)
    _is_dry_product_stage = _pipeline_is_dry_mode or _any_dry_product
    _n_created = sum(1 for i in prod_res.items if i.created_new)
    _n_mapped  = sum(1 for i in prod_res.items if not i.created_new)
    if _is_dry_product_stage:
        _product_note = (
            f"DRY-RUN: {_n_created} product payload(s) built · "
            f"{_n_mapped} mapped · no POST")
    else:
        _product_note = (
            f"{_n_created} product(s) created · {_n_mapped} mapped")
    p = transition(from_stage="CUSTOMER_RESOLVED",
                   to_stage="PRODUCT_RESOLVED", actor="worker",
                   note=_product_note)
    p.setdefault("$set", {})["product_resolution"] = prod_res.to_log_dict()
    # rev29 — Atomic CAS on CUSTOMER_RESOLVED → PRODUCT_RESOLVED.
    try:
        await _apply_atomic(
            db, row["id"], p,
            expected_from_stage="CUSTOMER_RESOLVED")
    except _StaleStageError as e:
        logger.warning(
            "rev29 product_resolved_stale row_id=%s trace_id=%s %s",
            row.get("id"), trace_id, e)
        return {
            "row_id":   row["id"],
            "outcome":  "STALE_STAGE_ABORT",
            "reason":   "row_stage_changed_before_product_resolved_write",
            "expected": "CUSTOMER_RESOLVED",
            "actual":   e.actual,
            "trace_id": trace_id,
        }

    # ── PREFLIGHT CHECKLIST ─────────────────────────────────────────
    decision = row.get("business_rules_decision") or {}
    existing_invoice = await db.qoyod_invoices.find_one(
        {"user_id": user_id, "salla_order_id": canonical.get("order_id")},
        {"_id": 0, "status": 1, "qoyod_invoice_id": 1})
    # Iter-2026-02.rev12 — treat DRY:/PREVIEW:/null invoice ids as
    # "no prior send" for preflight idempotency (`already_sent`
    # check). Only real قيود ids count.
    if existing_invoice is not None:
        from integrations.qoyod.eligible_orders import (
            _is_real_invoice_id,
        )
        if not _is_real_invoice_id(
                existing_invoice.get("qoyod_invoice_id")):
            existing_invoice = None
    pf: PreflightResult = preflight_run(
        dto_dict=canonical, settings=settings,
        qoyod_customer_id=qoyod_customer_id,
        product_resolutions=product_resolutions,
        existing_invoice_row=existing_invoice,
    )
    if not pf.passed:
        # Pre-flight failure is BEFORE invoice. Treat it as FAILED_INVOICE
        # → DEAD_LETTER so the operator can see exactly which check failed.
        await _dead_letter(
            db, row_id=row["id"], from_stage="PRODUCT_RESOLVED",
            fail_stage="FAILED_INVOICE",
            error={"code": "preflight_failed",
                   "message": "pre-flight checklist did not pass",
                   "preflight": pf.to_log_dict()},
            started_at=started_at)
        return {"row_id": row["id"], "outcome": "DEAD_LETTER",
                "reason": "preflight_failed",
                "preflight": pf.to_log_dict()}

    # ── 4c INVOICE — build payload, snapshot, POST ──────────────────
    # rev26 — SAS guard before invoice payload build. This is critical
    # even for DRY-RUN because dry-run still generates DRY:invoice:*
    # ids that clutter the audit trail on rejected rows.
    try:
        await _assert_sas_not_rejected(db, row["id"])
    except _StaleStageError as e:
        logger.warning(
            "sas_reject_guard_at_invoice_build row_id=%s trace_id=%s %s",
            row.get("id"), trace_id, e)
        return {
            "row_id":   row["id"],
            "outcome":  "STALE_STAGE_ABORT",
            "reason":   "sas_gate_rejected_row_before_invoice_build",
            "detail":   str(e),
            "trace_id": trace_id,
        }
    # Reconstruct datetime from ISO so the payload builder gets a real obj.
    from datetime import datetime
    inv_date_iso = (decision or {}).get("invoice_date")
    inv_date = (datetime.fromisoformat(inv_date_iso.replace("Z", "+00:00"))
                if inv_date_iso else None)
    invoice_payload = build_invoice_payload(
        dto_dict=canonical, qoyod_customer_id=qoyod_customer_id,
        product_resolutions=product_resolutions,
        invoice_date=inv_date, settings=settings,
    )

    # ─── DRY-Run Leak Preflight (Iter-267, P0) ────────────────────
    # Hard refuse if ANY id we're about to send carries the `DRY:`
    # prefix from the Dry-Run era. Belt-and-suspenders: the product
    # resolver already quarantines such mappings, but a single line
    # that slipped through MUST stop the invoice before it touches
    # Qoyod. Production order 268670571 hit this on 2026-02-27.
    #
    # ONLY active in PRODUCTION (dry_run_mode=False). In dry-run mode
    # the stub `DRY:*` ids ARE expected and must not be refused.
    if not settings.get("dry_run_mode", False):
        leaked: list[str] = []
        if str(qoyod_customer_id).startswith("DRY:"):
            leaked.append(f"contact_id={qoyod_customer_id}")
        for li in (invoice_payload.get("invoice", {}).get("line_items") or []):
            pid = li.get("product_id")
            if pid is None or str(pid).startswith("DRY:"):
                leaked.append(f"product_id={pid}")
        if leaked:
            err = {
                "code":    "dry_run_product_id_leaked_to_production",
                "message": ("منع الإرسال: تم اكتشاف معرّفات Dry-Run في "
                            "payload الفاتورة (" + ", ".join(leaked) + "). "
                            "هذا تسرّب من فترة الاختبار. يجب إعادة "
                            "إنشاء المنتج/العميل في قيود فعلياً."),
                "leaked_ids":     leaked,
                "remediation":    "rebuild_mapping_against_real_qoyod",
            }
            await _dead_letter(
                db, row_id=row["id"],
                from_stage="PRODUCT_RESOLVED",
                fail_stage="FAILED_INVOICE",
                error=err,
                started_at=row.get("pipeline_started_at"),
            )
            await db.integration_inbox.update_one(
                {"id": row["id"]},
                {"$set": {
                    "qoyod_payloads.invoice_blocked_preflight": invoice_payload,
                    "qoyod_payloads.invoice_blocked_at":         _now(),
                }},
            )
            return {
                "row_id":   row["id"],
                "outcome":  "DEAD_LETTER",
                "reason":   "dry_run_product_id_leaked_to_production",
                "leaked":   leaked,
                "trace_id": trace_id,
            }

    # ─── Iter-290e — extract diagnostics & pre-POST totals guard ─────
    # build_invoice_payload returns {"invoice": {...}, "_diagnostics": {...}}
    # — the diagnostics MUST NOT be sent to Qoyod. Pop and keep for
    # auditing + the math guard below.
    invoice_diagnostics = invoice_payload.pop("_diagnostics", None) or {}
    # Math guard: if our reverse-engineered discount math doesn't land
    # within 0.10 SAR of Salla's total, refuse to POST. Accounting
    # correctness > resilience here — a wrong invoice is worse than a
    # missing one (operator can manually retry once the math is right).
    if (not settings.get("dry_run_mode", False)
            and invoice_diagnostics.get("pricing_mode") == "match_salla_total"):
        diff = abs(float(invoice_diagnostics.get("difference") or 0.0))
        if diff > 0.10:
            # ── Iter-293.1 — Refine the error code so operators see a
            # specific actionable diagnostic instead of a generic
            # "mismatch". Three cases, ordered by specificity:
            #
            #   (a) COD fee exists but `default_cod_fee_product_id` is
            #       missing → MISSING_COD_FEE_PRODUCT_ID.
            #   (b) Diff is large AND payment_method is COD AND no
            #       cod_fee surfaced in the payload → strong signal
            #       that Make.com / Salla webhook isn't sending the
            #       order-level fee. MISSING_ORDER_LEVEL_CHARGE with
            #       suspected_charge=cod_fee.
            #   (c) Anything else → fall back to the legacy mismatch
            #       code (defensive — should never fire on COD orders
            #       after the two checks above).
            cod_fee_amt = float(invoice_diagnostics.get("cod_fee_amount") or 0.0)
            cod_fee_missing_product = bool(
                invoice_diagnostics.get("cod_fee_missing_product"))
            pm = (canonical.get("payment_method")
                  or canonical.get("payment_method_native") or "").lower()
            from .payment_methods import is_cod_family
            is_cod = is_cod_family(pm)

            if cod_fee_missing_product:
                err = {
                    "code":    "MISSING_COD_FEE_PRODUCT_ID",
                    "cod_fee_detected":    True,
                    "cod_fee_amount":      cod_fee_amt,
                    "cod_fee_source_path": invoice_diagnostics.get("cod_fee_source_path"),
                    "cod_fee_source_type": invoice_diagnostics.get("cod_fee_source_type"),
                    "inferred_from_delta": False,
                    "message": (
                        f"الطلب يحوي رسوم COD = {cod_fee_amt} SAR (من مصدر "
                        f"{invoice_diagnostics.get('cod_fee_source_path')}) "
                        "لكن إعدادات قيود لا تحوي `default_cod_fee_product_id`. "
                        "افتح إعدادات قيود → معرّفات افتراضية، وأضف منتج "
                        "قيود لرسوم COD (SKU = MEZAN_COD_FEE)."
                    ),
                    "diagnostics": invoice_diagnostics,
                }
            elif is_cod and cod_fee_amt == 0:
                err = {
                    "code":            "MISSING_ORDER_LEVEL_CHARGE",
                    "cod_fee_detected": False,
                    "inferred_from_delta": True,  # the delta exists but
                                                  # we REFUSE to act on it
                    "salla_total":     invoice_diagnostics.get("salla_total"),
                    "items_total":     invoice_diagnostics.get("expected_qoyod_total"),
                    "missing_delta":   round(diff, 2),
                    "payment_method":  pm,
                    "suspected_charge": "cod_fee",
                    "message": (
                        f"الفرق {diff:.2f} SAR بين إجمالي سلة "
                        f"({invoice_diagnostics.get('salla_total')}) "
                        f"وإجمالي أسطر المنتجات "
                        f"({invoice_diagnostics.get('expected_qoyod_total')}) "
                        f"غير مدعوم بحقل صريح في Payload. "
                        "لن يُحوَّل تلقائياً إلى COD Fee — قد يحتاج "
                        "سيناريو Make إعادة ضبط ليُرسل `amounts.cash_on_delivery` "
                        "أو أي حقل رسوم أصلي من سلة."
                    ),
                    "diagnostics": invoice_diagnostics,
                }
            else:
                err = {
                    "code":    "invoice_total_mismatch_before_post",
                    "message": (f"منع الإرسال (Iter-290e): الفرق بين إجمالي قيود "
                                f"المتوقع ({invoice_diagnostics.get('expected_qoyod_total')}) "
                                f"وإجمالي سلة ({invoice_diagnostics.get('salla_total')}) "
                                f"= {diff:.2f} SAR > 0.10. لن تُنشأ فاتورة بمبلغ "
                                f"غير مطابق للمبلغ المدفوع."),
                    "diagnostics": invoice_diagnostics,
                }
            await db.integration_inbox.update_one(
                {"id": row["id"]},
                {"$set": {
                    "qoyod_payloads.invoice_blocked_preflight": invoice_payload,
                    "qoyod_payloads.invoice_diagnostics":       invoice_diagnostics,
                    "qoyod_payloads.invoice_blocked_at":        _now(),
                }})
            await _dead_letter(
                db, row_id=row["id"], from_stage="PRODUCT_RESOLVED",
                fail_stage="FAILED_INVOICE", error=err,
                started_at=row.get("pipeline_started_at"),
            )
            return {
                "row_id":  row["id"], "outcome": "DEAD_LETTER",
                "reason":  err["code"],
                "trace_id": trace_id,
            }

    # Snapshot BEFORE attempting POST.
    await db.integration_inbox.update_one(
        {"id": row["id"]},
        {"$set": {"qoyod_payloads.invoice": invoice_payload,
                  "qoyod_payloads.invoice_diagnostics": invoice_diagnostics,
                  "qoyod_payloads.invoice_snapshot_at": _now(),
                  "preflight": pf.to_log_dict()}},
    )

    qoyod_invoice_id = None
    qoyod_invoice_number = None
    invoice_idem = f"mzn-{trace_id}-invoice"
    inv_resp_raw: Any = None
    inv_started_ms = int(_now().timestamp() * 1000)
    # Iter-001k — Selective Send decision captured at the invoice site
    # so the payment site can reuse the SAME frozen send_timestamp.
    # `None` when we short-circuit via `existing_qid` (invoice was
    # already created in a prior run) OR when running in dry-run mode.
    selective_send_decision = None

    # Iter-291 — Idempotent invoice short-circuit. When a previous run
    # successfully created the Qoyod invoice but the receipt failed
    # afterwards (PARTIAL_FAILURE), retrying the row must NOT create a
    # duplicate invoice in Qoyod. Reuse the stored id and jump straight
    # to the receipt step.
    existing_qid = row.get("qoyod_invoice_id")
    if existing_qid and not is_dry:
        qoyod_invoice_id = str(existing_qid)
        qoyod_invoice_number = row.get("qoyod_invoice_number")
        await db.integration_inbox.update_one(
            {"id": row["id"]},
            {"$set": {
                "qoyod_responses.invoice.reused_from_previous_run": True,
                "qoyod_responses.invoice.reused_qoyod_id": qoyod_invoice_id,
                "qoyod_responses.invoice.reused_at": _now(),
            }})
        # Skip the create_invoice POST entirely and fall through to
        # the post-success branch which advances the stage to
        # INVOICE_CREATED (it tolerates re-applying the same stage).
    else:
        # ── Iter-001k — Selective Live Send guard (STRICTEST layer) ─
        # Runs BEFORE `_writes_blocked` so a policy block returns a
        # dedicated `SELECTIVE_SEND_BLOCKED:<code>` stage that names
        # the exact reason (gate_disabled / q2_cutoff / bank_hold /
        # trigger_status_not_enabled / etc.).
        # DRY-run pipelines skip this — they use a DryRunQoyodClient
        # that never touches api.qoyod.com.
        #
        # SKIP when `_writes_blocked` would fire anyway: the existing
        # LOCKED_AWAITING_APPROVAL path is preserved for the pure
        # write-lock case (no gate, no Q2, no DRY, etc. — just lock).
        # That keeps Iter-293.4-rev5 semantics intact.
        if not is_dry and not _writes_blocked(api_client, settings):
            policy_order = _build_policy_order_from_pipeline_scope(
                row=row, canonical=canonical,
                qoyod_customer_id=qoyod_customer_id,
                products_resolution=prod_res,
                invoice_diagnostics=invoice_diagnostics,
                is_dry=is_dry,
            )
            # Iter-001k — Delegate write-lock resolution to
            # `_writes_blocked` (single source of truth). This honours
            # the Iter-293.4 per-order unlock: an api_client with
            # `write_lock_enabled=False` (as produced by
            # one_shot_reprocess after a valid approval_phrase) is
            # treated as effectively unlocked for the policy, without
            # ever mutating the DB `production_writes_locked` flag.
            _policy_settings = dict(settings)
            _policy_settings["production_writes_locked"] = \
                _writes_blocked(api_client, settings)
            # Iter-2026-02.rev20 — Selective Auto-Send gate bypass.
            # When the row has passed the NEW `selective_auto_send_gate`
            # (rev16), the OLD `selective_send_policy` MUST NOT refuse
            # it with `gate_disabled` just because
            # `selective_live_send_enabled=false` on disk. The two
            # gates are INDEPENDENT — auto-gate = per-row (with cutover
            # / status / pm / mapping / no real invoice), old gate =
            # global master switch. When auto-gate passes, we inject
            # a scoped `selective_live_send_enabled=True` into the
            # policy-only settings snapshot. DB is NEVER modified —
            # this is a per-row execution-context override only.
            if _sas_gate_passed:
                _policy_settings["selective_live_send_enabled"] = True
                _policy_settings["dry_run_mode"] = False
            try:
                selective_send_decision = assert_send_allowed(
                    order=policy_order, settings=_policy_settings)
            except SelectiveSendPolicyBlocked as blocked:
                # Persist blocker + park the row. No Qoyod call.
                await db.integration_inbox.update_one(
                    {"id": row["id"]},
                    {"$set": {
                        "qoyod_payloads.invoice_selective_blocked_payload":
                            invoice_payload,
                        "qoyod_payloads.invoice_selective_blocked_at":
                            _now(),
                        "pipeline_stage":
                            f"SELECTIVE_SEND_BLOCKED:"
                            f"{blocked.blocker_code}",
                        "selective_send_blocker_code":
                            blocked.blocker_code,
                        "selective_send_blocker_reason":
                            blocked.blocker_reason,
                        "selective_send_blocked_step":  "invoice",
                        "selective_send_blocked_at":    _now(),
                    }})
                return {
                    "row_id":         row["id"],
                    "outcome":        "SELECTIVE_SEND_BLOCKED",
                    "reason":         blocked.blocker_code,
                    "blocker_reason": blocked.blocker_reason,
                    "step":           "invoice",
                    "trace_id":       trace_id,
                    "note": ("Selective Send policy refused this order "
                             "BEFORE any Qoyod API call. See "
                             "selective_send_blocker_code."),
                }
            # Policy allowed — stamp payload dates with send_date_riyadh.
            invoice_payload = apply_send_date_to_qoyod_payload(
                invoice_payload, selective_send_decision)

        # ── Iter-293.3 — Production Writes Kill Switch ────────────
        # ZATCA-sensitive guard: AFTER the Mezan↔Qoyod↔ZATCA chain is
        # live, any uncontrolled POST to `api.qoyod.com` could push a
        # tax-incorrect invoice to ZATCA. To prevent that the operator
        # can set `production_writes_locked = true` in settings.
        #
        # While locked:
        #   • The pipeline runs all the way through (normalize →
        #     preflight → product/customer resolve → invoice payload
        #     build → totals guard). NOTHING is sent to Qoyod.
        #   • The fully-built `invoice_payload` is persisted in
        #     `qoyod_payloads.invoice_locked_payload` for review.
        #   • The row's pipeline_stage becomes `LOCKED_AWAITING_APPROVAL`.
        #   • Operator reviews via the Preview-Reprocess endpoint,
        #     then explicitly approves the order via the existing
        #     `one_shot_reprocess` (which honours its own approval
        #     flow). New webhooks DO NOT auto-retry.
        #
        # This is INDEPENDENT of `dry_run_mode` (which uses DRY:* stub
        # ids and is intended for offline simulation). Production
        # writes lock keeps real ids but skips the POST.
        if _writes_blocked(api_client, settings):
            # Iter-293.4-rev2 — Pre-check must ALSO write to the audit
            # collection so /admin/write-lock-report surfaces the
            # blocked attempt. Previously only the api_client._request
            # path persisted; the pre-check short-circuited BEFORE
            # the client was called and the audit log missed it.
            attempt_id = await record_blocked_attempt(
                db, user_id=user_id, action="create_invoice",
                method="POST", path="/invoices",
                payload=invoice_payload,
                idempotency_key=invoice_idem,
            )
            await db.integration_inbox.update_one(
                {"id": row["id"]},
                {"$set": {
                    "qoyod_payloads.invoice_locked_payload": invoice_payload,
                    "qoyod_payloads.invoice_locked_at":      _now(),
                    "pipeline_stage":                        "LOCKED_AWAITING_APPROVAL",
                    "lock_reason":                           "production_writes_locked",
                    "lock_step":                             "invoice",
                    "lock_attempt_id":                       attempt_id,
                    "lock_diagnostics":                      invoice_diagnostics,
                }},
            )
            return {
                "row_id":     row["id"],
                "outcome":    "LOCKED_AWAITING_APPROVAL",
                "reason":     "production_writes_locked",
                "step":       "invoice",
                "attempt_id": attempt_id,
                "trace_id":   trace_id,
                "note":       ("Production writes are locked. Use the "
                               "Preview-Reprocess endpoint to review, "
                               "then one_shot_reprocess to send after "
                               "explicit per-order approval."),
            }

        try:
            # rev32 — Final pre-POST guard #3: before create_invoice.
            # Reads FRESH settings + row from DB and enforces all 8
            # conditions from Issue #5 §4. On violation, triggers auto
            # kill-switch (flips production_writes_locked=true +
            # selective_live_send_enabled=false), persists diagnostic
            # flags on the row, and raises Rev32Violation.
            #
            # Skipped when `_pipeline_is_dry_mode` is True (any
            # DryRunQoyodClient subclass OR settings.dry_run_mode=True)
            # because those paths cannot make real Qoyod POSTs. The
            # guard's purpose is to protect against LIVE leaks —
            # matching the same `_pipeline_is_dry_mode` signal used
            # elsewhere in the pipeline for dry-vs-live wording.
            if not is_dry and not _pipeline_is_dry_mode:
                pm_for_guard = (canonical.get("payment_method")
                                or canonical.get("payment_method_native"))
                try:
                    await _rev32_assert_final_write_permitted(
                        db, row["id"],
                        action="create_invoice",
                        payment_method=pm_for_guard,
                        user_id=user_id,
                    )
                except Rev32Violation as _v:
                    logger.error(
                        "rev32 create_invoice_blocked row_id=%s "
                        "trace_id=%s violation_type=%s reason=%s",
                        row.get("id"), trace_id, _v.violation_type,
                        _v.reason)
                    await db.integration_inbox.update_one(
                        {"id": row["id"]},
                        {"$set": {
                            "qoyod_payloads.invoice_rev32_blocked_payload":
                                invoice_payload,
                            "qoyod_payloads.invoice_rev32_blocked_at":
                                _now(),
                        }})
                    await _dead_letter(
                        db, row_id=row["id"],
                        from_stage="PRODUCT_RESOLVED",
                        fail_stage="FAILED_INVOICE",
                        error={
                            "code":           "rev32_guard_blocked",
                            "violation_type": _v.violation_type,
                            "message":        _v.reason,
                            "evidence":       _v.evidence,
                        },
                        started_at=started_at)
                    return {
                        "row_id":         row["id"],
                        "outcome":        "REV32_BLOCKED",
                        "reason":         _v.violation_type,
                        "violation_type": _v.violation_type,
                        "step":           "create_invoice",
                        "trace_id":       trace_id,
                    }
            inv_resp = await api_client.create_invoice(invoice_payload,
                                                       idem=invoice_idem)
            inv_resp_raw = inv_resp
            # Extract id/number — tolerant to a few shapes.
            if isinstance(inv_resp, dict):
                inv = inv_resp.get("invoice") if isinstance(inv_resp.get("invoice"), dict) else inv_resp
                qoyod_invoice_id = str(inv.get("id")) if inv.get("id") is not None else None
                qoyod_invoice_number = inv.get("number") or inv.get("reference")
        except QoyodWriteLockedError as exc:
            # Iter-294 — Safety-net catch (api_client refused the write
            # because production_writes_locked flipped to True after the
            # pre-check above). Snapshot and surface cleanly.
            await db.integration_inbox.update_one(
                {"id": row["id"]},
                {"$set": {
                    "qoyod_payloads.invoice_locked_payload": invoice_payload,
                    "qoyod_payloads.invoice_locked_at":      _now(),
                    "pipeline_stage":                        "LOCKED_AWAITING_APPROVAL",
                    "lock_reason":                           "production_writes_locked",
                    "lock_step":                             "invoice",
                    "lock_attempt_id":                       exc.attempt_id,
                    "lock_diagnostics":                      invoice_diagnostics,
                }})
            return {"row_id": row["id"],
                    "outcome": "LOCKED_AWAITING_APPROVAL",
                    "reason":  "production_writes_locked",
                    "step":    "invoice",
                    "attempt_id": exc.attempt_id,
                    "trace_id": trace_id}
        except QoyodAPIError as exc:
            await db.integration_inbox.update_one(
                {"id": row["id"]},
                {"$set": {
                    "qoyod_responses.invoice.error":      exc.to_log_dict(),
                    "qoyod_responses.invoice.received_at": _now(),
                    "qoyod_responses.invoice.duration_ms":
                        int(_now().timestamp() * 1000) - inv_started_ms,
                }})
            await _dead_letter(
                db, row_id=row["id"], from_stage="PRODUCT_RESOLVED",
                fail_stage="FAILED_INVOICE", error=exc.to_log_dict(),
                started_at=started_at)
            return {"row_id": row["id"], "outcome": "DEAD_LETTER",
                    "reason": "FAILED_INVOICE"}

    # Persist raw invoice response (success path) — First-Sync-Monitor.
    await db.integration_inbox.update_one(
        {"id": row["id"]},
        {"$set": {
            "qoyod_responses.invoice.body":        inv_resp_raw,
            "qoyod_responses.invoice.received_at": _now(),
            "qoyod_responses.invoice.duration_ms":
                int(_now().timestamp() * 1000) - inv_started_ms,
            "qoyod_responses.invoice.qoyod_id":    qoyod_invoice_id,
            "qoyod_responses.invoice.qoyod_number": qoyod_invoice_number,
        }})

    if not qoyod_invoice_id:
        await _dead_letter(
            db, row_id=row["id"], from_stage="PRODUCT_RESOLVED",
            fail_stage="FAILED_INVOICE",
            error={"code": "qoyod_response_missing_id",
                   "message": "create_invoice returned no id"},
            started_at=started_at)
        return {"row_id": row["id"], "outcome": "DEAD_LETTER",
                "reason": "FAILED_INVOICE"}

    # rev29b — Dry-run wording enforcement for the invoice stage
    # (rev29c: strengthened via `_pipeline_is_dry_mode`).
    # Primary signal: `_pipeline_is_dry_mode`. Fallback: the actual
    # `qoyod_invoice_id` sentinel or the local `is_dry` flag.
    _is_dry_invoice = (
        _pipeline_is_dry_mode
        or is_dry
        or (isinstance(qoyod_invoice_id, str)
            and qoyod_invoice_id.startswith(("DRY:", "PREVIEW:"))))
    p = transition(from_stage="PRODUCT_RESOLVED",
                   to_stage="INVOICE_CREATED", actor="worker",
                   note=("DRY-RUN: invoice payload built, no POST"
                         if _is_dry_invoice
                         else f"invoice {qoyod_invoice_number} created"))
    p.setdefault("$set", {}).update({
        "qoyod_invoice_id":     qoyod_invoice_id,
        "qoyod_invoice_number": qoyod_invoice_number,
        "dry_run":              is_dry,
    })
    # rev29 — Atomic CAS on PRODUCT_RESOLVED → INVOICE_CREATED. This
    # is the most critical CAS in the pipeline because a duplicate
    # transition here in a LIVE tenant would produce TWO Qoyod invoices.
    try:
        await _apply_atomic(
            db, row["id"], p,
            expected_from_stage="PRODUCT_RESOLVED")
    except _StaleStageError as e:
        logger.warning(
            "rev29 invoice_created_stale row_id=%s trace_id=%s %s",
            row.get("id"), trace_id, e)
        return {
            "row_id":   row["id"],
            "outcome":  "STALE_STAGE_ABORT",
            "reason":   "row_stage_changed_before_invoice_created_write",
            "expected": "PRODUCT_RESOLVED",
            "actual":   e.actual,
            "trace_id": trace_id,
        }

    # Mirror to qoyod_invoices ledger (idempotent upsert).
    await db.qoyod_invoices.update_one(
        {"user_id": user_id, "salla_order_id": canonical.get("order_id")},
        {"$set": {
            "schema_version":      1,
            "user_id":             user_id,
            "trace_id":            trace_id,
            "salla_order_id":      canonical.get("order_id"),
            "salla_order_number":  canonical.get("order_number"),
            "salla_order_status":  canonical.get("order_status_native"),
            "qoyod_invoice_id":    qoyod_invoice_id,
            "qoyod_invoice_number":qoyod_invoice_number,
            "qoyod_customer_id":   qoyod_customer_id,
            "customer_label":      (canonical.get("customer") or {}).get("name"),
            "total_amount":        canonical.get("total_amount"),
            "tax_amount":          canonical.get("tax_amount"),
            "items_count":         len(canonical.get("items") or []),
            "payment_method":      canonical.get("payment_method"),
            "pipeline_stage":      "INVOICE_CREATED",
            "status":              ("sent" if not is_dry else "pending"),
            "dry_run":             is_dry,
            "updated_at":          _now(),
         },
         "$setOnInsert": {"id": uuid.uuid4().hex, "created_at": _now()}},
        upsert=True,
    )

    # ── Iter-293.4-rev7 — Post-create total verification ────────────
    # Production order 269571122 (2026-XX) revealed that قيود's
    # server-side total can differ from Salla's by 0.01 SAR when the
    # line-level discount rounds differently (قيود rounds discount to
    # 2 decimals BEFORE applying VAT; Mezan's simulator used 4-decimal
    # discounts). After ZATCA wiring, any mismatch (even one halala)
    # MUST stop the pipeline pending accountant review — auto-creating
    # a receipt would lock-in the wrong total in the books.
    #
    # Read the قيود-actual total from the response (best-effort across
    # several shape variants), compare with Salla's, persist the
    # comparison, and transition the row to INVOICE_CREATED_TOTAL_MISMATCH
    # if the difference exceeds 0.005 SAR. NO further pipeline work.
    if not is_dry and qoyod_invoice_id:
        salla_total_for_verify = canonical.get("total_amount")
        mezan_expected_for_verify = (invoice_diagnostics or {}).get(
            "mezan_expected_total")
        qoyod_actual_total = None
        for src in (
            inv_resp_raw,
            (inv_resp_raw.get("invoice")
             if isinstance(inv_resp_raw, dict) else None),
        ):
            if not isinstance(src, dict):
                continue
            for k in ("total", "total_amount", "balance", "grand_total"):
                if src.get(k) is not None:
                    try:
                        qoyod_actual_total = float(src[k])
                    except (TypeError, ValueError):
                        qoyod_actual_total = None
                    if qoyod_actual_total is not None:
                        break
            if qoyod_actual_total is not None:
                break

        try:
            diff_value = (
                None if (qoyod_actual_total is None
                         or salla_total_for_verify is None)
                else round(float(qoyod_actual_total)
                           - float(salla_total_for_verify), 4)
            )
        except (TypeError, ValueError):
            diff_value = None

        totals_comparison = {
            "salla_total":           salla_total_for_verify,
            "mezan_expected_total":  mezan_expected_for_verify,
            "qoyod_actual_total":    qoyod_actual_total,
            "difference":            diff_value,
            # Iter-293.4-rev8 — Tri-state rounding policy.
            #
            #   |diff| <= 0.005       → no warning (essentially zero).
            #   0.005 < |diff| <= 0.01 → ACCEPTED rounding gap.
            #                            Pipeline continues; row carries a
            #                            permanent `rounding_warning=True`
            #                            flag and lands at
            #                            COMPLETED_WITH_ROUNDING_WARNING.
            #   |diff| > 0.01         → BLOCKER. Pipeline halts at
            #                            INVOICE_CREATED_TOTAL_MISMATCH.
            #                            Accountant must review.
            "warning_tolerance_sar": 0.005,    # below = no warning
            "blocker_tolerance_sar": 0.01,     # above = blocker
            "rounding_warning":      (
                diff_value is not None
                and abs(diff_value) > 0.005
                and abs(diff_value) <= 0.01),
            "mismatch":              (diff_value is not None
                                      and abs(diff_value) > 0.01),
            "reason":                ("qoyod_server_side_rounding"
                                      if (diff_value is not None
                                          and abs(diff_value) > 0.005)
                                      else None),
            "checked_at":            _now(),
        }
        # Persist the comparison so the diagnostic endpoint surfaces
        # it even if the row never re-enters the pipeline.
        await db.integration_inbox.update_one(
            {"id": row["id"]},
            {"$set": {"totals_comparison": totals_comparison}})
        await db.qoyod_invoices.update_one(
            {"user_id":        user_id,
             "salla_order_id": canonical.get("order_id")},
            {"$set": {"totals_comparison": totals_comparison}})

        if totals_comparison["mismatch"]:
            # Transition the inbox row INVOICE_CREATED →
            # INVOICE_CREATED_TOTAL_MISMATCH and STOP.
            mismatch_error = {
                "code":    "qoyod_actual_total_mismatch",
                "message": (
                    "الفاتورة تم إنشاؤها في قيود لكن إجمالي قيود الفعلي "
                    f"({qoyod_actual_total}) يختلف عن Salla "
                    f"({salla_total_for_verify}) بمقدار "
                    f"{diff_value:+.2f} SAR. تم إيقاف السجل عند "
                    "INVOICE_CREATED_TOTAL_MISMATCH — لا سند قبض، "
                    "لا إكمال تلقائي. يلزم قرار محاسبي."
                ),
                "salla_total":         salla_total_for_verify,
                "mezan_expected_total": mezan_expected_for_verify,
                "qoyod_actual_total":   qoyod_actual_total,
                "difference":           diff_value,
                "qoyod_invoice_id":     qoyod_invoice_id,
                "qoyod_invoice_number": qoyod_invoice_number,
            }
            try:
                p_mm = transition(
                    from_stage="INVOICE_CREATED",
                    to_stage="INVOICE_CREATED_TOTAL_MISMATCH",
                    actor="worker",
                    error=mismatch_error,
                )
                p_mm.setdefault("$set", {})["pipeline_error"] = mismatch_error
                await _apply(db, row["id"], p_mm)
            except InvalidTransition as _exc:    # pragma: no cover
                logger.warning(
                    "INVOICE_CREATED → INVOICE_CREATED_TOTAL_MISMATCH "
                    "refused by state machine: %s", _exc)
            # Mirror the mismatch state to the ledger so an external
            # dashboard / report can filter on it.
            await db.qoyod_invoices.update_one(
                {"user_id":        user_id,
                 "salla_order_id": canonical.get("order_id")},
                {"$set": {
                    "pipeline_stage": "INVOICE_CREATED_TOTAL_MISMATCH",
                    "last_error":     mismatch_error,
                    "updated_at":     _now(),
                }})
            logger.warning(
                "QOYOD_ACTUAL_TOTAL_MISMATCH order=%s trace=%s "
                "qoyod_invoice_id=%s diff=%s SAR — pipeline halted.",
                canonical.get("order_number") or canonical.get("order_id"),
                trace_id, qoyod_invoice_id, diff_value,
            )
            return {
                "row_id":            row["id"],
                "outcome":           "INVOICE_CREATED_TOTAL_MISMATCH",
                "reason":            "qoyod_actual_total_mismatch",
                "qoyod_invoice_id":  qoyod_invoice_id,
                "qoyod_invoice_number": qoyod_invoice_number,
                "totals_comparison": totals_comparison,
                "trace_id":          trace_id,
                "note":              ("لا يتم إنشاء سند قبض ولا إكمال "
                                       "تلقائي للسجل عند فروقات الإجمالي. "
                                       "يلزم قرار محاسبي يدوي."),
            }
        # else: totals match. Fall through to the normal posting-mode
        # branch below.

    # ── 4d INVOICE PAYMENT (Iter-290h — replaces standalone Receipt) ──
    #
    # Why this exists
    # ───────────────
    # The previous flow called `POST /receipts` which produced a
    # STANDALONE Qoyod receipt — the invoice balance was never closed
    # and the receipt sat in Qoyod's "غير مستعمل" (unallocated) list.
    # The correct Qoyod flow per `apidoc.qoyod.com` is `POST
    # /invoice_payments` which registers the payment ON the invoice.
    #
    # New stage flow
    # ──────────────
    #     INVOICE_CREATED
    #     → PAYMENT_METHOD_MAPPING_MISSING (pre-POST guard)
    #     → PAYMENT_LINK_FAILED            (Qoyod 4xx/5xx)
    #     → INVOICE_PAYMENT_CREATED        (happy path)
    #     → COMPLETED
    #
    # No fallback to /receipts. Per user spec — "إذا فشل ربط السند
    # بالفاتورة، لا تسجل الطلب كناجح".

    # ── Iter-293.4-rev6 — posting_mode FIRST (BEFORE auto_receipt) ──
    # Critical ordering fix: the payment-method posting_mode is an
    # ACCOUNTING decision baked into the order itself. COD orders
    # MUST always post as `credit_invoice_only` (invoice only, no
    # invoice_payment) regardless of whether the operator has
    # `auto_receipt` enabled. Previously the auto_receipt guard fired
    # BEFORE posting_mode resolution, so COD orders with
    # `auto_receipt=False` got stuck at INVOICE_CREATED — invoice
    # was created in Qoyod but the row never reached COMPLETED.
    # Production order 269571122 hit this on 2026-XX-XX.
    from .payment_methods import (
        resolve_posting_mode,
        POSTING_MODE_CREDIT_INVOICE_ONLY,
        POSTING_MODE_DISABLED,
    )
    pm_for_mode = (canonical.get("payment_method")
                   or canonical.get("payment_method_native"))
    _posting_mode = resolve_posting_mode(settings, pm_for_mode)

    if _posting_mode == POSTING_MODE_CREDIT_INVOICE_ONLY:
        # COD path — invoice exists in Qoyod, no receipt. Mark row + invoice
        # as COMPLETED so the monitor doesn't think the payment step
        # failed. qoyod_invoice_payment_id stays NULL by design.
        #
        # Iter-293.4-rev8 — Honour the rounding-warning flag from the
        # post-create verification above. When |diff| in (0.005, 0.01]
        # the row lands at `COMPLETED_WITH_ROUNDING_WARNING` instead
        # of plain `COMPLETED`, with the totals_comparison preserved
        # so a daily report can list orders that carry the warning.
        _tc = (locals().get("totals_comparison") or {}) if not is_dry else {}
        _rounding_warning = bool(_tc.get("rounding_warning"))
        _final_stage = ("COMPLETED_WITH_ROUNDING_WARNING"
                        if _rounding_warning else "COMPLETED")
        _ledger_status = ("completed_with_rounding_warning"
                          if _rounding_warning else "sent")
        await db.qoyod_invoices.update_one(
            {"user_id": user_id, "salla_order_id": canonical.get("order_id")},
            {"$set": {
                "status":                _ledger_status,
                "pipeline_stage":        _final_stage,
                "posting_mode":          _posting_mode,
                "qoyod_invoice_payment_id": None,
                "qoyod_receipt_id":      None,
                "paid_amount":           0,
                "remaining_amount":      canonical.get("total_amount"),
                "rounding_warning":      _rounding_warning,
                "updated_at":            _now(),
            }})
        p = transition(from_stage="INVOICE_CREATED",
                       to_stage=_final_stage, actor="worker",
                       note=(
                           "credit_invoice_only — COD posted as credit "
                           "invoice, no receipt "
                           + (f"(rounding gap {_tc.get('difference'):+.2f} "
                              "SAR accepted as warning)"
                              if _rounding_warning else
                              "(correct accounting)")),
                       existing_started_at=started_at)
        p.setdefault("$set", {})["posting_mode"] = _posting_mode
        if _rounding_warning:
            p["$set"]["rounding_warning"] = True
        # rev29 — Atomic CAS INVOICE_CREATED → _final_stage (COMPLETED
        # or COMPLETED_WITH_ROUNDING_WARNING). Prevents duplicate
        # COMPLETED transitions for the same row.
        try:
            await _apply_atomic(
                db, row["id"], p,
                expected_from_stage="INVOICE_CREATED")
        except _StaleStageError as e:
            logger.warning(
                "rev29 completed_cod_stale row_id=%s trace_id=%s %s",
                row.get("id"), trace_id, e)
            return {"row_id":  row["id"],
                    "outcome": "STALE_STAGE_ABORT",
                    "reason":  "row_stage_changed_before_completed_write",
                    "expected": "INVOICE_CREATED",
                    "actual":  e.actual,
                    "trace_id": trace_id}
        return {"row_id":               row["id"],
                "outcome":              _final_stage,
                "reason":               ("credit_invoice_only_with_rounding_warning"
                                          if _rounding_warning
                                          else "credit_invoice_only"),
                "posting_mode":         _posting_mode,
                "qoyod_invoice_id":     qoyod_invoice_id,
                "qoyod_invoice_payment_id": None,
                "totals_comparison":    _tc or None,
                "rounding_warning":     _rounding_warning,
                "dry_run":              is_dry}

    if _posting_mode == POSTING_MODE_DISABLED:
        # rev30 — Payment method is intentionally not synced. Persist
        # a definitive `COMPLETED_INVOICE_ONLY` terminal stage on the
        # inbox row so the row isn't silently stuck at INVOICE_CREATED
        # (the exact silence the user reported on prod trace
        # `4dc65ba6...`). Also stamp payment-stage blocker fields the
        # diagnostics surfaces below.
        await db.qoyod_invoices.update_one(
            {"user_id": user_id, "salla_order_id": canonical.get("order_id")},
            {"$set": {"posting_mode": _posting_mode,
                      "pipeline_stage": "COMPLETED_INVOICE_ONLY",
                      "updated_at": _now()}})
        p_dis = transition(
            from_stage="INVOICE_CREATED",
            to_stage="COMPLETED_INVOICE_ONLY", actor="worker",
            note=(
                "posting_mode=disabled — invoice created, no "
                "invoice_payment by design"
                if not is_dry else
                "DRY-RUN: posting_mode=disabled — invoice payload built, "
                "no invoice_payment payload"),
            existing_started_at=started_at,
        )
        p_dis.setdefault("$set", {}).update({
            "posting_mode":                     _posting_mode,
            "payment_stage_blocker_code":       "posting_mode_disabled",
            "payment_stage_blocker_reason":     (
                f"payment_method_mapping.posting_mode is 'disabled' for "
                f"{pm_for_mode!r}; invoice was created but no "
                f"invoice_payment step will run."),
            "payment_stage_expected":           False,
            "invoice_payment_required_for_method":
                _is_pm_expecting_payment(pm_for_mode),
        })
        try:
            await _apply_atomic(
                db, row["id"], p_dis,
                expected_from_stage="INVOICE_CREATED")
        except _StaleStageError as e:
            logger.warning(
                "rev30 completed_invoice_only_stale row_id=%s trace_id=%s %s",
                row.get("id"), trace_id, e)
        return {"row_id": row["id"], "outcome": "COMPLETED_INVOICE_ONLY",
                "reason": "posting_mode_disabled",
                "posting_mode": _posting_mode,
                "qoyod_invoice_id": qoyod_invoice_id, "dry_run": is_dry}

    # auto_receipt / create_receipts capability gate (runs AFTER
    # posting_mode so it only governs pre-paid methods that would
    # normally build an invoice_payment).
    if not (settings.get("auto_receipt", True)
            and (settings.get("capabilities") or {}).get("create_receipts", True)):
        # rev30 — Invoice-payment step disabled by tenant capability.
        # Same reasoning as posting_mode=disabled: land the row at a
        # definitive terminal stage so it doesn't sit silently at
        # INVOICE_CREATED.
        p_cap = transition(
            from_stage="INVOICE_CREATED",
            to_stage="COMPLETED_INVOICE_ONLY", actor="worker",
            note=(
                "auto_receipt=false / create_receipts=false — "
                "invoice created, no invoice_payment by capability"
                if not is_dry else
                "DRY-RUN: auto_receipt=false / create_receipts=false — "
                "invoice payload built, no invoice_payment payload"),
            existing_started_at=started_at,
        )
        p_cap.setdefault("$set", {}).update({
            "posting_mode":                     _posting_mode,
            "payment_stage_blocker_code":       (
                "invoice_payment_disabled_by_capability"),
            "payment_stage_blocker_reason":     (
                "settings.auto_receipt=false OR "
                "settings.capabilities.create_receipts=false; the "
                "operator disabled the invoice_payment step. Invoice "
                "was created; no payment payload was built."),
            "payment_stage_expected":           False,
            "invoice_payment_required_for_method":
                _is_pm_expecting_payment(pm_for_mode),
        })
        try:
            await _apply_atomic(
                db, row["id"], p_cap,
                expected_from_stage="INVOICE_CREATED")
        except _StaleStageError as e:
            logger.warning(
                "rev30 completed_invoice_only_cap_stale row_id=%s trace_id=%s %s",
                row.get("id"), trace_id, e)
        return {"row_id": row["id"],
                "outcome": "COMPLETED_INVOICE_ONLY",
                "reason": "invoice_payment_disabled_by_capability",
                "posting_mode": _posting_mode,
                "dry_run": is_dry,
                "qoyod_invoice_id": qoyod_invoice_id}

    # rev26 — SAS guard before payment payload build (final defense).
    try:
        await _assert_sas_not_rejected(db, row["id"])
    except _StaleStageError as e:
        logger.warning(
            "sas_reject_guard_at_payment_build row_id=%s trace_id=%s %s",
            row.get("id"), trace_id, e)
        return {
            "row_id":   row["id"],
            "outcome":  "STALE_STAGE_ABORT",
            "reason":   "sas_gate_rejected_row_before_payment_build",
            "detail":   str(e),
            "trace_id": trace_id,
        }
    payment_payload, idem_fingerprint = build_invoice_payment_payload(
        qoyod_invoice_id=qoyod_invoice_id,
        dto_dict=canonical, invoice_date=inv_date, settings=settings)

    await db.integration_inbox.update_one(
        {"id": row["id"]},
        {"$set": {"qoyod_payloads.invoice_payment": payment_payload,
                  "qoyod_payloads.invoice_payment_fingerprint": idem_fingerprint,
                  "qoyod_payloads.invoice_payment_snapshot_at": _now()}},
    )

    # ── Pre-POST guard 1: payment account mapping must be set ──────
    # Iter-290h.6 — wire field is `account_id` (not `account`).
    if payment_payload["invoice_payment"].get("account_id") is None:
        err = {
            "code":            "payment_method_mapping_missing",
            "failed_at_stage": "PAYMENT_METHOD_MAPPING_MISSING",
            "salla_payment_method": (canonical.get("payment_method")
                                     or canonical.get("payment_method_native")),
            "message": (
                "لم يتم ضبط Qoyod payment_method_id لطريقة الدفع "
                f"'{canonical.get('payment_method')}' في الإعدادات. "
                "افتح إعدادات قيود ← طرق الدفع، وضبط حساب قيود "
                "لهذه الطريقة قبل إعادة المحاولة."
            ),
        }
        await db.integration_inbox.update_one(
            {"id": row["id"]},
            {"$set": {"qoyod_responses.invoice_payment.error": err,
                      "qoyod_responses.invoice_payment.received_at": _now()}})
        p = transition(from_stage="INVOICE_CREATED",
                       to_stage="PAYMENT_METHOD_MAPPING_MISSING",
                       actor="worker", error=err)
        p.setdefault("$set", {})["pipeline_error"] = err
        await _apply(db, row["id"], p)
        p2 = transition(from_stage="PAYMENT_METHOD_MAPPING_MISSING",
                        to_stage="PARTIAL_FAILURE", actor="worker",
                        note="invoice in Qoyod · payment_method mapping needed",
                        existing_started_at=started_at)
        await _apply(db, row["id"], p2)
        await db.qoyod_invoices.update_one(
            {"user_id": user_id, "salla_order_id": canonical.get("order_id")},
            {"$set": {"status": "invoice_sent_payment_method_missing",
                      "pipeline_stage": "PARTIAL_FAILURE",
                      "last_error": err, "updated_at": _now()}})
        return {"row_id": row["id"], "outcome": "PARTIAL_FAILURE",
                "reason": "PAYMENT_METHOD_MAPPING_MISSING",
                "qoyod_invoice_id": qoyod_invoice_id}

    # ── Pre-POST guard 2: DB-side idempotency on the fingerprint ─────
    # Per user spec — `order_id + invoice_id + payment_method + amount`.
    # If a matching row already exists in `qoyod_invoice_payments` with
    # a real Qoyod id, short-circuit straight to COMPLETED instead of
    # double-posting.
    existing_payment = await db.qoyod_invoice_payments.find_one({
        "user_id":          user_id,
        "salla_order_id":   idem_fingerprint["order_id"],
        "qoyod_invoice_id": idem_fingerprint["qoyod_invoice_id"],
        "payment_method":   idem_fingerprint["payment_method"],
        "amount":           idem_fingerprint["amount"],
    }, {"_id": 0, "qoyod_invoice_payment_id": 1})
    qoyod_invoice_payment_id: Optional[str] = None
    payment_resp_raw: Any = None
    payment_started_ms = int(_now().timestamp() * 1000)

    if existing_payment and existing_payment.get("qoyod_invoice_payment_id"):
        # Already posted in a previous run — reuse the id.
        qoyod_invoice_payment_id = str(existing_payment["qoyod_invoice_payment_id"])
        await db.integration_inbox.update_one(
            {"id": row["id"]},
            {"$set": {
                "qoyod_responses.invoice_payment.idempotent_short_circuit": True,
                "qoyod_responses.invoice_payment.qoyod_id": qoyod_invoice_payment_id,
            }})
    else:
        # ── Iter-001k — Selective Send guard for payment step ──────
        # Same rules as invoice site: skip when the legacy write-lock
        # path would fire anyway (keeps LOCKED_AWAITING_APPROVAL for
        # the pure write-lock case).
        payment_decision = selective_send_decision
        if payment_decision is None and not is_dry \
                and not _writes_blocked(api_client, settings):
            policy_order_pay = _build_policy_order_from_pipeline_scope(
                row=row, canonical=canonical,
                qoyod_customer_id=qoyod_customer_id,
                products_resolution=prod_res,
                invoice_diagnostics=None,
                is_dry=is_dry,
            )
            # Iter-001k — At the payment site, the existing invoice id
            # is EXPECTED (that's what we're paying against). Strip it
            # from the policy order so the `already_sent` check (which
            # is intended to prevent DUPLICATE invoice creation) does
            # not misfire on the payment path.
            policy_order_pay["existing_qoyod_invoice_id"] = None
            # Iter-001k — same per-order unlock resolution as the
            # invoice site (see comment there).
            _policy_settings_pay = dict(settings)
            _policy_settings_pay["production_writes_locked"] = \
                _writes_blocked(api_client, settings)
            # Iter-2026-02.rev20 — mirror of the invoice-site fix so
            # payment isn't refused by the OLD selective_send_policy
            # after the new auto-gate approved the row.
            if _sas_gate_passed:
                _policy_settings_pay["selective_live_send_enabled"] = True
                _policy_settings_pay["dry_run_mode"] = False
            try:
                payment_decision = assert_send_allowed(
                    order=policy_order_pay,
                    settings=_policy_settings_pay)
            except SelectiveSendPolicyBlocked as blocked:
                await db.integration_inbox.update_one(
                    {"id": row["id"]},
                    {"$set": {
                        "qoyod_payloads.invoice_payment_selective_blocked_payload":
                            payment_payload,
                        "qoyod_payloads.invoice_payment_selective_blocked_at":
                            _now(),
                        "pipeline_stage":
                            f"SELECTIVE_SEND_BLOCKED:"
                            f"{blocked.blocker_code}",
                        "selective_send_blocker_code":
                            blocked.blocker_code,
                        "selective_send_blocker_reason":
                            blocked.blocker_reason,
                        "selective_send_blocked_step":
                            "invoice_payment",
                        "selective_send_blocked_at": _now(),
                    }})
                return {
                    "row_id":         row["id"],
                    "outcome":        "SELECTIVE_SEND_BLOCKED",
                    "reason":         blocked.blocker_code,
                    "blocker_reason": blocked.blocker_reason,
                    "step":           "invoice_payment",
                    "qoyod_invoice_id": qoyod_invoice_id,
                    "trace_id":       trace_id,
                }
        # Stamp payment payload dates from the (invoice or fresh)
        # decision so invoice + payment share `send_date_riyadh`.
        if payment_decision is not None:
            payment_payload = apply_send_date_to_qoyod_payload(
                payment_payload, payment_decision)

        # ── Iter-293.4 — Global Write Lock pre-check for invoice_payment ─
        # Mirror the pre-check on the create_invoice path (line 652)
        # so the operator sees a clean LOCKED_AWAITING_APPROVAL outcome
        # instead of an exception bubbling up from api_client._request.
        # The API client itself enforces the lock as a safety net.
        if _writes_blocked(api_client, settings):
            # Iter-293.4-rev2 — Persist to audit log so /admin/write-lock-report
            # surfaces this attempt (previously invisible to the report).
            payment_idem = (
                f"mzn-{trace_id}-invoice-payment-{idem_fingerprint['qoyod_invoice_id']}")
            attempt_id = await record_blocked_attempt(
                db, user_id=user_id, action="create_invoice_payment",
                method="POST", path="/invoice_payments",
                payload=payment_payload,
                idempotency_key=payment_idem,
            )
            await db.integration_inbox.update_one(
                {"id": row["id"]},
                {"$set": {
                    "qoyod_payloads.invoice_payment_locked_payload": payment_payload,
                    "qoyod_payloads.invoice_payment_locked_at":      _now(),
                    "pipeline_stage":                                "LOCKED_AWAITING_APPROVAL",
                    "lock_reason":                                   "production_writes_locked",
                    "lock_step":                                     "invoice_payment",
                    "lock_attempt_id":                               attempt_id,
                }},
            )
            return {
                "row_id":     row["id"],
                "outcome":    "LOCKED_AWAITING_APPROVAL",
                "reason":     "production_writes_locked",
                "step":       "invoice_payment",
                "attempt_id": attempt_id,
                "qoyod_invoice_id": qoyod_invoice_id,
                "trace_id":   trace_id,
                "note":       ("Production writes are locked. The "
                               "invoice was already created in Qoyod; "
                               "the invoice_payment step has been parked "
                               "for explicit approval."),
            }

        payment_idem = (
            f"mzn-{trace_id}-invoice-payment-{idem_fingerprint['qoyod_invoice_id']}")
        # rev32 — Final pre-POST guard #4: before create_invoice_payment.
        # Same 8-condition check as create_invoice. Failing here after
        # an invoice was already created means the invoice remains in
        # قيود but the payment link is refused — the row is dead-
        # lettered so the operator sees the divergence in one place.
        # Skipped when `_pipeline_is_dry_mode` is True (fake HTTP-free
        # client OR settings.dry_run_mode=True) — see create_invoice
        # comment for rationale.
        if not is_dry and not _pipeline_is_dry_mode:
            pm_for_guard = (canonical.get("payment_method")
                            or canonical.get("payment_method_native"))
            try:
                await _rev32_assert_final_write_permitted(
                    db, row["id"],
                    action="create_invoice_payment",
                    payment_method=pm_for_guard,
                    user_id=user_id,
                )
            except Rev32Violation as _v:
                logger.error(
                    "rev32 create_invoice_payment_blocked row_id=%s "
                    "trace_id=%s violation_type=%s reason=%s",
                    row.get("id"), trace_id, _v.violation_type,
                    _v.reason)
                await db.integration_inbox.update_one(
                    {"id": row["id"]},
                    {"$set": {
                        "qoyod_payloads.invoice_payment_rev32_blocked_payload":
                            payment_payload,
                        "qoyod_payloads.invoice_payment_rev32_blocked_at":
                            _now(),
                    }})
                p_rv = transition(
                    from_stage="INVOICE_CREATED",
                    to_stage="PAYMENT_LINK_FAILED",
                    actor="worker",
                    error={
                        "code":           "rev32_guard_blocked",
                        "violation_type": _v.violation_type,
                        "message":        _v.reason,
                        "evidence":       _v.evidence,
                    })
                p_rv.setdefault("$set", {})["pipeline_error"] = {
                    "code":           "rev32_guard_blocked",
                    "violation_type": _v.violation_type,
                    "message":        _v.reason,
                }
                await _apply(db, row["id"], p_rv)
                p_rv2 = transition(
                    from_stage="PAYMENT_LINK_FAILED",
                    to_stage="PARTIAL_FAILURE", actor="worker",
                    note=(f"rev32 guard blocked invoice_payment: "
                          f"{_v.violation_type}"),
                    existing_started_at=started_at)
                await _apply(db, row["id"], p_rv2)
                return {
                    "row_id":         row["id"],
                    "outcome":        "REV32_BLOCKED",
                    "reason":         _v.violation_type,
                    "violation_type": _v.violation_type,
                    "step":           "create_invoice_payment",
                    "qoyod_invoice_id": qoyod_invoice_id,
                    "trace_id":       trace_id,
                }
        try:
            payment_resp = await api_client.create_invoice_payment(
                payment_payload, idem=payment_idem)
            payment_resp_raw = payment_resp
            if isinstance(payment_resp, dict):
                r = (payment_resp.get("invoice_payment")
                     if isinstance(payment_resp.get("invoice_payment"), dict)
                     else payment_resp)
                qoyod_invoice_payment_id = (
                    str(r.get("id")) if r.get("id") is not None else None)
        except QoyodWriteLockedError as exc:
            # Safety net — should be unreachable thanks to the pre-check
            # above, but if the lock was toggled to True mid-request the
            # api_client raised this. Persist locked snapshot + surface
            # cleanly.
            await db.integration_inbox.update_one(
                {"id": row["id"]},
                {"$set": {
                    "qoyod_payloads.invoice_payment_locked_payload": payment_payload,
                    "qoyod_payloads.invoice_payment_locked_at":      _now(),
                    "pipeline_stage":                                "LOCKED_AWAITING_APPROVAL",
                    "lock_reason":                                   "production_writes_locked",
                    "lock_step":                                     "invoice_payment",
                    "lock_attempt_id":                               exc.attempt_id,
                }})
            return {"row_id": row["id"],
                    "outcome": "LOCKED_AWAITING_APPROVAL",
                    "reason": "production_writes_locked",
                    "step":   "invoice_payment",
                    "attempt_id": exc.attempt_id,
                    "qoyod_invoice_id": qoyod_invoice_id}
        except QoyodAPIError as exc:
            err_log = exc.to_log_dict()
            err_log["request_body_json"] = payment_payload
            await db.integration_inbox.update_one(
                {"id": row["id"]},
                {"$set": {
                    "qoyod_responses.invoice_payment.error":      err_log,
                    "qoyod_responses.invoice_payment.received_at": _now(),
                    "qoyod_responses.invoice_payment.duration_ms":
                        int(_now().timestamp() * 1000) - payment_started_ms,
                }})
            # Partial failure! Invoice exists, payment-link does not.
            p = transition(from_stage="INVOICE_CREATED",
                           to_stage="PAYMENT_LINK_FAILED", actor="worker",
                           error=err_log)
            p.setdefault("$set", {})["pipeline_error"] = err_log
            await _apply(db, row["id"], p)
            p2 = transition(from_stage="PAYMENT_LINK_FAILED",
                            to_stage="PARTIAL_FAILURE", actor="worker",
                            note="invoice in Qoyod · payment_link failed · review",
                            existing_started_at=started_at)
            await _apply(db, row["id"], p2)
            await db.qoyod_invoices.update_one(
                {"user_id": user_id, "salla_order_id": canonical.get("order_id")},
                {"$set": {"status": "invoice_sent_payment_link_failed",
                          "pipeline_stage": "PARTIAL_FAILURE",
                          "last_error": err_log, "updated_at": _now()}})
            return {"row_id": row["id"], "outcome": "PARTIAL_FAILURE",
                    "reason": "PAYMENT_LINK_FAILED",
                    "qoyod_invoice_id": qoyod_invoice_id}

    # ── Happy path: payment linked ──────────────────────────────────
    # Persist into qoyod_invoice_payments ledger (DB-side idempotency
    # store + audit). Upsert on the fingerprint tuple.
    await db.qoyod_invoice_payments.update_one(
        {
            "user_id":          user_id,
            "salla_order_id":   idem_fingerprint["order_id"],
            "qoyod_invoice_id": idem_fingerprint["qoyod_invoice_id"],
            "payment_method":   idem_fingerprint["payment_method"],
            "amount":           idem_fingerprint["amount"],
        },
        {"$set": {
            "user_id":                   user_id,
            "trace_id":                  trace_id,
            "salla_order_id":            idem_fingerprint["order_id"],
            "salla_order_number":        canonical.get("order_number"),
            "qoyod_invoice_id":          idem_fingerprint["qoyod_invoice_id"],
            "qoyod_invoice_payment_id":  qoyod_invoice_payment_id,
            "payment_method":            idem_fingerprint["payment_method"],
            "payment_method_id":         idem_fingerprint["payment_method_id"],
            "amount":                    idem_fingerprint["amount"],
            "currency":                  canonical.get("currency") or "SAR",
            "dry_run":                   is_dry,
            "updated_at":                _now(),
        },
         "$setOnInsert": {"id": uuid.uuid4().hex, "created_at": _now()}},
        upsert=True,
    )

    p = transition(from_stage="INVOICE_CREATED",
                   to_stage="INVOICE_PAYMENT_CREATED", actor="worker",
                   note=("DRY-RUN: invoice_payment payload built, no POST"
                         if is_dry else "invoice_payment recorded ON invoice in Qoyod"))
    p.setdefault("$set", {})["qoyod_invoice_payment_id"] = qoyod_invoice_payment_id
    # Iter-290h.6 — Clear stale failure breadcrumbs from any previous
    # attempt on this row. Without this the monitor's
    # `_status_for_invoice_payment_step` keeps reporting the step as
    # "failed" because `last_failed_stage=PAYMENT_LINK_FAILED` is left
    # over from the first (truly failed) attempt, AND the drawer
    # shows BOTH a stale `error` and the fresh `body` under the same
    # step. Once the payment landed on قيود the prior failure is no
    # longer the source of truth.
    p["$set"]["last_failed_stage"] = None
    p["$set"]["pipeline_error"]    = None
    # rev29 — Atomic CAS INVOICE_CREATED → INVOICE_PAYMENT_CREATED.
    # In LIVE mode this transition marks the row as having received
    # a successful Qoyod invoice_payment POST — duplicating it would
    # be catastrophic.
    try:
        await _apply_atomic(
            db, row["id"], p,
            expected_from_stage="INVOICE_CREATED")
    except _StaleStageError as e:
        logger.warning(
            "rev29 invoice_payment_created_stale row_id=%s trace_id=%s %s",
            row.get("id"), trace_id, e)
        return {"row_id":  row["id"],
                "outcome": "STALE_STAGE_ABORT",
                "reason":  "row_stage_changed_before_invoice_payment_write",
                "expected": "INVOICE_CREATED",
                "actual":  e.actual,
                "trace_id": trace_id}
    # Persist raw response — First-Sync-Monitor.
    await db.integration_inbox.update_one(
        {"id": row["id"]},
        {"$set": {
            "qoyod_responses.invoice_payment.body":        payment_resp_raw,
            "qoyod_responses.invoice_payment.received_at": _now(),
            "qoyod_responses.invoice_payment.duration_ms":
                int(_now().timestamp() * 1000) - payment_started_ms,
            "qoyod_responses.invoice_payment.qoyod_id":    qoyod_invoice_payment_id,
            # Iter-290h.6 — explicitly clear the stale error so the
            # drawer doesn't mix the old 422 response with the fresh
            # success body.
            "qoyod_responses.invoice_payment.error":       None,
        }})

    p = transition(from_stage="INVOICE_PAYMENT_CREATED", to_stage="COMPLETED",
                   actor="worker",
                   note=("DRY-RUN COMPLETED — no Qoyod POSTs were made"
                         if is_dry else "invoice + invoice_payment pushed to Qoyod"),
                   existing_started_at=started_at)
    # rev29 — Atomic CAS on final COMPLETED transition.
    try:
        await _apply_atomic(
            db, row["id"], p,
            expected_from_stage="INVOICE_PAYMENT_CREATED")
    except _StaleStageError as e:
        logger.warning(
            "rev29 completed_final_stale row_id=%s trace_id=%s %s",
            row.get("id"), trace_id, e)
        return {"row_id":  row["id"],
                "outcome": "STALE_STAGE_ABORT",
                "reason":  "row_stage_changed_before_completed_write",
                "expected": "INVOICE_PAYMENT_CREATED",
                "actual":  e.actual,
                "trace_id": trace_id}
    await db.qoyod_invoices.update_one(
        {"user_id": user_id, "salla_order_id": canonical.get("order_id")},
        {"$set": {"qoyod_invoice_payment_id": qoyod_invoice_payment_id,
                  "pipeline_stage":  "COMPLETED",
                  "status":          ("sent" if not is_dry else "pending"),
                  "sent_at":         _now() if not is_dry else None,
                  "updated_at":      _now()}})

    return {"row_id": row["id"], "outcome": "COMPLETED",
            "dry_run": is_dry,
            "qoyod_invoice_id":         qoyod_invoice_id,
            "qoyod_invoice_payment_id": qoyod_invoice_payment_id}


async def process_pending_customer_resolved(
    db, user_id: str = "main", *, limit: int = 25, api_client=None,
) -> dict:
    cursor = db.integration_inbox.find(
        {"user_id": user_id, "pipeline_stage": "CUSTOMER_RESOLVED"},
        sort=[("received_at", 1)], limit=max(1, min(limit, 100)),
    )
    rows = []
    async for r in cursor:
        rows.append(r)
    counters = {"completed": 0, "partial_failure": 0, "dead_letter": 0,
                "invoice_only": 0}
    items: list[dict] = []
    for row in rows:
        out = await process_customer_resolved_row(db, row, api_client=api_client)
        items.append(out)
        oc = out.get("outcome")
        if oc == "COMPLETED":
            counters["completed"] += 1
        elif oc == "PARTIAL_FAILURE":
            counters["partial_failure"] += 1
        elif oc == "DEAD_LETTER":
            counters["dead_letter"] += 1
        elif oc == "INVOICE_CREATED":
            counters["invoice_only"] += 1
    return {"ok": True, "processed": len(items), "counts": counters,
            "items": items}


# ─── Day 4 Report (read-only aggregation) ───────────────────────────
async def day4_report(db, user_id: str) -> dict:
    """Aggregates eligibility outcomes across all `integration_inbox` rows
    for the tenant — answers "how did Day 4 rules + customer resolution
    play out so far?". Used by the dashboard card."""
    pipeline = [
        {"$match": {"user_id": user_id}},
        {"$group": {"_id": "$pipeline_stage", "n": {"$sum": 1}}},
    ]
    by_stage = {}
    async for d in db.integration_inbox.aggregate(pipeline):
        by_stage[d["_id"]] = d["n"]

    # Detail buckets
    skipped_reasons = {}
    async for d in db.integration_inbox.aggregate([
        {"$match": {"user_id": user_id, "pipeline_stage": "SKIPPED"}},
        {"$group": {"_id": "$business_rules_decision.reason", "n": {"$sum": 1}}},
    ]):
        skipped_reasons[d["_id"] or "unknown"] = d["n"]

    dead_letter_by_stage = {}
    async for d in db.integration_inbox.aggregate([
        {"$match": {"user_id": user_id, "pipeline_stage": "DEAD_LETTER"}},
        {"$group": {"_id": "$last_failed_stage", "n": {"$sum": 1}}},
    ]):
        dead_letter_by_stage[d["_id"] or "unknown"] = d["n"]

    return {
        "schema_version": 1,
        "generated_at":   _now(),
        "by_stage":       by_stage,
        "skipped_reasons": skipped_reasons,
        "dead_letter_by_stage": dead_letter_by_stage,
        "totals": {
            "normalized":          by_stage.get("NORMALIZED", 0),
            "customer_resolved":   by_stage.get("CUSTOMER_RESOLVED", 0),
            "skipped":             by_stage.get("SKIPPED", 0),
            "dead_letter":         by_stage.get("DEAD_LETTER", 0),
            "partial_failure":     by_stage.get("PARTIAL_FAILURE", 0),
            "completed":           by_stage.get("COMPLETED", 0),
        },
    }


async def process_pending_normalized(
    db, user_id: str = "main", *,
    limit: int = 25,
    api_client=None,
) -> dict:
    """Drain up to `limit` NORMALIZED rows for the tenant.

    Sequential (not parallel) — Day 4 is a manual / observed run; we
    keep it sequential so a single failure doesn't stampede the log.
    Day 5 introduces a proper background worker with concurrency.
    """
    cursor = db.integration_inbox.find(
        {"user_id": user_id, "pipeline_stage": "NORMALIZED"},
        sort=[("received_at", 1)],
        limit=max(1, min(limit, 100)),
    )
    rows = []
    async for r in cursor:
        rows.append(r)

    results: list[dict] = []
    counters = {"customer_resolved": 0, "skipped": 0, "dead_letter": 0}
    for row in rows:
        out = await process_normalized_row(db, row, api_client=api_client)
        results.append(out)
        oc = out.get("outcome")
        if oc == "CUSTOMER_RESOLVED":
            counters["customer_resolved"] += 1
        elif oc == "SKIPPED":
            counters["skipped"] += 1
        elif oc == "DEAD_LETTER":
            counters["dead_letter"] += 1
    return {
        "ok": True,
        "processed": len(results),
        "counts": counters,
        "items": results,
    }
