"""Purpose-limited signed Chatwoot bank-transfer receipt screening request.

The sender must verify the *current* public customer image message and exact
attachment with Chatwoot's native guarded API. OCR/vision fields are UNTRUSTED
candidates only. This action cannot mark paid, verify bank credit, post a
Salla order or tell an accounting/fulfillment workflow to proceed.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

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
    source_attachment_id: int = Field(ge=0)
    source_kind: Literal["image", "file", "text"] = "image"
    source_id_sha256: str = Field(pattern=DIGEST)
    selected_bank_id: int = Field(gt=0)
    visible: ReceiptVisibleFields

    @model_validator(mode="after")
    def require_canonical_attachment(self):
        # Customer TEXT has no attachment. An uploaded image/PDF/document
        # always has the exact original Chatwoot attachment ID. Reject
        # accidental or adversarial source-kind/attachment substitution.
        if ((self.source_kind == "text" and self.source_attachment_id != 0)
                or (self.source_kind != "text" and
                    self.source_attachment_id <= 0)):
            raise ValueError("bank_receipt_source_kind_attachment_mismatch")
        return self


def verify_bank_receipt_action(
    raw: bytes, stamp: str, delivery: str, signature: str, secret: bytes,
    *, now: int | None = None,
) -> SignedBankReceiptStage:
    # First verify HMAC, anti-replay clock and duplicate JSON-key rejection.
    verify_signed_json(raw, stamp, delivery, signature, secret, now=now)
    # Strict Pydantic JSON mode accepts ISO-8601 timestamps as JSON strings
    # while enforcing the typed timestamp field; Python-object strict mode
    # rejects every genuine serialized datetime.
    try:
        return SignedBankReceiptStage.model_validate_json(raw)
    except ValidationError as exc:
        raise SignatureDenied("invalid_bank_receipt_screening_action") from exc
