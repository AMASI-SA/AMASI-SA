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
SNAPSHOT = "packed_financial_snapshot_v1"
SNAPSHOT_MARK = {"stored_in_financial_snapshot_v1": True}
SNAPSHOT_FIELDS = frozenset({"facts", "obligations", "provider_fee_inputs", "provider_reports",
    "cod_reports", "order_versions", "profit_snapshot", "customer_tender_coverage"})
MAX_SNAPSHOT_BSON = 64 * 1024 * 1024
VISIBLE_AUDIT_GATE_PAGES = 8


class ArchiveError(ValueError):
    pass


class CapacityError(ValueError):
    pass


def _checkpoint():
    # Import lazily: archive is also used while the store module initializes.
    from operational_demand_refresh import calculation_checkpoint
    calculation_checkpoint()


async def _prepare_read(function, *args):
    """Keep bounded BSON work off the event loop and join it on cancellation."""
    from operational_demand_refresh import demand_context, run_calculation
    from operational_financial_activity_runtime import prepare_in_thread
    if demand_context() is not None:
        return await run_calculation(function, *args)
    return await prepare_in_thread(function, *args)


def content_key(owner, start, events):
    _checkpoint()
    return sha256(BSON.encode({"owner_id": owner, "start": start, "events": events})).hexdigest()


def manifest(count, pages):
    payload = {"version": 1, "count": count, "pages": pages}
    return {**payload, "sha256": sha256(BSON.encode(payload)).hexdigest()}


def validated_events(row, owner, start, ref):
    _checkpoint()
    events = row.get("events") if row else None
    if (not isinstance(events, list) or not events or len(events) != ref["count"]
            or row.get("_id") != ref["id"] or row.get("owner_id") != owner
            or row.get("start") != start or content_key(owner, start, events) != ref["id"]):
        raise ArchiveError("invalid reconciliation audit page")
    _checkpoint()
    return events


def unpack_issues(state):
    _checkpoint()
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
        _checkpoint()
        if (len(data) != packet["size"] or not decoder.eof or decoder.unused_data
                or sha256(data).hexdigest() != packet.get("sha256")):
            raise ArchiveError("incomplete diagnostic packet")
        restored = BSON(data).decode()
        _checkpoint()
        entries = restored.get("issues")
        if restored.get("owner_id") != state["owner_id"] or not isinstance(entries, list):
            raise ArchiveError("invalid diagnostic entries")
    except (zlib.error, InvalidBSON) as exc:
        raise ArchiveError("unreadable diagnostic packet") from exc
    logical = dict(state)
    del logical[ISSUES]
    logical["engine"] = {**engine, "issues": entries + issues[1:]}
    return logical


def compact_issues(state, limit, *, allow_oversize=False):
    """Keep current diagnostics inline when possible, without dropping a single issue."""
    _checkpoint()
    engine = state.get("engine", {})
    if not engine.get("issues"):
        raise CapacityError("operational state capacity")
    data = BSON.encode({"owner_id": state["owner_id"], "issues": engine["issues"]})
    if len(data) > MAX_ISSUE_BSON:
        raise CapacityError("operational diagnostic capacity")
    result = {**state, ISSUES: {"size": len(data), "sha256": sha256(data).hexdigest(),
                                "data": zlib.compress(data, level=1)},
              "engine": {**engine, "issues": [ISSUE_MARK]}}
    _checkpoint()
    if not allow_oversize and len(BSON.encode(result)) > limit:
        raise CapacityError("operational state capacity")
    return result


def compact_financial_snapshot(state, limit):
    """Lossless storage encoding under the same owner revision and atomic CAS.

    Openings, movements, idempotency requests and salary-history guard indexes
    stay inline. Large current calculation maps are encoded without changing a
    single amount, proof, key, type or historical audit page.
    """
    _checkpoint()
    engine = state.get("engine", {})
    selected = {key: engine[key] for key in sorted(SNAPSHOT_FIELDS) if key in engine}
    if not selected or any(value == SNAPSHOT_MARK for value in selected.values()):
        raise CapacityError("operational state capacity")
    data = BSON.encode({"owner_id": state["owner_id"], "fields": selected})
    if len(data) > MAX_SNAPSHOT_BSON:
        raise CapacityError("operational financial snapshot capacity")
    packet = {"version": 1, "fields": list(selected), "size": len(data),
              "sha256": sha256(data).hexdigest(), "data": zlib.compress(data, level=1)}
    _checkpoint()
    result = {**state, SNAPSHOT: packet,
              "engine": {**engine, **{key: dict(SNAPSHOT_MARK) for key in selected}}}
    if len(BSON.encode(result)) > limit:
        raise CapacityError("operational state capacity")
    return result


