"""Operational profit opts into the existing authority reader, never new math.

The real reducer/authority/owner guard run against intercepted Motor collection
operations. Only the database boundary is replaced; no socket is opened.
"""
import asyncio
from copy import deepcopy
import json

from fastapi import HTTPException
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
import pytest

import dashboard_v2_routes as dashboard
from operational_demand_refresh import LEASES, demand_context, run_visible_refresh
from order_cost_authority import CORRECTIONS, LIFECYCLES, SNAPSHOTS, load_order_cost_authority
from order_cost_reporting_repair import source_authority_evidence_hash
from review_acceptance_config_guard import AcceptanceConfigDatabase
from stock_component_consumption_service import PLANS, UNITS
from test_dashboard_bundle_cost_authority import OWNER, db as bundle_db, freeze, sale, sale_line
from test_operational_balance_demand_refresh import DB as LeaseDB, Request, allowed
from test_operational_read_runtime import ThreadedMotorCursor


INPUTS = {SNAPSHOTS, LIFECYCLES, PLANS}
ESTIMATE_CATALOG_SELECTOR = "owned_bundle_estimate_catalog"
# This fixture's shawl recipe includes its parent and the rose component.
# Pin the owned estimate's query/projection rather than its position among
# unrelated dashboard alias and selected-document catalog reads.
ESTIMATE_CATALOG_QUERY = {
    "user_id": OWNER,
    "$or": [
        {"salla_product_id": {"$in": ["rose", "shawl"]}},
        {"mezan_product_id": {"$in": ["rose", "shawl"]}},
        {"id": {"$in": ["rose", "shawl"]}},
    ],
}
ESTIMATE_CATALOG_PROJECTION = {
    "_id": 0, "id": 1, "salla_product_id": 1,
    "mezan_product_id": 1, "name": 1, "options": 1,
    "raw_salla": 1, "raw_salla_details": 1, "variants": 1,
    "skus": 1, "variants_count": 1,
    "cost_price_from_salla": 1, "cost_price": 1, "cost": 1,
}


class ProductBoundary:
    """Preserve exact Motor identity and capture query/projection/limit contracts."""
    def __init__(self, monkeypatch, memory):
        self.client = AsyncIOMotorClient('mongodb://127.0.0.1:1', connect=False)
        self.db, self.other_db = self.client['profit_reads'], self.client['other_profit_reads']
        self.memory, self.leases = memory, LeaseDB()
        self.calls = []
        self.active = self.authority_active = self.authority_peak = 0
        self.blocked = False
        self.started, self.stopping = set(), set()
        self.entered, self.all_stopping = asyncio.Event(), asyncio.Event()
        self.release, self.cleanup = asyncio.Event(), asyncio.Event()
        self.cleanup.set()
        self.context = None
        self.errors, self.delays = {}, {}
        boundary = self

        def collection(database, name):
            assert database is boundary.db or database is boundary.other_db
            if name == LEASES:
                return boundary.leases[name]
            original = memory[name]

            def observed(cursor, method, query, projection=None):
                class Cursor:
                    async def to_list(self, length=None):
                        boundary.calls.append((name, method, deepcopy(query), deepcopy(projection), length))
                        boundary.active += 1
                        if name in INPUTS:
                            boundary.authority_active += 1
                            boundary.authority_peak = max(boundary.authority_peak, boundary.authority_active)
                            boundary.started.add(name)
                            if boundary.started == INPUTS:
                                boundary.entered.set()
                        try:
                            await asyncio.sleep(boundary.delays.get(name, 0))
                            if name in boundary.errors:
                                raise boundary.errors[name]
                            if boundary.blocked and name in INPUTS:
                                await boundary.release.wait()
                            return await cursor.to_list(length=length)
                        except asyncio.CancelledError:
                            if name in INPUTS:
                                boundary.stopping.add(name)
                                if boundary.stopping == INPUTS:
                                    boundary.all_stopping.set()
                                await boundary.cleanup.wait()
                            raise
                        finally:
                            boundary.active -= 1
                            if name in INPUTS:
                                boundary.authority_active -= 1

                    def __aiter__(self):
                        async def iterate():
                            for row in await self.to_list(None):
                                yield row
                        return iterate()
                return Cursor()

            class Collection:
                def find(self, query, projection=None):
                    assert isinstance(query.get('user_id'), str)
                    return observed(original.find(query, projection), 'find', query, projection)

                def aggregate(self, pipeline):
                    assert isinstance(pipeline[0]['$match'].get('user_id'), str)
                    return observed(original.aggregate(pipeline), 'aggregate', pipeline)

                def __getattr__(self, operation):
                    raise AssertionError('Unexpected operation: ' + operation)
            return Collection()

        monkeypatch.setattr(AsyncIOMotorDatabase, '__getitem__', collection)

    async def visible(self, operation, *, database=None, request=None):
        async def work():
            self.context = demand_context()
            return await operation()
        return await run_visible_refresh(request or Request(), self.db if database is None else database,
                                         OWNER, 'synthetic-staff', allowed, work)

    def settled(self):
        assert self.active == self.authority_active == 0
        assert self.leases.data.get(LEASES, {}) == {}
        assert all(name == LEASES for name, _, _ in self.leases.writes)
        assert not [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]


