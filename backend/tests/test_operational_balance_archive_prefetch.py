"""Four owned raw pages may overlap; every financial validation stays ordered."""
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import gc
import os
from threading import Event
import time
from types import SimpleNamespace
from urllib.parse import urlsplit
import weakref

from bson import BSON, Binary, Code, Decimal128, Int64, Regex, Timestamp
from bson.codec_options import CodecOptions
from bson.raw_bson import RawBSONDocument
from fastapi import HTTPException
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
import pymongo
import pytest

import operational_audit_prefetch as prefetch
import operational_balance_archive as archive
import operational_balance_store as store
import operational_demand_refresh as demand
from demand_read_mongo_fixture import read_db, read_mongo_uri as demand_read_mongo_uri
from test_operational_balance_archive_visible_batches import (
    archived_fixture, assert_read_only, load_visible,
)
from test_operational_balance_demand_refresh import Request
from test_operational_read_runtime import MongoReadWire


@pytest.fixture(scope="session")
def read_mongo_uri(tmp_path_factory, request):
    # The Stock gate already owns a loopback replica under these test names.
    # Never fall back to application credentials or change the generic fixture.
    if not (os.environ.get("DEMAND_READ_TEST_MONGO_URI") or os.environ.get("DEMAND_READ_TEST_MONGOD")):
        supplied = {os.environ[name] for name in ("OPERATIONAL_TEST_MONGO_URI", "MZ2_TEST_MONGO_URI")
                    if os.environ.get(name)}
        if supplied:
            assert len(supplied) == 1, "Conflicting isolated Mongo test capabilities"
            uri = supplied.pop()
            parsed = urlsplit(uri)
            assert parsed.scheme == "mongodb" and parsed.hostname == "127.0.0.1" and parsed.port
            assert parsed.username is None and parsed.password is None and parsed.path in ("", "/")
            yield uri
            return
    yield from demand_read_mongo_uri.__wrapped__(tmp_path_factory, request)


class RawPages:
    """Awaitable Mongo-like I/O: cancellation of its waiter cannot stop it."""
    def __init__(self, db):
        self.db = db
        self.active = self.peak = self.started = self.finished = 0
        self.gate = None
        self.entered = asyncio.Event()

    async def find_one(self, query):
        self.active += 1
        self.peak = max(self.peak, self.active)
        self.started += 1
        self.entered.set()
        try:
            if self.gate is not None:
                await self.gate.wait()
            result = await self.db[archive.AUDIT_PAGES].find_one(query)
            return RawBSONDocument(BSON.encode(result)) if result is not None else None
        finally:
            self.active -= 1
            self.finished += 1


def activate_fake(monkeypatch, db):
    raw = RawPages(db)

    def capability(database, owner):
        context = demand.demand_context()
        assert database is db and owner == "owner"
        assert context.db is db and context.owner == owner and context.leased
        return raw, CodecOptions()

    monkeypatch.setattr(prefetch, "_collection", capability)
    return raw


@pytest.mark.asyncio
@pytest.mark.parametrize("pages", [1, 4, 8, 9, 17])
@pytest.mark.parametrize("packed", [False, True])
async def test_complete_bson_history_and_guard_cadence_match_serial(monkeypatch, pages, packed):
    db, expected = archived_fixture(pages, packed=packed)
    before = deepcopy(db.data)
    raw = activate_fake(monkeypatch, db)
    result, stats = await load_visible(db, monkeypatch)
    assert result == expected
    assert raw.started == raw.finished == db.page_reads == pages
    assert raw.peak <= prefetch.PREFETCH_PAGES == 4
    assert max(b-a for a, b in zip(stats["gates"], stats["gates"][1:])) <= 8
    assert stats["gates"][-1] == pages and stats["operation_calls"] == 1
    assert_read_only(db, before)


