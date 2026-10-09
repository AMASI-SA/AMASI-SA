"""Index cooperation gates, including real independent-writer read schedules."""
import asyncio
import json
import os
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest
from pymongo import MongoClient
import dashboard_v2_routes as dash
import product_catalog_cost_resolution as catalog
import dashboard_b_computation_experiment as computation
import dashboard_b_index_cooperative_experiment as experiment
from dashboard_b_cooperative_experiment import Budget
from test_dashboard_b_index_profile_correctness import CASES as INDEX_CASES, products_fixture, aliases, encoded
from dashboard_b_equivalence_fixtures import CASES, DEFAULT_KWARGS, OWNER, seed_mixed
from test_dashboard_b_baseline_mongo import populate
from test_dashboard_b_concurrency_gate import CASES as MUTATIONS, Gate, GatedDB
from test_dashboard_b_request_scope_mongo import isolated, invoke


class ForcedBudget(Budget):
    async def checkpoint(self):
        # Test seam only: deterministically yield at every boundary. Artificial
        # slice clocks are NEVER exported as performance measurements.
        self.cpu = time.thread_time() - self.seconds - 1
        await super().checkpoint()


@contextmanager
def configured(budget, hook=None):
    def factory(db, scope):
        return experiment.candidate(db, scope, index_budget_ms=budget, index_hook=hook)
    with patch.object(computation, 'candidate', factory):
        yield


@pytest.mark.asyncio
@pytest.mark.parametrize('budget', [1, 5, 10])
@pytest.mark.parametrize('case', INDEX_CASES)
async def test_exact_values_reference_graph_and_forced_boundaries(budget, case):
    products = products_fixture(case)
    before = encoded(products)
    expected = catalog.index_current_catalog_products(products)
    observations, visits = [], []
    async def hook(): visits.append('boundary')
    with patch.object(experiment, 'Budget', ForcedBudget):
        actual = await experiment.index_builder(budget, observations.append, hook)(products)
    assert encoded(actual) == encoded(expected)
    assert aliases(actual) == aliases(expected)
    assert encoded(products) == before
    assert observations[0]['completed']
    assert observations[0]['yields'] == len(visits)
    assert len(visits) >= len(products)


@pytest.mark.asyncio
@pytest.mark.parametrize('budget', [None, 1, 5, 10])
@pytest.mark.parametrize('products', [None, [7], ['bad-mapping'], [[('broken',)]], [{'id': 'valid'}, 7]])
async def test_exception_parity(budget, products):
    before = encoded(products)
    observations = []
    with pytest.raises(Exception) as reference:
        catalog.index_current_catalog_products(products)
    with pytest.raises(type(reference.value)) as actual:
        with patch.object(experiment, 'Budget', ForcedBudget):
            await experiment.index_builder(budget, observer=observations.append)(products)
    assert str(actual.value) == str(reference.value)
    assert encoded(products) == before
    assert observations and observations[0]['completed'] is False


@pytest.mark.asyncio
@pytest.mark.parametrize('budget', [1, 5, 10])
async def test_shallow_nested_references_to_original_input(budget):
    products = products_fixture('variant_duplicates_and_raw')
    before = encoded(products)
    with patch.object(experiment, 'Budget', ForcedBudget):
        maps = await experiment.index_builder(budget)(products)
    row = maps[0]['one']
    assert row is not products[0]
    assert row['raw_salla_details'] is products[0]['raw_salla_details']
    assert row['variants'] is not products[0]['variants']
    assert row['variants'][0] is not products[0]['variants'][0]
    assert row['variants'][0]['options'] is products[0]['variants'][0]['options']
    assert encoded(products) == before


@pytest.mark.asyncio
@pytest.mark.parametrize('unit', ['large_product', 'large_name_group'])
async def test_whole_product_and_name_group_checkpoint_limit(unit):
    if unit == 'large_product':
        products = [{'salla_product_id': 'same', 'name': 'Same',
                     'variants': [{'id': str(i), 'sku': 'v' + str(i)} for i in range(1000)]}]
    else:
        products = [{'salla_product_id': 'same', 'id': str(i), 'name': 'Same'} for i in range(1000)]
    visits = []
    async def hook(): visits.append('outer_boundary')
    expected = catalog.index_current_catalog_products(products)
    with patch.object(experiment, 'Budget', ForcedBudget):
        actual = await experiment.index_builder(5, hook=hook)(products)
    assert encoded(actual) == encoded(expected)
    # Exactly one checkpoint/product and one for the single name group.
    # Large variant loops/group identity comprehensions remain uninterrupted;
    # this test deliberately makes no claim of a hard time bound.
    assert len(visits) == len(products) + 1