async def mixed_orders(db):
    frozen = sale('frozen', sale_line('frozen-line', 4), actual_partial_refund_amount=100)
    corrected = sale('corrected', sale_line('corrected-line', 2, product='plain'))
    await freeze(db, frozen)
    await freeze(db, corrected)
    # Applied reporting corrections remain part of the profit authority.
    authority = await load_order_cost_authority(db, OWNER, [corrected])
    original = authority['lines'][('corrected', 'corrected-line')]
    correction = deepcopy(original)
    correction.update(user_id=OWNER, order_number='corrected', order_item_id='corrected-line',
                      state='applied', source_authority_evidence_hash=source_authority_evidence_hash(original))
    correction['cost'].update(base_total=26.0, line_total=26.0)
    await db[CORRECTIONS].insert_one(correction)
    # A selected plan keeps the exact units query in both paths.
    await db[PLANS].insert_one({'_id': 'selected-plan', 'user_id': OWNER,
                               'order_id': 'frozen', 'bundle_snapshots': {}})
    await db[UNITS].insert_one({'user_id': 'different-owner', 'plan_id': 'selected-plan',
                               'order_line_id': 'frozen-line', 'state': 'consumed', 'unit_index': 1})
    return [frozen, corrected,
            sale('ordinary', sale_line('plain-line', 2, product='plain')),
            sale('current-estimate', sale_line('current-line', 3)),
            sale('missing', {'order_item_id': 'missing-line', 'product_id': 'unpriced', 'quantity': 1, 'total': 25}),
            sale('cancelled', sale_line('cancelled-line', 2, product='plain'), order_status='ملغي')]


async def documents(db):
    return {name: await db[name].find({}).to_list(None) for name in await db.list_collection_names()}


