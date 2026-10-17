"""Original/coarse/fine calibration of captured real Dashboard input objects."""
import asyncio
import copy
import hashlib
import gc
import json
import os
import platform
import subprocess
import time
from pathlib import Path
from unittest.mock import patch
import pytest
import product_catalog_cost_resolution as catalog
import dashboard_b_computation_experiment as computation
from dashboard_b_index_instrumentation import Recorder, instrumented, candidate
from test_dashboard_b_baseline_mongo import populate
from dashboard_b_equivalence_fixtures import DEFAULT_KWARGS
from test_dashboard_b_request_scope_mongo import isolated,invoke
from test_dashboard_b_ads_profile_correctness import encoded as wire


@pytest.mark.asyncio
async def test_index_phase_profile():
    evidence={'head':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        'python':platform.python_version(),'platform':platform.platform(),'mongo':'8.0.12',
        'runtime_adoption':False,'optimization':False,'samples':[]}
    path=Path(os.environ['DASHBOARD_INDEX_PROFILE_PATH'])
    async with isolated() as (raw,db,commands):
        for size in [int(s) for s in os.environ.get('DASHBOARD_INDEX_SIZES','10000,100000').split(',')]:
            await populate(raw,size)
            captured=[]
            responses=[];read_counts=[]
            for mode in ['current','coarse','fine']:
                probe=Recorder()
                def configured(db,scope):
                    return candidate(db,scope,mode=mode,probe=probe,
                                     capture=captured.append if mode=='current' else None)
                with patch.object(computation,'candidate',configured):
                    result,metrics=await invoke(db,commands,'computation',DEFAULT_KWARGS)
                responses.append(hashlib.sha256(wire(result)).hexdigest())
                read_counts.append((metrics['mongo_commands'],metrics['documents_read']))
                del result
            assert len(set(responses))==1
            assert all(r==read_counts[0] for r in read_counts)
            assert len(captured)==1
            products=captured.pop()
            evidence.setdefault('dashboard_equivalence',[]).append(dict(size=size,json_sha256=responses[0],
                equivalent=True,reads=read_counts))
            await profile(products, 'normalized_db_catalog', evidence, path, commands)
            if size==10000:
                rich=copy.deepcopy(products)
                for i,row in enumerate(rich):
                    row['variants']=[{'id':str(i)+'-a','sku':str(i)+'-a','cost_price_from_salla':None},
                                     {'id':str(i)+'-b','sku':str(i)+'-b','cost_price_from_salla':10}]
                    row['raw_salla_details']={'cost_price':12,'variants':[
                        {'id':str(i)+'-a','sku':str(i)+'-a','cost_price':8},
                        {'id':str(i)+'-c','sku':str(i)+'-c','cost_price':9}]}
                await profile(rich,'variant_fixture',evidence,path,commands)


async def profile(products, workload, evidence, path, commands):
    size=len(products)
    input_bytes=len(wire(products))
    input_digest=hashlib.sha256(wire(products)).hexdigest()
    counts={'products':size,
            'input_variants':sum(len(catalog._list(p.get('variants'))) for p in products),
            'raw_variants':sum(len(catalog._raw_variants(p)) for p in products)}
    for iteration in range(4):
        digests=[]
        modes=['current','coarse','fine']
        if iteration%2:modes.reverse()
        for mode in modes:
            probe=Recorder()
            fn=catalog.index_current_catalog_products if mode=='current' else instrumented(probe,fine=mode=='fine')
            lag=[];done=asyncio.Event()
            async def beat():
                while not done.is_set():
                    expected=time.perf_counter()+.01
                    await asyncio.sleep(.01)
                    lag.append(max(0,time.perf_counter()-expected)*1000)
            pulse=asyncio.create_task(beat());await asyncio.sleep(0)
            gc_events=[];gc_start={}
            def gc_probe(phase, info):
                if phase=='start':gc_start[info['generation']]=time.perf_counter()
                else:
                    started=gc_start.pop(info['generation'],None)
                    if started is not None:gc_events.append((time.perf_counter()-started)*1000)
            gc.callbacks.append(gc_probe)
            commands.reset();start=time.perf_counter();cpu=time.thread_time()
            try:
                value=fn(products)
                wall=(time.perf_counter()-start)*1000
                cpu_ms=(time.thread_time()-cpu)*1000
            finally:
                gc.callbacks.remove(gc_probe)
                done.set();await pulse
            digest=hashlib.sha256(wire(value)).hexdigest();digests.append(digest)
            assert not commands.counts and not commands.writes
            assert hashlib.sha256(wire(products)).hexdigest()==input_digest
            row={'size':size,'mode':mode,'iteration':iteration,'wall_ms':wall,'cpu_ms':cpu_ms,
                'input_json_bytes':input_bytes,'input_counts':counts,
                'output_index_counts':[len(index) for index in value],'workload':workload,'heartbeat_max_ms':max(lag,default=0),
                'gc_ms':sum(gc_events),'gc_collections':len(gc_events),'sections':dict(probe.stats),'blocks':probe.blocks,'json_sha256':digest,
                'function_mongo_commands':0,'function_serialization':'outside timed function'}
            if iteration:evidence['samples'].append(row)
            path.write_text(json.dumps(evidence),encoding='utf8')
            print('INDEX_PROFILE '+json.dumps(row),flush=True)
            del value  # Do not time destruction of the previous index in the next call.
        assert len(set(digests))==1


# Reuse the established mixed-result and independent-read mutation schedules;
# replace ONLY their diagnostic candidate seam with the indexing observer.
import test_dashboard_b_ads_profile_correctness as existing_cases


@pytest.mark.asyncio
@pytest.mark.parametrize('case', existing_cases.CASES, ids=lambda row: row['name'])
async def test_index_dashboard_mixed_filters_permissions_and_tenants(case):
    with patch.object(existing_cases, 'candidate', candidate):
        await existing_cases.test_dashboard_profile_mixed_permissions_and_tenants(case)


@pytest.mark.asyncio
@pytest.mark.parametrize('mutation', existing_cases.MUTATIONS, ids=lambda row: row[0])
async def test_index_existing_read_mutations(mutation):
    with patch.object(existing_cases, 'candidate', candidate):
        await existing_cases.test_ads_profile_existing_independent_read_mutations(mutation)