@pytest.mark.asyncio
async def test_validated_raw_buffers_are_released_before_the_next_validation(monkeypatch):
    db, expected = archived_fixture(9, packed=True)
    before = deepcopy(db.data)
    raw = activate_fake(monkeypatch, db)
    buffers, validated = {}, []
    read, validate = prefetch._read_raw, prefetch._validated_raw

    class TrackedRaw(bytearray):
        """Weak-referenceable BSON bytes for observing retained task results."""

    async def tracked_read(collection, owner, ref):
        data = TrackedRaw(await read(collection, owner, ref))
        buffers[ref["id"]] = weakref.ref(data)
        return data

    def observe_validation(data, codec, owner, start, ref):
        gc.collect()
        retained = {key for key, value in buffers.items() if value() is not None}
        assert retained.isdisjoint(validated), "a completed page retained its raw BSON buffer"
        assert len(retained) <= prefetch.PREFETCH_PAGES
        result = validate(data, codec, owner, start, ref)
        validated.append(ref["id"])
        return result

    monkeypatch.setattr(prefetch, "_read_raw", tracked_read)
    monkeypatch.setattr(prefetch, "_validated_raw", observe_validation)
    result, stats = await load_visible(db, monkeypatch)
    gc.collect()
    assert result == expected and stats["operation_calls"] == 1
    assert len(validated) == raw.started == raw.finished == 9
    assert all(value() is None for value in buffers.values())
    assert_read_only(db, before)


@pytest.mark.asyncio
@pytest.mark.parametrize("index", [0, 8])
@pytest.mark.parametrize("damage", ["missing", "owner", "id", "start", "count", "content", "ref_count"])
async def test_corrupt_page_joins_only_its_authorized_batch_and_never_applies(monkeypatch, index, damage):
    db, _ = archived_fixture(12)
    stored = db.data[store.STATES]["owner"]
    refs = stored[archive.MARKER]["pages"]
    page = db.data[archive.AUDIT_PAGES][refs[index]["id"]]
    if damage == "missing":
        del db.data[archive.AUDIT_PAGES][refs[index]["id"]]
    elif damage == "owner":
        page["owner_id"] = "other"
    elif damage == "id":
        page["_id"] = "other"
    elif damage == "start":
        page["start"] += 1
    elif damage == "count":
        page["events"] = []
    elif damage == "content":
        page["events"][0]["proof"] = b"changed"
    else:
        refs[index]["count"] += 1
        stored[archive.MARKER] = archive.manifest(13, refs)
    before = deepcopy(db.data)
    raw = activate_fake(monkeypatch, db)
    stats = {"gates": [], "operation_calls": 0}
    with pytest.raises(HTTPException) as caught:
        await load_visible(db, monkeypatch, stats=stats)
    assert caught.value.status_code == 503
    assert caught.value.detail["code"] == "operational_audit_archive_invalid"
    assert index + 1 <= raw.started <= index + 4
    assert raw.started == raw.finished and raw.active == 0
    assert stats["operation_calls"] == 0
    assert_read_only(db, before)


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", ["duplicate", "total", "ref_type", "manifest_hash"])
async def test_complete_manifest_remains_required_before_operation(monkeypatch, damage):
    db, _ = archived_fixture(9)
    state = db.data[store.STATES]["owner"]
    refs = state[archive.MARKER]["pages"]
    if damage == "duplicate":
        refs[1] = deepcopy(refs[0])
    elif damage == "ref_type":
        refs[0]["id"] = {"$ne": None}
    state[archive.MARKER] = archive.manifest(10 if damage == "total" else 9, refs)
    if damage == "manifest_hash":
        state[archive.MARKER]["sha256"] = "0" * 64
    before = deepcopy(db.data)
    raw = activate_fake(monkeypatch, db)
    stats = {"gates": [], "operation_calls": 0}
    with pytest.raises(HTTPException) as caught:
        await load_visible(db, monkeypatch, stats=stats)
    assert caught.value.status_code == 503 and stats["operation_calls"] == 0
    assert raw.finished == raw.started and raw.active == 0
    if damage in {"ref_type", "manifest_hash"}:
        assert raw.started == 0
    assert_read_only(db, before)


