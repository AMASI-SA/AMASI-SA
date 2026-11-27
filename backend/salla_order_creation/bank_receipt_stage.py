"""Persist a STRICTLY preliminary bank-transfer receipt screening result.

Only a signed, canonically verified Chatwoot current customer image event can
submit OCR/vision *candidates*. Mezan independently compares candidate
beneficiary IBAN/name to an active Salla store receiving bank. Neither this
module nor the model verifies money arrived in the merchant's bank.

Only redacted metadata and HMAC fingerprints are persisted. Raw attachment
stays in private Chatwoot; raw IBAN/reference never stored in this ledger.
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Awaitable, Callable

from .bank_receipt_review import (
    ReceiptVisibleFields, ReceiptAppearanceDenied, inspect_visible_bank_receipt,
)
from .bank_source import read_active_salla_payment_bank
from .service import intent_storage_id


class BankReceiptStageDenied(ValueError):
    pass


def _digest(secret: bytes, owner: str, bank_id: int, ref: str) -> str:
    if not isinstance(secret, bytes) or len(secret) < 32:
        raise BankReceiptStageDenied("bank_receipt_fingerprint_secret_missing")
    if not owner or not isinstance(ref, str) or not ref.strip():
        raise BankReceiptStageDenied("bank_receipt_reference_unavailable")
    body = f"{owner}\x00{bank_id}\x00{ref.strip().upper()}".encode("utf-8")
    return hmac.new(secret, body, hashlib.sha256).hexdigest()


async def stage_bank_receipt(
    db: Any, *,
    user_id: str,
    intent_id: str,
    account_id: int, inbox_id: int, conversation_id: int,
    contact_id: int, message_id: int, attachment_id: int,
    source_id_sha256: str,
    selected_bank_id: int,
    visible: ReceiptVisibleFields,
    reference_secret: bytes,
    bank_reader: Callable[..., Awaitable[dict]] = read_active_salla_payment_bank,
    enabled: bool = False,
    now: datetime | None = None,
) -> dict:
    """Store first plausible receipt once, no Salla POST and never mark paid."""
    if not enabled:
        raise BankReceiptStageDenied("bank_receipt_review_disabled")
    if (not user_id or not all(type(x) is int and x > 0 for x in (
            account_id, inbox_id, conversation_id, contact_id, message_id,
            attachment_id, selected_bank_id,
        )) or not isinstance(source_id_sha256, str)
            or len(source_id_sha256) != 64
            or any(c not in "0123456789abcdef" for c in source_id_sha256)):
        raise BankReceiptStageDenied("canonical_customer_image_source_required")
    intent_storage_id(user_id, intent_id)
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        raise BankReceiptStageDenied("trusted_receipt_clock_required")
    moment = moment.astimezone(timezone.utc)

    # Amount shown in a screenshot is a claim, NOT money received.
    # This bank read is scoped to the merchant's installed Salla payments.read.
    source = await bank_reader(db, user_id, selected_bank_id)
    assessment = inspect_visible_bank_receipt(
        visible, salla_bank=source, checked_at=moment, now=moment,
    )
    if assessment["state"] != "appearance_plausible":
        return {
            "state": "needs_customer_clarification",
            "issues": assessment["flags"],
            "bank_credit_confirmed": False,
            "paid": False,
            "order_created": False,
        }
    reference = visible.transfer_reference
    reference_hash = _digest(reference_secret, user_id, selected_bank_id, reference)
    key = hashlib.sha256(
        f"bank-receipt:{user_id}:{intent_id}".encode("utf-8")
    ).hexdigest()
    amount_sar = assessment["receipt_amount_sar"]
    core = {
        "user_id": user_id, "intent_id": intent_id,
        "account_id": account_id, "inbox_id": str(inbox_id),
        "conversation_id": str(conversation_id),
        "source_contact_id": contact_id, "source_message_id": message_id,
        "source_attachment_id": attachment_id,
        "source_id_sha256": source_id_sha256,
        "bank_id": selected_bank_id,
        "reference_fingerprint": reference_hash,
        "receipt_amount_sar": amount_sar,
    }
    record = {
        "_id": key, **core,
        "state": "appearance_plausible",
        "source": "chatwoot_verified_image_bank_receipt_v1",
        "credit_received": False, "staff_review_required": True,
        "created_at": moment, "expires_at": moment + timedelta(hours=2),
        "purge_after": moment + timedelta(days=7),
    }

    # HMAC transfer reference reuse fence avoids attaching the same receipt
    # to multiple order intents, without storing its full reference.
    unique = {"_id": reference_hash, "user_id": user_id,
              "intent_id": intent_id, "bank_id": selected_bank_id}
    try:
        await db.salla_ai_bank_receipt_refs.insert_one(unique)
    except Exception:
        prior = await db.salla_ai_bank_receipt_refs.find_one({
            "_id": reference_hash,
        })
        if not isinstance(prior, dict) or prior.get("user_id") != user_id or (
                prior.get("intent_id") != intent_id or
                prior.get("bank_id") != selected_bank_id):
            raise BankReceiptStageDenied("receipt_reference_already_claimed")

    try:
        await db.salla_ai_bank_receipt_evidence.insert_one(record)
    except Exception:
        prior = await db.salla_ai_bank_receipt_evidence.find_one({"_id": key})
        if not isinstance(prior, dict) or any(
            prior.get(k) != v for k,v in core.items()
        ):
            raise BankReceiptStageDenied("conflicting_receipt_on_same_intent")
        expiry = prior.get("expires_at")
        if not isinstance(expiry, datetime):
            raise BankReceiptStageDenied("receipt_record_expiration_missing")
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        if expiry <= moment:
            raise BankReceiptStageDenied("receipt_evidence_expired")
    return {
        "state": "appearance_plausible",
        "receipt_ref": "receipt." + key[:32],
        "receipt_amount_sar": amount_sar,
        "bank_credit_confirmed": False,
        "paid": False,
        "staff_review_required": True,
        "order_created": False,
    }
