"""Advertising cooperation only; parser5/cost5 remain fixed prerequisites."""
import asyncio
import json
import os
import time
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest
from motor.motor_asyncio import AsyncIOMotorClient
import dashboard_b_computation_experiment as computation
from dashboard_b_cooperative_experiment import Budget
from dashboard_b_ads_cooperative_experiment import ads_builder, candidate
from dashboard_v2_ads_executive import build_salla_ads_executive_breakdown
from test_dashboard_b_ads_profile_correctness import fixture, advertising, encoded
from dashboard_b_equivalence_fixtures import CASES, DEFAULT_KWARGS, OWNER, seed_mixed
from test_dashboard_b_baseline_mongo import populate
from test_dashboard_b_concurrency_gate import CASES as MUTATIONS, Gate, GatedDB
from test_dashboard_b_request_scope_mongo import isolated, invoke


@contextmanager
def forced_checkpoints():
    """Correctness-only expired-budget seam; never performance evidence."""
    original = Budget.checkpoint
    async def expired(self):
        self.cpu = time.thread_time() - self.seconds - 1
        return await original(self)
    with patch.object(Budget, 'checkpoint', expired):
        yield


@contextmanager
def configured(budget):
    def factory(db, scope):
        return candidate(db, scope, ads_budget_ms=budget)
    with patch.object(computation, 'candidate', factory):
        yield


@pytest.mark.asyncio
@pytest.mark.parametrize('budget', [1, 5, 10])
@pytest.mark.parametrize('case', ['nested_attribution', 'duplicates', 'mixed_fx', 'unknown_over_100',
                                  'refunds', 'google_spend', 'rounding', 'empty'])
async def test_ads_cooperative_exact_bytes(budget, case):
    rows, ads = fixture(case)
    before = encoded((rows, ads))
    expected = build_salla_ads_executive_breakdown(rows, ads)
    observed, visits = [], []
    async def hook(): visits.append(len(visits))
    with forced_checkpoints():
        actual = await ads_builder(budget, observer=observed.append, hook=hook)(rows, ads)
    assert encoded(actual) == encoded(expected)
    assert encoded((rows, ads)) == before
    assert len(visits) == len(rows)
    assert observed and observed[0]['completed']
    assert observed[0]['yields'] == len(rows)


@pytest.mark.asyncio
@pytest.mark.parametrize('budget', [1, 5, 10])
@pytest.mark.parametrize('value', [None, True, -1, 'invalid', float('nan'), float('inf'), 0, .005, 2.5])
async def test_ads_spend_count_rounding(budget, value):
    rows, ads = fixture('rounding')
    ads['breakdown']['snapchat'] = value
    ads['providers']['meta']['orders'] = value
    before = encoded((rows, ads))
    expected = build_salla_ads_executive_breakdown(rows, ads)
    assert encoded(await ads_builder(budget)(rows, ads)) == encoded(expected)
    assert encoded((rows, ads)) == before


@pytest.mark.asyncio
@pytest.mark.parametrize('budget', [1, 5, 10])
@pytest.mark.parametrize('bad', ['order_none', 'order_scalar', 'ads_none', 'breakdown_list', 'provider_scalar'])
async def test_ads_exception_parity(budget, bad):
    rows, ads = fixture('empty')
    if bad == 'order_none': rows = [None]
    elif bad == 'order_scalar': rows = [7]
    elif bad == 'ads_none': ads = None
    elif bad == 'breakdown_list': ads['breakdown'] = [1]
    else: ads['providers']['snapchat'] = 7
    with pytest.raises(Exception) as expected:
        build_salla_ads_executive_breakdown(rows, ads)
    with pytest.raises(type(expected.value)) as actual:
        with forced_checkpoints():
            await ads_builder(budget)(rows, ads)
    assert str(actual.value) == str(expected.value)


@pytest.mark.asyncio
async def test_cancellation_and_concurrent_private_accumulators():
    rows, ads = fixture('nested_attribution')
    other, other_ads = fixture('mixed_fx')
    entered, observations = asyncio.Event(), []
    async def hook():
        entered.set()
        await asyncio.Event().wait()
    with forced_checkpoints():
        task = asyncio.create_task(ads_builder(5, observer=observations.append, hook=hook)(rows, ads))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            task.cancel()
            with pytest.raises(asyncio.CancelledError): await task
        finally:
            if not task.done(): task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        assert observations and observations[0]['completed'] is False
        function = ads_builder(5)
        first, second = await asyncio.gather(function(rows, ads), function(other, other_ads))
    assert encoded(first) == encoded(build_salla_ads_executive_breakdown(rows, ads))
    assert encoded(second) == encoded(build_salla_ads_executive_breakdown(other, other_ads))


