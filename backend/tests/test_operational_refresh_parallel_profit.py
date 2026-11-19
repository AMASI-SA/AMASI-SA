"""Visible refresh joins profit reads and ledger preparation before one CAS.

Event gates prove ordering and ownership without timing a performance budget.
The reducer stub runs in the real request-owned calculation thread, and all
tests retain the production visible-refresh lease and aggregate store.
"""
import asyncio
from copy import deepcopy
from threading import Event

from fastapi import HTTPException
import pytest

import operational_balance_engine as engine
import operational_balance_inventory as inventory
import operational_balance_service as service
import operational_balance_sources as sources
import operational_demand_refresh as demand
import operational_profit_sources as profit_sources
from operational_balance_store import STATES
from operational_read_runtime import SourceReadError
from test_operational_balance_demand_refresh import Collection, DB, Request, allowed


OWNER = "owner"
START = "2026-11-01T00:30:00+03:00"
STAMP = "2026-11-18T19:31:35+00:00"
WAIT = 5


async def reached(event):
    await asyncio.wait_for(event.wait(), WAIT)


def prepared_state(state, stamp, attempt=1):
    result = deepcopy(state)
    result["movements"].append({
        "id": "fixture-shipping-fee", "amount": "34.50", "currency": "SAR",
        "direction": "outgoing", "kind": "expense", "attempt": attempt,
    })
    result["engine"] = {"as_of": stamp, "issues": [], "obligations": {}}
    return result


def setup_sources(monkeypatch, database=None):
    database = database or DB()
    state = database.data[STATES][OWNER]
    state["started_at"] = START
    state["engine"] = {
        "as_of": "2026-11-18T02:40:00+00:00",
        "profit_snapshot": {"saved": "previous-success"},
    }
    collected = []

    async def no_collision(db, owner, operational_state):
        assert db is database and owner == operational_state["owner_id"] == OWNER

    async def collect(db, owner, start, stamp, *, baselines, operational_state):
        assert db is database and owner == operational_state["owner_id"] == OWNER
        assert start == operational_state["started_at"] == START and stamp == STAMP
        rows = {"orders": [], "attempt": len(collected) + 1}
        collected.append((operational_state, rows))
        return rows

    monkeypatch.setattr(inventory, "assert_no_source_collision", no_collision)
    monkeypatch.setattr(sources, "collect_sources", collect)
    return database, collected


