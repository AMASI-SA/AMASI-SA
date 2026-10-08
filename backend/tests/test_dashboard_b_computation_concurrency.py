"""All previously divergent mutation schedules must now have identical JSON."""
import asyncio
import json
import os
import subprocess
from pathlib import Path
import pytest
import dashboard_v2_routes as dash
from test_dashboard_b_concurrency_gate import CASES, Gate, GatedDB, differences
from test_dashboard_b_request_scope_mongo import isolated, invoke, wire
from test_dashboard_b_baseline_mongo import populate
from dashboard_b_equivalence_fixtures import DEFAULT_KWARGS


@pytest.mark.asyncio
async def test_computation_mutation_semantics_gate():
    evidence = {'head':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
                'mongo':'8.0.12', 'cases':[], 'runtime_adoption':'NOT_AUTHORIZED'}
    async with isolated() as (raw, readonly, commands):
        for name, kind, fields, filters, expected_difference in CASES:
            outcomes = {}
            for mode in ('current', 'computation'):
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
                assert gate.reads == 2
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
            diff = differences(outcomes['current']['response'], outcomes['computation']['response'])
            case = {'name':name, 'mutation':fields, 'read_boundary':'daily_costs repeated reads' if kind == 'ads' else 'legacy normalized order read -> V2 normalized order read',
                    'outcomes':outcomes, 'differences':diff, 'full_json_equal':wire(outcomes['current']['response']) == wire(outcomes['computation']['response'])}
            evidence['cases'].append(case)
            Path(os.environ.get('DASHBOARD_COMPUTATION_CONCURRENCY_PATH','../dashboard-computation-concurrency.json')).write_text(json.dumps(evidence,indent=2,ensure_ascii=False,default=str),encoding='utf8')
            assert not diff, (name, diff)
            assert case['full_json_equal']
            if expected_difference:
                assert all(v['response']['totals']['total_orders'] == 0 for v in outcomes.values())
                assert all(v['response']['totals']['total_sales'] == 0 for v in outcomes.values())
            if name == 'product_profile_cost':
                assert all(v['response']['totals']['total_product_cost'] == 55 for v in outcomes.values())
            if name == 'advertising_google':
                assert all(v['response']['totals']['total_ads_cost'] == 40 for v in outcomes.values())
            print('CONCURRENCY_GATE '+json.dumps({'case':name,'different':bool(diff),'paths':[d['path'] for d in diff]}),flush=True)
