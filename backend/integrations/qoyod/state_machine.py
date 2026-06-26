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
    "RECEIPT_CREATED",      # 4d — receipt exists in Qoyod
    "COMPLETED",            # terminal success
)

# Side-stages — non-failure terminal or transient.
SKIPPED:   str = "SKIPPED"     # business rule said: do not send
RETRYING:  str = "RETRYING"    # transient between failure and resume

# Failure stages — exactly one per pipeline step where work happens.
FAILURE_STAGES: tuple[str, ...] = (
    "FAILED_VALIDATION",    # validation step rejected the payload
    "FAILED_NORMALIZATION", # normalization step couldn't build the DTO
    "FAILED_CUSTOMER",      # 4a couldn't resolve/create customer
    "FAILED_PRODUCT",       # 4b couldn't resolve/create products
    "FAILED_INVOICE",       # 4c invoice POST to Qoyod failed
    "FAILED_RECEIPT",       # 4d receipt POST failed (invoice succeeded)
    "DEAD_LETTER",          # terminal failure — max attempts exhausted
)

# The full canonical set (used for Pydantic Literal validation).
ALL_STAGES: tuple[str, ...] = HAPPY_PATH + (SKIPPED, RETRYING) + FAILURE_STAGES

TERMINAL_STAGES: frozenset[str] = frozenset({"COMPLETED", "SKIPPED", "DEAD_LETTER"})

# Map each failure stage back to the happy-path stage we resume from
# when the row transitions through RETRYING. This is the ONLY
# encoding of "what does retry mean" — keeps it diff-able in one place.
FAILURE_TO_RESUME: dict[str, str] = {
    "FAILED_VALIDATION":    "RECEIVED",     # re-run validation
    "FAILED_NORMALIZATION": "VALIDATED",    # re-run normalization
    "FAILED_CUSTOMER":      "RULES_APPLIED",
    "FAILED_PRODUCT":       "CUSTOMER_RESOLVED",
    "FAILED_INVOICE":       "PRODUCT_RESOLVED",
    "FAILED_RECEIPT":       "INVOICE_CREATED",
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
