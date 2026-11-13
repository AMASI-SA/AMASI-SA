"""Request-owned cost reads preserve full-cohort evidence and calculation results."""
import asyncio
from copy import deepcopy

from fastapi import HTTPException
from mongomock_motor import AsyncMongoMockClient
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
import pytest

from operational_demand_refresh import LEASES, demand_context, run_visible_refresh
from operational_open_cost_exposure import project
from operational_remaining_bundle_sources import load_remaining_bundles
from order_cost_authority import LIFECYCLES, SNAPSHOTS, load_order_cost_authority
from stock_component_consumption_service import PLANS, UNITS
from test_operational_balance_demand_refresh import DB as LeaseDB, Request, allowed
from test_operational_open_cost_exposure import NOW, native_order, operating_state, seeded_db
from test_operational_bundle_cost_projection import snapshot


INPUTS = {SNAPSHOTS, LIFECYCLES, PLANS}
ORDERS = [{'order_number': 'OLD', 'items': [
    {'order_item_id': 'line', 'product_id': 'p1', 'quantity': 1},
]}]


async def seed_cost(db):
    await db[SNAPSHOTS].insert_one({
        'user_id': 'owner', 'order_number': 'OLD', 'order_item_id': 'line',
        'quantity': 1, 'product_id': 'p1', 'base_unit_cost': 40,
        'line_cost': 40, 'currency': 'SAR', 'cost_complete': True,
    })


def test_historical_cost_keeps_bundle_evidence_from_current_orders():
    """Narrowing to OLD loses NEW's frozen product classification and invents40."""
    async def exercise():
        db = AsyncMongoMockClient()['cross_period_cost_proof']
        data = seeded_db().data
        data['unified_orders'].append(native_order('NEW', created='2026-11-01T07:00:00+00:00'))
        recipe = snapshot(rose=False)
        recipe.update(product_id='p1', selected_options=[], sale_quantity=1, historical_cost_verified=True)
        data[LIFECYCLES] = [{'user_id': 'owner', 'order_number': 'NEW',
            'bundle_intake_snapshots': {'salla:NEW:line': recipe}}]
        for name, rows in data.items():
            if rows:
                await db[name].insert_many(deepcopy(rows))
        before = {name: await db[name].find({}).to_list(None) for name in await db.list_collection_names()}
        result = await project(db, 'owner', operating_state(), as_of=NOW)
        assert 'bundle_snapshot_missing' in {issue['code'] for issue in result['issues']}
        assert result['currencies'] == {} and result['complete'] is False
        assert before == {name: await db[name].find({}).to_list(None) for name in before}
    asyncio.run(exercise())


class MotorBoundary:
    """Real Motor DB identity; every collection operation stays in memory."""
    def __init__(self, monkeypatch):
        self.client = AsyncIOMotorClient('mongodb://127.0.0.1:1', connect=False)
        self.db = self.client['cost_read_test']
        self.other_db = self.client['other_cost_read_test']
        self.memory = AsyncMongoMockClient()['cost_read_test']
        self.leases = LeaseDB()
        self.calls, self.active, self.peak = [], 0, 0
        self.blocked = False
        self.entered, self.all_stopping = asyncio.Event(), asyncio.Event()
        self.release, self.cleanup = asyncio.Event(), asyncio.Event()
        self.cleanup.set()
        self.started, self.stopping = set(), set()
        self.errors = {}
        self.delays = {}
        self.context = None
        boundary = self

        def collection(database, name):
            assert database is boundary.db or database is boundary.other_db
            if name == LEASES:
                return boundary.leases[name]
            original = boundary.memory[name]

            class Collection:
                def find(self, query, projection):
                    assert set(query) >= {'user_id'}
                    boundary.calls.append((name, deepcopy(query)))
                    cursor = original.find(query, projection)

                    class Cursor:
                        async def to_list(self, *args, **kwargs):
                            boundary.active += 1
                            boundary.peak = max(boundary.peak, boundary.active)
                            if name in INPUTS:
                                boundary.started.add(name)
                                if boundary.started == INPUTS:
                                    boundary.entered.set()
                            try:
                                await asyncio.sleep(boundary.delays.get(name, .001))
                                if name in boundary.errors:
                                    raise boundary.errors[name]
                                if boundary.blocked and name in INPUTS:
                                    await boundary.release.wait()
                                return await cursor.to_list(*args, **kwargs)
                            except asyncio.CancelledError:
                                boundary.stopping.add(name)
                                if boundary.stopping >= INPUTS:
                                    boundary.all_stopping.set()
                                await boundary.cleanup.wait()
                                raise
                            finally:
                                boundary.active -= 1
                    return Cursor()
            return Collection()
        monkeypatch.setattr(AsyncIOMotorDatabase, '__getitem__', collection)

    async def load(self, *, database=None, owner='owner', parallel=True):
        return await load_order_cost_authority(self.db if database is None else database, owner, ORDERS,
            include_corrections=False, include_frozen_metadata=True, parallel_reads=parallel)

    async def visible(self, operation, *, database=None, request=None):
        async def run():
            self.context = demand_context()
            return await operation()
        return await run_visible_refresh(request or Request(), self.db if database is None else database,
            'owner', 'staff', allowed, run)

    def settled(self):
        assert self.active == 0
        assert self.leases.data.get(LEASES, {}) == {}
        assert all(name == LEASES for name, _, _ in self.leases.writes)
        assert not [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]


