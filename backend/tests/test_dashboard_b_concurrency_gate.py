"""Deterministic read/mutation schedules; passing tests can prove a BLOCKED gate.

Writes target only the explicit UUID fixture DB, from a separate asyncio task.
The dashboard itself retains the read-only facade. No runtime optimization.
"""
import asyncio
import copy
import inspect
import json
import os
import subprocess
import time
from pathlib import Path

import pytest
from motor.motor_asyncio import AsyncIOMotorClient
import dashboard_v2_routes as dash
from test_dashboard_b_baseline_mongo import populate
from test_dashboard_b_request_scope_mongo import isolated, invoke, wire
from dashboard_b_equivalence_fixtures import DEFAULT_KWARGS


CASES = [
    ('status', 'orders', {'order_status': 'cancelled'}, {}, True),
    ('payment_status', 'orders', {'payment_method': 'cash_on_delivery', 'order_status': 'cancelled'}, {'payment_methods': 'mada'}, True),
    ('shipping_status', 'orders', {'shipping_company': 'SMSA', 'order_status': 'cancelled'}, {'shipping_companies': 'Fixture carrier'}, True),
    ('payment_only', 'orders', {'payment_method': 'cash_on_delivery'}, {'payment_methods': 'mada'}, True),
    ('shipping_only', 'orders', {'shipping_company': 'SMSA'}, {'shipping_companies': 'Fixture carrier'}, True),
    ('product_profile_cost', 'profile', {'base_cost': 55}, {}, False),
    ('embedded_order_cost', 'orders', {'total_product_cost': 60}, {}, False),
    ('advertising_google', 'ads', {'google_ads': 40}, {}, False),
    ('date_universe', 'orders', {'order_date': '2026-09-30'}, {}, True),
    ('irrelevant_note', 'orders', {'internal_fixture_note': 'changed'}, {}, False),
]


class Gate:
    def __init__(self, raw, kind, fields):
        self.raw, self.kind, self.fields = raw, kind, fields
        self.fetched = asyncio.Event()
        self.committed = asyncio.Event()
        self.trace = []
        self.reads = 0

    def record(self, point, **details):
        self.trace.append({'point': point, 'monotonic_ns': time.monotonic_ns(), **details})

    async def writer(self):
        await asyncio.wait_for(self.fetched.wait(), 10)
        self.record('mutation_started', fields=self.fields)
        # Separate connection: handler listener must still observe ZERO writes.
        client = AsyncIOMotorClient(os.environ['MZ2_TEST_MONGO_URI'], serverSelectionTimeoutMS=5000)
        writer_db = client[self.raw.name]
        try:
            if self.kind == 'profile':
                result = await writer_db[dash.COST_PROFILES].update_one({'user_id':'fixture-owner','salla_product_id':'0'}, {'$set':self.fields})
            elif self.kind == 'ads':
                result = await writer_db.daily_costs.update_one({'user_id':'fixture-owner','date':'2026-10-05'}, {'$set':self.fields})
            else:
                result = await writer_db.unified_orders.update_one({'user_id':'fixture-owner','order_number':'1000000'}, {'$set':self.fields})
        finally:
            client.close()
        assert result.matched_count == result.modified_count == 1
        self.record('mutation_acknowledged')
        self.committed.set()

    async def read(self, cursor, actor, query, projection, args, kwargs):
        # Explicitly order the legacy and V2 normalized reads even if Motor
        # schedules their settings reads differently. Shared has just `load`.
        if self.kind != 'ads' and actor == '_filtered_orders':
            await asyncio.wait_for(self.committed.wait(), 10)
        elif self.reads:
            await asyncio.wait_for(self.committed.wait(), 10)
        self.reads += 1
        number = self.reads
        self.record('read_started', read=number, actor=actor, query=copy.deepcopy(query), projection=copy.deepcopy(projection))
        rows = await cursor.to_list(*args, **kwargs)
        self.record('read_completed', read=number, actor=actor, rows=copy.deepcopy(rows))
        if number == 1:
            self.fetched.set()
            await asyncio.wait_for(self.committed.wait(), 10)
        return rows


def actor_name():
    frame = inspect.currentframe()
    try:
        while frame:
            if frame.f_code.co_name in {'dashboard', '_filtered_orders', 'load'}:
                return frame.f_code.co_name
            frame = frame.f_back
        return 'later_ads_read'
    finally:
        del frame


class Cursor:
    def __init__(self, cursor, gate, actor, query, projection):
        self.cursor, self.gate, self.actor = cursor, gate, actor
        self.query, self.projection = query, projection
    async def to_list(self, *args, **kwargs):
        return await self.gate.read(self.cursor, self.actor, self.query, self.projection, args, kwargs)


class ObservedCursor:
    def __init__(self, cursor, gate, collection, query, projection):
        self.cursor, self.gate, self.collection = cursor, gate, collection
        self.query, self.projection = query, projection
    async def to_list(self, *args, **kwargs):
        self.gate.record('downstream_read_started', collection=self.collection, query=copy.deepcopy(self.query), projection=copy.deepcopy(self.projection))
        rows = await self.cursor.to_list(*args, **kwargs)
        self.gate.record('downstream_read_completed', collection=self.collection, rows=copy.deepcopy(rows))
        return rows