@pytest.mark.asyncio
@pytest.mark.parametrize('adapter', ['raw', 'acceptance'])
@pytest.mark.parametrize('include_product_rows', [False, True])
async def test_opt_in_keeps_full_product_result_queries_and_saved_evidence(monkeypatch, bundle_db, adapter, include_product_rows):
    orders = await mixed_orders(bundle_db)
    original_orders, before = deepcopy(orders), await documents(bundle_db)
    boundary = ProductBoundary(monkeypatch, bundle_db)
    try:
        db = boundary.db if adapter == 'raw' else AcceptanceConfigDatabase(boundary.db)
        async def load(**options):
            return await dashboard.build_mezan_v2_product_cost_cooperative(
                db, OWNER, orders, include_product_rows=include_product_rows, **options)
        serial = await boundary.visible(load, database=db)
        assert boundary.authority_peak == 1
        serial_calls = deepcopy(boundary.calls)
        boundary.calls.clear()
        boundary.authority_peak = 0
        parallel = await boundary.visible(lambda: load(parallel_order_cost_reads=True), database=db)
        assert parallel == serial
        assert json.dumps(parallel, ensure_ascii=False) == json.dumps(serial, ensure_ascii=False)
        assert parallel['total'] == 374  # frozen165 + corrected26 + ordinary18 + current165
        assert parallel['missing_products_count'] == parallel['incomplete_orders_count'] == 1
        assert boundary.calls == serial_calls
        assert boundary.authority_peak == 3
        assert any(name == CORRECTIONS for name, *_ in boundary.calls)
        assert [call for call in boundary.calls if call[0] == UNITS][0][2]['plan_id'] == {'$in': ['selected-plan']}
        assert orders == original_orders and await documents(bundle_db) == before
        boundary.settled()
    finally:
        boundary.client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['default', 'disabled', 'truthy', 'no_context', 'wrong_db', 'wrong_owner',
                                 'public_session', 'private_session', 'nested_acceptance'])
async def test_default_and_unsupported_profit_readers_keep_serial_authority(monkeypatch, bundle_db, mode):
    boundary = ProductBoundary(monkeypatch, bundle_db)
    try:
        class Wrapper:
            def __init__(self, db):
                self.db = db
                self.__session = object()
            def __getitem__(self, name):
                return self.db[name]
            def __getattr__(self, name):
                return self[name]
        db = boundary.other_db if mode == 'wrong_db' else boundary.db
        if mode in {'public_session', 'private_session'}:
            db = Wrapper(db)
            if mode == 'public_session':
                db.session = object()
        elif mode == 'nested_acceptance':
            db = AcceptanceConfigDatabase(AcceptanceConfigDatabase(db))
        owner = 'different-owner' if mode == 'wrong_owner' else OWNER
        options = {} if mode == 'default' else {'parallel_order_cost_reads':
            False if mode == 'disabled' else 1 if mode == 'truthy' else True}
        async def load():
            return await dashboard.build_mezan_v2_product_cost_cooperative(
                db, owner, [sale('serial', sale_line('line', product='plain'))],
                include_product_rows=False, **options)
        if mode == 'no_context':
            await load()
        else:
            await boundary.visible(load, database=boundary.db if mode == 'wrong_db' else db)
        assert boundary.authority_peak == 1
        assert [call for call in boundary.calls if call[0] == UNITS][0][2]['plan_id'] == {'$in': []}
        boundary.settled()
    finally:
        boundary.client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('adapter', ['raw', 'acceptance'])
async def test_profit_authority_inputs_overlap_before_any_is_released(monkeypatch, bundle_db, adapter):
    boundary = ProductBoundary(monkeypatch, bundle_db)
    boundary.blocked = True
    task = None
    try:
        db = boundary.db if adapter == 'raw' else AcceptanceConfigDatabase(boundary.db)
        task = asyncio.create_task(boundary.visible(lambda: dashboard.build_mezan_v2_product_cost_cooperative(
            db, OWNER, [sale('overlap', sale_line('line', product='plain'))],
            include_product_rows=False, parallel_order_cost_reads=True), database=db))
        await asyncio.wait_for(boundary.entered.wait(), 2)
        assert boundary.authority_active == boundary.authority_peak == 3 and not task.done()
        boundary.release.set()
        assert (await task)['total'] == 9
        boundary.settled()
    finally:
        boundary.release.set()
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        boundary.client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('repeated', [False, True])
async def test_profit_disconnect_waits_for_all_authority_cleanup(monkeypatch, bundle_db, repeated):
    boundary = ProductBoundary(monkeypatch, bundle_db)
    boundary.blocked = True
    boundary.cleanup.clear()
    request, task = Request(), None
    try:
        task = asyncio.create_task(boundary.visible(lambda: dashboard.build_mezan_v2_product_cost_cooperative(
            boundary.db, OWNER, [sale('cancel', sale_line('line', product='plain'))],
            include_product_rows=False, parallel_order_cost_reads=True), request=request))
        await asyncio.wait_for(boundary.entered.wait(), 2)
        request.disconnect()
        await asyncio.wait_for(boundary.all_stopping.wait(), 2)
        if repeated:
            boundary.context.work.cancel()
            task.cancel()
            await asyncio.sleep(0)
        assert boundary.active == 3 and not task.done()
        boundary.cleanup.set()
        with pytest.raises(HTTPException) as error:
            await asyncio.wait_for(task, 2)
        assert error.value.status_code == 499 and boundary.stopping == INPUTS
        boundary.settled()
    finally:
        boundary.cleanup.set()
        boundary.release.set()
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        boundary.client.close()


