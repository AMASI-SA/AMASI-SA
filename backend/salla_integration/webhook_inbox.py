"""Durable, bounded processing of verified Salla business notifications.

The HTTP ingress verifies the original bytes before calling this module. It
acknowledges only an acknowledged Mongo write, never completion of business
processing. Payloads use the existing encrypted private-payload store. A stable
event tombstone deduplicates deliveries without dropping a later changed event.

One renewable lease serializes this queue across the existing worker processes;
it is deliberately independent of the optional V3 shadow pipeline. Execution is
at least once after interruption: existing order/source and outbox idempotency
remain necessary when a process dies after a business write but before settling
its inbox row. An expired or lost claim never authorizes completion.
"""
from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
import re
from typing import Any, Awaitable, Callable
import uuid

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError
from pymongo.write_concern import WriteConcern

from customer_identity import decrypt_private_payload, encrypt_private_payload


COLLECTION = "salla_webhook_inbox"
LEASES_COLLECTION = "salla_webhook_inbox_leases"
WORKER_LEASE_ID = "verified-business-events"
MAX_ATTEMPTS = 8
LEASE_SECONDS = 90.0
PROCESSING_TIMEOUT_SECONDS = 45.0
HEARTBEAT_INTERVAL_SECONDS = 10.0
IDLE_SECONDS = 2.0
MAX_PAYLOAD_BYTES = 8 * 1024 * 1024
LIFECYCLE_EVENTS = frozenset({
    "app.store.authorize", "app.installed", "app.updated", "app.uninstalled",
})
_EVENT_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_.:-]{0,127}\Z")
_DURABLE_WRITE = WriteConcern(w="majority", j=True, wtimeout=5000)
log = logging.getLogger("salla.webhook_inbox")

Processor = Callable[[Any, dict[str, Any]], Awaitable[dict[str, Any]]]


class WebhookInboxInvalidEvent(ValueError):
    """A fixed public code; never includes the supplied payload."""


class _RetryProcessing(RuntimeError):
    pass


class _QuarantineProcessing(RuntimeError):
    pass


class _LeaseLost(RuntimeError):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _collection(db: Any, name: str = COLLECTION):
    # A local-primary acknowledgement may roll back during a failover. Queue,
    # dedupe and lease mutations must be majority committed and journalled.
    return db[name].with_options(write_concern=_DURABLE_WRITE)


def _semantic_payload(value: Any) -> Any:
    from .webhook_event_capture import _SECRET_KEYS
    if isinstance(value, dict):
        return {key: _semantic_payload(child) for key, child in value.items()
                if key.strip().lower() not in _SECRET_KEYS}
    if isinstance(value, list):
        return [_semantic_payload(child) for child in value]
    return value


