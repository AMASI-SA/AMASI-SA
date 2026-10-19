"""Cost-only HTTP comparison; unchanged parser5ms in every arm."""
import json
import os
import platform
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
import test_dashboard_b_cooperative_mongo as load
from dashboard_b_cost_cooperative_experiment import candidate
from dashboard_b_cost_instrumentation import Recorder
from test_dashboard_b_request_scope_mongo import isolated


async def wave(db,commands,budget,scenario,baseline=False,phase=0):
    probe=Recorder(); slices=[]
    def observed(row):
        row['id']=load.request_id.get()
        slices.append(row)
    def configured(*args,**kwargs):
        return candidate(*args,**kwargs,cost_budget_ms=budget,cost_probe=probe,cost_observer=observed)
    with patch.object(load,'cooperative_candidate',configured):
        result=await load.wave(db,commands,'cooperative-5',scenario,baseline=baseline,phase=phase)
    result['cost_sections']=dict(probe.stats)
    result['cost_blocks']=probe.blocks
    result['cost_slices']=slices
    result['cost_budget_ms']=budget
    return result


def summarize(waves):
    report=load.summarize(waves)
    report['cost_slices_cpu_ms']=load.quantiles([s['cpu_ms'] for w in waves for p in w['cost_slices'] for s in p['slices']])
    report['cost_slices_wall_ms']=load.quantiles([(s['end']-s['start'])*1000 for w in waves for p in w['cost_slices'] for s in p['slices']])
    report['cost_sections_cpu_ms_per_wave']={name:load.quantiles([w['cost_sections'][name]['cpu_ms'] for w in waves]) for name in waves[0]['cost_sections']}
    return report


@pytest.mark.asyncio
async def test_cost_interactive():
    size=int(os.environ['DASHBOARD_INTERACTIVE_SIZE'])
    scenario=os.environ['DASHBOARD_INTERACTIVE_SCENARIO']
    repeats=int(os.environ.get('DASHBOARD_INTERACTIVE_REPEATS','6'))
    arms={'cost-current':None,'cost-1':1,'cost-5':5,'cost-10':10}
    evidence={'head':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        'python':platform.python_version(),'platform':platform.platform(),'mongo':'8.0.12',
        'size':size,'scenario':scenario,'runtime_adoption':'NOT_PERFORMED',
        'reference':'Original cost loop; existing computation-only and parser5ms fixed in ALL arms',
        'materiality_rule':'diagnostic >10% AND >25ms interactive p95/p99; no SLO claim',
        'baseline':{},'waves':{m:[] for m in arms}}
    path=Path(os.environ['DASHBOARD_COST_INTERACTIVE_PATH'])
    async with isolated() as (raw,db,commands):
        await load.seed(raw,size,scenario)
        evidence['baseline']=await wave(db,commands,None,scenario,baseline=True)
        for iteration in range(repeats+1):
            names=list(arms);names=names[iteration%4:]+names[:iteration%4]
            if iteration%2:names.reverse()
            digests={};query_counts={}
            for name in names:
                result=await wave(db,commands,arms[name],scenario,phase=(iteration*.037)%.25)
                evidence['last_wave']={'mode':name,'iteration':iteration,'result':result}
                path.write_text(json.dumps(evidence),encoding='utf8')
                assert not any(r.get('generator_overflow') for r in result['load']['records'])
                dashboards=[r for r in result['load']['records'] if r['kind']=='dashboard']
                assert all(r.get('status')==200 for r in dashboards),dashboards
                digests[name]=sorted((r['merchant'],r['digest']) for r in dashboards)
                query_counts[name]=sum(r['kind']=='dashboard' for r in result['mongo_events'])
                if arms[name] is not None:
                    assert result['cost_slices'] and all(r['completed'] for r in result['cost_slices'])
                    if size>=10000:assert all(r['yields']>0 for r in result['cost_slices'])
                if iteration:evidence['waves'][name].append(result)
                print('COST_INTERACTIVE_WAVE '+json.dumps({'size':size,'scenario':scenario,'iteration':iteration,'mode':name}),flush=True)
            assert all(v==digests['cost-current'] for v in digests.values()), 'HTTP byte difference'
            assert len(set(query_counts.values()))==1,'Dashboard Mongo command count changed'
            evidence['summary']={m:summarize(ws) for m,ws in evidence['waves'].items() if ws}
            path.write_text(json.dumps(evidence),encoding='utf8')
    print('COST_INTERACTIVE_RESULT '+json.dumps(evidence['summary']),flush=True)
