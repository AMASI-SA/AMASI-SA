"""Queue a bank-transfer WhatsApp receipt for real human settlement review.

This is intentionally NOT `bank_transfer_review_routes.confirm`: that older
route posts a bank GL entry when a reviewer clicks confirm. We neither call
it nor duplicate its accounting semantics. This collection tracks customer
order/receipt evidence only, with an explicit human-review lifecycle.

PII: never store a receipt URL, IBAN, bank account, card or raw screenshot.
Staff may retrieve the original attachment from the existing scoped Chatwoot
conversation. Staging may link to Salla only after the order is confirmed
and the merchant's status capability is verified.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from .bank_receipt_review import (
    PrivateReceiptObservation, assess_bank_receipt_appearance,
    receipt_review_key, ReceiptReviewDenied,
)
from .service import intent_storage_id, quote_pricing_mode


class BankReviewStateError(ValueError):
    pass


async def enqueue_bank_receipt_review(
    db: Any, *,
    owner_id: str, intent_id: str,
    account_id: int, inbox_id: int, conversation_id: int,
    contact_id: int,
    observation: PrivateReceiptObservation,
    order_total_sar: str | None,
    now: datetime | None = None,
) -> dict:
    """One review per real customer message + exact Salla order.

    Human staff must verify the credit in the target bank account. Even an
    apparently perfect screenshot has `payment_confirmed=False`.
    """
    if (not owner_id or not intent_id or
            type(account_id) is not int or account_id < 1 or
            type(inbox_id) is not int or inbox_id < 1 or
            type(conversation_id) is not int or conversation_id < 1 or
            type(contact_id) is not int or contact_id < 1 or
            not isinstance(observation, PrivateReceiptObservation) or
            (observation.account_id, observation.inbox_id,
             observation.conversation_id, observation.contact_id) != (
                account_id, inbox_id, conversation_id, contact_id)):
        raise BankReviewStateError("trusted_bank_receipt_context_required")
    timestamp = now or datetime.now(timezone.utc)
    if timestamp.tzinfo is None:
        raise BankReviewStateError("utc_bank_review_clock_required")
    timestamp = timestamp.astimezone(timezone.utc)

    key = intent_storage_id(owner_id, intent_id)
    order_intent = await db.salla_ai_order_intents.find_one({
        "_id": key, "user_id": owner_id,
        "inbox_id": str(inbox_id),
        "conversation_id": str(conversation_id),
    })
    if (not isinstance(order_intent, dict) or
            order_intent.get("state") not in (
                "created", "created_amount_unverified",
            ) or order_intent.get("pricing_mode") != "salla_bank_review"
            or type(order_intent.get("salla_order_id")) is not int or
            order_intent["salla_order_id"] <= 0):
        raise BankReviewStateError("confirmed_unpaid_bank_order_required")
    quote = await db.salla_ai_order_quotes.find_one({
        "user_id": owner_id, "intent_id": intent_id,
        "inbox_id": str(inbox_id),
        "conversation_id": str(conversation_id),
    })
    if (quote_pricing_mode(quote) != "salla_bank_review" or
            quote.get("source_contact_id") != contact_id or
            quote.get("payload_digest") != order_intent.get("payload_digest")):
        raise BankReviewStateError("bank_quote_customer_mismatch")

    appearance = assess_bank_receipt_appearance(
        observation, expected_total_sar=order_total_sar, now=timestamp,
    )
    row_id = receipt_review_key(
        owner_id, order_intent["salla_order_id"], observation.message_id,
    )
    row = {
        "_id": row_id,
        "user_id": owner_id, "intent_id": intent_id,
        "account_id": account_id,
        "inbox_id": inbox_id,
        "conversation_id": conversation_id,
        "contact_id": contact_id,
        "salla_order_id": order_intent["salla_order_id"],
        "reference_id": order_intent.get("reference_id"),
        "source_message_id": observation.message_id,
        "attachment_id": observation.attachment_id,
        "attachment_source_hash": observation.attachment_source_hash,
        "source": "trusted_private_receipt_inspector_v1",
        "appearance": appearance["appearance"],
        "issues": appearance["issues"],
        "status": "awaiting_human_bank_confirmation",
        "bank_settlement_confirmed": False,
        "salla_paid": False,
        "gl_posted": False,
        "reviewed_by": None,
        "reviewed_at": None,
        "order_total_sar": (
            format(Decimal(order_total_sar), ".2f")
            if "salla_final_amount_not_yet_synced" not in appearance["issues"]
            else None
        ),
        "created_at": timestamp,
        "updated_at": timestamp,
    }
    try:
        await db.salla_ai_bank_receipt_reviews.insert_one(row)
    except Exception:
        prev = await db.salla_ai_bank_receipt_reviews.find_one({
            "_id": row_id,
        })
        if not isinstance(prev, dict):
            raise
        # Same receipt may arrive as duplicate webhook. Do not roll back a
        # human decision or duplicate staff review on replay.
        if any(prev.get(field) != row[field] for field in (
            "user_id", "intent_id", "account_id", "inbox_id",
            "conversation_id", "contact_id", "salla_order_id",
            "source_message_id", "attachment_id",
            "attachment_source_hash",
        )):
            raise BankReviewStateError("bank_receipt_replay_conflict")
        return {
            "state": prev.get("status"),
            "review_id": row_id,
            "replayed": True,
            "payment_confirmed": False,
            "customer_message": "transfer_receipt_under_human_review",
        }

    return {
        "state": "awaiting_human_bank_confirmation",
        "review_id": row_id,
        "appearance": appearance["appearance"],
        "issues": appearance["issues"],
        "replayed": False,
        "payment_confirmed": False,
        "customer_message": "transfer_receipt_under_human_review",
    }