class PreparationPair:
    """Hold each side independently, including its cancellation cleanup."""

    def __init__(self, monkeypatch, database):
        self.database = database
        self.loop = asyncio.get_running_loop()
        self.started = {side: asyncio.Event() for side in ("reconcile", "profit")}
        self.closing = {side: asyncio.Event() for side in ("reconcile", "profit")}
        self.finished = {side: asyncio.Event() for side in ("reconcile", "profit")}
        self.reconcile_allowed, self.thread_cleanup_allowed = Event(), Event()
        self.profit_allowed, self.profit_cleanup_allowed = asyncio.Event(), asyncio.Event()
        self.hold_cleanup = set()
        self.failures, self.cleanup_failures, self.inputs = {}, {}, {}
        self.context = None
        self.prepared = None
        self.trace = []
        self.profit_cleanup_interruptions = 0
        self.snapshot = {"owner_id": OWNER, "as_of": STAMP, "fixture": "joined-profit"}
        monkeypatch.setattr(engine, "reconcile", self.reconcile)
        monkeypatch.setattr(profit_sources, "load_profit_snapshot", self.profit)

    def notify(self, events, side):
        self.loop.call_soon_threadsafe(events[side].set)

    def reconcile(self, state, rows, stamp):
        self.inputs["reconcile"] = (state, rows, stamp)
        self.trace.append("reconcile-start")
        self.notify(self.started, "reconcile")
        try:
            assert self.reconcile_allowed.wait(WAIT), "test did not release the calculation"
            demand.calculation_checkpoint()
            if "reconcile" in self.failures:
                raise self.failures["reconcile"]
            self.prepared = prepared_state(state, stamp)
            return self.prepared
        finally:
            self.notify(self.closing, "reconcile")
            if "reconcile" in self.hold_cleanup:
                assert self.thread_cleanup_allowed.wait(WAIT), "thread cleanup was not released"
            self.trace.append("reconcile-finish")
            self.notify(self.finished, "reconcile")
            if "reconcile" in self.cleanup_failures:
                raise self.cleanup_failures["reconcile"]

    async def profit(self, db, owner, period, *, as_of):
        assert db is self.database and owner == period["owner_id"] == OWNER
        assert period["started_at"] == START and as_of == STAMP
        self.inputs["profit"] = period
        self.context = demand.demand_context()
        self.trace.append("profit-start")
        self.started["profit"].set()
        try:
            await self.profit_allowed.wait()
            if "profit" in self.failures:
                raise self.failures["profit"]
            return self.snapshot
        finally:
            self.closing["profit"].set()
            if "profit" in self.hold_cleanup:
                try:
                    await self.profit_cleanup_allowed.wait()
                except asyncio.CancelledError:
                    self.profit_cleanup_interruptions += 1
                    raise
            self.trace.append("profit-finish")
            self.finished["profit"].set()
            if "profit" in self.cleanup_failures:
                raise self.cleanup_failures["profit"]

    async def both_started(self):
        await asyncio.wait_for(asyncio.gather(*(event.wait() for event in self.started.values())), WAIT)

    def release(self, side):
        (self.reconcile_allowed if side == "reconcile" else self.profit_allowed).set()

    def release_cleanup(self, side):
        (self.thread_cleanup_allowed if side == "reconcile" else self.profit_cleanup_allowed).set()

    async def close(self, work):
        for side in ("reconcile", "profit"):
            self.release(side)
            self.release_cleanup(side)
        await asyncio.gather(work, return_exceptions=True)


class SourcePreparation:
    """Gate a real refresh's source boundary and its cancellation cleanup."""

    def __init__(self, monkeypatch, database, stage="collection"):
        self.database = database
        self.started, self.closing, self.finished = (asyncio.Event() for _ in range(3))
        self.allowed, self.cleanup_allowed = asyncio.Event(), asyncio.Event()
        self.hold_cleanup = False
        self.failure = self.cleanup_failure = self.context = None
        self.cleanup_interruptions = 0
        if stage == "collection":
            self.operation = sources.collect_sources
            monkeypatch.setattr(sources, "collect_sources", self.collect)
        else:
            assert stage == "collision"
            self.operation = inventory.assert_no_source_collision
            monkeypatch.setattr(inventory, "assert_no_source_collision", self.collision)

    async def collect(self, db, owner, start, stamp, *, baselines, operational_state):
        assert db is self.database and owner == operational_state["owner_id"] == OWNER
        assert start == operational_state["started_at"] == START and stamp == STAMP
        return await self.run(db, owner, start, stamp,
                              baselines=baselines, operational_state=operational_state)

    async def collision(self, db, owner, state):
        assert db is self.database and owner == state["owner_id"] == OWNER
        return await self.run(db, owner, state)

    async def run(self, *args, **kwargs):
        self.context = demand.demand_context()
        self.started.set()
        try:
            await self.allowed.wait()
            if self.failure is not None:
                raise self.failure
            return await self.operation(*args, **kwargs)
        finally:
            self.closing.set()
            if self.hold_cleanup:
                try:
                    await self.cleanup_allowed.wait()
                except asyncio.CancelledError:
                    self.cleanup_interruptions += 1
                    raise
            self.finished.set()
            if self.cleanup_failure is not None:
                raise self.cleanup_failure

    def release(self):
        self.allowed.set()

    def release_cleanup(self):
        self.cleanup_allowed.set()


def visible(database, request=None, verify=allowed):
    return demand.run_visible_refresh(
        request or Request(), database, OWNER, "staff", verify,
        lambda: service.refresh(database, OWNER, clock=STAMP),
    )


def unchanged(database, before):
    assert database.data[STATES] == before
    assert not [row for row in database.writes if row[0] != demand.LEASES]


