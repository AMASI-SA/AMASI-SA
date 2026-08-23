"""Atomic distributed lease for Snapchat analytics refresh runs.

Prevents more than one Snapchat refresh from executing at the same time
across multiple replicas.  The lease is stored in
``mezan_integration_sync_leases_v2`` with a unique compound index on
``(user_id, provider)``.  Acquisition uses ``find_one_and_update`` with an
``$or`` filter that admits either "no active holder" or "expired lease" or
"self is holder" – making the take-over deterministic.

The lease intentionally lives outside of ``mezan_integration_sync_runs_v2``
so a failed / racing insert cannot leave a phantom "running" row visible
to the fail-closed dashboard reader.
"""
from __future__ import annotations

import os
import socket
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from .snapchat_native_data_common import SNAPCHAT_PROVIDER_ID

LEASE_COLLECTION = "mezan_integration_sync_leases_v2"

# 10 minute cadence with a 25-minute lease TTL gives the run enough headroom
# for the provider fetch (~1-3 minutes typical, up to ~10 minutes worst case)
# while still allowing another replica to take over promptly if the holder
# process crashes without releasing.
DEFAULT_LEASE_TTL = timedelta(minutes=25)
MIN_LEASE_TTL = timedelta(minutes=5)
MAX_LEASE_TTL = timedelta(hours=1)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds")


def _timestamp(value: Any) -> datetime | None:
    """Normalize legacy ISO strings and BSON datetimes for lease comparisons."""

    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:10]}"


def _collection(db: Any, name: str) -> Any:
    getter = getattr(db, "get_collection", None)
    if callable(getter):
        return getter(name)
    return db[name]


@dataclass(frozen=True)
class SnapchatLeaseHandle:
    """Handle returned by :func:`acquire_snapchat_lease`.

    Callers pass this back to :func:`renew_snapchat_lease` /
    :func:`release_snapchat_lease` so the atomic writes match the exact
    owner token that was granted.
    """

    user_id: str
    provider: str
    owner_token: str
    worker_id: str
    acquired_at: datetime
    lease_until: datetime
    run_id: str | None = None
    took_over_from: str | None = None


async def acquire_snapchat_lease(
    db: Any,
    *,
    user_id: str,
    now: datetime | None = None,
    ttl: timedelta = DEFAULT_LEASE_TTL,
    worker_id: str | None = None,
) -> SnapchatLeaseHandle | None:
    """Attempt to atomically acquire the Snapchat refresh lease.

    Returns a :class:`SnapchatLeaseHandle` when the lease is granted, or
    ``None`` when another live holder already owns it.  The write is a
    single ``find_one_and_update`` guarded by an ``$or`` filter so two
    replicas racing at the same instant cannot both win.
    """

    if not isinstance(user_id, str) or user_id != user_id.strip() or not user_id:
        return None
    current = now if isinstance(now, datetime) and now.tzinfo else _utcnow()
    ttl_bounded = max(MIN_LEASE_TTL, min(ttl, MAX_LEASE_TTL))
    resolved_worker = worker_id or _worker_id()
    owner_token = uuid.uuid4().hex
    lease_until = current + ttl_bounded

    collection = _collection(db, LEASE_COLLECTION)
    create_index = getattr(collection, "create_index", None)
    if callable(create_index):
        await create_index(
            [("user_id", 1), ("provider", 1)],
            unique=True,
            name="uq_snapchat_sync_lease_user_provider",
        )
    existing = await collection.find_one(
        {"user_id": user_id, "provider": SNAPCHAT_PROVIDER_ID},
        {"_id": 0},
    )
    previous_owner = None
    filter_ors: list[dict[str, Any]] = [
        {"user_id": user_id, "provider": SNAPCHAT_PROVIDER_ID, "released": True},
    ]
    if existing is None:
        upsert = True
        set_on_insert = {
            "user_id": user_id,
            "provider": SNAPCHAT_PROVIDER_ID,
            "created_at": _iso(current),
        }
        filter_ors.append(
            {"user_id": user_id, "provider": SNAPCHAT_PROVIDER_ID, "owner_token": {"$exists": False}}
        )
    else:
        upsert = False
        set_on_insert = {}
        previous_owner = str(existing.get("owner_token") or "").strip() or None
        observed_until = _timestamp(existing.get("lease_until"))
        observed_acquired = _timestamp(existing.get("acquired_at"))
        hard_deadline = current - MAX_LEASE_TTL
        lease_is_expired = observed_until is None or observed_until < current
        lease_is_hard_stale = (
            observed_acquired is not None and observed_acquired < hard_deadline
        )
        lease_is_malformed_future = (
            observed_acquired is not None
            and observed_acquired > current + MAX_LEASE_TTL
        ) or (
            observed_acquired is None
            and observed_until is not None
            and observed_until > current + MAX_LEASE_TTL
        )
        if lease_is_expired or lease_is_hard_stale or lease_is_malformed_future:
            # Compare-and-swap against the exact document we observed. This makes
            # legacy string/BSON/null leases reclaimable without stealing a lease
            # that another worker renewed between find_one and find_one_and_update.
            observed_filter: dict[str, Any] = {
                "user_id": user_id,
                "provider": SNAPCHAT_PROVIDER_ID,
            }
            for field in ("owner_token", "lease_until", "acquired_at", "released"):
                if field in existing:
                    observed_filter[field] = existing[field]
                else:
                    observed_filter[field] = {"$exists": False}
            filter_ors.append(observed_filter)

    result = await collection.find_one_and_update(
        {"$or": filter_ors},
        {
            "$set": {
                "user_id": user_id,
                "provider": SNAPCHAT_PROVIDER_ID,
                "owner_token": owner_token,
                "worker_id": resolved_worker,
                "acquired_at": _iso(current),
                "lease_until": _iso(lease_until),
                "released": False,
                "run_id": None,
                "previous_owner": previous_owner,
            },
            "$setOnInsert": set_on_insert,
        },
        upsert=upsert,
        return_document=True,
        projection={"_id": 0},
    )
    if not result or result.get("owner_token") != owner_token:
        return None
    took_over_from = result.get("previous_owner")
    return SnapchatLeaseHandle(
        user_id=user_id,
        provider=SNAPCHAT_PROVIDER_ID,
        owner_token=owner_token,
        worker_id=resolved_worker,
        acquired_at=current,
        lease_until=lease_until,
        run_id=None,
        took_over_from=took_over_from if isinstance(took_over_from, str) else None,
    )


