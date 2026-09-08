"""Durable, idempotent capture for verified Salla order events in V3 shadow."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any


EVENTS_COLLECTION = "salla_orders_v3_events"
JOBS_COLLECTION = "salla_orders_v3_jobs"

ORDER_EVENTS = {"order.created", "order.updated", "order.status.updated"}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _order_payload(event_body: dict[str, Any]) -> dict[str, Any]:
    data = event_body.get("data")
    data = data if isinstance(data, dict) else {}
    nested = data.get("order")
    if isinstance(nested, dict):
        merged = deepcopy(nested)
        for key, value in data.items():
            if key != "order" and key not in merged:
                merged[key] = deepcopy(value)
        return merged
    return deepcopy(data)


def _event_id(
    *,
    user_id: str,
    store_id: str,
    event_body: dict[str, Any],
) -> str:
    provider_id = _text(
        event_body.get("id")
        or event_body.get("event_id")
        or event_body.get("webhook_id")
    )
    if provider_id:
        suffix = provider_id
    else:
        canonical = json.dumps(
            event_body,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        suffix = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"{_text(user_id)}:{_text(store_id)}:{suffix}"


async def capture_verified_order_event(
    db: Any,
    *,
    user_id: str,
    store_id: str,
    event_body: dict[str, Any],
) -> dict[str, Any]:
    """Capture one already-verified event and enqueue shadow enrichment once."""
    event_name = _text(event_body.get("event"))
    if event_name not in ORDER_EVENTS:
        return {"accepted": False, "reason": "not_order_event"}

    payload = _order_payload(event_body)
    internal_id = _text(payload.get("id") or payload.get("order_id"))
    order_number = _text(
        payload.get("reference_id") or payload.get("order_number")
    )
    if not internal_id or not order_number:
        return {"accepted": False, "reason": "missing_order_identity"}

    now = datetime.now(timezone.utc)
    event_key = _event_id(
        user_id=user_id,
        store_id=store_id,
        event_body=event_body,
    )
    events = getattr(db, EVENTS_COLLECTION)
    result = await events.update_one(
        {"_id": event_key},
        {"$setOnInsert": {
            "_id": event_key,
            "user_id": _text(user_id),
            "store_id": _text(store_id),
            "event": event_name,
            "event_created_at": event_body.get("created_at"),
            "internal_order_id": internal_id,
            "order_number": order_number,
            "payload": deepcopy(payload),
            "received_at": now,
            "shadow_only": True,
        }},
        upsert=True,
    )
    created = result.upserted_id is not None
    if not created:
        return {
            "accepted": True,
            "created": False,
            "queued": False,
            "duplicate": True,
            "event_id": event_key,
        }

    job_key = await enqueue_shadow_job(
        db,
        user_id=user_id,
        store_id=store_id,
        light_order=payload,
        event_created_at=event_body.get("created_at"),
        source_event_id=event_key,
        now=now,
    )
    return {
        "accepted": True,
        "created": True,
        "queued": True,
        "duplicate": False,
        "event_id": event_key,
        "job_id": job_key,
    }


async def enqueue_shadow_job(
    db: Any,
    *,
    user_id: str,
    store_id: str,
    light_order: dict[str, Any],
    event_created_at: Any = None,
    source_event_id: str | None = None,
    now: datetime | None = None,
) -> str:
    """Upsert one per-order enrichment job in the isolated V3 queue."""
    now = now or datetime.now(timezone.utc)
    internal_id = _text(light_order.get("id") or light_order.get("order_id"))
    order_number = _text(
        light_order.get("reference_id") or light_order.get("order_number")
    )
    if not internal_id or not order_number:
        raise ValueError("Salla shadow job requires order identity")
    job_key = f"{_text(user_id)}:{_text(store_id)}:{internal_id}"
    jobs = getattr(db, JOBS_COLLECTION)
    await jobs.update_one(
        {"_id": job_key},
        {
            "$setOnInsert": {
                "_id": job_key,
                "created_at": now,
                "attempts": 0,
            },
            "$set": {
                "user_id": _text(user_id),
                "store_id": _text(store_id),
                "internal_order_id": internal_id,
                "order_number": order_number,
                "light_order": deepcopy(light_order),
                "event_created_at": event_created_at,
                "source_event_id": source_event_id,
                "status": "pending",
                "attempts": 0,
                "completed_at": None,
                "last_error": None,
                "next_attempt_at": now,
                "updated_at": now,
                "shadow_only": True,
            },
        },
        upsert=True,
    )
    return job_key
