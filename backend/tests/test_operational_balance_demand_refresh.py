"""Visible request ownership, lease arbitration and atomic-save cancellation."""
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from threading import Event
from types import SimpleNamespace

from fastapi import HTTPException
from pymongo.errors import DuplicateKeyError
import pytest

from operational_balance_store import STATES, initial, mutate
from operational_demand_refresh import (
    LEASES, DemandRefreshContext, calculation_checkpoint, checkpoint,
    demand_context, run_calculation, run_visible_refresh,
)


class Request:
    def __init__(self):
        self.messages = asyncio.Queue()
        self.disconnected = False
        self.probes = 0

    async def is_disconnected(self):
        self.probes += 1
        return self.disconnected

    async def receive(self):
        return await self.messages.get()

    def disconnect(self):
        self.disconnected = True
        self.messages.put_nowait({"type": "http.disconnect"})


def matches(row, query):
    for key, value in query.items():
        if key == "$or":
            if not any(matches(row, condition) for condition in value):
                return False
        elif isinstance(value, dict):
            if "$gt" in value and not (row.get(key) is not None and row[key] > value["$gt"]):
                return False
            if "$lte" in value and not (row.get(key) is not None and row[key] <= value["$lte"]):
                return False
        elif row.get(key) != value:
            return False
    return True


class Collection:
    def __init__(self, db, name):
        self.db, self.name = db, name

    async def find_one(self, query, projection=None):
        return deepcopy(next((row for row in self.db.data.get(self.name, {}).values()
                              if matches(row, query)), None))

    async def find_one_and_update(self, query, update, **options):
        self.db.writes.append((self.name, "acquire", deepcopy(query)))
        rows = self.db.data.setdefault(self.name, {})
        row = next((row for row in rows.values() if matches(row, query)), None)
        if row is None:
            if query["_id"] in rows:
                raise DuplicateKeyError("busy")
            row = rows[query["_id"]] = {"_id": query["_id"]}
        row.update(deepcopy(update["$set"]))
        return deepcopy(row)

    async def update_one(self, query, update):
        self.db.writes.append((self.name, "renew", deepcopy(query)))
        row = next((row for row in self.db.data.get(self.name, {}).values() if matches(row, query)), None)
        if row is not None:
            row.update(deepcopy(update["$set"]))
        return SimpleNamespace(matched_count=int(row is not None))

    async def delete_one(self, query):
        self.db.writes.append((self.name, "release", deepcopy(query)))
        rows = self.db.data.get(self.name, {})
        keys = [key for key, row in rows.items() if matches(row, query)]
        for key in keys:
            del rows[key]
        return SimpleNamespace(deleted_count=len(keys))

    async def replace_one(self, query, row):
        self.db.cas_entered.set()
        if self.db.delay_cas:
            await self.db.cas_allowed.wait()
        self.db.writes.append((self.name, "replace", deepcopy(query)))
        current = self.db.data[self.name].get(query["_id"])
        matched = current is not None and matches(current, query) and not self.db.reject_cas
        if matched:
            self.db.data[self.name][query["_id"]] = deepcopy(row)
        return SimpleNamespace(matched_count=int(matched))

    async def insert_one(self, row):
        self.db.writes.append((self.name, "insert", deepcopy(row)))
        rows = self.db.data.setdefault(self.name, {})
        if row["_id"] in rows:
            raise DuplicateKeyError("existing")
        rows[row["_id"]] = deepcopy(row)


class DB:
    def __init__(self):
        state = initial("owner")
        state.update(status="active", revision=1, started_at="2026-11-01T00:00:00+03:00")
        self.data = {STATES: {"owner": state}}
        self.writes = []
        self.delay_cas = False
        self.reject_cas = False
        self.cas_entered = asyncio.Event()
        self.cas_allowed = asyncio.Event()

    def __getitem__(self, name):
        return Collection(self, name)


async def allowed():
    pass


async def update(db):
    async def apply(state):
        state["engine"] = {"as_of": "2026-11-12T12:00:00+00:00", "amount": "120.00"}
        return state
    return await mutate(db, "owner", apply)