@pytest.mark.parametrize('has_plan', [False, True])
def test_units_read_requires_a_selected_plan_and_keeps_saved_cost(monkeypatch, has_plan):
    async def exercise():
        boundary = MotorBoundary(monkeypatch)
        try:
            await seed_cost(boundary.memory)
            await boundary.memory[UNITS].insert_one({'user_id': 'owner', 'plan_id': 'unrelated-plan',
                'order_line_id': 'line', 'unit_index': 0, 'state': 'consumed'})
            if has_plan:
                await boundary.memory[PLANS].insert_one({'_id': 'selected-plan', 'user_id': 'owner',
                    'order_id': 'OLD', 'bundle_snapshots': {}})
            result = await boundary.visible(boundary.load)
            assert result['lines'][('OLD', 'line')]['cost']['line_total'] == 40
            units = [query for name, query in boundary.calls if name == UNITS]
            assert units == ([{'user_id': 'owner', 'plan_id': {'$in': ['selected-plan']},
                'state': {'$ne': 'released'}, 'order_line_id': {'$in': ['line']}}] if has_plan else [])
            assert boundary.peak == 3
            boundary.settled()
        finally:
            boundary.client.close()
    asyncio.run(exercise())


def test_opt_in_matches_serial_result_and_is_bounded_to_three_reads(monkeypatch):
    async def exercise():
        boundary = MotorBoundary(monkeypatch)
        try:
            await seed_cost(boundary.memory)
            before = await boundary.memory[SNAPSHOTS].find({}).to_list(None)
            serial = await boundary.visible(lambda: boundary.load(parallel=False))
            assert boundary.peak == 1
            serial_calls = deepcopy(boundary.calls)
            boundary.calls.clear()
            boundary.peak = 0
            concurrent = await boundary.visible(boundary.load)
            assert concurrent == serial and concurrent['lines'][('OLD', 'line')]['cost']['line_total'] == 40
            assert boundary.calls == serial_calls and boundary.peak == 3
            assert await boundary.memory[SNAPSHOTS].find({}).to_list(None) == before
            boundary.settled()
        finally:
            boundary.client.close()
    asyncio.run(exercise())


def test_remaining_bundle_adapter_opts_in_only_inside_visible_request(monkeypatch):
    async def exercise():
        boundary = MotorBoundary(monkeypatch)
        try:
            raw = native_order()['raw_by_source']['salla_direct']
            async def load():
                return await load_remaining_bundles(boundary.db, 'owner', [raw], NOW)
            assert await load() == {} and boundary.peak == 1
            boundary.peak = 0
            assert await boundary.visible(load) == {} and boundary.peak == 3
            boundary.settled()
        finally:
            boundary.client.close()
    asyncio.run(exercise())