@pytest.mark.asyncio
async def test_cancelled_partial_indexes_never_return_and_requests_are_private():
    products = products_fixture('name_first_object_same_identity')
    entered, observations = asyncio.Event(), []
    async def hook():
        entered.set()
        await asyncio.Event().wait()
    with patch.object(experiment, 'Budget', ForcedBudget):
        task = asyncio.create_task(experiment.index_builder(5, observations.append, hook)(products))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            task.cancel()
            with pytest.raises(asyncio.CancelledError): await task
        finally:
            if not task.done(): task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        assert observations[0]['completed'] is False
        function = experiment.index_builder(5)
        a, b = await asyncio.gather(function(products), function(products))
    expected = catalog.index_current_catalog_products(products)
    assert encoded(a) == encoded(b) == encoded(expected)
    assert aliases(a) == aliases(b) == aliases(expected)
    assert a[0]['same'] is not b[0]['same']


@pytest.mark.asyncio
@pytest.mark.parametrize('case', CASES, ids=lambda row: row['name'])
async def test_dashboard_mixed_permissions_and_tenants(case):
    async with isolated() as (raw, db, commands):
        await seed_mixed(raw)
        if case['settings']:
            await raw.settings.update_one({'user_id': OWNER['id']}, {'$set': case['settings']})
        outcomes = []
        for budget in (None, 1, 5, 10):
            with configured(budget):
                response, metrics = await invoke(db, commands, 'computation', case['kwargs'], case['user'])
            outcomes.append((response, metrics))
        for response, metrics in outcomes[1:]:
            assert encoded(response) == encoded(outcomes[0][0])
            assert metrics['mongo_commands'] == outcomes[0][1]['mongo_commands']
            assert metrics['documents_read'] == outcomes[0][1]['documents_read']
        if case['name'] == 'permission_denied':
            assert all(r['http_error'] == 403 and not m['mongo_commands'] for r, m in outcomes)


def evidence_case(name, value):
    target = os.environ.get('DASHBOARD_INDEX_COOPERATIVE_CORRECTNESS_PATH')
    if target:
        path = Path(target)
        data = json.loads(path.read_text(encoding='utf8')) if path.exists() else {}
        data[name] = value
        path.write_text(json.dumps(data, default=str, ensure_ascii=False), encoding='utf8')


@pytest.mark.asyncio
@pytest.mark.parametrize('mutation', MUTATIONS, ids=lambda row: row[0])
async def test_existing_independent_read_mutations(mutation):
    name, kind, fields, filters, _ = mutation
    async with isolated() as (raw, db, commands):
        results, traces = {}, {}
        for budget in (None, 1, 5, 10):
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
                if not writer.done(): writer.cancel()
                await asyncio.gather(writer, return_exceptions=True)
            assert gate.committed.is_set() and gate.reads == 2
            key = 'current' if budget is None else str(budget)
            results[key], traces[key] = response, gate.trace
        assert all(encoded(r) == encoded(results['current']) for r in results.values())
        evidence_case(name, {'equivalent': True, 'outcomes': results, 'traces': traces})


