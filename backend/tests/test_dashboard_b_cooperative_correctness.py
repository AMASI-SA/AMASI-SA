"""Test-only parser equivalence and honest new-checkpoint interleaving evidence."""
import asyncio
import copy
import json
import os
import time
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest
import orders_db
import dashboard_b_computation_experiment as computation
from dashboard_b_cooperative_experiment import candidate, parser
from dashboard_b_equivalence_fixtures import CASES, DEFAULT_KWARGS, OWNER, seed_mixed
from test_dashboard_b_baseline_mongo import populate
from test_dashboard_b_concurrency_gate import CASES as MUTATIONS, Gate, GatedDB, differences
from test_dashboard_b_request_scope_mongo import isolated, invoke, wire


@contextmanager
def cooperative(budget=5, hook=None):
    def configured(db, scope):
        return candidate(db, scope, budget_ms=budget, hook=hook)
    with patch.object(computation, 'candidate', configured):
        yield


def orders_fixture():
    # Repeated IDs, ties, floating addition order, missing values and >100
    # unverified currencies exercise accumulator/finalizer boundaries.
    return [dict(order_number=str(i % 17), total_amount=v, currency='SAR',
                 payment_method=('mada' if i % 2 else 'visa'),
                 shipping_company=('A' if i % 2 else 'B'),
                 order_status='completed', order_date='2026-10-05', source='web')
            for i, v in enumerate([.005, .015, 100000000.01, .1, .2] * 200)] + [
        dict(order_number=str(i % 3), total_amount=17, currency='XYZ',
             payment_method=None, shipping_company='', order_status=None)
        for i in range(120)]


@pytest.mark.asyncio
@pytest.mark.parametrize('budget', [1, 5, 10])
@pytest.mark.parametrize('empty', [False, True])
async def test_parser_exact_order_rounding_duplicates_currency(budget, empty):
    rows = [] if empty else orders_fixture()
    before = copy.deepcopy(rows)
    expected = orders_db.orders_to_parsed(rows)
    observed = []
    actual = await parser(budget, observer=observed.append)(rows)
    assert wire(actual) == wire(expected)
    assert rows == before
    assert observed[0]['completed']
    assert len(actual['orders_sample']) == min(10, len(rows))


@pytest.mark.asyncio
@pytest.mark.parametrize('budget', [1, 5, 10])
@pytest.mark.parametrize('bad', [None, 7, {'payment_method': 5}, {'shipping_company': []}])
async def test_parser_exception_parity(budget, bad):
    rows = orders_fixture() + [bad]
    try:
        expected = orders_db.orders_to_parsed(rows)
    except Exception as error:
        with pytest.raises(type(error)) as actual:
            await parser(budget)(rows)
        assert str(actual.value) == str(error)
    else:
        assert wire(await parser(budget)(rows)) == wire(expected)


