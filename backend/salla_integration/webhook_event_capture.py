"""Safe handling for verified Salla business webhooks.

Every event is stored in sanitized form for audit. order.created/order.updated are
persisted directly into unified_orders, and shipment events enrich shipment fields
from the webhook payload only. Snapchat purchase delivery is decoupled through an
idempotent outbox; this webhook never waits for a Snapchat provider call. No
outbound Salla API or Qoyod calls occur here.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any, Iterable

from .abandoned_carts import (
    ABANDONED_CART_EVENTS,
    persist_abandoned_cart_event,
)
from .webhook_order_sync import (
    _resolve_user_id,
    sync_order_from_verified_webhook,
    sync_shipment_payload_from_verified_webhook,
)


log = logging.getLogger("salla.webhook_capture")

_SECRET_KEYS = {
    "access_token",
    "refresh_token",
    "authorization",
    "token",
    "secret",
    "client_secret",
    "webhook_secret",
    "password",
}

_PII_KEYS = {
    "name",
    "email",
    "phone",
    "mobile",
    "mobile_number",
    "phone_number",
    "first_name",
    "last_name",
    "full_name",
    "street",
    "city",
    "country",
    "postal_code",
    "postcode",
    "zip",
    "ip",
    "ip_address",
    "device_id",
    "url",
    "urls",
    "checkout_url",
    "recovery_url",
    "cart_url",
}
_PII_CONTAINERS = {
    "customer",
    "customer_data",
    "contact",
    "address",
    "billing_address",
    "shipping_address",
    "user",
}


def _sanitize(value: Any, *, redact_pii: bool = False) -> Any:
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for key, child in value.items():
            key_text = str(key)
            normalized_key = key_text.strip().lower()
            if normalized_key in _SECRET_KEYS:
                cleaned[key_text] = "[REDACTED]"
            elif redact_pii and normalized_key in _PII_CONTAINERS:
                cleaned[key_text] = "[REDACTED_PII]"
            elif redact_pii and normalized_key in _PII_KEYS:
                cleaned[key_text] = "[REDACTED_PII]"
            else:
                cleaned[key_text] = _sanitize(child, redact_pii=redact_pii)
        return cleaned
    if isinstance(value, list):
        return [_sanitize(child, redact_pii=redact_pii) for child in value]
    return value


def _fingerprint(payload: dict[str, Any]) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def capture_unknown_event(
    db: Any,
    event_body: dict[str, Any],
    *,
    known_events: Iterable[str],
) -> dict[str, Any]:
    """Persist and process a verified non-lifecycle Salla event.

    Events are deduplicated by merchant + event name + sanitized payload hash.
    Processing failure never prevents acknowledging the verified webhook.
    """
    event_name = str(event_body.get("event") or "").strip()
    if event_name in set(known_events):
        return {
            "captured": False,
            "reason": "known_event",
            "event": event_name,
        }

    is_abandoned_cart_event = event_name in ABANDONED_CART_EVENTS
    sanitized = _sanitize(
        event_body,
        redact_pii=is_abandoned_cart_event,
    )
    merchant_id = str(event_body.get("merchant") or "").strip() or None
    event_hash = _fingerprint(sanitized)
    now = datetime.now(timezone.utc)

    order_sync: dict[str, Any] = {
        "attempted": False,
        "reason": "not_order_snapshot_event",
    }
    shipment_sync: dict[str, Any] = {
        "attempted": False,
        "reason": "not_shipment_event",
    }
    snapchat_capi: dict[str, Any] = {
        "queued": False,
        "reason": "order_not_synced",
    }
    cart_sync: dict[str, Any] = {
        "attempted": False,
        "reason": "not_abandoned_cart_event",
    }
    orders_v3_shadow: dict[str, Any] = {
        "accepted": False,
        "reason": "shadow_disabled_or_not_order_event",
    }

    if is_abandoned_cart_event:
        # Abandoned-cart webhooks have a deliberately isolated execution
        # boundary. They may update only the encrypted customer/cart memory and
        # the sanitized webhook audit below; order, shipment, Qoyod and ads
        # conversion paths are never entered for these events.
        order_sync["reason"] = "isolated_abandoned_cart_event"
        shipment_sync["reason"] = "isolated_abandoned_cart_event"
        snapchat_capi["reason"] = "isolated_abandoned_cart_event"
        try:
            cart_sync = await persist_abandoned_cart_event(db, event_body)
        except Exception as exc:  # webhook acknowledgement remains independent
            log.exception("abandoned_cart_webhook.unhandled event=%s", event_name)
            cart_sync = {
                "attempted": True,
                "synced": False,
                "reason": "unhandled_exception",
                "error": str(exc)[:500],
                "provider_write_reached": False,
                "pii_stored": False,
                "plaintext_pii_stored": False,
            }

    if not is_abandoned_cart_event:
        try:
            order_sync = await sync_order_from_verified_webhook(db, event_body)
        except Exception as exc:  # webhook must still return HTTP 200
            log.exception("order_webhook.unhandled event=%s", event_name or "<none>")
            order_sync = {
                "attempted": True,
                "synced": False,
                "reason": "unhandled_exception",
                "error": str(exc)[:500],
                "no_salla_api_calls": True,
                "no_qoyod_calls": True,
            }

    # The V3 observer runs only after webhook verification and writes only to
    # its isolated shadow event/queue collections. It cannot change the result
    # of the established operational handler above.
    if not is_abandoned_cart_event and event_name in {
        "order.created", "order.updated", "order.status.updated",
    }:
        try:
            from salla_orders_v3.ingestion import capture_verified_order_event
            from salla_orders_v3.worker import shadow_enabled

            if shadow_enabled():
                shadow_user_id = await _resolve_user_id(db, merchant_id)
                if shadow_user_id:
                    orders_v3_shadow = await capture_verified_order_event(
                        db,
                        user_id=shadow_user_id,
                        store_id=merchant_id or "",
                        event_body=event_body,
                    )
                else:
                    orders_v3_shadow = {
                        "accepted": False,
                        "reason": "connected_owner_not_found",
                    }
        except Exception as exc:
            log.exception("salla.orders_v3.shadow_capture_failed event=%s", event_name)
            orders_v3_shadow = {
                "accepted": False,
                "reason": "shadow_capture_failed",
                "error_type": type(exc).__name__,
            }

    # Provider delivery is never performed inside the webhook.  This call only
    # creates a hashed, idempotent outbox record when CAPI is explicitly enabled.
    if not is_abandoned_cart_event and order_sync.get("synced"):
        try:
            from integrations_control_center.snapchat_capi_purchases import (
                enqueue_snapchat_purchase_from_salla_event,
            )

            snapchat_capi = await enqueue_snapchat_purchase_from_salla_event(
                db,
                event_body,
            )
        except Exception as exc:  # webhook acknowledgement remains independent
            log.exception(
                "snapchat_capi.enqueue_failed event=%s order=%s",
                event_name or "<none>",
                order_sync.get("order_number"),
            )
            snapchat_capi = {
                "queued": False,
                "reason": "enqueue_failed",
                "error": str(exc)[:300],
                "provider_call_reached": False,
            }

    if not is_abandoned_cart_event:
        try:
            shipment_sync = await sync_shipment_payload_from_verified_webhook(
                db,
                event_body,
            )
        except Exception as exc:  # webhook must still return HTTP 200
            log.exception("shipment_webhook.unhandled event=%s", event_name or "<none>")
            shipment_sync = {
                "attempted": True,
                "synced": False,
                "reason": "unhandled_exception",
                "error": str(exc)[:500],
                "no_salla_api_calls": True,
                "no_qoyod_calls": True,
            }

    log.info(
        "salla_webhook.result event=%s order_synced=%s shipment_synced=%s "
        "snapchat_capi_queued=%s cart_synced=%s order_number=%s "
        "shipment_id=%s reason=%s",
        event_name or "<none>",
        order_sync.get("synced"),
        shipment_sync.get("synced"),
        snapchat_capi.get("queued"),
        cart_sync.get("synced"),
        order_sync.get("order_number") or shipment_sync.get("order_reference_id"),
        shipment_sync.get("shipment_id"),
        shipment_sync.get("reason") or order_sync.get("reason"),
    )

    selector = {
        "merchant_id": merchant_id,
        "event": event_name or None,
        "event_hash": event_hash,
    }
    order_mutation_scope = (
        "full_order_from_webhook"
        if order_sync.get("synced")
        else "shipping_fields_only"
        if shipment_sync.get("order_modified")
        else "none"
    )
    update = {
        "$setOnInsert": {
            **selector,
            "payload": sanitized,
            "first_received_at": now,
            "created_at": now,
            "verified_before_capture": True,
            "no_salla_api_calls": True,
            "no_qoyod_calls": True,
        },
        "$set": {
            "last_received_at": now,
            "updated_at": now,
            "order_sync": order_sync,
            "shipment_sync": shipment_sync,
            "snapchat_capi": snapchat_capi,
            "abandoned_cart_sync": cart_sync,
            "orders_v3_shadow": orders_v3_shadow,
            "abandoned_cart_isolated": is_abandoned_cart_event,
            "order_mutation_scope": order_mutation_scope,
        },
        "$inc": {"delivery_count": 1},
    }
    result = await db.salla_webhook_event_captures.update_one(
        selector,
        update,
        upsert=True,
    )
    return {
        "captured": True,
        "created": result.upserted_id is not None,
        "event": event_name or None,
        "merchant_id": merchant_id,
        "event_hash_prefix": event_hash[:12],
        "order_sync": order_sync,
        "shipment_sync": shipment_sync,
        "snapchat_capi": snapchat_capi,
        "abandoned_cart_sync": cart_sync,
        "orders_v3_shadow": orders_v3_shadow,
        "abandoned_cart_isolated": is_abandoned_cart_event,
        "order_mutation_scope": order_mutation_scope,
        "no_salla_api_calls": True,
        "no_qoyod_calls": True,
    }


async def list_recent_captures(
    db: Any,
    *,
    limit: int = 20,
) -> list[dict[str, Any]]:
    safe_limit = max(1, min(int(limit or 20), 100))
    cursor = (
        db.salla_webhook_event_captures
        .find({}, {"_id": 0})
        .sort("last_received_at", -1)
        .limit(safe_limit)
    )
    return await cursor.to_list(length=safe_limit)