def fully_joined(database, pair=None):
    if pair is not None:
        assert all(event.is_set() for event in pair.finished.values())
        assert pair.profit_cleanup_interruptions == 0
    assert database.data[demand.LEASES] == {}
    assert demand.demand_context() is None
    assert not [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]


@pytest.mark.parametrize("first", ["sources", "profit"])
def test_visible_refresh_overlaps_source_collection_with_profit_before_reconciliation(monkeypatch, first):
    async def scenario():
        database, collected = setup_sources(monkeypatch)
        before = deepcopy(database.data[STATES])
        pair = PreparationPair(monkeypatch, database)
        source = SourcePreparation(monkeypatch, database)
        work = asyncio.create_task(visible(database))
        try:
            await reached(source.started)
            await reached(pair.started["profit"])
            assert source.context is pair.context
            assert source.context.read_deadline is not None
            assert not source.finished.is_set() and not pair.started["reconcile"].is_set()
            assert pair.inputs["profit"] == {"owner_id": OWNER, "started_at": START}
            assert not collected
            unchanged(database, before)

            if first == "profit":
                pair.release("profit")
                await reached(pair.finished["profit"])
                assert not source.finished.is_set() and not pair.started["reconcile"].is_set()
                assert not work.done() and not database.cas_entered.is_set()
                unchanged(database, before)
            source.release()
            await reached(pair.started["reconcile"])
            assert source.finished.is_set()
            state, rows, stamp = pair.inputs["reconcile"]
            assert (state, rows) == collected[0] and stamp == STAMP
            assert pair.inputs["profit"] is not state
            pair.release("reconcile")
            await reached(pair.finished["reconcile"])
            if first == "sources":
                assert not pair.finished["profit"].is_set()
                assert not work.done() and not database.cas_entered.is_set()
                unchanged(database, before)
                pair.release("profit")
            result = await asyncio.wait_for(asyncio.shield(work), WAIT)
            saved = database.data[STATES][OWNER]
            assert result["revision"] == saved["revision"] == 2
            assert saved["engine"]["as_of"] == STAMP
            assert saved["engine"]["profit_snapshot"] == pair.snapshot
            assert saved["movements"] == prepared_state(before[OWNER], STAMP)["movements"]
            assert [row[1] for row in database.writes if row[0] == STATES] == ["replace"]
        finally:
            source.release()
            source.release_cleanup()
            await pair.close(work)
        fully_joined(database, pair)
    asyncio.run(scenario())


@pytest.mark.parametrize("stage", ["collision", "collection"])
@pytest.mark.parametrize("failure_kind", ["read", "setup", "http"])
def test_source_failure_keeps_its_error_contract_and_joins_profit_without_reconciliation(
        monkeypatch, stage, failure_kind):
    async def scenario():
        from operational_source_diagnostics import source_failure_detail

        database, collected = setup_sources(monkeypatch)
        before = deepcopy(database.data[STATES])
        pair = PreparationPair(monkeypatch, database)
        source = SourcePreparation(monkeypatch, database, stage)
        failures = {
            "read": SourceReadError("operational_source_read_timeout", "unified_orders"),
            "setup": ValueError("private source setup failure"),
            "http": HTTPException(409, detail={"code": "fixture_collision_rejected"}),
        }
        source.failure = failures[failure_kind]
        pair.hold_cleanup.add("profit")
        pair.cleanup_failures["profit"] = RuntimeError("secondary profit cleanup failure")
        work = asyncio.create_task(visible(database))
        try:
            await reached(source.started)
            await reached(pair.started["profit"])
            source.release()
            await reached(source.finished)
            await reached(pair.closing["profit"])
            assert not pair.started["reconcile"].is_set() and not collected
            assert not work.done() and database.data[demand.LEASES]
            unchanged(database, before)
            pair.release_cleanup("profit")
            with pytest.raises(HTTPException) as raised:
                await asyncio.wait_for(asyncio.shield(work), WAIT)
            assert raised.value.status_code == 409
            if failure_kind == "read":
                assert {key: value for key, value in raised.value.detail.items()
                        if key != "stage_timings"} == source_failure_detail(
                            "operational_source_read_timeout", "unified_orders")
                assert {"stage": "source_collection", "outcome": "failed"} in [
                    {"stage": row["stage"], "outcome": row["outcome"]}
                    for row in raised.value.detail["stage_timings"]]
            elif failure_kind == "setup":
                assert raised.value.detail == {
                    "code": "operational_source_setup_incomplete",
                    "message": "مصادر ميزان 2 غير مكتملة أو تجاوزت حدود القراءة",
                }
            else:
                assert raised.value is source.failure
        finally:
            source.release()
            source.release_cleanup()
            await pair.close(work)
        assert source.finished.is_set() and pair.finished["profit"].is_set()
        assert pair.profit_cleanup_interruptions == 0
        unchanged(database, before)
        fully_joined(database)
    asyncio.run(scenario())


