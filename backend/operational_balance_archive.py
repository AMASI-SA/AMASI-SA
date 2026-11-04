"""Immutable, owner-scoped pages for the reconciliation audit.

Publish pages before the owner's revision CAS. A failed CAS may leave an
unreferenced page. Readers use only the manifest of the revision they read.
The content key binds tenant, offset, order and BSON values; pages are never
updated or expired, so a concurrent reader can finish reading an old revision.
"""
import zlib
from hashlib import sha256

from bson import BSON
from bson.errors import InvalidBSON
from pymongo.errors import DuplicateKeyError
from pymongo.read_concern import ReadConcern
from pymongo.write_concern import WriteConcern

AUDIT_PAGES = "operational_balance_audit_pages_v1"
MARKER = "engine_audit_archive"
PAGE_TARGET = 512 * 1024
ISSUES = "packed_source_issues_v1"
ISSUE_MARK = {"code": "operational_source_issues_packed"}
MAX_ISSUE_BSON = 64 * 1024 * 1024


class ArchiveError(ValueError):
    pass


class CapacityError(ValueError):
    pass


def content_key(owner, start, events):
    return sha256(BSON.encode({"owner_id": owner, "start": start, "events": events})).hexdigest()


def manifest(count, pages):
    payload = {"version": 1, "count": count, "pages": pages}
    return {**payload, "sha256": sha256(BSON.encode(payload)).hexdigest()}


def validated_events(row, owner, start, ref):
    events = row.get("events") if row else None
    if (not isinstance(events, list) or not events or len(events) != ref["count"]
            or row.get("_id") != ref["id"] or row.get("owner_id") != owner
            or row.get("start") != start or content_key(owner, start, events) != ref["id"]):
        raise ArchiveError("invalid reconciliation audit page")
    return events


def unpack_issues(state):
    engine = state.get("engine", {})
    issues = engine.get("issues", [])
    marked = isinstance(issues, list) and issues[:1] == [ISSUE_MARK]
    if ISSUES not in state and not marked:
        return state
    packet = state.get(ISSUES)
    if (not marked or not isinstance(packet, dict) or not isinstance(packet.get("data"), bytes)
            or type(packet.get("size")) is not int or not 0 < packet["size"] <= MAX_ISSUE_BSON):
        raise ArchiveError("invalid diagnostic packet")
    try:
        decoder = zlib.decompressobj()
        data = decoder.decompress(packet["data"], packet["size"] + 1)
        if (len(data) != packet["size"] or not decoder.eof or decoder.unused_data
                or sha256(data).hexdigest() != packet.get("sha256")):
            raise ArchiveError("incomplete diagnostic packet")
        restored = BSON(data).decode()
        entries = restored.get("issues")
        if restored.get("owner_id") != state["owner_id"] or not isinstance(entries, list):
            raise ArchiveError("invalid diagnostic entries")
    except (zlib.error, InvalidBSON) as exc:
        raise ArchiveError("unreadable diagnostic packet") from exc
    logical = dict(state)
    del logical[ISSUES]
    logical["engine"] = {**engine, "issues": entries + issues[1:]}
    return logical


def compact_issues(state, limit):
    """Keep current diagnostics inline when possible, without dropping a single issue."""
    engine = state.get("engine", {})
    if not engine.get("issues"):
        raise CapacityError("operational state capacity")
    data = BSON.encode({"owner_id": state["owner_id"], "issues": engine["issues"]})
    if len(data) > MAX_ISSUE_BSON:
        raise CapacityError("operational diagnostic capacity")
    result = {**state, ISSUES: {"size": len(data), "sha256": sha256(data).hexdigest(),
                                "data": zlib.compress(data, level=1)},
              "engine": {**engine, "issues": [ISSUE_MARK]}}
    if len(BSON.encode(result)) > limit:
        raise CapacityError("operational state capacity")
    return result


async def unpack(db, owner, state):
    state = unpack_issues(state)
    if MARKER not in state:
        if isinstance(state.get("engine", {}).get("audit", []), list):
            return state
        raise ArchiveError("missing reconciliation audit manifest")
    meta = state[MARKER]
    tail = state.get("engine", {}).get("audit")
    if (not isinstance(meta, dict) or not isinstance(meta.get("pages"), list)
            or type(meta.get("count")) is not int or meta["count"] < 1
            or meta != manifest(meta["count"], meta["pages"])
            or not isinstance(tail, dict) or set(tail) != {"tail"}
            or not isinstance(tail["tail"], list)):
        raise ArchiveError("invalid reconciliation audit manifest")
    events = []
    for ref in meta["pages"]:
        if (not isinstance(ref, dict) or set(ref) != {"id", "count"}
                or not isinstance(ref["id"], str) or type(ref["count"]) is not int or ref["count"] <= 0):
            raise ArchiveError("invalid reconciliation audit reference")
        page = await db[AUDIT_PAGES].find_one({"_id": ref["id"], "owner_id": owner})
        events.extend(validated_events(page, owner, len(events), ref))
    if len(events) != meta["count"]:
        raise ArchiveError("incomplete reconciliation audit")
    logical = dict(state)
    del logical[MARKER]
    logical["engine"] = {**state["engine"], "audit": events + tail["tail"]}
    return logical


def batches(events):
    batch, size = [], 0
    for event in events:
        size_of_event = len(BSON.encode({"event": event})) + 16
        if batch and size + size_of_event > PAGE_TARGET:
            yield batch
            batch, size = [], 0
        batch.append(event)
        size += size_of_event
    if batch:
        yield batch


def prepare(owner, raw, before, after, limit):
    """Build a storage plan without touching Mongo, including on overflow."""
    previous = raw.get(MARKER)
    if not previous and len(BSON.encode(after)) <= limit:
        return after, []
    count = previous["count"] if previous else 0
    pages = list(previous["pages"]) if previous else []
    engine = after.get("engine", {})
    audit = engine.get("audit", [])
    if count and before["engine"]["audit"][:count] != audit[:count]:
        raise ArchiveError("immutable reconciliation audit changed")
    tail = audit[count:]
    def head(remaining):
        return {**after, MARKER: manifest(count, pages), "engine": {**engine, "audit": {"tail": remaining}}}

    if previous:
        compact = head(tail)
        if len(BSON.encode(compact)) <= limit:
            return compact, []
    pending = []
    for events in batches(tail):
        key = content_key(owner, count, events)
        doc = {"_id": key, "owner_id": owner, "start": count, "events": events}
        if len(BSON.encode(doc)) > limit:
            break
        pending.append(doc)
        pages.append({"id": key, "count": len(events)})
        count += len(events)
    if count != len(audit):
        raise CapacityError("operational state capacity")
    compact = head([]) if pages else after
    if len(BSON.encode(compact)) > limit:
        compact = compact_issues(compact, limit)
    return compact, pending


async def stage_pages(db, pages):
    if not pages:
        return
    # A failover between staging and publishing the head must not lose a page.
    collection = db[AUDIT_PAGES].with_options(write_concern=WriteConcern("majority"),
                                              read_concern=ReadConcern("majority"))
    for doc in pages:
        try:
            await collection.insert_one(doc)
        except DuplicateKeyError:
            # A competing worker or an interrupted attempt can stage the same
            # immutable prefix. Never publish a reference to different bytes.
            owner, key = doc["owner_id"], doc["_id"]
            existing = await collection.find_one({"_id": key, "owner_id": owner})
            validated_events(existing, owner, doc["start"], {"id": key, "count": len(doc["events"])})
