"""Cross-replica startup serialization and replica-specific jitter."""
from __future__ import annotations

import hashlib
import os
import secrets
import socket
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from pymongo.errors import DuplicateKeyError

COLLECTION = "backend_startup_leases_v1"
LEASE_ID = "backend-heavy-initialization"


def replica_jitter(max_seconds: float) -> float:
    if max_seconds <= 0:
        return 0.0
    identity = os.environ.get("REPLICA_ID") or socket.gethostname()
    stable = int.from_bytes(hashlib.sha256(identity.encode()).digest()[:8], "big")
    # Stable spreading plus secure per-boot entropy; never derives from PID.
    stable_part = (stable / (2**64 - 1)) * max_seconds * 0.75
    random_part = secrets.randbelow(1_000_001) / 1_000_000 * max_seconds * 0.25
    return min(max_seconds, stable_part + random_part)


def new_owner_id() -> str:
    return f"{socket.gethostname()}:{uuid.uuid4()}"


async def acquire_startup_lease(
    db: Any, owner_id: str, *, now: datetime | None = None, ttl_seconds: int = 3600
) -> bool:
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    try:
        result = await db[COLLECTION].update_one(
            {
                "_id": LEASE_ID,
                "$or": [
                    {"owner_id": owner_id},
                    {"expires_at": {"$lte": current}},
                    {"expires_at": {"$exists": False}},
                ],
            },
            {
                "$set": {
                    "owner_id": owner_id,
                    "acquired_at": current,
                    "expires_at": current + timedelta(seconds=max(60, ttl_seconds)),
                }
            },
            upsert=True,
        )
    except DuplicateKeyError:
        return False
    return bool(result.matched_count or result.upserted_id)


async def release_startup_lease(db: Any, owner_id: str) -> None:
    await db[COLLECTION].delete_one({"_id": LEASE_ID, "owner_id": owner_id})


__all__ = [
    "COLLECTION", "LEASE_ID", "acquire_startup_lease", "new_owner_id",
    "release_startup_lease", "replica_jitter",
]