class Collection:
    def __init__(self, base, name, gate): self.base, self.name, self.gate = base, name, gate
    def __getattr__(self, name): return getattr(self.base, name)
    def find(self, query, projection=None, *args, **kwargs):
        cursor = self.base.find(query, projection, *args, **kwargs)
        selected = (self.name == 'daily_costs' if self.gate.kind == 'ads' else
                    self.name == 'unified_orders' and projection == {'_id':0, 'raw_by_source':0})
        if selected:
            return Cursor(cursor, self.gate, actor_name(), query, projection)
        if self.name in {'unified_orders', dash.COST_PROFILES}:
            return ObservedCursor(cursor, self.gate, self.name, query, projection)
        return cursor


class GatedDB:
    def __init__(self, base, gate): self.base, self.gate = base, gate
    def __getitem__(self, name): return Collection(self.base[name], name, self.gate)
    def __getattr__(self, name): return self[name]


def differences(left, right, prefix=''):
    if isinstance(left, dict) and isinstance(right, dict):
        result = []
        for key in sorted(left.keys() | right.keys()):
            path = prefix + '/' + key
            if key not in left or key not in right:
                result.append({'path':path, 'current':left.get(key), 'shared':right.get(key)})
            else:
                result.extend(differences(left[key], right[key], path))
        return result
    if left != right:
        return [{'path':prefix, 'current':left, 'shared':right}]
    return []


@pytest.mark.asyncio
async def test_concurrent_mutation_semantics_gate():
    evidence = {'head':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
                'mongo':'8.0.12', 'cases':[], 'runtime_adoption':'BLOCKED_IF_BUSINESS_DIVERGENCE'}
    async with isolated() as (raw, readonly, commands):
        for name, kind, fields, filters, expected_difference in CASES:
            outcomes = {}
            for mode in ('current', 'shared'):
                await populate(raw, 1)
                await raw.settings.update_one({'user_id':'fixture-owner'}, {'$set':{'report_included_statuses':['completed']}})
                await raw.daily_costs.insert_one({'user_id':'fixture-owner','date':'2026-10-05','google_ads':10})
                gate = Gate(raw, kind, fields)
                writer = asyncio.create_task(gate.writer())
                try:
                    response, _ = await invoke(GatedDB(readonly, gate), commands, mode, {**DEFAULT_KWARGS, **filters})
                    await writer
                finally:
                    if not writer.done(): writer.cancel()
                    await asyncio.gather(writer, return_exceptions=True)
                assert gate.committed.is_set()
                assert gate.reads == (2 if mode == 'current' or kind == 'ads' else 1)
                points = [entry['point'] for entry in gate.trace if not entry['point'].startswith('downstream_')]
                assert points[:4] == ['read_started','read_completed','mutation_started','mutation_acknowledged']
                if gate.reads == 2:
                    assert points[4:] == ['read_started','read_completed']
                assert [t['monotonic_ns'] for t in gate.trace] == sorted(t['monotonic_ns'] for t in gate.trace)
                profile_reads = [t for t in gate.trace if t['point'] == 'downstream_read_completed' and t['collection'] == dash.COST_PROFILES]
                assert len(profile_reads) == 1
                if kind == 'profile':
                    assert profile_reads[0]['rows'][0]['base_cost'] == 55
                    assert next(t['monotonic_ns'] for t in gate.trace if t['point'] == 'mutation_acknowledged') < profile_reads[0]['monotonic_ns']
                assert 'http_error' not in response
                outcomes[mode] = {'response':response, 'trace':gate.trace}
            diff = differences(outcomes['current']['response'], outcomes['shared']['response'])
            case = {'name':name, 'mutation':fields, 'read_boundary':'daily_costs repeated reads' if kind == 'ads' else 'legacy normalized order read -> V2 normalized order read',
                    'outcomes':outcomes, 'differences':diff, 'full_json_equal':wire(outcomes['current']['response']) == wire(outcomes['shared']['response'])}
            evidence['cases'].append(case)
            evidence['runtime_adoption'] = 'BLOCKED' if any(not c['full_json_equal'] for c in evidence['cases']) else 'UNDECIDED'
            Path(os.environ.get('DASHBOARD_CONCURRENCY_EVIDENCE_PATH','../dashboard-concurrency-evidence.json')).write_text(json.dumps(evidence,indent=2,ensure_ascii=False,default=str),encoding='utf8')
            assert bool(diff) == expected_difference, (name, diff)
            if expected_difference:
                assert outcomes['current']['response']['totals']['total_orders'] == 0
                assert outcomes['shared']['response']['totals']['total_orders'] == 1
                assert outcomes['current']['response']['totals']['total_sales'] == 0
                assert outcomes['shared']['response']['totals']['total_sales'] == 100
            if name == 'product_profile_cost':
                assert all(v['response']['totals']['total_product_cost'] == 55 for v in outcomes.values())
            if name == 'advertising_google':
                assert all(v['response']['totals']['total_ads_cost'] == 40 for v in outcomes.values())
            print('CONCURRENCY_GATE '+json.dumps({'case':name,'different':bool(diff),'paths':[d['path'] for d in diff]}),flush=True)