@pytest.mark.asyncio
async def test_profit_authority_keeps_serial_error_priority_and_drains_siblings(monkeypatch, bundle_db):
    boundary = ProductBoundary(monkeypatch, bundle_db)
    original = ValueError('stored-input-invalid')
    boundary.errors = {SNAPSHOTS: original, PLANS: ValueError('later-plan-input-invalid')}
    boundary.delays = {SNAPSHOTS: .01, LIFECYCLES: .05}
    try:
        with pytest.raises(ValueError) as error:
            await boundary.visible(lambda: dashboard.build_mezan_v2_product_cost_cooperative(
                boundary.db, OWNER, [sale('error', sale_line('line', product='plain'))],
                include_product_rows=False, parallel_order_cost_reads=True))
        assert error.value is original
        assert boundary.authority_peak == 3 and LIFECYCLES in boundary.stopping
        assert not any(name == CORRECTIONS for name, *_ in boundary.calls)
        boundary.settled()
    finally:
        boundary.client.close()


def threaded_reads(monkeypatch, boundary, overrides):
    """Override only selected physical reads; keep real guard/reducer/queries."""
    original = AsyncIOMotorDatabase.__getitem__
    counts = {}

    def collection(database, name):
        normal = original(database, name)
        class Collection:
            def select(self, method, args):
                counts[name] = counts.get(name, 0) + 1
                query = args[0] if method == 'find' else args[0][0]['$match']
                selector = counts[name]
                if (name == dashboard.PRODUCTS and method == 'find'
                        and query == ESTIMATE_CATALOG_QUERY
                        and args[1] == ESTIMATE_CATALOG_PROJECTION):
                    selector = ESTIMATE_CATALOG_SELECTOR
                cursor = overrides.get((name, selector))
                if cursor is None:
                    return getattr(normal, method)(*args)
                assert query['user_id'] == OWNER
                return cursor
            def find(self, query, projection=None):
                return self.select('find', (query, projection))
            def aggregate(self, pipeline):
                return self.select('aggregate', (pipeline,))
            def __getattr__(self, method):
                return getattr(normal, method)
        return Collection()
    monkeypatch.setattr(AsyncIOMotorDatabase, '__getitem__', collection)
    return counts


@pytest.mark.asyncio
async def test_estimate_catalog_interception_excludes_unrelated_direct_reads(monkeypatch, bundle_db):
    product = await bundle_db[dashboard.PRODUCTS].find_one({"user_id": OWNER, "salla_product_id": "shawl"})
    boundary = ProductBoundary(monkeypatch, bundle_db)
    owned_cursor = object()
    counts = threaded_reads(monkeypatch, boundary, {
        (dashboard.PRODUCTS, ESTIMATE_CATALOG_SELECTOR): owned_cursor,
    })
    try:
        unrelated_reads = [
            ({"user_id": OWNER}, dashboard.PRODUCT_COST_ALIAS_PROJECTION),
            ({"user_id": OWNER, "_id": {"$in": [product["_id"]]}},
             {**dashboard.PRODUCT_COST_CATALOG_PROJECTION, "_id": 1}),
            ({**ESTIMATE_CATALOG_QUERY, "user_id": "different-owner"}, ESTIMATE_CATALOG_PROJECTION),
            (ESTIMATE_CATALOG_QUERY, {**ESTIMATE_CATALOG_PROJECTION, "_id": 1}),
            ({**ESTIMATE_CATALOG_QUERY, "extra_scope": True}, ESTIMATE_CATALOG_PROJECTION),
        ]
        for query, projection in unrelated_reads:
            assert boundary.db[dashboard.PRODUCTS].find(query, projection) is not owned_cursor
        # The exact owned read is selected even after five unrelated reads;
        # there is no implicit assumption that it is the second/third query.
        assert boundary.db[dashboard.PRODUCTS].find(
            deepcopy(ESTIMATE_CATALOG_QUERY), deepcopy(ESTIMATE_CATALOG_PROJECTION),
        ) is owned_cursor
        assert counts[dashboard.PRODUCTS] == len(unrelated_reads) + 1
    finally:
        boundary.client.close()


