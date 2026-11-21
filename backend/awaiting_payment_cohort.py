"""Separate source order counts from the guarded financial reader cohort."""
from collections import Counter
from hashlib import sha256
import json

from awaiting_payment_eligibility import ELIGIBILITY_FIELD

READER_VERSION = "financial-cohort-deferred-dashboard-v2"


def split_financial_cohort(orders):
    """Preserve unknown orders for review; exclude only checked unpaid proofs.

    The returned count is not a certification of payment or revenue. It is the
    number retained by the caller's existing policy after this narrow rule.
    Neither the source registry nor any financial facts are modified.
    """
    selected, decisions, reasons = [], [], Counter()
    excluded = reviews = 0
    for order in orders:
        decision = order.get(ELIGIBILITY_FIELD)
        decision = decision if isinstance(decision, dict) else {}
        if decision.get("eligible") is False and (
                decision.get("history_checked") is not True or decision.get("review_required") is True):
            decision = {**decision, "eligible": True, "review_required": True,
                        "reason_code": (decision.get("reason_code") if decision.get("review_required") is True
                                        else "awaiting_payment_history_unverified")}
            order = {**order, ELIGIBILITY_FIELD: decision}
        remove = decision.get("eligible") is False and decision.get("history_checked") is True
        review = decision.get("review_required") is True
        if remove:
            excluded += 1
        else:
            selected.append(order)
            reviews += int(review)
            if review:
                reasons[str(decision.get("reason_code") or "awaiting_payment_review_required")] += 1
        watermark = order.get("g47_salla_snapshot")
        watermark = watermark if isinstance(watermark, dict) else {}
        decisions.append([
            str(order.get("user_id") or ""),
            str(order.get("salla_order_id") or order.get("order_id") or ""),
            str(order.get("order_number") or ""),
            str(decision.get("source_updated_at") or watermark.get("source_updated_at") or ""),
            remove, review, str(decision.get("reason_code") or ""),
            decision.get("history_checked") is True,
        ])
    digest = sha256(json.dumps(sorted(decisions), ensure_ascii=False,
                               separators=(",", ":")).encode()).hexdigest()
    return selected, {
        "reader_version": READER_VERSION,
        "total_orders": len(orders),
        "financial_eligible_orders": len(selected),
        "excluded_unpaid_orders": excluded,
        "review_orders": reviews,
        "complete": reviews == 0,
        "review_reason_counts": dict(sorted(reasons.items())),
        "eligibility_digest": digest,
    }
