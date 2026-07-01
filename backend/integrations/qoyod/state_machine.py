"""Qoyod Pipeline State Machine — Pre-Day 3 Refinement.

Encapsulates the canonical state vocabulary, the allowed transitions,
and the stage-history append logic for both:

  • `integration_inbox` rows (the running pipeline state).
  • `qoyod_invoices`     rows (the audit record per Salla order).

Design notes (per user directive on Pre-Day 3 review):

1. States are **UPPERCASE** and treated as opaque tokens. Each happy-
   path stage has a single forward successor. Failure stages branch
   sideways. Retries flow `FAILED_* → RETRYING → <stage_before_fail>`.
2. Movement is strictly through `transition()` so every change is
   recorded in `stage_history[]`. No direct writes to `pipeline_stage`
   outside this module.
3. `RETRYING` is transient — a row should never sit in it for more
   than the time it takes the worker to resume the failed stage.
4. `DEAD_LETTER` is terminal. Manual operator action only.
5. ALL functions are pure — they return a Mongo `$set/$push` patch.
   The caller decides when to write. This keeps the state machine
   trivially unit-testable (no DB needed).

ADR-001 compliance:
   #4  Canonical Domain    — single source of truth for stage tokens.
   #8  Event Driven        — append-only `stage_history`.
   #10 Idempotency         — transitions are idempotent (no-op if
                              `from_stage == to_stage`).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional


# ─────────────────────────────────────────────────────────────────────
# Canonical state vocabulary
# ─────────────────────────────────────────────────────────────────────
# Happy-path stages (strictly forward). Each one names the *result*
# of completing a pipeline step — never the work-in-progress.
HAPPY_PATH: tuple[str, ...] = (
    "NEW",                  # row materialised in inbox, nothing else
    "RECEIVED",             # raw payload stored + headers captured
    "VALIDATED",            # signature/structure/token checks passed
    "NORMALIZED",           # canonical SalesOrder DTO built
    "RULES_APPLIED",        # business rules decided: send / skip
    "CUSTOMER_RESOLVED",    # 4a — Qoyod customer id available
    "PRODUCT_RESOLVED",     # 4b — Qoyod product ids available
    "INVOICE_CREATED",      # 4c — invoice exists in Qoyod
    # Iter-290h — Renamed step. Was "RECEIPT_CREATED" (we used to call
    # POST /receipts which produced a STANDALONE Qoyod receipt that
    # never closed the invoice, leaving it "unallocated"). New flow
    # calls POST /invoice_payments which records the payment ON the
    # invoice — the only way to land balance=0. The legacy
    # "RECEIPT_CREATED" stage stays in ALL_STAGES + TERMINAL eligible
    # transitions purely for back-compat reading of historical rows.
    "INVOICE_PAYMENT_CREATED",
    "COMPLETED",            # terminal success
)

# Iter-290h — deprecated legacy stage. New code path never writes this;
# kept here so historic rows can still be enumerated and so the
# state machine accepts the transition `RECEIPT_CREATED → COMPLETED`
# for any in-flight rows during the deploy window.
LEGACY_RECEIPT_CREATED: str = "RECEIPT_CREATED"

# Side-stages — non-failure terminal or transient.
SKIPPED:   str = "SKIPPED"     # business rule said: do not send
RETRYING:  str = "RETRYING"    # transient between failure and resume
PARTIAL_FAILURE: str = "PARTIAL_FAILURE"   # invoice OK but receipt failed
NEEDS_ENRICHMENT: str = "NEEDS_ENRICHMENT"  # waiting for Salla-API enricher
                                            # (toggle: enrichment_fallback_enabled)

# Failure stages — exactly one per pipeline step where work happens.
FAILURE_STAGES: tuple[str, ...] = (
    "FAILED_VALIDATION",    # validation step rejected the payload
    "FAILED_NORMALIZATION", # normalization step couldn't build the DTO
    "FAILED_ENRICHMENT",    # Salla-API enricher exhausted its retries
    "FAILED_CUSTOMER",      # 4a couldn't resolve/create customer
    "FAILED_PRODUCT",       # 4b couldn't resolve/create products
    "FAILED_INVOICE",       # 4c invoice POST to Qoyod failed
    # Iter-290h — Replaces "FAILED_RECEIPT" in the new flow. Triggered
    # when POST /invoice_payments fails AFTER the invoice was created.
    # The invoice is already in Qoyod (with a non-zero balance) so we
    # land in PARTIAL_FAILURE so the operator can review/retry the
    # payment link without re-creating the invoice.
    "PAYMENT_LINK_FAILED",
    # Iter-290h — Pre-POST guard. Triggered when the operator hasn't
    # configured `payment_method_accounts[<salla_method>]` (the Qoyod
    # payment_method_id for this Salla payment method, e.g. "mada"
    # → 17). The invoice still lives in Qoyod; the row halts here so
    # the operator can map the method in Settings and retry.
    "PAYMENT_METHOD_MAPPING_MISSING",
    # Iter-289 legacy — kept for back-compat with rows already failed
    # under the previous /receipts flow. New code never writes this.
    "FAILED_RECEIPT",
    "DEAD_LETTER",          # terminal failure — max attempts exhausted
)

# The full canonical set (used for Pydantic Literal validation).
ALL_STAGES: tuple[str, ...] = HAPPY_PATH + (
    SKIPPED, RETRYING, PARTIAL_FAILURE, NEEDS_ENRICHMENT, LEGACY_RECEIPT_CREATED,
    # Iter-293.4-rev3 — Global Write Lock hold stage.
    "LOCKED_AWAITING_APPROVAL",
    # Iter-293.4-rev7 — Post-create total mismatch hold stage.
    # The قيود invoice exists but its server-computed total disagrees
    # with Salla's by more than 0.005 SAR. The row halts here pending
    # accountant decision — NO auto-receipt, NO auto-completion.
    "INVOICE_CREATED_TOTAL_MISMATCH",
    # Iter-293.4-rev8 — Terminal "completed with rounding warning".
    # 0 < |salla_total - qoyod_actual_total| <= 0.01 SAR.
    # This is an ACCEPTABLE rounding gap caused by قيود's server-side
    # 2-decimal discount rounding (RCA Iter-293.4-rev8). Treated as
    # SUCCESS (no blocker) but the row carries a permanent warning
    # flag for audit + reporting.
    "COMPLETED_WITH_ROUNDING_WARNING",
    # Iter-293.5 — Selective Live Send Gate HOLD stages.
    # Rows land here when the automated gate refuses to grant a
    # scoped bypass; each stage maps to a category on the
    # /integrations/qoyod/pending-orders operator UI.
    "HOLD_UNSUPPORTED_PAYMENT_METHOD",  # payment method not on allowlist
    "HOLD_COD_PENDING_FIX",             # COD but posting_mode ≠ credit_invoice_only
    "UNRESOLVED_QOYOD_DEPENDENCY",      # sendable=false or DRY/PREVIEW/null
    "ORDER_SUPERSEDED_BY_NEWER_EVENT",  # newer Salla event supersedes this trace
    "STALE_TRACE_NOT_CURRENT_ORDER_STATE",
    # Iter-293.5-rev2 — POST-invoice hold for bank_transfer.
    # The invoice IS created in قيود (ZATCA-critical) but the
    # invoice_payment / receipt is DEFERRED until Iter-294 delivers
    # the receiving-bank routing so we never book to a legacy
    # generic-bank account.
    "BANK_TRANSFER_PAYMENT_ROUTING_PENDING",
) + FAILURE_STAGES

TERMINAL_STAGES: frozenset[str] = frozenset({
    "COMPLETED", "SKIPPED", "DEAD_LETTER", "PARTIAL_FAILURE",
    # Iter-293.4-rev8 — Terminal success with a known-acceptable
    # rounding gap (<= 0.01 SAR caused by قيود server-side rounding).
    "COMPLETED_WITH_ROUNDING_WARNING",
})

# Map each failure stage back to the happy-path stage we resume from
# when the row transitions through RETRYING. This is the ONLY
# encoding of "what does retry mean" — keeps it diff-able in one place.
FAILURE_TO_RESUME: dict[str, str] = {
    "FAILED_VALIDATION":    "RECEIVED",     # re-run validation
    "FAILED_NORMALIZATION": "VALIDATED",    # re-run normalization
    "FAILED_ENRICHMENT":    "RECEIVED",     # re-attempt Salla enricher
    "FAILED_CUSTOMER":      "RULES_APPLIED",
    "FAILED_PRODUCT":       "CUSTOMER_RESOLVED",
    "FAILED_INVOICE":       "PRODUCT_RESOLVED",
    "FAILED_RECEIPT":       "INVOICE_CREATED",
    # Iter-290h — new failure stages both resume from INVOICE_CREATED
    # (the invoice already exists in Qoyod; only the payment-link step
    # needs to be re-attempted).
    "PAYMENT_LINK_FAILED":            "INVOICE_CREATED",
    "PAYMENT_METHOD_MAPPING_MISSING": "INVOICE_CREATED",
}


def _next_happy(stage: str) -> Optional[str]:
    """Return the immediate next happy-path stage, or None if terminal."""
    try:
        i = HAPPY_PATH.index(stage)
    except ValueError:
        return None
    if i + 1 < len(HAPPY_PATH):
        return HAPPY_PATH[i + 1]
    return None


def _build_allowed() -> set[tuple[str, str]]:
    """Compute the full set of allowed (from, to) transitions."""
    allowed: set[tuple[str, str]] = set()

    # Happy-path strict forward edges.
    for i in range(len(HAPPY_PATH) - 1):
        allowed.add((HAPPY_PATH[i], HAPPY_PATH[i + 1]))

    # Any pre-terminal happy-path stage can be SKIPPED by a business
    # rule. SKIPPED is terminal — no edge out of it.
    for stage in HAPPY_PATH[:-1]:
        allowed.add((stage, SKIPPED))

    # Each pipeline step that can fail has a one-step edge to its
    # corresponding failure stage. The "from" is the predecessor
    # of the happy successor (i.e. the work happens BETWEEN those
    # two stages, and the failure is recorded against the failure
    # itself, not the predecessor).
    for fail_stage, resume_from in FAILURE_TO_RESUME.items():
        # The work-in-progress happens between `resume_from` and its
        # next stage. Failure is reachable from `resume_from`.
        allowed.add((resume_from, fail_stage))

    # FAILED_RECEIPT is special: invoice already exists. From the
    # happy `INVOICE_CREATED` (one before receipt) we can fail to
    # FAILED_RECEIPT. Already encoded above via FAILURE_TO_RESUME.

    # Failure → RETRYING (operator or worker pushes back into the
    # pipeline). RETRYING → resume_from (computed in transition()).
    for fail_stage, resume_from in FAILURE_TO_RESUME.items():
        allowed.add((fail_stage, RETRYING))
        allowed.add((RETRYING, resume_from))

    # Failure → DEAD_LETTER (max attempts reached / operator decision).
    for fail_stage in FAILURE_TO_RESUME:
        allowed.add((fail_stage, "DEAD_LETTER"))

    # ─── Totals Guard (Iter-273, 2026-02-27) ──────────────────────────
    # The pipeline runs a `validate_totals` check immediately after
    # building the canonical DTO (stage NORMALIZED) and BEFORE any
    # Qoyod-bound side-effects. A mismatch (e.g. Make.com truncated
    # items[] like in Production order 268670571) terminates the row
    # via FAILED_VALIDATION → DEAD_LETTER. No auto-retry — the fix
    # lives upstream (Make or Salla), not in our pipeline.
    allowed.add(("NORMALIZED", "FAILED_VALIDATION"))

    # FAILED_RECEIPT has a special partial-success route. The invoice
    # already exists in Qoyod; only the receipt POST failed. Going to
    # DEAD_LETTER would imply we lost everything — but we didn't.
    # PARTIAL_FAILURE preserves that nuance for the operator.
    allowed.add(("FAILED_RECEIPT", "PARTIAL_FAILURE"))
    # Iter-290h — Mirror the same nuance for the two new failure
    # stages. The invoice exists in Qoyod, only the payment-link step
    # missed.
    allowed.add(("PAYMENT_LINK_FAILED", "PARTIAL_FAILURE"))
    allowed.add(("PAYMENT_METHOD_MAPPING_MISSING", "PARTIAL_FAILURE"))

    # Iter-290h — Legacy `RECEIPT_CREATED` stage is no longer in
    # HAPPY_PATH but historic rows that already reached it during the
    # old /receipts flow must still be able to land on COMPLETED.
    allowed.add((LEGACY_RECEIPT_CREATED, "COMPLETED"))
    # INVOICE_CREATED → RECEIPT_CREATED is preserved for the same
    # back-compat reason (rows in flight when the deploy lands).
    allowed.add(("INVOICE_CREATED", LEGACY_RECEIPT_CREATED))

    # ─── NEEDS_ENRICHMENT (transient) ─────────────────────────────────
    # When Legacy Adapter detects an items-missing payload AND the
    # `enrichment_fallback_enabled` toggle is on, the row enters
    # NEEDS_ENRICHMENT from RECEIVED. The (separately implemented)
    # enricher resolves it to either VALIDATED (success) or
    # FAILED_ENRICHMENT (give up). DEAD_LETTER stays reachable as the
    # terminal manual override.
    allowed.add(("RECEIVED", NEEDS_ENRICHMENT))
    allowed.add((NEEDS_ENRICHMENT, "VALIDATED"))
    allowed.add((NEEDS_ENRICHMENT, "FAILED_ENRICHMENT"))
    allowed.add((NEEDS_ENRICHMENT, "DEAD_LETTER"))

    # ─── Auto-Requeue (2026-02-27) ────────────────────────────────────
    # Operator/worker-driven recovery path for DEAD_LETTER /
    # PARTIAL_FAILURE rows whose error matches a `KNOWN_FIXED_PATTERNS`
    # entry. The row hops DEAD_LETTER → RETRYING → NORMALIZED (the only
    # stage the background worker drains) so the full pipeline replays
    # against the now-fixed code. Restricted to specific edges so a
    # generic DEAD_LETTER row cannot be smuggled back into the pipeline.
    allowed.add(("DEAD_LETTER",     RETRYING))
    allowed.add(("PARTIAL_FAILURE", RETRYING))
    allowed.add((RETRYING, "NORMALIZED"))
    allowed.add((RETRYING, "CUSTOMER_RESOLVED"))
    # Iter-290h — Operator-driven retry of the payment-link step.
    # PAYMENT_LINK_FAILED / PAYMENT_METHOD_MAPPING_MISSING resume from
    # INVOICE_CREATED (the invoice already exists; only the
    # invoice_payment POST needs to be re-attempted).
    allowed.add((RETRYING, "INVOICE_CREATED"))

    # ─── Iter-293.4-rev3 — LOCKED_AWAITING_APPROVAL hold + release ────
    # The Global Write Lock parks rows in LOCKED_AWAITING_APPROVAL
    # at the create_invoice and create_invoice_payment pre-checks.
    # Operator-driven release via one-shot-reprocess hops:
    #   LOCKED_AWAITING_APPROVAL → RETRYING → NORMALIZED
    # (rebuild from scratch — no stale locked_payload reuse).
    # Pipeline → LOCKED edges: from PRODUCT_RESOLVED (invoice step)
    # and from INVOICE_CREATED (invoice_payment step).
    allowed.add(("PRODUCT_RESOLVED",        "LOCKED_AWAITING_APPROVAL"))
    allowed.add(("INVOICE_CREATED",         "LOCKED_AWAITING_APPROVAL"))
    allowed.add(("LOCKED_AWAITING_APPROVAL", RETRYING))
    allowed.add(("LOCKED_AWAITING_APPROVAL", "DEAD_LETTER"))

    # ─── Iter-293.4-rev5 — COD credit_invoice_only direct completion ──
    # COD (cash-on-delivery) orders post the invoice as a CREDIT
    # invoice — they intentionally SKIP the invoice_payment step
    # because the money is collected later by the courier. The
    # pipeline's posting_mode resolver (`credit_invoice_only`)
    # transitions the row directly from INVOICE_CREATED to COMPLETED
    # without ever creating an invoice_payment. The state machine
    # must permit this edge OR the row crashes with InvalidTransition
    # the moment per-order approval unlocks a COD invoice.
    #
    # Note: this is COD-specific accounting. Pre-paid methods (mada,
    # apple_pay, etc.) still flow through the full
    # INVOICE_CREATED → INVOICE_PAYMENT_CREATED → COMPLETED path.
    allowed.add(("INVOICE_CREATED", "COMPLETED"))

    # ─── Iter-293.4-rev7 — INVOICE_CREATED_TOTAL_MISMATCH (post-create) ─
    # After a successful POST /invoices, the pipeline reads the
    # قيود-computed total from the response and compares it with
    # Salla's total_amount. If they differ by more than 0.01 SAR,
    # the row transitions to INVOICE_CREATED_TOTAL_MISMATCH and STOPS.
    # NO invoice_payment, NO receipt, NO completion. The invoice
    # exists in قيود — only the local audit trail reflects the
    # discrepancy so an accountant can review.
    #
    # Edges:
    #   INVOICE_CREATED → INVOICE_CREATED_TOTAL_MISMATCH  (pipeline writes)
    #   INVOICE_CREATED_TOTAL_MISMATCH → COMPLETED         (operator: reconciled)
    #   INVOICE_CREATED_TOTAL_MISMATCH → COMPLETED_WITH_ROUNDING_WARNING
    #                                                      (operator: small-gap
    #                                                       accepted)
    #   INVOICE_CREATED_TOTAL_MISMATCH → DEAD_LETTER       (operator: void in قيود)
    allowed.add(("INVOICE_CREATED", "INVOICE_CREATED_TOTAL_MISMATCH"))
    allowed.add(("INVOICE_CREATED_TOTAL_MISMATCH", "COMPLETED"))
    allowed.add(("INVOICE_CREATED_TOTAL_MISMATCH",
                 "COMPLETED_WITH_ROUNDING_WARNING"))
    allowed.add(("INVOICE_CREATED_TOTAL_MISMATCH", "DEAD_LETTER"))

    # ─── Iter-293.4-rev8 — COMPLETED_WITH_ROUNDING_WARNING edges ──────
    # Reachable from:
    #   • INVOICE_CREATED — COD/credit_invoice_only flow when diff in
    #     (0.005, 0.01] (warning-grade).
    #   • INVOICE_PAYMENT_CREATED — pre-paid flow when diff in the
    #     warning band after the payment-link step.
    #   • INVOICE_CREATED_TOTAL_MISMATCH — operator explicitly
    #     reconciled / accepted the small gap (covered above).
    # Terminal — no edges out.
    allowed.add(("INVOICE_CREATED", "COMPLETED_WITH_ROUNDING_WARNING"))
    allowed.add(("INVOICE_PAYMENT_CREATED",
                 "COMPLETED_WITH_ROUNDING_WARNING"))

    # ─── Iter-293.5 — Selective Live Send Gate HOLD edges ────────────
    # Rows reach these stages from RULES_APPLIED / PRODUCT_RESOLVED /
    # CUSTOMER_RESOLVED whenever the gate refuses a bypass. All HOLD
    # stages can climb back to RETRYING when the operator resolves the
    # blocker (adopt product / customer, wait for Iter-294, override
    # payment method), and can escalate to DEAD_LETTER for manual
    # dismissal. Not terminal — but they carry no outgoing
    # auto-send edges either.
    for _from in ("RULES_APPLIED", "PRODUCT_RESOLVED", "CUSTOMER_RESOLVED",
                  "NORMALIZED"):
        for _hold in ("HOLD_UNSUPPORTED_PAYMENT_METHOD",
                      "HOLD_COD_PENDING_FIX",
                      "UNRESOLVED_QOYOD_DEPENDENCY",
                      "ORDER_SUPERSEDED_BY_NEWER_EVENT",
                      "STALE_TRACE_NOT_CURRENT_ORDER_STATE"):
            allowed.add((_from, _hold))
    for _hold in ("HOLD_UNSUPPORTED_PAYMENT_METHOD",
                  "HOLD_COD_PENDING_FIX",
                  "UNRESOLVED_QOYOD_DEPENDENCY"):
        allowed.add((_hold, RETRYING))
        allowed.add((_hold, "DEAD_LETTER"))

    # ─── Iter-293.5-rev2 — bank_transfer POST-invoice hold ───────────
    # The invoice is created FIRST (ZATCA), then the row parks at
    # BANK_TRANSFER_PAYMENT_ROUTING_PENDING waiting for Iter-294's
    # bank-routing to build the invoice_payment against the correct
    # receiving bank. Two exit edges:
    #   → COMPLETED    once Iter-294 books the payment successfully.
    #   → DEAD_LETTER  operator overrides / voids.
    allowed.add(("INVOICE_CREATED", "BANK_TRANSFER_PAYMENT_ROUTING_PENDING"))
    allowed.add(("BANK_TRANSFER_PAYMENT_ROUTING_PENDING", "COMPLETED"))
    allowed.add(("BANK_TRANSFER_PAYMENT_ROUTING_PENDING", "DEAD_LETTER"))

    return allowed


ALLOWED_TRANSITIONS: frozenset[tuple[str, str]] = frozenset(_build_allowed())


# ─────────────────────────────────────────────────────────────────────
# Public helpers
# ─────────────────────────────────────────────────────────────────────
def _now() -> datetime:
    return datetime.now(timezone.utc)


def is_valid_stage(stage: str) -> bool:
    return stage in ALL_STAGES


def can_transition(from_stage: str, to_stage: str) -> bool:
    """True if (from → to) is allowed by the state machine.

    Idempotent self-loops (`from == to`) are NEVER allowed — callers
    that want a no-op should simply not call `transition()`.
    """
    return (from_stage, to_stage) in ALLOWED_TRANSITIONS


def resume_target(failed_stage: str) -> str:
    """For a row currently in RETRYING, where should it land next?
    Looks up the stage to resume the failed step from.

    Raises ValueError if `failed_stage` isn't a known failure stage.
    """
    if failed_stage not in FAILURE_TO_RESUME:
        raise ValueError(f"unknown failure stage: {failed_stage!r}")
    return FAILURE_TO_RESUME[failed_stage]


class InvalidTransition(ValueError):
    """Raised by transition() when the (from, to) pair isn't allowed."""


