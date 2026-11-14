"""Visible refresh loads complete audit history with bounded authorization I/O."""
import asyncio
from contextlib import contextmanager
from copy import deepcopy
from threading import Event

from bson import Decimal128
from fastapi import HTTPException
import pytest

import operational_balance_archive as archive
import operational_balance_store as store
import operational_demand_refresh as demand
from test_operational_balance_demand_refresh import Collection, DB, Request
from test_operational_balance_snapshot_encoding import large_snapshot


class ObservedCollection(Collection):
    async def find_one(self, query, projection=None, **kwargs):
        self.db.reads.append((self.name, deepcopy(query), kwargs))
        if self.name == archive.AUDIT_PAGES:
            self.db.page_reads += 1
            if self.db.fail_on_page == self.db.page_reads:
                raise AssertionError("read continued after an invalid audit page")
            if self.db.block_page == self.db.page_reads:
                self.db.read_started.set()
                try:
                    await self.db.read_allowed.wait()
                finally:
                    self.db.read_stopped.set()
        return await super().find_one(query, projection)


class ObservedDB(DB):
    def __init__(self):
        super().__init__()
        self.reads = []
        self.page_reads = 0
        self.block_page = None
        self.fail_on_page = None
        self.read_started = asyncio.Event()
        self.read_allowed = asyncio.Event()
        self.read_stopped = asyncio.Event()

    def __getitem__(self, name):
        return ObservedCollection(self, name)


def archived_fixture(page_count, *, packed=True, owner="owner"):
    db = ObservedDB()
    state = large_snapshot()
    state.update(_id=owner, owner_id=owner)
    state["engine"]["facts"]["typed"] = {
        "amount": Decimal128("9007199254740993.01"), "proof": b"exact saved evidence"}
    state["engine"]["issues"] = [{"code": "synthetic_missing_evidence", "source_id": "fixture"}]
    tail = [{"action": "source_refresh", "ordinal": page_count, "tail": True}]
    expected = deepcopy(state)
    events, refs = [], []
    for index in range(page_count):
        page_events = [{"action": "source_refresh", "ordinal": index,
                        "proof": b"synthetic archive evidence", "value": Decimal128("100.01")}]
        key = archive.content_key(owner, index, page_events)
        db.data.setdefault(archive.AUDIT_PAGES, {})[key] = {
            "_id": key, "owner_id": owner, "start": index, "events": page_events}
        refs.append({"id": key, "count": 1})
        events.extend(deepcopy(page_events))
    expected["engine"]["audit"] = events + tail
    state[archive.MARKER] = archive.manifest(page_count, refs)
    state["engine"]["audit"] = {"tail": tail}
    if packed:
        state = archive.compact_financial_snapshot(state, 30000)
        state = archive.compact_issues(state, 30000)
    db.data[store.STATES] = {owner: state}
    return db, expected


def observe_stages(monkeypatch, stats):
    original = demand.refresh_stage

    @contextmanager
    def observed(stage):
        checks = len(stats["gates"])
        try:
            with original(stage):
                yield
        finally:
            if stage == "snapshot_load":
                stats["snapshot_gates"] = len(stats["gates"]) - checks

    monkeypatch.setattr(demand, "refresh_stage", observed)


async def load_visible(db, monkeypatch, *, request=None, verify_hook=None, operation=None, stats=None):
    stats = stats if stats is not None else {"gates": [], "operation_calls": 0}
    observe_stages(monkeypatch, stats)

    async def verify():
        stats["gates"].append(db.page_reads)
        if verify_hook is not None:
            await verify_hook(db, stats)

    async def unchanged(state):
        stats["operation_calls"] += 1
        return state

    result = await demand.run_visible_refresh(
        request or Request(), db, "owner", "owner", verify,
        operation or (lambda: store.mutate(db, "owner", unchanged)))
    return result, stats


def assert_read_only(db, before):
    assert {name: rows for name, rows in db.data.items() if name != demand.LEASES} == before
    assert not [write for write in db.writes if write[0] != demand.LEASES]
    assert db.data.get(demand.LEASES, {}) == {}
    assert asyncio.all_tasks() == {asyncio.current_task()}


def test_visible_snapshot_load_bounds_authorization_round_trips_without_losing_history(monkeypatch):
    async def scenario():
        db, expected = archived_fixture(64)
        before = deepcopy(db.data)
        result, stats = await load_visible(db, monkeypatch)
        assert result == expected
        assert db.page_reads == 64
        assert_read_only(db, before)
        # A complete 64-page load needs no more than 24 fresh gates. The old
        # per-page preparation performs 136, each with repeated metadata I/O.
        assert stats["snapshot_gates"] <= 24
        assert max(b - a for a, b in zip(stats["gates"], stats["gates"][1:])) <= 8
        assert stats["operation_calls"] == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("page_count", [1, 8, 9, 16])