@pytest.mark.asyncio
async def test_cancellation_discards_request_local_accumulator():
    entered = asyncio.Event()
    async def hook():
        entered.set()
        await asyncio.Event().wait()
    observations = []
    task = asyncio.create_task(parser(.000001, observations.append, hook)(orders_fixture()))
    await asyncio.wait_for(entered.wait(), 5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert observations and observations[0]['completed'] is False
    assert wire(await parser(5)(orders_fixture())) == wire(orders_db.orders_to_parsed(orders_fixture()))


@pytest.mark.asyncio
@pytest.mark.parametrize('case', CASES, ids=lambda case: case['name'])
async def test_mixed_json_permissions_tenant_equivalence(case):
    async with isolated() as (raw, db, commands):
        await seed_mixed(raw)
        if case['settings']:
            await raw.settings.update_one({'user_id': OWNER['id']}, {'$set': case['settings']})
        current, before = await invoke(db, commands, 'computation', case['kwargs'], case['user'])
        with cooperative():
            changed, after = await invoke(db, commands, 'computation', case['kwargs'], case['user'])
        assert wire(changed) == wire(current)
        assert before['mongo_commands'] == after['mongo_commands']
        assert before['documents_read'] == after['documents_read']
        if case['name'] == 'permission_denied':
            assert changed['http_error'] == 403
            assert sum(after['mongo_commands'].values()) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize('mutation', MUTATIONS, ids=lambda case: case[0])
async def test_existing_read_boundary_mutations(mutation):
    name, kind, fields, filters, _ = mutation
    async with isolated() as (raw, readonly, commands):
        results = {}
        for mode in ('computation', 'cooperative'):
            await populate(raw, 1)
            await raw.settings.update_one({'user_id': OWNER['id']}, {'$set': {'report_included_statuses': ['completed']}})
            await raw.daily_costs.insert_one({'user_id': OWNER['id'], 'date': '2026-10-05', 'google_ads': 10})
            gate = Gate(raw, kind, fields)
            writer = asyncio.create_task(gate.writer())
            try:
                if mode == 'cooperative':
                    with cooperative(.000001):
                        response, _ = await invoke(GatedDB(readonly, gate), commands, 'computation', {**DEFAULT_KWARGS, **filters})
                else:
                    response, _ = await invoke(GatedDB(readonly, gate), commands, 'computation', {**DEFAULT_KWARGS, **filters})
                await writer
            finally:
                if not writer.done():
                    writer.cancel()
                await asyncio.gather(writer, return_exceptions=True)
            assert gate.reads == 2 and gate.committed.is_set()
            results[mode] = response
        assert not differences(results['computation'], results['cooperative']), name
        assert wire(results['computation']) == wire(results['cooperative'])


@pytest.mark.asyncio
async def test_new_checkpoint_interleaving_diagnostic():
    """A newly reachable schedule, NOT a claim of whole-handler equivalence.

    A same-loop writer is eligible at parser entry. A checkpoint lets it
    commit while parsing; without a checkpoint it cannot run before return.
    The controlled sibling read records both DB snapshots. This deliberately
    exposes a timing-semantic difference instead of relabelling it PASS.
    """
    evidence = {'scope': 'parser plus controlled sibling Mongo read',
                'runtime_adoption': 'NOT_PERFORMED', 'cases': []}
    async with isolated() as (raw, readonly, commands):
        for mode in ('current', 'cooperative'):
            await populate(raw, 1)
            rows = await readonly.unified_orders.find({'user_id': OWNER['id']}, {'_id': 0}).to_list(10)
            immutable = wire(rows)
            trace = []
            def record(point):
                trace.append({'point': point, 'time_ns': time.monotonic_ns()})
            eligible, checkpoint, parsed, committed, read_done = [asyncio.Event() for _ in range(5)]
            async def writer():
                await eligible.wait()
                record('writer_scheduled')
                if not checkpoint.is_set():
                    await read_done.wait()
                await raw.unified_orders.update_one({'user_id': OWNER['id'], 'order_number': '1000000'}, {'$set': {'order_status': 'cancelled'}})
                record('mutation_acknowledged')
                committed.set()
            async def hook():
                if not checkpoint.is_set():
                    record('checkpoint')
                    checkpoint.set()
                    await committed.wait()
            task = asyncio.create_task(writer())
            try:
                record('parser_enter')
                eligible.set()
                if mode == 'current':
                    result = orders_db.orders_to_parsed(rows)
                else:
                    result = await parser(.000001, hook=hook)(rows)
                record('parser_return')
                parsed.set()
                read = await readonly.unified_orders.find_one({'user_id': OWNER['id'], 'order_number': '1000000'}, {'_id': 0})
                record('sibling_read_completed')
                read_done.set()
                await task
            finally:
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            assert wire(rows) == immutable
            evidence['cases'].append({'mode': mode, 'parser_result': result,
                                      'sibling_status': read['order_status'], 'trace': trace})
    current, changed = evidence['cases']
    assert wire(current['parser_result']) == wire(changed['parser_result'])
    assert current['sibling_status'] == 'completed'
    assert changed['sibling_status'] == 'cancelled'
    evidence['arbitrary_interleaving_equivalence'] = 'NOT_PROVEN: controlled schedule demonstrates a new interleaving, not whole Dashboard divergence'
    evidence['parser_immutable_input_equivalence'] = 'PASS'
    Path(os.environ.get('DASHBOARD_COOPERATIVE_INTERLEAVING_PATH', '../dashboard-cooperative-interleaving.json')).write_text(json.dumps(evidence, indent=2, ensure_ascii=False, default=str), encoding='utf8')
    print('COOPERATIVE_INTERLEAVING ' + json.dumps(evidence, default=str), flush=True)


@pytest.mark.asyncio
async def test_fresh_request_observes_database_change():
    async with isolated() as (raw, db, commands):
        await populate(raw, 4)
        with cooperative():
            first, _ = await invoke(db, commands, 'computation', DEFAULT_KWARGS)
        await raw.unified_orders.update_one({'order_number':'1000000'}, {'$set':{'total_amount':123, 'total_amount_sar':123}})
        with cooperative():
            second, _ = await invoke(db, commands, 'computation', DEFAULT_KWARGS)
        reference, _ = await invoke(db, commands, 'current', DEFAULT_KWARGS)
        assert wire(second) == wire(reference)
        assert first['totals']['total_sales'] != second['totals']['total_sales']


@pytest.mark.asyncio
async def test_concurrent_parser_accumulators_are_private():
    first, second = orders_fixture(), list(reversed(orders_fixture()))
    run = parser(1)
    left, right = await asyncio.gather(run(first), run(second))
    assert wire(left) == wire(orders_db.orders_to_parsed(first))
    assert wire(right) == wire(orders_db.orders_to_parsed(second))
    left['orders_individual'].clear()
    assert right['orders_individual']
