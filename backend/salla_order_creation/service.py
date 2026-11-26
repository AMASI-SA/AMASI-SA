"""Conservative confirmed-order execution for Mezan -> Salla.

Only a trusted Mezan quote AND an independently persisted WhatsApp customer
confirmation authorize POST. This module does not create either proof record;
their producer must be integrated and audited separately. Feature flag defaults
OFF and AI never receives a Salla token. No payment-link generation.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Awaitable, Callable

from .payload import PendingOrder, build_pending_order_payload

PostSalla = Callable[..., Awaitable[dict[str, Any]]]
_INTENT_RE = re.compile(r"^[a-zA-Z0-9_-]{8,100}$")
_PROOF_MAX_AGE = timedelta(minutes=30)


class CreationDenied(Exception):
    def __init__(self, code: str, status_code: int = 409):
        self.code = code
        self.status_code = status_code
        super().__init__(code)


def order_digest(order: PendingOrder) -> str:
    # Stable digest of only allowlisted Salla fields. Never log raw payloads.
    payload = build_pending_order_payload(order)
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def intent_storage_id(user_id: str, intent_id: str) -> str:
    if not user_id or not _INTENT_RE.fullmatch(intent_id or ""):
        raise CreationDenied("invalid_intent_id", 422)
    return hashlib.sha256(f"{user_id}\n{intent_id}".encode("utf-8")).hexdigest()


def _utc(value: Any) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        # MongoDB stores BSON datetime in UTC and the default Motor codec
        # returns UTC-naive values. Treat ONLY database proof timestamps as
        # UTC; external timestamps are never accepted by this method.
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _is_verified_quote(quote: dict | None, *, digest: str,
                       user_id: str, intent_id: str,
                       inbox_id: str, conversation_id: str, now: datetime) -> bool:
    if not quote:
        return False
    if any(quote.get(k) != v for k, v in (
        ("user_id", user_id), ("intent_id", intent_id),
        ("inbox_id", inbox_id), ("conversation_id", conversation_id),
        ("payload_digest", digest), ("source", "mezan_authoritative_quote_v1"),
        ("state", "verified"),
    )):
        return False
    if not all(quote.get(k) is True for k in (
        "products_verified", "shipping_verified", "pricing_verified",
        "payment_methods_verified",
    )):
        return False
    verified_at = _utc(quote.get("verified_at"))
    expires_at = _utc(quote.get("expires_at"))
    if not verified_at or not expires_at or not (verified_at <= now < expires_at):
        return False
    try:
        total = Decimal(str(quote.get("total_sar") or "0"))
    except (InvalidOperation, ValueError):
        return False
    return total.is_finite() and total > 0 and total.as_tuple().exponent >= -2


def _is_customer_confirmed(confirmation: dict | None, quote: dict, *,
                           digest: str, user_id: str, intent_id: str,
                           inbox_id: str, conversation_id: str,
                           now: datetime) -> bool:
    if not confirmation or confirmation.get("confirmed") is not True:
        return False
    if any(confirmation.get(k) != v for k, v in (
        ("user_id", user_id), ("intent_id", intent_id),
        ("inbox_id", inbox_id), ("conversation_id", conversation_id),
        ("payload_digest", digest),
        ("quote_version", quote.get("version")),
        ("source", "chatwoot_customer_inbound_v1"),
    )):
        return False
    if not isinstance(confirmation.get("message_id"), (str, int)) or not str(
        confirmation["message_id"]
    ).strip():
        return False
    at = _utc(confirmation.get("confirmed_at"))
    quote_at = _utc(quote.get("verified_at"))
    return bool(at and quote_at and quote_at <= at <= now
                and now - at <= _PROOF_MAX_AGE)


async def create_confirmed_salla_order(
    db: Any,
    *,
    user_id: str,
    actor_id: str,
    intent_id: str,
    inbox_id: str,
    conversation_id: str,
    order: PendingOrder,
    post_salla: PostSalla,
    enabled: bool = False,
) -> dict[str, Any]:
    """Persist one immutable intent BEFORE POST; unknown outcome is not retried.

    All input identity values come from authenticated Mezan route; provider
    transport is the existing token-refreshing Salla client. This function
    performs no pricing calculation and does not mutate Mezan accounting.
    """
    if not enabled:
        raise CreationDenied("assisted_order_creation_disabled", 503)
    if not user_id or not actor_id or not inbox_id or not conversation_id:
        raise CreationDenied("missing_authenticated_context", 403)

    storage_id = intent_storage_id(user_id, intent_id)
    digest = order_digest(order)
    now = datetime.now(timezone.utc)
    quote = await db.salla_ai_order_quotes.find_one(
        {"user_id": user_id, "intent_id": intent_id}
    )
    if not _is_verified_quote(
        quote, digest=digest, user_id=user_id, intent_id=intent_id,
        inbox_id=inbox_id, conversation_id=conversation_id, now=now,
    ):
        raise CreationDenied("fresh_server_verified_quote_required", 409)
    confirmation = await db.salla_ai_order_confirmations.find_one(
        {"user_id": user_id, "intent_id": intent_id}
    )
    if not _is_customer_confirmed(
        confirmation, quote, digest=digest, user_id=user_id,
        intent_id=intent_id, inbox_id=inbox_id,
        conversation_id=conversation_id, now=now,
    ):
        raise CreationDenied("independent_customer_confirmation_required", 409)

    record = {
        "_id": storage_id, "user_id": user_id, "intent_id": intent_id,
        "actor_id": actor_id, "state": "sending", "payload_digest": digest,
        "quote_version": quote["version"],
        "confirmation_message_id": str(confirmation["message_id"]),
        "created_at": now, "updated_at": now,
    }
    try:
        # Mongo's built-in unique _id index is sufficient across processes;
        # no race-prone find-then-insert and no extra index creation per request.
        await db.salla_ai_order_intents.insert_one(record)
    except Exception:
        prior = await db.salla_ai_order_intents.find_one({"_id": storage_id})
        if prior is None:
            raise  # Database error, not a duplicate.
        if prior.get("user_id") != user_id or prior.get("payload_digest") != digest:
            raise CreationDenied("intent_reused_with_different_order", 409)
        return {
            "state": prior.get("state", "unknown"),
            "salla_order_id": prior.get("salla_order_id"),
            "reference_id": prior.get("reference_id"),
            "replayed": True,
        }

    # One and only one POST. Never retry on network exception or ambiguous
    # response, because the provider might have committed the order.
    try:
        result = await post_salla(
            db, user_id, "POST", "/orders",
            json=build_pending_order_payload(order),
        )
        # Reject provider-level failure even when an error envelope happens
        # to contain a numeric id. An uncertain provider outcome is reconciled,
        # never optimistically announced to the customer.
        if not isinstance(result, dict) or result.get("success") is False:
            raise ValueError("Salla create call not confirmed successful")
        data = result.get("data")
        if not isinstance(data, dict) or isinstance(data.get("id"), bool):
            raise ValueError("Salla create response missing order object")
        order_id = data.get("id")
        if not (str(order_id).isdigit() and int(order_id) > 0):
            raise ValueError("Salla create response missing positive order ID")
    except Exception as exc:
        await db.salla_ai_order_intents.update_one(
            {"_id": storage_id, "state": "sending"},
            {"$set": {
                "state": "unknown", "updated_at": datetime.now(timezone.utc),
                "error_class": type(exc).__name__,
            }},
        )
        # Don't return exception detail or customer/provider payload.
        return {"state": "unknown", "salla_order_id": None,
                "replayed": False, "needs_reconciliation": True}

    reference = data.get("reference_id")
    reference = reference if str(reference).isdigit() else None

    # Salla remains the price authority after creation. A mismatched/absent
    # provider total must never lead to automatic checkout-link delivery.
    total_raw = (data.get("amounts") or {}).get("total")
    total_currency = ""
    if isinstance(total_raw, dict):
        total_currency = str(total_raw.get("currency") or "").upper()
        total_raw = total_raw.get("amount")
    try:
        observed_total = Decimal(str(total_raw))
        expected_total = Decimal(str(quote["total_sar"]))
        amount_confirmed = (
            total_currency == "SAR"
            and observed_total.is_finite()
            and abs(observed_total - expected_total) <= Decimal("0.01")
        )
    except (InvalidOperation, ValueError, TypeError):
        amount_confirmed = False

    final_state = "created" if amount_confirmed else "created_amount_unverified"
    await db.salla_ai_order_intents.update_one(
        {"_id": storage_id, "state": "sending"},
        {"$set": {
            "state": final_state, "salla_order_id": int(order_id),
            "reference_id": int(reference) if reference is not None else None,
            "updated_at": datetime.now(timezone.utc),
        }},
    )
    return {
        "state": final_state, "salla_order_id": int(order_id),
        "reference_id": int(reference) if reference is not None else None,
        "replayed": False, "amount_confirmed": amount_confirmed,
    }
