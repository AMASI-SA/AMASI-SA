"""Prepare an UNPAID Salla order intent from Mezan-verified input facts.

This solves the owner's order-of-operations request: Salla, NOT the AI,
calculates the final payable amount AFTER creating a pending-payment order.
Mezan then reads the SAME synced order and its existing payment checkout URL.
No new checkout/link generator, no guessed final total.

The writer is disabled by default. The owner/bot identity, recipient and
shipping address must be sourced from authenticated and scope-checked
conversation state BEFORE this server-owned validation is invoked.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable
from uuid import uuid4

from .catalog_validation import CartUnverified, validate_selected_cart
from .payload import PendingOrder
from .readers import (
    SourceUnavailable,
    read_checkout_payment_methods,
    read_exact_product,
    read_shipping_rate_candidates,
)
from .service import intent_storage_id, order_digest, quote_pricing_mode
from .shipping_selection import (
    ShippingSelectionUnverified, validate_shipping_selection,
)

ONLINE_ONLY = frozenset({"mada", "credit_card"})
QUOTE_MAX_AGE = timedelta(minutes=10)
QUOTE_RETENTION = timedelta(days=7)


class PrepareDenied(ValueError):
    def __init__(self, reason: str, status_code: int = 409):
        self.status_code = status_code
        super().__init__(reason)


async def prepare_salla_unpaid_order_intent(
    db: Any,
    *,
    user_id: str,
    intent_id: str,
    inbox_id: str,
    conversation_id: str,
    order: PendingOrder,
    selected_rate_id: int,
    enabled: bool = False,
    product_reader: Callable[..., Awaitable[dict]] = read_exact_product,
    rate_reader: Callable[..., Awaitable[list[dict]]] = read_shipping_rate_candidates,
    methods_reader: Callable[..., Awaitable[dict]] = read_checkout_payment_methods,
    now: datetime | None = None,
) -> dict:
    """Read authenticated Salla facts; store an immutable provider-priced intent.

    Never accepts input claiming that a total or eligibility was verified.
    The quote writer constructs these booleans only from provider validation.
    It does not charge the customer or create any Salla order.
    """
    if not enabled:
        raise PrepareDenied("assisted_quote_writer_disabled", 503)
    if (not user_id or not inbox_id or not conversation_id or
            not isinstance(order, PendingOrder) or
            type(selected_rate_id) is not int or selected_rate_id <= 0):
        raise PrepareDenied("invalid_verified_order_intent", 422)
    # Validate stable ID before doing ANY remote reads or Mongo writes.
    intent_storage_id(user_id, intent_id)
    if (not order.accepted_methods or
            not set(order.accepted_methods).issubset(ONLINE_ONLY)):
        raise PrepareDenied("post_create_pricing_online_only")

    timestamp = now or datetime.now(timezone.utc)
    if timestamp.tzinfo is None:
        raise PrepareDenied("utc_server_clock_required", 503)
    timestamp = timestamp.astimezone(timezone.utc)

    # Product options and shipping are never declared verified by AI text.
    # Every product is looked up by the existing merchant Salla OAuth client.
    product_ids = set()
    for line in order.products:
        if line.identifier_type != "id" or type(line.identifier) is not int:
            raise PrepareDenied("canonical_salla_product_id_required")
        product_ids.add(line.identifier)
    try:
        products = {
            product_id: await product_reader(db, user_id, product_id)
            for product_id in sorted(product_ids)
        }
        rates = await rate_reader(
            db, user_id,
            country_id=order.ship_to.country,
            city_id=order.ship_to.city,
            postal_code=order.ship_to.postal_code,
        )
        methods = await methods_reader(db, user_id)
        if (not isinstance(methods, dict) or
                methods.get("source") != "salla_payment_methods" or
                methods.get("can_create_order") is not False):
            raise PrepareDenied("payment_method_source_unverified")
        allowed = methods.get("eligible_create_order_method_candidates")
        if (not isinstance(allowed, list) or
                not set(order.accepted_methods).issubset(set(allowed))):
            raise PrepareDenied("online_payment_method_not_enabled")

        product_evidence = validate_selected_cart(
            order, catalog_by_product_id=products,
            checked_at=timestamp, now=timestamp,
        )
        shipping_evidence = validate_shipping_selection(
            order, selected_rate_id=selected_rate_id,
            rate_candidates=rates,
            checked_at=timestamp, now=timestamp,
        )
        if (not product_evidence or
                shipping_evidence.get("can_create_order") is not False or
                shipping_evidence.get("shipping_company_id") !=
                int(order.courier_id)):
            raise PrepareDenied("provider_cart_selection_unverified")
    except (SourceUnavailable, CartUnverified,
            ShippingSelectionUnverified) as exc:
        # No provider payload/PII is returned to the chat client.
        raise PrepareDenied("salla_cart_inputs_unverified") from exc

    digest = order_digest(order)
    storage_id = hashlib.sha256(
        f"assisted-unpaid-quote:{user_id}:{intent_id}".encode("utf-8")
    ).hexdigest()
    record = {
        "_id": storage_id,
        "user_id": user_id, "intent_id": intent_id,
        "inbox_id": inbox_id, "conversation_id": conversation_id,
        "quote_ref": "quote." + uuid4().hex,
        "version": 1,
        "payload_digest": digest,
        "order_payload": order.model_dump(exclude_none=True),
        "source": "mezan_verified_unpaid_order_intent_v1",
        "state": "authorized_for_pending_creation",
        "post_create_salla_total": True,
        "online_only": True,
        "total_sar": None,
        "pricing_verified": False,
        "shipping_price_verified": False,
        "products_verified": True,
        "carrier_eligibility_verified": True,
        "payment_methods_verified": True,
        "verified_at": timestamp,
        "expires_at": timestamp + QUOTE_MAX_AGE,
        "purge_after": timestamp + QUOTE_RETENTION,
        "created_at": timestamp,
    }
    if quote_pricing_mode(record) != "salla_post_create":
        raise PrepareDenied("quote_policy_not_verified")
    try:
        await db.salla_ai_order_quotes.insert_one(record)
    except Exception:
        previous = await db.salla_ai_order_quotes.find_one({
            "_id": storage_id,
        })
        if not isinstance(previous, dict):
            raise  # Persistence failure, not a duplicate.
        if (previous.get("payload_digest") != digest or
                previous.get("inbox_id") != inbox_id or
                previous.get("conversation_id") != conversation_id or
                quote_pricing_mode(previous) != "salla_post_create"):
            raise PrepareDenied("quote_intent_reused_with_different_cart")
        old_expiry = previous.get("expires_at")
        if not isinstance(old_expiry, datetime):
            raise PrepareDenied("quote_expiration_missing")
        if old_expiry.tzinfo is None:
            old_expiry = old_expiry.replace(tzinfo=timezone.utc)
        if old_expiry <= timestamp:
            raise PrepareDenied("quote_expired_new_intent_required")
        record = previous
    return {
        "state": "ready_for_customer_confirmation",
        "intent_id": intent_id,
        "quote_ref": record["quote_ref"],
        "quote_version": record["version"],
        "quote_digest": digest,
        "pricing_mode": "salla_post_create",
        "verified_total_sar": None,
        "checkout_url": None,
    }
