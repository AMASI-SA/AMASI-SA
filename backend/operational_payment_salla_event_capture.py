"""Optional capture of proved Salla payment-update evidence into operational CAS.

Called only from the signed Salla payment update inbox after the existing
source fence. This does not post or change balances. Feature flag defaults OFF
and must not be enabled until Preview UAT and source reconciliation pass.
"""
from __future__ import annotations

from datetime import datetime, timezone
import os


FLAG = "OPERATIONAL_PAYMENT_EVIDENCE_CAPTURE_ENABLED"


def enabled():
    return os.environ.get(FLAG) == "1"


def normalized_webhook_capture(event, *, as_of=None):
    """Conservative adapter; no inference from order status or totals alone."""
    if not isinstance(event, dict) or event.get("event") not in {"order.created", "order.payment.updated"}:
        return None
    raw = event.get("data")
    if not isinstance(raw, dict):
        return None
    order = raw.get("order")
    if isinstance(order, dict):
        raw = {**raw, **order}
    order_id = str(raw.get("id") or "").strip()
    if not order_id:
        return None
    amounts = raw.get("amounts")
    total = amounts.get("total") if isinstance(amounts, dict) else raw.get("total")
    if total is None:
        return None
    currency = total.get("currency") if isinstance(total, dict) else raw.get("currency")
    if not isinstance(currency, str) or currency != "SAR":
        return None
    created = raw.get("created_at")
    if not isinstance(created, str):
        return None
    from operational_payment_evidence import normalize_payment
    as_of = as_of or datetime.now(timezone.utc).isoformat()
    normalized, diagnostics = normalize_payment(raw, currency, total, created, as_of)
    return (order_id, normalized, diagnostics)


async def stage_verified_payment_update(db, item):
    """Internal worker only; signed inbound event and owner are already fenced.

    Returns False if the captured proof cannot be durably staged. The inbox
    worker must retry instead of treating a failed staging write as complete.
    A source event without complete capture evidence is not an error and never
    causes a fabricated receipt.
    """
    if not enabled():
        return True
    source = item.get("event")
    if not isinstance(source, dict):
        return False
    try:
        proof = normalized_webhook_capture(source)
        if proof is None:
            return True
        order_id, normalized, diagnostics = proof
        if not normalized or not normalized.get("captures") or diagnostics:
            # Persist only genuinely indicated electronic-payment exceptions,
            # never a pending-payment order based on fulfillment status alone.
            raw = source.get("data") or {}
            if not isinstance(raw, dict):
                return True
            raw = raw.get("order") if isinstance(raw.get("order"), dict) else raw
            payment = raw.get("payment") if isinstance(raw.get("payment"), dict) else {}
            apparent_success = payment.get("status") in {"paid", "captured"}
            payment_update = source.get("event") == "order.payment.updated"
            provider = (normalized or {}).get("provider")
            if not provider:
                provider = next((row.get("provider") for row in diagnostics
                                 if isinstance(row, dict) and row.get("provider")), None)
            if not provider and apparent_success:
                # The merchant must be told that the successful payment
                # cannot be assigned to a platform. Keep it under unknown,
                # never invent a platform or post to a default account.
                provider = "unknown"
            if provider in {"salla", "tabby", "tamara", "emkan", "paypal", "unknown"} and (
                    apparent_success or payment_update):
                from operational_payment_evidence_review import stage_unverified_review
                reasons = [row.get("code") for row in diagnostics if isinstance(row, dict)]
                await stage_unverified_review(
                    db, item["user_id"], order_id, item["_id"], provider,
                    source["event"], reasons,
                )
            return True
        from operational_payment_evidence_inbox import stage_normalized_captures
        await stage_normalized_captures(
            db, item["user_id"], order_id, normalized, diagnostics,
            source_ref=item["_id"],
        )
    except Exception:
        # The worker owns retry/structured diagnostics. Never log financial
        # payloads, tokens, source refs or customer identifiers.
        return False
    return True
