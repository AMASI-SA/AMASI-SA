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
import time
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


class WebhookProcessingIncomplete(RuntimeError):
    """Fixed diagnostic used by durable workers; contains no event data."""


def _processing_retry_required(*results: Any) -> bool:
    """Recognize unfinished work without turning intentional rejections into retries."""
    retry_reasons = {
        "unhandled_exception", "order_webhook_persist_failed",
        "connected_salla_owner_not_found", "address_projection_failed",
        "customer_options_unavailable", "source_changed", "order_not_found",
        "enqueue_failed", "shadow_capture_failed",
    }
    for result in results:
        if not isinstance(result, dict):
            continue
        reason = result.get("reason")
        if result.get("retry_required") is True or isinstance(reason, str) and reason in retry_reasons:
            return True
        for key in ("auto_fulfillment", "customer_options_enrichment", "address_sync"):
            child = result.get(key)
            if isinstance(child, dict) and _processing_retry_required(child):
                return True
    return False


def _safe_processing_diagnostics(value: Any) -> Any:
    """Exceptions may echo a customer payload; retain only fixed error codes."""
    if isinstance(value, dict):
        return {key: ("processing_error" if key in {"error", "last_error", "exception", "traceback"}
                      else _safe_processing_diagnostics(child))
                for key, child in value.items()}
    if isinstance(value, list):
        return [_safe_processing_diagnostics(child) for child in value]
    return value

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


def _log_reason_code(value: Any) -> str | None:
    """Project an opaque handler result to a fixed diagnostic, never its data."""
    if value is None:
        return None
    if isinstance(value, str):
        for code in (
            "not_order_snapshot_event", "not_shipment_event",
            "attribution_pilot_store_orders_blocked",
            "attribution_pilot_store_shipments_blocked",
            "connected_salla_owner_not_found", "missing_order_payload",
            "missing_order_reference", "stale_salla_snapshot",
            "order_webhook_persist_failed", "return_shipment_kept_separate",
            "missing_order_reference_in_webhook", "shipment_order_identity_ambiguous",
            "order_not_found", "shipment_order_identity_conflict",
            "shipping_snapshot_not_current", "shipping_snapshot_already_current",
            "synced_from_webhook", "payment_update_queued", "unhandled_exception", "isolated_abandoned_cart_event",
        ):
            if value == code:
                return code
    return "unrecognized"