async def cleanup_workers(boundary, task, cursors):
    for cursor in cursors:
        cursor.release.set()
        cursor.close_release.set()
    if task is not None and not task.done():
        task.cancel()
    if task is not None:
        await asyncio.gather(task, return_exceptions=True)
    for cursor in cursors:
        await asyncio.gather(*cursor.operations, return_exceptions=True)
    boundary.client.close()


async def callbacks():
    for _ in range(20):
        await asyncio.sleep(0)


@pytest.mark.asyncio
@pytest.mark.parametrize('adapter', ['raw', 'acceptance'])
@pytest.mark.parametrize('reader', ['product_builder', 'full_snapshots'])
async def test_owned_profit_authority_joins_motor_workers_and_close_before_request_release(monkeypatch, bundle_db, adapter, reader):
    boundary = ProductBoundary(monkeypatch, bundle_db)
    cursors = {name: ThreadedMotorCursor([[], []], block_close=True) for name in INPUTS}
    threaded_reads(monkeypatch, boundary, {(name, 1): cursor for name, cursor in cursors.items()})
    task = None
    try:
        db = boundary.db if adapter == 'raw' else AcceptanceConfigDatabase(boundary.db)
        orders = [sale('worker', sale_line('line', product='plain'))]
        async def load():
            if reader == 'full_snapshots':
                return await load_order_cost_authority(db, OWNER, orders, include_frozen_metadata=True, parallel_reads=True)
            return await dashboard.build_mezan_v2_product_cost_cooperative(
                db, OWNER, orders, include_product_rows=False, parallel_order_cost_reads=True)
        task = asyncio.create_task(boundary.visible(load, database=db))
        await asyncio.wait_for(asyncio.gather(*(cursor.entered.wait() for cursor in cursors.values())), 1)
        task.cancel()
        await callbacks()
        assert not task.done() and boundary.leases.data[LEASES]
        for cursor in cursors.values():
            assert not cursor.worker_finished.is_set() and cursor.close_calls == 0
        boundary.context.work.cancel()
        await callbacks()
        assert not task.done() and boundary.leases.data[LEASES]
        for cursor in cursors.values():
            cursor.release.set()
        await asyncio.wait_for(asyncio.gather(*(cursor.close_entered.wait() for cursor in cursors.values())), 1)
        boundary.context.work.cancel()
        await callbacks()
        assert not task.done() and boundary.leases.data[LEASES]
        for cursor in cursors.values():
            assert cursor.worker_finished.is_set() and not cursor.closed_during_read
            assert len(cursor.operations) == cursor.close_calls == 1
            cursor.close_release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        boundary.settled()
    finally:
        await cleanup_workers(boundary, task, cursors.values())


@pytest.mark.asyncio
@pytest.mark.parametrize('phase', ['definitions', 'units', 'corrections', 'estimate_definitions',
                                 'estimate_catalog', 'estimate_inputs', 'estimate_resources'])