@pytest.mark.parametrize("packed", [False, True])
def test_visible_batches_preserve_complete_bson_history_tail_and_manifest_order(monkeypatch, page_count, packed):
    async def scenario():
        db, expected = archived_fixture(page_count, packed=packed)
        # A failed competing CAS may leave a different immutable page at the
        # same owner/start. Only the saved manifest selects financial history.
        orphan_events = [{"action": "unreferenced fixture", "ordinal": 0}]
        orphan_key = archive.content_key("owner", 0, orphan_events)
        db.data[archive.AUDIT_PAGES][orphan_key] = {
            "_id": orphan_key, "owner_id": "owner", "start": 0, "events": orphan_events}
        before = deepcopy(db.data)
        result, stats = await load_visible(db, monkeypatch)
        refs = before[store.STATES]["owner"][archive.MARKER]["pages"]
        assert result == expected
        assert [query for name, query, _ in db.reads if name == archive.AUDIT_PAGES] == [
            {"_id": ref["id"], "owner_id": "owner"} for ref in refs]
        assert db.page_reads == page_count
        assert max(b - a for a, b in zip(stats["gates"], stats["gates"][1:])) <= 8
        assert stats["gates"][-1] == page_count
        assert_read_only(db, before)

    asyncio.run(scenario())


@pytest.mark.parametrize("page_index", [0, 8])
@pytest.mark.parametrize("damage", ["missing", "owner", "id", "start", "count", "content", "ref_count"])
def test_invalid_page_stops_before_the_next_read_and_before_returning_state(monkeypatch, page_index, damage):
    async def scenario():
        db, _ = archived_fixture(10)
        stored = db.data[store.STATES]["owner"]
        refs = stored[archive.MARKER]["pages"]
        key = refs[page_index]["id"]
        page = db.data[archive.AUDIT_PAGES][key]
        if damage == "missing":
            del db.data[archive.AUDIT_PAGES][key]
        elif damage == "owner":
            page["owner_id"] = "other"
        elif damage == "id":
            page["_id"] = "other"
        elif damage == "start":
            page["start"] += 1
        elif damage == "count":
            page["events"] = []
        elif damage == "content":
            page["events"][0]["proof"] = b"changed evidence"
        else:
            refs[page_index]["count"] = 2
            stored[archive.MARKER] = archive.manifest(11, refs)
        db.fail_on_page = page_index + 2
        before = deepcopy(db.data)
        stats = {"gates": [], "operation_calls": 0}
        with pytest.raises(HTTPException) as error:
            await load_visible(db, monkeypatch, stats=stats)
        assert error.value.status_code == 503
        assert error.value.detail["code"] == "operational_audit_archive_invalid"
        assert db.page_reads == page_index + 1
        assert stats["operation_calls"] == 0
        assert_read_only(db, before)

    asyncio.run(scenario())


@pytest.mark.parametrize("damage", ["manifest_hash", "ref_shape", "ref_type", "duplicate", "total", "tail"])
def test_archive_structure_and_completeness_checks_remain_mandatory(monkeypatch, damage):
    async def scenario():
        db, _ = archived_fixture(2)
        stored = db.data[store.STATES]["owner"]
        refs = stored[archive.MARKER]["pages"]
        expected_reads = 0
        if damage == "manifest_hash":
            stored[archive.MARKER]["sha256"] = "0" * 64
        elif damage == "ref_shape":
            refs[0]["extra"] = True
            stored[archive.MARKER] = archive.manifest(2, refs)
        elif damage == "ref_type":
            refs[0]["id"] = {"$ne": None}
            stored[archive.MARKER] = archive.manifest(2, refs)
        elif damage == "duplicate":
            refs[1] = deepcopy(refs[0])
            stored[archive.MARKER] = archive.manifest(2, refs)
            expected_reads = 2
        elif damage == "total":
            stored[archive.MARKER] = archive.manifest(3, refs)
            expected_reads = 2
        else:
            stored["engine"]["audit"] = {"tail": {}}
        before = deepcopy(db.data)
        stats = {"gates": [], "operation_calls": 0}
        with pytest.raises(HTTPException) as error:
            await load_visible(db, monkeypatch, stats=stats)
        assert error.value.status_code == 503
        assert error.value.detail["code"] == "operational_audit_archive_invalid"
        assert db.page_reads == expected_reads
        assert stats["operation_calls"] == 0
        assert_read_only(db, before)

    asyncio.run(scenario())


@pytest.mark.parametrize("at_page", [8, 9, 16])
def test_fresh_boundary_authorization_blocks_revoked_reader_before_more_io_or_output(monkeypatch, at_page):
    async def scenario():
        db, _ = archived_fixture(16 if at_page != 9 else 9)
        before = deepcopy(db.data)
        stats = {"gates": [], "operation_calls": 0}

        async def revoke(database, _stats):
            if database.page_reads == at_page:
                raise HTTPException(403, detail={"code": "fixture_permission_revoked"})

        with pytest.raises(HTTPException) as error:
            await load_visible(db, monkeypatch, verify_hook=revoke, stats=stats)
        assert error.value.status_code == 403
        assert db.page_reads == at_page
        assert stats["operation_calls"] == 0
        assert_read_only(db, before)

    asyncio.run(scenario())


