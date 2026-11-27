"""Immutable customer bank-receipt CLAIM awaiting HUMAN bank-credit review.

The customer's WhatsApp image and a private visual preflight may suggest the
receipt looks valid, never that money landed in the merchant bank. Keep the
original image inside the scoped Chatwoot conversation; Mezan stores only
opaque attachment/message IDs and hashes, no public URL or banking PII.

Mongo unique _id binds one order intent; a separately operator-installed
unique (user_id, image_sha256) index blocks the same screenshot in other
intents. A TTL index is required for retention. No GL, Qoyod, paid state,
fulfillment or Salla order POST here.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any

from .bank_receipt import (
    MerchantBankFact, ReceiptVisionEvidence, ReceiptAppearanceDenied,
    validate_receipt_appearance,
)
from .service import intent_storage_id

SCREENSHOT_UNIQUE = "bank_claim_unique_image_per_owner_v1"
REVIEW_TTL = "bank_claim_purge_ttl_v1"


class BankReviewDenied(ValueError):
    pass


def claim_storage_id(owner_id: str, intent_id: str) -> str:
    anchor = intent_storage_id(owner_id, intent_id)
    return hashlib.sha256(f"bank-receipt:{anchor}".encode()).hexdigest()


def claim_reference(owner_id: str, intent_id: str) -> str:
    return "bank-review." + claim_storage_id(owner_id, intent_id)[:32]


async def require_bank_review_indexes(db: Any) -> None:
    try:
        indexes = await db.salla_ai_bank_receipt_reviews.index_information()
    except Exception as exc:
        raise BankReviewDenied("bank_receipt_indexes_unavailable") from exc
    if type(indexes) is not dict:
        raise BankReviewDenied("bank_receipt_indexes_unavailable")
    unique = indexes.get(SCREENSHOT_UNIQUE)
    expire = indexes.get(REVIEW_TTL)
    if (not isinstance(unique, dict) or unique.get("unique") is not True or
            list(unique.get("key") or []) !=
            [("user_id", 1), ("image_sha256", 1)]):
        raise BankReviewDenied("bank_receipt_duplicate_guard_missing")
    if (not isinstance(expire, dict) or expire.get("expireAfterSeconds") != 0 or
            list(expire.get("key") or []) != [("purge_after", 1)]):
        raise BankReviewDenied("bank_receipt_ttl_missing")


async def install_bank_review_indexes(db: Any) -> None:
    """Explicit reviewed migration only, never called by request handlers."""
    col=db.salla_ai_bank_receipt_reviews
    await col.create_index(
        [("user_id", 1), ("image_sha256", 1)], unique=True,
        name=SCREENSHOT_UNIQUE,
    )
    await col.create_index(
        [("purge_after", 1)], expireAfterSeconds=0, name=REVIEW_TTL,
    )
    await require_bank_review_indexes(db)


async def register_bank_receipt_claim(
    db: Any, *,
    user_id: str, intent_id: str, account_id: int, inbox_id: int,
    conversation_id: int, contact_id: int, message_id: int,
    attachment_id: int, bank: MerchantBankFact,
    visual: ReceiptVisionEvidence, now: datetime | None = None,
) -> dict:
    clock = now or datetime.now(timezone.utc)
    if clock.tzinfo is None:
        raise BankReviewDenied("bank_claim_server_clock_unverified")
    clock=clock.astimezone(timezone.utc)
    if (not user_id or any(type(k) is not int or k < 1 for k in (
            account_id,inbox_id,conversation_id,contact_id,
            message_id,attachment_id
    ))):
        raise BankReviewDenied("bank_claim_customer_source_unverified")
    if bank.user_id != user_id:
        raise BankReviewDenied("bank_claim_cross_merchant")
    await require_bank_review_indexes(db)
    try:
        validated = validate_receipt_appearance(
            visual, merchant_id=user_id, verified_bank=bank, now=clock,
        )
    except ReceiptAppearanceDenied as exc:
        raise BankReviewDenied(str(exc)) from None

    key=claim_storage_id(user_id,intent_id)
    doc={
        "_id":key, "review_ref":claim_reference(user_id,intent_id),
        "user_id":user_id,"intent_id":intent_id,
        "account_id":account_id,"inbox_id":inbox_id,
        "conversation_id":conversation_id,"contact_id":contact_id,
        "source_message_id":message_id,"source_attachment_id":attachment_id,
        "image_sha256":validated["source_image_sha256"],
        "bank_reference_sha256":validated["bank_reference_sha256"],
        "store_bank_id":validated["store_bank_id"],
        "receipt_amount_sar":validated["receipt_amount_sar"],
        "receipt_transfer_at_utc":validated["receipt_transfer_at_utc"],
        "appearance":"plausible_for_staff_review",
        "state":"visual_preflight_accepted",
        "human_bank_credit_verified":False,
        "payment_marked_paid":False,
        "fulfillment_released":False,
        "salla_order_id":None,"salla_reference_id":None,
        "salla_actual_total_sar":None,
        "amount_matches_salla":None,
        "salla_review_status":None,
        "created_at":clock,"updated_at":clock,
        "purge_after":clock+timedelta(days=90),
    }
    try:
        await db.salla_ai_bank_receipt_reviews.insert_one(doc)
    except Exception:
        prev=await db.salla_ai_bank_receipt_reviews.find_one({
            "_id":key,"user_id":user_id,
        })
        if prev is None:
            # Duplicate picture belonging to another order, or database
            # failure: never silently accept or retry.
            raise BankReviewDenied("bank_receipt_image_duplicate_or_persistence_error")
        for field in (
            "account_id","inbox_id","conversation_id","contact_id",
            "source_message_id","source_attachment_id",
            "image_sha256","bank_reference_sha256","store_bank_id",
            "receipt_amount_sar",
        ):
            if prev.get(field)!=doc[field]:
                raise BankReviewDenied("bank_receipt_claim_intent_conflict")
        return {
            "state":"awaiting_order_creation",
            "review_ref":doc["review_ref"],
            "replayed":True,
            "bank_credit_verified":False,
        }
    return {
        "state":"awaiting_order_creation",
        "review_ref":doc["review_ref"],
        "replayed":False,
        "bank_credit_verified":False,
    }


async def get_claim_for_order(
    db: Any, *, user_id: str, intent_id: str, review_ref: str,
    inbox_id: str, conversation_id: str, contact_id: int,
) -> dict:
    if not review_ref or type(contact_id) is not int or contact_id<=0:
        raise BankReviewDenied("bank_claim_reference_required")
    row=await db.salla_ai_bank_receipt_reviews.find_one({
        "_id":claim_storage_id(user_id,intent_id),
        "user_id":user_id,"intent_id":intent_id,
        "review_ref":review_ref,
        "inbox_id":int(inbox_id),"conversation_id":int(conversation_id),
        "contact_id":contact_id,
    })
    if not isinstance(row,dict) or row.get("state") not in (
        "visual_preflight_accepted","pending_mezan_sync",
        "awaiting_human_bank_review","amount_mismatch_review",
    ):
        raise BankReviewDenied("bank_receipt_claim_not_ready")
    if (row.get("human_bank_credit_verified") is not False or
            row.get("payment_marked_paid") is not False or
            row.get("fulfillment_released") is not False):
        raise BankReviewDenied("bank_claim_financial_state_changed")
    return row
