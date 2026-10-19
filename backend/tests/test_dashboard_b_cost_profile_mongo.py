"""Phase1 only: measure whole cost loop before designing a candidate."""
import asyncio
import hashlib
import json
import os
import platform
import subprocess
import time
from pathlib import Path

import pytest
from bson import BSON
import dashboard_v2_routes as dash
from dashboard_b_cost_instrumentation import Recorder, instrumented
from test_dashboard_b_baseline_mongo import populate
from test_dashboard_b_request_scope_mongo import isolated, wire
from dashboard_b_equivalence_fixtures import CASES, OWNER, seed_mixed


@pytest.mark.asyncio
async def test_cost_profile_equivalence():
    async with isolated() as (raw, db, commands):
        await seed_mixed(raw)
        orders = await db.unified_orders.find({'user_id':OWNER['id']},{'_id':0}).to_list(100000)
        before = wire(orders)
        expected = await dash.build_mezan_v2_product_cost(db, OWNER['id'], orders)
        for fine in (False,True):
            probe=Recorder()
            result=await instrumented(probe,fine)(db,OWNER['id'],orders)
            assert wire(result)==wire(expected)
            assert wire(orders)==before
            assert probe.stats['cost_profit_loop']['records']==len(orders)


@pytest.mark.asyncio
async def test_cost_phase_profile():
    sizes=[int(s) for s in os.environ.get('DASHBOARD_COST_SIZES','10000,100000').split(',')]
    repeats=int(os.environ.get('DASHBOARD_COST_REPEATS','3'))
    evidence={'head':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
        'phase':'instrumentation only; no cooperative candidate yet','python':platform.python_version(),
        'platform':platform.platform(),'mongo':'8.0.12','samples':[]}
    path=Path(os.environ['DASHBOARD_COST_PROFILE_PATH'])
    async with isolated() as (raw,db,commands):
        for size in sizes:
            await populate(raw,size)
            orders=await db.unified_orders.find({'user_id':'fixture-owner'},{'_id':0}).to_list(100000)
            # Actual encoded input size, outside every timed sample.
            input_bytes=sum(len(BSON.encode(row)) for row in orders)
            catalog_bytes=0
            async for row in raw[dash.PRODUCTS].find({'user_id':'fixture-owner'},dash.PRODUCT_COST_CATALOG_PROJECTION):
                catalog_bytes+=len(BSON.encode(row))
            for iteration in range(repeats+1):
                digests=[]; query_counts=[]
                modes=['current','coarse','fine']
                if iteration%2:modes.reverse()
                for mode in modes:
                    probe=Recorder()
                    fn=dash.build_mezan_v2_product_cost if mode=='current' else instrumented(probe,mode=='fine')
                    lag=[];done=asyncio.Event()
                    async def heartbeat():
                        while not done.is_set():
                            expected=time.perf_counter()+.01
                            await asyncio.sleep(.01)
                            lag.append(max(0,time.perf_counter()-expected)*1000)
                    pulse=asyncio.create_task(heartbeat())
                    await asyncio.sleep(0)
                    commands.reset();start=time.perf_counter();cpu=time.thread_time()
                    try:
                        result=await fn(db,'fixture-owner',orders)
                        elapsed=(time.perf_counter()-start)*1000
                        api_cpu=(time.thread_time()-cpu)*1000
                    finally:
                        done.set();await pulse
                    digest=hashlib.sha256(wire(result)).hexdigest()
                    digests.append(digest);query_counts.append(dict(commands.counts))
                    assert not commands.writes
                    record={'size':size,'iteration':iteration,'mode':mode,'wall_ms':elapsed,
                        'api_thread_cpu_ms':api_cpu,'input_order_bson_bytes':input_bytes,
                        'input_catalog_bson_bytes':catalog_bytes,'items':sum(len(o.get('products') or []) for o in orders),
                        'product_rows':len(result['product_rows']),'mongo_commands':dict(commands.counts),
                        'documents_read':sum(commands.docs.values()),'mongo_driver_ms':commands.duration_ms,
                        'heartbeat_max_ms':max(lag,default=0),'sections':dict(probe.stats),
                        'blocks':probe.blocks,'json_sha256':digest}
                    if iteration:evidence['samples'].append(record)
                    path.write_text(json.dumps(evidence,indent=2),encoding='utf8')
                    print('COST_PROFILE '+json.dumps(record),flush=True)
                assert len(set(digests))==1
                assert all(q==query_counts[0] for q in query_counts)
