"""Purpose-bound signed intake for source-verified UNPAID order preparation.

The sender is the trusted native Chatwoot service (NOT the language model).
It may request Mezan to verify products/variants, courier eligibility and
electronic payment methods, but it may not assert a final price, payment,
customer confirmation, or Salla order creation.

No real order is created here. Caller-origin details must be independently
checked against the customer conversation by the native worker.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .payload import PendingOrder
from .signature import ID, SignatureDenied, verify_signed_json


class SignedPrepareUnpaidOrder(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    action: Literal["salla.prepare_unpaid_order"]
    account_id: int = Field(gt=0)
    inbox_id: int = Field(gt=0)
    conversation_id: int = Field(gt=0)
    intent_id: str = Field(pattern=ID)
    source_message_id: int = Field(gt=0)
    source_contact_id: int = Field(gt=0)
    selected_rate_id: int = Field(gt=0)
    order: PendingOrder


def verify_prepare_unpaid_order(
    raw: bytes,
    timestamp: str,
    delivery: str,
    signature: str,
    secret: bytes,
    *,
    now: int | None = None,
) -> SignedPrepareUnpaidOrder:
    signed = verify_signed_json(
        raw, timestamp, delivery, signature, secret, now=now,
    )
    try:
        message = SignedPrepareUnpaidOrder.model_validate(signed)
    except ValidationError as exc:
        raise SignatureDenied("invalid_unpaid_order_preparation") from exc

    # The trusted Chatwoot bot must use a customer identity from an
    # authoritative source. For initial signed service pilot, reject a
    # caller-asserted unrelated Salla customer ID. Returning customers
    # need a separate tenant-scoped customer-ID/mobile matching contract.
    if message.order.customer.id is not None:
        raise SignatureDenied("signed_customer_id_requires_verified_lookup")
    # For an actual new Salla customer the API needs real name/mobile/email;
    # the server may not synthesize email or use recipient gift details as buyer.
    if not (message.order.customer.name and
            message.order.customer.mobile and
            message.order.customer.email):
        raise SignatureDenied("actual_new_buyer_identity_required")
    return message