@pytest.mark.asyncio
async def test_large_single_record_has_only_outer_checkpoint():
    # Broad nested fallback input, not a new truncation/depth rule. One order
    # retains a single outer checkpoint, so no hard CPU-budget bound is claimed.
    rows = [{'order_number': 'large', 'currency': 'SAR', 'total_amount': 100,
             'traffic_source': [{'value': ['unrecognized'] * 20} for _ in range(250)]}]
    ads, visits = advertising(), []
    async def hook(): visits.append('before_order')
    expected = build_salla_ads_executive_breakdown(rows, ads)
    with forced_checkpoints():
        actual = await ads_builder(5, hook=hook)(rows, ads)
    assert visits == ['before_order']
    assert encoded(actual) == encoded(expected)


@pytest.mark.asyncio
@pytest.mark.parametrize('budget', [1, 5, 10])
@pytest.mark.parametrize('case', CASES, ids=lambda row: row['name'])
async def test_dashboard_permissions_tenant_and_mixed_equivalence(budget, case):
    async with isolated() as (raw, db, commands):
        await seed_mixed(raw)
        if case['settings']:
            await raw.settings.update_one({'user_id': OWNER['id']}, {'$set': case['settings']})
        with configured(None):
            before, reference = await invoke(db, commands, 'computation', case['kwargs'], case['user'])
        with configured(budget):
            after, changed = await invoke(db, commands, 'computation', case['kwargs'], case['user'])
        assert encoded(before) == encoded(after)
        assert reference['mongo_commands'] == changed['mongo_commands']
        assert reference['documents_read'] == changed['documents_read']
        if case['name'] == 'permission_denied':
            assert after['http_error'] == 403 and not changed['mongo_commands']


@pytest.mark.asyncio
@pytest.mark.parametrize('mutation', MUTATIONS, ids=lambda row: row[0])
async def test_existing_read_boundary_mutations(mutation):
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
            assert gate.reads == 2 and gate.committed.is_set()
            key = 'current' if budget is None else str(budget)
            results[key], traces[key] = response, gate.trace
        assert all(encoded(response) == encoded(results['current']) for response in results.values())
        target = os.environ.get('DASHBOARD_ADS_COOPERATIVE_CORRECTNESS_PATH')
        if target:
            path = Path(target)
            evidence = json.loads(path.read_text(encoding='utf8')) if path.exists() else {}
            evidence[name] = {'equivalent': True, 'outcomes': results, 'traces': traces}
            path.write_text(json.dumps(evidence, ensure_ascii=False, default=str), encoding='utf8')


@pytest.mark.asyncio
async def test_post_input_read_database_mutation_preserves_ads_snapshot():
    async with isolated() as (raw, db, commands):
        await populate(raw, 2)
        await raw.daily_costs.insert_one({'user_id': OWNER['id'], 'date': '2026-10-05', 'google_ads': 10})
        async def inputs():
            rows = await db.unified_orders.find({'user_id': OWNER['id']}, {'_id': 0}).to_list(10)
            daily = await db.daily_costs.find_one({'user_id': OWNER['id']}, {'_id': 0})
            ads = advertising()
            ads['breakdown']['google_transitional'] = daily['google_ads']
            return rows, ads
        rows, ads = await inputs()
        snapshot = encoded((rows, ads))
        expected = build_salla_ads_executive_breakdown(rows, ads)
        started, committed = asyncio.Event(), asyncio.Event()
        async def writer():
            await asyncio.wait_for(started.wait(), 10)
            client = AsyncIOMotorClient(os.environ['MZ2_TEST_MONGO_URI'], serverSelectionTimeoutMS=5000)
            try:
                await client[raw.name].unified_orders.update_one({'user_id': OWNER['id'], 'order_number': '1000000'}, {'$set': {'total_amount': 700, 'order_status': 'cancelled'}})
                await client[raw.name].daily_costs.update_one({'user_id': OWNER['id']}, {'$set': {'google_ads': 40}})
                committed.set()
            finally: client.close()
        task = asyncio.create_task(writer())
        async def hook():
            if not started.is_set():
                started.set()
                await asyncio.wait_for(committed.wait(), 10)
        commands.reset()
        try:
            with forced_checkpoints():
                actual = await ads_builder(5, hook=hook)(rows, ads)
            await task
        finally:
            if not task.done(): task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        assert committed.is_set() and not commands.writes and not commands.counts
        assert encoded(actual) == encoded(expected)
        assert encoded((rows, ads)) == snapshot
        fresh_rows, fresh_ads = await inputs()
        fresh = await ads_builder(5)(fresh_rows, fresh_ads)
        assert encoded(fresh) == encoded(build_salla_ads_executive_breakdown(fresh_rows, fresh_ads))
        assert encoded(fresh) != encoded(expected)
