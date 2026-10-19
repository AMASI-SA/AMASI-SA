"""Isolated-Mongo cost-loop correctness; parser5 stays fixed in HTTP candidates."""
import asyncio
import copy
import json
import os
from pathlib import Path
from contextlib import contextmanager
from unittest.mock import patch
from motor.motor_asyncio import AsyncIOMotorClient

import pytest
import dashboard_v2_routes as dash
import dashboard_b_computation_experiment as computation
from dashboard_b_cost_cooperative_experiment import candidate, cost_builder
from dashboard_b_equivalence_fixtures import CASES, DEFAULT_KWARGS, OWNER, seed_mixed
from test_dashboard_b_baseline_mongo import populate
from test_dashboard_b_concurrency_gate import CASES as MUTATIONS, Gate, GatedDB
from test_dashboard_b_request_scope_mongo import isolated, invoke, wire


@contextmanager
def configured(budget):
    def factory(db, scope):
        return candidate(db, scope, cost_budget_ms=budget)
    with patch.object(computation, 'candidate', factory):
        yield


async def rows_for(db, owner=OWNER['id']):
    return await db.unified_orders.find({'user_id': owner}, {'_id': 0}).to_list(1000)


@pytest.mark.asyncio
@pytest.mark.parametrize('budget', [1, 5, 10])
async def test_cost_mixed_exact_runtime_equivalence(budget):
    async with isolated() as (raw, db, commands):
        await seed_mixed(raw)
        rows = await rows_for(db)
        before = wire(rows)
        commands.reset()
        expected = await dash.build_mezan_v2_product_cost(db, OWNER['id'], rows)
        reads, docs = dict(commands.counts), dict(commands.docs)
        assert not commands.writes
        commands.reset()
        actual = await cost_builder(budget_ms=budget)(db, OWNER['id'], rows)
        assert wire(actual) == wire(expected)
        assert wire(rows) == before
        assert dict(commands.counts) == reads and dict(commands.docs) == docs
        assert not commands.writes


@pytest.mark.asyncio
@pytest.mark.parametrize('budget', [.000001, 1, 5, 10])
@pytest.mark.parametrize('case', ['repeated_product', 'duplicate_order_id', 'sort_ties',
                                  'first_image', 'rounding', 'mixed_currency',
                                  'malformed_items', 'zero_missing_refund'])
async def test_cost_accumulator_edge_equivalence(budget, case):
    async with isolated() as (raw, db, commands):
        await populate(raw, 4)
        rows = await rows_for(db)
        if case == 'repeated_product':
            rows = rows[:1]
            rows[0]['products'] *= 3
        elif case == 'duplicate_order_id':
            rows = [copy.deepcopy(rows[0]), copy.deepcopy(rows[0])]
        elif case == 'sort_ties':
            await raw[dash.PRODUCTS].update_many({'user_id': OWNER['id']}, {'$set': {'name': 'Equal'}})
            rows.reverse()
        elif case == 'first_image':
            rows = [copy.deepcopy(rows[0]) for _ in range(3)]
            rows[1]['products'][0]['image_url'] = 'fixture-first-image'
            rows[2]['products'][0]['image_url'] = 'fixture-later-image'
        elif case == 'rounding':
            rows = [copy.deepcopy(rows[0]) for _ in range(12)]
            for index, row in enumerate(rows):
                row['products'][0].update(quantity=[.125, 1, 3][index % 3], price=[.005, .015, 100000000.01][index % 3])
                row['actual_partial_refund_amount'] = .33
        elif case == 'mixed_currency':
            rows = [copy.deepcopy(rows[0]), copy.deepcopy(rows[0])]
            rows[1].update(currency='KWD', raw_by_source={}, total_amount_sar=None)
        elif case == 'malformed_items':
            rows[0]['products'] = []
            rows[1]['products'] = [None, 'malformed', 17]
            rows[2]['products'].insert(0, None)
        else:
            await raw[dash.COST_PROFILES].update_one({'salla_product_id': '0'}, {'$set': {'base_cost': 0}})
            await raw[dash.COST_PROFILES].delete_one({'salla_product_id': '1'})
            await raw[dash.PRODUCTS].update_one({'salla_product_id': '1'}, {'$unset': {'cost_price': ''}})
            rows[0]['order_status'] = rows[1]['order_status'] = 'cancelled'
            rows[2]['actual_partial_refund_amount'] = 1000
        snapshot = wire(rows)
        expected = await dash.build_mezan_v2_product_cost(db, OWNER['id'], rows)
        actual = await cost_builder(budget_ms=budget)(db, OWNER['id'], rows)
        assert wire(actual) == wire(expected), case
        assert wire(rows) == snapshot
        if case in {'repeated_product', 'duplicate_order_id'}:
            assert actual['product_rows'][0]['orders_count'] == (1 if case == 'repeated_product' else 2)
        elif case == 'first_image':
            assert actual['product_rows'][0]['image_url'] == 'fixture-first-image'
        elif case == 'sort_ties':
            assert [row['identity'] for row in actual['product_rows']] == ['3', '2', '1', '0']
        elif case == 'mixed_currency':
            assert actual['product_rows'][0]['total_sales'] is None
            assert actual['product_rows'][0]['net_profit'] is None
        elif case == 'malformed_items':
            assert actual['no_products_orders_count'] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('bad', [None, 17, {'products': 7}])
async def test_cost_malformed_exception_parity(bad):
    async with isolated() as (raw, db, commands):
        await populate(raw, 1)
        with pytest.raises(Exception) as expected:
            await dash.build_mezan_v2_product_cost(db, OWNER['id'], [bad])
        with pytest.raises(type(expected.value)) as actual:
            await cost_builder(budget_ms=5)(db, OWNER['id'], [bad])
        assert str(actual.value) == str(expected.value)


