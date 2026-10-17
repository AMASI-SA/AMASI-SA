"""Advertising RCA only: original vs coarse timers, not optimization."""
import json
import os
import platform
import subprocess
from pathlib import Path
from unittest.mock import patch
import pytest
import test_dashboard_b_cooperative_mongo as load
from dashboard_b_ads_profile_experiment import candidate
from dashboard_b_ads_instrumentation import Recorder
from dashboard_b_cost_instrumentation import Recorder as CostRecorder
from test_dashboard_b_request_scope_mongo import isolated


async def wave(db,commands,mode,scenario,baseline=False,phase=0):
    probe=Recorder();cost=CostRecorder();slices=[]
    def observed(row):
        row['id']=load.request_id.get();slices.append(row)
    def configured(*args,**kwargs):
        return candidate(*args,**kwargs,mode=mode,probe=probe,cost_probe=cost,cost_observer=observed)
    with patch.object(load,'cooperative_candidate',configured):
        result=await load.wave(db,commands,'cooperative-5',scenario,baseline=baseline,phase=phase)
    result.update(ads_sections=dict(probe.stats),ads_blocks=probe.blocks,cost_sections=dict(cost.stats),cost_blocks=cost.blocks,cost_slices=slices)
    return result


def summarize(waves):
    result=load.summarize(waves)
    result['ads_phases']={name:{field:load.quantiles([w['ads_sections'][name][field] for w in waves])
        for field in ['wall_ms','cpu_ms','max_wall_ms','max_cpu_ms','records']} for name in waves[0]['ads_sections']}
    return result


@pytest.mark.asyncio
async def test_ads_interactive_profile():
    size=int(os.environ['DASHBOARD_INTERACTIVE_SIZE']);scenario=os.environ['DASHBOARD_INTERACTIVE_SCENARIO']
    repeats=int(os.environ.get('DASHBOARD_INTERACTIVE_REPEATS','6'))
    evidence={'head':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        'python':platform.python_version(),'platform':platform.platform(),'mongo':'8.0.12',
        'size':size,'scenario':scenario,'reference':'parser5 and cost5 fixed; original ads vs coarse observer ONLY',
        'runtime_adoption':False,'optimization':False,'waves':{'current':[],'coarse':[]}}
    path=Path(os.environ['DASHBOARD_ADS_INTERACTIVE_PATH'])
    async with isolated() as (raw,db,commands):
        await load.seed(raw,size,scenario)
        evidence['baseline']=await wave(db,commands,'current',scenario,baseline=True)
        for iteration in range(repeats+1):
            digests={};counts={}
            for mode in (['current','coarse'] if iteration%2==0 else ['coarse','current']):
                result=await wave(db,commands,mode,scenario,phase=(iteration*.037)%.25)
                evidence['last_wave']={'mode':mode,'iteration':iteration,'result':result}
                path.write_text(json.dumps(evidence),encoding='utf8')
                rows=[r for r in result['load']['records'] if r['kind']=='dashboard']
                assert all(r.get('status')==200 for r in rows),rows
                assert not any(r.get('generator_overflow') for r in result['load']['records'])
                digests[mode]=sorted((r['merchant'],r['digest']) for r in rows)
                counts[mode]=sum(r['kind']=='dashboard' for r in result['mongo_events'])
                if iteration:evidence['waves'][mode].append(result)
                print('ADS_RCA_WAVE '+json.dumps({'size':size,'scenario':scenario,'iteration':iteration,'mode':mode}),flush=True)
            assert digests['current']==digests['coarse']
            assert counts['current']==counts['coarse']
            evidence['summary']={m:summarize(ws) for m,ws in evidence['waves'].items() if ws}
            path.write_text(json.dumps(evidence),encoding='utf8')
    print('ADS_RCA_RESULT '+json.dumps(evidence['summary']),flush=True)