def unpack_financial_snapshot(state, *, selected=None):
    """Validate the packet before exposing any calculation data.

    Explicit narrow projections may select facts/obligations without reading
    audit history. They must include owner_id and the packet in their query.
    Full state reads require every marker, so a missing field cannot become an
    empty ledger. This function never mutates the supplied stored document.
    """
    _checkpoint()
    engine = state.get("engine", {})
    marked = {key for key in SNAPSHOT_FIELDS if engine.get(key) == SNAPSHOT_MARK}
    if SNAPSHOT not in state:
        if marked:
            raise ArchiveError("missing financial snapshot packet")
        return state
    packet = state[SNAPSHOT]
    fields = packet.get("fields") if isinstance(packet, dict) else None
    if (not isinstance(packet, dict) or type(packet.get("version")) is not int or packet["version"] != 1
            or not isinstance(fields, list) or not fields or any(not isinstance(key, str) for key in fields)
            or fields != sorted(set(fields)) or not set(fields) <= SNAPSHOT_FIELDS
            or type(packet.get("size")) is not int or not 0 < packet["size"] <= MAX_SNAPSHOT_BSON
            or not isinstance(packet.get("data"), bytes) or not state.get("owner_id")):
        raise ArchiveError("invalid financial snapshot packet")
    wanted = set(fields) if selected is None else set(fields).intersection(selected)
    if not marked <= set(fields) or not wanted <= marked or selected is None and marked != set(fields):
        raise ArchiveError("financial snapshot marker mismatch")
    try:
        decoder = zlib.decompressobj()
        data = decoder.decompress(packet["data"], packet["size"] + 1)
        _checkpoint()
        if (len(data) != packet["size"] or not decoder.eof or decoder.unused_data
                or sha256(data).hexdigest() != packet.get("sha256")):
            raise ArchiveError("incomplete financial snapshot packet")
        restored = BSON(data).decode()
        _checkpoint()
        values = restored.get("fields")
        if (restored.get("owner_id") != state["owner_id"] or not isinstance(values, dict)
                or sorted(values) != fields or any(value == SNAPSHOT_MARK for value in values.values())):
            raise ArchiveError("financial snapshot scope mismatch")
    except (zlib.error, InvalidBSON, TypeError) as exc:
        raise ArchiveError("unreadable financial snapshot packet") from exc
    logical = {key: value for key, value in state.items() if key != SNAPSHOT}
    logical["engine"] = {**engine, **{key: values[key] for key in wanted}}
    return logical


def _unpack_packets(state):
    return unpack_issues(unpack_financial_snapshot(state))


def _audit_metadata(state):
    _checkpoint()
    meta = state[MARKER]
    tail = state.get("engine", {}).get("audit")
    if (not isinstance(meta, dict) or not isinstance(meta.get("pages"), list)
            or type(meta.get("count")) is not int or meta["count"] < 1
            or meta != manifest(meta["count"], meta["pages"])
            or not isinstance(tail, dict) or set(tail) != {"tail"}
            or not isinstance(tail["tail"], list)):
        raise ArchiveError("invalid reconciliation audit manifest")
    _checkpoint()
    return meta, tail


def _restore_audit(state, events, tail):
    _checkpoint()
    logical = {key: value for key, value in state.items() if key != MARKER}
    logical["engine"] = {**state["engine"], "audit": events + tail["tail"]}
    _checkpoint()
    return logical


async def unpack(db, owner, state):
    _checkpoint()
    if state.get("owner_id") != owner:
        raise ArchiveError("financial snapshot owner mismatch")
    # Ordinary small inline states need no thread. Packed states still undergo
    # the exact owner, hash, length and marker validation before being exposed.
    if SNAPSHOT in state or ISSUES in state:
        state = await _prepare_read(_unpack_packets, state)
    else:
        state = _unpack_packets(state)
    if MARKER not in state:
        if isinstance(state.get("engine", {}).get("audit", []), list):
            return state
        raise ArchiveError("missing reconciliation audit manifest")
    meta, tail = await _prepare_read(_audit_metadata, state)
    from operational_demand_refresh import checkpoint, demand_context
    from operational_financial_activity_runtime import prepare_in_thread
    context = demand_context()
    visible = context is not None and context.db is db and context.owner == owner
    # Only this exact request/owner may group fresh authorization gates. Keep
    # every page read sequential and validate it before reading the next one.
    # The joined worker still observes cancellation for each page; ordinary
    # readers and other database/session capabilities retain their old gates.
    prepare_page = prepare_in_thread if visible else _prepare_read
    events = []
    refs = meta["pages"]
    for index, ref in enumerate(refs):
        if visible and index % VISIBLE_AUDIT_GATE_PAGES == 0:
            await checkpoint()
        _checkpoint()
        if (not isinstance(ref, dict) or set(ref) != {"id", "count"}
                or not isinstance(ref["id"], str) or type(ref["count"]) is not int or ref["count"] <= 0):
            raise ArchiveError("invalid reconciliation audit reference")
        page = await db[AUDIT_PAGES].find_one({"_id": ref["id"], "owner_id": owner})
        events.extend(await prepare_page(validated_events, page, owner, len(events), ref))
        if visible and ((index + 1) % VISIBLE_AUDIT_GATE_PAGES == 0 or index + 1 == len(refs)):
            await checkpoint()
    if len(events) != meta["count"]:
        raise ArchiveError("incomplete reconciliation audit")
    return await _prepare_read(_restore_audit, state, events, tail)


def batches(events):
    batch, size = [], 0
    for event in events:
        _checkpoint()
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
    _checkpoint()
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
        if compact.get("engine", {}).get("issues"):
            compact = compact_issues(compact, limit, allow_oversize=True)
        if len(BSON.encode(compact)) > limit:
            compact = compact_financial_snapshot(compact, limit)
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
