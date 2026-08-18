"""Replica-safe global cycle for Advertising Product Watch V3.

This mirrors the proven Campaign AI cadence pattern: create one singleton
control document, then claim only when due and not leased. No upsert is used on
a conditional claim, so a non-claimable existing singleton cannot cause a
DuplicateKey race across web replicas.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import uuid
from typing import Any

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from advertising_product_watch_v3 import (
    CAMPAIGN_PRODUCT_LINK_COLLECTION,
    CADENCE_COLLECTION,
    CADENCE_ID,
    LEASE_SECONDS,
    WATCH_INTERVAL_SECONDS,
    ensure_product_watch_indexes,
    scan_user_product_watch,
)
from campaign_ai_product_change_history_v3 import (
    snapshot_recently_watched_products,
)


RETRY_SECONDS = 5 * 60


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def _ensure_control_document(collection: Any, current: datetime) -> None:
    try:
        await collection.insert_one({
            "_id": CADENCE_ID,
            "cadence_id": CADENCE_ID,
            "state": "idle",
            "next_run_at": current - timedelta(seconds=1),
            "protocol_version": 2,
            "created_at": current,
            "updated_at": current,
        })
    except DuplicateKeyError:
        pass


async def claim_cycle(
    db: Any,
    *,
    now: datetime | None = None,
    owner: str | None = None,
) -> dict[str, Any]:
    current = (now or _utcnow()).astimezone(timezone.utc)
    collection = db[CADENCE_COLLECTION]
    await _ensure_control_document(collection, current)
    claim_owner = owner or str(uuid.uuid4())
    lease_until = current + timedelta(seconds=LEASE_SECONDS)
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
            "lease_until": lease_until,
            "last_started_at": current,
            "updated_at": current,
            "protocol_version": 2,
        }},
        return_document=ReturnDocument.AFTER,
    )
    if document:
        return {"claimed": True, "owner": claim_owner, "lease_until": lease_until}
    existing = await collection.find_one({"_id": CADENCE_ID}, {"_id": 0}) or {}
    lease = existing.get("lease_until")
    running = bool(
        existing.get("state") == "running"
        and isinstance(lease, datetime)
        and lease.replace(tzinfo=lease.tzinfo or timezone.utc) > current
    )
    return {
        "claimed": False,
        "skip_reason": "running_elsewhere" if running else "not_due",
        "next_run_at": existing.get("next_run_at"),
        "lease_until": lease,
    }


async def finish_cycle(
    db: Any,
    owner: str,
    *,
    failed: bool,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = (now or _utcnow()).astimezone(timezone.utc)
    next_run_at = current + timedelta(
        seconds=RETRY_SECONDS if failed else WATCH_INTERVAL_SECONDS
    )
    result = await db[CADENCE_COLLECTION].update_one(
        {"_id": CADENCE_ID, "state": "running", "owner": owner},
        {
            "$set": {
                "state": "idle",
                "next_run_at": next_run_at,
                "last_finished_at": current,
                "last_status": "failed" if failed else "complete",
                "updated_at": current,
            },
            "$unset": {"owner": "", "lease_until": ""},
        },
    )
    return {
        "released": bool(getattr(result, "modified_count", 0)),
        "next_run_at": next_run_at,
    }


async def run_global_product_watch(db: Any) -> dict[str, Any]:
    await ensure_product_watch_indexes(db)
    claim = await claim_cycle(db)
    if not claim.get("claimed"):
        return {"skipped": True, **claim}
    owner = str(claim["owner"])
    try:
        user_ids = await db[CAMPAIGN_PRODUCT_LINK_COLLECTION].distinct("user_id")
        summaries = []
        content_snapshots = []
        for user_id in sorted(str(value) for value in user_ids if value):
            scan = await scan_user_product_watch(db, user_id)
            summaries.append(scan)
            content_snapshots.append(await snapshot_recently_watched_products(
                db,
                user_id,
            ))
        finished = await finish_cycle(db, owner, failed=False)
        return {
            "skipped": False,
            "users": len(summaries),
            "active_alerts": sum(int(row.get("active_alerts") or 0) for row in summaries),
            "watched_products": sum(int(row.get("watched_products") or 0) for row in summaries),
            "product_content_snapshots": sum(
                int(row.get("products_snapshotted") or 0)
                for row in content_snapshots
            ),
            "products_with_observed_changes": sum(
                int(row.get("products_with_changes") or 0)
                for row in content_snapshots
            ),
            "summaries": summaries,
            "next_run_at": finished.get("next_run_at"),
        }
    except Exception:
        await finish_cycle(db, owner, failed=True)
        raise


__all__ = ["claim_cycle", "finish_cycle", "run_global_product_watch"]
