"""Durable shipping observations; never queries Salla or replays an order.

Only verified webhook callers enqueue. Persist before acknowledging/processing
the webhook. Retries update shipping fields through the existing carrier fence.
"""
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
import uuid

from pymongo import ReturnDocument
from operational_atomic import operational_owner
from salla_shipping import CURRENT_SHIPPING, accept_shipping, extract_shipping, shipping_root_fields
from .webhook_order_sync import _resolve_user_id, _order_payload, _shipment_payload
from .store_scope import is_attribution_pilot_store

COLLECTION = "salla_shipping_label_inbox"
log = logging.getLogger(__name__)


def _ref(value):
    return str(value).strip() if isinstance(value, (str, int)) and not isinstance(value, bool) else ""


async def enqueue_verified_label(db, event):
    name = _ref(event.get("event"))
    if name not in {"order.created", "order.updated", "order.status.updated"} and "shipment" not in name:
        return None
    merchant = _ref(event.get("merchant"))
    if not merchant or is_attribution_pilot_store(merchant):
        return None
    shipment_event = "shipment" in name
    payload = _shipment_payload(event) if shipment_event else _order_payload(event)
    observation = extract_shipping(payload, event_name=name, event_time=event.get("created_at"))
    # Persist invalidations too: otherwise a failed cancellation/replacement
    # could be lost while an older positive label is recovered successfully.
    if not observation or not any(observation.get(key) for key in
            ("company_name", "company_code", "status", "label_url")):
        return None
    owner = await _resolve_user_id(db, merchant)
    if not owner:
        return None
    order = payload.get("order") if isinstance(payload.get("order"), dict) else {}
    number = _ref(payload.get("order_reference_id") or payload.get("reference_id") or order.get("reference_id") or order.get("order_number"))
    internal_id = _ref(payload.get("order_id") or order.get("id")) if shipment_event else _ref(payload.get("id"))
    if not number and not internal_id:
        return None
    # No customer, address, payment data, credentials or entire webhook retained.
    content = {"user_id": owner, "merchant_id": merchant, "order_number": number,
               "order_id": internal_id, "observation": observation}
    key = hashlib.sha256(json.dumps(content, sort_keys=True, default=str).encode()).hexdigest()
    now = datetime.now(timezone.utc)
    await db[COLLECTION].update_one({"_id": key}, {"$setOnInsert": {
        **content, "state": "pending", "attempts": 0, "next_attempt_at": now,
        "created_at": now, "lease_until": now,
    }}, upsert=True)
    return key


async def _apply(db, item):
    if await _resolve_user_id(db, item["merchant_id"]) != item["user_id"]:
        return "paused"

    async def persist(scoped):
        selector = {"user_id": item["user_id"]}
        if item["order_number"]:
            selector["order_number"] = item["order_number"]
        else:
            identifiers = [item["order_id"]]
            if item["order_id"].isdigit():
                identifiers.append(int(item["order_id"]))
            selector["$or"] = [{field: {"$in": identifiers}}
                for field in ("order_id", "salla_order_id", "raw_by_source.salla_direct.id")]
        rows = await scoped.unified_orders.find(selector).limit(2).to_list(length=2)
        if not rows:
            return "pending"
        if len(rows) != 1:
            return "paused"
        current = rows[0]
        raw = (current.get("raw_by_source") or {}).get("salla_direct") or {}
        current_id = _ref(current.get("order_id") or current.get("salla_order_id") or raw.get("id"))
        if item["order_id"] and current_id and item["order_id"] != current_id:
            return "paused"
        # Do not briefly recover an old PDF while a newer invalidation is
        # waiting after a failed write. Wait for its fence to reach the order.
        references = [{"order_number": current["order_number"]}]
        if current_id:
            references.append({"order_id": current_id})
        shipment_id = item["observation"].get("shipment_id")
        if shipment_id and item["observation"].get("status") not in {"cancelled", "canceled", "void", "deleted"}:
            cancelled = await scoped[COLLECTION].find_one({"user_id": item["user_id"], "$or": references,
                "observation.shipment_id": shipment_id,
                "observation.status": {"$in": ["cancelled", "canceled", "void", "deleted"]},
                "observation.shipment_updated_at": {"$gte": item["observation"].get("shipment_updated_at") or ""}},
                {"_id": 1})
            if cancelled:
                return "done"
        newer = await scoped[COLLECTION].find_one({"user_id": item["user_id"],
            "state": "pending", "_id": {"$ne": item["_id"]}, "$or": references,
            "observation.provider_updated_at": {"$gt": item["observation"].get("provider_updated_at") or ""}},
            {"_id": 1})
        if newer:
            return "pending"
        accepted = accept_shipping(current, item["observation"])
        if not accepted:
            return "done"  # Superseded or older provider observation.
        fields = shipping_root_fields(accepted)
        fields[CURRENT_SHIPPING] = deepcopy(accepted)
        result = await scoped.unified_orders.update_one({"_id": current["_id"],
            "user_id": item["user_id"], CURRENT_SHIPPING: current.get(CURRENT_SHIPPING, {"$exists": False})},
            {"$set": fields})
        if result.matched_count != 1:
            raise RuntimeError("shipping_inbox_concurrent_change")
        return "done"

    return await operational_owner(db, item["user_id"], persist)


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
            log.warning("shipping_label_inbox.retry_required")
            state = "pending"
        finished_at = now or datetime.now(timezone.utc)
        values = {"state": state, "lease_until": finished_at, "last_attempt_at": finished_at,
                  "next_attempt_at": finished_at + timedelta(seconds=min(300, 5 * 2 ** min(item["attempts"], 6)))}
        # Retain cancellation tombstones: the original label can arrive before
        # its order, or be redelivered after the normal completed-job retention.
        if state == "done" and item["observation"].get("status") not in {"cancelled", "canceled", "void", "deleted"}:
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
                log.warning("shipping_label_inbox.worker_retry_required")
            await asyncio.sleep(5)
    return asyncio.create_task(run())