@pytest.mark.parametrize("secondary_cleanup_error", [False, True])
def test_profit_failure_joins_inflight_collection_before_returning_without_save(
        monkeypatch, secondary_cleanup_error):
    async def scenario():
        database, collected = setup_sources(monkeypatch)
        before = deepcopy(database.data[STATES])
        pair = PreparationPair(monkeypatch, database)
        source = SourcePreparation(monkeypatch, database)
        failure = ValueError("profit preparation rejected")
        pair.failures["profit"] = failure
        source.hold_cleanup = True
        if secondary_cleanup_error:
            source.cleanup_failure = RuntimeError("secondary source cleanup failure")
        work = asyncio.create_task(visible(database))
        try:
            await reached(source.started)
            await reached(pair.started["profit"])
            pair.release("profit")
            await reached(pair.finished["profit"])
            await reached(source.closing)
            assert not source.finished.is_set() and not work.done()
            assert database.data[demand.LEASES]
            assert not pair.started["reconcile"].is_set() and not collected
            unchanged(database, before)
            source.release_cleanup()
            with pytest.raises(ValueError) as raised:
                await asyncio.wait_for(asyncio.shield(work), WAIT)
            assert raised.value is failure
        finally:
            source.release()
            source.release_cleanup()
            await pair.close(work)
        assert source.finished.is_set() and source.cleanup_interruptions == 0
        unchanged(database, before)
        fully_joined(database)
    asyncio.run(scenario())


@pytest.mark.parametrize("cause", ["disconnect", "deadline", "parent", "repeated_parent"])
def test_cancellation_during_collection_joins_both_sources_before_lease_release(monkeypatch, cause):
    if cause == "deadline":
        monkeypatch.setattr(demand, "REQUEST_SECONDS", 1)

    async def scenario():
        database, collected = setup_sources(monkeypatch)
        before = deepcopy(database.data[STATES])
        request = Request()
        pair = PreparationPair(monkeypatch, database)
        pair.hold_cleanup.add("profit")
        source = SourcePreparation(monkeypatch, database)
        source.hold_cleanup = True
        work = asyncio.create_task(visible(database, request))
        try:
            await reached(source.started)
            await reached(pair.started["profit"])
            if cause == "disconnect":
                request.disconnect()
            elif cause in {"parent", "repeated_parent"}:
                work.cancel()
            await reached(source.closing)
            await reached(pair.closing["profit"])
            assert not pair.started["reconcile"].is_set() and not collected
            assert not work.done() and database.data[demand.LEASES]
            unchanged(database, before)
            if cause == "repeated_parent":
                source.context.work.cancel()
                await asyncio.sleep(0)
                work.cancel()
                await asyncio.sleep(0)
                assert not work.done() and not source.finished.is_set()
                assert not pair.finished["profit"].is_set()
                assert source.cleanup_interruptions == pair.profit_cleanup_interruptions == 0
            pair.release_cleanup("profit")
            await reached(pair.finished["profit"])
            assert not work.done() and database.data[demand.LEASES]
            source.release_cleanup()
            expected = HTTPException if cause in {"disconnect", "deadline"} else asyncio.CancelledError
            with pytest.raises(expected) as raised:
                await asyncio.wait_for(asyncio.shield(work), WAIT)
            if cause in {"disconnect", "deadline"}:
                assert raised.value.status_code == (499 if cause == "disconnect" else 408)
                assert raised.value.detail["code"] == (
                    "operational_refresh_cancelled" if cause == "disconnect" else "operational_refresh_timeout")
        finally:
            source.release()
            source.release_cleanup()
            await pair.close(work)
        assert source.finished.is_set() and pair.finished["profit"].is_set()
        assert source.cleanup_interruptions == pair.profit_cleanup_interruptions == 0
        unchanged(database, before)
        fully_joined(database)
    asyncio.run(scenario())