async def test_every_owned_authority_phase_drains_motor_before_lease_release(monkeypatch, bundle_db, phase):
    from bundle_catalog_v2 import BUNDLES
    from product_fulfillment_rules import PRODUCT_RESOURCE_BINDINGS
    from product_option_cost_routes import BINDINGS, RESOURCES
    from product_v2_details_routes import COST_PROFILES
    from product_v2_routes import PRODUCTS

    order = sale('worker-phase', sale_line('line', product='shawl' if phase.startswith('estimate_') else 'plain'))
    if phase == 'units':
        await bundle_db[PLANS].insert_one({'_id': 'phase-plan', 'user_id': OWNER,
                                         'order_id': order['order_number'], 'bundle_snapshots': {}})
    if phase == 'estimate_resources':
        await bundle_db[PRODUCT_RESOURCE_BINDINGS].insert_one({'user_id': OWNER, 'id': 'wrapping-binding',
            'salla_product_id': 'shawl', 'resource_id': 'wrapping', 'quantity': 1})
        await bundle_db[RESOURCES].insert_one({'user_id': OWNER, 'id': 'wrapping', 'unit_cost': 2})
    keys = {
        'definitions': [(BUNDLES, 1)], 'units': [(UNITS, 1)], 'corrections': [(CORRECTIONS, 1)],
        'estimate_definitions': [(BUNDLES, 2)], 'estimate_catalog': [(PRODUCTS, ESTIMATE_CATALOG_SELECTOR)],
        'estimate_inputs': [(name, 2) for name in (COST_PROFILES, BINDINGS, PRODUCT_RESOURCE_BINDINGS)],
        'estimate_resources': [(RESOURCES, 2)],
    }[phase]
    boundary = ProductBoundary(monkeypatch, bundle_db)
    cursors = [ThreadedMotorCursor([[], []], block_close=True) for _ in keys]
    threaded_reads(monkeypatch, boundary, dict(zip(keys, cursors)))
    task = None
    try:
        task = asyncio.create_task(boundary.visible(lambda: dashboard.build_mezan_v2_product_cost_cooperative(
            boundary.db, OWNER, [order], include_product_rows=False, parallel_order_cost_reads=True)))
        await asyncio.wait_for(asyncio.gather(*(cursor.entered.wait() for cursor in cursors)), 1)
        task.cancel()
        await callbacks()
        assert not task.done() and boundary.leases.data[LEASES]
        for cursor in cursors:
            assert not cursor.worker_finished.is_set() and cursor.close_calls == 0
            cursor.release.set()
        await asyncio.wait_for(asyncio.gather(*(cursor.close_entered.wait() for cursor in cursors)), 1)
        boundary.context.work.cancel()
        await callbacks()
        assert not task.done() and boundary.leases.data[LEASES]
        for cursor in cursors:
            assert cursor.worker_finished.is_set() and cursor.close_calls == 1 and not cursor.closed_during_read
            cursor.close_release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert all(len(cursor.operations) == 1 and all(op.done() for op in cursor.operations) for cursor in cursors)
        boundary.settled()
    finally:
        await cleanup_workers(boundary, task, cursors)


