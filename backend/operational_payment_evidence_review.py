"""Durable, no-money record of unverified electronic payment evidence.

A payment status or awaiting-review order status alone is not a capture.
Missing/malformed financial proof is retained as an exception and never
increases the operational balance. The owner-only integrity preview can
summarize the exceptions for review.

No public API routes or external calls. Must be invoked only by the
already-authenticated Salla event worker with owner identity verified.
"""
from __future__ import annotations

from hashlib import sha256
import json

MAX_PENDING = 10000
PENDING_KEY = "payment_evidence_review_inbox"
# Fixed, payment-specific failure vocabulary. Do not import the larger Salla
# runtime into the pure review model or disclose a raw third-party exception.
ALLOWED_REASONS = frozenset({
    "provider_capture_evidence_incomplete",
    "provider_payment_method_conflict",
    "provider_payment_shape_invalid",
    "provider_payment_currency_conflict",
    "provider_payment_amount_conflict",
    "provider_payment_reference_missing",
    "provider_payment_reference_conflict",
    "provider_capture_timestamp_invalid",
    "provider_capture_conflict",
    "provider_capture_status_conflict",
    "provider_transaction_binding_conflict",
    "provider_transaction_not_captured",
    "provider_transaction_amount_conflict",
    "provider_split_payment_review_required",
    "provider_refund_execution_evidence_required",
})
ALLOWED_PROVIDERS = frozenset({"salla", "tabby", "tamara", "emkan", "paypal", "unknown"})
ALLOWED_EVENTS = frozenset({"order.created", "order.payment.updated"})


def _digest(parts) -> str:
    return sha256(json.dumps(parts, sort_keys=True, ensure_ascii=False,
                             separators=(",", ":")).encode()).hexdigest()


def build_review_record(owner, order_id, source_ref, provider, event_type, reasons):
    """Return a safe fixed-code review record; do not include amounts or PII."""
    if not all(type(v) is str and 0 < len(v) <= 256 for v in (owner, order_id, source_ref)):
        raise ValueError("payment_review_identity_invalid")
    if provider not in ALLOWED_PROVIDERS or event_type not in ALLOWED_EVENTS:
        raise ValueError("payment_review_provider_event_invalid")
    if not isinstance(reasons, (tuple, list, set, frozenset)):
        raise ValueError("payment_review_reasons_invalid")
    safe_reasons = sorted({reason for reason in reasons if type(reason) is str and reason in ALLOWED_REASONS})
    if not safe_reasons:
        safe_reasons = ["provider_capture_evidence_incomplete"]
    key = _digest([owner, "unverified_payment", source_ref, order_id, provider])
    return {"key": key, "order_id": order_id, "provider": provider,
            "event_type": event_type, "reason_codes": safe_reasons,
            "status": "pending_evidence", "source_ref": source_ref}


def queue_review_in_state(state, record, stamp):
    """Atomic inside an operational CAS candidate. Does not post money."""
    if state.get("status") != "active":
        raise ValueError("operational_workspace_not_active")
    rows = state.setdefault(PENDING_KEY, {})
    if not isinstance(rows, dict) or len(rows) > MAX_PENDING:
        raise ValueError("payment_review_capacity")
    existing = rows.get(record["key"])
    if existing is not None:
        if (existing.get("order_id"), existing.get("provider"), existing.get("event_type")) != (
                record["order_id"], record["provider"], record["event_type"]):
            raise ValueError("payment_review_identity_conflict")
        # Replays must not reset an accountant's later decision.
        return {"status": "already_queued", "key": record["key"]}
    if len(rows) >= MAX_PENDING:
        raise ValueError("payment_review_capacity")
    rows[record["key"]] = {**record, "created_at": stamp}
    return {"status": "pending_evidence", "key": record["key"]}


async def stage_unverified_review(db, owner, order_id, source_ref, provider,
                                  event_type, reasons, *, stamp=None):
    from operational_balance_store import fail, mutate, now
    record = build_review_record(owner, order_id, source_ref, provider, event_type, reasons)
    stamp = stamp or now()

    async def apply(state):
        try:
            return queue_review_in_state(state, record, stamp)
        except ValueError as exc:
            fail(str(exc), "تعذر تسجيل استثناء إثبات الدفع", 409)

    return await mutate(db, owner, apply)