@pytest.mark.parametrize("first", ["reconcile", "profit"])
def test_visible_refresh_overlaps_both_jobs_but_saves_only_the_complete_pair(monkeypatch, first):
    async def scenario():
        database, collected = setup_sources(monkeypatch)
        before = deepcopy(database.data[STATES])
        pair = PreparationPair(monkeypatch, database)
        work = asyncio.create_task(visible(database))
        try:
            await pair.both_started()
            assert not any(event.is_set() for event in pair.finished.values())
            unchanged(database, before)
            state, rows, stamp = pair.inputs["reconcile"]
            assert state is collected[0][0] and rows is collected[0][1]
            assert stamp == STAMP and state["owner_id"] == OWNER and state["started_at"] == START
            assert pair.inputs["profit"] == {"owner_id": OWNER, "started_at": START}
            assert pair.inputs["profit"] is not state

            pair.release(first)
            await reached(pair.finished[first])
            assert not work.done() and not database.cas_entered.is_set()
            unchanged(database, before)
            pair.release("profit" if first == "reconcile" else "reconcile")
            result = await asyncio.wait_for(asyncio.shield(work), WAIT)

            saved = database.data[STATES][OWNER]
            assert result["revision"] == saved["revision"] == 2
            assert saved["engine"]["as_of"] == STAMP
            assert saved["engine"]["profit_snapshot"] == pair.snapshot
            assert saved["movements"] == prepared_state(before[OWNER], STAMP)["movements"]
            assert [(row[0], row[1]) for row in database.writes if row[0] == STATES] == [(STATES, "replace")]
            assert database.writes[-1][0:2] == (demand.LEASES, "release")
            assert sorted((row["stage"], row["outcome"]) for row in pair.context.stage_timings
                          if row["stage"] in {"reconciliation", "profit_snapshot"}) == [
                              ("profit_snapshot", "complete"), ("reconciliation", "complete")]
        finally:
            await pair.close(work)
        fully_joined(database, pair)
    asyncio.run(scenario())


@pytest.mark.parametrize("failed_side", ["reconcile", "profit"])
@pytest.mark.parametrize("secondary_cleanup_error", [False, True])
def test_failure_preserves_original_error_and_joins_the_other_job_without_cas(
        monkeypatch, failed_side, secondary_cleanup_error):
    async def scenario():
        database, _ = setup_sources(monkeypatch)
        before = deepcopy(database.data[STATES])
        pair = PreparationPair(monkeypatch, database)
        sibling = "profit" if failed_side == "reconcile" else "reconcile"
        failure = ValueError("synthetic preparation rejected")
        pair.failures[failed_side] = failure
        pair.hold_cleanup.add(sibling)
        if secondary_cleanup_error:
            pair.cleanup_failures[sibling] = RuntimeError("synthetic secondary cleanup failure")
        work = asyncio.create_task(visible(database))
        try:
            await pair.both_started()
            pair.release(failed_side)
            await reached(pair.finished[failed_side])
            if sibling == "reconcile":
                # The source error must first cancel the owned CPU waiter.
                await reached(pair.closing[failed_side])
                assert await asyncio.to_thread(pair.context.cancelled.wait, WAIT)
                pair.release("reconcile")
            await reached(pair.closing[sibling])
            assert not work.done() and database.data[demand.LEASES]
            unchanged(database, before)
            pair.release_cleanup(sibling)
            with pytest.raises(ValueError) as raised:
                await asyncio.wait_for(asyncio.shield(work), WAIT)
            assert raised.value is failure
        finally:
            await pair.close(work)
        unchanged(database, before)
        fully_joined(database, pair)
    asyncio.run(scenario())


