"""A single private order field from native Chatwoot, never model-proposed.

A distinct purpose-specific HMAC authorizes one value sourced from the latest
canonical customer message. Mezan stores it encrypted with a seven-day TTL.
No Salla order creation, checkout URL or financial status can be submitted.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .private_fields import allowed_private_field_name
from .signature import ID, DIGEST, SignatureDenied, verify_signed_json


class SignedPrivateOrderField(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    action: Literal["salla.store_private_order_field"]
    account_id: int = Field(gt=0)
    inbox_id: int = Field(gt=0)
    conversation_id: int = Field(gt=0)
    intent_id: str = Field(pattern=ID)
    source_message_id: int = Field(gt=0)
    source_contact_id: int = Field(gt=0)
    source_text_sha256: str = Field(pattern=DIGEST)
    field: str = Field(min_length=3, max_length=40)
    value: str = Field(min_length=1, max_length=180)


def verify_private_field(
    raw: bytes, timestamp: str, delivery: str, signature: str, secret: bytes,
    *, now: int | None = None,
) -> SignedPrivateOrderField:
    parsed = verify_signed_json(
        raw, timestamp, delivery, signature, secret, now=now,
    )
    try:
        out = SignedPrivateOrderField.model_validate(parsed)
    except ValidationError as exc:
        raise SignatureDenied("invalid_private_field_action") from exc
    if not allowed_private_field_name(out.field):
        raise SignatureDenied("field_not_allowlisted")
    return out