async def bind_run_to_lease(
    db: Any,
    handle: SnapchatLeaseHandle,
    *,
    run_id: str,
) -> bool:
    """Attach a run_id to the lease atomically.

    Callers create the run row first (or reuse the caller's run_id) and
    then bind it here so the reader can prove the fact came from *this*
    lease's run, not an unrelated concurrent one.  Returns ``True`` if
    the binding was applied.
    """

    if not isinstance(run_id, str) or run_id != run_id.strip() or not run_id:
        return False
    result = await _collection(db, LEASE_COLLECTION).update_one(
        {
            "user_id": handle.user_id,
            "provider": handle.provider,
            "owner_token": handle.owner_token,
            "released": False,
        },
        {"$set": {"run_id": run_id}},
    )
    return getattr(result, "matched_count", 0) == 1


async def renew_snapchat_lease(
    db: Any,
    handle: SnapchatLeaseHandle,
    *,
    now: datetime | None = None,
    ttl: timedelta = DEFAULT_LEASE_TTL,
) -> bool:
    """Extend ``lease_until`` while the same owner still holds it."""

    current = now if isinstance(now, datetime) and now.tzinfo else _utcnow()
    ttl_bounded = max(MIN_LEASE_TTL, min(ttl, MAX_LEASE_TTL))
    lease_until = current + ttl_bounded
    result = await _collection(db, LEASE_COLLECTION).update_one(
        {
            "user_id": handle.user_id,
            "provider": handle.provider,
            "owner_token": handle.owner_token,
            "released": False,
        },
        {"$set": {"lease_until": _iso(lease_until)}},
    )
    return getattr(result, "matched_count", 0) == 1


async def release_snapchat_lease(
    db: Any,
    handle: SnapchatLeaseHandle,
    *,
    final_status: str = "released",
    now: datetime | None = None,
) -> bool:
    """Release the lease atomically.  Never releases a lease we do not own."""

    current = now if isinstance(now, datetime) and now.tzinfo else _utcnow()
    result = await _collection(db, LEASE_COLLECTION).update_one(
        {
            "user_id": handle.user_id,
            "provider": handle.provider,
            "owner_token": handle.owner_token,
        },
        {
            "$set": {
                "released": True,
                "released_at": _iso(current),
                "final_status": str(final_status)[:64],
            }
        },
    )
    return getattr(result, "matched_count", 0) == 1


__all__ = [
    "DEFAULT_LEASE_TTL",
    "LEASE_COLLECTION",
    "MAX_LEASE_TTL",
    "MIN_LEASE_TTL",
    "SnapchatLeaseHandle",
    "acquire_snapchat_lease",
    "bind_run_to_lease",
    "release_snapchat_lease",
    "renew_snapchat_lease",
]