class ReadTraceDB:
    def __init__(self, db, started, record): self.db, self.started, self.record = db, started, record
    def __getitem__(self, name):
        base, owner = self.db[name], self
        class Collection:
            def __getattr__(self, attribute): return getattr(base, attribute)
            def find(self, *args, **kwargs):
                if owner.started.is_set(): owner.record('downstream_find', collection=name)
                return base.find(*args, **kwargs)
        return Collection()
    def __getattr__(self, name): return self[name]


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['catalog', 'profile', 'product_binding', 'option_binding', 'resource', 'policy'])
async def test_mutations_during_index_preserve_captured_catalog_and_later_reads(kind):
    """An independent OS thread can write while Current blocks synchronously.

    Current uses a synchronous test fence inside first enrichment, not an
    invented await. Cooperative uses its first real checkpoint. Both commit
    the same mutation after catalog capture and before downstream reads.
    This controlled schedule does NOT prove all arbitrary races equivalent.
    """
    async with isolated() as (raw, db, commands):
        outcomes, all_traces = {}, {}
        for budget in (None, 1, 5, 10):
            await populate(raw, 2)
            await raw[dash.RESOURCES].insert_one({'user_id': OWNER['id'], 'id': 'resource', 'unit_cost': 3})
            await raw[dash.PRODUCT_RESOURCE_BINDINGS].insert_one({'user_id': OWNER['id'], 'salla_product_id': '0', 'id': 'binding', 'resource_id': 'resource', 'quantity': 2})
            await raw[dash.BINDINGS].insert_one({'user_id': OWNER['id'], 'salla_product_id': '0', 'id': 'option', 'option_name': 'التغليف', 'value_name': 'فاخر', 'mode': 'direct', 'direct_amount': 4})
            await raw.unified_orders.update_one({'order_number': '1000000'}, {'$set': {'products.0.options': [{'name': 'التغليف', 'value': 'فاخر'}]}})
            trace, errors = [], []
            started, committed, stop = threading.Event(), threading.Event(), threading.Event()
            ack = asyncio.Event()
            loop = asyncio.get_running_loop()
            def record(point, **fields): trace.append(dict(point=point, time_ns=time.monotonic_ns(), **fields))
            targets = {
                'catalog': (dash.PRODUCTS, {'salla_product_id': '0'}, {'name': 'Changed catalog name'}),
                'profile': (dash.COST_PROFILES, {'salla_product_id': '0'}, {'base_cost': 99}),
                'product_binding': (dash.PRODUCT_RESOURCE_BINDINGS, {'id': 'binding'}, {'quantity': 5}),
                'option_binding': (dash.BINDINGS, {'id': 'option'}, {'direct_amount': 12}),
                'resource': (dash.RESOURCES, {'id': 'resource'}, {'unit_cost': 8}),
                'policy': ('order_status_policy', {'status': 'completed'}, {'category': 'cancelled'}),
            }
            def writer():
                client = None
                try:
                    assert started.wait(15), 'Index mutation fence not reached'
                    if stop.is_set(): return
                    client = MongoClient(os.environ['MZ2_TEST_MONGO_URI'], serverSelectionTimeoutMS=5000)
                    collection, query, fields = targets[kind]
                    result = client[raw.name][collection].update_one(
                        {'user_id': OWNER['id'], **query}, {'$set': fields}, upsert=kind == 'policy')
                    assert result.modified_count == 1 or result.upserted_id is not None
                    record('mutation_acknowledged', collection=collection)
                except BaseException as error: errors.append(repr(error))
                finally:
                    if client is not None: client.close()
                    committed.set()
                    loop.call_soon_threadsafe(ack.set)
            thread = threading.Thread(target=writer, daemon=True)
            thread.start()
            original_enrich = catalog.enrich_current_salla_cost
            def sync_enrich(product):
                if not started.is_set():
                    record('current_sync_index_fence')
                    started.set()
                    assert committed.wait(15), 'Independent writer did not complete'
                    assert not errors, errors
                return original_enrich(product)
            async def hook():
                if not started.is_set():
                    record('cooperative_index_checkpoint')
                    started.set()
                    await asyncio.wait_for(ack.wait(), 15)
                    assert not errors, errors
            try:
                observed_db = ReadTraceDB(db, started, record)
                if budget is None:
                    with patch.object(catalog, 'enrich_current_salla_cost', sync_enrich), configured(None):
                        response, _ = await invoke(observed_db, commands, 'computation', DEFAULT_KWARGS)
                else:
                    with patch.object(experiment, 'Budget', ForcedBudget), configured(budget, hook):
                        response, _ = await invoke(observed_db, commands, 'computation', DEFAULT_KWARGS)
            finally:
                stop.set(); started.set()
                thread.join(timeout=20)
            assert not thread.is_alive() and not errors
            mutation_time = next(row['time_ns'] for row in trace if row['point'] == 'mutation_acknowledged')
            for collection in (dash.COST_PROFILES, dash.BINDINGS, dash.PRODUCT_RESOURCE_BINDINGS, dash.RESOURCES, 'order_status_policy'):
                reads = [row for row in trace if row['point'] == 'downstream_find' and row['collection'] == collection]
                assert reads and all(row['time_ns'] > mutation_time for row in reads), (collection, trace)
            key = 'current' if budget is None else str(budget)
            outcomes[key], all_traces[key] = response, trace
            expected_total = {'catalog': 85, 'profile': 146.5, 'product_binding': 94,
                              'option_binding': 93, 'resource': 95, 'policy': 0}[kind]
            assert response['product_cost_v2']['total'] == expected_total, (kind, response['product_cost_v2'])
            if kind == 'catalog':
                assert all(row['name'] != 'Changed catalog name' for row in response['product_cost_v2']['product_rows'])
                with configured(budget):
                    fresh, _ = await invoke(db, commands, 'computation', DEFAULT_KWARGS)
                assert any(row['name'] == 'Changed catalog name' for row in fresh['product_cost_v2']['product_rows'])
        equal = all(encoded(row) == encoded(outcomes['current']) for row in outcomes.values())
        evidence_case('during_index_' + kind, {'equivalent': equal, 'outcomes': outcomes, 'traces': all_traces,
            'scope': 'controlled matched post-catalog/pre-downstream mutation schedule; arbitrary races NOT PROVEN'})
        assert equal, kind
