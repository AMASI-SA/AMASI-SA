"""Purpose-bound private WhatsApp BANK order preparation, no PII or payment claim."""

from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .payload import Product
from .signature import ID, SignatureDenied, verify_signed_json


class SignedBankVaultPrepare(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    action: Literal["salla.prepare_bank_pending_review"]
    account_id: int = Field(gt=0)
    inbox_id: int = Field(gt=0)
    conversation_id: int = Field(gt=0)
    intent_id: str = Field(pattern=ID)
    source_contact_id: int = Field(gt=0)
    source_message_id: int = Field(gt=0)
    receiver_country_code: Literal["SA", "QA", "KW", "AE", "BH", "OM"]
    country_id: int = Field(gt=0)
    city_id: int = Field(gt=0)
    district_id: int = Field(gt=0)
    courier_id: int = Field(gt=0)
    selected_rate_id: int = Field(gt=0)
    branch_id: int | None = Field(default=None, gt=0)
    products: list[Product] = Field(min_length=1, max_length=50)


def verify_bank_vault_prepare(
    raw: bytes, stamp: str, delivery: str, signature: str, secret: bytes,
    *, now: int | None = None,
) -> SignedBankVaultPrepare:
    body = verify_signed_json(
        raw, stamp, delivery, signature, secret, now=now,
    )
    try:
        return SignedBankVaultPrepare.model_validate(body)
    except ValidationError as exc:
        raise SignatureDenied("invalid_bank_pending_review_action") from exc
