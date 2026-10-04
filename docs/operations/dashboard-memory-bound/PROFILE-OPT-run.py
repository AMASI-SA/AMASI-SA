import sys, asyncio, json, uuid, cProfile, pstats, time
from pathlib import Path
from collections import defaultdict
ROOT=Path(r'C:/Users/amasi/dashboard-memory-bound')
sys.path[:0]=[str(ROOT/'backend/tests'),str(ROOT/'backend'),str(ROOT)]
import benchmark_dashboard_summary_memory as bench
import dashboard_spill as spill
import dashboard_order_accumulator as acc
OUT=ROOT/'docs/operations/dashboard-memory-bound'
mode=sys.argv[1]
counters=defaultdict(lambda:[0,0.0])
def wrap(obj,name,label):
    original=getattr(obj,name)
    def measured(*args,**kwargs):
        key=label
        if label=='sqlite':key+=' '+str(args[1]).split(' VALUES')[0].split(' WHERE')[0][:110]
        start=time.perf_counter()
        try:return original(*args,**kwargs)
        finally:
            counters[key][0]+=1;counters[key][1]+=time.perf_counter()-start
    setattr(obj,name,measured)
if mode=='direct':
    for name in ['_encode','_decode','_store_value']:wrap(spill,name,name)
    wrap(spill.DashboardSpill,'execute','sqlite')
    for name in ['observe','observe_fee_batch','finish']:wrap(acc.DashboardOrderAccumulator,name,'acc.'+name)
    for name in ['orders_to_parsed','match_settings','_same_method','shipping_breakdown']:wrap(acc,name,'acc.'+name)
async def run():
    db=bench.DB_PREFIX+'profile_opt_'+uuid.uuid4().hex[:16]
    try:
        await bench.seed(db,1000,1000,1024)
        profile=cProfile.Profile()
        if mode=='profile':profile.enable()
        result=await bench.worker('after',db,1)
        if mode=='profile':
            profile.disable();profile.dump_stats(str(OUT/'PROFILE-OPT-1000.prof'))
            with (OUT/'PROFILE-OPT-1000-TOP.txt').open('w',encoding='utf8') as f:
                stats=pstats.Stats(profile,stream=f).strip_dirs();stats.sort_stats('tottime').print_stats(70);stats.sort_stats('cumulative').print_stats(70)
        result['direct_boundaries']=dict(sorted(counters.items(),key=lambda item:-item[1][1]))
        (OUT/('PROFILE-OPT-1000-'+mode.upper()+'.json')).write_text(json.dumps(result,indent=2),encoding='utf8')
        print(json.dumps(result))
    finally:await bench.cleanup(db)
asyncio.run(run())

