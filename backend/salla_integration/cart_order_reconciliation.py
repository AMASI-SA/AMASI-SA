"""Reconcile verified Salla order webhooks with abandoned-cart snapshots.

Salla may deliver ``order.created`` before, after, or without a matching
``abandoned.cart.purchased`` webhook.  The order is authoritative evidence that
an active cart converted, so this module promotes the matching cart to
``purchased`` after the order has been persisted.

The matcher is deliberately conservative: exact cart id wins; otherwise the
same encrypted customer identity is required plus product overlap, amount
agreement, or a very short temporal hand-off.  No Salla API call is made here.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

from customer_identity import link_unified_customer_orders, resolve_customer_identity

from .abandoned_carts import ABANDONED_CART_COLLECTION, parse_salla_datetime

_ORDER_EVENTS = frozenset({"order.created", "order.updated", "order.status.updated"})
_MAX_FALLBACK_GAP = timedelta(minutes=15)
_MAX_MATCH_AGE = timedelta(days=7)


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _text(value: Any) -> str | None:
    if value is None or isinstance(value, (dict, list, tuple, set)):
        return None
    rendered = str(value).strip()
    return rendered or None


def _first(*values: Any) -> Any:
    for value in values:
        if value not in (None, "", [], {}):
            return value
    return None


def _number(value: Any) -> float | None:
    if isinstance(value, dict):
        value = _first(value.get("amount"), value.get("value"), value.get("total"))
    try:
        parsed = float(str(value).replace(",", "").strip())
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if parsed == parsed and abs(parsed) != float("inf") else None


def _event_order(event_body: dict[str, Any]) -> dict[str, Any]:
    data = _mapping(event_body.get("data"))
    order = _mapping(data.get("order"))
    if not order:
        return data
    merged = dict(order)
    for key, value in data.items():
        if key != "order" and key not in merged:
            merged[key] = value
    return merged


def _customer_context(order: dict[str, Any]) -> dict[str, Any]:
    customer = _mapping(
        _first(
            order.get("customer"),
            order.get("customer_data"),
            order.get("contact"),
        )
    )
    return {
        "external_customer_id": _first(customer.get("id"), order.get("customer_id")),
        "email": _first(customer.get("email"), order.get("customer_email")),
        "mobile": _first(
            customer.get("mobile"),
            customer.get("phone"),
            order.get("customer_mobile"),
            order.get("customer_phone"),
        ),
        "private_profile": {
            key: value
            for key, value in {
                "name": _first(customer.get("name"), customer.get("full_name")),
                "email": _first(customer.get("email"), order.get("customer_email")),
                "mobile": _first(
                    customer.get("mobile"),
                    customer.get("phone"),
                    order.get("customer_mobile"),
                    order.get("customer_phone"),
                ),
            }.items()
            if value not in (None, "")
        },
    }


def _cart_id(order: dict[str, Any]) -> str | None:
    cart = _mapping(order.get("cart"))
    checkout = _mapping(order.get("checkout"))
    source = _mapping(order.get("source"))
    return _text(
        _first(
            order.get("cart_id"),
            order.get("abandoned_cart_id"),
            cart.get("id"),
            cart.get("cart_id"),
            checkout.get("cart_id"),
            source.get("cart_id"),
        )
    )


def _order_number(order: dict[str, Any]) -> str | None:
    return _text(
        _first(
            order.get("reference_id"),
            order.get("order_number"),
            order.get("id"),
        )
    )


def _order_time(event_body: dict[str, Any], order: dict[str, Any]) -> datetime:
    parsed = parse_salla_datetime(
        _first(
            order.get("created_at"),
            order.get("updated_at"),
            event_body.get("created_at"),
        )
    )
    return parsed or datetime.now(timezone.utc)


def _cart_time(cart: dict[str, Any]) -> datetime | None:
    for key in (
        "cart_updated_at",
        "last_received_at",
        "updated_at",
        "cart_created_at",
        "first_seen_at",
        "created_at",
    ):
        parsed = parse_salla_datetime(cart.get(key))
        if parsed:
            return parsed
    return None


def _item_ids(value: Any) -> set[str]:
    if not isinstance(value, list):
        return set()
    output: set[str] = set()
    for raw in value[:500]:
        item = _mapping(raw)
        product = _mapping(item.get("product"))
        variant = _mapping(item.get("variant"))
        for candidate in (
            item.get("product_id"),
            product.get("id"),
            item.get("variant_id"),
            variant.get("id"),
            item.get("sku"),
            product.get("sku"),
            variant.get("sku"),
        ):
            rendered = _text(candidate)
            if rendered:
                output.add(rendered.casefold())
    return output


def _order_items(order: dict[str, Any]) -> Any:
    return _first(order.get("items"), order.get("products"), [])


def _order_total(order: dict[str, Any]) -> float | None:
    amounts = _mapping(order.get("amounts"))
    return _number(
        _first(
            order.get("total"),
            order.get("total_amount"),
            order.get("grand_total"),
            amounts.get("total"),
        )
    )


def _amount_matches(cart: dict[str, Any], order: dict[str, Any]) -> bool:
    cart_total = _number(cart.get("total"))
    order_total = _order_total(order)
    if cart_total is None or order_total is None:
        return False
    tolerance = max(1.0, abs(cart_total) * 0.02)
    return abs(cart_total - order_total) <= tolerance


def _select_cart_candidate(
    carts: list[dict[str, Any]],
    *,
    order: dict[str, Any],
    order_at: datetime,
    exact_cart_id: str | None,
) -> tuple[dict[str, Any] | None, str | None]:
    """Choose one cart using only conversion-grade evidence."""
    active = [row for row in carts if row.get("purchased") is not True]
    if exact_cart_id:
        for cart in active:
            if _text(cart.get("cart_id")) == exact_cart_id:
                return cart, "order_cart_id"

    order_ids = _item_ids(_order_items(order))
    ranked: list[tuple[datetime, dict[str, Any], str]] = []
    for cart in active:
        cart_at = _cart_time(cart)
        if not cart_at:
            continue
        gap = order_at - cart_at
        if gap < timedelta(minutes=-5) or gap > _MAX_MATCH_AGE:
            continue

        cart_ids = _item_ids(cart.get("items"))
        product_overlap = bool(order_ids and cart_ids and order_ids.intersection(cart_ids))
        amount_match = _amount_matches(cart, order)
        very_recent = timedelta(0) <= gap <= _MAX_FALLBACK_GAP
        if product_overlap:
            evidence = "customer_product_overlap"
        elif amount_match:
            evidence = "customer_amount_match"
        elif very_recent:
            evidence = "customer_recent_checkout"
        else:
            continue
        ranked.append((cart_at, cart, evidence))

    if not ranked:
        return None, None
    ranked.sort(key=lambda row: row[0], reverse=True)
    _, cart, evidence = ranked[0]
    return cart, evidence


async def reconcile_cart_from_verified_order(
    db: Any,
    event_body: dict[str, Any],
) -> dict[str, Any]:
    """Promote one abandoned cart after a verified order conversion."""
    event_name = _text(event_body.get("event")) or ""
    if event_name not in _ORDER_EVENTS:
        return {"attempted": False, "reason": "not_order_event"}

    merchant_id = _text(event_body.get("merchant"))
    if not merchant_id:
        return {"attempted": True, "reconciled": False, "reason": "merchant_missing"}

    integration = await db.salla_integrations.find_one(
        {"store_id": {"$in": [merchant_id, int(merchant_id)] if merchant_id.isdigit() else [merchant_id]}},
        {"_id": 0, "user_id": 1},
        sort=[("updated_at", -1)],
    )
    user_id = _text((integration or {}).get("user_id"))
    if not user_id:
        return {"attempted": True, "reconciled": False, "reason": "owner_missing"}

    order = _event_order(event_body)
    customer = _customer_context(order)
    identity = await resolve_customer_identity(
        db,
        user_id=user_id,
        merchant_id=merchant_id,
        source_system="salla",
        external_customer_id=customer.get("external_customer_id"),
        email=customer.get("email"),
        mobile=customer.get("mobile"),
        private_profile=customer.get("private_profile") or None,
        observed_at=_order_time(event_body, order),
    )
    identity_id = _text((identity or {}).get("customer_identity_id"))
    if not identity_id:
        return {"attempted": True, "reconciled": False, "reason": "customer_identity_missing"}

    order_number = _order_number(order)
    await link_unified_customer_orders(
        db,
        user_id=user_id,
        customer_identity_id=identity_id,
        external_customer_id=customer.get("external_customer_id"),
        email=customer.get("email"),
        mobile=customer.get("mobile"),
        order_number=order_number,
    )

    cursor = (
        getattr(db, ABANDONED_CART_COLLECTION)
        .find(
            {
                "user_id": user_id,
                "customer_identity_id": identity_id,
                "purchased": {"$ne": True},
            },
            {
                "_id": 0,
                "cart_id": 1,
                "purchased": 1,
                "total": 1,
                "items": 1,
                "cart_updated_at": 1,
                "last_received_at": 1,
                "updated_at": 1,
                "cart_created_at": 1,
                "first_seen_at": 1,
                "created_at": 1,
            },
        )
        .sort("cart_updated_at", -1)
        .limit(20)
    )
    carts = await cursor.to_list(length=20)
    order_at = _order_time(event_body, order)
    candidate, evidence = _select_cart_candidate(
        carts,
        order=order,
        order_at=order_at,
        exact_cart_id=_cart_id(order),
    )
    if not candidate:
        return {
            "attempted": True,
            "reconciled": False,
            "reason": "no_conversion_grade_cart_match",
            "order_number": order_number,
        }

    now = datetime.now(timezone.utc)
    result = await getattr(db, ABANDONED_CART_COLLECTION).update_one(
        {
            "user_id": user_id,
            "customer_identity_id": identity_id,
            "cart_id": candidate.get("cart_id"),
            "purchased": {"$ne": True},
        },
        {
            "$set": {
                "purchased": True,
                "status": "purchased",
                "order_number": order_number,
                "purchase_state_source": f"verified_salla_order:{evidence}",
                "purchase_reconciled_at": now,
                "updated_at": now,
            },
            "$addToSet": {"source_events": event_name},
        },
    )
    reconciled = int(getattr(result, "modified_count", 0) or 0) > 0
    return {
        "attempted": True,
        "reconciled": reconciled,
        "reason": evidence if reconciled else "already_reconciled",
        "cart_id": _text(candidate.get("cart_id")),
        "order_number": order_number,
        "provider_write_reached": False,
    }


def install_verified_order_cart_reconciliation() -> None:
    """Wrap the verified webhook capture once, without changing its contract."""
    from . import webhook_event_capture as capture_module

    current = capture_module.capture_unknown_event
    if getattr(current, "_mezan_cart_reconciliation_installed", False):
        return

    async def wrapped(
        db: Any,
        event_body: dict[str, Any],
        *,
        known_events: Any,
    ) -> dict[str, Any]:
        result = await current(db, event_body, known_events=known_events)
        event_name = _text(event_body.get("event")) or ""
        if event_name in _ORDER_EVENTS and (result.get("order_sync") or {}).get("synced"):
            try:
                result["abandoned_cart_conversion"] = await reconcile_cart_from_verified_order(
                    db, event_body
                )
            except Exception as exc:  # webhook acknowledgement must remain independent
                result["abandoned_cart_conversion"] = {
                    "attempted": True,
                    "reconciled": False,
                    "reason": "reconciliation_exception",
                    "error_type": type(exc).__name__,
                }
        return result

    wrapped._mezan_cart_reconciliation_installed = True  # type: ignore[attr-defined]
    capture_module.capture_unknown_event = wrapped


__all__ = [
    "install_verified_order_cart_reconciliation",
    "reconcile_cart_from_verified_order",
    "_select_cart_candidate",
]
