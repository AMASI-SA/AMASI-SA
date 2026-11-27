"""Appearance-only bank receipt precheck for WhatsApp assisted Salla orders.

Never infer money actually reached a bank from a screenshot or OCR field. A
customer-supplied transfer receipt is *claim evidence*; final human reviewer
must reconcile the bank account/bank feed independently before marking paid.
No paid flag, Salla payment mutation, ledger or Qoyod calls exist here.

The structured observations must originate from a separate trusted, private
receipt inspection process with a canonical Chatwoot attachment reference
and provenance. Not from arbitrary language-model chat text. Until such a
private inspector is approved, this is synthetic/offline acceptance ONLY.
"""

from __future__ import annotations

import hashlib
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

MAX_DATE_AGE_DAYS = 30


class ReceiptReviewDenied(ValueError):
    pass


class PrivateReceiptObservation(BaseModel):
    """Private inspector observations only, never direct WhatsApp user input."""
    model_config = ConfigDict(extra="forbid", strict=True)

    account_id: int = Field(gt=0)
    inbox_id: int = Field(gt=0)
    conversation_id: int = Field(gt=0)
    contact_id: int = Field(gt=0)
    message_id: int = Field(gt=0)
    attachment_id: int = Field(gt=0)
    attachment_source_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    inspector_source: Literal["trusted_private_receipt_inspector_v1"]
    attachment_kind: Literal["image", "pdf"]
    legible: bool
    appears_bank_receipt: bool
    visible_currency: Literal["SAR", "unknown", "other"]
    visible_amount_sar: str | None = None
    transfer_date_iso: str | None = None
    transaction_reference_sha256: str | None = Field(
        default=None,pattern=r"^[a-f0-9]{64}$"
    )
    # The actual payee is never stored here. A private account-directory
    # check gives only the exact-match result for an approved bank account.
    recipient_bank_match: Literal["match", "mismatch", "unverified"]
    duplication_indicator: bool = False
    screenshot_edit_warning: bool = False


def _currency(value: str | None) -> Decimal | None:
    if type(value) is not str:
        return None
    try:
        out = Decimal(value)
    except (InvalidOperation, ValueError):
        return None
    if (not out.is_finite() or out <= 0 or
            out.as_tuple().exponent < -2):
        return None
    return out


def assess_bank_receipt_appearance(
    observation: PrivateReceiptObservation,
    *,
    expected_total_sar: str | None,
    now: datetime,
) -> dict:
    """Appearance-only checks, never bank clearance or 'paid' evidence."""
    if (not isinstance(observation, PrivateReceiptObservation) or
            type(now) is not datetime or now.tzinfo is None):
        raise ReceiptReviewDenied("trusted_receipt_context_required")
    now = now.astimezone(timezone.utc)
    reasons = []
    if not observation.legible:
        reasons.append("receipt_not_legible")
    if not observation.appears_bank_receipt:
        reasons.append("not_recognizable_bank_receipt")
    if observation.duplication_indicator:
        reasons.append("possible_reused_receipt")
    if observation.screenshot_edit_warning:
        reasons.append("image_integrity_warning")
    if observation.recipient_bank_match != "match":
        reasons.append("bank_recipient_not_verified")
    if observation.visible_currency != "SAR":
        reasons.append("receipt_currency_not_sar")

    parsed_amount = _currency(observation.visible_amount_sar)
    actual_total = _currency(expected_total_sar)
    if actual_total is None:
        reasons.append("salla_final_amount_not_yet_synced")
    elif parsed_amount is None or abs(parsed_amount - actual_total) > Decimal("0.01"):
        reasons.append("receipt_amount_not_matching_order")

    try:
        transfer_date = (
            date.fromisoformat(observation.transfer_date_iso)
            if observation.transfer_date_iso else None
        )
    except (TypeError, ValueError):
        transfer_date = None
    if transfer_date is None:
        reasons.append("transfer_date_unverified")
    elif (transfer_date > now.date() or
          transfer_date < now.date() - timedelta(days=MAX_DATE_AGE_DAYS)):
        reasons.append("transfer_date_outside_review_window")
    if observation.transaction_reference_sha256 is None:
        reasons.append("transaction_reference_missing")

    return {
        "appearance": "looks_consistent" if not reasons else "needs_clarification",
        "issues": sorted(reasons),
        "review_required": True,
        "payment_confirmed": False,
        "bank_settlement_confirmed": False,
        "may_post_gl": False,
        "may_mark_salla_paid": False,
        "attachment_id": observation.attachment_id,
        "source_message_id": observation.message_id,
    }


def receipt_review_key(owner_id: str, order_id: int, message_id: int) -> str:
    if (not owner_id or type(order_id) is not int or order_id < 1 or
            type(message_id) is not int or message_id < 1):
        raise ReceiptReviewDenied("invalid_bank_review_identity")
    return hashlib.sha256(
        f"assisted-bank-review:{owner_id}:{order_id}:{message_id}".encode()
    ).hexdigest()
