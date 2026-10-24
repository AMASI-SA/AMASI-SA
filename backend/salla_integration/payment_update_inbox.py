"""Durable retry for verified payment-update snapshots through the source fence."""
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
import uuid
from pymongo import ReturnDocument
from .webhook_order_sync import _resolve_user_id, _order_payload, sync_order_from_verified_webhook
from .store_scope import is_attribution_pilot_store

COLLECTION = "salla_payment_update_inbox"
log = logging.getLogger(__name__)


async def enqueue_verified_payment(db, event):
    if event.get("event") != "order.payment.updated":
        return None
    merchant = str(event.get("merchant") or "").strip()
    if not merchant or is_attribution_pilot_store(merchant):
        return None
    owner = await _resolve_user_id(db, merchant)
    payload = _order_payload(event)
    if not owner or not payload.get("reference_id") or not payload.get("id"):
        return None
    # Only the verified order payload needed for replay, no headers or secrets.
    safe_event = {"event": "order.payment.updated", "merchant": merchant, "data": deepcopy(payload)}
    key = hashlib.sha256(json.dumps(safe_event, sort_keys=True, default=str).encode()).hexdigest()
    now = datetime.now(timezone.utc)
    await db[COLLECTION].update_one({"_id": key}, {"$setOnInsert": {
        "user_id": owner, "merchant_id": merchant, "order_number": str(payload["reference_id"]),
        "event": safe_event, "state": "pending", "attempts": 0,
        "next_attempt_at": now, "created_at": now, "lease_until": now,
    }}, upsert=True)
    return key


async def _apply(db, item):
    if await _resolve_user_id(db, item["merchant_id"]) != item["user_id"]:
        return "paused"
    result = await sync_order_from_verified_webhook(db, item["event"])
    if result.get("synced") or result.get("reason") == "stale_salla_snapshot":
        return "done"
    return "pending"


async def run_once(db, *, now=None, limit=10):
    for _ in range(limit):
        claim_time = now or datetime.now(timezone.utc)
        claim = uuid.uuid4().hex
        item = await db[COLLECTION].find_one_and_update({"state": "pending",
            "next_attempt_at": {"$lte": claim_time}, "lease_until": {"$lte": claim_time}},
            {"$set": {"claim": claim, "lease_until": claim_time + timedelta(seconds=60)},
             "$inc": {"attempts": 1}}, sort=[("next_attempt_at", 1)], return_document=ReturnDocument.AFTER)
        if not item:
            return
        try:
            state = await asyncio.wait_for(_apply(db, item), timeout=30)
        except Exception:
            # Keep retry evidence, but never log URLs, order identities or payloads.
            log.warning("payment_update_inbox.retry_required")
            state = "pending"
        finished_at = now or datetime.now(timezone.utc)
        values = {"state": state, "lease_until": finished_at, "last_attempt_at": finished_at,
                  "next_attempt_at": finished_at + timedelta(seconds=min(300, 5 * 2 ** min(item["attempts"], 6)))}
        if state == "done":
            values["completed_at"] = finished_at
        await db[COLLECTION].update_one({"_id": item["_id"], "claim": claim}, {"$set": values})


async def start_worker(db):
    await db[COLLECTION].create_index([("state", 1), ("next_attempt_at", 1), ("lease_until", 1)])
    await db[COLLECTION].create_index("completed_at", expireAfterSeconds=30 * 86400)
    await db[COLLECTION].create_index([("user_id", 1), ("state", 1), ("order_number", 1)])

    async def run():
        while True:
            try:
                await run_once(db)
            except Exception:
                log.warning("payment_update_inbox.worker_retry_required")
            await asyncio.sleep(5)
    return asyncio.create_task(run())
