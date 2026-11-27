"""HMAC-only bank review status sync from already-created Salla intent.

The caller may not provide bank payment status, an order ID, a receipt URL or
a bank-credit confirmation. Mezan resolves the original Salla order ID,
customer receipt claim and amount from its own protected database.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .signature import ID, SignatureDenied, verify_signed_json


class SignedBankReviewSync(BaseModel):
    model_config=ConfigDict(extra="forbid",strict=True)

    action: Literal["salla.reconcile_bank_receipt_review"]
    account_id: int=Field(gt=0)
    inbox_id: int=Field(gt=0)
    conversation_id: int=Field(gt=0)
    source_contact_id: int=Field(gt=0)
    intent_id: str=Field(pattern=ID)
    review_ref: str=Field(pattern=r"^bank-review\.[a-f0-9]{32}$")


def verify_bank_review_sync(
    raw: bytes, timestamp: str, delivery: str,
    signature: str, secret: bytes, *, now: int | None = None,
) -> SignedBankReviewSync:
    parsed=verify_signed_json(
        raw,timestamp,delivery,signature,secret,now=now,
    )
    try:
        return SignedBankReviewSync.model_validate(parsed)
    except ValidationError as exc:
        raise SignatureDenied("invalid_bank_review_sync_action") from exc
