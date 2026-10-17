"""Original/coarse/fine calibration of captured real Dashboard input objects."""
import asyncio
import copy
import hashlib
import json
import os
import platform
import subprocess
import time
from pathlib import Path
from unittest.mock import patch
import pytest
import dashboard_v2_ads_executive as ads_module
import dashboard_b_computation_experiment as computation
from dashboard_b_ads_instrumentation import Recorder,instrumented
from dashboard_b_ads_profile_experiment import candidate
from test_dashboard_b_baseline_mongo import populate
from dashboard_b_equivalence_fixtures import DEFAULT_KWARGS
from test_dashboard_b_request_scope_mongo import isolated,invoke,wire


@pytest.mark.asyncio
async def test_ads_phase_profile():
    evidence={'head':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        'python':platform.python_version(),'platform':platform.platform(),'mongo':'8.0.12',
        'runtime_adoption':False,'optimization':False,'samples':[]}
    path=Path(os.environ['DASHBOARD_ADS_PROFILE_PATH'])
    async with isolated() as (raw,db,commands):
        for size in [int(s) for s in os.environ.get('DASHBOARD_ADS_SIZES','10000,100000').split(',')]:
            await populate(raw,size)
            captured=[]
            def capture(orders,ads):
                captured.append((orders,copy.deepcopy(ads)))
            def configured(db,scope):
                return candidate(db,scope,mode='current',capture=capture)
            with patch.object(computation,'candidate',configured):
                result,_=await invoke(db,commands,'computation',DEFAULT_KWARGS)
            del result
            assert len(captured)==1
            orders,ads=captured.pop()
            input_bytes=len(wire(orders));ads_bytes=len(wire(ads))
            input_digest=hashlib.sha256(wire([orders,ads])).hexdigest()
            for iteration in range(4):
                digests=[]
                modes=['current','coarse','fine']
                if iteration%2:modes.reverse()
                for mode in modes:
                    probe=Recorder()
                    fn=ads_module.build_salla_ads_executive_breakdown if mode=='current' else instrumented(probe,fine=mode=='fine')
                    lag=[];done=asyncio.Event()
                    async def beat():
                        while not done.is_set():
                            expected=time.perf_counter()+.01
                            await asyncio.sleep(.01)
                            lag.append(max(0,time.perf_counter()-expected)*1000)
                    pulse=asyncio.create_task(beat());await asyncio.sleep(0)
                    commands.reset();start=time.perf_counter();cpu=time.thread_time()
                    try:
                        value=fn(orders,ads)
                        wall=(time.perf_counter()-start)*1000
                        cpu_ms=(time.thread_time()-cpu)*1000
                    finally:
                        done.set();await pulse
                    digest=hashlib.sha256(wire(value)).hexdigest();digests.append(digest)
                    assert not commands.counts and not commands.writes
                    assert hashlib.sha256(wire([orders,ads])).hexdigest()==input_digest
                    row={'size':size,'mode':mode,'iteration':iteration,'wall_ms':wall,'cpu_ms':cpu_ms,
                        'order_input_json_bytes':input_bytes,'ads_input_json_bytes':ads_bytes,
                        'providers':len(value['providers']),'orders':len(orders),'heartbeat_max_ms':max(lag,default=0),
                        'sections':dict(probe.stats),'blocks':probe.blocks,'json_sha256':digest,
                        'function_mongo_commands':0,'function_serialization':'outside timed function'}
                    if iteration:evidence['samples'].append(row)
                    path.write_text(json.dumps(evidence),encoding='utf8')
                    print('ADS_PROFILE '+json.dumps(row),flush=True)
                assert len(set(digests))==1