@pytest.mark.parametrize("cause", ["disconnect", "deadline", "parent", "repeated_parent"])
def test_cancellation_joins_thread_and_source_cleanup_before_releasing_lease(monkeypatch, cause):
    if cause == "deadline":
        monkeypatch.setattr(demand, "REQUEST_SECONDS", 1)

    async def scenario():
        database, _ = setup_sources(monkeypatch)
        before = deepcopy(database.data[STATES])
        request = Request()
        pair = PreparationPair(monkeypatch, database)
        pair.hold_cleanup.update({"reconcile", "profit"})
        work = asyncio.create_task(visible(database, request))
        try:
            await pair.both_started()
            if cause == "disconnect":
                request.disconnect()
            elif cause in {"parent", "repeated_parent"}:
                work.cancel()
            await reached(pair.closing["profit"])
            pair.release("reconcile")
            await reached(pair.closing["reconcile"])
            assert not work.done() and database.data[demand.LEASES]
            unchanged(database, before)
            if cause == "repeated_parent":
                # The request may receive both deadline and disconnect cancellation.
                pair.context.work.cancel()
                await asyncio.sleep(0)
                work.cancel()
                await asyncio.sleep(0)
                assert not work.done()
                assert not any(event.is_set() for event in pair.finished.values())
                assert pair.profit_cleanup_interruptions == 0
                assert database.data[demand.LEASES]
            pair.release_cleanup("profit")
            await reached(pair.finished["profit"])
            assert not work.done() and database.data[demand.LEASES]
            pair.release_cleanup("reconcile")
            expected = HTTPException if cause in {"disconnect", "deadline"} else asyncio.CancelledError
            with pytest.raises(expected) as raised:
                await asyncio.wait_for(asyncio.shield(work), WAIT)
            if cause in {"disconnect", "deadline"}:
                assert raised.value.status_code == (499 if cause == "disconnect" else 408)
                assert raised.value.detail["code"] == (
                    "operational_refresh_cancelled" if cause == "disconnect" else "operational_refresh_timeout")
        finally:
            await pair.close(work)
        unchanged(database, before)
        fully_joined(database, pair)
    asyncio.run(scenario())


def test_revoked_authorization_after_both_jobs_start_prevents_financial_save(monkeypatch):
    async def scenario():
        database, _ = setup_sources(monkeypatch)
        before = deepcopy(database.data[STATES])
        pair = PreparationPair(monkeypatch, database)
        revoked = False
        failure = HTTPException(403, detail={"code": "fixture_permission_revoked"})

        async def verify():
            if revoked:
                raise failure

        work = asyncio.create_task(visible(database, verify=verify))
        try:
            await pair.both_started()
            revoked = True
            pair.release("reconcile")
            pair.release("profit")
            with pytest.raises(HTTPException) as raised:
                await asyncio.wait_for(asyncio.shield(work), WAIT)
            assert raised.value is failure
        finally:
            await pair.close(work)
        unchanged(database, before)
        fully_joined(database, pair)
    asyncio.run(scenario())


