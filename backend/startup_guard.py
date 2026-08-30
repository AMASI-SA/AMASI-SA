"""Durable release-keyed startup coordination and replica-specific jitter."""
from __future__ import annotations

import asyncio
import hashlib
import os
import re
import secrets
import socket
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

from pymongo.errors import DuplicateKeyError

COLLECTION = "backend_startup_leases_v1"
LEASE_ID = "backend-heavy-initialization"
MIN_LEASE_SECONDS = 5
MAX_LEASE_SECONDS = 300
FULL_GIT_SHA = re.compile(r"^[0-9a-f]{40}$")


@dataclass(frozen=True)
class StartupClaim:
    state: str
    release_key: str
    owner_id: str | None = None
    fence: str | None = None


def replica_jitter(max_seconds: float) -> float:
    if max_seconds <= 0:
        return 0.0
    identity = os.environ.get("REPLICA_ID") or socket.gethostname()
    stable = int.from_bytes(hashlib.sha256(identity.encode()).digest()[:8], "big")
    stable_part = (stable / (2**64 - 1)) * max_seconds * 0.75
    random_part = secrets.randbelow(1_000_001) / 1_000_000 * max_seconds * 0.25
    return min(max_seconds, stable_part + random_part)


def new_owner_id() -> str:
    return f"{socket.gethostname()}:{uuid.uuid4()}"


def verified_release_key(
    release_payload: dict[str, Any], *, environment: dict[str, str] | None = None
) -> str:
    """Return a verified source SHA or an explicitly configured dev/test key."""
    release = release_payload.get("release") or {}
    source_sha = str(release.get("source_git_sha") or "").lower()
    verified = (
        release.get("verified_identity_available") is True
        and release.get("critical_file_hashes_match") is True
        and release.get("frontend_build_verified") is True
    )
    if verified and FULL_GIT_SHA.fullmatch(source_sha):
        return source_sha
    env = environment if environment is not None else os.environ
    mode = str(env.get("APP_ENV") or env.get("ENVIRONMENT") or "production").lower()
    explicit = str(env.get("TEST_RELEASE_STARTUP_KEY") or "")
    if mode in {"test", "development"} and explicit.startswith(("test:", "dev:")):
        return explicit
    raise ValueError("verified release source_git_sha is required for startup")


def _lease_seconds(value: int) -> int:
    return min(MAX_LEASE_SECONDS, max(MIN_LEASE_SECONDS, int(value)))


def _document_id(release_key: str) -> str:
    return f"{LEASE_ID}:{release_key}"


async def claim_startup_lease(
    db: Any,
    release_key: str,
    owner_id: str,
    *,
    now: datetime | None = None,
    ttl_seconds: int = 30,
) -> StartupClaim:
    """Atomically claim initialization or observe durable completion."""
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    collection = db[COLLECTION]
    document_id = _document_id(release_key)
    existing = await collection.find_one({"_id": document_id})
    if existing and existing.get("status") == "completed":
        return StartupClaim("completed", release_key)
    fence = str(uuid.uuid4())
    try:
        result = await collection.update_one(
            {
                "_id": document_id,
                "status": {"$ne": "completed"},
                "$or": [
                    {"expires_at": {"$lte": current}},
                    {"expires_at": {"$exists": False}},
                ],
            },
            {
                "$set": {
                    "release_key": release_key,
                    "owner_id": owner_id,
                    "fence": fence,
                    "status": "running",
                    "acquired_at": current,
                    "heartbeat_at": current,
                    "expires_at": current + timedelta(seconds=_lease_seconds(ttl_seconds)),
                },
                "$unset": {"completed_at": "", "error": ""},
            },
            upsert=True,
        )
    except DuplicateKeyError:
        return StartupClaim("waiting", release_key)
    if result.matched_count or result.upserted_id:
        return StartupClaim("leader", release_key, owner_id, fence)
    return StartupClaim("waiting", release_key)


async def heartbeat_startup_lease(
    db: Any,
    claim: StartupClaim,
    *,
    now: datetime | None = None,
    ttl_seconds: int = 30,
) -> bool:
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    result = await db[COLLECTION].update_one(
        {
            "_id": _document_id(claim.release_key),
            "owner_id": claim.owner_id,
            "fence": claim.fence,
            "status": "running",
        },
        {"$set": {
            "heartbeat_at": current,
            "expires_at": current + timedelta(seconds=_lease_seconds(ttl_seconds)),
        }},
    )
    return bool(result.matched_count)


async def complete_startup_lease(
    db: Any, claim: StartupClaim, *, now: datetime | None = None
) -> bool:
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    result = await db[COLLECTION].update_one(
        {
            "_id": _document_id(claim.release_key),
            "owner_id": claim.owner_id,
            "fence": claim.fence,
            "status": "running",
        },
        {
            "$set": {"status": "completed", "completed_at": current},
            "$unset": {"expires_at": ""},
        },
    )
    return bool(result.matched_count)


async def _heartbeat_loop(
    db: Any, claim: StartupClaim, interval: float, ttl_seconds: int
) -> None:
    while True:
        await asyncio.sleep(interval)
        if not await heartbeat_startup_lease(db, claim, ttl_seconds=ttl_seconds):
            raise RuntimeError("startup lease fencing lost")


async def run_release_startup(
    db: Any,
    *,
    release_key: str,
    owner_id: str,
    governor: Any,
    global_initialization: Callable[[], Awaitable[None]],
    local_initialization: Callable[[], Awaitable[None]],
    wait_timeout: float = 120.0,
    poll_interval: float = 0.5,
    ttl_seconds: int = 30,
) -> str:
    """Run global work once per release, then local work on every replica."""
    deadline = asyncio.get_running_loop().time() + max(0.1, wait_timeout)
    while True:
        claim = await claim_startup_lease(
            db, release_key, owner_id, ttl_seconds=ttl_seconds
        )
        if claim.state == "completed":
            await local_initialization()
            return "follower"
        if claim.state == "leader":
            heartbeat = asyncio.create_task(
                _heartbeat_loop(
                    db, claim, max(0.1, min(ttl_seconds / 3, 10.0)), ttl_seconds
                )
            )
            global_task: asyncio.Task[None] | None = None
            try:
                async with governor.heavy("startup", task_name="release_startup"):
                    global_task = asyncio.create_task(global_initialization())
                    done, _ = await asyncio.wait(
                        {global_task, heartbeat},
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if heartbeat in done:
                        # A lost fence/heartbeat must stop the old owner before
                        # its bounded lease can expire and a successor starts.
                        await heartbeat
                        raise RuntimeError("startup heartbeat stopped unexpectedly")
                    await global_task
                if not await complete_startup_lease(db, claim):
                    raise RuntimeError("startup completion rejected by owner fence")
            finally:
                if global_task is not None and not global_task.done():
                    global_task.cancel()
                    try:
                        await global_task
                    except asyncio.CancelledError:
                        pass
                heartbeat.cancel()
                try:
                    await heartbeat
                except asyncio.CancelledError:
                    pass
            await local_initialization()
            return "leader"
        if asyncio.get_running_loop().time() >= deadline:
            raise TimeoutError("timed out waiting for release startup completion")
        await asyncio.sleep(poll_interval)


__all__ = [
    "COLLECTION", "LEASE_ID", "StartupClaim", "claim_startup_lease",
    "complete_startup_lease", "heartbeat_startup_lease", "new_owner_id",
    "replica_jitter", "run_release_startup", "verified_release_key",
]
