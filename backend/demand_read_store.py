"""Disposable, actor-scoped read projections; never a financial authority.

Callers own fresh authorization before demand, before serving and in the worker.
This module only stores immutable derived generations and bounded visible demand.
No canonical collection or provider is written here.
"""
from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import math
import re

from bson import BSON
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError


CODE_VERSION = "demand-read-v1"
JOBS = "mezan_demand_read_jobs_v1"
ROWS = "mezan_demand_read_rows_v1"
FAMILIES = frozenset({
    "operational_home", "operational_ledger", "operational_register",
    "operational_statement", "operational_history", "operational_directory",
    "operational_selection", "operational_options", "operational_position",
    "operational_resource",
    "operational_report",
    "dashboard_card", "assembly_board",
})
CONSUMER_SECONDS = 30
MAX_CONSUMERS = 16
MAX_OWNER_PENDING = 8
MAX_SPEC_BYTES = 16384
MAX_HEADER_BYTES = 12 * 1024 * 1024
MAX_ROW_BYTES = 512 * 1024
MAX_RESULT_BYTES = 64 * 1024 * 1024
MAX_RESULT_ROWS = 100000
MAX_PAGE_SIZE = 300
RETENTION_SECONDS = 24 * 60 * 60
_INDEXED = OrderedDict()
_KEY = re.compile(r"[a-f0-9]{64}\Z")
_CONSUMER = re.compile(r"[A-Za-z0-9_-]{8,128}\Z")
METRIC_PREFIX = "mezan_demand_read_metrics "
_METRIC_NUMBERS = frozenset({"pid", "wall_ms", "build_ms", "materialize_ms", "check_ms", "check_calls",
                            "authorize_ms", "process_cpu_ms", "peak_rss_kib", "queue_ms",
                            "mongo_driver_command_p50_ms", "mongo_driver_command_p95_ms", "mongo_driver_command_p99_ms",
                            "mongo_driver_checkout_p95_ms", "mongo_driver_command_timeouts", "mongo_driver_checkout_timeouts"})


class InvalidReadSpec(ValueError):
    pass


class SnapshotChanged(ValueError):
    pass


class ProjectionUnavailable(ValueError):
    pass


def safe_metrics(value):
    """A fixed numeric vocabulary, never arbitrary child stdout or exceptions."""
    if (not isinstance(value, dict) or not set(value) <= _METRIC_NUMBERS | {"family", "outcome"}
            or not isinstance(value.get("family"), str) or value["family"] not in FAMILIES
            or not isinstance(value.get("outcome"), str)
            or value["outcome"] not in {"ready", "failed", "paused", "cancelled", "claim_lost"}):
        return None
    result = {"family": value["family"], "outcome": value["outcome"]}
    for key in value.keys() & _METRIC_NUMBERS:
        number = value[key]
        if type(number) not in (int, float) or not 0 <= number <= 10 ** 12 or not math.isfinite(number):
            return None
        result[key] = round(number, 3) if isinstance(number, float) else number
    return result


async def read_metric_stream(stream, accept):
    """Drain all output with at most 2KiB retained; accept only fixed records."""
    pending = b""
    overlong = False
    while chunk := await stream.read(4096):
        fragments = chunk.split(b"\n")
        for index, part in enumerate(fragments):
            if not overlong:
                pending += part
                if len(pending) > 2048:
                    pending, overlong = b"", True
            if index == len(fragments) - 1:
                continue
            if not overlong and pending.startswith(METRIC_PREFIX.encode()):
                try:
                    record = safe_metrics(json.loads(pending[len(METRIC_PREFIX):]))
                    if record is not None:
                        accept(record)
                except Exception:
                    pass
            pending, overlong = b"", False


