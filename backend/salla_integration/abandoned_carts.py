"""Privacy-minimised Salla abandoned-cart ingestion and historical backfill.

The webhook path calls :func:`persist_abandoned_cart_event` only after the
existing Easy Mode signature/token verification succeeds.  Historical reads
remain dormant until the stored OAuth grant explicitly contains ``carts.read``.
No customer name, email, phone number, or address is copied into either
collection owned by this module.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Iterable

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
ABANDONED_CART_SCHEMA_VERSION = 1
# The connected Amasi store already has more than 6,000 abandoned carts.  At
# Salla's conservative 30-row page size, a 200-page ceiling would silently
# stop before the historical read completes.  Keep the read bounded, but high
# enough to cover the current catalogue with room for growth.
MAX_BACKFILL_PAGES = 500
SALLA_PAGE_SIZE = 30


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
    "campaign_id": frozenset({"campaign_id", "campaignid", "sc_campaign_id"}),
    "ad_squad_id": frozenset(
        {"ad_squad_id", "adsquad_id", "adset_id", "ad_set_id", "sc_ad_squad_id"}
    ),
    "ad_id": frozenset({"ad_id", "adid", "creative_id", "sc_ad_id"}),
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
            if isinstance(child, (dict, list)):
                visit(child, depth=depth + 1)

    visit(source)
    return output


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


def normalize_abandoned_cart_event(
    event_body: dict[str, Any],
    *,
    ingestion_source: str = "salla_verified_webhook",
) -> dict[str, Any] | None:
    """Return a stable analytics record without copying customer PII."""
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
        "attribution": _attribution(data),
        "cart_created_at": created_at,
        "cart_updated_at": updated_at,
        "source_event": event_name,
        "source": ingestion_source,
        "pii_stored": False,
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
        }
    record["user_id"] = owner_id
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
            "cart_updated_at": 1,
            "purchased": 1,
            "first_seen_at": 1,
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
        await getattr(db, ABANDONED_CART_COLLECTION).update_one(
            identity,
            {
                "$set": {"last_received_at": now, "updated_at": now},
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
        return {
            "attempted": True,
            "synced": True,
            "created": False,
            "event_created": event_result.upserted_id is not None,
            "cart_id": record["cart_id"],
            "ignored_out_of_order": out_of_order,
            "ignored_after_purchase": purchase_downgrade,
            "pii_stored": False,
        }

    snapshot = dict(record)
    snapshot.update(
        {
            "last_received_at": now,
            "updated_at": now,
            "pii_stored": False,
        }
    )
    if out_of_order and purchase_upgrade:
        snapshot.pop("cart_updated_at", None)
        snapshot.pop("cart_created_at", None)
    snapshot_result = await getattr(db, ABANDONED_CART_COLLECTION).update_one(
        identity,
        {
            "$set": snapshot,
            "$setOnInsert": {"first_seen_at": now, "created_at": now},
            "$inc": {"delivery_count": 1},
            "$addToSet": {"source_events": record["source_event"]},
        },
        upsert=True,
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
    stopped_reason = "max_pages_reached"

    for page in range(1, bounded_pages + 1):
        retry_number = 0
        while True:
            try:
                payload = await call_provider(
                    db,
                    user_id,
                    "GET",
                    "/carts/abandoned",
                    params={"page": page, "per_page": bounded_per_page},
                )
                break
            except Exception as exc:  # noqa: BLE001 - provider owns error type
                status_code = int(getattr(exc, "status_code", 0) or 0)
                transient = status_code == 429 or status_code >= 500
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
        "stopped_reason": stopped_reason,
        "provider_write_reached": False,
        "pii_stored": False,
    }


async def ensure_abandoned_cart_indexes(db: Any) -> None:
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
    "ABANDONED_CART_SCOPE",
    "AbandonedCartScopeError",
    "backfill_abandoned_carts",
    "ensure_abandoned_cart_indexes",
    "normalize_abandoned_cart_event",
    "persist_abandoned_cart_event",
    "require_carts_read",
    "split_scopes",
]