@pytest.mark.asyncio
@pytest.mark.parametrize('phase', ['historical_inputs', 'estimate_inputs'])
async def test_owned_authority_error_waits_for_motor_siblings_and_preserves_error_order(monkeypatch, bundle_db, phase):
    from product_fulfillment_rules import PRODUCT_RESOURCE_BINDINGS
    from product_option_cost_routes import BINDINGS
    from product_v2_details_routes import COST_PROFILES

    boundary = ProductBoundary(monkeypatch, bundle_db)
    if phase == 'historical_inputs':
        held = [(LIFECYCLES, 1), (PLANS, 1)]
        failed = (SNAPSHOTS, 1)
    else:
        held = [(COST_PROFILES, 2), (BINDINGS, 2)]
        failed = (PRODUCT_RESOURCE_BINDINGS, 2)  # Last argument fails first; retain gather semantics.
    cursors = [ThreadedMotorCursor([[], []], block_close=True) for _ in held]
    error = ValueError('synthetic-owned-input-failure')
    # The earlier gather argument fails later while closing. It must never
    # replace the already-observed last-argument error or escape cleanup.
    original_close = cursors[0]._async_close
    secondary_closed = []
    async def close_with_secondary_error():
        await original_close()
        secondary_closed.append(True)
        raise ValueError('later-cleanup-failure')
    cursors[0]._async_close = close_with_secondary_error
    class ErrorCursor:
        async def to_list(self, length):
            await asyncio.gather(*(cursor.entered.wait() for cursor in cursors))
            raise error
    threaded_reads(monkeypatch, boundary, {**dict(zip(held, cursors)), failed: ErrorCursor()})
    task = None
    try:
        task = asyncio.create_task(boundary.visible(lambda: dashboard.build_mezan_v2_product_cost_cooperative(
            boundary.db, OWNER, [sale('worker-error', sale_line('line',
                product='shawl' if phase == 'estimate_inputs' else 'plain'))],
            include_product_rows=False, parallel_order_cost_reads=True)))
        await asyncio.wait_for(asyncio.gather(*(cursor.entered.wait() for cursor in cursors)), 1)
        await callbacks()
        assert not task.done() and boundary.leases.data[LEASES]
        for cursor in cursors:
            assert not cursor.worker_finished.is_set()
            cursor.release.set()
        await asyncio.wait_for(asyncio.gather(*(cursor.close_entered.wait() for cursor in cursors)), 1)
        assert not task.done() and boundary.leases.data[LEASES]
        for cursor in cursors:
            assert cursor.worker_finished.is_set() and not cursor.closed_during_read
            cursor.close_release.set()
        with pytest.raises(ValueError) as failure:
            await task
        assert failure.value is error
        assert secondary_closed == [True]
        assert all(len(cursor.operations) == cursor.close_calls == 1 for cursor in cursors)
        boundary.settled()
    finally:
        await cleanup_workers(boundary, task, cursors)


@pytest.mark.asyncio
async def test_profit_source_deadline_joins_authority_workers_before_reporting_timeout(monkeypatch, bundle_db):
    from operational_read_runtime import SourceReadError, load_collections, source_read_budget
    boundary = ProductBoundary(monkeypatch, bundle_db)
    cursors = {name: ThreadedMotorCursor([[], []], block_close=True) for name in INPUTS}
    threaded_reads(monkeypatch, boundary, {(name, 1): cursor for name, cursor in cursors.items()})
    task = None
    async def reader(db, owner, name):
        assert name == 'products'
        return await dashboard.build_mezan_v2_product_cost_cooperative(
            db, owner, [sale('deadline', sale_line('line', product='plain'))],
            include_product_rows=False, parallel_order_cost_reads=True)
    async def load():
        with source_read_budget(.2):
            return await load_collections(boundary.db, OWNER, ['products'], reader=reader, per_read_timeout=.03)
    try:
        task = asyncio.create_task(boundary.visible(load))
        await asyncio.wait_for(asyncio.gather(*(cursor.entered.wait() for cursor in cursors.values())), 1)
        await asyncio.sleep(.05)
        assert not task.done() and boundary.leases.data[LEASES]
        for cursor in cursors.values():
            assert not cursor.worker_finished.is_set() and cursor.close_calls == 0
            assert len(cursor.driver_budgets) == 1 and 0 < cursor.driver_budgets[0] <= .03
            cursor.release.set()
        await asyncio.wait_for(asyncio.gather(*(cursor.close_entered.wait() for cursor in cursors.values())), 1)
        assert not task.done() and boundary.leases.data[LEASES]
        for cursor in cursors.values():
            assert cursor.worker_finished.is_set() and not cursor.closed_during_read
            cursor.close_release.set()
        with pytest.raises(SourceReadError) as error:
            await task
        assert (error.value.collection, error.value.code) == ('products', 'operational_source_read_timeout')
        assert all(len(cursor.operations) == cursor.close_calls == 1 for cursor in cursors.values())
        boundary.settled()
    finally:
        await cleanup_workers(boundary, task, cursors.values())