def normalize_spec(spec):
    if not isinstance(spec, dict):
        raise InvalidReadSpec("read_spec_invalid")
    try:
        encoded = json.dumps(spec, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                             allow_nan=False).encode("utf-8")
    except (ValueError, TypeError, OverflowError, RecursionError):
        raise InvalidReadSpec("read_spec_invalid") from None
    if len(encoded) > MAX_SPEC_BYTES:
        raise InvalidReadSpec("read_spec_too_large")
    value = json.loads(encoded)
    if not isinstance(value.get("family"), str) or value["family"] not in FAMILIES:
        raise InvalidReadSpec("read_family_invalid")
    for field in ("owner_id", "actor_id", "client_kind", "permission_fingerprint", "projection_version"):
        item = value.get(field)
        if not isinstance(item, str) or not item.strip() or len(item) > 512:
            raise InvalidReadSpec("read_scope_invalid")
    request = value.get("request")
    if (not isinstance(request, dict) or request.get("method") != "GET"
            or not isinstance(request.get("path"), str) or not request["path"].startswith("/api/")
            or not isinstance(request.get("query", {}), dict) or "source_revision" not in value):
        raise InvalidReadSpec("read_request_invalid")
    seconds = value.get("cache_seconds", 120)
    if type(seconds) is not int or not 5 <= seconds <= 18000:
        raise InvalidReadSpec("read_cache_duration_invalid")
    return value


def projection_key(spec):
    normalized = normalize_spec(spec)
    encoded = json.dumps([CODE_VERSION, normalized], sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False, allow_nan=False).encode("utf-8")
    return sha256(encoded).hexdigest()


def _scope(spec):
    return {"_id": projection_key(spec), "owner_id": spec["owner_id"], "actor_id": spec["actor_id"]}


def _consumer(value):
    if not isinstance(value, str) or not _CONSUMER.fullmatch(value):
        raise InvalidReadSpec("read_consumer_invalid")
    return sha256(value.encode()).hexdigest()


def active_consumers():
    return {"$filter": {"input": {"$ifNull": ["$consumers", []]}, "as": "consumer",
                        "cond": {"$gt": ["$$consumer.until", "$$NOW"]}}}


def has_demand():
    return {"$gt": [{"$size": active_consumers()}, 0]}


async def ensure_indexes(db):
    if _INDEXED.get(id(db)) is db:
        return
    # Each index corresponds to one queue, page, focus or TTL query below.
    await db[JOBS].create_index([("owner_id", 1), ("queue_slot", 1)], unique=True,
        partialFilterExpression={"active_slot": True}, name="demand_read_owner_slots")
    await db[JOBS].create_index([("code_version", 1), ("status", 1), ("requested_at", 1)],
                               name="demand_read_pending")
    await db[JOBS].create_index("retire_at", expireAfterSeconds=0, name="demand_read_job_retention")
    await db[ROWS].create_index([("projection_id", 1), ("generation", 1), ("ordinal", 1)],
                               unique=True, name="demand_read_page")
    await db[ROWS].create_index([("projection_id", 1), ("generation", 1), ("row_id", 1), ("ordinal", 1)],
                               name="demand_read_focus")
    await db[ROWS].create_index("retire_at", expireAfterSeconds=0, name="demand_read_row_retention")
    _INDEXED[id(db)] = db
    _INDEXED.move_to_end(id(db))
    while len(_INDEXED) > 8:
        _INDEXED.popitem(last=False)


def public_status(row):
    state = row.get("status")
    public = "ready" if state == "ready" else "failed" if state == "failed" else "preparing"
    result = {"status": public, "key": row["_id"], "retry_after_ms": 2000 if public == "preparing" else None}
    if public == "failed":
        result["error_code"] = row.get("error_code") or "read_preparation_failed"
    return result


