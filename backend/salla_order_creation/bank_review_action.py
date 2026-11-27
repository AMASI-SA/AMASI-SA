"""Scoped HMAC receipt review request from PRIVATE Chatwoot inspection only.

The model's visual guess never becomes an accepted bank payment. This
request may create a pending human review and request Salla 'under_review'
order status, but cannot mark paid, post GL, alter totals or issue refunds.
"""

from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .bank_receipt_review import PrivateReceiptObservation
from .signature import ID, SignatureDenied, verify_signed_json


class SignedBankReceiptReview(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    action: Literal["salla.queue_bank_receipt_for_review"]
    account_id: int = Field(gt=0)
    inbox_id: int = Field(gt=0)
    conversation_id: int = Field(gt=0)
    intent_id: str = Field(pattern=ID)
    observation: PrivateReceiptObservation


def verify_bank_receipt_review(
    raw: bytes, timestamp: str, delivery: str, signature: str,
    secret: bytes, *, now: int | None = None,
) -> SignedBankReceiptReview:
    obj=verify_signed_json(
        raw,timestamp,delivery,signature,secret,now=now,
    )
    try:
        action=SignedBankReceiptReview.model_validate(obj)
    except ValidationError as exc:
        raise SignatureDenied("invalid_bank_receipt_review_action") from exc
    if (action.account_id,action.inbox_id,action.conversation_id)!=(
            action.observation.account_id,
            action.observation.inbox_id,
            action.observation.conversation_id):
        raise SignatureDenied("receipt_scope_mismatch")
    return action