@pytest.mark.parametrize("mode", ["ordinary", "snapshot_session"])
def test_nonvisible_and_snapshot_session_callers_retain_serial_result_state_contract(monkeypatch, mode):
    async def scenario():
        database, _ = setup_sources(monkeypatch)
        if mode == "snapshot_session":
            database.session = object()
        pair = PreparationPair(monkeypatch, database)
        source = SourcePreparation(monkeypatch, database)
        operation = visible(database) if mode == "snapshot_session" else service.refresh(database, OWNER, clock=STAMP)
        work = asyncio.create_task(operation)
        try:
            await reached(source.started)
            assert not pair.started["reconcile"].is_set() and not pair.started["profit"].is_set()
            source.release()
            await reached(pair.started["reconcile"])
            assert source.finished.is_set()
            assert not pair.started["profit"].is_set()
            pair.release("reconcile")
            await reached(pair.started["profit"])
            assert pair.inputs["profit"] is pair.prepared
            assert pair.trace.index("reconcile-finish") < pair.trace.index("profit-start")
            pair.release("profit")
            await asyncio.wait_for(asyncio.shield(work), WAIT)
            assert database.data[STATES][OWNER]["engine"]["profit_snapshot"] == pair.snapshot
        finally:
            source.release()
            source.release_cleanup()
            await pair.close(work)
        if mode == "snapshot_session":
            fully_joined(database, pair)
        else:
            assert demand.LEASES not in database.data and demand.demand_context() is None
            assert not [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
    asyncio.run(scenario())


def test_cas_retry_rebuilds_both_preparations_from_the_new_revision(monkeypatch):
    async def scenario():
        database, collected = setup_sources(monkeypatch)
        original_replace = Collection.replace_one
        revisions, periods = [], []

        async def replace(collection, query, row):
            if collection.name == STATES and not revisions:
                revisions.append(query["revision"])
                database.reject_cas = True
                rejected = await original_replace(collection, query, row)
                database.reject_cas = False
                saved = database.data[STATES][OWNER]
                saved["revision"] = 2
                saved["movements"].append({"id": "concurrent-accepted-movement", "amount": "10.00"})
                return rejected
            if collection.name == STATES:
                revisions.append(query["revision"])
            return await original_replace(collection, query, row)

        def calculate(state, rows, stamp):
            assert state["revision"] == rows["attempt"]
            return prepared_state(state, stamp, rows["attempt"])

        async def load_profit(db, owner, period, *, as_of):
            assert db is database and owner == OWNER and as_of == STAMP
            assert period == {"owner_id": OWNER, "started_at": START}
            periods.append(period)
            return {"attempt": len(periods), "owner_id": owner, "as_of": as_of}

        monkeypatch.setattr(Collection, "replace_one", replace)
        monkeypatch.setattr(engine, "reconcile", calculate)
        monkeypatch.setattr(profit_sources, "load_profit_snapshot", load_profit)
        result = await visible(database)
        saved = database.data[STATES][OWNER]
        assert revisions == [1, 2] and len(collected) == len(periods) == 2
        assert periods[0] is not periods[1]
        assert result["revision"] == saved["revision"] == 3
        assert saved["engine"]["profit_snapshot"]["attempt"] == 2
        assert [row["id"] for row in saved["movements"]] == [
            "concurrent-accepted-movement", "fixture-shipping-fee"]
        assert saved["movements"][-1]["attempt"] == 2
        fully_joined(database)
    asyncio.run(scenario())


def test_soft_profit_source_timeout_keeps_its_incomplete_snapshot_in_the_same_save(monkeypatch):
    async def scenario():
        database, _ = setup_sources(monkeypatch)

        def calculate(state, rows, stamp):
            return prepared_state(state, stamp)

        async def unavailable(db, owner, readers, **kwargs):
            assert db is database and owner == OWNER and set(readers) == {"context"}
            raise SourceReadError("operational_source_read_timeout", "context")

        monkeypatch.setattr(engine, "reconcile", calculate)
        monkeypatch.setattr(profit_sources, "load_collections", unavailable)
        await visible(database)
        saved = database.data[STATES][OWNER]
        snapshot = saved["engine"]["profit_snapshot"]
        assert saved["revision"] == 2 and len(saved["movements"]) == 1
        assert snapshot["owner_id"] == OWNER and snapshot["as_of"] == STAMP
        assert snapshot["quality"]["complete"] is False
        assert snapshot["quality"]["unknown_is_zero"] is False
        assert snapshot["quality"]["source_errors"] == {"context": "operational_source_read_timeout"}
        assert snapshot["quality"]["reason_codes"] == ["operational_source_read_timeout"]
        assert all(value["amount"] is None for value in snapshot["components"].values())
        assert [(row[0], row[1]) for row in database.writes if row[0] == STATES] == [(STATES, "replace")]
        fully_joined(database)
    asyncio.run(scenario())