def test_lost_lease_at_batch_boundary_prevents_more_reads_and_preserves_successor(monkeypatch):
    async def scenario():
        db, _ = archived_fixture(16)
        before = deepcopy(db.data)
        stats = {"gates": [], "operation_calls": 0}

        async def replace_lease(database, _stats):
            if database.page_reads == 8:
                database.data[demand.LEASES]["owner"]["token"] = "successor-fixture"

        with pytest.raises(HTTPException) as error:
            await load_visible(db, monkeypatch, verify_hook=replace_lease, stats=stats)
        assert error.value.detail["code"] == "operational_refresh_lease_lost"
        assert db.page_reads == 8 and stats["operation_calls"] == 0
        assert {name: rows for name, rows in db.data.items() if name != demand.LEASES} == before
        assert not [write for write in db.writes if write[0] != demand.LEASES]
        assert db.data[demand.LEASES]["owner"]["token"] == "successor-fixture"
        assert asyncio.all_tasks() == {asyncio.current_task()}

    asyncio.run(scenario())


@pytest.mark.parametrize("page", [5, 9])
def test_disconnect_cancels_page_io_without_reading_more_or_exposing_state(monkeypatch, page):
    async def scenario():
        db, _ = archived_fixture(16)
        db.block_page = page
        request = Request()
        before = deepcopy(db.data)
        stats = {"gates": [], "operation_calls": 0}
        task = asyncio.create_task(load_visible(db, monkeypatch, request=request, stats=stats))
        await asyncio.wait_for(db.read_started.wait(), 2)
        request.disconnect()
        with pytest.raises(HTTPException) as error:
            await asyncio.wait_for(task, 2)
        assert error.value.status_code == 499
        assert db.read_stopped.is_set()
        assert db.page_reads == page and stats["operation_calls"] == 0
        assert_read_only(db, before)

    asyncio.run(scenario())


def test_disconnect_joins_page_validation_worker_before_returning(monkeypatch):
    async def scenario():
        db, _ = archived_fixture(16)
        request = Request()
        before = deepcopy(db.data)
        started, stopped = Event(), Event()
        original = archive.validated_events
        stats = {"gates": [], "operation_calls": 0}

        def paused_page(row, owner, start, ref):
            if start != 4:
                return original(row, owner, start, ref)
            started.set()
            try:
                while True:
                    demand.calculation_checkpoint()
            finally:
                stopped.set()

        monkeypatch.setattr(archive, "validated_events", paused_page)
        task = asyncio.create_task(load_visible(db, monkeypatch, request=request, stats=stats))
        assert await asyncio.to_thread(started.wait, 2)
        request.disconnect()
        with pytest.raises(HTTPException) as error:
            await asyncio.wait_for(task, 2)
        assert error.value.status_code == 499 and stopped.is_set()
        assert db.page_reads == 5 and stats["operation_calls"] == 0
        assert_read_only(db, before)

    asyncio.run(scenario())


@pytest.mark.parametrize("mismatch", ["database", "owner"])
def test_unrelated_demand_context_retains_per_page_authorization(monkeypatch, mismatch):
    async def scenario():
        db, expected = archived_fixture(9)
        context_db = ObservedDB() if mismatch == "database" else db
        context_owner = "other" if mismatch == "owner" else "owner"
        before = deepcopy(db.data)
        guarded_pages = []
        original = archive._prepare_read

        async def observed(function, *args):
            if function is archive.validated_events:
                guarded_pages.append(args[2])
            return await original(function, *args)

        monkeypatch.setattr(archive, "_prepare_read", observed)

        async def verify():
            pass

        result = await demand.run_visible_refresh(
            Request(), context_db, context_owner, context_owner, verify,
            lambda: store.read(db, "owner"))
        assert result == expected and guarded_pages == list(range(9))
        assert_read_only(db, before)
        assert context_db.data.get(demand.LEASES, {}) == {}

    asyncio.run(scenario())


@pytest.mark.parametrize("wrapper", ["ordinary", "snapshot", "review"])
def test_ordinary_and_strict_session_readers_keep_exact_sequential_capabilities(monkeypatch, wrapper):
    async def scenario():
        from operational_driver_review_store import ReviewDatabase
        from operational_supplier_invoice_preview import SnapshotReads

        db, expected = archived_fixture(9)
        before = deepcopy(db.data)
        session = object()
        scoped = (SnapshotReads(db, "owner", session) if wrapper == "snapshot" else
                  ReviewDatabase(db, session, "owner") if wrapper == "review" else db)
        guarded_pages = []
        original = archive._prepare_read

        async def observed(function, *args):
            if function is archive.validated_events:
                guarded_pages.append(args[2])
            return await original(function, *args)

        monkeypatch.setattr(archive, "_prepare_read", observed)
        result = await store.read(scoped, "owner")
        assert result == expected and guarded_pages == list(range(9))
        refs = before[store.STATES]["owner"][archive.MARKER]["pages"]
        page_reads = [(query, kwargs) for name, query, kwargs in db.reads if name == archive.AUDIT_PAGES]
        assert page_reads == [({"_id": ref["id"], "owner_id": "owner"},
                               {} if wrapper == "ordinary" else {"session": session}) for ref in refs]
        assert_read_only(db, before)

    asyncio.run(scenario())
