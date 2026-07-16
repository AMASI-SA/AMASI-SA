"""Safe handling for signed Salla webhook events not handled by app lifecycle.

Only call this module after webhook signature/token verification. Every event is
stored in sanitized form for audit. If the verified payload exposes a shipment
ID, a read-only Shipment Details/Tracking sync is attempted.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any, Iterable

from .shipment_webhook_sync import sync_shipment_from_verified_webhook


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


def _sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        for key, child in value.items():
            key_text = str(key)
            if key_text.strip().lower() in _SECRET_KEYS:
                cleaned[key_text] = "[REDACTED]"
            else:
                cleaned[key_text] = _sanitize(child)
        return cleaned
    if isinstance(value, list):
        return [_sanitize(child) for child in value]
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
    """Persist a verified event and enrich shipment data when possible.

    Events are deduplicated by merchant + event name + sanitized payload hash.
    Salla operations are read-only and this function never calls Qoyod.
    Shipment sync failure never prevents acknowledging the verified webhook.
    """
    event_name = str(event_body.get("event") or "").strip()
    if event_name in set(known_events):
        return {
            "captured": False,
            "reason": "known_event",
            "event": event_name,
        }

    sanitized = _sanitize(event_body)
    merchant_id = str(event_body.get("merchant") or "").strip() or None
    event_hash = _fingerprint(sanitized)
    now = datetime.now(timezone.utc)

    try:
        shipment_sync = await sync_shipment_from_verified_webhook(db, event_body)
    except Exception as exc:  # noqa: BLE001 — webhook must still return HTTP 200
        log.exception("shipment_webhook.unhandled event=%s", event_name or "<none>")
        shipment_sync = {
            "attempted": True,
            "synced": False,
            "reason": "unhandled_exception",
            "error": str(exc)[:500],
            "no_qoyod_calls": True,
        }

    log.info(
        "shipment_webhook.result event=%s attempted=%s synced=%s shipment_id=%s "
        "order_ref=%s matched=%s modified=%s reason=%s",
        event_name or "<none>",
        shipment_sync.get("attempted"),
        shipment_sync.get("synced"),
        shipment_sync.get("shipment_id"),
        shipment_sync.get("order_reference_id"),
        shipment_sync.get("order_matched"),
        shipment_sync.get("order_modified"),
        shipment_sync.get("reason"),
    )

    selector = {
        "merchant_id": merchant_id,
        "event": event_name or None,
        "event_hash": event_hash,
    }
    update = {
        "$setOnInsert": {
            **selector,
            "payload": sanitized,
            "first_received_at": now,
            "created_at": now,
            "verified_before_capture": True,
            "no_qoyod_calls": True,
        },
        "$set": {
            "last_received_at": now,
            "updated_at": now,
            "shipment_sync": shipment_sync,
            "order_mutation_scope": (
                "shipping_fields_only"
                if shipment_sync.get("order_modified")
                else "none"
            ),
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
        "shipment_sync": shipment_sync,
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