@pytest.mark.asyncio
@pytest.mark.parametrize("cause", ["revoked", "lease_lost"])
async def test_fresh_authorization_and_lease_stop_next_batch(monkeypatch, cause):
    db, _ = archived_fixture(16)
    before = deepcopy(db.data)
    raw = activate_fake(monkeypatch, db)
    stats = {"gates": [], "operation_calls": 0}

    async def revoke(database, _stats):
        if database.page_reads == 8:
            if cause == "revoked":
                raise HTTPException(403, detail={"code": "revoked"})
            database.data[demand.LEASES]["owner"]["token"] = "successor"

    with pytest.raises(HTTPException) as caught:
        await load_visible(db, monkeypatch, verify_hook=revoke, stats=stats)
    assert caught.value.status_code == (403 if cause == "revoked" else 409)
    assert raw.started == raw.finished == 8 and stats["operation_calls"] == 0
    assert not [write for write in db.writes if write[0] != demand.LEASES]
    assert {key: value for key, value in db.data.items() if key != demand.LEASES} == before
    assert asyncio.all_tasks() == {asyncio.current_task()}


@pytest.mark.asyncio
@pytest.mark.parametrize("cause", ["disconnect", "deadline", "repeated_cancel"])
async def test_cancel_joins_active_io_and_does_not_launch_next_batch(monkeypatch, cause):
    db, _ = archived_fixture(12)
    before = deepcopy(db.data)
    raw = activate_fake(monkeypatch, db)
    raw.gate = asyncio.Event()
    request = Request()
    stats = {"gates": [], "operation_calls": 0}
    if cause == "deadline":
        monkeypatch.setattr(demand, "REQUEST_SECONDS", .05)
    task = asyncio.create_task(load_visible(db, monkeypatch, request=request, stats=stats))
    try:
        await asyncio.wait_for(raw.entered.wait(), 2)
        if cause == "disconnect":
            request.disconnect()
        elif cause == "repeated_cancel":
            task.cancel()
        else:
            await asyncio.sleep(.08)
        for _ in range(6):
            await asyncio.sleep(0)
        if cause == "repeated_cancel":
            task.cancel()
        assert not task.done() and raw.active and raw.finished == 0
        raw.gate.set()
        with pytest.raises(asyncio.CancelledError if cause == "repeated_cancel" else HTTPException) as caught:
            await asyncio.wait_for(task, 2)
        if cause != "repeated_cancel":
            assert caught.value.status_code == (499 if cause == "disconnect" else 408)
        assert 1 <= raw.started <= 4 and raw.started == raw.finished
        assert raw.active == 0 and stats["operation_calls"] == 0
        assert_read_only(db, before)
    finally:
        raw.gate.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_cancel_joins_cpu_validator_after_all_prefetched_io(monkeypatch):
    db, _ = archived_fixture(12)
    before = deepcopy(db.data)
    raw = activate_fake(monkeypatch, db)
    started, stopped = Event(), Event()
    request = Request()
    stats = {"gates": [], "operation_calls": 0}

    def blocked(*args):
        started.set()
        try:
            while True:
                demand.calculation_checkpoint()
        finally:
            stopped.set()

    monkeypatch.setattr(archive, "validated_events", blocked)
    task = asyncio.create_task(load_visible(db, monkeypatch, request=request, stats=stats))
    assert await asyncio.to_thread(started.wait, 2)
    request.disconnect()
    with pytest.raises(HTTPException) as caught:
        await asyncio.wait_for(task, 2)
    assert caught.value.status_code == 499 and stopped.is_set()
    assert raw.started == raw.finished == 4 and stats["operation_calls"] == 0
    assert_read_only(db, before)


@pytest.mark.asyncio
@pytest.mark.parametrize("bound", ["page", "batch"])
async def test_raw_buffer_capacity_is_checked_before_financial_operation(monkeypatch, bound):
    db, _ = archived_fixture(4)
    before = deepcopy(db.data)
    raw = activate_fake(monkeypatch, db)
    monkeypatch.setattr(prefetch, "MAX_BSON_SIZE" if bound == "page" else "MAX_PREFETCH_BYTES", 16)
    stats = {"gates": [], "operation_calls": 0}
    with pytest.raises(HTTPException) as caught:
        await load_visible(db, monkeypatch, stats=stats)
    assert caught.value.status_code == 503 and stats["operation_calls"] == 0
    assert raw.started == raw.finished and raw.active == 0
    assert_read_only(db, before)


