"""rev44 — Skip classification (user decree 2026-07-07).

Forensics proof (prod): status/payment-scope SKIPPEDs permanently
locked completed orders. FINAL RULE:
  • transient  — temporary operational scope (transitional status,
                 payment-method scope) → does NOT block the order;
                 resumable via audited one-shot.
  • fatal      — DRY / DEAD_LETTER / real duplicate invoice / outside
                 the 2026-07-01 integration floor / cancelled-refunded
                 by policy / structural failure → stays absolutely
                 terminal.
Unclassified (legacy rows, unknown reasons) → FATAL (fail-closed).
New flow only — existing rows are NEVER touched.
"""
from __future__ import annotations

TRANSIENT = "transient"
FATAL = "fatal"

# Statuses that are FINAL business outcomes — never resumable.
CANCELLED_LIKE_STATUSES = frozenset({
    "cancelled", "canceled", "ملغي", "restored", "مسترجع",
    "restoring", "قيد الاسترجاع", "refunded", "مرتجع",
})

# Temporary operational scope reasons (SAS gate / business rules /
# canary scope) — the order itself is still commercially valid.
TRANSIENT_SKIP_REASONS = frozenset({
    "status_not_in_allow_list",
    "status_hard_blocked",
    "not_in_trigger_statuses",
    "payment_method_not_in_allow_list",
    "payment_method_hard_blocked",
    "payment_method_mapping_missing",
    "canary_scope_skip_pm_not_in_allowlist",
    # rev47 — audited manual recovery hold: a DEAD_LETTER row that was
    # falsely vetoed (rev33 SKIPPED-history veto on a transient skip)
    # is parked back at SKIPPED so the worker never auto-sends it; the
    # ONLY resume path is the explicit operator canary one-shot.
    "dead_letter_false_veto_recovery_hold",
})


def classify_skip(reason: str, *, status_native: str | None = None,
                  status_canon: str | None = None) -> str:
    """ONE classification rule for every SKIPPED write site."""
    statuses = {str(status_native or "").strip(),
                str(status_canon or "").strip()}
    if statuses & CANCELLED_LIKE_STATUSES:
        return FATAL
    if str(reason or "").strip() in TRANSIENT_SKIP_REASONS:
        return TRANSIENT
    return FATAL


def stamp_skip_class(patch: dict, *, reason: str, row: dict) -> dict:
    """Stamp skip_class/skip_class_reason into a transition patch."""
    canonical = row.get("canonical_payload") or {}
    patch.setdefault("$set", {}).update({
        "skip_class": classify_skip(
            reason,
            status_native=canonical.get("order_status_native"),
            status_canon=canonical.get("order_status")),
        "skip_class_reason": str(reason or ""),
    })
    return patch
