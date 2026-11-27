"""Strict signed receipt visual evidence from a PRIVATE WhatsApp service.

The service must privately inspect the actual latest Chatwoot image,
check its SHA-256 and use an independently approved vision source. The
assistant cannot call this from arbitrary text/LLM JSON. A signed receipt
is *only plausible*, never money received or a verified bank transaction.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .bank_receipt import ReceiptVisionEvidence
from .signature import ID, DIGEST, SignatureDenied, verify_signed_json


class SignedBankReceiptScreen(BaseModel):
    model_config=ConfigDict(extra="forbid",strict=True)

    action: Literal["salla.screen_bank_transfer_receipt"]
    account_id: int=Field(gt=0)
    inbox_id: int=Field(gt=0)
    conversation_id: int=Field(gt=0)
    contact_id: int=Field(gt=0)
    source_message_id: int=Field(gt=0)
    source_attachment_id: int=Field(gt=0)
    intent_id: str=Field(pattern=ID)
    store_bank_id: int=Field(gt=0)
    looks_like_bank_transfer_receipt: bool
    text_legible: bool
    amount_sar: str=Field(min_length=1,max_length=24)
    currency: Literal["SAR"]
    beneficiary_iban_last4: str=Field(min_length=4,max_length=4)
    source_image_sha256: str=Field(pattern=DIGEST)
    bank_reference_sha256: str=Field(pattern=DIGEST)
    transfer_at_iso: str=Field(min_length=20,max_length=40)
    flags: list[str]=Field(default_factory=list,max_length=12)
    visual_source: Literal["approved_private_receipt_vision_v1"]


def verify_bank_receipt_screen(
    raw: bytes, timestamp: str, delivery: str, signature: str,
    secret: bytes, *, now: int | None = None,
) -> SignedBankReceiptScreen:
    verified=verify_signed_json(
        raw,timestamp,delivery,signature,secret,now=now,
    )
    try:
        return SignedBankReceiptScreen.model_validate(verified)
    except ValidationError as exc:
        raise SignatureDenied("invalid_signed_bank_receipt_screen") from exc


def as_visual_evidence(action: SignedBankReceiptScreen) -> ReceiptVisionEvidence:
    if not isinstance(action,SignedBankReceiptScreen):
        raise SignatureDenied("trusted_receipt_screen_required")
    try:
        at=datetime.fromisoformat(action.transfer_at_iso.replace("Z","+00:00"))
    except (ValueError,TypeError) as exc:
        raise SignatureDenied("invalid_receipt_transfer_time") from exc
    if at.tzinfo is None:
        raise SignatureDenied("receipt_transfer_timezone_required")
    return ReceiptVisionEvidence(
        source=action.visual_source,
        looks_like_bank_transfer_receipt=action.looks_like_bank_transfer_receipt,
        text_legible=action.text_legible,
        amount_sar=action.amount_sar,
        currency=action.currency,
        beneficiary_bank_id=action.store_bank_id,
        beneficiary_iban_last4=action.beneficiary_iban_last4,
        transfer_at_utc=at,
        source_image_sha256=action.source_image_sha256,
        bank_reference_sha256=action.bank_reference_sha256,
        flags=tuple(action.flags),
    )
