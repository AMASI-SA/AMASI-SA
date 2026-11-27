"""Signed, PII-free request to prepare an order from Mezan's private vault.

The AI/Chatwoot client sends ONLY verified catalog selection, Salla numeric
region/carrier IDs, scoped conversation/contact and chosen online method.
Buyer/recipient contact, email, street and coordinates are decrypted inside
Mezan's private field vault, NEVER read back to ChatGPT.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .payload import Product, PaymentMethod
from .signature import ID, SignatureDenied, verify_signed_json


class SignedPrivateVaultPrepare(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    action: Literal["salla.prepare_from_private_vault"]
    account_id: int = Field(gt=0)
    inbox_id: int = Field(gt=0)
    conversation_id: int = Field(gt=0)
    intent_id: str = Field(pattern=ID)
    source_message_id: int = Field(gt=0)
    source_contact_id: int = Field(gt=0)
    receiver_country_code: Literal["SA", "QA", "KW", "AE", "BH", "OM"]
    country_id: int = Field(gt=0)
    city_id: int = Field(gt=0)
    district_id: int = Field(gt=0)
    courier_id: int = Field(gt=0)
    selected_rate_id: int = Field(gt=0)
    branch_id: int | None = Field(default=None, gt=0)
    payment_flow: Literal["online", "bank_review"] = "online"
    accepted_methods: list[PaymentMethod] = Field(min_length=1, max_length=2)
    products: list[Product] = Field(min_length=1, max_length=50)


def verify_private_vault_prepare(
    raw: bytes, stamp: str, delivery: str, signature: str, secret: bytes,
    *, now: int | None = None,
) -> SignedPrivateVaultPrepare:
    parsed = verify_signed_json(
        raw, stamp, delivery, signature, secret, now=now,
    )
    try:
        return SignedPrivateVaultPrepare.model_validate(parsed)
    except ValidationError as exc:
        raise SignatureDenied("invalid_private_vault_prepare") from exc
