"""Atomic distributed lease primitives for Snapchat V2 sync workers."""
from __future__ import annotations

import os
import socket
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

try:
    from pymongo.errors import DuplicateKeyError
except ImportError:  # pragma: no cover
    class DuplicateKeyError(Exception):
        pass

from .models import SNAPCHAT_PROVIDER

SNAPCHAT_LEASE_COLLECTION = "mezan_snapchat_leases_v2"
DEFAULT_LEASE_TTL = timedelta(minutes=15)


class SnapchatLeaseLost(RuntimeError):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def build_owner_id() -> str:
    container = (
        os.environ.get("HOSTNAME", "").strip()
        or os.environ.get("RENDER_INSTANCE_ID", "").strip()
        or socket.gethostname()
        or "unknown-worker"
    )
    return f"{container}:{os.getpid()}:{uuid.uuid4()}"


def lease_key(user_id: str, ad_account_id: str) -> dict[str, str]:
    return {
        "user_id": str(user_id),
        "provider": SNAPCHAT_PROVIDER,
        "ad_account_id": str(ad_account_id),
    }


async def ensure_lease_indexes(db: Any) -> None:
    collection = db[SNAPCHAT_LEASE_COLLECTION]
    await collection.create_index(
        [("user_id", 1), ("provider", 1), ("ad_account_id", 1)],
        unique=True,
        name="snapchat_v2_lease_identity_unique",
    )
    await collection.create_index(
        [("status", 1), ("expires_at", 1)],
        name="snapchat_v2_lease_status_expiry",
    )


async def _take_existing(
    collection: Any,
    *,
    key: dict[str, str],
    owner_id: str,
    now: datetime,
    expires_at: datetime,
) -> bool:
    result = await collection.update_one(
        {
            **key,
            "$or": [
                {"status": {"$ne": "held"}},
                {"expires_at": {"$lte": now}},
                {"owner_id": owner_id},
            ],
        },
        {
            "$set": {
                "status": "held",
                "owner_id": owner_id,
                "acquired_at": now,
                "heartbeat_at": now,
                "expires_at": expires_at,
                "released_at": None,
                "updated_at": now,
            },
            "$inc": {"generation": 1},
        },
    )
    return int(getattr(result, "modified_count", 0) or 0) == 1


async def acquire_lease(
    db: Any,
    user_id: str,
    ad_account_id: str,
    owner_id: str,
    ttl_minutes: int = 15,
    *,
    now: Callable[[], datetime] = _utcnow,
) -> bool:
    if ttl_minutes < 1 or ttl_minutes > 240:
        raise ValueError("ttl_minutes must be between 1 and 240")
    collection = db[SNAPCHAT_LEASE_COLLECTION]
    current = now().astimezone(timezone.utc)
    expires_at = current + timedelta(minutes=ttl_minutes)
    key = lease_key(user_id, ad_account_id)

    if await _take_existing(
        collection,
        key=key,
        owner_id=owner_id,
        now=current,
        expires_at=expires_at,
    ):
        return True

    try:
        await collection.insert_one(
            {
                **key,
                "status": "held",
                "owner_id": owner_id,
                "generation": 1,
                "acquired_at": current,
                "heartbeat_at": current,
                "expires_at": expires_at,
                "released_at": None,
                "created_at": current,
                "updated_at": current,
            }
        )
        return True
    except DuplicateKeyError:
        return await _take_existing(
            collection,
            key=key,
            owner_id=owner_id,
            now=current,
            expires_at=expires_at,
        )


async def heartbeat_lease(
    db: Any,
    user_id: str,
    ad_account_id: str,
    owner_id: str,
    ttl_minutes: int = 15,
    *,
    now: Callable[[], datetime] = _utcnow,
) -> datetime:
    if ttl_minutes < 1 or ttl_minutes > 240:
        raise ValueError("ttl_minutes must be between 1 and 240")
    current = now().astimezone(timezone.utc)
    expires_at = current + timedelta(minutes=ttl_minutes)
    result = await db[SNAPCHAT_LEASE_COLLECTION].update_one(
        {
            **lease_key(user_id, ad_account_id),
            "status": "held",
            "owner_id": owner_id,
        },
        {
            "$set": {
                "heartbeat_at": current,
                "expires_at": expires_at,
                "updated_at": current,
            }
        },
    )
    matched_count = getattr(result, "matched_count", None)
    owned = (
        int(matched_count or 0) == 1
        if matched_count is not None
        else int(getattr(result, "modified_count", 0) or 0) == 1
    )
    if not owned:
        raise SnapchatLeaseLost("Snapchat V2 sync lease is no longer owned by this worker")
    return expires_at


async def release_lease(
    db: Any,
    user_id: str,
    ad_account_id: str,
    owner_id: str,
    *,
    outcome: str = "released",
    now: Callable[[], datetime] = _utcnow,
) -> bool:
    current = now().astimezone(timezone.utc)
    result = await db[SNAPCHAT_LEASE_COLLECTION].update_one(
        {
            **lease_key(user_id, ad_account_id),
            "status": "held",
            "owner_id": owner_id,
        },
        {
            "$set": {
                "status": "released",
                "owner_id": None,
                "released_at": current,
                "expires_at": current,
                "last_outcome": str(outcome)[:64],
                "updated_at": current,
            }
        },
    )
    return int(getattr(result, "modified_count", 0) or 0) == 1


async def recover_expired_leases(
    db: Any,
    *,
    now: Callable[[], datetime] = _utcnow,
) -> int:
    current = now().astimezone(timezone.utc)
    result = await db[SNAPCHAT_LEASE_COLLECTION].update_many(
        {
            "provider": SNAPCHAT_PROVIDER,
            "status": "held",
            "expires_at": {"$lte": current},
        },
        {
            "$set": {
                "status": "abandoned",
                "owner_id": None,
                "released_at": current,
                "updated_at": current,
                "last_outcome": "stale_lease_recovered",
            }
        },
    )
    return int(getattr(result, "modified_count", 0) or 0)


async def get_lease_status(db: Any, user_id: str, ad_account_id: str) -> dict[str, Any] | None:
    return await db[SNAPCHAT_LEASE_COLLECTION].find_one(
        lease_key(user_id, ad_account_id),
        {"_id": 0},
    )
