"""Privacy-minimised Snapchat Conversions API v3 purchase delivery.

Consent and safety contract
---------------------------
This module is disabled unless ``MEZAN_SNAPCHAT_CAPI_ENABLED=true`` is set in
Production.  When enabled it sends website PURCHASE events to one canonical
Snap Pixel.  Plaintext customer contact data is never stored in the CAPI outbox:
email, phone, city, country, postal code and external identifiers are normalised
and SHA-256 hashed before persistence.  IP address and User-Agent are deliberately
excluded.  Snap click/cookie attribution IDs are sent only when already present.

The Salla webhook only enqueues an idempotent event.  A server-side worker owns
provider delivery, retries and seven-day backfill, so Salla acknowledgement and
order persistence never depend on Snapchat availability.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import os
import re
import socket
import uuid
from contextlib import suppress
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Callable
from urllib.parse import parse_qs, urlsplit

import httpx
from fastapi import APIRouter, Depends
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from .snapchat_account_selection import _load_selected_accounts
from .snapchat_native_data_common import (
    BUSINESS_TIMEZONE,
    SNAPCHAT_PROVIDER_ID,
    SnapchatNativeSyncError,
    SnapchatSyncContext,
    _collection,
    _parse_datetime,
    _timezone,
)
from .snapchat_native_tracking_diagnostics import TRACKING_ASSET_COLLECTION

logger = logging.getLogger(__name__)

CAPI_ENABLED_ENV = "MEZAN_SNAPCHAT_CAPI_ENABLED"
CAPI_PIXEL_ID_ENV = "SNAPCHAT_CAPI_PIXEL_ID"
CAPI_SOURCE_URL_ENV = "SNAPCHAT_CAPI_EVENT_SOURCE_URL"
CAPI_INTERVAL_ENV = "MEZAN_SNAPCHAT_CAPI_INTERVAL_SECONDS"
CAPI_BACKFILL_DAYS_ENV = "MEZAN_SNAPCHAT_CAPI_BACKFILL_DAYS"
CAPI_BATCH_SIZE_ENV = "MEZAN_SNAPCHAT_CAPI_BATCH_SIZE"

DEFAULT_SOURCE_URL = "https://amasi-sa.com/"
DEFAULT_INTERVAL_SECONDS = 300
DEFAULT_BACKFILL_DAYS = 7
DEFAULT_BATCH_SIZE = 100
MAX_BACKFILL_DAYS = 7
MAX_BATCH_SIZE = 250
MAX_ATTEMPTS = 8
EVENT_MAX_AGE = timedelta(days=7)
LOCK_TTL = timedelta(minutes=10)
SENT_RETENTION = timedelta(days=30)
FAILED_RETENTION = timedelta(days=90)

OUTBOX_COLLECTION = "mezan_snapchat_capi_outbox_v1"
SCHEDULER_COLLECTION = "mezan_snapchat_capi_scheduler_v1"
SCHEDULER_ID = "snapchat-capi-purchase-worker"
SOURCE_MODE = "snapchat_conversions_api_v3_purchase_outbox"
CAPI_ENDPOINT = "https://tr.snapchat.com/v3/{pixel_id}/events"

PENDING_STATUSES = ("pending", "retry")
INELIGIBLE_STATUS_TOKENS = {
    "cancelled", "canceled", "refunded", "failed", "draft", "deleted",
    "void", "ملغي", "ملغى", "مسترجع",
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None = None) -> str:
    return (value or _utcnow()).astimezone(timezone.utc).isoformat()


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError):
        parsed = default
    return min(max(parsed, minimum), maximum)


def capi_enabled() -> bool:
    return str(os.environ.get(CAPI_ENABLED_ENV, "false")).strip().lower() in {
        "1", "true", "on", "yes", "enabled",
    }


def capi_interval_seconds() -> int:
    return _bounded_int(
        os.environ.get(CAPI_INTERVAL_ENV),
        default=DEFAULT_INTERVAL_SECONDS,
        minimum=60,
        maximum=3600,
    )


def capi_backfill_days() -> int:
    return _bounded_int(
        os.environ.get(CAPI_BACKFILL_DAYS_ENV),
        default=DEFAULT_BACKFILL_DAYS,
        minimum=1,
        maximum=MAX_BACKFILL_DAYS,
    )


def capi_batch_size() -> int:
    return _bounded_int(
        os.environ.get(CAPI_BATCH_SIZE_ENV),
        default=DEFAULT_BATCH_SIZE,
        minimum=1,
        maximum=MAX_BATCH_SIZE,
    )


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def normalize_email(value: Any) -> str | None:
    text = str(value or "").strip().lower()
    if not text or "@" not in text or len(text) > 320:
        return None
    return text


def _country_code(value: Any) -> str:
    text = str(value or "").strip().lower()
    mapping = {
        "sa": "966", "sau": "966", "saudi arabia": "966",
        "السعودية": "966", "المملكة العربية السعودية": "966",
        "ye": "967", "yem": "967", "yemen": "967", "اليمن": "967",
        "qa": "974", "qat": "974", "qatar": "974", "قطر": "974",
        "ae": "971", "are": "971", "uae": "971",
        "united arab emirates": "971", "الإمارات": "971",
    }
    return mapping.get(text, "")


def _country_iso(value: Any) -> str:
    text = str(value or "").strip().lower()
    mapping = {
        "sa": "sa", "sau": "sa", "saudi arabia": "sa",
        "السعودية": "sa", "المملكة العربية السعودية": "sa",
        "ye": "ye", "yem": "ye", "yemen": "ye", "اليمن": "ye",
        "qa": "qa", "qat": "qa", "qatar": "qa", "قطر": "qa",
        "ae": "ae", "are": "ae", "uae": "ae",
        "united arab emirates": "ae", "الإمارات": "ae",
    }
    return mapping.get(text, text[:2] if len(text) == 2 and text.isascii() else "")


def normalize_phone(value: Any, *, country: Any = None) -> str | None:
    digits = re.sub(r"\D+", "", str(value or ""))
    if digits.startswith("00"):
        digits = digits[2:]
    if not digits:
        return None
    code = _country_code(country)
    if digits.startswith("0") and code:
        digits = code + digits.lstrip("0")
    elif len(digits) == 9 and digits.startswith("5") and code == "966":
        digits = "966" + digits
    if len(digits) < 8 or len(digits) > 15:
        return None
    return digits


def _normalise_hash_text(value: Any) -> str | None:
    text = str(value or "").strip().lower()
    text = re.sub(r"[\s\-_,.;:/\\]+", "", text)
    return text or None


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _money(value: Any) -> float:
    if isinstance(value, dict):
        for key in ("amount", "value", "total", "price"):
            if value.get(key) not in (None, ""):
                return _money(value.get(key))
        return 0.0
    try:
        parsed = float(value or 0)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    return round(parsed, 2) if math.isfinite(parsed) and parsed > 0 else 0.0


def _walk_key(value: Any, keys: set[str], *, depth: int = 0) -> Any:
    if depth > 8:
        return None
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).strip().lower() in keys and child not in (None, "", [], {}):
                return child
        for child in value.values():
            found = _walk_key(child, keys, depth=depth + 1)
            if found not in (None, "", [], {}):
                return found
    elif isinstance(value, list):
        for child in value[:50]:
            found = _walk_key(child, keys, depth=depth + 1)
            if found not in (None, "", [], {}):
                return found
    return None


def _raw_order(order: dict[str, Any]) -> dict[str, Any]:
    sources = _dict(order.get("raw_by_source"))
    for source in ("salla_direct", "salla_webhook", "make_webhook"):
        value = sources.get(source)
        if isinstance(value, list):
            value = next((row for row in reversed(value) if isinstance(row, dict)), None)
        if isinstance(value, dict):
            return value
    raw = order.get("raw")
    return dict(raw) if isinstance(raw, dict) else {}


def _parse_datetime_value(value: Any) -> datetime | None:
    if isinstance(value, dict):
        value = value.get("date") or value.get("value")
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            try:
                day = date.fromisoformat(text[:10])
            except ValueError:
                return None
            parsed = datetime.combine(day, time.min, tzinfo=_timezone(BUSINESS_TIMEZONE))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_timezone(BUSINESS_TIMEZONE))
    return parsed.astimezone(timezone.utc)


def _event_time(order: dict[str, Any], raw: dict[str, Any], *, now: datetime) -> datetime:
    candidates = (
        raw.get("created_at"), raw.get("date"), order.get("order_date_raw"),
        order.get("salla_webhook_received_at"), order.get("created_at"),
        order.get("order_date"),
    )
    for candidate in candidates:
        parsed = _parse_datetime_value(candidate)
        if parsed:
            return min(parsed, now)
    return now


def _event_source_url(raw: dict[str, Any]) -> str:
    configured = str(os.environ.get(CAPI_SOURCE_URL_ENV) or DEFAULT_SOURCE_URL).strip()
    candidate = _walk_key(
        raw,
        {"event_source_url", "landing_page_url", "landing_url", "page_url", "checkout_url"},
    )
    for value in (candidate, configured, DEFAULT_SOURCE_URL):
        text = str(value or "").strip()
        try:
            parsed = urlsplit(text)
        except Exception:  # noqa: BLE001
            continue
        if parsed.scheme in {"http", "https"} and parsed.hostname:
            return f"{parsed.scheme}://{parsed.netloc}{parsed.path or '/'}"
    return DEFAULT_SOURCE_URL


def _click_id(raw: dict[str, Any]) -> str | None:
    direct = _walk_key(raw, {"sc_click_id", "sccid", "sc_cid", "sc-cid"})
    if direct:
        return str(direct).strip()[:500] or None
    url_value = _walk_key(
        raw,
        {"event_source_url", "landing_page_url", "landing_url", "page_url", "url"},
    )
    if url_value:
        try:
            query = parse_qs(urlsplit(str(url_value)).query)
            for key, values in query.items():
                if key.lower() == "sccid" and values:
                    return str(values[0]).strip()[:500] or None
        except Exception:  # noqa: BLE001
            pass
    return None


def _cookie_id(raw: dict[str, Any]) -> str | None:
    value = _walk_key(raw, {"sc_cookie1", "_scid", "snap_cookie"})
    return str(value).strip()[:500] if value not in (None, "") else None


def _status_eligible(order: dict[str, Any]) -> bool:
    text = " ".join(
        str(order.get(key) or "").strip().lower()
        for key in ("order_status_slug", "order_status")
    )
    return not any(token in text for token in INELIGIBLE_STATUS_TOKENS)


def _products(order: dict[str, Any], raw: dict[str, Any]) -> list[dict[str, Any]]:
    rows = order.get("products")
    if not isinstance(rows, list) or not rows:
        rows = raw.get("items") if isinstance(raw.get("items"), list) else []
    result: list[dict[str, Any]] = []
    for row in rows[:100]:
        if not isinstance(row, dict):
            continue
        product = _dict(row.get("product"))
        item_id = _text(
            row.get("sku") or row.get("product_id") or product.get("sku")
            or product.get("id") or row.get("id")
        )
        if not item_id:
            continue
        quantity = _money(row.get("quantity")) or 1.0
        price = _money(row.get("price") or _dict(row.get("amounts")).get("price"))
        item: dict[str, Any] = {
            "id": item_id[:200],
            "quantity": str(int(quantity) if quantity.is_integer() else quantity),
        }
        if price > 0:
            item["item_price"] = price
        result.append(item)
    return result


def build_snapchat_purchase_event(
    order: dict[str, Any],
    *,
    raw: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """Build a CAPI v3 PURCHASE event containing hashed customer identifiers."""
    current = (now or _utcnow()).astimezone(timezone.utc)
    raw_order = raw if isinstance(raw, dict) else _raw_order(order)
    customer = _dict(raw_order.get("customer"))
    receiver = _dict(raw_order.get("receiver"))
    address = _dict(
        order.get("shipping_address_raw")
        or _dict(raw_order.get("shipping")).get("address")
        or receiver.get("address")
        or customer.get("address")
    )

    order_number = _text(
        order.get("order_number") or raw_order.get("reference_id") or raw_order.get("id")
    )
    value = _money(
        order.get("total_amount")
        or _dict(raw_order.get("amounts")).get("total")
        or raw_order.get("total")
    )
    if not order_number or value <= 0 or not _status_eligible(order):
        return None

    occurred_at = _event_time(order, raw_order, now=current)
    if current - occurred_at > EVENT_MAX_AGE:
        return None

    country_raw = (
        order.get("shipping_country") or address.get("country")
        or address.get("country_code") or "SA"
    )
    email = normalize_email(
        customer.get("email") or receiver.get("email") or order.get("customer_email")
    )
    phone = normalize_phone(
        customer.get("mobile") or customer.get("phone")
        or receiver.get("mobile") or receiver.get("phone")
        or order.get("customer_mobile"),
        country=country_raw,
    )
    user_data: dict[str, Any] = {}
    if email:
        user_data["em"] = _sha256(email)
    if phone:
        user_data["ph"] = _sha256(phone)

    # Data-minimised consent: require a hashed contact signal and deliberately
    # avoid storing or sending IP address and User-Agent.
    if not any(key in user_data for key in ("em", "ph")):
        return None

    click_id = _click_id(raw_order)
    cookie_id = _cookie_id(raw_order)
    if click_id:
        user_data["sc_click_id"] = click_id
    if cookie_id:
        user_data["sc_cookie1"] = cookie_id

    customer_id = _text(customer.get("id") or order.get("customer_id"))
    if customer_id:
        user_data["external_id"] = _sha256(customer_id.lower())

    city = _normalise_hash_text(order.get("shipping_city") or address.get("city"))
    country = _country_iso(address.get("country_code") or country_raw)
    postal = _normalise_hash_text(
        order.get("shipping_postal_code") or address.get("postal_code")
    )
    if city:
        user_data["ct"] = _sha256(city)
    if country:
        user_data["country"] = _sha256(country)
    if postal:
        user_data["zp"] = _sha256(postal)

    currency = str(
        order.get("currency")
        or _dict(_dict(raw_order.get("amounts")).get("total")).get("currency")
        or "SAR"
    ).strip().upper() or "SAR"
    contents = _products(order, raw_order)
    custom_data: dict[str, Any] = {
        "currency": currency,
        "value": value,
        "order_id": order_number,
        "num_items": str(sum(float(item.get("quantity") or 0) for item in contents) or 1),
    }
    if contents:
        custom_data["contents"] = contents
        custom_data["content_ids"] = [item["id"] for item in contents]
        custom_data["content_type"] = "product"

    event = {
        "event_name": "PURCHASE",
        "event_time": int(occurred_at.timestamp()),
        "event_id": order_number,
        "action_source": "WEB",
        "event_source_url": _event_source_url(raw_order),
        "user_data": user_data,
        "custom_data": custom_data,
    }
    return event


def _payload_fingerprint(event: dict[str, Any]) -> str:
    canonical = json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return _sha256(canonical)


async def ensure_snapchat_capi_indexes(db: Any) -> None:
    outbox = _collection(db, OUTBOX_COLLECTION)
    await outbox.create_index(
        [("user_id", 1), ("event_id", 1)],
        unique=True,
        name="snapchat_capi_user_event_unique",
    )
    await outbox.create_index(
        [("status", 1), ("next_attempt_at", 1), ("created_at", 1)],
        name="snapchat_capi_pending_queue",
    )
    await outbox.create_index(
        [("purge_at", 1)],
        expireAfterSeconds=0,
        name="snapchat_capi_outbox_ttl",
    )


async def enqueue_snapchat_purchase_event(
    db: Any,
    *,
    user_id: str,
    order: dict[str, Any],
    raw: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    if not capi_enabled():
        return {"queued": False, "reason": "capi_disabled"}
    event = build_snapchat_purchase_event(order, raw=raw, now=now)
    if not event:
        return {"queued": False, "reason": "order_not_eligible_or_unmatchable"}
    await ensure_snapchat_capi_indexes(db)
    current = (now or _utcnow()).astimezone(timezone.utc)
    event_id = str(event["event_id"])
    document = {
        "user_id": str(user_id),
        "provider": SNAPCHAT_PROVIDER_ID,
        "event_id": event_id,
        "event_name": "PURCHASE",
        "source_order_number": event_id,
        "status": "pending",
        "attempts": 0,
        "next_attempt_at": current,
        "lock_owner": None,
        "lock_expires_at": None,
        "payload": event,
        "payload_fingerprint": _payload_fingerprint(event),
        "contains_plaintext_pii": False,
        "source_mode": SOURCE_MODE,
        "created_at": current,
        "updated_at": current,
        "provider_write_authorized": True,
        "campaign_write_reached": False,
        "accounting_write_reached": False,
        "qoyod_write_reached": False,
    }
    inserted = False
    try:
        result = await _collection(db, OUTBOX_COLLECTION).update_one(
            {"user_id": str(user_id), "event_id": event_id},
            {"$setOnInsert": document},
            upsert=True,
        )
        inserted = bool(result and result.upserted_id is not None)
    except DuplicateKeyError:
        inserted = False

    enriched = False
    if not inserted:
        # Repeated Salla order updates may contain a richer phone, email or
        # ScCid.  Improve only an unsent event; never reopen a sent purchase.
        update = await _collection(db, OUTBOX_COLLECTION).update_one(
            {
                "user_id": str(user_id),
                "event_id": event_id,
                "status": {"$in": ["pending", "retry"]},
            },
            {"$set": {
                "payload": event,
                "payload_fingerprint": _payload_fingerprint(event),
                "updated_at": current,
            }},
        )
        enriched = bool(getattr(update, "matched_count", 0))
    return {
        "queued": inserted,
        "duplicate": not inserted,
        "enriched_pending_event": enriched,
        "event_id": event_id,
        "plaintext_customer_contact_stored": False,
    }


async def cancel_pending_snapchat_purchase_event(
    db: Any,
    *,
    user_id: str,
    event_id: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = (now or _utcnow()).astimezone(timezone.utc)
    result = await _collection(db, OUTBOX_COLLECTION).update_many(
        {
            "user_id": str(user_id),
            "event_id": str(event_id),
            "status": {"$in": ["pending", "retry"]},
        },
        {"$set": {
            "status": "cancelled",
            "payload": None,
            "payload_redacted_after_cancel": True,
            "lock_owner": None,
            "lock_expires_at": None,
            "last_error": {
                "code": "source_order_cancelled_before_delivery",
                "retryable": False,
            },
            "purge_at": current + FAILED_RETENTION,
            "updated_at": current,
        }},
    )
    return {
        "queued": False,
        "cancelled_pending": int(getattr(result, "modified_count", 0)),
        "event_id": str(event_id),
        "reason": "order_ineligible",
    }


async def _resolve_salla_user_id(db: Any, merchant_id: Any) -> str | None:
    values: list[Any] = []
    text = _text(merchant_id)
    if text:
        values.append(text)
        try:
            values.append(int(text))
        except (TypeError, ValueError, OverflowError):
            pass
    if not values:
        return None
    query = {"store_id": {"$in": values}}
    row = await _collection(db, "salla_integrations").find_one(
        query,
        {"_id": 0, "user_id": 1},
        sort=[("updated_at", -1)],
    )
    return _text((row or {}).get("user_id"))


def _salla_event_order(event_body: dict[str, Any]) -> dict[str, Any]:
    data = _dict(event_body.get("data"))
    order = _dict(data.get("order"))
    if not order:
        return data
    merged = dict(order)
    for key, value in data.items():
        if key != "order" and key not in merged:
            merged[key] = value
    return merged


async def enqueue_snapchat_purchase_from_salla_event(
    db: Any,
    event_body: dict[str, Any],
) -> dict[str, Any]:
    event_name = str(event_body.get("event") or "").strip()
    if event_name not in {"order.created", "order.updated", "order.status.updated"}:
        return {"queued": False, "reason": "not_order_event"}
    user_id = await _resolve_salla_user_id(db, event_body.get("merchant"))
    raw = _salla_event_order(event_body)
    if not user_id or not raw:
        return {"queued": False, "reason": "owner_or_order_missing"}
    customer = _dict(raw.get("customer"))
    amounts = _dict(raw.get("amounts"))
    total = amounts.get("total") or raw.get("total")
    status = raw.get("status")
    status_obj = _dict(status)
    order = {
        "order_number": raw.get("reference_id") or raw.get("order_number") or raw.get("id"),
        "order_date_raw": raw.get("created_at") or raw.get("date"),
        "order_status": status_obj.get("name") or status_obj.get("customized") or status,
        "order_status_slug": status_obj.get("slug"),
        "total_amount": _money(total),
        "currency": _dict(total).get("currency") if isinstance(total, dict) else "SAR",
        "customer_mobile": customer.get("mobile") or customer.get("phone"),
        "products": raw.get("items") if isinstance(raw.get("items"), list) else [],
    }
    if not _status_eligible(order):
        event_id = _text(order.get("order_number"))
        if not event_id:
            return {"queued": False, "reason": "cancelled_order_id_missing"}
        return await cancel_pending_snapchat_purchase_event(
            db,
            user_id=user_id,
            event_id=event_id,
        )
    return await enqueue_snapchat_purchase_event(
        db,
        user_id=user_id,
        order=order,
        raw=raw,
    )


async def enqueue_recent_purchase_events(
    db: Any,
    user_id: str,
    *,
    days: int | None = None,
    limit: int = 1000,
    now: datetime | None = None,
) -> dict[str, Any]:
    if not capi_enabled():
        return {"scanned": 0, "queued": 0, "disabled": True}
    current = (now or _utcnow()).astimezone(timezone.utc)
    safe_days = min(max(int(days or capi_backfill_days()), 1), MAX_BACKFILL_DAYS)
    start = current.astimezone(_timezone(BUSINESS_TIMEZONE)).date() - timedelta(days=safe_days - 1)
    cursor = _collection(db, "unified_orders").find(
        {
            "user_id": str(user_id),
            "order_date": {"$gte": start.isoformat()},
            "total_amount": {"$gt": 0},
        },
        {
            "_id": 0,
            "user_id": 1,
            "order_number": 1,
            "order_date": 1,
            "order_date_raw": 1,
            "created_at": 1,
            "salla_webhook_received_at": 1,
            "order_status": 1,
            "order_status_slug": 1,
            "total_amount": 1,
            "currency": 1,
            "customer_id": 1,
            "customer_email": 1,
            "customer_mobile": 1,
            "shipping_address_raw": 1,
            "shipping_city": 1,
            "shipping_country": 1,
            "shipping_postal_code": 1,
            "products": 1,
            "raw_by_source": 1,
            "raw": 1,
        },
    )
    if hasattr(cursor, "sort"):
        cursor = cursor.sort("order_date", -1)
    if hasattr(cursor, "limit"):
        cursor = cursor.limit(min(max(int(limit), 1), 5000))
    orders = await cursor.to_list(length=min(max(int(limit), 1), 5000))
    queued = duplicates = skipped = 0
    for order in orders:
        result = await enqueue_snapchat_purchase_event(
            db,
            user_id=str(user_id),
            order=order,
            raw=_raw_order(order),
            now=current,
        )
        if result.get("queued"):
            queued += 1
        elif result.get("duplicate"):
            duplicates += 1
        else:
            skipped += 1
    return {
        "scanned": len(orders),
        "queued": queued,
        "duplicates": duplicates,
        "skipped": skipped,
        "date_from": start.isoformat(),
        "date_to": current.astimezone(_timezone(BUSINESS_TIMEZONE)).date().isoformat(),
    }


@dataclass(frozen=True)
class PixelResolution:
    status: str
    pixel_id: str | None
    source: str | None
    candidates: tuple[str, ...] = ()


async def resolve_capi_pixel_id(db: Any, user_id: str) -> PixelResolution:
    configured = str(os.environ.get(CAPI_PIXEL_ID_ENV) or "").strip()
    if configured:
        return PixelResolution("ready", configured, "environment", (configured,))
    selected = await _load_selected_accounts(db, str(user_id))
    selected_ids = [
        str(row.get("ad_account_id") or "").strip()
        for row in selected
        if row.get("ad_account_id")
    ]
    query: dict[str, Any] = {"user_id": str(user_id), "pixel_id": {"$nin": [None, ""]}}
    if selected_ids:
        query["ad_account_id"] = {"$in": selected_ids}
    cursor = _collection(db, TRACKING_ASSET_COLLECTION).find(
        query,
        {"_id": 0, "pixel_id": 1, "last_observed_at": 1},
    )
    rows = await cursor.to_list(length=100)
    candidates = tuple(sorted({str(row.get("pixel_id")).strip() for row in rows if row.get("pixel_id")}))
    if len(candidates) == 1:
        return PixelResolution("ready", candidates[0], "tracking_discovery", candidates)
    if len(candidates) > 1:
        return PixelResolution("pixel_selection_required", None, None, candidates)
    return PixelResolution("pixel_not_discovered", None, None, ())


def _safe_provider_summary(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    result: dict[str, Any] = {}
    for key in (
        "status", "reason", "request_status", "events_received",
        "events_processed", "event_id", "message", "error", "errors",
    ):
        value = payload.get(key)
        if value not in (None, "", [], {}):
            result[key] = str(value)[:1000] if not isinstance(value, (int, float, bool)) else value
    return result


async def _claim_event(
    db: Any,
    *,
    user_id: str,
    worker_id: str,
    now: datetime,
) -> dict[str, Any] | None:
    return await _collection(db, OUTBOX_COLLECTION).find_one_and_update(
        {
            "user_id": str(user_id),
            "status": {"$in": list(PENDING_STATUSES)},
            "next_attempt_at": {"$lte": now},
            "$or": [
                {"lock_expires_at": {"$lte": now}},
                {"lock_expires_at": None},
                {"lock_expires_at": {"$exists": False}},
            ],
        },
        {
            "$set": {
                "status": "sending",
                "lock_owner": worker_id,
                "lock_expires_at": now + LOCK_TTL,
                "last_attempt_at": now,
                "updated_at": now,
            },
            "$inc": {"attempts": 1},
        },
        sort=[("created_at", 1)],
        return_document=ReturnDocument.AFTER,
    )


async def _complete_event(
    db: Any,
    event: dict[str, Any],
    *,
    status: str,
    now: datetime,
    provider_summary: dict[str, Any] | None = None,
    error: dict[str, Any] | None = None,
    retryable: bool = False,
) -> None:
    attempts = int(event.get("attempts") or 1)
    patch: dict[str, Any] = {
        "status": status,
        "lock_owner": None,
        "lock_expires_at": None,
        "updated_at": now,
        "provider_summary": provider_summary or None,
        "last_error": error,
    }
    if status == "sent":
        patch.update({
            "sent_at": now,
            "payload": None,
            "payload_redacted_after_send": True,
            "purge_at": now + SENT_RETENTION,
        })
    elif retryable and attempts < MAX_ATTEMPTS:
        delay = min(60 * (2 ** max(attempts - 1, 0)), 3600)
        patch.update({
            "status": "retry",
            "next_attempt_at": now + timedelta(seconds=delay),
        })
    else:
        patch.update({
            "status": "failed",
            "purge_at": now + FAILED_RETENTION,
        })
    await _collection(db, OUTBOX_COLLECTION).update_one(
        {"_id": event.get("_id"), "lock_owner": event.get("lock_owner")},
        {"$set": patch},
    )


async def drain_snapchat_capi_outbox(
    db: Any,
    user_id: str,
    *,
    limit: int | None = None,
    now: Callable[[], datetime] = _utcnow,
) -> dict[str, Any]:
    if not capi_enabled():
        return {"status": "disabled", "sent": 0, "failed": 0, "retried": 0}
    await ensure_snapchat_capi_indexes(db)
    resolution = await resolve_capi_pixel_id(db, str(user_id))
    if not resolution.pixel_id:
        return {
            "status": resolution.status,
            "sent": 0,
            "failed": 0,
            "retried": 0,
            "pixel_candidates": list(resolution.candidates),
        }
    context = SnapchatSyncContext(db, str(user_id), now=now)
    try:
        access_token = await context.access_token()
    except SnapchatNativeSyncError as exc:
        return {"status": "token_error", "code": exc.code, "sent": 0, "failed": 0, "retried": 0}
    worker_id = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:10]}"
    sent = failed = retried = expired = 0
    maximum = min(max(int(limit or capi_batch_size()), 1), MAX_BATCH_SIZE)
    async with httpx.AsyncClient(timeout=25.0) as client:
        for _ in range(maximum):
            current = now().astimezone(timezone.utc)
            event = await _claim_event(
                db,
                user_id=str(user_id),
                worker_id=worker_id,
                now=current,
            )
            if not event:
                break
            if str(event.get("user_id")) != str(user_id):
                await _complete_event(
                    db,
                    event,
                    status="retry",
                    now=current,
                    error={"code": "worker_user_mismatch"},
                    retryable=True,
                )
                retried += 1
                continue
            payload = event.get("payload") if isinstance(event.get("payload"), dict) else None
            event_time = datetime.fromtimestamp(
                int((payload or {}).get("event_time") or 0), tz=timezone.utc
            ) if payload else None
            if not payload or not event_time or current - event_time > EVENT_MAX_AGE:
                await _complete_event(
                    db,
                    event,
                    status="failed",
                    now=current,
                    error={"code": "event_expired_or_payload_missing", "retryable": False},
                    retryable=False,
                )
                expired += 1
                failed += 1
                continue
            url = CAPI_ENDPOINT.format(pixel_id=resolution.pixel_id)
            try:
                response = await client.post(
                    url,
                    params={"access_token": access_token},
                    headers={"Content-Type": "application/json", "Accept": "application/json"},
                    json={"data": [payload]},
                )
                try:
                    provider_payload = response.json() or {}
                except (TypeError, ValueError):
                    provider_payload = {}
                summary = _safe_provider_summary(provider_payload)
                provider_status = str(
                    summary.get("status") or summary.get("request_status") or ""
                ).upper()
                provider_failed = any(
                    token in provider_status
                    for token in ("FAIL", "ERROR", "INVALID", "REJECT")
                )
                provider_accepted = (
                    provider_status in {"VALID", "SUCCESS", "OK"}
                    or summary.get("events_received") is not None
                    or summary.get("events_processed") is not None
                )
                if (
                    200 <= response.status_code < 300
                    and provider_accepted
                    and not provider_failed
                ):
                    await _complete_event(
                        db,
                        event,
                        status="sent",
                        now=current,
                        provider_summary={"http_status": response.status_code, **summary},
                    )
                    sent += 1
                else:
                    retryable = (
                        response.status_code == 429
                        or response.status_code >= 500
                        or (200 <= response.status_code < 300 and not provider_failed)
                    )
                    await _complete_event(
                        db,
                        event,
                        status="retry" if retryable else "failed",
                        now=current,
                        provider_summary={"http_status": response.status_code, **summary},
                        error={
                            "code": f"snapchat_capi_http_{response.status_code}",
                            "retryable": retryable,
                        },
                        retryable=retryable,
                    )
                    retried += int(retryable)
                    failed += int(not retryable)
            except httpx.HTTPError as exc:
                await _complete_event(
                    db,
                    event,
                    status="retry",
                    now=current,
                    error={
                        "code": "snapchat_capi_network_error",
                        "type": type(exc).__name__,
                        "retryable": True,
                    },
                    retryable=True,
                )
                retried += 1
    return {
        "status": "complete" if not failed else "partial",
        "pixel_id": resolution.pixel_id,
        "pixel_source": resolution.source,
        "sent": sent,
        "failed": failed,
        "retried": retried,
        "expired": expired,
        "provider_calls": sent + failed + retried,
    }


async def run_snapchat_capi_cycle(
    db: Any,
    user_id: str,
    *,
    now: Callable[[], datetime] = _utcnow,
) -> dict[str, Any]:
    if not capi_enabled():
        return {"status": "disabled", "enabled": False}
    current = now().astimezone(timezone.utc)
    enqueued = await enqueue_recent_purchase_events(
        db,
        str(user_id),
        days=capi_backfill_days(),
        now=current,
    )
    delivered = await drain_snapchat_capi_outbox(db, str(user_id), now=now)
    return {
        "status": delivered.get("status"),
        "enabled": True,
        "enqueued": enqueued,
        "delivery": delivered,
        "source_mode": SOURCE_MODE,
        "plaintext_customer_contact_stored": False,
        "provider_write_authorized": True,
        "campaign_write_reached": False,
        "accounting_write_reached": False,
        "qoyod_write_reached": False,
    }


async def snapchat_capi_status(db: Any, user_id: str) -> dict[str, Any]:
    await ensure_snapchat_capi_indexes(db)
    resolution = await resolve_capi_pixel_id(db, str(user_id))
    counts = {
        status: await _collection(db, OUTBOX_COLLECTION).count_documents(
            {"user_id": str(user_id), "status": status}
        )
        for status in ("pending", "sending", "retry", "sent", "failed", "cancelled")
    }
    latest = await _collection(db, OUTBOX_COLLECTION).find_one(
        {"user_id": str(user_id)},
        {"_id": 0, "event_id": 1, "status": 1, "sent_at": 1, "last_error": 1, "updated_at": 1},
        sort=[("updated_at", -1)],
    ) or {}
    return {
        "enabled": capi_enabled(),
        "pixel": {
            "status": resolution.status,
            "pixel_id": resolution.pixel_id,
            "source": resolution.source,
            "candidates": list(resolution.candidates),
        },
        "outbox": counts,
        "latest": latest,
        "backfill_days": capi_backfill_days(),
        "interval_seconds": capi_interval_seconds(),
        "data_shared": {
            "event": ["PURCHASE", "event_time", "event_id", "order_id", "value", "currency", "contents"],
            "hashed_only": ["email", "phone", "city", "country", "postal_code", "external_id"],
            "unhashed_attribution_signals_when_available": ["sc_click_id", "sc_cookie1"],
            "excluded": ["client_ip_address", "client_user_agent"],
            "plaintext_customer_contact_stored_in_outbox": False,
        },
        "source_mode": SOURCE_MODE,
    }


async def _connected_users(db: Any) -> list[str]:
    cursor = _collection(db, "mezan_integrations_v2").find(
        {
            "provider": SNAPCHAT_PROVIDER_ID,
            "connection_status": "connected",
            "connection_provenance": "api_connection",
        },
        {"_id": 0, "user_id": 1},
    )
    rows = await cursor.to_list(length=1000)
    return sorted({str(row.get("user_id")) for row in rows if row.get("user_id")})


async def _acquire_scheduler_lease(db: Any, worker_id: str, now: datetime) -> bool:
    try:
        document = await _collection(db, SCHEDULER_COLLECTION).find_one_and_update(
            {
                "_id": SCHEDULER_ID,
                "$and": [
                    {"$or": [
                        {"lease_expires_at": {"$lte": now}},
                        {"lease_expires_at": None},
                        {"lease_expires_at": {"$exists": False}},
                    ]},
                    {"$or": [
                        {"next_due_at": {"$lte": now}},
                        {"next_due_at": {"$exists": False}},
                    ]},
                ],
            },
            {
                "$set": {
                    "lease_owner": worker_id,
                    "lease_expires_at": now + LOCK_TTL,
                    "next_due_at": now + timedelta(seconds=capi_interval_seconds()),
                    "status": "running",
                    "updated_at": now,
                },
                "$setOnInsert": {"created_at": now},
            },
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
    except DuplicateKeyError:
        return False
    return bool(document and document.get("lease_owner") == worker_id)


async def snapchat_capi_loop(db: Any) -> None:
    if not capi_enabled():
        logger.info("Snapchat CAPI worker disabled")
        return
    worker_id = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:10]}"
    await asyncio.sleep(30)
    while True:
        try:
            now = _utcnow()
            if await _acquire_scheduler_lease(db, worker_id, now):
                results = []
                for user_id in await _connected_users(db):
                    try:
                        results.append(await run_snapchat_capi_cycle(db, user_id))
                    except Exception as exc:  # noqa: BLE001
                        logger.exception("Snapchat CAPI cycle failed user=%s", user_id)
                        results.append({"user_id": user_id, "status": "failed", "error": str(exc)[:300]})
                await _collection(db, SCHEDULER_COLLECTION).update_one(
                    {"_id": SCHEDULER_ID, "lease_owner": worker_id},
                    {"$set": {
                        "status": "complete",
                        "lease_expires_at": _utcnow(),
                        "last_finished_at": _utcnow(),
                        "last_results": results,
                        "updated_at": _utcnow(),
                    }},
                )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("Snapchat CAPI scheduler heartbeat failed")
        await asyncio.sleep(15)


def attach_snapchat_capi_purchase_routes(
    router: APIRouter,
    db: Any,
    current_user: Callable,
    require_owner: Callable[[Any], dict],
) -> None:
    task: asyncio.Task | None = None

    @router.get(f"/{SNAPCHAT_PROVIDER_ID}/capi/status")
    async def read_snapchat_capi_status(
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        owner = require_owner(user)
        return await snapchat_capi_status(db, str(owner["id"]))

    @router.post(f"/{SNAPCHAT_PROVIDER_ID}/capi/sync")
    async def sync_snapchat_capi_now(
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        owner = require_owner(user)
        return await run_snapchat_capi_cycle(db, str(owner["id"]))

    async def start() -> None:
        nonlocal task
        if capi_enabled() and (task is None or task.done()):
            task = asyncio.create_task(
                snapchat_capi_loop(db),
                name="mezan-snapchat-capi-purchases",
            )

    async def stop() -> None:
        nonlocal task
        if task is None:
            return
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        task = None

    router.on_startup.append(start)
    router.on_shutdown.append(stop)


__all__ = [
    "CAPI_ENABLED_ENV",
    "CAPI_PIXEL_ID_ENV",
    "OUTBOX_COLLECTION",
    "SOURCE_MODE",
    "attach_snapchat_capi_purchase_routes",
    "build_snapchat_purchase_event",
    "cancel_pending_snapchat_purchase_event",
    "capi_enabled",
    "drain_snapchat_capi_outbox",
    "enqueue_recent_purchase_events",
    "enqueue_snapchat_purchase_event",
    "enqueue_snapchat_purchase_from_salla_event",
    "normalize_email",
    "normalize_phone",
    "resolve_capi_pixel_id",
    "run_snapchat_capi_cycle",
    "snapchat_capi_status",
]
