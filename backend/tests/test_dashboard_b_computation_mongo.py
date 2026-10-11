"""Computation-only experiment: unchanged reads, exact JSON and paired metrics."""
import json
import math
import os
import platform
import statistics
import subprocess
from pathlib import Path
import pytest
from test_dashboard_b_request_scope_mongo import isolated, invoke, wire
from test_dashboard_b_baseline_mongo import populate
from dashboard_b_equivalence_fixtures import CASES, OWNER, DEFAULT_KWARGS, seed_mixed


@pytest.mark.asyncio
@pytest.mark.parametrize('case', CASES, ids=lambda c: c['name'])
async def test_full_json_equivalence(case):
    async with isolated() as (raw, db, commands):
        await seed_mixed(raw)
        if case['settings']:
            await raw.settings.update_one({'user_id': OWNER['id']}, {'$set': case['settings']})
        before, current = await invoke(db, commands, 'current', case['kwargs'], case['user'])
        after, shared = await invoke(db, commands, 'computation', case['kwargs'], case['user'], audit=True)
        assert before == after
        assert wire(before) == wire(after), case['name']
        if case['name'] == 'permission_denied':
            assert after['http_error'] == 403
            assert sum(current['mongo_commands'].values()) == sum(shared['mongo_commands'].values()) == 0
        else:
            assert 'http_error' not in before


@pytest.mark.asyncio
async def test_new_request_observes_changed_database():
    async with isolated() as (raw, db, commands):
        await populate(raw, 4)
        first, _ = await invoke(db, commands, 'computation', DEFAULT_KWARGS, audit=True)
        await raw.unified_orders.update_one({'order_number': '1000000'}, {'$set': {'total_amount': 123, 'total_amount_sar': 123}})
        second, _ = await invoke(db, commands, 'computation', DEFAULT_KWARGS, audit=True)
        reference, _ = await invoke(db, commands, 'current', DEFAULT_KWARGS)
        assert wire(second) == wire(reference)
        assert first['totals']['total_sales'] != second['totals']['total_sales']


@pytest.mark.asyncio
async def test_paired_computation_performance():
    evidence = {'head': subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        'python': platform.python_version(), 'platform': platform.platform(), 'mongo': '8.0.12', 'cases': []}
    repeats = int(os.environ.get('DASHBOARD_PERF_REPEATS', '10'))
    sizes = [int(x) for x in os.environ.get('DASHBOARD_PERF_SIZES','10000,50000,100000').split(',')]
    async with isolated() as (raw, db, commands):
        for size in sizes:
            await populate(raw, size)
            for period, kwargs in [('month', DEFAULT_KWARGS), ('overlap', {**DEFAULT_KWARGS,'from_date':'2026-10-02','to_date':'2026-10-06'})]:
                samples = {'current': [], 'computation': []}
                for iteration in range(repeats+1):
                    digests = []
                    pair = {}
                    for mode in (['current','computation'] if iteration % 2 == 0 else ['computation','current']):
                        response, metrics = await invoke(db, commands, mode, kwargs)
                        assert response['totals']['total_orders'] == size
                        assert response['totals']['total_sales'] == 100*size
                        assert response['totals']['total_product_cost'] == 37.5*size
                        digests.append(metrics['json_sha256'])
                        pair[mode] = metrics
                        if iteration: samples[mode].append(metrics)
                        del response
                    assert digests[0] == digests[1], 'Full JSON mismatch in paired performance sample'
                    assert pair['computation']['shared_load_hits'] == 0
                    assert pair['current']['documents_read'] - pair['computation']['documents_read'] == 0
                    assert pair['current']['mongo_commands'] == pair['computation']['mongo_commands']
                    assert pair['current']['docs_by_collection'] == pair['computation']['docs_by_collection']
                    assert pair['current']['computations']['orders_to_parsed']['calls'] == 2
                    assert pair['computation']['computations']['orders_to_parsed']['calls'] == 1
                fields = ('handler_ms','python_cpu_ms','mongo_ms','documents_read','event_loop_max_lag_ms','serialization_ms','response_bytes','rss_peak_bytes','rss_peak_delta_bytes')
                summary = {mode:{field:{'p50':statistics.median(s[field] for s in rows),
                    'p95':sorted(s[field] for s in rows)[math.ceil(.95*len(rows))-1]} for field in fields} for mode,rows in samples.items()}
                evidence['cases'].append({'size':size,'period':period,'samples':samples,'summary':summary})
                Path(os.environ.get('DASHBOARD_COMPUTATION_EVIDENCE_PATH','../dashboard-computation-evidence.json')).write_text(json.dumps(evidence,indent=2),encoding='utf8')
                print('DASHBOARD_COMPUTATION '+json.dumps({'size':size,'period':period,'summary':summary}),flush=True)
