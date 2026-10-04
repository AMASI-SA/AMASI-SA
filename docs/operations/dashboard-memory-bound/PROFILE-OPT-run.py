"""Isolated stage profiler; inclusive async timings overlap and must not be summed.
Synchronous timings include exclusive counters to separate serialization/SQL from
calculator work. Run alone; final acceptance uses uninstrumented benchmark mode.
"""
import sys, asyncio, json, uuid, time, inspect, functools
from pathlib import Path
from collections import defaultdict
from contextvars import ContextVar
ROOT=Path(__file__).resolve().parents[3]
sys.path[:0]=[str(ROOT/'backend/tests'),str(ROOT/'backend'),str(ROOT)]
import benchmark_dashboard_summary_memory as bench
import dashboard_spill as spill
import dashboard_order_accumulator as acc
import dashboard_order_reads as reads
import dashboard_product_pages as products
from motor.motor_asyncio import AsyncIOMotorCursor
OUT=ROOT/'docs/operations/dashboard-memory-bound'
count=int(sys.argv[1]) if len(sys.argv)>1 else 1000
label=sys.argv[2] if len(sys.argv)>2 else 'current'
counters=defaultdict(lambda:dict(calls=0,inclusive_s=0.,exclusive_s=0.))
stack=ContextVar('sync_profile',default=())

def measured(original,label):
    if inspect.iscoroutinefunction(original):
        @functools.wraps(original)
        async def wrapped(*args,**kwargs):
            started=time.perf_counter()
            try:return await original(*args,**kwargs)
            finally:
                c=counters[label];c['calls']+=1;c['inclusive_s']+=time.perf_counter()-started
        return wrapped
    @functools.wraps(original)
    def wrapped(*args,**kwargs):
        parent=stack.get();frame=[0.];token=stack.set(parent+(frame,));started=time.perf_counter()
        try:return original(*args,**kwargs)
        finally:
            elapsed=time.perf_counter()-started;stack.reset(token)
            if parent:parent[-1][0]+=elapsed
            c=counters[label];c['calls']+=1;c['inclusive_s']+=elapsed;c['exclusive_s']+=elapsed-frame[0]
    return wrapped

def wrap(obj,name,label):setattr(obj,name,measured(getattr(obj,name),label))
for name in ['_encode','_decode','_store_value']:wrap(spill,name,'serialization.'+name)
wrap(spill.DashboardSpill,'execute','temporary_storage.sql_execute')
for name in ['observe','observe_fee_batch','finish']:wrap(acc.DashboardOrderAccumulator,name,'financial_labels.'+name)
for name in ['orders_to_parsed','match_settings','_same_method','shipping_breakdown']:wrap(acc,name,'calculator.'+name)
wrap(reads,'_load_bounded','batching.order_snapshot')
wrap(products,'load_product_context','product.catalog')
wrap(products,'finalize_product_pages','product.finalize')
old_to_list=AsyncIOMotorCursor.to_list
async def to_list(*args,**kwargs):
    start=time.perf_counter()
    try:return await old_to_list(*args,**kwargs)
    finally:
        c=counters['query.batch_fetch_wait'];c['calls']+=1;c['inclusive_s']+=time.perf_counter()-start
AsyncIOMotorCursor.to_list=to_list
original_extract=bench.extract_dashboard

def extract(*args,**kwargs):
    function=original_extract(*args,**kwargs)
    for name in ('compute_balances','summarize_orders_sar','order_total_sar','effective_product_cost'):
        if name in function.__globals__:function.__globals__[name]=measured(function.__globals__[name],'legacy.'+name)
    return measured(function,'stage.legacy_summary')
bench.extract_dashboard=extract
original_endpoint=bench.v2_endpoint

def endpoint(*args,**kwargs):
    function=original_endpoint(*args,**kwargs);ns=inspect.unwrap(function).__globals__
    for name in ('build_mezan_v2_product_cost','build_mezan_v2_ads','_dashboard_recurring_totals','_gather_dashboard_reads','summarize_orders_sar','build_salla_ads_executive_breakdown'):
        if name in ns:ns[name]=measured(ns[name],'stage.'+name)
    return function
bench.v2_endpoint=endpoint
async def run():
    database=bench.DB_PREFIX+'stage_'+uuid.uuid4().hex[:16]
    try:
        await bench.seed(database,count,1000,1024)
        result=await bench.worker('after',database,1)
        result['dataset_orders']=count
        result['profile_caveat']='Async stage wall intervals overlap; exclusive synchronous intervals exclude nested profiled calls. Query command time is a separate driver measurement. Profiling adds overhead; final latency needs uninstrumented execution.'
        result['stage_timings']=dict(sorted(counters.items(),key=lambda x:-x[1]['inclusive_s']))
        path=OUT/f'PROFILE-{label}-{count}.json'
        path.write_text(json.dumps(result,indent=2),encoding='utf8')
        print(json.dumps(dict(file=str(path),wall=result['wall_seconds'],timings=result['stage_timings'])),flush=True)
    finally:await bench.cleanup(database)
asyncio.run(run())