async def seed_real(db, owner="owner", pages=9):
    state = store.initial(owner)
    state.update(revision=7, status="active", started_at="2026-01-01T00:00:00+00:00")
    refs, expected, documents = [], [], []
    for index in range(pages):
        events = [{"ordinal": index, "amount": Decimal128("101.000001"), "integer": Int64(7),
            "proof": Binary(b"exact synthetic bytes", subtype=128), "when": datetime(2026, 1, 1, tzinfo=timezone.utc),
            "code": Code("synthetic", {"fixture": True}), "regex": Regex("synthetic", "i"),
            "timestamp": Timestamp(100, 1)}]
        key = archive.content_key(owner, index, events)
        documents.append({"_id": key, "owner_id": owner, "start": index, "events": events})
        refs.append({"id": key, "count": 1})
        expected.extend(events)
    await db[archive.AUDIT_PAGES].insert_many(documents)
    state[archive.MARKER] = archive.manifest(pages, refs)
    state["engine"]["audit"] = {"tail": [{"tail": True}]}
    state["engine"]["facts"] = {"typed": {"amount": Decimal128("101.000001"), "proof": b"saved"}}
    state["engine"]["issues"] = [{"code": "synthetic", "source_id": "one"}]
    packed = archive.compact_issues(archive.compact_financial_snapshot(state, 30000), 30000)
    await db[store.STATES].insert_one(packed)
    return packed


@pytest.mark.asyncio
@pytest.mark.parametrize("wrapped", [False, True])
async def test_actual_mongo_codec_full_state_and_immutable_pages_match_serial(read_db, monkeypatch, wrapped):
    if wrapped:
        from review_acceptance_config_guard import AcceptanceConfigDatabase
        read_db = AcceptanceConfigDatabase(read_db)
    await seed_real(read_db)
    baseline = await store.read(read_db, "owner")
    original = prefetch._read_raw
    reads = []

    async def observed(collection, owner, ref):
        assert collection.codec_options.document_class is RawBSONDocument
        data = await original(collection, owner, ref)
        assert type(data) is bytes and len(data) <= prefetch.MAX_BSON_SIZE
        reads.append(ref["id"])
        return data

    monkeypatch.setattr(prefetch, "_read_raw", observed)
    async def verify():
        pass
    async def unchanged(state):
        return state
    before = await read_db[store.STATES].find_one({"_id": "owner"})
    result = await demand.run_visible_refresh(Request(), read_db, "owner", "owner", verify,
        lambda: store.mutate(read_db, "owner", unchanged))
    assert BSON.encode(result) == BSON.encode(baseline)
    assert len(reads) == 9 and len(set(reads)) == 9
    assert await read_db[store.STATES].find_one({"_id": "owner"}) == before
    assert await read_db[demand.LEASES].count_documents({}) == 0
    assert asyncio.all_tasks() == {asyncio.current_task()}


@pytest.mark.asyncio
@pytest.mark.parametrize("mismatch", ["owner", "database", "unleased", "snapshot"])
async def test_actual_motor_fastpath_requires_exact_lease_capability(read_db, mismatch):
    context = SimpleNamespace(db=read_db, owner="owner", leased=True)
    db = read_db
    if mismatch == "owner":
        context.owner = "other"
    elif mismatch == "database":
        context.db = object()
    elif mismatch == "unleased":
        context.leased = False
    else:
        db = SimpleNamespace(session=object())
        context.db = db
    token = demand._CURRENT.set(context)
    try:
        assert prefetch._collection(db, "owner") is None
    finally:
        demand._CURRENT.reset(token)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["opaque", "slotted", "derived_motor", "wrapped_opaque"])
async def test_opaque_capabilities_decline_before_collection_access(read_db, kind):
    from review_acceptance_config_guard import AcceptanceConfigDatabase

    class Opaque:
        def __init__(self):
            self._hidden_session = object()
        def __getitem__(self, name):
            pytest.fail("opaque session adapter was accessed for prefetch")

    class Slotted:
        __slots__ = ("_hidden_session",)
        def __init__(self):
            self._hidden_session = object()
        def __getitem__(self, name):
            pytest.fail("slotted session adapter was accessed for prefetch")

    class DerivedMotor(AsyncIOMotorDatabase):
        def __getitem__(self, name):
            pytest.fail("custom Motor adapter was accessed for prefetch")

    db = {"opaque": Opaque, "slotted": Slotted,
          "derived_motor": lambda: DerivedMotor(read_db.client, read_db.name),
          "wrapped_opaque": lambda: AcceptanceConfigDatabase(Opaque())}[kind]()
    token = demand._CURRENT.set(SimpleNamespace(db=db, owner="owner", leased=True))
    try:
        assert prefetch._collection(db, "owner") is None
    finally:
        demand._CURRENT.reset(token)


