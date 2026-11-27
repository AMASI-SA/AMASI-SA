"""Preliminary VISUAL receipt checks, never proof of an actual bank deposit.

The approved PRIVATE vision service returns a structured observation of
the WhatsApp receipt image. Mezan verifies visible beneficiary, claimed
amount/date/reference and screenshot digest. The result only means the
image is PLAUSIBLE for a human bank-credit review, not that money arrived.

No public OpenAI image upload, no OCR in Mezan, no GL, no paid order status.
"""

from __future__ import annotations

import hashlib
import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from pydantic import BaseModel, ConfigDict, Field


class ReceiptReviewDenied(ValueError):
    pass


class PrivateVisualObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    source: str
    image_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    document_kind: str
    text_legible: bool
    transfer_marked_completed: bool
    suspicious_editing_visible: bool
    currency: str
    amount: str
    transfer_date: str
    receiving_store_bank_id: int = Field(gt=0)
    beneficiary_name_visible: str = Field(min_length=2, max_length=140)
    transaction_reference_visible: str = Field(min_length=6, max_length=100)


def _space(value: str) -> str:
    return " ".join(value.casefold().strip().split())


def assess_visible_bank_receipt(
    observation: PrivateVisualObservation,
    *,
    expected_image_sha256: str,
    bank_details: dict,
    today: date,
) -> dict:
    """Accept only an approved private visual service's visible observations.

    Appearance can be forged. The reviewer MUST still independently verify
    bank transaction arrival via a bank statement or trusted bank feed.
    """
    if not isinstance(observation, PrivateVisualObservation):
        raise ReceiptReviewDenied("private_vision_observation_required")
    if (observation.source != "approved_private_receipt_vision_v1" or
            observation.image_sha256 != expected_image_sha256 or
            not re.fullmatch(r"[0-9a-f]{64}", expected_image_sha256 or "") or
            not isinstance(bank_details, dict) or
            bank_details.get("source") != "salla_active_store_bank_details" or
            bank_details.get("active") is not True):
        raise ReceiptReviewDenied("receipt_source_unverified")
    if (observation.document_kind != "bank_transfer_receipt" or
            not observation.text_legible or
            not observation.transfer_marked_completed or
            observation.suspicious_editing_visible):
        raise ReceiptReviewDenied("receipt_image_requires_human_review")
    if (observation.currency != "SAR" or
            observation.receiving_store_bank_id != bank_details.get("store_bank_id") or
            _space(observation.beneficiary_name_visible) !=
            _space(str(bank_details.get("beneficiary_name") or ""))):
        raise ReceiptReviewDenied("receipt_visible_bank_details_mismatch")
    try:
        amount = Decimal(observation.amount)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ReceiptReviewDenied("receipt_visible_amount_invalid") from exc
    if (not amount.is_finite() or amount <= 0 or
            amount.as_tuple().exponent < -2 or amount > Decimal("1000000")):
        raise ReceiptReviewDenied("receipt_visible_amount_invalid")
    try:
        claimed_date = date.fromisoformat(observation.transfer_date)
    except ValueError as exc:
        raise ReceiptReviewDenied("receipt_visible_date_invalid") from exc
    if (not isinstance(today, date) or
            claimed_date > today or
            today - claimed_date > timedelta(days=10)):
        raise ReceiptReviewDenied("receipt_date_needs_human_review")
    reference = observation.transaction_reference_visible.strip()
    if (not re.fullmatch(r"[A-Za-z0-9_-]{6,100}", reference) or
            len(set(reference)) == 1):
        raise ReceiptReviewDenied("receipt_reference_unreliable")
    return {
        "state": "preliminary_pass_for_human_review",
        "visual_only": True,
        "bank_credit_confirmed": False,
        "human_review_required": True,
        "store_bank_id": bank_details["store_bank_id"],
        "claimed_amount_sar": f"{amount:.2f}",
        "claimed_transfer_date": claimed_date.isoformat(),
        "reference_sha256": hashlib.sha256(
            reference.encode("utf-8")
        ).hexdigest(),
        "image_sha256": expected_image_sha256,
    }
