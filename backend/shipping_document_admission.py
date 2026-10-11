"""Distributed admission for reusable bearer document reads, never provider writes."""
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import secrets
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError
from shipping_print_document import COLLECTION, DocumentError

SLOTS = "shipping_document_read_slots"
MAX_PARALLEL = 2
MAX_ATTEMPTS = 4
LEASE_SECONDS = 45
TOKEN_COOLDOWN_SECONDS = 5
GLOBAL_COOLDOWN_SECONDS = 1


def now():
    return datetime.now(timezone.utc)


@asynccontextmanager
async def document_read(db, document):
    claim = secrets.token_hex(16)
    slot = None
    instant = now()
    deadline = instant + timedelta(seconds=LEASE_SECONDS)
    for index in range(MAX_PARALLEL):
        try:
            await db[SLOTS].update_one({"_id": index}, {"$setOnInsert": {
                "available_at": datetime(1970, 1, 1, tzinfo=timezone.utc)}}, upsert=True)
        except DuplicateKeyError:
            pass
        row = await db[SLOTS].find_one_and_update({"_id": index, "available_at": {"$lte": instant}},
            {"$set": {"claim": claim, "available_at": deadline}}, return_document=ReturnDocument.AFTER)
        if row:
            slot = index
            break
    if slot is None:
        raise DocumentError("shipping_document_read_busy", status_code=429)
    acquired = False
    try:
        row = await db[COLLECTION].find_one_and_update({
            "_id": document["_id"], "status": "verified", "expires_at": {"$gt": instant},
            "$and": [
                {"$or": [{"read_attempts": {"$exists": False}}, {"read_attempts": {"$lt": MAX_ATTEMPTS}}]},
                {"$or": [{"read_available_at": {"$exists": False}}, {"read_available_at": {"$lte": instant}}]},
            ]}, {"$set": {"read_claim": claim, "read_available_at": deadline},
                  "$inc": {"read_attempts": 1}}, return_document=ReturnDocument.AFTER)
        if not row:
            raise DocumentError("shipping_document_read_limited", status_code=429)
        acquired = True
        yield
        # Reject a dead/superseded admission; release remains claim-matched.
        current = await db[COLLECTION].find_one({"_id": document["_id"], "read_claim": claim})
        if current is None or now() >= deadline:
            raise DocumentError("shipping_document_read_lease_lost", status_code=409)
    finally:
        if acquired:
            await db[COLLECTION].update_one({"_id": document["_id"], "read_claim": claim}, {"$set": {
                "read_claim": None, "read_available_at": now() + timedelta(seconds=TOKEN_COOLDOWN_SECONDS)}})
        await db[SLOTS].update_one({"_id": slot, "claim": claim}, {"$set": {
            "claim": None, "available_at": now() + timedelta(seconds=GLOBAL_COOLDOWN_SECONDS)}})
