"""Salla abandoned-cart ingestion and encrypted Customer Memory foundation.

The webhook path calls :func:`persist_abandoned_cart_event` only after the
existing Easy Mode signature/token verification succeeds.  Historical reads
remain dormant until the stored OAuth grant explicitly contains ``carts.read``.
Customer PII and cart-recovery secrets are routed to Mezan's encrypted private
store.  Analytics snapshots and event history remain plaintext-PII-free.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Iterable

import httpx

from customer_identity import (
    attach_customer_activity,
    encrypt_private_payload,
    ensure_customer_identity_indexes,
    link_unified_customer_orders,
    resolve_customer_identity,
)

ABANDONED_CART_EVENTS = frozenset(
    {
        "abandoned.cart",
        "abandoned.cart.updated",
        "abandoned.cart.status.changed",
        "abandoned.cart.purchased",
    }
)
ABANDONED_CART_SCOPE = "carts.read"
ABANDONED_CART_COLLECTION = "salla_abandoned_carts_v1"
ABANDONED_CART_EVENT_COLLECTION = "salla_abandoned_cart_events_v1"
# Keep the existing collection names so the production history is upgraded in
# place on the next idempotent backfill instead of splitting the 6,824+ carts
# across incompatible V1/V2 stores.
ABANDONED_CART_SCHEMA_VERSION = 2
# The connected Amasi store already has more than 6,000 abandoned carts.  At
# Salla's conservative 30-row page size, a 200-page ceiling would silently
# stop before the historical read completes.  Keep the read bounded, but high
# enough to cover the current catalogue with room for growth.
MAX_BACKFILL_PAGES = 500
SALLA_PAGE_SIZE = 30
SALLA_PAGE_REQUEST_TIMEOUT_SECONDS = 45


class AbandonedCartScopeError(RuntimeError):
    """Raised before network access when ``carts.read`` is not granted."""

    code = "salla_carts_read_pending"


def _text(value: Any) -> str | None:
    if value is None:
        return None
    rendered = str(value).strip()
    return rendered or None


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _first(*values: Any) -> Any:
    for value in values:
        if value not in (None, "", [], {}):
            return value
    return None


def _nested(source: dict[str, Any], *path: str) -> Any:
    current: Any = source
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif value:
        try:
            parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    else:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iso(value: Any) -> str | None:
    parsed = _datetime(value)
    return parsed.isoformat() if parsed else _text(value)


def _number(value: Any) -> int | float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, dict):
        value = _first(value.get("amount"), value.get("value"), value.get("total"))
    try:
        parsed = float(str(value).replace(",", "").strip())
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed != parsed or abs(parsed) == float("inf"):
        return None
    return int(parsed) if parsed.is_integer() else parsed


def _currency(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, dict):
            candidate = _first(value.get("currency"), value.get("currency_code"))
        else:
            candidate = value
        rendered = _text(candidate)
        if rendered and 2 <= len(rendered) <= 8:
            return rendered.upper()
    return None


_ATTRIBUTION_ALIASES: dict[str, frozenset[str]] = {
    "platform": frozenset(
        {
            "platform",
            "ad_platform",
            "channel",
            "source",
            "source_native",
            "utm_source",
        }
    ),
    "account_id": frozenset(
        {"account_id", "ad_account_id", "advertiser_id", "sc_ad_account_id"}
    ),
    "campaign_id": frozenset({"campaign_id", "campaignid", "sc_campaign_id"}),
    "ad_group_id": frozenset(
        {"ad_squad_id", "adsquad_id", "adset_id", "ad_set_id", "sc_ad_squad_id"}
    ),
    "ad_id": frozenset({"ad_id", "adid", "sc_ad_id"}),
    "creative_id": frozenset(
        {"creative_id", "creativeid", "sc_creative_id", "asset_id"}
    ),
    "click_id": frozenset(
        {"click_id", "sc_click_id", "scclid", "gclid", "fbclid", "ttclid"}
    ),
    "session_id": frozenset({"session_id", "sessionid", "ga_session_id"}),
    "utm_source": frozenset({"utm_source"}),
    "utm_medium": frozenset({"utm_medium"}),
    "utm_campaign": frozenset({"utm_campaign"}),
    "utm_content": frozenset({"utm_content"}),
    "utm_term": frozenset({"utm_term"}),
}
_ATTRIBUTION_CONTAINERS = frozenset(
    {
        "utm",
        "source_details",
        "marketing",
        "attribution",
        "tracking",
        "traffic",
        "metadata",
        "meta",
        "source",
        "campaign",
    }
)
_PII_CONTAINERS = frozenset(
    {
        "customer",
        "customer_data",
        "contact",
        "billing_address",
        "shipping_address",
        "address",
        "user",
    }
)


def _attribution(source: Any) -> dict[str, str]:
    output: dict[str, str] = {}

    def visit(value: Any, *, depth: int = 0) -> None:
        if depth > 8:
            return
        if isinstance(value, list):
            for child in value[:100]:
                visit(child, depth=depth + 1)
            return
        if not isinstance(value, dict):
            return
        for raw_key, child in value.items():
            key = str(raw_key or "").strip().lower().replace("-", "_")
            if key in _PII_CONTAINERS:
                continue
            for canonical, aliases in _ATTRIBUTION_ALIASES.items():
                if canonical not in output and key in aliases:
                    rendered = _text(child)
                    if rendered:
                        output[canonical] = rendered[:500]
            if key in _ATTRIBUTION_CONTAINERS and isinstance(child, (dict, list)):
                visit(child, depth=depth + 1)

    visit(source)
    raw_platform = output.get("platform") or output.get("utm_source")
    if raw_platform:
        normalized = raw_platform.casefold().replace("_", "").replace("-", "")
        platform = None
        if "snap" in normalized or "سناب" in normalized:
            platform = "snapchat"
        elif "tiktok" in normalized or "تيكتوك" in normalized:
            platform = "tiktok"
        elif any(value in normalized for value in ("meta", "facebook", "instagram")):
            platform = "meta"
        elif "google" in normalized or "adwords" in normalized:
            platform = "google"
        if platform:
            output["platform"] = platform
    return output


def _option_rows(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        value = value.get("values") or value.get("options") or [value]
    if not isinstance(value, list):
        return []
    rows: list[dict[str, Any]] = []
    for raw in value[:100]:
        if isinstance(raw, dict):
            option = {
                "option_id": _text(_first(raw.get("option_id"), raw.get("id"))),
                "name": _text(_first(raw.get("name"), raw.get("option_name"))),
                "value_id": _text(_first(raw.get("value_id"), raw.get("option_value_id"))),
                "value": _text(
                    _first(
                        raw.get("value"),
                        raw.get("option_value"),
                        raw.get("label"),
                    )
                ),
            }
        else:
            option = {"option_id": None, "name": None, "value_id": None, "value": _text(raw)}
        if any(item is not None for item in option.values()):
            rows.append(option)
    return rows


def _normalise_item(item: Any) -> dict[str, Any] | None:
    source = _mapping(item)
    product = _mapping(source.get("product"))
    variant = _mapping(source.get("variant"))
    product_id = _text(
        _first(source.get("product_id"), product.get("id"), source.get("id"))
    )
    variant_id = _text(_first(source.get("variant_id"), variant.get("id")))
    sku = _text(_first(source.get("sku"), variant.get("sku"), product.get("sku")))
    name = _text(_first(source.get("name"), product.get("name"), variant.get("name")))
    quantity = _number(_first(source.get("quantity"), source.get("qty"), 1))
    unit_price_source = _first(source.get("price"), source.get("unit_price"))
    total_price_source = _first(source.get("total"), source.get("total_price"))
    row = {
        "product_id": product_id,
        "variant_id": variant_id,
        "sku": sku,
        "name": name[:500] if name else None,
        "quantity": quantity,
        "unit_price": _number(unit_price_source),
        "total_price": _number(total_price_source),
        "currency": _currency(unit_price_source, total_price_source),
        "options": _option_rows(
            _first(
                source.get("options"),
                source.get("product_options"),
                variant.get("options"),
            )
        ),
    }
    if not any(value is not None for value in row.values()):
        return None
    return row


def _items(data: dict[str, Any]) -> list[dict[str, Any]]:
    raw = _first(
        data.get("items"),
        data.get("products"),
        _nested(data, "cart", "items"),
        _nested(data, "cart", "products"),
    )
    if not isinstance(raw, list):
        return []
    output: list[dict[str, Any]] = []
    for item in raw[:500]:
        normalized = _normalise_item(item)
        if normalized:
            output.append(normalized)
    return output


def _address_private(value: Any) -> dict[str, str]:
    source = _mapping(value)
    aliases = {
        "country": ("country", "country_name"),
        "country_code": ("country_code", "code"),
        "city": ("city", "city_name"),
        "district": ("district", "district_name"),
        "street": ("street", "address_line", "description"),
        "postal_code": ("postal_code", "postcode", "zip"),
        "building_number": ("building_number",),
        "additional_number": ("additional_number",),
        "short_address": ("short_address", "national_address"),
    }
    result: dict[str, str] = {}
    for target, fields in aliases.items():
        rendered = _text(_first(*(source.get(field) for field in fields)))
        if rendered:
            result[target] = rendered[:1000]
    return result


def _customer_private_context(event_body: dict[str, Any]) -> dict[str, Any]:
    data = _mapping(event_body.get("data"))
    cart = _mapping(data.get("cart"))
    source = cart or data
    customer = _mapping(
        _first(
            source.get("customer"),
            data.get("customer"),
            source.get("customer_data"),
            source.get("contact"),
        )
    )
    profile: dict[str, Any] = {}
    fields = {
        "name": ("full_name", "name"),
        "first_name": ("first_name",),
        "last_name": ("last_name",),
        "email": ("email",),
        "mobile": ("mobile", "phone", "mobile_number", "phone_number"),
        "gender": ("gender",),
    }
    for target, aliases in fields.items():
        rendered = _text(_first(*(customer.get(alias) for alias in aliases)))
        if rendered:
            profile[target] = rendered[:1000]
    for target, values in {
        "shipping_address": (
            customer.get("shipping_address"),
            source.get("shipping_address"),
        ),
        "billing_address": (
            customer.get("billing_address"),
            source.get("billing_address"),
        ),
        "address": (customer.get("address"), source.get("address")),
    }.items():
        address = _address_private(_first(*values))
        if address:
            profile[target] = address
    return {
        "external_customer_id": _text(
            _first(
                customer.get("id"),
                source.get("customer_id"),
                data.get("customer_id"),
            )
        ),
        "email": profile.get("email"),
        "mobile": profile.get("mobile"),
        "private_profile": profile,
    }


def _cart_private_context(event_body: dict[str, Any]) -> dict[str, str]:
    data = _mapping(event_body.get("data"))
    cart = _mapping(data.get("cart"))
    source = cart or data
    urls = _mapping(source.get("urls"))
    coupon = _mapping(_first(source.get("coupon"), data.get("coupon")))
    recovery_url = _text(
        _first(
            source.get("recovery_url"),
            source.get("checkout_url"),
            source.get("cart_url"),
            source.get("url"),
            urls.get("checkout"),
            urls.get("recovery"),
            urls.get("cart"),
        )
    )
    coupon_code = _text(
        _first(
            source.get("coupon_code"),
            data.get("coupon_code"),
            coupon.get("code"),
            coupon.get("coupon_code"),
        )
    )
    private: dict[str, str] = {}
    if recovery_url:
        private["recovery_url"] = recovery_url[:10000]
    if coupon_code:
        private["coupon_code"] = coupon_code[:500]
    return private


def _record_attribution(data: dict[str, Any]) -> dict[str, str]:
    raw_by_source = _mapping(data.get("raw_by_source"))
    salla_direct = _mapping(raw_by_source.get("salla_direct"))
    merged = _attribution(salla_direct)
    merged.update(_attribution(data))
    return merged


async def _linked_order_attribution(
    db: Any,
    *,
    user_id: str,
    order_number: str | None,
    order_id: str | None,
) -> dict[str, str]:
    selector: dict[str, Any] | None = None
    if order_number:
        selector = {"user_id": user_id, "order_number": str(order_number)}
    elif order_id:
        selector = {"user_id": user_id, "order_id": str(order_id)}
    if selector is None:
        return {}
    order = await db.unified_orders.find_one(selector, {"_id": 0})
    return _record_attribution(order or {})


def normalize_abandoned_cart_event(
    event_body: dict[str, Any],
    *,
    ingestion_source: str = "salla_verified_webhook",
) -> dict[str, Any] | None:
    """Return a stable analytics record without copying plaintext PII."""
    event_name = _text(event_body.get("event")) or "abandoned.cart"
    if event_name not in ABANDONED_CART_EVENTS:
        return None
    data = _mapping(event_body.get("data"))
    cart = _mapping(data.get("cart"))
    source = cart or data
    cart_id = _text(
        _first(
            source.get("id"),
            source.get("cart_id"),
            data.get("cart_id"),
            source.get("reference_id"),
        )
    )
    if not cart_id:
        return None
    status_source = _first(source.get("status"), data.get("status"))
    if isinstance(status_source, dict):
        status_source = _first(
            status_source.get("slug"),
            status_source.get("code"),
            status_source.get("name"),
            status_source.get("status"),
        )
    status = _text(
        _first(
            status_source,
            "purchased" if event_name.endswith(".purchased") else None,
        )
    )
    status_normalized = (status or "unknown").strip().lower()
    purchased = event_name.endswith(".purchased") or status_normalized == "purchased"
    created_at = _iso(_first(source.get("created_at"), data.get("created_at")))
    updated_at = _iso(
        _first(
            source.get("updated_at"),
            data.get("updated_at"),
            event_body.get("created_at"),
            created_at,
        )
    )
    total_source = _first(
        source.get("total"),
        source.get("total_amount"),
        _nested(source, "amounts", "total"),
        data.get("total"),
    )
    subtotal_source = _first(
        source.get("subtotal"),
        _nested(source, "amounts", "subtotal"),
    )
    discount_source = _first(
        source.get("discount"),
        _nested(source, "amounts", "discount"),
    )
    order = _mapping(_first(source.get("order"), data.get("order")))
    order_id = _text(
        _first(
            source.get("order_id"),
            data.get("order_id"),
            order.get("id"),
            order.get("reference_id"),
        )
    )
    order_number = _text(
        _first(
            source.get("order_number"),
            data.get("order_number"),
            order.get("number"),
            order.get("reference_id"),
        )
    )
    private_context = _cart_private_context(event_body)
    return {
        "schema_version": ABANDONED_CART_SCHEMA_VERSION,
        "merchant_id": _text(event_body.get("merchant")),
        "cart_id": cart_id,
        "status": "purchased" if purchased else status_normalized,
        "purchased": purchased,
        "order_id": order_id,
        "order_number": order_number,
        "currency": _currency(
            source.get("currency"),
            data.get("currency"),
            total_source,
            subtotal_source,
        ),
        "total": _number(total_source),
        "subtotal": _number(subtotal_source),
        "discount": _number(discount_source),
        "items": _items(source),
        "attribution": _record_attribution(data),
        "coupon_present": bool(private_context.get("coupon_code")),
        "recovery_url_present": bool(private_context.get("recovery_url")),
        "cart_created_at": created_at,
        "cart_updated_at": updated_at,
        "source_event": event_name,
        "source": ingestion_source,
        "pii_stored": False,
        "plaintext_pii_stored": False,
    }


def _fingerprint(record: dict[str, Any]) -> str:
    canonical = json.dumps(
        record,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def _owner_for_merchant(db: Any, merchant_id: str | None) -> str | None:
    if not merchant_id:
        return None
    store_selector: Any = (
        {"$in": [merchant_id, int(merchant_id)]}
        if merchant_id.isdigit()
        else merchant_id
    )
    integration = await db.salla_integrations.find_one(
        {"store_id": store_selector},
        {"_id": 0, "user_id": 1},
    )
    return _text((integration or {}).get("user_id"))


async def persist_abandoned_cart_event(
    db: Any,
    event_body: dict[str, Any],
    *,
    user_id: str | None = None,
    source: str = "salla_verified_webhook",
) -> dict[str, Any]:
    """Idempotently store one verified webhook/backfill cart snapshot."""
    record = normalize_abandoned_cart_event(
        event_body,
        ingestion_source=source,
    )
    if not record:
        return {"attempted": True, "synced": False, "reason": "cart_id_missing"}
    merchant_id = record.get("merchant_id")
    if not merchant_id:
        return {
            "attempted": True,
            "synced": False,
            "reason": "merchant_id_missing",
            "pii_stored": False,
        }
    owner_id = _text(user_id) or await _owner_for_merchant(db, merchant_id)
    if not owner_id:
        # Multi-tenant records must never be persisted without an owner.  The
        # verified audit capture remains available with PII redacted, while
        # the cart-specific collections stay empty until the Salla merchant is
        # bound to a Mezan tenant.
        return {
            "attempted": True,
            "synced": False,
            "reason": "owner_not_found",
            "pii_stored": False,
            "plaintext_pii_stored": False,
        }
    record["user_id"] = owner_id

    customer_context = _customer_private_context(event_body)
    customer_link = await resolve_customer_identity(
        db,
        user_id=owner_id,
        merchant_id=merchant_id,
        source_system="salla",
        external_customer_id=customer_context.get("external_customer_id"),
        email=customer_context.get("email"),
        mobile=customer_context.get("mobile"),
        private_profile=customer_context.get("private_profile") or {},
        observed_at=record.get("cart_updated_at"),
    )
    if customer_link:
        record["customer_identity_id"] = customer_link["customer_identity_id"]

    customer_orders_linked = 0
    if customer_link:
        customer_orders_linked = await link_unified_customer_orders(
            db,
            user_id=owner_id,
            customer_identity_id=customer_link["customer_identity_id"],
            external_customer_id=customer_context.get("external_customer_id"),
            email=customer_context.get("email"),
            mobile=customer_context.get("mobile"),
            order_number=record.get("order_number"),
        )

    private_context = _cart_private_context(event_body)
    private_context_ciphertext = encrypt_private_payload(private_context)

    direct_attribution = dict(record.get("attribution") or {})
    order_attribution = await _linked_order_attribution(
        db,
        user_id=owner_id,
        order_number=record.get("order_number"),
        order_id=record.get("order_id"),
    )
    if order_attribution:
        merged_attribution = dict(order_attribution)
        merged_attribution.update(direct_attribution)
        record["attribution"] = merged_attribution
        record["attribution_method"] = (
            "cart_event" if direct_attribution else "linked_order"
        )
    elif direct_attribution:
        record["attribution_method"] = "cart_event"

    event_hash = _fingerprint(record)
    now = datetime.now(timezone.utc)
    event_selector = {
        "merchant_id": merchant_id,
        "cart_id": record["cart_id"],
        "event_hash": event_hash,
    }
    event_result = await getattr(db, ABANDONED_CART_EVENT_COLLECTION).update_one(
        event_selector,
        {
            "$setOnInsert": {
                **event_selector,
                "user_id": owner_id,
                "event": record["source_event"],
                "record": record,
                "first_received_at": now,
                "created_at": now,
                "pii_stored": False,
                "plaintext_pii_stored": False,
                "customer_private_profile_encrypted": bool(
                    customer_link and customer_link.get("private_profile_encrypted")
                ),
            },
            "$set": {"last_received_at": now, "updated_at": now},
            "$inc": {"delivery_count": 1},
        },
        upsert=True,
    )

    identity = {"merchant_id": merchant_id, "cart_id": record["cart_id"]}
    existing = await getattr(db, ABANDONED_CART_COLLECTION).find_one(
        identity,
        {
            "_id": 0,
            "customer_identity_id": 1,
            "cart_updated_at": 1,
            "purchased": 1,
            "first_seen_at": 1,
            "attribution": 1,
            "attribution_first_touch": 1,
            "attribution_last_touch": 1,
            "private_cart_context_encrypted": 1,
        },
    )
    incoming_time = _datetime(record.get("cart_updated_at"))
    existing_time = _datetime((existing or {}).get("cart_updated_at"))
    out_of_order = bool(
        existing_time and incoming_time and incoming_time < existing_time
    )
    purchase_upgrade = bool(
        record.get("purchased") and not (existing or {}).get("purchased")
    )
    purchase_downgrade = bool(
        (existing or {}).get("purchased") and not record.get("purchased")
    )

    if purchase_downgrade or (out_of_order and not purchase_upgrade):
        # Preserve the newer operational snapshot, but still allow an older
        # V1 record to receive additive V2 memory fields.  Historical Salla
        # backfills often return a snapshot older than a webhook already held
        # by Mezan.  Treating the entire event as ignored left those carts
        # without their resolved customer identity even though the progress
        # counter correctly reported that an identity had been found.
        safe_enrichment: dict[str, Any] = {
            "last_received_at": now,
            "updated_at": now,
            "schema_version": ABANDONED_CART_SCHEMA_VERSION,
            "user_id": owner_id,
            "pii_stored": False,
            "plaintext_pii_stored": False,
        }
        if customer_link and not (existing or {}).get("customer_identity_id"):
            safe_enrichment.update(
                {
                    "customer_identity_id": customer_link["customer_identity_id"],
                    "customer_private_profile_encrypted": bool(
                        customer_link.get("private_profile_encrypted")
                    ),
                }
            )
        if (
            private_context_ciphertext
            and not (existing or {}).get("private_cart_context_encrypted")
        ):
            safe_enrichment.update(
                {
                    "private_cart_ciphertext": private_context_ciphertext,
                    "private_cart_fields": sorted(private_context),
                    "private_cart_schema_version": 1,
                    "private_cart_context_encrypted": True,
                }
            )
        attribution = dict(record.get("attribution") or {})
        if attribution and not (existing or {}).get("attribution"):
            safe_enrichment["attribution"] = attribution
            safe_enrichment["attribution_method"] = record.get(
                "attribution_method"
            )
            if not (existing or {}).get("attribution_first_touch"):
                safe_enrichment["attribution_first_touch"] = attribution
            if not (existing or {}).get("attribution_last_touch"):
                safe_enrichment["attribution_last_touch"] = attribution
        await getattr(db, ABANDONED_CART_COLLECTION).update_one(
            identity,
            {
                "$set": safe_enrichment,
                "$inc": {
                    "delivery_count": 1,
                    (
                        "ignored_after_purchase_count"
                        if purchase_downgrade
                        else "ignored_out_of_order_count"
                    ): 1,
                },
                "$addToSet": {"source_events": record["source_event"]},
            },
            upsert=True,
        )
        if customer_link:
            await attach_customer_activity(
                db,
                user_id=owner_id,
                customer_identity_id=customer_link["customer_identity_id"],
                cart_id=record["cart_id"],
                order_number=(
                    record.get("order_number") if record.get("purchased") else None
                ),
                activity_at=incoming_time or now,
            )
        return {
            "attempted": True,
            "synced": True,
            "created": False,
            "event_created": event_result.upserted_id is not None,
            "cart_id": record["cart_id"],
            "ignored_out_of_order": out_of_order,
            "ignored_after_purchase": purchase_downgrade,
            "pii_stored": False,
            "plaintext_pii_stored": False,
            "customer_identity_linked": bool(customer_link),
            "private_context_encrypted": bool(private_context_ciphertext),
            "attributed": bool(record.get("attribution")),
            "order_linked": bool(record.get("order_id") or record.get("order_number")),
            "customer_orders_linked": customer_orders_linked,
        }

    snapshot = dict(record)
    snapshot.update(
        {
            "last_received_at": now,
            "updated_at": now,
            "pii_stored": False,
            "plaintext_pii_stored": False,
            "customer_private_profile_encrypted": bool(
                customer_link and customer_link.get("private_profile_encrypted")
            ),
        }
    )
    attribution = dict(record.get("attribution") or {})
    if attribution:
        snapshot["attribution_last_touch"] = attribution
        if not (existing or {}).get("attribution_first_touch"):
            snapshot["attribution_first_touch"] = attribution
    else:
        # Sparse status events must not erase previously captured attribution.
        snapshot.pop("attribution", None)
    if private_context_ciphertext:
        snapshot["private_cart_ciphertext"] = private_context_ciphertext
        snapshot["private_cart_fields"] = sorted(private_context)
        snapshot["private_cart_schema_version"] = 1
        snapshot["private_cart_context_encrypted"] = True
    if purchase_upgrade:
        snapshot["converted_at"] = incoming_time or now
    if out_of_order and purchase_upgrade:
        snapshot.pop("cart_updated_at", None)
        snapshot.pop("cart_created_at", None)
    set_on_insert: dict[str, Any] = {"first_seen_at": now, "created_at": now}
    snapshot_result = await getattr(db, ABANDONED_CART_COLLECTION).update_one(
        identity,
        {
            "$set": snapshot,
            "$setOnInsert": set_on_insert,
            "$inc": {"delivery_count": 1},
            "$addToSet": {"source_events": record["source_event"]},
        },
        upsert=True,
    )
    if customer_link:
        await attach_customer_activity(
            db,
            user_id=owner_id,
            customer_identity_id=customer_link["customer_identity_id"],
            cart_id=record["cart_id"],
            order_number=record.get("order_number") if record.get("purchased") else None,
            activity_at=incoming_time or now,
        )
    return {
        "attempted": True,
        "synced": True,
        "created": snapshot_result.upserted_id is not None,
        "event_created": event_result.upserted_id is not None,
        "cart_id": record["cart_id"],
        "ignored_out_of_order": False,
        "purchase_upgrade": purchase_upgrade,
        "pii_stored": False,
        "plaintext_pii_stored": False,
        "customer_identity_linked": bool(customer_link),
        "private_context_encrypted": bool(private_context_ciphertext),
        "attributed": bool(record.get("attribution")),
        "order_linked": bool(record.get("order_id") or record.get("order_number")),
        "customer_orders_linked": customer_orders_linked,
    }


def split_scopes(value: Any) -> frozenset[str]:
    if isinstance(value, str):
        return frozenset(part for part in value.replace(",", " ").split() if part)
    if isinstance(value, Iterable) and not isinstance(value, (bytes, dict)):
        return frozenset(str(part).strip() for part in value if str(part).strip())
    return frozenset()


async def require_carts_read(db: Any, user_id: str) -> dict[str, Any]:
    integration = await db.salla_integrations.find_one(
        {"user_id": user_id},
        {"_id": 0, "scope": 1, "store_id": 1, "status": 1},
    )
    if not integration or ABANDONED_CART_SCOPE not in split_scopes(
        integration.get("scope")
    ):
        raise AbandonedCartScopeError(
            "Salla has not granted carts.read to the current store token yet."
        )
    return integration


def _payload_rows(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    data = payload.get("data")
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    if isinstance(data, dict):
        for key in ("data", "items", "carts", "results"):
            rows = data.get(key)
            if isinstance(rows, list):
                return [row for row in rows if isinstance(row, dict)]
    return []


def _total_pages(payload: dict[str, Any]) -> int | None:
    pagination = _mapping(
        _first(
            payload.get("pagination"),
            _nested(payload, "data", "pagination"),
        )
    )
    value = _first(
        pagination.get("totalPages"),
        pagination.get("total_pages"),
        pagination.get("last_page"),
    )
    number = _number(value)
    return int(number) if number is not None and number >= 1 else None


async def backfill_abandoned_carts(
    db: Any,
    user_id: str,
    *,
    call_provider: Callable[..., Awaitable[dict[str, Any]]],
    max_pages: int = MAX_BACKFILL_PAGES,
    per_page: int = SALLA_PAGE_SIZE,
    progress_hook: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
) -> dict[str, Any]:
    """Read Salla pages sequentially after an explicit scope gate."""
    integration = await require_carts_read(db, user_id)
    bounded_pages = max(1, min(int(max_pages), MAX_BACKFILL_PAGES))
    bounded_per_page = max(1, min(int(per_page), SALLA_PAGE_SIZE))
    merchant_id = _text(integration.get("store_id"))
    pages_fetched = 0
    rows_seen = 0
    rows_saved = 0
    created = 0
    updated = 0
    errors_count = 0
    identity_linked = 0
    attributed = 0
    order_linked = 0
    private_context_encrypted = 0
    customer_orders_linked = 0
    stopped_reason = "max_pages_reached"

    for page in range(1, bounded_pages + 1):
        retry_number = 0
        while True:
            try:
                payload = await asyncio.wait_for(
                    call_provider(
                        db,
                        user_id,
                        "GET",
                        "/carts/abandoned",
                        params={"page": page, "per_page": bounded_per_page},
                    ),
                    timeout=SALLA_PAGE_REQUEST_TIMEOUT_SECONDS,
                )
                break
            except Exception as exc:  # noqa: BLE001 - provider owns error type
                status_code = int(getattr(exc, "status_code", 0) or 0)
                transient_transport = isinstance(
                    exc,
                    (asyncio.TimeoutError, httpx.TransportError),
                )
                transient = (
                    transient_transport or status_code == 429 or status_code >= 500
                )
                if not transient or retry_number >= 5:
                    raise
                await asyncio.sleep(min(2**retry_number, 8))
                retry_number += 1
        pages_fetched += 1
        rows = _payload_rows(payload)
        rows_seen += len(rows)
        for row in rows:
            event_body = {
                "event": "abandoned.cart",
                "merchant": merchant_id,
                "created_at": _first(row.get("updated_at"), row.get("created_at")),
                "data": row,
            }
            result = await persist_abandoned_cart_event(
                db,
                event_body,
                user_id=user_id,
                source="salla_abandoned_carts_api",
            )
            if result.get("synced"):
                rows_saved += 1
                identity_linked += int(bool(result.get("customer_identity_linked")))
                attributed += int(bool(result.get("attributed")))
                order_linked += int(bool(result.get("order_linked")))
                private_context_encrypted += int(
                    bool(result.get("private_context_encrypted"))
                )
                customer_orders_linked += int(
                    result.get("customer_orders_linked") or 0
                )
                if result.get("created"):
                    created += 1
                else:
                    updated += 1
            else:
                errors_count += 1
        progress = {
            "pages_fetched": pages_fetched,
            "rows_seen": rows_seen,
            "rows_saved": rows_saved,
            "created": created,
            "updated": updated,
            "errors_count": errors_count,
            "identity_linked": identity_linked,
            "attributed": attributed,
            "order_linked": order_linked,
            "private_context_encrypted": private_context_encrypted,
            "customer_orders_linked": customer_orders_linked,
        }
        if progress_hook is not None:
            try:
                await progress_hook(progress)
            except Exception:  # noqa: BLE001 - monitoring cannot stop ingestion
                pass
        total_pages = _total_pages(payload)
        if total_pages is not None and page >= total_pages:
            stopped_reason = "pagination_complete"
            break
        if len(rows) < bounded_per_page:
            stopped_reason = "short_page"
            break

    purchased_flags_repaired = await reconcile_purchased_cart_flags(db, user_id)

    return {
        "ok": True,
        "scope": ABANDONED_CART_SCOPE,
        "scope_verified": True,
        "pages_fetched": pages_fetched,
        "rows_seen": rows_seen,
        "rows_saved": rows_saved,
        "created": created,
        "updated": updated,
        "errors_count": errors_count,
        "identity_linked": identity_linked,
        "attributed": attributed,
        "order_linked": order_linked,
        "private_context_encrypted": private_context_encrypted,
        "customer_orders_linked": customer_orders_linked,
        "purchased_flags_repaired": purchased_flags_repaired,
        "stopped_reason": stopped_reason,
        "provider_write_reached": False,
        "pii_stored": False,
        "plaintext_pii_stored": False,
        "schema_version": ABANDONED_CART_SCHEMA_VERSION,
    }


async def reconcile_purchased_cart_flags(db: Any, user_id: str) -> int:
    """Repair legacy snapshots from either durable verified purchase marker.

    Older deployments could persist ``abandoned.cart.purchased`` in the
    snapshot's append-only ``source_events`` audit trail without promoting the
    operational ``purchased`` flag.  Deployments from before cart-specific
    ingestion still retained the sanitized payload in
    ``salla_webhook_event_captures`` after signature verification.  Both
    markers are merchant- and tenant-scoped, so the repair remains safe and
    idempotent after a read-only backfill.
    """
    now = datetime.now(timezone.utc)
    carts = getattr(db, ABANDONED_CART_COLLECTION)
    source_event_result = await carts.update_many(
        {
            "user_id": user_id,
            "purchased": {"$ne": True},
            "source_events": {"$in": ["abandoned.cart.purchased"]},
        },
        {
            "$set": {
                "purchased": True,
                "status": "purchased",
                "purchase_state_source": "abandoned.cart.purchased",
                "purchase_reconciled_at": now,
                "updated_at": now,
            }
        },
    )
    repaired = int(getattr(source_event_result, "modified_count", 0) or 0)

    integration = await db.salla_integrations.find_one(
        {"user_id": user_id},
        {"_id": 0, "store_id": 1},
    )
    merchant_id = _text((integration or {}).get("store_id"))
    if not merchant_id:
        return repaired

    capture_cursor = db.salla_webhook_event_captures.find(
        {
            "merchant_id": merchant_id,
            "event": "abandoned.cart.purchased",
            "verified_before_capture": True,
        },
        {"_id": 0, "payload": 1},
    )
    captures = await capture_cursor.to_list(length=10_000)
    captured_cart_ids: set[str] = set()
    for capture in captures:
        payload = _mapping(capture.get("payload"))
        record = normalize_abandoned_cart_event(payload)
        if record and record.get("purchased") and record.get("cart_id"):
            captured_cart_ids.add(str(record["cart_id"]))

    if not captured_cart_ids:
        return repaired

    capture_result = await carts.update_many(
        {
            "user_id": user_id,
            "merchant_id": merchant_id,
            "cart_id": {"$in": sorted(captured_cart_ids)},
            "purchased": {"$ne": True},
        },
        {
            "$set": {
                "purchased": True,
                "status": "purchased",
                "purchase_state_source": (
                    "verified_webhook_capture:abandoned.cart.purchased"
                ),
                "purchase_reconciled_at": now,
                "updated_at": now,
            },
            "$addToSet": {"source_events": "abandoned.cart.purchased"},
        },
    )
    repaired += int(getattr(capture_result, "modified_count", 0) or 0)
    return repaired


async def ensure_abandoned_cart_indexes(db: Any) -> None:
    await ensure_customer_identity_indexes(db)
    await getattr(db, ABANDONED_CART_COLLECTION).create_index(
        [("merchant_id", 1), ("cart_id", 1)],
        unique=True,
        name="salla_abandoned_cart_identity_unique",
    )
    await getattr(db, ABANDONED_CART_COLLECTION).create_index(
        [("user_id", 1), ("cart_updated_at", -1)],
        name="salla_abandoned_cart_user_updated",
    )
    await getattr(db, ABANDONED_CART_COLLECTION).create_index(
        [("user_id", 1), ("purchased", 1), ("updated_at", -1)],
        name="salla_abandoned_cart_user_status",
    )
    await getattr(db, ABANDONED_CART_COLLECTION).create_index(
        [("user_id", 1), ("customer_identity_id", 1), ("cart_updated_at", -1)],
        name="salla_abandoned_cart_customer_history",
    )
    await getattr(db, ABANDONED_CART_COLLECTION).create_index(
        [("user_id", 1), ("attribution.campaign_id", 1), ("cart_updated_at", -1)],
        name="salla_abandoned_cart_campaign_history",
    )
    await getattr(db, ABANDONED_CART_COLLECTION).create_index(
        [("user_id", 1), ("order_number", 1)],
        name="salla_abandoned_cart_order_link",
    )
    await getattr(db, ABANDONED_CART_EVENT_COLLECTION).create_index(
        [("merchant_id", 1), ("cart_id", 1), ("event_hash", 1)],
        unique=True,
        name="salla_abandoned_cart_event_unique",
    )
    await getattr(db, ABANDONED_CART_EVENT_COLLECTION).create_index(
        [("user_id", 1), ("event", 1), ("last_received_at", -1)],
        name="salla_abandoned_cart_event_user_status",
    )


__all__ = [
    "ABANDONED_CART_COLLECTION",
    "ABANDONED_CART_EVENTS",
    "ABANDONED_CART_SCHEMA_VERSION",
    "ABANDONED_CART_SCOPE",
    "AbandonedCartScopeError",
    "backfill_abandoned_carts",
    "ensure_abandoned_cart_indexes",
    "normalize_abandoned_cart_event",
    "persist_abandoned_cart_event",
    "reconcile_purchased_cart_flags",
    "require_carts_read",
    "split_scopes",
]
