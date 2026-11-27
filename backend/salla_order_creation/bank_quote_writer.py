"""Source-verified bank-transfer order intent, NEVER paid or settled.

Requires a private preliminary WhatsApp receipt assessment, active Salla
receiving bank and available Bank Transfer method, actual Salla product
options/stock and eligible shipping. The claimed receipt amount is *not*
used as a final payable quote. Salla computes actual total after the unpaid
order is created; a customer support reviewer independently checks bank credit.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable
from uuid import uuid4

from .bank_receipt_review import _key as receipt_key
from .bank_transfer import (
    BankSelectionDenied,
    bank_order_digest,
    get_verified_receiving_bank,
)
from .catalog_validation import CartUnverified, validate_selected_cart
from .payload import PendingOrder
from .quote_cipher import EncryptedOrderUnavailable, encrypt_order_payload
from .readers import (
    SourceUnavailable, read_checkout_payment_methods, read_exact_product,
    read_shipping_rate_candidates,
)
from .shipping_selection import ShippingSelectionUnverified, validate_shipping_selection
from .service import intent_storage_id

MAX_QUOTE_AGE = timedelta(minutes=10)
QUOTE_RETENTION = timedelta(days=7)


class PrepareBankOrderDenied(ValueError):
    pass


async def prepare_bank_receipt_order_intent(
    db: Any, *,
    owner_id: str, intent_id: str,
    inbox_id: str, conversation_id: str,
    source_contact_id: int, source_message_id: int,
    order: PendingOrder, selected_rate_id: int,
    enabled: bool = False,
    product_reader: Callable[..., Awaitable[dict]] = read_exact_product,
    rate_reader: Callable[..., Awaitable[list[dict]]] = read_shipping_rate_candidates,
    methods_reader: Callable[..., Awaitable[dict]] = read_checkout_payment_methods,
    bank_reader: Callable[..., Awaitable[dict]] = get_verified_receiving_bank,
    now: datetime | None = None,
) -> dict:
    if not enabled:
        raise PrepareBankOrderDenied("bank_review_quote_disabled")
    if (not owner_id or not inbox_id or not conversation_id or
            type(source_contact_id) is not int or source_contact_id < 1 or
            type(source_message_id) is not int or source_message_id < 1 or
            type(selected_rate_id) is not int or selected_rate_id < 1 or
            not isinstance(order, PendingOrder) or
            order.accepted_methods != ["bank"]):
        raise PrepareBankOrderDenied("bank_transfer_order_scope_unverified")

    intent_storage_id(owner_id, intent_id)
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        raise PrepareBankOrderDenied("bank_quote_clock_invalid")
    moment = moment.astimezone(timezone.utc)
    evidence_id = receipt_key(owner_id, intent_id)
    evidence = await db.salla_ai_bank_receipt_preflights.find_one({
        "_id": evidence_id, "owner_id": owner_id, "intent_id": intent_id,
    })
    if (not isinstance(evidence, dict) or
            evidence.get("state") != "preliminary_pass_for_human_review" or
            evidence.get("human_review_required") is not True or
            evidence.get("bank_credit_confirmed") is not False or
            str(evidence.get("inbox_id")) != inbox_id or
            str(evidence.get("conversation_id")) != conversation_id or
            evidence.get("contact_id") != source_contact_id):
        raise PrepareBankOrderDenied("verified_private_receipt_required")
    receipt_time = evidence.get("created_at")
    if not isinstance(receipt_time, datetime):
        raise PrepareBankOrderDenied("receipt_origin_clock_unverified")
    if receipt_time.tzinfo is None:
        receipt_time = receipt_time.replace(tzinfo=timezone.utc)
    if not receipt_time <= moment < receipt_time + timedelta(days=1):
        raise PrepareBankOrderDenied("stale_bank_receipt")
    bank_id = evidence.get("store_bank_id")
    if type(bank_id) is not int or bank_id < 1:
        raise PrepareBankOrderDenied("verified_bank_id_required")
    for line in order.products:
        if line.identifier_type != "id" or type(line.identifier) is not int:
            raise PrepareBankOrderDenied("canonical_product_ids_required")

    try:
        products = {
            product_id: await product_reader(db, owner_id, product_id)
            for product_id in sorted({line.identifier for line in order.products})
        }
        product_checked_at = now or datetime.now(timezone.utc)
        rates = await rate_reader(
            db, owner_id, country_id=order.ship_to.country,
            city_id=order.ship_to.city,
            postal_code=order.ship_to.postal_code,
        )
        methods = await methods_reader(db, owner_id)
        receiving_bank = await bank_reader(db, owner_id, bank_id)
        moment = now or datetime.now(timezone.utc)
        if (not isinstance(methods, dict) or
                methods.get("source") != "salla_payment_methods" or
                methods.get("can_create_order") is not False or
                "bank" not in methods.get("eligible_create_order_method_candidates", [])):
            raise PrepareBankOrderDenied("bank_payment_not_enabled")
        if (not isinstance(receiving_bank, dict) or
                receiving_bank.get("source") != "salla_active_store_bank_details" or
                receiving_bank.get("active") is not True or
                receiving_bank.get("store_bank_id") != bank_id):
            raise PrepareBankOrderDenied("active_salla_bank_required")
        validate_selected_cart(
            order, catalog_by_product_id=products,
            checked_at=product_checked_at, now=moment,
        )
        validate_shipping_selection(
            order, selected_rate_id=selected_rate_id,
            rate_candidates=rates, checked_at=moment, now=moment,
        )
    except (SourceUnavailable, BankSelectionDenied, CartUnverified,
            ShippingSelectionUnverified) as exc:
        raise PrepareBankOrderDenied("source_verified_bank_cart_required") from None

    digest = bank_order_digest(order, store_bank_id=bank_id)
    try:
        sealed = encrypt_order_payload(
            order, owner_id=owner_id, intent_id=intent_id,
        )
    except EncryptedOrderUnavailable:
        raise PrepareBankOrderDenied("private_order_encryption_unavailable") from None
    key = hashlib.sha256(
        f"assisted-unpaid-quote:{owner_id}:{intent_id}".encode("utf-8")
    ).hexdigest()
    quote = {
        "_id": key,
        "user_id": owner_id, "intent_id": intent_id,
        "inbox_id": inbox_id, "conversation_id": conversation_id,
        "source_contact_id": source_contact_id,
        "source_message_id": source_message_id,
        "quote_ref": "quote." + uuid4().hex,
        "version": 1, "payload_digest": digest,
        "encrypted_order_payload": sealed,
        "source": "mezan_bank_receipt_pending_review_v1",
        "state": "authorized_for_pending_creation",
        "bank_review_only": True, "bank_credit_confirmed": False,
        "receipt_appearance_checked": True,
        "receipt_preflight_id": evidence_id,
        "receipt_image_sha256": evidence["image_sha256"],
        "receipt_claimed_amount_sar": evidence["claimed_amount_sar"],
        "store_bank_id": bank_id,
        "post_create_salla_total": True,
        "online_only": False,
        "total_sar": None, "pricing_verified": False,
        "shipping_price_verified": False,
        "products_verified": True,
        "carrier_eligibility_verified": True,
        "payment_methods_verified": True,
        "verified_at": moment,
        "expires_at": moment + MAX_QUOTE_AGE,
        "purge_after": moment + QUOTE_RETENTION,
        "created_at": moment,
    }
    try:
        await db.salla_ai_order_quotes.insert_one(quote)
    except Exception:
        previous = await db.salla_ai_order_quotes.find_one({"_id": key})
        if not isinstance(previous, dict) or any(
            previous.get(x) != quote[x] for x in (
                "user_id", "intent_id", "inbox_id", "conversation_id",
                "source_contact_id", "payload_digest", "store_bank_id",
                "receipt_preflight_id", "receipt_image_sha256",
            )
        ):
            raise PrepareBankOrderDenied("bank_quote_intent_conflict")
        exp = previous.get("expires_at")
        if not isinstance(exp, datetime):
            raise PrepareBankOrderDenied("bank_quote_expiry_missing")
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        if exp <= moment:
            raise PrepareBankOrderDenied("bank_quote_expired_new_intent_required")
        quote = previous
    return {
        "state": "ready_for_customer_confirmation",
        "intent_id": intent_id,
        "quote_ref": quote["quote_ref"],
        "quote_version": quote["version"],
        "quote_digest": quote["payload_digest"],
        "pricing_mode": "bank_pending_review",
        "bank_credit_confirmed": False,
        "human_review_required": True,
        "verified_total_sar": None,
        "checkout_url": None,
    }
