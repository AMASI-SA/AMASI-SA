"""Immutable preliminary WhatsApp bank-receipt evidence and human review hold.

A plausible-looking receipt is NOT proof of bank credit. Never mark paid, send
a payment capture, trigger order fulfillment or write accounting entries here.

All documents contain only scoped message/attachment IDs, digest, provider bank
ID, claimed amount and preliminary flags; raw receipt media and bank reference
are NOT stored in these collections. Staff re-open the protected original
Chatwoot attachment through the existing authorized customer-service system.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any

from .service import intent_storage_id


class BankReviewDenied(ValueError):
    pass


def _key(owner: str, intent: str) -> str:
    return hashlib.sha256(
        ("bank-review:" + intent_storage_id(owner, intent)).encode("utf-8")
    ).hexdigest()


async def save_preliminary_bank_receipt(
    db: Any, *, owner_id: str, intent_id: str, account_id: int,
    inbox_id: int, conversation_id: int, contact_id: int,
    source_message_id: int, attachment_id: int,
    assessment: dict, now: datetime | None = None,
) -> dict:
    """Atomic reference claim + immutable receipt record; never bank deposit."""
    if (not isinstance(assessment, dict) or
            assessment.get("state") != "preliminary_pass_for_human_review" or
            assessment.get("visual_only") is not True or
            assessment.get("bank_credit_confirmed") is not False or
            assessment.get("human_review_required") is not True):
        raise BankReviewDenied("verified_private_visual_receipt_required")
    if not all(type(x) is int and x > 0 for x in (
        account_id, inbox_id, conversation_id, contact_id,
        source_message_id, attachment_id,
    )):
        raise BankReviewDenied("canonical_chatwoot_receipt_origin_required")
    bank_id = assessment.get("store_bank_id")
    amount = assessment.get("claimed_amount_sar")
    source_digest = assessment.get("image_sha256")
    reference_hash = assessment.get("reference_sha256")
    if (type(bank_id) is not int or bank_id < 1 or
            type(amount) is not str or
            type(source_digest) is not str or len(source_digest) != 64 or
            type(reference_hash) is not str or len(reference_hash) != 64):
        raise BankReviewDenied("invalid_receipt_evidence")
    now = now or datetime.now(timezone.utc)
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise BankReviewDenied("utc_receipt_clock_required")

    record_id = _key(owner_id, intent_id)
    # Claim a transfer reference once across all assisted intents for this
    # merchant/bank. Two different orders cannot reuse the same screenshot
    # reference while staff are reviewing a claimed bank payment.
    reference_claim = hashlib.sha256(
        f"{owner_id}:{bank_id}:{reference_hash}".encode("utf-8")
    ).hexdigest()
    claim = {
        "_id": reference_claim, "owner_id": owner_id, "intent_id": intent_id,
        "bank_id": bank_id, "source": "bank_receipt_reference_claim",
        "created_at": now,
    }
    try:
        await db.salla_ai_bank_receipt_claims.insert_one(claim)
    except Exception:
        previous = await db.salla_ai_bank_receipt_claims.find_one({
            "_id": reference_claim,
        })
        if (not isinstance(previous, dict) or
                previous.get("owner_id") != owner_id or
                previous.get("intent_id") != intent_id or
                previous.get("bank_id") != bank_id):
            raise BankReviewDenied("receipt_reference_reused_for_other_order")

    document = {
        "_id": record_id, "owner_id": owner_id, "intent_id": intent_id,
        "account_id": account_id, "inbox_id": inbox_id,
        "conversation_id": conversation_id, "contact_id": contact_id,
        "source_message_id": source_message_id,
        "attachment_id": attachment_id,
        "image_sha256": source_digest, "reference_sha256": reference_hash,
        "store_bank_id": bank_id, "claimed_amount_sar": amount,
        "claimed_transfer_date": assessment["claimed_transfer_date"],
        "state": "preliminary_pass_for_human_review",
        "bank_credit_confirmed": False,
        "human_review_required": True, "created_at": now,
    }
    try:
        await db.salla_ai_bank_receipt_preflights.insert_one(document)
    except Exception:
        previous = await db.salla_ai_bank_receipt_preflights.find_one({
            "_id": record_id,
        })
        comparable = (
            "owner_id", "intent_id", "account_id", "inbox_id",
            "conversation_id", "contact_id", "source_message_id",
            "attachment_id", "image_sha256", "reference_sha256",
            "store_bank_id", "claimed_amount_sar",
        )
        if (not isinstance(previous, dict) or
                any(previous.get(k) != document[k] for k in comparable)):
            raise BankReviewDenied("receipt_intent_conflict")
    return {
        "state": "preliminary_pass_for_human_review",
        "receipt_ref": "receipt." + record_id[:32],
        "intent_id": intent_id,
        "bank_credit_confirmed": False,
        "human_review_required": True,
    }


async def ensure_pending_bank_review(
    db: Any, *, owner_id: str, intent_id: str, order_id: int,
    reference_id: int | None,
    now: datetime | None = None,
) -> dict:
    """Attach a confirmed Salla-created order to a HUMAN-ONLY receipt review."""
    if type(order_id) is not int or order_id < 1:
        raise BankReviewDenied("salla_order_id_required")
    record_id = _key(owner_id, intent_id)
    evidence = await db.salla_ai_bank_receipt_preflights.find_one({
        "_id": record_id, "owner_id": owner_id, "intent_id": intent_id,
    })
    if (not isinstance(evidence, dict) or
            evidence.get("state") != "preliminary_pass_for_human_review" or
            evidence.get("bank_credit_confirmed") is not False):
        raise BankReviewDenied("receipt_preflight_not_verified")
    timepoint = now or datetime.now(timezone.utc)
    if timepoint.tzinfo is None:
        raise BankReviewDenied("utc_bank_review_clock_required")
    document = {
        "_id": record_id, "owner_id": owner_id, "intent_id": intent_id,
        "account_id": evidence["account_id"],
        "inbox_id": evidence["inbox_id"],
        "conversation_id": evidence["conversation_id"],
        "contact_id": evidence["contact_id"],
        "source_message_id": evidence["source_message_id"],
        "attachment_id": evidence["attachment_id"],
        "store_bank_id": evidence["store_bank_id"],
        "receipt_image_sha256": evidence["image_sha256"],
        "receipt_reference_sha256": evidence["reference_sha256"],
        "claimed_amount_sar": evidence["claimed_amount_sar"],
        "salla_order_id": order_id, "salla_reference_id": reference_id,
        "state": "awaiting_human_bank_review",
        "bank_credit_confirmed": False,
        "payment_marked_paid": False, "gl_posted": False,
        "fulfillment_released": False,
        "created_at": timepoint,
    }
    try:
        await db.salla_ai_bank_receipt_reviews.insert_one(document)
    except Exception:
        previous = await db.salla_ai_bank_receipt_reviews.find_one({
            "_id": record_id,
        })
        if (not isinstance(previous, dict) or
                previous.get("owner_id") != owner_id or
                previous.get("intent_id") != intent_id or
                previous.get("salla_order_id") != order_id or
                previous.get("store_bank_id") != evidence["store_bank_id"]):
            raise BankReviewDenied("different_salla_order_review_conflict")
    return {
        "state": "awaiting_human_bank_review",
        "salla_order_id": order_id,
        "reference_id": reference_id,
        "bank_credit_confirmed": False,
        "fulfillment_released": False,
    }