async def demand(db, spec, consumer_id, *, refresh=False, retry=False):
    spec = normalize_spec(spec)
    scope, consumer = _scope(spec), _consumer(consumer_id)
    await ensure_indexes(db)
    if not refresh:
        ready = await db[JOBS].find_one({**scope, "status": "ready",
            "$expr": {"$gt": ["$ready_until", "$$NOW"]}}, {"status": 1})
        if ready is not None:
            return public_status(ready)
    # Slots expire logically when demand goes away, without computing anything.
    await db[JOBS].update_many({"owner_id": spec["owner_id"],
        "$or": [{"status": "queued"}, {"status": "running", "$expr": {"$lte": ["$lease_until", "$$NOW"]}}],
        "$expr": {"$not": [has_demand()]}}, {"$set": {"status": "paused", "active_slot": False}})
    keep = {"$filter": {"input": active_consumers(), "as": "consumer",
                        "cond": {"$ne": ["$$consumer.id", consumer]}}}
    consumers = {"$let": {"vars": {"live": keep}, "in": {"$cond": [
        {"$lt": [{"$size": "$$live"}, MAX_CONSUMERS]},
        {"$concatArrays": ["$$live", [{"id": consumer, "until": {"$add": ["$$NOW", CONSUMER_SECONDS * 1000]}}]]},
        "$$live",
    ]}}}
    expired = {"$and": [{"$eq": ["$status", "ready"]},
                          {"$lte": [{"$ifNull": ["$ready_until", "$$NOW"]}, "$$NOW"]}]}
    requeue = {"$or": [
        {"$in": [{"$ifNull": ["$status", "missing"]}, ["missing", "paused", "cancelled"]]},
        expired, {"$and": [bool(refresh), {"$eq": ["$status", "ready"]}]},
        {"$and": [bool(retry), {"$eq": ["$status", "failed"]}]},
    ]}
    for slot in range(MAX_OWNER_PENDING):
        try:
            row = await db[JOBS].find_one_and_update(scope, [
                {"$set": {"_requeue": requeue, "consumers": consumers,
                    "owner_id": {"$literal": spec["owner_id"]}, "actor_id": {"$literal": spec["actor_id"]},
                    "family": {"$literal": spec["family"]}, "spec": {"$literal": spec},
                    "code_version": CODE_VERSION, "retire_at": {"$add": ["$$NOW", RETENTION_SECONDS * 1000]}}},
                {"$set": {"status": {"$cond": ["$_requeue", "queued", "$status"]},
                    "active_slot": {"$cond": ["$_requeue", True, {"$ifNull": ["$active_slot", False]}]},
                    "queue_slot": {"$cond": ["$_requeue", slot, {"$ifNull": ["$queue_slot", slot]}]},
                    "attempts": {"$cond": ["$_requeue", 0, {"$ifNull": ["$attempts", 0]}]},
                    "requested_at": {"$cond": ["$_requeue", "$$NOW", "$requested_at"]},
                    "retry_at": {"$cond": ["$_requeue", "$$NOW", {"$ifNull": ["$retry_at", "$$NOW"]}]},
                    "error_code": {"$cond": ["$_requeue", None, "$error_code"]}}},
                {"$unset": "_requeue"},
            ], upsert=True, return_document=ReturnDocument.AFTER)
            return public_status(row)
        except DuplicateKeyError:
            # Another exact request may have won _id, or this owner's bounded
            # slot is occupied. Re-read/retry under the same unique constraints.
            continue
    return {"status": "failed", "key": scope["_id"], "retry_after_ms": None,
            "error_code": "read_queue_busy"}


async def release_by_key(db, key, actor_id, consumer_id, *, owner_id=None):
    if not isinstance(key, str) or not _KEY.fullmatch(key):
        raise InvalidReadSpec("read_key_invalid")
    consumer = _consumer(consumer_id)
    if not isinstance(actor_id, str) or not actor_id:
        raise InvalidReadSpec("read_scope_invalid")
    query = {"_id": key, "actor_id": actor_id}
    if owner_id is not None:
        query["owner_id"] = owner_id
    await db[JOBS].update_one(query, [{"$set": {"consumers": {"$filter": {
        "input": active_consumers(), "as": "consumer", "cond": {"$ne": ["$$consumer.id", consumer]}}}}}])
    # A new consumer concurrent with release prevents this conditional pause.
    await db[JOBS].update_one({**query, "status": "queued", "$expr": {"$not": [has_demand()]}},
                             {"$set": {"status": "paused", "active_slot": False}})


async def release(db, spec, consumer_id):
    spec = normalize_spec(spec)
    await release_by_key(db, projection_key(spec), spec["actor_id"], consumer_id, owner_id=spec["owner_id"])


async def _ready_manifest(db, spec, snapshot=None):
    spec = normalize_spec(spec)
    row = await db[JOBS].find_one({**_scope(spec), "status": "ready",
        "$expr": {"$gt": ["$ready_until", "$$NOW"]}},
        {"header": 1, "generation": 1, "total": 1, "paged": 1, "generated_at": 1})
    if row is None:
        return None
    if snapshot is not None and snapshot != row["generation"]:
        raise SnapshotChanged("read_snapshot_changed")
    return row