def transition(
    *,
    from_stage: str,
    to_stage: str,
    actor: str = "system",
    note: Optional[str] = None,
    error: Optional[dict] = None,
    existing_started_at: Optional[datetime] = None,
) -> dict[str, Any]:
    """Return a Mongo update document that performs the transition.

    The caller writes the result with `db.collection.update_one(filter, patch)`.

    Audit Trail fields (Pre-Day 3 spec, expanded):
        • `pipeline_started_at`   — set once on NEW → RECEIVED.
        • `pipeline_finished_at`  — set on entry to any terminal stage
                                    (COMPLETED, SKIPPED, DEAD_LETTER).
        • `pipeline_duration_ms`  — computed when `existing_started_at`
                                    is provided AND we are entering a
                                    terminal stage.
        • `last_success_stage`    — updated when transitioning INTO any
                                    happy-path stage (NEW excluded).
        • `last_failed_stage`     — updated when transitioning INTO any
                                    FAILED_* stage.

    Caller responsibility: pass `existing_started_at` from the current
    row when entering a terminal stage so duration can be computed
    server-side in a single round-trip.

    Result shape:
        {
          "$set":  {"pipeline_stage": <to_stage>, "updated_at": <utc>, ...},
          "$push": {"stage_history": <history-entry>},
          "$inc":  {"attempts": 1}  # only for retries
        }

    Raises:
        InvalidTransition: if (from_stage, to_stage) isn't allowed.
    """
    if not can_transition(from_stage, to_stage):
        raise InvalidTransition(
            f"transition not allowed: {from_stage!r} → {to_stage!r}"
        )

    now = _now()
    entry: dict[str, Any] = {
        "from_stage": from_stage,
        "to_stage":   to_stage,
        "at":         now,
        "actor":      actor,
    }
    if note:
        entry["note"] = note
    if error:
        # Defensive copy + cap large strings so we never bloat the row.
        entry["error"] = {
            k: (v[:500] if isinstance(v, str) else v)
            for k, v in error.items()
        }

    set_block: dict[str, Any] = {
        "pipeline_stage": to_stage,
        "updated_at":     now,
    }

    # ─── Audit Trail bookkeeping ────────────────────────────────────
    # Mark pipeline start exactly once — first hop out of NEW.
    if from_stage == "NEW" and to_stage == "RECEIVED":
        set_block["pipeline_started_at"] = now

    # Mark pipeline finish on entry to terminal stages.
    if to_stage in TERMINAL_STAGES:
        set_block["pipeline_finished_at"] = now
        set_block["pipeline_outcome"]     = to_stage
        if existing_started_at is not None:
            try:
                delta = now - existing_started_at
                set_block["pipeline_duration_ms"] = int(
                    delta.total_seconds() * 1000)
            except Exception:
                # Defensive — never break a transition for an arithmetic
                # edge case; just skip the duration.
                pass

    # Track the last successful happy-path stage we reached (excluding
    # NEW because NEW is "row exists", not "work done").
    if to_stage in HAPPY_PATH and to_stage != "NEW":
        set_block["last_success_stage"] = to_stage

    # Track the most recent failure for the operator UI. We deliberately
    # EXCLUDE DEAD_LETTER here because DEAD_LETTER is a catch-all bucket;
    # the meaningful "last failed stage" is the specific FAILED_* hop
    # that the row passed through right before being dead-lettered.
    if to_stage in FAILURE_STAGES and to_stage != "DEAD_LETTER":
        set_block["last_failed_stage"] = to_stage

    patch: dict[str, Any] = {
        "$set":  set_block,
        "$push": {"stage_history": entry},
    }
    # Increment attempt counter only when we re-enter the pipeline
    # (RETRYING → resume_from).
    if from_stage == RETRYING and to_stage in HAPPY_PATH:
        patch["$inc"] = {"attempts": 1}
    return patch


def initial_history_entry(actor: str = "system",
                          note: Optional[str] = None) -> dict[str, Any]:
    """The first stage_history entry written at row creation
    (when there is no `from_stage`). Use this when inserting a brand
    new inbox row in `NEW` state.
    """
    entry: dict[str, Any] = {
        "from_stage": None,
        "to_stage":   "NEW",
        "at":         _now(),
        "actor":      actor,
    }
    if note:
        entry["note"] = note
    return entry