async def capture_unknown_event(
    db: Any,
    event_body: dict[str, Any],
    *,
    known_events: Iterable[str],
    raise_on_processing_error: bool = False,
) -> dict[str, Any]:
    """Persist and process a verified non-lifecycle Salla event.

    Events are deduplicated by merchant + event name + sanitized payload hash.
    Direct callers retain the captured-result contract. A durable worker asks
    for a strict failure after saving the audit when processing is incomplete.
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
    started = time.monotonic()
    timings: dict[str, int] = {}
    refund_retry_required = False

    async def timed(stage: str, work):
        began = time.monotonic()
        try:
            return await work
        finally:
            timings[stage] = round((time.monotonic() - began) * 1000)

    # A received label must survive a later order-processing failure. Failure
    # to durably enqueue is propagated instead of acknowledging lost data.
    from .shipping_label_inbox import enqueue_verified_label
    await enqueue_verified_label(db, event_body)
    from .payment_update_inbox import enqueue_verified_payment
    payment_job = await enqueue_verified_payment(db, event_body)

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
            cart_sync = await timed("abandoned_cart", persist_abandoned_cart_event(db, event_body))
        except Exception as exc:  # webhook acknowledgement remains independent
            log.warning("abandoned_cart_webhook.unhandled error_type=%s", type(exc).__name__)
            cart_sync = {
                "attempted": True,
                "synced": False,
                "reason": "unhandled_exception",
                "error_type": type(exc).__name__,
                "provider_write_reached": False,
                "pii_stored": False,
                "plaintext_pii_stored": False,
            }

    if payment_job:
        order_sync = {"attempted": True, "synced": False, "reason": "payment_update_queued"}

    if not is_abandoned_cart_event and not payment_job:
        try:
            order_sync = await timed("order_sync", sync_order_from_verified_webhook(db, event_body))
        except Exception as exc:  # webhook must still return HTTP 200
            log.warning("order_webhook.unhandled error_type=%s", type(exc).__name__)
            order_sync = {
                "attempted": True,
                "synced": False,
                "reason": "unhandled_exception",
                "error_type": type(exc).__name__,
                "no_salla_api_calls": True,
                "no_qoyod_calls": True,
            }
        # The existing order.updated feed is sufficient. Address projection
        # must not depend on a removed shipping event, or on component
        # acceptance of this same update. Independent owner/version fence.
        if event_name == "order.updated":
            try:
                from .address_webhook_sync import sync_verified_shipping_address
                address_result = await sync_verified_shipping_address(db, event_body)
                order_sync["address_sync"] = {
                    key: address_result.get(key)
                    for key in ("synced", "reason", "source")
                }
            except Exception as exc:
                log.warning("order_address_webhook.retry_required error_type=%s", type(exc).__name__)
                order_sync["address_sync"] = {
                    "synced": False, "reason": "address_projection_failed"
                }

    # Order notifications carry no independent refund identity. Resolve only
    # confirmed financial sources belonging to the original payment provider.
    if event_name in {"order.refunded", "order.updated", "order.status.updated"}:
        payload = event_body.get("data") or {}
        status = payload.get("status") or {}
        status = status.get("slug") if isinstance(status, dict) else status
        refund_action = (payload.get("payment_actions") or {}).get("refund_action") or {}
        if event_name == "order.refunded" or status in {"refunded", "partially_refunded", "canceled", "cancelled"} or refund_action.get("has_refund_amount") is True:
            try:
                refund_owner = await _resolve_user_id(db, merchant_id)
                reference = payload.get("reference_id") or payload.get("order_number")
                if refund_owner and reference:
                    from accounting_order_refunds import process_order_refunds
                    await process_order_refunds(db, owner=refund_owner, order_number=str(reference),
                        source={"kind": "salla_verified_order_update", "event": event_name, "capture_hash": event_hash}, payload=payload)
            except Exception as exc:
                log.warning("mz2.order_refund_requires_retry error_type=%s", type(exc).__name__)
                refund_retry_required = True

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
            log.warning("salla.orders_v3.shadow_capture_failed error_type=%s", type(exc).__name__)
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
            log.warning("snapchat_capi.enqueue_failed error_type=%s", type(exc).__name__)
            snapchat_capi = {
                "queued": False,
                "reason": "enqueue_failed",
                "error_type": type(exc).__name__,
                "provider_call_reached": False,
            }

    if not is_abandoned_cart_event:
        try:
            shipment_sync = await timed(
                "shipment_sync", sync_shipment_payload_from_verified_webhook(db, event_body),
            )
        except Exception as exc:  # webhook must still return HTTP 200
            log.warning("shipment_webhook.unhandled error_type=%s", type(exc).__name__)
            shipment_sync = {
                "attempted": True,
                "synced": False,
                "reason": "unhandled_exception",
                "error_type": type(exc).__name__,
                "no_salla_api_calls": True,
                "no_qoyod_calls": True,
            }

    # Keep correlation with the existing audit identity, without exposing raw
    # order/shipment references or stringifying opaque handler result values.
    log.info(
        "salla_webhook.result event=%s order_synced=%s shipment_synced=%s "
        "snapchat_capi_queued=%s cart_synced=%s capture_hash=%s reason=%s",
        event_name or "<none>",
        order_sync.get("synced") is True,
        shipment_sync.get("synced") is True,
        snapchat_capi.get("queued") is True,
        cart_sync.get("synced") is True,
        event_hash[:12],
        _log_reason_code(shipment_sync.get("reason") or order_sync.get("reason")),
    )

    order_sync, shipment_sync, cart_sync, snapchat_capi, orders_v3_shadow = (
        _safe_processing_diagnostics(result)
        for result in (order_sync, shipment_sync, cart_sync, snapchat_capi, orders_v3_shadow)
    )
    retry_required = refund_retry_required or _processing_retry_required(
        order_sync, shipment_sync, cart_sync, snapchat_capi, orders_v3_shadow,
    )
    timings["total"] = round((time.monotonic() - started) * 1000)
    log.info("salla_webhook.processing duration_ms=%s retry_required=%s capture_hash=%s",
             timings["total"], retry_required, event_hash[:12])
    selector = {
        "merchant_id": merchant_id,
        "event": event_name or None,
        "event_hash": event_hash,
    }
    order_mutation_scope = (
        "full_order_from_webhook"
        if order_sync.get("synced")
        else "customer_options_only"
        if (order_sync.get("customer_options_enrichment") or {}).get("applied") is True
        else "shipping_address_only"
        if (order_sync.get("address_sync") or {}).get("synced")
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
            "processing_retry_required": retry_required,
            "processing_timings_ms": timings,
        },
        "$inc": {"delivery_count": 1},
    }
    result = await db.salla_webhook_event_captures.update_one(
        selector,
        update,
        upsert=True,
    )
    if raise_on_processing_error and retry_required:
        raise WebhookProcessingIncomplete("salla_webhook_processing_incomplete")
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
        "processing_retry_required": retry_required,
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
