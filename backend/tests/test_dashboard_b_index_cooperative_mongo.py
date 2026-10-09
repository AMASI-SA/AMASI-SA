"""Indexing-only four-arm experiment with independent interactive client."""
import json
import os
import platform
import subprocess
import time
from pathlib import Path
from unittest.mock import patch
import pytest
import test_dashboard_b_cooperative_mongo as load
from dashboard_b_index_cooperative_experiment import candidate
from dashboard_b_cost_instrumentation import Recorder as CostRecorder
from test_dashboard_b_request_scope_mongo import isolated


MODES = ['current', 'indexing-1', 'indexing-5', 'indexing-10']
# Williams ordering: every arm occurs once at each position in four waves.
ORDERS = [[0, 1, 3, 2], [1, 2, 0, 3], [2, 3, 1, 0], [3, 0, 2, 1]]


async def wave(db, commands, mode, scenario, baseline=False, phase=0):
    cost = CostRecorder()
    cost_slices, ads_slices, index_slices = [], [], []
    def observed(target):
        def record(row):
            row['id'] = load.request_id.get()
            target.append(row)
        return record
    from contextlib import contextmanager
    @contextmanager
    def configured(*args, **kwargs):
        budget = None if mode == 'current' else float(mode.split('-')[1])
        with candidate(*args, **kwargs, index_budget_ms=budget,
                       index_observer=observed(index_slices), cost_probe=cost,
                       cost_observer=observed(cost_slices),
                       ads_observer=observed(ads_slices)) as pair:
            yield pair
    with patch.object(load, 'cooperative_candidate', configured):
        result = await load.wave(db, commands, 'cooperative-5', scenario, baseline=baseline, phase=phase)
    result.update(index_slices=index_slices, ads_slices=ads_slices,
                  cost_sections=dict(cost.stats), cost_blocks=cost.blocks, cost_slices=cost_slices)
    return result


def summarize(waves):
    result = load.summarize(waves)
    result['index_slices_cpu_ms'] = load.quantiles([s['cpu_ms'] for w in waves for p in w['index_slices'] for s in p['slices']])
    result['index_slices_wall_ms'] = load.quantiles([(s['end']-s['start'])*1000 for w in waves for p in w['index_slices'] for s in p['slices']])
    result['index_yields'] = sum(p['yields'] for w in waves for p in w['index_slices'])
    # Whole async-call CPU spans include work of other coroutines: do NOT sum
    # or label them exclusive CPU. Slice CPU and total API-thread CPU are separate.
    return result


@pytest.mark.asyncio
async def test_index_cooperative_interactive():
    size=int(os.environ['DASHBOARD_INTERACTIVE_SIZE']);scenario=os.environ['DASHBOARD_INTERACTIVE_SCENARIO']
    repeats=int(os.environ.get('DASHBOARD_INTERACTIVE_REPEATS','8'))
    evidence={'head':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        'python':platform.python_version(),'platform':platform.platform(),'mongo':'8.0.12',
        'size':size,'scenario':scenario,'reference':'parser5, cost5 and ads1 fixed; only product indexing budget differs',
        'materiality_rule':'diagnostic: >10% AND >25ms interactive p95/p99; assess Dashboard and throughput too',
        'runtime_adoption':False,'optimization':'product indexing experiment only','waves':{mode:[] for mode in MODES}}
    path=Path(os.environ['DASHBOARD_INDEX_COOPERATIVE_PATH'])
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
                assert result['index_slices'] and all(p['completed'] for p in result['index_slices'])
                if size >= 10000 and mode != 'current':
                    assert all(p['yields'] > 0 for p in result['index_slices'])
                if iteration:evidence['waves'][mode].append(result)
                print('INDEX_COOPERATIVE_WAVE '+json.dumps({'size':size,'scenario':scenario,'iteration':iteration,'mode':mode}),flush=True)
            assert all(digests[m] == digests['current'] for m in MODES)
            assert all(counts[m] == counts['current'] for m in MODES)
            evidence['summary']={m:summarize(ws) for m,ws in evidence['waves'].items() if ws}
            path.write_text(json.dumps(evidence),encoding='utf8')
    print('INDEX_COOPERATIVE_RESULT '+json.dumps(evidence['summary']),flush=True)