def _prepare_event(event_body: dict[str, Any]) -> tuple[dict[str, Any], str, str, str]:
    if not isinstance(event_body, dict):
        raise WebhookInboxInvalidEvent("salla_webhook_invalid_object")
    name = event_body.get("event")
    if not isinstance(name, str) or not _EVENT_NAME.fullmatch(name.strip()):
        raise WebhookInboxInvalidEvent("salla_webhook_invalid_event")
    name = name.strip()
    if name in LIFECYCLE_EVENTS:
        raise WebhookInboxInvalidEvent("salla_webhook_lifecycle_not_queued")
    merchant = event_body.get("merchant")
    if isinstance(merchant, bool) or not isinstance(merchant, (str, int)):
        raise WebhookInboxInvalidEvent("salla_webhook_invalid_merchant")
    merchant = str(merchant).strip()
    if not merchant or len(merchant) > 160:
        raise WebhookInboxInvalidEvent("salla_webhook_invalid_merchant")
    # Keep customer choices, source timestamps and native IDs in the hash.
    # Only credential material is redacted; abandoned-cart PII is encrypted,
    # not erased from the processable payload or collapsed into one hash.
    from .webhook_event_capture import _sanitize
    try:
        safe = _sanitize(event_body)
        safe["event"] = name
        safe["merchant"] = merchant
        rendered = json.dumps(_semantic_payload(safe), ensure_ascii=False, sort_keys=True,
                              separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise WebhookInboxInvalidEvent("salla_webhook_invalid_payload") from exc
    if len(rendered) > MAX_PAYLOAD_BYTES:
        raise WebhookInboxInvalidEvent("salla_webhook_payload_too_large")
    digest = hashlib.sha256(rendered).hexdigest()
    return safe, name, merchant, digest


async def enqueue_verified_event(db: Any, event_body: dict[str, Any], *,
                                 now: datetime | None = None) -> dict[str, Any]:
    """Persist one verified event; failures propagate before HTTP can ACK it."""
    safe, name, merchant, digest = _prepare_event(event_body)
    ciphertext = encrypt_private_payload(safe)
    if not ciphertext:
        raise RuntimeError("salla_webhook_payload_not_encrypted")
    # A single indexed local lookup pins a known tenant at receipt time. No
    # transaction, Salla request or business mutation occurs in this path.
    from .webhook_order_sync import _resolve_user_id
    owner = await _resolve_user_id(db, merchant)
    received_at = now or _utcnow()
    row = {
        "merchant_id": merchant, "event_name": name, "event_hash": digest,
        "user_id": owner, "payload_ciphertext": ciphertext,
        "state": "pending", "attempts": 0, "created_at": received_at,
        "first_received_at": received_at, "next_attempt_at": received_at,
        "lease_until": received_at, "verified_before_enqueue": True,
    }
    update = {"$setOnInsert": row,
              "$set": {"last_received_at": received_at},
              "$inc": {"delivery_count": 1}}
    try:
        result = await _collection(db).update_one({"_id": digest}, update, upsert=True)
    except DuplicateKeyError:
        # Concurrent first deliveries may both attempt Mongo's upsert insert.
        # The unique _id is the authority, never an earlier find/check pair.
        result = await _collection(db).update_one({"_id": digest}, {
            "$set": {"last_received_at": received_at}, "$inc": {"delivery_count": 1},
        })
        if not getattr(result, "matched_count", 0):
            raise RuntimeError("salla_webhook_duplicate_not_persisted") from None
    if not getattr(result, "acknowledged", False):
        raise RuntimeError("salla_webhook_write_not_acknowledged")
    return {"queued": True, "duplicate": result.upserted_id is None,
            "event": name, "job_id": digest, "capture_hash_prefix": digest[:12]}


async def ensure_indexes(db: Any) -> None:
    """Startup-only indexes. Pending/quarantined rows have no TTL."""
    await _collection(db).create_index(
        [("state", 1), ("next_attempt_at", 1), ("lease_until", 1)],
        name="salla_webhook_inbox_due",
    )
    await _collection(db).create_index(
        [("merchant_id", 1), ("state", 1), ("created_at", 1)],
        name="salla_webhook_inbox_merchant_state",
    )
    await db.salla_integrations.create_index(
        [("store_id", 1), ("updated_at", -1)], name="salla_webhook_store_owner",
    )


async def _acquire_worker(db: Any, at: datetime, lease_seconds: float) -> dict | None:
    token = uuid.uuid4().hex
    try:
        row = await _collection(db, LEASES_COLLECTION).find_one_and_update({
            "_id": WORKER_LEASE_ID,
            "$or": [{"lease_until": {"$lte": at}}, {"lease_until": {"$exists": False}}],
        }, {"$setOnInsert": {"created_at": at},
            "$set": {"lease_token": token, "lease_until": at + timedelta(seconds=lease_seconds),
                     "acquired_at": at}, "$inc": {"epoch": 1}},
            upsert=True, return_document=ReturnDocument.AFTER)
    except DuplicateKeyError:
        return None
    return row if row and row.get("lease_token") == token else None


def _worker_filter(lease: dict, at: datetime | None = None) -> dict:
    query = {"_id": WORKER_LEASE_ID, "lease_token": lease["lease_token"],
             "epoch": lease["epoch"]}
    if at is not None:
        query["lease_until"] = {"$gt": at}
    return query


def _job_filter(item: dict, at: datetime) -> dict:
    return {"_id": item["_id"], "state": "processing",
            "claim_token": item["claim_token"], "lease_until": {"$gt": at}}


async def _renew_worker(db: Any, lease: dict, at: datetime, lease_seconds: float) -> bool:
    result = await _collection(db, LEASES_COLLECTION).update_one(_worker_filter(lease, at),
        {"$set": {"lease_until": at + timedelta(seconds=lease_seconds), "heartbeat_at": at}})
    return getattr(result, "matched_count", 0) == 1


async def _owns_worker(db: Any, lease: dict, at: datetime) -> bool:
    return bool(await _collection(db, LEASES_COLLECTION).find_one(_worker_filter(lease, at), {"_id": 1}))


async def _claim(db: Any, lease: dict, at: datetime, lease_seconds: float) -> dict | None:
    # Reclaim a process-death row only after its own lease expired. A live
    # duplicate delivery never resets its state, attempts or completion fence.
    return await _collection(db).find_one_and_update({"$or": [
        {"state": {"$in": ["pending", "retry"]}, "next_attempt_at": {"$lte": at}},
        {"state": "processing", "lease_until": {"$lte": at}},
    ]}, {"$set": {"state": "processing", "claim_token": uuid.uuid4().hex,
                   "worker_token": lease["lease_token"], "started_at": at,
                   "lease_until": at + timedelta(seconds=lease_seconds)},
         "$inc": {"attempts": 1}},
        sort=[("next_attempt_at", 1), ("created_at", 1), ("_id", 1)],
        return_document=ReturnDocument.AFTER)


async def _heartbeat(db: Any, lease: dict, item: dict, *, clock, lease_seconds: float,
                     interval: float, stop: asyncio.Event, lost: asyncio.Event) -> None:
    while True:
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
            return
        except asyncio.TimeoutError:
            pass
        try:
            at = clock()
            if not await _renew_worker(db, lease, at, lease_seconds):
                lost.set()
                return
            renewed = await _collection(db).update_one(_job_filter(item, at),
                {"$set": {"lease_until": at + timedelta(seconds=lease_seconds),
                          "heartbeat_at": at}})
            if getattr(renewed, "matched_count", 0) != 1:
                lost.set()
                return
        except Exception:
            lost.set()
            return


async def _process(db: Any, item: dict, *, processor: Processor | None, clock) -> dict:
    if item["attempts"] > MAX_ATTEMPTS:
        raise _QuarantineProcessing("attempt_limit")
    try:
        event = decrypt_private_payload(item.get("payload_ciphertext"))
        _, name, merchant, digest = _prepare_event(event)
    except Exception:
        raise _QuarantineProcessing("payload_unreadable") from None
    if digest != item["_id"] or merchant != item["merchant_id"] or name != item["event_name"]:
        raise _QuarantineProcessing("payload_identity_mismatch")
    from .webhook_order_sync import _resolve_user_id
    current_owner = await _resolve_user_id(db, merchant)
    pinned_owner = item.get("user_id")
    if pinned_owner and current_owner != pinned_owner:
        raise _QuarantineProcessing("owner_binding_changed")
    if not current_owner:
        raise _RetryProcessing("owner_not_bound")
    if not pinned_owner:
        # Only the inbox row is bound here. The business dispatcher receives
        # this expected owner and rechecks it at its current lookup boundary.
        query = _job_filter(item, clock())
        query["user_id"] = None
        bound = await _collection(db).update_one(query, {"$set": {"user_id": current_owner}})
        if getattr(bound, "matched_count", 0) != 1:
            raise _LeaseLost()
        pinned_owner = current_owner
    if processor is None:
        from .easy_mode_webhook import dispatch_event
        result = await dispatch_event(db, event, raise_on_processing_error=True,
                                      expected_user_id=pinned_owner)
    else:
        result = await processor(db, event)
    if not isinstance(result, dict) or result.get("ok") is not True:
        raise _RetryProcessing("processing_not_confirmed")
    return result


async def _execute(db: Any, lease: dict, item: dict, *, processor, clock,
                   processing_timeout: float, lease_seconds: float,
                   heartbeat_interval: float) -> tuple[str, str | None]:
    stop, lost = asyncio.Event(), asyncio.Event()
    work = asyncio.create_task(_process(db, item, processor=processor, clock=clock))
    monitor = asyncio.create_task(_heartbeat(db, lease, item, clock=clock,
        lease_seconds=lease_seconds, interval=heartbeat_interval, stop=stop, lost=lost))
    lease_loss = asyncio.create_task(lost.wait())
    try:
        done, _ = await asyncio.wait({work, lease_loss}, timeout=processing_timeout,
                                     return_when=asyncio.FIRST_COMPLETED)
        if lost.is_set():
            raise _LeaseLost()
        if work not in done:
            return "retry", "processing_timeout"
        try:
            work.result()
        except _LeaseLost:
            raise
        except _QuarantineProcessing as exc:
            return "quarantined", str(exc)
        except _RetryProcessing as exc:
            return "retry", str(exc)
        except Exception:
            return "retry", "processing_failed"
        return "done", None
    finally:
        # Do not release a claim while a cancelled business coroutine is still
        # running. Keep the heartbeat alive until it acknowledges cancellation.
        if not work.done():
            work.cancel()
        await asyncio.gather(work, return_exceptions=True)
        stop.set()
        lease_loss.cancel()
        monitor.cancel()
        await asyncio.gather(monitor, lease_loss, return_exceptions=True)


async def _settle(db: Any, lease: dict, item: dict, state: str, reason: str | None,
                  at: datetime) -> bool:
    if not await _owns_worker(db, lease, at):
        return False
    if state == "retry" and item["attempts"] >= MAX_ATTEMPTS:
        state, reason = "quarantined", "attempt_limit"
    values = {"state": state, "last_attempt_at": at, "lease_until": at,
              "last_error_code": reason}
    update: dict[str, Any] = {"$set": values, "$unset": {"claim_token": "", "worker_token": ""}}
    if state == "done":
        values["completed_at"] = at
        # Retain the stable dedupe tombstone, without keeping another copy of
        # customer content indefinitely. No TTL may re-enable old deliveries.
        update["$unset"]["payload_ciphertext"] = ""
    elif state == "quarantined":
        values["quarantined_at"] = at
    else:
        values["next_attempt_at"] = at + timedelta(seconds=min(900, 5 * 2 ** min(item["attempts"], 8)))
    result = await _collection(db).update_one(_job_filter(item, at), update)
    return getattr(result, "matched_count", 0) == 1


async def run_once(db: Any, *, now: datetime | None = None, limit: int = 10,
                   processor: Processor | None = None, clock=None,
                   processing_timeout: float = PROCESSING_TIMEOUT_SECONDS,
                   lease_seconds: float = LEASE_SECONDS,
                   heartbeat_interval: float = HEARTBEAT_INTERVAL_SECONDS) -> dict[str, Any]:
    """Drain a bounded batch, with a single consumer across production replicas."""
    if not 0 < processing_timeout < lease_seconds or not 0 < heartbeat_interval < lease_seconds:
        raise ValueError("salla_webhook_invalid_worker_deadline")
    clock = clock or ((lambda: now) if now is not None else _utcnow)
    counts: dict[str, Any] = {"claimed": 0, "done": 0, "retry": 0, "quarantined": 0,
                              "lease_busy": False, "lease_lost": False}
    lease = await _acquire_worker(db, clock(), lease_seconds)
    if lease is None:
        counts["lease_busy"] = True
        return counts
    item = None
    try:
        for _ in range(max(1, min(int(limit), 50))):
            if not await _renew_worker(db, lease, clock(), lease_seconds):
                counts["lease_lost"] = True
                break
            item = await _claim(db, lease, clock(), lease_seconds)
            if item is None:
                break
            counts["claimed"] += 1
            # A delayed database claim may return after this process lost its
            # global lease. Never begin a business handler on that stale result.
            if not await _owns_worker(db, lease, clock()):
                counts["lease_lost"] = True
                break
            try:
                state, reason = await _execute(db, lease, item, processor=processor, clock=clock,
                    processing_timeout=processing_timeout, lease_seconds=lease_seconds,
                    heartbeat_interval=heartbeat_interval)
            except _LeaseLost:
                counts["lease_lost"] = True
                break
            if not await _settle(db, lease, item, state, reason, clock()):
                counts["lease_lost"] = True
                break
            if state == "retry" and item["attempts"] >= MAX_ATTEMPTS:
                state = "quarantined"
            counts[state] += 1
            item = None
            await asyncio.sleep(0)
    except asyncio.CancelledError:
        if item is not None:
            # _execute's finally has awaited cancellation acknowledgement.
            # A failed cleanup leaves the durable processing lease to expire.
            with suppress(Exception):
                await _settle(db, lease, item, "retry", "worker_interrupted", clock())
        raise
    finally:
        with suppress(Exception):
            await _collection(db, LEASES_COLLECTION).update_one(_worker_filter(lease),
                {"$set": {"lease_until": clock(), "released_at": clock()}})
    return counts


async def start_worker(db: Any):
    await ensure_indexes(db)

    async def run():
        while True:
            try:
                result = await run_once(db)
            except Exception:
                log.warning("salla_webhook_inbox.worker_retry_required")
                result = {}
            if result.get("retry"):
                log.warning("salla_webhook_inbox.retry_scheduled count=%d", result["retry"])
            if result.get("quarantined"):
                log.error("salla_webhook_inbox.quarantined count=%d", result["quarantined"])
            # Empty polling is a single indexed queue operation, not a scan or
            # provider refresh. Yield between full batches to existing jobs.
            await asyncio.sleep(0.1 if result.get("claimed") == 10 else IDLE_SECONDS)

    return asyncio.create_task(run(), name="salla-webhook-inbox")
