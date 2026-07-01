"""Selective Send Guard — Phase C P0 Wiring (2026-07-01).

Purpose
────────
Single choke point every Qoyod-write code path MUST pass through
before making an API call. Combines:

    1. `assert_send_allowed()` — runs the Iter-001i policy and either
       returns the `SelectiveSendDecision` (allow) or RAISES
       `SelectiveSendPolicyBlocked` (block). Callers cannot silently
       proceed past a block.

    2. `apply_send_date_to_qoyod_payload()` — canonical rewriter that
       stamps `date` / `issue_date` / `due_date` / `payment_date`
       fields with `decision.send_date_riyadh`. Removes any legacy
       date lingering in the payload (`completed_at`, `delivered_at`,
       `paid_at`, `received_at`, `order.created_at`).

Contract:
    • Zero Qoyod API calls in this module.
    • Zero DB writes.
    • Pure — every function is deterministic given inputs.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from integrations.qoyod.selective_send_policy import (
    SelectiveSendDecision,
    should_allow_selective_live_send,
)


class SelectiveSendPolicyBlocked(Exception):
    """Raised when the policy REFUSES to send an order to قيود.

    Callers MUST catch this and abort the write — never fall back to
    "send anyway". The exception carries the full decision so the
    caller can log the blocker_code and blocker_reason.
    """

    def __init__(self, decision: SelectiveSendDecision):
        self.decision = decision
        self.blocker_code = decision.blocker_code
        self.blocker_reason = decision.blocker_reason
        super().__init__(
            f"SELECTIVE_SEND_BLOCKED code={decision.blocker_code} "
            f"reason={decision.blocker_reason!r} "
            f"order={decision.order_number}")


def assert_send_allowed(
    *,
    order: dict,
    settings: dict,
    manual_send_requested: bool = False,
    manual_approval_phrase: Optional[str] = None,
    now_utc: Optional[datetime] = None,
) -> SelectiveSendDecision:
    """Run the policy. Return the decision on ALLOW, raise on BLOCK.

    This is the SINGLE canonical entry point every send code path
    must call before invoking QoyodAPIClient. Two guarantees:

        • If it RETURNS, `decision.decision == "allow"` and
          `decision.would_send_to_qoyod is True`. The caller may
          proceed to build & send the payload — but MUST also
          respect the api_client's `production_writes_locked` guard
          (defense-in-depth).

        • If it RAISES `SelectiveSendPolicyBlocked`, the caller MUST
          abort. No fallback, no retry with different args.
    """
    decision = should_allow_selective_live_send(
        order=order,
        settings=settings,
        manual_send_requested=manual_send_requested,
        manual_approval_phrase=manual_approval_phrase,
        now_utc=now_utc,
    )
    if decision.decision != "allow":
        raise SelectiveSendPolicyBlocked(decision)
    return decision


# ── Date-stamping fields we OWN (per Iter-001h directive) ───────────
# Every Qoyod payload we build MUST use `send_date_riyadh` for these
# fields. Legacy sources are actively wiped by this rewriter.
_DATE_FIELDS_TO_STAMP: tuple[str, ...] = (
    "date", "issue_date", "invoice_date",
    "due_date", "payment_date", "receipt_date",
)

# Legacy date keys we SCRUB from the payload to prevent the caller
# from accidentally shipping order-side timestamps.
_LEGACY_DATE_FIELDS_TO_SCRUB: tuple[str, ...] = (
    "completed_at", "delivered_at", "paid_at",
    "received_at", "order_created_at", "created_at",
)


def apply_send_date_to_qoyod_payload(
    payload: dict,
    decision: SelectiveSendDecision,
) -> dict:
    """Rewrite payload dates to `decision.send_date_riyadh`.

    Called EXACTLY ONCE by every payload builder right before the
    payload is handed to `QoyodAPIClient`. Guarantees:
        • Every date field we own = `send_date_riyadh` (YYYY-MM-DD).
        • Legacy timestamp fields are scrubbed (set to None then
          deleted) so nothing bleeds through by accident.
        • Nested `invoice` / `payment` / `receipt` sub-payloads are
          also rewritten recursively.

    The rewrite is idempotent — calling it twice is safe.
    """
    if not isinstance(payload, dict):
        return payload
    if decision is None or not decision.send_date_riyadh:
        raise ValueError(
            "apply_send_date_to_qoyod_payload requires an ALLOW "
            "decision carrying send_date_riyadh")
    send_date = decision.send_date_riyadh

    def _rewrite(node: Any) -> Any:
        if isinstance(node, dict):
            for k in list(node.keys()):
                if k in _DATE_FIELDS_TO_STAMP:
                    node[k] = send_date
                elif k in _LEGACY_DATE_FIELDS_TO_SCRUB:
                    node.pop(k, None)
                else:
                    node[k] = _rewrite(node[k])
            return node
        if isinstance(node, list):
            return [_rewrite(item) for item in node]
        return node

    return _rewrite(payload)