async def read_ready(db, spec, *, offset=0, limit=None, snapshot=None):
    if type(offset) is not int or offset < 0 or (limit is not None and (type(limit) is not int or not 1 <= limit <= MAX_PAGE_SIZE)):
        raise InvalidReadSpec("read_page_invalid")
    row = await _ready_manifest(db, spec, snapshot)
    if row is None:
        return None
    items = None
    if row["paged"]:
        size = min(MAX_PAGE_SIZE if limit is None else limit, max(0, row["total"] - offset))
        items = []
        if size:
            selected = await db[ROWS].find({
                "projection_id": row["_id"], "generation": row["generation"],
                "owner_id": spec["owner_id"], "actor_id": spec["actor_id"],
                "ordinal": {"$gte": offset, "$lt": offset + size},
            }, {"_id": 0, "ordinal": 1, "value": 1}).sort("ordinal", 1).limit(size).to_list(size)
            if [item["ordinal"] for item in selected] != list(range(offset, offset + size)):
                raise ProjectionUnavailable("read_projection_incomplete")
            items = [item["value"] for item in selected]
    return {"header": row["header"], "items": items, "total": row["total"],
            "snapshot": row["generation"], "generated_at": row["generated_at"]}


async def find_row_ordinal(db, spec, identity, *, snapshot=None):
    if not isinstance(identity, str) or not identity or len(identity) > 250:
        raise InvalidReadSpec("read_focus_invalid")
    row = await _ready_manifest(db, spec, snapshot)
    if row is None or not row["paged"]:
        return None
    found = await db[ROWS].find_one({"projection_id": row["_id"], "generation": row["generation"],
        "owner_id": spec["owner_id"], "actor_id": spec["actor_id"], "row_id": identity},
        {"ordinal": 1}, sort=[("ordinal", 1)])
    return found["ordinal"] if found is not None else None


async def materialize(db, claim, result, checkpoint):
    """Write an invisible immutable generation; caller fences its publication."""
    if (not isinstance(result, dict) or set(result) != {"header", "rows"}
            or not isinstance(result["header"], dict)
            or result["rows"] is not None and not isinstance(result["rows"], list)):
        raise ProjectionUnavailable("read_projection_invalid")
    header = deepcopy(result["header"])
    rows = result["rows"]
    header_size = len(BSON.encode({"value": header}))
    if header_size > MAX_HEADER_BYTES or rows is not None and len(rows) > MAX_RESULT_ROWS:
        raise ProjectionUnavailable("read_projection_too_large")
    total_bytes = header_size
    batch, batch_bytes = [], 0
    # Ready expiry is at most five hours. Orphan and retired generations live
    # longer so a reader that already has the manifest can finish safely.
    retire_at = claim["started_at"] + timedelta(seconds=RETENTION_SECONDS)
    for ordinal, value in enumerate(rows or []):
        row_id = value.get("id") if isinstance(value, dict) else None
        document = {"_id": f"{claim['_id']}:{claim['generation']}:{ordinal}",
            "projection_id": claim["_id"], "generation": claim["generation"],
            "owner_id": claim["owner_id"], "actor_id": claim["actor_id"],
            "ordinal": ordinal, "row_id": row_id if isinstance(row_id, str) else None,
            "value": value, "retire_at": retire_at}
        size = len(BSON.encode(document))
        total_bytes += size
        if size > MAX_ROW_BYTES or total_bytes > MAX_RESULT_BYTES:
            raise ProjectionUnavailable("read_projection_too_large")
        if batch and (len(batch) >= 128 or batch_bytes + size > 1024 * 1024):
            await checkpoint()
            await db[ROWS].insert_many(batch, ordered=True)
            batch, batch_bytes = [], 0
        batch.append(document)
        batch_bytes += size
    if batch:
        await checkpoint()
        await db[ROWS].insert_many(batch, ordered=True)
    await checkpoint()
    return {"header": header, "paged": rows is not None, "total": len(rows) if rows is not None else 0}