def test_visible_request_owns_lease_work_and_cleanup():
    async def scenario():
        db, request = DB(), Request()
        calls = []

        async def verify():
            calls.append("verify")

        result = await run_visible_refresh(request, db, "owner", "staff", verify, lambda: update(db))
        assert result["revision"] == 2
        assert db.data[STATES]["owner"]["engine"]["amount"] == "120.00"
        assert len(calls) >= 5
        assert db.data[LEASES] == {}
        assert [call[1] for call in db.writes if call[0] == STATES] == ["replace"]
        assert not [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
        assert demand_context() is None
    asyncio.run(scenario())


def test_disconnect_before_start_does_not_read_or_write():
    async def scenario():
        db, request = DB(), Request()
        request.disconnect()

        async def forbidden():
            raise AssertionError("disconnected request performed work")

        with pytest.raises(HTTPException) as failure:
            await run_visible_refresh(request, db, "owner", "staff", forbidden, forbidden)
        assert failure.value.status_code == 499
        assert db.writes == []
    asyncio.run(scenario())


def test_disconnect_received_during_ready_preflight_cannot_race_first_write():
    async def scenario():
        db, request = DB(), Request()

        async def verify():
            if not request.disconnected:
                request.disconnect()

        with pytest.raises(HTTPException) as failure:
            await run_visible_refresh(request, db, "owner", "staff", verify, lambda: update(db))
        assert failure.value.status_code == 499
        assert db.writes == []
    asyncio.run(scenario())


def test_disconnection_cancels_and_joins_source_reads_without_save():
    async def scenario():
        db, request = DB(), Request()
        started, stopped = asyncio.Event(), asyncio.Event()

        async def read_source():
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()

        work = asyncio.create_task(run_visible_refresh(request, db, "owner", "staff", allowed, read_source))
        await started.wait()
        request.disconnect()
        with pytest.raises(HTTPException) as failure:
            await work
        assert failure.value.status_code == 499
        assert stopped.is_set()
        assert not [call for call in db.writes if call[0] == STATES]
        assert db.data[LEASES] == {}
        assert not [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
    asyncio.run(scenario())


def test_request_deadline_cancels_work_before_lease_can_expire(monkeypatch):
    import operational_demand_refresh as demand
    monkeypatch.setattr(demand, "REQUEST_SECONDS", .02)

    async def scenario():
        db, request = DB(), Request()
        stopped = asyncio.Event()

        async def source():
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()

        with pytest.raises(HTTPException) as failure:
            await run_visible_refresh(request, db, "owner", "staff", allowed, source)
        assert failure.value.status_code == 408
        assert failure.value.detail["code"] == "operational_refresh_timeout"
        assert stopped.is_set() and db.data[LEASES] == {}
        assert not [call for call in db.writes if call[0] == STATES]
        assert not [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
    asyncio.run(scenario())


def test_calculation_thread_stops_at_checkpoint_and_is_joined():
    async def scenario():
        db, request = DB(), Request()
        started, stopped = Event(), Event()

        def calculate():
            started.set()
            try:
                while True:
                    calculation_checkpoint()
            finally:
                stopped.set()

        async def operation():
            await run_calculation(calculate)
            raise AssertionError("cancelled CPU calculation continued")

        work = asyncio.create_task(run_visible_refresh(request, db, "owner", "staff", allowed, operation))
        await asyncio.to_thread(started.wait, 2)
        assert started.is_set()
        request.disconnect()
        with pytest.raises(HTTPException) as failure:
            await work
        assert failure.value.status_code == 499
        assert stopped.is_set()
        assert db.data[STATES]["owner"]["revision"] == 1
        assert db.data[LEASES] == {}
    asyncio.run(scenario())


def test_disconnect_during_admitted_commit_observes_exactly_one_atomic_result():
    async def scenario():
        db, request = DB(), Request()
        db.delay_cas = True
        work = asyncio.create_task(run_visible_refresh(request, db, "owner", "staff", allowed, lambda: update(db)))
        await db.cas_entered.wait()
        request.disconnect()
        db.cas_allowed.set()
        result = await work
        assert result["revision"] == 2
        assert db.data[STATES]["owner"]["revision"] == 2
        assert len([call for call in db.writes if call[0] == STATES]) == 1
        assert db.data[LEASES] == {}
    asyncio.run(scenario())


def test_request_deadline_waits_for_an_already_admitted_atomic_commit(monkeypatch):
    import operational_demand_refresh as demand
    monkeypatch.setattr(demand, "REQUEST_SECONDS", .02)

    async def scenario():
        db, request = DB(), Request()
        db.delay_cas = True
        work = asyncio.create_task(run_visible_refresh(request, db, "owner", "staff", allowed, lambda: update(db)))
        await db.cas_entered.wait()
        await asyncio.sleep(.04)
        assert not work.done()
        assert db.data[LEASES]["owner"]
        db.cas_allowed.set()
        result = await work
        assert result["revision"] == 2
        assert len([call for call in db.writes if call[0] == STATES]) == 1
        assert db.data[LEASES] == {}
    asyncio.run(scenario())


def test_failed_cas_does_not_retry_after_page_disconnect():
    async def scenario():
        db, request = DB(), Request()
        db.delay_cas, db.reject_cas = True, True
        work = asyncio.create_task(run_visible_refresh(request, db, "owner", "staff", allowed, lambda: update(db)))
        await db.cas_entered.wait()
        request.disconnect()
        # Let the listener observe the event before releasing the DB operation.
        await checkpoint_after_listener(request)
        db.cas_allowed.set()
        with pytest.raises(HTTPException) as failure:
            await work
        assert failure.value.status_code == 499
        assert len([call for call in db.writes if call[0] == STATES]) == 1
        assert db.data[STATES]["owner"]["revision"] == 1
    asyncio.run(scenario())


async def checkpoint_after_listener(request):
    # Queue.join cannot be used for ASGI messages. A scheduled callback provides
    # one event-loop turn without a timed sleep or production polling loop.
    loop = asyncio.get_running_loop()
    turn = loop.create_future()
    loop.call_soon(turn.set_result, None)
    await turn


def test_handler_cancellation_waits_for_started_commit():
    async def scenario():
        db, request = DB(), Request()
        db.delay_cas = True
        work = asyncio.create_task(run_visible_refresh(request, db, "owner", "staff", allowed, lambda: update(db)))
        await db.cas_entered.wait()
        work.cancel()
        db.cas_allowed.set()
        result = await work
        assert result["revision"] == 2
        assert db.data[LEASES] == {}
    asyncio.run(scenario())


def test_distributed_lease_rejects_second_context_and_releases_only_own_token():
    async def scenario():
        db, first_request, second_request = DB(), Request(), Request()
        entered, release = asyncio.Event(), asyncio.Event()

        async def hold():
            entered.set()
            await release.wait()
            return "first"

        first = asyncio.create_task(run_visible_refresh(first_request, db, "owner", "staff", allowed, hold))
        await entered.wait()
        original_token = db.data[LEASES]["owner"]["token"]
        with pytest.raises(HTTPException) as failure:
            await run_visible_refresh(second_request, db, "owner", "owner", allowed, hold)
        assert failure.value.detail["code"] == "operational_refresh_in_progress"
        assert db.data[LEASES]["owner"]["token"] == original_token
        release.set()
        assert await first == "first"
        assert db.data[LEASES] == {}
    asyncio.run(scenario())


def test_expired_lease_takeover_blocks_old_worker_renewal_and_release():
    async def scenario():
        db = DB()
        stamp = datetime(2026, 11, 12, tzinfo=timezone.utc)
        first = DemandRefreshContext(Request(), db, "owner", "staff", allowed, clock=lambda: stamp)
        second = DemandRefreshContext(Request(), db, "owner", "owner", allowed,
                                      clock=lambda: stamp + timedelta(minutes=3))
        await first.acquire()
        await second.acquire()
        with pytest.raises(HTTPException) as failure:
            await first.checkpoint()
        assert failure.value.detail["code"] == "operational_refresh_lease_lost"
        await first.release()
        assert db.data[LEASES]["owner"]["token"] == second.token
        await second.release()
        assert db.data[LEASES] == {}
    asyncio.run(scenario())


def test_permission_revocation_at_final_gate_prevents_save():
    async def scenario():
        db, request = DB(), Request()
        revoked = False

        async def verify():
            if revoked:
                raise HTTPException(403, detail={"code": "operational_actor_inactive"})

        async def operation():
            async def apply(state):
                nonlocal revoked
                state["engine"]["amount"] = "999.00"
                revoked = True
                return state
            return await mutate(db, "owner", apply)

        with pytest.raises(HTTPException) as failure:
            await run_visible_refresh(request, db, "owner", "staff", verify, operation)
        assert failure.value.status_code == 403
        assert db.data[STATES]["owner"]["revision"] == 1
        assert db.data[LEASES] == {}
        assert not [call for call in db.writes if call[0] == STATES]
    asyncio.run(scenario())


def test_ordinary_writer_has_no_visible_request_or_lease_requirement():
    async def scenario():
        db = DB()
        calculation_checkpoint()
        await checkpoint()
        assert await run_calculation(lambda value: value + 1, 3) == 4
        assert (await update(db))["revision"] == 2
        assert LEASES not in db.data
    asyncio.run(scenario())
