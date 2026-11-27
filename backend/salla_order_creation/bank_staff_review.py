"""One durable, redacted bank transfer review case per created Salla order.

The receipt stays in the private Chatwoot message; Mezan stores only its
scoped image ID, redacted amount and bank ID. Staff must separately compare
the actual bank deposit and approve. Neither OCR/vision nor Salla create
confirms payment. This module never writes paid status or accounting entries.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any


class BankReviewDenied(ValueError):
    pass


def _amount(value):
    try:
        n = Decimal(str(value))
        if n.is_finite() and n > 0 and n.as_tuple().exponent >= -2:
            return f"{n:.2f}"
    except (InvalidOperation, ValueError, TypeError):
        pass
    raise BankReviewDenied("invalid_receipt_or_salla_total")


async def ensure_staff_bank_review(
    db: Any, *,
    user_id: str, intent_id: str,
    salla_order_id: int, reference_id: int | None,
    receipt: dict, now: datetime | None = None,
) -> dict:
    if (not user_id or not intent_id or type(salla_order_id) is not int
            or salla_order_id < 1 or not isinstance(receipt, dict)
            or receipt.get("user_id") != user_id
            or receipt.get("intent_id") != intent_id
            or receipt.get("state") != "appearance_plausible"
            or receipt.get("credit_received") is not False):
        raise BankReviewDenied("verified_pending_bank_review_required")
    clock = now or datetime.now(timezone.utc)
    key = hashlib.sha256(
        f"bank-staff-review:{user_id}:{intent_id}".encode("utf-8")
    ).hexdigest()
    core = {
        "user_id": user_id, "intent_id": intent_id,
        "salla_order_id": salla_order_id,
        "salla_reference_id": reference_id,
        "inbox_id": receipt["inbox_id"],
        "conversation_id": receipt["conversation_id"],
        "source_contact_id": receipt["source_contact_id"],
        "source_message_id": receipt["source_message_id"],
        "source_attachment_id": receipt["source_attachment_id"],
        "source_kind": receipt.get("source_kind", "image"),
        "bank_id": receipt["bank_id"],
        "receipt_reference_fingerprint": receipt["reference_fingerprint"],
        "receipt_amount_sar": _amount(receipt["receipt_amount_sar"]),
    }
    document = {
        "_id": key, **core,
        "review_state": "awaiting_staff_bank_verification",
        "bank_credit_confirmed": False,
        "paid": False,
        "fulfillment_eligible": False,
        "receipt_appearance_only": True,
        "created_at": clock,
    }
    try:
        await db.salla_ai_bank_staff_reviews.insert_one(document)
    except Exception:
        prior = await db.salla_ai_bank_staff_reviews.find_one({"_id": key})
        if prior is None:
            raise
        if any(prior.get(k) != v for k,v in core.items()):
            raise BankReviewDenied("staff_review_case_intent_conflict")
        return {"state": prior.get("review_state"),
                "salla_order_id": salla_order_id,
                "staff_review_required": True,
                "bank_credit_confirmed": False,
                "replayed": True}
    return {
        "state": "awaiting_staff_bank_verification",
        "salla_order_id": salla_order_id,
        "staff_review_required": True,
        "bank_credit_confirmed": False,
        "replayed": False,
    }


def assess_bank_order_total(case: dict, *,
                            actual_total_sar: str,
                            actual_currency: str,
                            actual_method: str,
                            is_pending_review: bool) -> dict:
    """Readback only: no payment status mutation and no automatic settlement."""
    if (not isinstance(case, dict) or
            case.get("review_state") not in (
                "awaiting_staff_bank_verification",
                "needs_staff_reconciliation",
            ) or case.get("bank_credit_confirmed") is not False):
        raise BankReviewDenied("unverified_bank_review_case")
    flags = []
    if actual_method != "bank":
        flags.append("salla_payment_method_not_bank")
    if actual_currency != "SAR":
        flags.append("order_currency_not_sar")
    if not is_pending_review:
        flags.append("salla_order_not_in_review_status")
    try:
        actual = _amount(actual_total_sar)
        receipt = _amount(case.get("receipt_amount_sar"))
        if abs(Decimal(actual) - Decimal(receipt)) > Decimal("0.01"):
            flags.append("receipt_amount_differs_from_salla_order")
    except BankReviewDenied:
        flags.append("order_or_receipt_amount_missing")
    return {
        "state": ("awaiting_staff_bank_verification" if not flags
                  else "needs_staff_reconciliation"),
        "flags": sorted(set(flags)),
        "bank_credit_confirmed": False,
        "paid": False,
        "fulfillment_eligible": False,
        "staff_review_required": True,
    }
