"""Receive a signed, scoped Chatwoot customer confirmation as a Mezan proof.

No Salla POST occurs here. Only the independent Chatwoot native worker may
sign this action AFTER re-reading the latest bot-owned public customer
message and verifying the exact current quote. Models never hold the secret.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .service import _is_verified_quote, _utc
from .signature import DIGEST, ID, REF, SignatureDenied, verify_signed_json


class ConfirmationDenied(ValueError):
    def __init__(self, reason: str, status_code: int = 409):
        self.status_code = status_code
        super().__init__(reason)


class SignedCustomerConfirmation(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    action: Literal["salla.confirm_pending_order"]
    account_id: int = Field(gt=0)
    inbox_id: int = Field(gt=0)
    conversation_id: int = Field(gt=0)
    intent_id: str = Field(pattern=ID)
    quote_ref: str = Field(pattern=REF)
    quote_digest: str = Field(pattern=DIGEST)
    quote_version: int = Field(gt=0)
    confirmation_message_id: int = Field(gt=0)
    message_created_at: int = Field(gt=0)
    source_id_sha256: str = Field(pattern=DIGEST)


def verify_confirmation(
    raw: bytes, timestamp: str, delivery: str, signature: str,
    secret: bytes, *, now: int | None = None,
) -> SignedCustomerConfirmation:
    payload = verify_signed_json(
        raw, timestamp, delivery, signature, secret, now=now
    )
    try:
        return SignedCustomerConfirmation.model_validate(payload)
    except ValidationError as exc:
        raise SignatureDenied("invalid_customer_confirmation") from exc


def _id(user_id: str, intent_id: str) -> str:
    return hashlib.sha256(
        f"assisted-confirmation:{user_id}:{intent_id}".encode("utf-8")
    ).hexdigest()


async def persist_signed_confirmation(
    db: Any, *,
    owner_id: str, expected_account_id: int, expected_inbox_id: int,
    action: SignedCustomerConfirmation, now: datetime | None = None,
) -> dict:
    """Immutable proof with one Mongo _id per tenant and order intent."""
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        raise ConfirmationDenied("invalid_confirmation_clock")
    moment = moment.astimezone(timezone.utc)
    if not owner_id or not (expected_account_id > 0 and expected_inbox_id > 0):
        raise ConfirmationDenied("missing_confirmed_tenant", 503)
    if (action.account_id != expected_account_id or
            action.inbox_id != expected_inbox_id):
        raise ConfirmationDenied("outside_approved_inbox", 403)

    # Rejection happens BEFORE any record is written. Trust neither model
    # text nor an ordinary request field as permission to send an order.
    quote = await db.salla_ai_order_quotes.find_one({
        "user_id": owner_id, "intent_id": action.intent_id,
        "inbox_id": str(action.inbox_id),
        "conversation_id": str(action.conversation_id),
        "quote_ref": action.quote_ref,
    })
    if not _is_verified_quote(
        quote, digest=action.quote_digest, user_id=owner_id,
        intent_id=action.intent_id, inbox_id=str(action.inbox_id),
        conversation_id=str(action.conversation_id), now=moment
    ) or quote.get("version") != action.quote_version:
        raise ConfirmationDenied("current_verified_quote_required")

    message_time = datetime.fromtimestamp(
        action.message_created_at, tz=timezone.utc
    )
    verified_at = quote["verified_at"].astimezone(timezone.utc)
    # Delayed messages, pre-quote approval or future message times
    # cannot authorize creating a new customer order.
    if (message_time < verified_at or
            message_time > moment or
            (moment - message_time).total_seconds() > 300):
        raise ConfirmationDenied("stale_or_unrelated_customer_approval")

    key = _id(owner_id, action.intent_id)
    identity = {
        "user_id": owner_id,
        "intent_id": action.intent_id,
        "inbox_id": str(action.inbox_id),
        "conversation_id": str(action.conversation_id),
        "payload_digest": action.quote_digest,
        "quote_version": action.quote_version,
        "message_id": str(action.confirmation_message_id),
        "provider_source_sha256": action.source_id_sha256,
    }
    record = {
        "_id": key, **identity, "confirmed": True,
        "source": "chatwoot_customer_inbound_v1",
        "confirmed_at": moment,
        "created_at": moment,
    }
    try:
        # The default unique Mongo _id index protects all workers from
        # recording two distinct approvals for the same intent.
        await db.salla_ai_order_confirmations.insert_one(record)
    except Exception:
        prev = await db.salla_ai_order_confirmations.find_one({"_id": key})
        if prev is None:
            # Persistence failure, NOT an assumed duplicate.
            raise
        if any(prev.get(field) != value for field, value in identity.items()):
            raise ConfirmationDenied("incompatible_confirmation_replay")
        return {
            "state": "recorded", "replayed": True,
            "confirmation_message_id": action.confirmation_message_id,
        }
    return {
        "state": "recorded", "replayed": False,
        "confirmation_message_id": action.confirmation_message_id,
    }
