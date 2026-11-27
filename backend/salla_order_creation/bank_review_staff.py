"""Read-only Mezan bank-receipt reconciliation and explicit STAFF decision.

This is a separate review queue from bank_transfer_review_routes.py:
that legacy /confirm endpoint may POST general-ledger entries. New AI bank
receipt review MUST NOT call it or mark Salla paid automatically.

A screenshot can be forged. Only a human verifying an independent bank
statement/credit can record 'bank_credit_seen_accounting_hold'. Accounting,
order release and Salla payment updates remain separately authorized work.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any


class StaffBankReviewDenied(ValueError):
    pass


def _money(value: Any) -> Decimal | None:
    try:
        x=Decimal(str(value))
    except (TypeError,ValueError,InvalidOperation):
        return None
    return x if x.is_finite() and x > 0 and x.as_tuple().exponent>=-2 else None


def reconcile_bank_receipt_with_mezan_order(review: dict, order: Any) -> dict:
    """Compare receipt CLAIM to actual same-order SAR total, NEVER to GL."""
    if not isinstance(review, dict) or order is None:
        return {"state":"waiting_for_mezan_sync","amount_match":False}
    if (str(getattr(order,"order_id","")) != str(review.get("salla_order_id")) or
            str(getattr(order,"order_number","")) != str(
                review.get("salla_reference_id")
            )):
        return {"state":"wrong_salla_order","amount_match":False}
    status = str(getattr(order, "status", "") or "").strip().casefold()
    native_status = str(getattr(order, "status_native", "") or "").strip()
    if (status not in (
        "under_review", "waiting", "pending_payment", "payment_pending",
    ) and native_status not in (
        "بإنتظار المراجعة", "بانتظار المراجعة",
        "بإنتظار الدفع", "بانتظار الدفع",
    )):
        return {"state":"order_not_in_review_stage","amount_match":False}
    totals=getattr(order,"totals",None)
    payment=getattr(order,"payment",None)
    if totals is None or payment is None:
        return {"state":"waiting_for_mezan_payment","amount_match":False}
    if (str(getattr(totals,"currency","")).upper()!="SAR" or
            str(getattr(payment,"method","")).lower() not in
            ("bank","bank_transfer","تحويل بنكي","حوالة بنكية") or
            getattr(payment,"paid_amount",None) not in (0,0.0,"0","0.00") or
            getattr(payment,"collection_status",None) in ("paid","partial")):
        return {"state":"bank_order_payment_unverified","amount_match":False}
    total=_money(getattr(totals,"total",None))
    claimed=_money(review.get("claimed_amount_sar"))
    if total is None or claimed is None:
        return {"state":"bank_amount_unavailable","amount_match":False}
    matched=abs(total-claimed)<=Decimal("0.01")
    return {
        "state":"ready_for_human_bank_review" if matched
                else "receipt_amount_differs_from_salla_order",
        "salla_order_id":review.get("salla_order_id"),
        "salla_reference_id":review.get("salla_reference_id"),
        "actual_salla_total_sar":f"{total:.2f}",
        "receipt_claimed_amount_sar":f"{claimed:.2f}",
        "amount_match":matched,
        "bank_credit_confirmed":False,
        "fulfillment_released":False,
    }


def validate_human_bank_decision(
    *, decision: str, review: dict, reconciliation: dict,
    reviewer_id: str, statement_reference: str | None = None,
    statement_amount_sar: str | None = None,
) -> dict:
    if (not reviewer_id or not isinstance(review,dict) or
            review.get("state")!="awaiting_human_bank_review"):
        raise StaffBankReviewDenied("pending_human_review_required")
    if decision not in ("needs_customer_evidence", "reject_receipt",
                        "bank_credit_seen"):
        raise StaffBankReviewDenied("unsupported_bank_review_decision")
    if decision == "bank_credit_seen":
        if (not isinstance(reconciliation,dict) or
                reconciliation.get("state")!="ready_for_human_bank_review" or
                reconciliation.get("amount_match") is not True):
            raise StaffBankReviewDenied("independent_amount_match_required")
        if (not isinstance(statement_reference,str) or
                not re.fullmatch(r"[A-Za-z0-9_-]{6,100}",statement_reference)):
            raise StaffBankReviewDenied("independent_bank_statement_reference_required")
        amount=_money(statement_amount_sar)
        actual=_money(reconciliation.get("actual_salla_total_sar"))
        if amount is None or actual is None or abs(amount-actual)>Decimal("0.01"):
            raise StaffBankReviewDenied("bank_statement_amount_mismatch")
        return {
            "state":"bank_credit_seen_accounting_hold",
            "bank_statement_reference_sha256":hashlib.sha256(
                statement_reference.encode("utf-8")
            ).hexdigest(),
            "bank_statement_amount_sar":f"{amount:.2f}",
            "visual_receipt_approved_by_staff":True,
            "accounting_posted":False,
            "salla_marked_paid":False,
            "fulfillment_released":False,
        }
    return {
        "state": "needs_customer_evidence" if decision=="needs_customer_evidence"
                 else "receipt_rejected",
        "visual_receipt_approved_by_staff":False,
        "accounting_posted":False,
        "salla_marked_paid":False,
        "fulfillment_released":False,
    }
