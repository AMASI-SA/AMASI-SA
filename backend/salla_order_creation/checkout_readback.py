"""Authorized checkout readback from Mezan's EXISTING Salla-synced order.

Never generate a checkout link. Only read the same canonical OrderDTO consumed by
the Mezan order-detail page AFTER the Salla order has been synchronized.

A pending Salla order may be created while the link is not yet in Mezan. That
is WAITING, never a reason to create a second Salla order or invent a URL.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Awaitable, Callable
from urllib.parse import parse_qsl, urlsplit
import unicodedata

from .service import intent_storage_id

ReadOrder = Callable[[str, str], Awaitable[Any]]
ALLOWED_STATUS = frozenset({
    "waiting", "waiting payment", "pending payment", "awaiting payment",
    "بانتظار الدفع",
})
UNSAFE_REDIRECT_KEYS = frozenset({
    "redirect", "redirect_url", "return_url", "next", "continue",
    "callback", "target", "url", "destination",
})


def _money(value: Any) -> Decimal | None:
    if type(value) not in (int, float, str, Decimal):
        return None
    try:
        money = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    if (not money.is_finite() or money < 0 or
            money.as_tuple().exponent < -2):
        return None
    return money


def _status(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    text = unicodedata.normalize("NFKC", value).strip().casefold()
    # Arabic hamza written with or without the initial hamza in Salla labels.
    text = text.replace("إ", "ا").replace("أ", "ا").replace("آ", "ا")
    return " ".join(text.replace("_", " ").split())


def validated_salla_checkout_url(url: Any, allowed_hosts: frozenset[str]) -> str | None:
    """Require an explicitly configured HTTPS Salla/store checkout host.

    Reject admin, userinfo, non-443 ports and arbitrary open redirects. Do not
    append a token or construct a URL. The caller obtains this from Mezan.
    """
    if type(url) is not str or not (0 < len(url) <= 2048):
        return None
    if any(ord(c) < 32 or ord(c) == 127 for c in url):
        return None
    if not allowed_hosts or type(allowed_hosts) is not frozenset:
        return None
    try:
        parsed = urlsplit(url)
        hostname = (parsed.hostname or "").lower()
        if (parsed.scheme != "https" or
                hostname not in allowed_hosts or
                parsed.port not in (None, 443) or
                parsed.username or parsed.password or parsed.fragment or
                parsed.path.startswith(("//", "/admin")) or
                parsed.path.lower().startswith("/dashboard")):
            return None
        if not parsed.path or parsed.path == "/":
            return None
        if any(k.lower() in UNSAFE_REDIRECT_KEYS for k, _ in
               parse_qsl(parsed.query, keep_blank_values=True)):
            return None
    except ValueError:
        return None
    return url


def _not_ready(state: str, *, salla_order_id: int | None = None,
               reference_id: int | None = None) -> dict:
    return {
        "state": state,
        "salla_order_id": salla_order_id,
        "reference_id": reference_id,
        "checkout_url": None,
        "amount_verified": False,
    }


async def resolve_synced_checkout(
    db: Any, *,
    owner_id: str,
    intent_id: str,
    inbox_id: str,
    conversation_id: str,
    quote_ref: str,
    quote_digest: str,
    quote_version: int,
    confirmation_message_id: int,
    load_order: ReadOrder,
    allowed_hosts: frozenset[str],
) -> dict:
    """Return URL *only* when the newly created Salla order matches Mezan.

    Caller passes an authenticated purpose-scoped identity. The authoritative
    quote and one-time POST intent come from Mezan storage; the canonical order
    comes from Mezan's existing tenant-scoped Salla sync. Never trust an order
    number or URL supplied by the WhatsApp agent.
    """
    if (not owner_id or not inbox_id or not conversation_id or
            type(confirmation_message_id) is not int or
            confirmation_message_id < 1 or
            type(quote_version) is not int or quote_version < 1):
        return _not_ready("scope_not_verified")
    storage_id = intent_storage_id(owner_id, intent_id)
    intent = await db.salla_ai_order_intents.find_one({
        "_id": storage_id, "user_id": owner_id,
    })
    if not isinstance(intent, dict) or intent.get("state") not in (
        "created", "created_amount_unverified",
    ):
        return _not_ready("order_not_created_or_unresolved")
    order_id = intent.get("salla_order_id")
    ref_id = intent.get("reference_id")
    if (type(order_id) is not int or order_id <= 0 or
            type(ref_id) is not int or ref_id <= 0 or
            intent.get("payload_digest") != quote_digest or
            intent.get("quote_version") != quote_version or
            str(intent.get("confirmation_message_id")) != str(confirmation_message_id)):
        return _not_ready("order_intent_mismatch")
    if (intent.get("inbox_id") != inbox_id or
            intent.get("conversation_id") != conversation_id):
        return _not_ready("conversation_scope_mismatch")

    quote = await db.salla_ai_order_quotes.find_one({
        "user_id": owner_id, "intent_id": intent_id,
        "inbox_id": inbox_id, "conversation_id": conversation_id,
        "quote_ref": quote_ref,
    })
    if (not isinstance(quote, dict) or
            quote.get("source") != "mezan_authoritative_quote_v1" or
            quote.get("state") != "verified" or
            quote.get("payload_digest") != quote_digest or
            quote.get("version") != quote_version or
            not all(quote.get(x) is True for x in (
                "products_verified", "shipping_verified",
                "pricing_verified", "payment_methods_verified",
            ))):
        return _not_ready("original_quote_not_verified", salla_order_id=order_id,
                          reference_id=ref_id)
    approved_total = _money(quote.get("total_sar"))
    if approved_total is None or approved_total <= 0:
        return _not_ready("original_total_unverified", salla_order_id=order_id,
                          reference_id=ref_id)

    # Reuse the exact Mezan order engine. This is read-only; the signed client
    # cannot ask to retrieve any different order or merchant.
    try:
        order = await load_order(owner_id, str(ref_id))
    except LookupError:
        return _not_ready("waiting_for_mezan_sync", salla_order_id=order_id,
                          reference_id=ref_id)
    if order is None:
        return _not_ready("waiting_for_mezan_sync", salla_order_id=order_id,
                          reference_id=ref_id)
    if str(getattr(order, "order_id", "")) != str(order_id) or (
        str(getattr(order, "order_number", "")) != str(ref_id)
    ):
        return _not_ready("synced_order_identity_mismatch", salla_order_id=order_id,
                          reference_id=ref_id)
    status = _status(getattr(order, "status", "")) or _status(
        getattr(order, "status_native", "")
    )
    native_status = _status(getattr(order, "status_native", ""))
    if status not in ALLOWED_STATUS and native_status not in ALLOWED_STATUS:
        return _not_ready("order_not_waiting_for_payment", salla_order_id=order_id,
                          reference_id=ref_id)

    payment = getattr(order, "payment", None)
    totals = getattr(order, "totals", None)
    if payment is None or totals is None:
        return _not_ready("synced_payment_unavailable", salla_order_id=order_id,
                          reference_id=ref_id)
    if (_status(getattr(payment, "status", "")) in (
            "paid", "refunded", "cancelled", "partially paid") or
            getattr(payment, "collection_status", None) in ("paid", "partial")):
        return _not_ready("not_an_unpaid_order", salla_order_id=order_id,
                          reference_id=ref_id)
    paid = _money(getattr(payment, "paid_amount", None))
    total = _money(getattr(totals, "total", None))
    if (paid is None or paid != 0 or total is None or
            str(getattr(totals, "currency", "")).upper() != "SAR"):
        return _not_ready("payment_or_currency_not_verified", salla_order_id=order_id,
                          reference_id=ref_id)
    if abs(total - approved_total) > Decimal("0.01"):
        # The customer approved a different amount: do not send the link.
        return _not_ready("amount_mismatch_needs_customer_review",
                          salla_order_id=order_id, reference_id=ref_id)

    url = validated_salla_checkout_url(
        getattr(payment, "checkout_url", None), allowed_hosts,
    )
    if not url:
        return _not_ready("waiting_for_verified_mezan_checkout_url",
                          salla_order_id=order_id, reference_id=ref_id)
    return {
        "state": "ready",
        "salla_order_id": order_id,
        "reference_id": ref_id,
        "total_sar": f"{total:.2f}",
        "checkout_url": url,
        "amount_verified": True,
    }
