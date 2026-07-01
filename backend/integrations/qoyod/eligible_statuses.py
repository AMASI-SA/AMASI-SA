"""Unified Salla → قيود Eligible-Status Set — Iter-293.5-rev3.

Source of truth for "which Salla order statuses make a row a
candidate for invoicing in قيود". Historically each layer carried
its own copy:

    • pending_orders (routes.py):    wide set (incl. shipping/processing).
    • business_rules.py:             `["completed"]` fallback only.
    • preflight.py:                  `["completed"]` fallback only.
    • live_send_gate.py G1:          `{completed, delivered, تم التنفيذ}`.

That caused the exact bug reported on order 268307955 (Tabby /
`delivered`): the pending queue surfaced the row as a Candidate, but
preflight rejected it with `status_not_in_triggers`. Now every layer
consults `ELIGIBLE_ORDER_STATUSES` (or the helpers below) so the
answer is identical everywhere.

Tenants MAY narrow the set via `qoyod_settings.invoice_trigger_statuses`
(explicit override). When the setting is missing OR equals the legacy
`["completed"]` sentinel, we widen to `ELIGIBLE_ORDER_STATUSES`.
"""
from __future__ import annotations

from typing import Iterable, Optional


# Canonical English tokens the normalizer emits, PLUS the Arabic
# labels Salla uses on some tenants (Salla emits either language
# depending on merchant locale — we accept both).
ELIGIBLE_ORDER_STATUSES: frozenset[str] = frozenset({
    # English canonicals
    "completed",
    "delivered",
    "shipped",
    "shipping",
    "processing",
    "in_progress",
    "under_delivery",
    # Arabic natives — kept for defensive matching against payloads
    # that arrive before the normalizer canonicalises them.
    "تم التنفيذ",
    "تم التوصيل",
    "تم الشحن",
    "جاري التوصيل",
    "قيد التنفيذ",
    "قيد التوصيل",
})


# Legacy default that older tenants have stored on their settings
# doc. Preserved for reference — the widening logic ONLY kicks in
# when the field is missing/empty; explicit narrowing (including
# `["completed"]`) is honoured.
_LEGACY_COMPLETED_ONLY_DEFAULT: tuple[str, ...] = ("completed",)


def resolve_trigger_statuses(settings: dict) -> list[str]:
    """Return the list of Salla statuses that TRIGGER invoicing for
    this tenant.

    Contract
    ────────
    • When `settings["invoice_trigger_statuses"]` is explicitly set
      (non-empty list of strings), it is honoured verbatim — even if
      it narrows the set to a single status. Tenants who want a
      stricter policy keep full control.

    • When the field is MISSING / None / empty, we widen to the
      unified `ELIGIBLE_ORDER_STATUSES` set so preflight,
      business_rules, pending queue, and live_send_gate agree on
      which statuses are candidates for invoicing.

    The returned list is lower-cased + trimmed for direct membership
    tests, and Arabic natives are preserved as-is.
    """
    raw = settings.get("invoice_trigger_statuses")
    if raw is None:
        return sorted(ELIGIBLE_ORDER_STATUSES)
    normalised: list[str] = []
    for s in raw:
        if not isinstance(s, str):
            continue
        v = s.strip()
        if not v:
            continue
        # Preserve original casing for Arabic natives; lowercase
        # English canonicals so downstream .lower() comparisons hit.
        normalised.append(v.lower() if v.isascii() else v)
    if not normalised:
        return sorted(ELIGIBLE_ORDER_STATUSES)
    return normalised


def is_eligible_status(
    status: Optional[str],
    triggers: Optional[Iterable[str]] = None,
) -> bool:
    """Return True iff `status` is on the eligible list. When
    `triggers` is supplied it takes precedence (explicit tenant
    override); otherwise the shared unified set is used."""
    if not status:
        return False
    s = str(status).strip().lower()
    if triggers is not None:
        allowed = {str(t).strip().lower() for t in triggers}
        return s in allowed
    # Fallback: unified set. Also check the raw (non-lowercased) form
    # so Arabic natives (which don't change under .lower()) match.
    return s in ELIGIBLE_ORDER_STATUSES or status in ELIGIBLE_ORDER_STATUSES
