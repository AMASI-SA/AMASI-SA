"""Narrow signed WhatsApp bank receipt VISUAL observation (no bank settlement).

Only a separately approved private vision service on the native Chatwoot host
may sign source-bound observations from a canonical customer image message.
It is forbidden to send an AI-uploaded raw receipt URL, bank credentials,
payment.status, bank credit claim or public checkout link in this action.
"""

from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .bank_receipt_preflight import PrivateVisualObservation
from .signature import ID, SignatureDenied, verify_signed_json


class SignedBankReceiptObservation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    action: Literal["salla.bank_receipt_preliminary_review"]
    account_id: int = Field(gt=0)
    inbox_id: int = Field(gt=0)
    conversation_id: int = Field(gt=0)
    source_contact_id: int = Field(gt=0)
    source_message_id: int = Field(gt=0)
    attachment_id: int = Field(gt=0)
    intent_id: str = Field(pattern=ID)
    image_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    observation: PrivateVisualObservation


def verify_bank_receipt_action(
    body: bytes, stamp: str, delivery: str, signature: str, secret: bytes,
    *, now: int | None = None,
) -> SignedBankReceiptObservation:
    raw = verify_signed_json(
        body, stamp, delivery, signature, secret, now=now,
    )
    try:
        action = SignedBankReceiptObservation.model_validate(raw)
    except ValidationError as exc:
        raise SignatureDenied("invalid_private_bank_receipt_action") from exc
    if action.image_sha256 != action.observation.image_sha256:
        raise SignatureDenied("receipt_image_digest_mismatch")
    if action.observation.source != "approved_private_receipt_vision_v1":
        raise SignatureDenied("unapproved_receipt_vision_source")
    return action