@pytest.mark.parametrize('kind', ['public_session', 'private_session', 'slots_session', 'nested', 'wrong_db', 'wrong_owner', 'no_context'])
def test_snapshot_wrappers_and_unowned_calls_stay_serial(monkeypatch, kind):
    async def exercise():
        boundary = MotorBoundary(monkeypatch)
        try:
            class Wrapper:
                def __init__(self, database):
                    self.db = database
                def __getitem__(self, name):
                    return self.db[name]
            class Slots:
                __slots__ = ('__db', '__session')
                def __init__(self, database):
                    self.__db, self.__session = database, object()
                def __getitem__(self, name):
                    return self.__db[name]
            class Private(Wrapper):
                def __init__(self, database):
                    super().__init__(database)
                    self.__session = object()
            database = boundary.db
            if kind == 'public_session':
                database = Wrapper(database)
                database.session = object()
            elif kind == 'private_session':
                database = Private(database)
            elif kind == 'slots_session':
                database = Slots(database)
            elif kind == 'nested':
                database = Wrapper(Slots(database))
            if kind == 'no_context':
                await boundary.load()
            elif kind == 'wrong_db':
                await boundary.visible(lambda: boundary.load(database=boundary.other_db))
            elif kind == 'wrong_owner':
                await boundary.visible(lambda: boundary.load(owner='different-owner'))
            else:
                await boundary.visible(lambda: boundary.load(database=database), database=database)
            assert boundary.peak == 1
            boundary.settled()
        finally:
            boundary.client.close()
    asyncio.run(exercise())


def test_read_limit_error_is_preserved_and_sibling_reads_are_joined(monkeypatch):
    async def exercise():
        boundary = MotorBoundary(monkeypatch)
        try:
            for _ in range(5):
                await seed_cost(boundary.memory)
            boundary.delays = {SNAPSHOTS: .003, LIFECYCLES: .05, PLANS: .05}
            with pytest.raises(ValueError, match='^order_cost_authority_read_limit:' + SNAPSHOTS + '$'):
                await boundary.visible(boundary.load)
            assert boundary.peak == 3
            assert boundary.stopping == {LIFECYCLES, PLANS}
            boundary.settled()
        finally:
            boundary.client.close()
    asyncio.run(exercise())


def test_parallel_failures_keep_the_original_serial_error_order(monkeypatch):
    async def exercise():
        boundary = MotorBoundary(monkeypatch)
        try:
            stored_error = ValueError('stored-source-rejected')
            boundary.errors = {SNAPSHOTS: stored_error, PLANS: ValueError('later-plan-source-rejected')}
            boundary.delays = {SNAPSHOTS: .01, PLANS: 0, LIFECYCLES: .02}
            with pytest.raises(ValueError) as failure:
                await boundary.visible(boundary.load)
            assert failure.value is stored_error
            assert LIFECYCLES in boundary.stopping
            boundary.settled()
        finally:
            boundary.client.close()
    asyncio.run(exercise())


@pytest.mark.parametrize('repeated', [False, True])
def test_disconnect_joins_all_reads_even_during_repeated_cancellation(monkeypatch, repeated):
    async def exercise():
        boundary = MotorBoundary(monkeypatch)
        task = None
        try:
            boundary.blocked = True
            boundary.cleanup.clear()
            request = Request()
            task = asyncio.create_task(boundary.visible(boundary.load, request=request))
            await asyncio.wait_for(boundary.entered.wait(), 2)
            request.disconnect()
            await asyncio.wait_for(boundary.all_stopping.wait(), 2)
            if repeated:
                boundary.context.work.cancel()
                task.cancel()
                await asyncio.sleep(0)
            assert not task.done() and boundary.active == 3
            boundary.cleanup.set()
            with pytest.raises(HTTPException) as failure:
                await asyncio.wait_for(task, 2)
            assert failure.value.status_code == 499
            assert boundary.stopping == INPUTS
            boundary.settled()
        finally:
            boundary.cleanup.set()
            boundary.release.set()
            if task is not None and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            boundary.client.close()
    asyncio.run(exercise())