@pytest.mark.asyncio
@pytest.mark.parametrize('case', CASES, ids=lambda case: case['name'])
async def test_dashboard_mixed_cost_only_equivalence(case):
    async with isolated() as (raw, db, commands):
        await seed_mixed(raw)
        if case['settings']:
            await raw.settings.update_one({'user_id': OWNER['id']}, {'$set': case['settings']})
        with configured(None):
            before, reference = await invoke(db, commands, 'computation', case['kwargs'], case['user'])
        with configured(5):
            after, changed = await invoke(db, commands, 'computation', case['kwargs'], case['user'])
        assert wire(before) == wire(after)
        assert reference['mongo_commands'] == changed['mongo_commands']
        assert reference['documents_read'] == changed['documents_read']
        if case['name'] == 'permission_denied':
            assert after['http_error'] == 403 and not changed['mongo_commands']


@pytest.mark.asyncio
@pytest.mark.parametrize('mutation', MUTATIONS, ids=lambda row: row[0])
async def test_existing_cost_read_boundary_mutations(mutation):
    name, kind, fields, filters, _ = mutation
    async with isolated() as (raw, db, commands):
        outcomes = []
        traces = []
        for budget in (None, 5):
            await populate(raw, 1)
            await raw.settings.update_one({'user_id': OWNER['id']}, {'$set': {'report_included_statuses': ['completed']}})
            await raw.daily_costs.insert_one({'user_id': OWNER['id'], 'date': '2026-10-05', 'google_ads': 10})
            gate = Gate(raw, kind, fields)
            writer = asyncio.create_task(gate.writer())
            try:
                with configured(budget):
                    response, _ = await invoke(GatedDB(db, gate), commands, 'computation', {**DEFAULT_KWARGS, **filters})
                await writer
            finally:
                if not writer.done():
                    writer.cancel()
                await asyncio.gather(writer, return_exceptions=True)
            assert gate.committed.is_set() and gate.reads == 2
            outcomes.append(response)
            traces.append(gate.trace)
        assert wire(outcomes[0]) == wire(outcomes[1]), name
        target = os.environ.get('DASHBOARD_COST_CORRECTNESS_PATH')
        if target:
            path = Path(target)
            evidence = json.loads(path.read_text()) if path.exists() else {}
            evidence[name] = {'equivalent': True, 'current': outcomes[0], 'cooperative': outcomes[1], 'traces': traces}
            path.write_text(json.dumps(evidence, default=str), encoding='utf8')


@pytest.mark.asyncio
async def test_profile_mutation_after_final_read_keeps_inflight_inputs():
    async with isolated() as (raw, db, commands):
        await populate(raw, 2)
        rows = await rows_for(db)
        original = wire(rows)
        expected = await dash.build_mezan_v2_product_cost(db, OWNER['id'], rows)
        calls = 0
        started, committed = asyncio.Event(), asyncio.Event()
        async def writer():
            await asyncio.wait_for(started.wait(), 10)
            client = AsyncIOMotorClient(os.environ['MZ2_TEST_MONGO_URI'], serverSelectionTimeoutMS=5000)
            try:
                result = await client[raw.name][dash.COST_PROFILES].update_one(
                    {'user_id': OWNER['id'], 'salla_product_id': '0'}, {'$set': {'base_cost': 99}})
                assert result.modified_count == 1
                committed.set()
            finally:
                client.close()
        mutation = asyncio.create_task(writer())
        async def hook():
            nonlocal calls
            if not calls:
                calls += 1
                # Hook occurs only inside outer cost loop, after all inputs and
                # policy have been read. Fixture writer is deliberately separate.
                started.set()
                await asyncio.wait_for(committed.wait(), 10)
        commands.reset()
        try:
            actual = await cost_builder(budget_ms=.000001, hook=hook)(db, OWNER['id'], rows)
            await mutation
        finally:
            if not mutation.done():
                mutation.cancel()
            await asyncio.gather(mutation, return_exceptions=True)
        assert not commands.writes
        assert calls == 1 and wire(actual) == wire(expected) and wire(rows) == original
        subsequent = await cost_builder(budget_ms=5)(db, OWNER['id'], rows)
        fresh_reference = await dash.build_mezan_v2_product_cost(db, OWNER['id'], rows)
        assert wire(subsequent) == wire(fresh_reference)
        assert subsequent['total'] != expected['total']


@pytest.mark.asyncio
async def test_cost_cancellation_and_concurrent_private_accumulators():
    async with isolated() as (raw, db, commands):
        await seed_mixed(raw)
        rows = await rows_for(db)
        other = await rows_for(db, 'fixture-other')
        assert rows and other
        expected = await dash.build_mezan_v2_product_cost(db, OWNER['id'], rows)
        other_expected = await dash.build_mezan_v2_product_cost(db, 'fixture-other', other)
        entered = asyncio.Event()
        observations = []
        async def hook():
            entered.set()
            await asyncio.Event().wait()
        task = asyncio.create_task(cost_builder(budget_ms=.000001, observer=observations.append, hook=hook)(db, OWNER['id'], rows))
        await asyncio.wait_for(entered.wait(), 10)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert observations and observations[0]['completed'] is False
        fn = cost_builder(budget_ms=.000001)
        current, other_current = await asyncio.gather(fn(db, OWNER['id'], rows), fn(db, 'fixture-other', other))
        assert wire(current) == wire(expected)
        assert wire(other_current) == wire(other_expected)
