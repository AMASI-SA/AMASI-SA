"""Qoyod Dead-Letter Auto-Requeue — Self-Healing for KNOWN-FIXED Failures.

Why this exists
───────────────
When a Qoyod-side bug is identified and patched in our code (e.g. the
`contact_name: Can't be blank` issue fixed on 2026-02-26 by sending both
`name` AND `contact_name` in the customer payload), every inbox row
that DEAD-LETTERed against that bug stays red forever — even though
re-running the now-fixed pipeline would succeed.

The user-visible symptom: QYD-GO keeps showing "1 production invoice
failed" indefinitely. The user must NOT have to manually clean these
rows — that defeats SSOT.

Design (strictly bounded per user directive 2026-02-27)
───────────────────────────────────────────────────────
1. The set of "auto-fixable" errors is encoded in `KNOWN_FIXED_PATTERNS`
   below. Generic DEAD_LETTER rows are NEVER touched. Adding a new
   pattern requires explicit code review.
2. Each candidate row must satisfy ALL of:
       • pipeline_stage ∈ {DEAD_LETTER, PARTIAL_FAILURE}
       • some KNOWN_FIXED_PATTERNS entry matches
       • requeue_attempts < MAX_REQUEUE_ATTEMPTS (default 2)
3. Requeue path: DEAD_LETTER → RETRYING → NORMALIZED. The background
   worker picks the row up next tick and drives it back through the
   full pipeline (rules → customer → product → invoice → receipt).
4. State machine guarantees idempotency: if the row is no longer in a
   terminal failure stage, requeue is a no-op.
5. If the re-run fails AGAIN with the same KNOWN_FIXED pattern, the
   second requeue attempt still happens (up to MAX_REQUEUE_ATTEMPTS).
   After that the row STICKS in DEAD_LETTER — the operator must
   investigate manually. The same row is therefore never an infinite
   loop in the worker.

What this module DOES NOT do
────────────────────────────
• It does NOT auto-requeue rows with errors outside the registry.
• It does NOT auto-requeue rows that already exceeded MAX_REQUEUE_ATTEMPTS.
• It does NOT touch `qoyod_invoices`, `qoyod_settings`, or any Qoyod-side
  data. Only the `integration_inbox` row's stage transitions are
  affected (recorded in `stage_history` via the state machine).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from integrations.qoyod.state_machine import transition


logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────
# Pattern registry — add new entries ONLY after a code fix has shipped
# and you want every historical victim of that bug to auto-recover.
# ─────────────────────────────────────────────────────────────────────
def _contact_name_blank_matcher(err: dict | None) -> bool:
    """Match Qoyod's `contact_name: ["Can't be blank"]` validation
    error that was caused by sending only `name` (not `contact_name`)
    in the create-contact payload. Fixed 2026-02-26 by sending both
    fields — see `customer_resolver._build_contact_payload`.

    Defensive: accepts the error in several shapes Qoyod has returned:
        • {"code": "qoyod_validation_error",
           "details": {"contact_name": ["Can't be blank"]}}
        • {"code": "qoyod_api_error",
           "message": "... contact_name ... Can't be blank ..."}
        • Nested under `qoyod_errors` for older log shapes.

    Iter-267 fix: previously used `str(err).lower()` which invokes
    Python's `repr()` on inner string values — that escapes
    apostrophes (`Can't` → `Can\\'t`), making the literal substring
    search for `can't be blank` miss every real production row.
    Switched to `json.dumps(default=str)` which preserves apostrophes
    verbatim (JSON only escapes `"` and `\\`).
    """
    if not isinstance(err, dict):
        return False
    try:
        blob = json.dumps(err, ensure_ascii=False, default=str).lower()
    except (TypeError, ValueError):
        # Last-resort fallback for non-serializable inputs.
        blob = str(err).lower()
    # `contact_name` + `blank` is the unique signature (we deliberately
    # do NOT require the literal `can't` token so locale variants
    # like `cant be blank` or `can not be blank` also match).
    return "contact_name" in blob and "blank" in blob


KNOWN_FIXED_PATTERNS: list[dict[str, Any]] = [
    {
        "id":            "contact_name_blank_2026_02_26",
        "description":   ("Qoyod rejected create-contact because only "
                          "`name` was sent. Fixed on 2026-02-26 by also "
                          "sending `contact_name` (see customer_resolver."
                          "_build_contact_payload)."),
        "applies_to_failed_stages": frozenset({"FAILED_CUSTOMER"}),
        "matcher":       _contact_name_blank_matcher,
        "fixed_at":      "2026-02-26",
    },
]


# ─────────────────────────────────────────────────────────────────────
# Policy guard (user directive 2026-02-27)
# ─────────────────────────────────────────────────────────────────────
# The registry must stay SMALL and reviewed. Auto-requeue must NEVER
# silently hide a real production failure under a generic pattern.
#
# Adding a new entry requires:
#   1. A shipped code fix for the specific Qoyod error.
#   2. A dedicated test in test_qoyod_dead_letter_auto_requeue.py
#      proving (a) the matcher accepts only the fixed shape, and
#      (b) unrelated errors are still untouched.
#   3. Code review explicitly OK'ing the addition (this comment
#      block is the policy anchor — do not relax it lightly).
#
# The invariant below is asserted at import time AND re-asserted by
# `test_known_fixed_patterns_registry_has_contact_name_only` so any
# accidental expansion breaks CI immediately.
_REVIEWED_PATTERN_IDS: frozenset[str] = frozenset({
    "contact_name_blank_2026_02_26",
})
_unreviewed = {p["id"] for p in KNOWN_FIXED_PATTERNS} - _REVIEWED_PATTERN_IDS
assert not _unreviewed, (
    "KNOWN_FIXED_PATTERNS contains unreviewed entries: "
    f"{sorted(_unreviewed)}. Adding a pattern requires updating "
    "_REVIEWED_PATTERN_IDS *and* code review — see policy comment."
)
del _unreviewed


MAX_REQUEUE_ATTEMPTS: int = 2


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────
def _now() -> datetime:
    return datetime.now(timezone.utc)


def _resume_target_for(last_failed_stage: str | None) -> str:
    """Return the happy-path stage the row should re-enter at.

    We hop back to **NORMALIZED** for early-pipeline failures (validation
    / normalization / enrichment / customer) because that's the only
    stage the background worker drains. For mid-pipeline failures
    (product / invoice / receipt) we hop to CUSTOMER_RESOLVED so the
    customer-resolved worker picks the row up.

    Why not use `FAILURE_TO_RESUME` directly? Because resume_from for
    some failure stages (e.g. RULES_APPLIED) is NOT drained by the
    current worker — the row would sit there forever. Mapping to one
    of the two drained stages is the simplest safe choice.
    """
    early_failures = {
        "FAILED_VALIDATION", "FAILED_NORMALIZATION",
        "FAILED_ENRICHMENT", "FAILED_CUSTOMER",
    }
    mid_failures = {
        "FAILED_PRODUCT", "FAILED_INVOICE", "FAILED_RECEIPT",
    }
    if last_failed_stage in early_failures:
        return "NORMALIZED"
    if last_failed_stage in mid_failures:
        return "CUSTOMER_RESOLVED"
    # Default: re-enter from the broadest safe point.
    return "NORMALIZED"


def match_pattern(row: dict) -> dict | None:
    """Return the first KNOWN_FIXED_PATTERNS entry that matches the
    row, or None. A match requires BOTH the failed-stage AND the
    error matcher to accept the row.
    """
    last_failed = row.get("last_failed_stage")
    err = row.get("pipeline_error") or {}
    for pat in KNOWN_FIXED_PATTERNS:
        if last_failed not in pat["applies_to_failed_stages"]:
            continue
        try:
            if pat["matcher"](err):
                return pat
        except Exception:   # defensive — never let a matcher crash us
            logger.exception(
                "qoyod auto-requeue matcher crashed: pattern=%s",
                pat.get("id"))
            continue
    return None


# ─────────────────────────────────────────────────────────────────────
# Core requeue mechanic — single row
# ─────────────────────────────────────────────────────────────────────
async def requeue_row(
    db, row: dict, *, pattern: dict, actor: str = "auto-requeue",
    force: bool = False,
) -> dict:
    """Move a single DEAD_LETTER / PARTIAL_FAILURE row back into the
    pipeline. Two-hop transition recorded in `stage_history`:

        <terminal> → RETRYING → <resume_stage>

    Increments `requeue_attempts` and tags the row with metadata so
    the operator can see in the UI why/when it was requeued. Returns
    `{ok, row_id, resume_stage, requeue_attempts, pattern_id}`.

    `force=True` bypasses MAX_REQUEUE_ATTEMPTS — used ONLY by the
    operator "إعادة فرض المعالجة" path when they've manually verified
    the underlying bug is fixed. Forced requeues are tagged with
    `forced_requeue_at` and `forced_by` in the row for audit.
    """
    row_id = row.get("id")
    if not row_id:
        return {"ok": False, "reason": "row_missing_id"}

    current_stage = row.get("pipeline_stage")
    if current_stage not in ("DEAD_LETTER", "PARTIAL_FAILURE"):
        return {"ok": False, "reason": "row_not_in_terminal_failure",
                "current_stage": current_stage, "row_id": row_id}

    prev_attempts = int(row.get("requeue_attempts") or 0)
    if prev_attempts >= MAX_REQUEUE_ATTEMPTS and not force:
        return {"ok": False, "reason": "max_requeue_attempts_reached",
                "row_id": row_id, "requeue_attempts": prev_attempts,
                "hint": "use force=true to override (operator only)"}

    last_failed = row.get("last_failed_stage")
    resume_stage = _resume_target_for(last_failed)
    pattern_id = pattern.get("id")

    # Hop 1: <terminal> → RETRYING
    forced_tag = " [FORCED]" if force else ""
    note1 = (f"auto-requeue{forced_tag}: pattern={pattern_id} "
             f"last_failed_stage={last_failed}")
    p1 = transition(
        from_stage=current_stage, to_stage="RETRYING",
        actor=actor, note=note1,
    )
    p1.setdefault("$set", {}).update({
        "requeue_attempts": prev_attempts + 1,
        "last_requeue_at":  _now(),
        "last_requeue_pattern": pattern_id,
    })
    if force:
        p1["$set"].update({
            "forced_requeue_at": _now(),
            "forced_by":         actor,
        })
    # Don't clear pipeline_error — keep it for forensics. The
    # `pipeline_stage` change is the visible signal.
    await db.integration_inbox.update_one({"id": row_id}, p1)

    # Hop 2: RETRYING → resume_stage (always a HAPPY_PATH stage the
    # worker drains).
    note2 = (f"auto-requeue resumed at {resume_stage} "
             f"(attempt {prev_attempts + 1}/{MAX_REQUEUE_ATTEMPTS})")
    p2 = transition(
        from_stage="RETRYING", to_stage=resume_stage,
        actor=actor, note=note2,
    )
    # The state machine `transition()` already sets `$inc.attempts += 1`
    # because (RETRYING → HAPPY_PATH) is exactly the retry edge.
    await db.integration_inbox.update_one({"id": row_id}, p2)

    return {
        "ok":               True,
        "row_id":           row_id,
        "trace_id":         row.get("trace_id"),
        "previous_stage":   current_stage,
        "resume_stage":     resume_stage,
        "requeue_attempts": prev_attempts + 1,
        "pattern_id":       pattern_id,
    }


# ─────────────────────────────────────────────────────────────────────
# Discovery + bulk requeue
# ─────────────────────────────────────────────────────────────────────
async def find_requeue_candidates(
    db, *, user_id: str, include_dry_run: bool = False, limit: int = 200,
) -> list[dict]:
    """Return DEAD_LETTER / PARTIAL_FAILURE rows that match a known-fix
    pattern and still have requeue attempts remaining. Read-only;
    perfect for the "preview before requeue" UI.

    `include_dry_run=False` — production rows only by default. Dry-run
    failures are also fixable but the user already has the archive
    cleanup tool for those.
    """
    q: dict = {
        "user_id": user_id,
        "pipeline_stage": {"$in": ["DEAD_LETTER", "PARTIAL_FAILURE"]},
    }
    if not include_dry_run:
        q["dry_run"] = {"$ne": True}

    candidates: list[dict] = []
    cursor = db.integration_inbox.find(q, limit=max(1, min(limit, 500)))
    async for row in cursor:
        pat = match_pattern(row)
        if not pat:
            continue
        attempts = int(row.get("requeue_attempts") or 0)
        if attempts >= MAX_REQUEUE_ATTEMPTS:
            continue
        candidates.append({
            "row_id":            row.get("id"),
            "trace_id":          row.get("trace_id"),
            "pipeline_stage":    row.get("pipeline_stage"),
            "last_failed_stage": row.get("last_failed_stage"),
            "received_at":       row.get("received_at"),
            "requeue_attempts":  attempts,
            "max_requeue_attempts": MAX_REQUEUE_ATTEMPTS,
            "pattern_id":        pat.get("id"),
            "pattern_description": pat.get("description"),
            "pipeline_error":    row.get("pipeline_error"),
            "order_id":          (row.get("canonical_payload") or {}).get(
                                    "order_id"),
            "order_number":      (row.get("canonical_payload") or {}).get(
                                    "order_number"),
            "dry_run":           bool(row.get("dry_run")),
        })
    return candidates


async def auto_requeue_known_fixed(
    db, *, user_id: str, include_dry_run: bool = False,
    actor: str = "auto-requeue", limit: int = 200,
) -> dict:
    """Requeue every candidate row found via `find_requeue_candidates`.

    Used both:
      • by the background worker (every tick — see worker._one_round)
      • by the manual operator button in QYD-GO

    Returns `{ok, scanned, requeued, skipped, failures, items}`.
    """
    q: dict = {
        "user_id": user_id,
        "pipeline_stage": {"$in": ["DEAD_LETTER", "PARTIAL_FAILURE"]},
    }
    if not include_dry_run:
        q["dry_run"] = {"$ne": True}

    items: list[dict] = []
    counters = {"requeued": 0, "skipped_no_pattern": 0,
                "skipped_max_attempts": 0, "skipped_other": 0,
                "failures": 0}
    scanned = 0
    cursor = db.integration_inbox.find(q, limit=max(1, min(limit, 500)))
    async for row in cursor:
        scanned += 1
        pat = match_pattern(row)
        if not pat:
            counters["skipped_no_pattern"] += 1
            continue
        attempts = int(row.get("requeue_attempts") or 0)
        if attempts >= MAX_REQUEUE_ATTEMPTS:
            counters["skipped_max_attempts"] += 1
            continue
        try:
            res = await requeue_row(db, row, pattern=pat, actor=actor)
        except Exception as exc:   # defensive
            logger.exception("auto-requeue failed for row=%s", row.get("id"))
            counters["failures"] += 1
            items.append({"ok": False, "row_id": row.get("id"),
                          "error": f"{exc.__class__.__name__}: {exc}"})
            continue
        items.append(res)
        if res.get("ok"):
            counters["requeued"] += 1
        else:
            counters["skipped_other"] += 1

    return {
        "ok":       True,
        "scanned":  scanned,
        **counters,
        "items":    items,
        "at":       _now().isoformat(),
    }


# ─────────────────────────────────────────────────────────────────────
# Per-row manual requeue — for the "إعادة معالجة فورية" button on a
# single row (UI surface deferred; module-level entry point exposed
# now for future routes/tests).
# ─────────────────────────────────────────────────────────────────────
async def requeue_one(
    db, *, user_id: str, row_id: Optional[str] = None,
    trace_id: Optional[str] = None, actor: str = "operator",
    force: bool = False,
) -> dict:
    """Manually requeue a single row by `row_id` or `trace_id`.
    Still bounded by the pattern registry — generic DEAD_LETTER rows
    cannot be requeued.

    `force=True` bypasses MAX_REQUEUE_ATTEMPTS for the matched row.
    The pattern registry guard still applies — generic errors cannot
    be force-requeued.

    Returns `{ok, result}` on success or `{ok: False, reason}`.
    """
    if not (row_id or trace_id):
        return {"ok": False, "reason": "row_id_or_trace_id_required"}
    q: dict = {"user_id": user_id}
    if row_id:
        q["id"] = row_id
    else:
        q["trace_id"] = trace_id
    row = await db.integration_inbox.find_one(q)
    if not row:
        return {"ok": False, "reason": "row_not_found"}
    pat = match_pattern(row)
    if not pat:
        return {"ok": False, "reason": "no_known_fix_pattern_matches",
                "last_failed_stage": row.get("last_failed_stage"),
                "pipeline_error":    row.get("pipeline_error")}
    res = await requeue_row(db, row, pattern=pat, actor=actor, force=force)
    return {"ok": bool(res.get("ok")), "result": res}
