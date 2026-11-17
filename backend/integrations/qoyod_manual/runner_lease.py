"""One valid manual-send runner across updated workers, using Mongo's clock.

This slot is separate from automatic Qoyod sending. Callers still own their
per-job lease and must check both leases immediately before provider writes.
"""
from __future__ import annotations

import logging

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError


COLLECTION = "qoyod_manual_send_runner_leases"
RUNNER_ID = "main"
log = logging.getLogger(__name__)


def _live(token):
    return {"_id": RUNNER_ID, "lease_token": token,
            "$expr": {"$gt": ["$lease_until", "$$NOW"]}}


async def claim(db, token: str, lease_seconds: int) -> bool:
    """Atomically claim an absent/expired slot; unknown ownership is refused."""
    if not isinstance(token, str) or not token or type(lease_seconds) is not int or lease_seconds < 1:
        return False
    try:
        row = await db[COLLECTION].find_one_and_update(
            {"_id": RUNNER_ID},
            [{"$replaceWith": {"$cond": [
                {"$lte": [{"$ifNull": ["$lease_until", "$$NOW"]}, "$$NOW"]},
                {"$mergeObjects": ["$$ROOT", {
                    "lease_token": {"$literal": token},
                    "lease_until": {"$add": ["$$NOW", lease_seconds * 1000]},
                    "acquired_at": "$$NOW",
                }]},
                "$$ROOT",
            ]}}],
            upsert=True, return_document=ReturnDocument.AFTER,
            projection={"lease_token": 1},
        )
    except DuplicateKeyError:
        return False
    except Exception as exc:
        log.warning("manual_runner_claim_unavailable type=%s", type(exc).__name__)
        return False
    return bool(row and row.get("lease_token") == token)


async def renew(db, token: str, lease_seconds: int) -> bool:
    """An expired or replaced owner cannot resurrect its lease."""
    if not isinstance(token, str) or not token or type(lease_seconds) is not int or lease_seconds < 1:
        return False
    try:
        result = await db[COLLECTION].update_one(
            _live(token),
            [{"$set": {"lease_until": {"$add": ["$$NOW", lease_seconds * 1000]}}}],
        )
        return result.matched_count == 1
    except Exception as exc:
        log.warning("manual_runner_renew_unavailable type=%s", type(exc).__name__)
        return False


async def is_current(db, token: str) -> bool:
    if not isinstance(token, str) or not token:
        return False
    try:
        return bool(await db[COLLECTION].find_one(_live(token), {"_id": 1}))
    except Exception as exc:
        log.warning("manual_runner_check_unavailable type=%s", type(exc).__name__)
        return False


async def release(db, token: str) -> None:
    """Release only this token; failed cleanup is left to lease expiration."""
    if not isinstance(token, str) or not token:
        return
    try:
        await db[COLLECTION].update_one(
            {"_id": RUNNER_ID, "lease_token": token},
            [{"$set": {"lease_until": "$$NOW"}}],
        )
    except Exception as exc:
        log.warning("manual_runner_release_unavailable type=%s", type(exc).__name__)
