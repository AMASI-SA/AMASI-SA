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
    # The canonical provider recognition policy requires that a financial
    # capture follows the source order's ORIGINAL creation instant. Retain
    # that trusted clock (never substituted by webhook arrival or order update).
    if isinstance(normalized, dict):
        normalized = {**normalized, "order_created_at": created}
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
            # Strong, currently scoped Salla order-page evidence is a separate
            # cumulative paid observation. It is NOT a fabricated captured_at
            # and never applies to bank transfers or COD.
            # Avoid importing the native-page financial writer at all while
            # its opt-in is OFF. This keeps ordinary Salla ingestion fast.
            if os.environ.get("OPERATIONAL_PAYMENT_ORDER_PAGE_POST_ENABLED") == "1":
                from operational_payment_first_order_page_writer import post_first_paid_page
                outcome = await post_first_paid_page(
                    db, item["user_id"], item["merchant_id"],
                    item["order_number"], source_ref=item["_id"],
                )
                if outcome.get("status") in {"posted", "already_posted"}:
                    return True
                if outcome.get("status") == "review_required":
                    from operational_payment_evidence_review import stage_unverified_review
                    await stage_unverified_review(
                        db, item["user_id"], outcome["order_id"],
                        item["_id"], outcome["provider"],
                        source["event"], [outcome["reason_code"]],
                    )
                    return True
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
        # A SECOND, independently disabled gate optionally posts only the
        # FIRST, full native capture into the existing operational provider
        # claim. Partial/additional payments retain the reviewed workflow.
        from operational_payment_first_capture_writer import (
            enabled as first_capture_post_enabled, post_first_salla_capture,
        )
        if first_capture_post_enabled():
            from decimal import Decimal, InvalidOperation
            captures = normalized["captures"]
            try:
                first_full = (len(captures) == 1
                              and Decimal(str(captures[0]["amount"])) ==
                                  Decimal(str(normalized["gross"]))
                              and not normalized.get("refunds"))
            except (TypeError, ValueError, InvalidOperation, KeyError):
                first_full = False
            if first_full:
                outcome = await post_first_salla_capture(
                    db, item["user_id"], item["merchant_id"],
                    order_id, item["order_number"], normalized,
                    source_ref=item["_id"],
                )
                if outcome.get("status") == "review_required":
                    from operational_payment_evidence_review import stage_unverified_review
                    await stage_unverified_review(
                        db, item["user_id"], order_id, item["_id"],
                        normalized["provider"], source["event"],
                        [outcome["reason_code"]],
                    )
    except Exception:
        # The worker owns retry/structured diagnostics. Never log financial
        # payloads, tokens, source refs or customer identifiers.
        return False
    return True
