"""Global cadence gate for Campaign AI across all production web replicas.

Every FastAPI replica owns a lightweight subprocess scheduler. Without a
durable cadence gate, replicas can run the isolated worker sequentially after
the short concurrency lease is released, publishing several different OpenAI
snapshots minutes apart. This module makes the five-hour decision interval a
Mongo-backed global invariant rather than a per-process timer.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
import uuid
from typing import Any

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError


CADENCE_COLLECTION = "mezan_campaign_ai_global_cadence_v1"
CADENCE_ID = "campaign_ai_openai_global_v1"
DEFAULT_INTERVAL_SECONDS = 5 * 60 * 60
DEFAULT_RETRY_SECONDS = 15 * 60
DEFAULT_LEASE_SECONDS = 12 * 60


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _seconds(name: str, default: int, *, minimum: int) -> int:
    raw = str(os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return max(minimum, int(float(raw)))
    except (TypeError, ValueError):
        return default


def interval_seconds() -> int:
    return _seconds(
        "MEZAN_CAMPAIGN_AI_INTERVAL_SECONDS",
        DEFAULT_INTERVAL_SECONDS,
        minimum=5 * 60,
    )


def retry_seconds() -> int:
    return _seconds(
        "MEZAN_CAMPAIGN_AI_RETRY_DELAY_SECONDS",
        DEFAULT_RETRY_SECONDS,
        minimum=60,
    )


def lease_seconds() -> int:
    return _seconds(
        "MEZAN_CAMPAIGN_AI_GLOBAL_LEASE_SECONDS",
        DEFAULT_LEASE_SECONDS,
        minimum=120,
    )


async def _ensure_document(collection: Any, current: datetime) -> None:
    try:
        await collection.insert_one({
            "_id": CADENCE_ID,
            "state": "idle",
            "next_run_at": current - timedelta(seconds=1),
            "created_at": current,
            "updated_at": current,
            "protocol_version": 1,
        })
    except DuplicateKeyError:
        pass


async def claim_global_cycle(
    db: Any,
    *,
    now: datetime | None = None,
    owner: str | None = None,
) -> dict[str, Any]:
    """Atomically claim the one global Campaign AI cycle that is due now."""
    current = (now or _utcnow()).astimezone(timezone.utc)
    collection = db[CADENCE_COLLECTION]
    await _ensure_document(collection, current)
    claim_owner = owner or str(uuid.uuid4())
    lease_until = current + timedelta(seconds=lease_seconds())

    document = await collection.find_one_and_update(
        {
            "_id": CADENCE_ID,
            "$and": [
                {"$or": [
                    {"next_run_at": {"$lte": current}},
                    {"next_run_at": {"$exists": False}},
                ]},
                {"$or": [
                    {"state": {"$ne": "running"}},
                    {"lease_until": {"$lte": current}},
                    {"lease_until": {"$exists": False}},
                ]},
            ],
        },
        {"$set": {
            "state": "running",
            "owner": claim_owner,
            "claimed_at": current,
            "lease_until": lease_until,
            "updated_at": current,
            "protocol_version": 1,
        }},
        return_document=ReturnDocument.AFTER,
    )
    if document:
        return {
            "claimed": True,
            "owner": claim_owner,
            "claimed_at": current,
            "lease_until": lease_until,
            "next_run_at": document.get("next_run_at"),
        }

    existing = await collection.find_one({"_id": CADENCE_ID}) or {}
    existing_state = str(existing.get("state") or "idle")
    existing_lease = existing.get("lease_until")
    running = bool(
        existing_state == "running"
        and isinstance(existing_lease, datetime)
        and existing_lease.replace(tzinfo=existing_lease.tzinfo or timezone.utc) > current
    )
    return {
        "claimed": False,
        "skip_reason": "running_elsewhere" if running else "not_due",
        "next_run_at": existing.get("next_run_at"),
        "lease_until": existing.get("lease_until"),
        "last_completed_at": existing.get("last_completed_at"),
        "last_outcome": existing.get("last_outcome"),
    }


async def finish_global_cycle(
    db: Any,
    owner: str,
    *,
    retryable: bool,
    outcome: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Release the global cycle and publish the next allowed run time."""
    current = (now or _utcnow()).astimezone(timezone.utc)
    delay = retry_seconds() if retryable else interval_seconds()
    next_run_at = current + timedelta(seconds=delay)
    result = await db[CADENCE_COLLECTION].update_one(
        {
            "_id": CADENCE_ID,
            "state": "running",
            "owner": owner,
        },
        {
            "$set": {
                "state": "idle",
                "next_run_at": next_run_at,
                "last_completed_at": current,
                "last_outcome": str(outcome or "unknown")[:80],
                "updated_at": current,
            },
            "$unset": {
                "owner": "",
                "lease_until": "",
            },
        },
    )
    return {
        "released": bool(getattr(result, "modified_count", 0)),
        "next_run_at": next_run_at,
        "retryable": retryable,
        "outcome": outcome,
    }


__all__ = [
    "CADENCE_COLLECTION",
    "CADENCE_ID",
    "claim_global_cycle",
    "finish_global_cycle",
    "interval_seconds",
    "lease_seconds",
    "retry_seconds",
]
