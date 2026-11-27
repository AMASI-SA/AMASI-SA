"""Purpose-limited signed Chatwoot bank-transfer receipt screening request.

The sender must verify the *current* public customer image message and exact
attachment with Chatwoot's native guarded API. OCR/vision fields are UNTRUSTED
candidates only. This action cannot mark paid, verify bank credit, post a
Salla order or tell an accounting/fulfillment workflow to proceed.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .bank_receipt_review import ReceiptVisibleFields
from .signature import ID, DIGEST, SignatureDenied, verify_signed_json


class SignedBankReceiptStage(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    action: Literal["salla.stage_bank_receipt_appearance"]
    account_id: int = Field(gt=0)
    inbox_id: int = Field(gt=0)
    conversation_id: int = Field(gt=0)
    intent_id: str = Field(pattern=ID)
    source_contact_id: int = Field(gt=0)
    source_message_id: int = Field(gt=0)
    source_attachment_id: int = Field(gt=0)
    source_id_sha256: str = Field(pattern=DIGEST)
    selected_bank_id: int = Field(gt=0)
    visible: ReceiptVisibleFields


def verify_bank_receipt_action(
    raw: bytes, stamp: str, delivery: str, signature: str, secret: bytes,
    *, now: int | None = None,
) -> SignedBankReceiptStage:
    parsed = verify_signed_json(raw, stamp, delivery, signature, secret, now=now)
    try:
        return SignedBankReceiptStage.model_validate(parsed)
    except ValidationError as exc:
        raise SignatureDenied("invalid_bank_receipt_screening_action") from exc
