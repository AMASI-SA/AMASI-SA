"""Advertising-only four-arm experiment with independent interactive client."""
import json
import os
import platform
import subprocess
import time
from pathlib import Path
from unittest.mock import patch
import pytest
import test_dashboard_b_cooperative_mongo as load
from dashboard_b_ads_cooperative_experiment import candidate
from dashboard_b_cost_instrumentation import Recorder as CostRecorder
from test_dashboard_b_request_scope_mongo import isolated


MODES = ['current', 'advertising-1', 'advertising-5', 'advertising-10']
# Williams ordering: every arm occurs once at each position in four waves.
ORDERS = [[0, 1, 3, 2], [1, 2, 0, 3], [2, 3, 1, 0], [3, 0, 2, 1]]


async def wave(db, commands, mode, scenario, baseline=False, phase=0):
    cost = CostRecorder()
    cost_slices, ads_slices, ads_calls = [], [], []
    def observed(target):
        def record(row):
            row['id'] = load.request_id.get()
            target.append(row)
        return record
    from contextlib import contextmanager
    @contextmanager
    def configured(*args, **kwargs):
        budget = None if mode == 'current' else float(mode.split('-')[1])
        with candidate(*args, **kwargs, ads_budget_ms=budget,
                       ads_observer=observed(ads_slices), cost_probe=cost,
                       cost_observer=observed(cost_slices)) as (factory, legacy):
            function = factory.__globals__['build_salla_ads_executive_breakdown']
            async def measured(orders, advertising):
                start, cpu = time.perf_counter(), time.thread_time()
                complete = False
                try:
                    result = await function(orders, advertising)
                    complete = True
                    return result
                finally:
                    end, elapsed = time.perf_counter(), (time.thread_time()-cpu)*1000
                    row = dict(id=load.request_id.get(), start=start, end=end,
                               cpu_ms=elapsed, records=len(orders), completed=complete)
                    ads_calls.append(row)
                    if mode == 'current':
                        observed(ads_slices)(dict(slices=[row], yields=0, completed=complete))
            factory.__globals__['build_salla_ads_executive_breakdown'] = measured
            yield factory, legacy
    with patch.object(load, 'cooperative_candidate', configured):
        result = await load.wave(db, commands, 'cooperative-5', scenario, baseline=baseline, phase=phase)
    result.update(ads_calls=ads_calls, ads_slices=ads_slices,
                  cost_sections=dict(cost.stats), cost_blocks=cost.blocks, cost_slices=cost_slices)
    return result


def summarize(waves):
    result = load.summarize(waves)
    result['ads_slices_cpu_ms'] = load.quantiles([s['cpu_ms'] for w in waves for p in w['ads_slices'] for s in p['slices']])
    result['ads_slices_wall_ms'] = load.quantiles([(s['end']-s['start'])*1000 for w in waves for p in w['ads_slices'] for s in p['slices']])
    result['ads_yields'] = sum(p['yields'] for w in waves for p in w['ads_slices'])
    result['ads_call_wall_ms'] = load.quantiles([(p['end']-p['start'])*1000 for w in waves for p in w['ads_calls']])
    # Whole async-call CPU spans include work of other coroutines: do NOT sum
    # or label them exclusive CPU. Slice CPU and total API-thread CPU are separate.
    return result


@pytest.mark.asyncio
async def test_ads_cooperative_interactive():
    size=int(os.environ['DASHBOARD_INTERACTIVE_SIZE']);scenario=os.environ['DASHBOARD_INTERACTIVE_SCENARIO']
    repeats=int(os.environ.get('DASHBOARD_INTERACTIVE_REPEATS','8'))
    evidence={'head':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        'python':platform.python_version(),'platform':platform.platform(),'mongo':'8.0.12',
        'size':size,'scenario':scenario,'reference':'parser5 and cost5 fixed; only advertising budget differs',
        'runtime_adoption':False,'optimization':'advertising experiment only','waves':{mode:[] for mode in MODES}}
    path=Path(os.environ['DASHBOARD_ADS_COOPERATIVE_PATH'])
    async with isolated() as (raw,db,commands):
        await load.seed(raw,size,scenario)
        evidence['baseline']=await wave(db,commands,'current',scenario,baseline=True)
        for iteration in range(repeats+1):
            digests={};counts={}
            for mode in [MODES[i] for i in ORDERS[(iteration-1)%4]]:
                result=await wave(db,commands,mode,scenario,phase=(iteration*.037)%.25)
                evidence['last_wave']={'mode':mode,'iteration':iteration,'result':result}
                path.write_text(json.dumps(evidence),encoding='utf8')
                rows=[r for r in result['load']['records'] if r['kind']=='dashboard']
                assert all(r.get('status')==200 for r in rows),rows
                assert not any(r.get('generator_overflow') for r in result['load']['records'])
                digests[mode]=sorted((r['merchant'],r['digest']) for r in rows)
                counts[mode]=sum(r['kind']=='dashboard' for r in result['mongo_events'])
                assert result['ads_slices'] and all(p['completed'] for p in result['ads_slices'])
                if size >= 10000 and mode != 'current':
                    assert all(p['yields'] > 0 for p in result['ads_slices'])
                if iteration:evidence['waves'][mode].append(result)
                print('ADS_COOPERATIVE_WAVE '+json.dumps({'size':size,'scenario':scenario,'iteration':iteration,'mode':mode}),flush=True)
            assert all(digests[m] == digests['current'] for m in MODES)
            assert all(counts[m] == counts['current'] for m in MODES)
            evidence['summary']={m:summarize(ws) for m,ws in evidence['waves'].items() if ws}
            path.write_text(json.dumps(evidence),encoding='utf8')
    print('ADS_COOPERATIVE_RESULT '+json.dumps(evidence['summary']),flush=True)