@pytest.mark.asyncio
async def test_real_socket_deadline_survives_cancel_and_inherits_shorter_budgets(monkeypatch):
    from pymongo.synchronous.pool import Connection
    from operational_read_runtime import source_read_budget
    wire = await MongoReadWire("find").start()
    client = AsyncIOMotorClient(f"mongodb://127.0.0.1:{wire.port}/?directConnection=true",
                                serverSelectionTimeoutMS=1000, retryReads=False)
    collection = client.owned_read_wire[archive.AUDIT_PAGES].with_options(
        codec_options=CodecOptions(document_class=RawBSONDocument))
    budgets = []
    original = Connection.apply_timeout

    def observed(connection, mongo_client, cmd=None):
        value = original(connection, mongo_client, cmd)
        if cmd is not None and "find" in cmd:
            budgets.append(value)
        return value

    monkeypatch.setattr(Connection, "apply_timeout", observed)
    monkeypatch.setattr(prefetch, "PAGE_READ_SECONDS", 1.6)
    context = SimpleNamespace(read_deadline=time.monotonic()+1.5,
                              require_connected=lambda: None)
    token = demand._CURRENT.set(context)
    task = None
    try:
        with source_read_budget(1.2), pymongo.timeout(1):
            task = asyncio.create_task(prefetch._read_raw(collection, "owner", {"id": "synthetic"}))
            async with asyncio.timeout(2):
                while not any("find" in value for value in wire.commands):
                    if task.done():
                        await task
                    await asyncio.sleep(.001)
            task.cancel()
            await asyncio.sleep(.01)
            assert not task.done(), "cancelled waiter abandoned the real Mongo socket"
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 2)
        await asyncio.wait_for(wire.read_disconnected.wait(), 1)
        assert len(budgets) == 1 and 0 < budgets[0] <= 1
        assert len([value for value in wire.commands if "find" in value]) == 1
        assert not wire.errors
        assert pymongo._csot.remaining() is None
    finally:
        demand._CURRENT.reset(token)
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await asyncio.wait_for(asyncio.to_thread(client.close), 3)
        await wire.close()


@pytest.mark.asyncio
async def test_motor_workers_share_diminishing_source_deadline_without_leaking_it(monkeypatch):
    from motor.frameworks import asyncio as motor_asyncio
    from operational_read_runtime import source_read_budget
    budgets = []
    loop = asyncio.get_running_loop()

    class Driver:
        def find_one(self, query):
            def worker():
                budgets.append(pymongo._csot.remaining())
                return RawBSONDocument(BSON.encode({"_id": query["_id"], "owner_id": "owner"}))
            return motor_asyncio.run_on_executor(loop, worker)

    monkeypatch.setattr(prefetch, "PAGE_READ_SECONDS", .6)
    context = SimpleNamespace(read_deadline=time.monotonic()+.5, require_connected=lambda: None)
    token = demand._CURRENT.set(context)
    try:
        with source_read_budget(.3), pymongo.timeout(.4):
            await prefetch._read_raw(Driver(), "owner", {"id": "one"})
            await asyncio.sleep(.04)
            await prefetch._read_raw(Driver(), "owner", {"id": "two"})
            assert pymongo._csot.remaining() > budgets[-1]
        assert len(budgets) == 2 and 0 < budgets[1] < budgets[0] - .02 <= .3
        assert pymongo._csot.remaining() is None
        assert await motor_asyncio.run_on_executor(loop, pymongo._csot.remaining) is None
    finally:
        demand._CURRENT.reset(token)


@pytest.mark.asyncio
async def test_expired_request_deadline_never_dispatches_mongo():
    class NoRead:
        def find_one(self, *args):
            pytest.fail("expired request dispatched an archive read")

    token = demand._CURRENT.set(SimpleNamespace(read_deadline=time.monotonic()-1,
                                                require_connected=lambda: None))
    try:
        with pytest.raises(TimeoutError):
            await prefetch._read_raw(NoRead(), "owner", {"id": "synthetic"})
    finally:
        demand._CURRENT.reset(token)
