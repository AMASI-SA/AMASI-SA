"""Diagnostic-only runner. Target application files stay at the approved SHA."""
import sys, os, asyncio, json, time, types, inspect, functools, uuid, subprocess
from pathlib import Path
from collections import defaultdict
from contextvars import ContextVar
ROOT=Path(os.environ.get('FORENSIC_ROOT',Path.cwd()))
sys.path[:0]=[str(ROOT/'backend/tests'),str(ROOT/'backend'),str(ROOT)]
import benchmark_dashboard_summary_memory as bench
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorCursor
from pymongo import monitoring
TARGET='bd8e1a4c377e61544b5cfe0f71f573c71abca89f'
TENANT=ContextVar('forensic_tenant',default='control')
STACK=ContextVar('forensic_stack',default=())
STAGES=defaultdict(lambda:defaultdict(lambda:dict(calls=0,wall_s=0.,active_wall_s=0.,cpu_s=0.,exclusive_cpu_s=0.,exclusive_wall_s=0.,suspended_s=0.)))
METRICS=defaultdict(lambda:defaultdict(float))
STORES=defaultdict(set)

def measured(fn,label):
    @types.coroutine
    def drive(awaitable,rec):
        iterator=awaitable.__await__(); value=None; error=None
        while True:
            frame={'active':True,'child_cpu':0.,'child_wall':0.}
            parents=STACK.get(); token=STACK.set(parents+(frame,))
            c=time.thread_time();w=time.perf_counter()
            try:
                if error is None: yielded=iterator.send(value)
                else: yielded=iterator.throw(error);error=None
            except StopIteration as stop:return stop.value
            finally:
                cpu=time.thread_time()-c; wall=time.perf_counter()-w
                rec['cpu_s']+=cpu;rec['active_wall_s']+=wall
                rec['exclusive_cpu_s']+=cpu-frame['child_cpu'];rec['exclusive_wall_s']+=wall-frame['child_wall']
                frame['active']=False;STACK.reset(token)
                if parents and parents[-1]['active']:
                    parents[-1]['child_cpu']+=cpu;parents[-1]['child_wall']+=wall
            pause=time.perf_counter()
            try:value=yield yielded
            except BaseException as exc:error=exc
            finally:rec['suspended_s']+=time.perf_counter()-pause
    if inspect.iscoroutinefunction(fn):
        @functools.wraps(fn)
        async def asynchronous(*args,**kwargs):
            rec=STAGES[TENANT.get()][label];rec['calls']+=1;started=time.perf_counter()
            try:return await drive(fn(*args,**kwargs),rec)
            finally:rec['wall_s']+=time.perf_counter()-started
        return asynchronous
    @functools.wraps(fn)
    def synchronous(*args,**kwargs):
        rec=STAGES[TENANT.get()][label];rec['calls']+=1
        frame={'active':True,'child_cpu':0.,'child_wall':0.};parents=STACK.get();token=STACK.set(parents+(frame,))
        c=time.thread_time();w=time.perf_counter()
        try:return fn(*args,**kwargs)
        finally:
            cpu=time.thread_time()-c;wall=time.perf_counter()-w
            rec['wall_s']+=wall;rec['active_wall_s']+=wall;rec['cpu_s']+=cpu
            rec['exclusive_cpu_s']+=cpu-frame['child_cpu'];rec['exclusive_wall_s']+=wall-frame['child_wall']
            frame['active']=False;STACK.reset(token)
            if parents and parents[-1]['active']:
                parents[-1]['child_cpu']+=cpu;parents[-1]['child_wall']+=wall
    return synchronous

def wrap(obj,name,label):setattr(obj,name,measured(getattr(obj,name),label))

class MongoTrace(monitoring.CommandListener,monitoring.ConnectionPoolListener):
    def started(self,event):pass
    def succeeded(self,event):
        who=TENANT.get();m=METRICS[who]
        if event.command_name in ('find','getMore','aggregate'):
            cursor=event.reply.get('cursor',{});n=len(cursor.get('firstBatch',cursor.get('nextBatch',[])))
            m['mongo_commands']+=1;m['mongo_docs']+=n;m['mongo_roundtrip_s']+=event.duration_micros/1e6
            m[event.command_name+'_commands']+=1;m[event.command_name+'_roundtrip_s']+=event.duration_micros/1e6
    def failed(self,event):METRICS[TENANT.get()]['mongo_failures']+=1
    def connection_checked_out(self,event):
        m=METRICS[TENANT.get()];m['pool_checkouts']+=1;m['pool_checkout_s']+=event.duration;m['pool_checkout_max_s']=max(m['pool_checkout_max_s'],event.duration)
    def connection_check_out_failed(self,event):METRICS[TENANT.get()]['pool_failures']+=1
for name in ('pool_created','pool_ready','pool_cleared','pool_closed','connection_created','connection_ready','connection_closed','connection_check_out_started','connection_checked_in'):
    setattr(MongoTrace,name,lambda self,event:None)

def install_low_overhead():
    # Motor propagates contextvars into its shared thread pool. Measure queue
    # wait separately from the worker's CPU and event-loop completion delay.
    import motor.frameworks.asyncio as framework
    original=framework.run_on_executor
    def executor(loop,fn,*args,**kwargs):
        who=TENANT.get();queued=time.perf_counter();done=[None]
        def work(*args,**kwargs):
            start=time.perf_counter();cpu=time.thread_time();m=METRICS[who]
            m['executor_jobs']+=1;m['executor_queue_s']+=start-queued;m['executor_queue_max_s']=max(m['executor_queue_max_s'],start-queued)
            try:return fn(*args,**kwargs)
            finally:
                m['executor_cpu_s']+=time.thread_time()-cpu;m['executor_work_s']+=time.perf_counter()-start;done[0]=time.perf_counter()
        future=original(loop,work,*args,**kwargs)
        def completed(_):
            if done[0] is not None:
                delay=time.perf_counter()-done[0];m=METRICS[who];m['completion_dispatch_s']+=delay;m['completion_dispatch_max_s']=max(m['completion_dispatch_max_s'],delay)
        future.add_done_callback(completed)
        return future
    framework.run_on_executor=executor
    handle=asyncio.Handle._run
    def run(self):
        who=self._context.get(TENANT,'control');c=time.thread_time();w=time.perf_counter()
        try:return handle(self)
        finally:
            m=METRICS[who];elapsed=time.perf_counter()-w
            m['loop_cpu_s']+=time.thread_time()-c;m['loop_active_wall_s']+=elapsed;m['loop_callbacks']+=1;m['loop_longest_callback_s']=max(m['loop_longest_callback_s'],elapsed)
    asyncio.Handle._run=run
    import dashboard_spill as spill
    original_init=spill.DashboardSpill.__init__
    def init(self,*args,**kwargs):
        original_init(self,*args,**kwargs);STORES[TENANT.get()].add(str(self.directory))
    spill.DashboardSpill.__init__=init
    import dashboard_read_coordinator as coordinator
    original_run=coordinator.DashboardReadCoordinator.run
    async def coordinate(self,key,factory):
        m=METRICS[TENANT.get()];m['coordinator_enter_s']=time.perf_counter()
        async def factory_trace():
            m['factory_start_s']=time.perf_counter();return await factory()
        return await original_run(self,key,factory_trace)
    coordinator.DashboardReadCoordinator.run=coordinate

def install_stages():
    import dashboard_spill as spill, dashboard_order_reads as reads, dashboard_order_accumulator as acc
    import dashboard_product_pages as products, dashboard_operating_reads as operating, dashboard_recurring_reads as recurring
    for name in ('execute','executemany'):wrap(spill.DashboardSpill,name,'temporary_sql.'+name)
    for name in ('_store_value','_decode'):wrap(spill,name,'serialization.'+name)
    for name in ('observe','observe_fee_batch','finish'):wrap(acc.DashboardOrderAccumulator,name,'financial_labels.'+name)
    wrap(acc,'summarize_dashboard_metadata','python.metadata_accumulation')
    wrap(reads,'_load_bounded','snapshot.hydration')
    wrap(products,'load_product_context','product.catalog')
    wrap(products,'finalize_product_pages','product.finalize')
    wrap(operating,'load_dashboard_operating_inputs','financial.operating_reader')
    wrap(recurring,'load_dashboard_recurring_inputs','financial.recurring_reader')
    wrap(AsyncIOMotorCursor,'to_list','mongo.batch_fetch')
    orig_extract=bench.extract_dashboard
    def extract(*args,**kwargs):
        fn=orig_extract(*args,**kwargs);ns=fn.__globals__
        for name in ('compute_balances','compute_operating_expenses_for_range','aggregate_settlements_by_provider','summarize_orders_sar'):
            if name in ns:ns[name]=measured(ns[name],'financial.'+name)
        return measured(fn,'summary.legacy')
    bench.extract_dashboard=extract
    orig_endpoint=bench.v2_endpoint
    def endpoint(*args,**kwargs):
        fn=orig_endpoint(*args,**kwargs);ns=inspect.unwrap(fn).__globals__
        for name in ('build_mezan_v2_product_cost','build_mezan_v2_ads','_dashboard_recurring_totals','build_salla_ads_executive_breakdown','summarize_orders_sar'):
            if name in ns:ns[name]=measured(ns[name],'summary.'+name)
        return fn
    bench.v2_endpoint=endpoint

def os_counters():
    result={}
    if sys.platform=='linux':
        import resource
        r=resource.getrusage(resource.RUSAGE_SELF)
        result.update(user_cpu_s=r.ru_utime,system_cpu_s=r.ru_stime,voluntary_switches=r.ru_nvcsw,involuntary_switches=r.ru_nivcsw)
        for prefix,path in [('process_io','/proc/self/io'),('cgroup_cpu','/sys/fs/cgroup/cpu.stat')]:
            try:
                for line in Path(path).read_text().splitlines():
                    key,value=line.replace(':','').split();result[prefix+'.'+key]=int(value)
            except (OSError,ValueError):pass
        pid=os.environ.get('FORENSIC_MONGO_PID')
        if pid:
            try:
                stat=Path('/proc/'+pid+'/stat').read_text().split()
                result['mongod_cpu_s']=(int(stat[13])+int(stat[14]))/os.sysconf('SC_CLK_TCK')
            except (OSError,ValueError):pass
    return result

async def worker(database,tenants,profile):
    assert database.startswith(bench.DB_PREFIX)
    install_low_overhead()
    if profile:install_stages()
    client=AsyncIOMotorClient(bench.URI,event_listeners=[MongoTrace()],serverSelectionTimeoutMS=3000)
    await client.admin.command('ping');endpoint=bench.v2_endpoint(client[database],'after')
    probe=bench.SpillProbe();probe.install();os_before=os_counters();started=time.perf_counter();cpu=time.process_time()
    lag={'count':0,'total_s':0.,'max_s':0.};stop=asyncio.Event()
    async def heartbeat():
        while not stop.is_set():
            due=time.perf_counter()+0.02;await asyncio.sleep(.02);v=max(0,time.perf_counter()-due)
            lag['count']+=1;lag['total_s']+=v;lag['max_s']=max(lag['max_s'],v)
    monitor=asyncio.create_task(heartbeat())
    async def request(i):
        who=f'tenant-{i}';token=TENANT.set(who);begin=time.perf_counter()
        try:
            response=await endpoint(user={'id':who,'role':'owner'},from_date='2026-09-01',to_date='2026-09-30',payment_methods=None,shipping_companies=None)
            encode=time.perf_counter();safe=bench.jsonable_encoder(response);wire=json.dumps(safe,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode();encoding=time.perf_counter()-encode
            financial={k:safe.get(k) for k in ('totals','monthly','payment_breakdown','shipping_breakdown','source_breakdown','month_kpis','currency_conversion','net_sales_config')}
            return dict(tenant=who,wall_s=time.perf_counter()-begin,response_serialization_s=encoding,response_bytes=len(wire),financial_signature=bench.signature(bench.financial_values(financial)))
        finally:TENANT.reset(token)
    try:
        results=await asyncio.gather(*(request(i) for i in range(tenants)))
        wall=time.perf_counter()-started;process_cpu=time.process_time()-cpu;current,peak=bench.rss();os_after=os_counters()
        stop.set();await monitor
        owners={who:len(paths) for who,paths in STORES.items()};all_paths=[x for paths in STORES.values() for x in paths]
        return dict(os_delta={k:os_after[k]-v for k,v in os_before.items() if k in os_after},target_sha=TARGET,tenants=tenants,profile=profile,wall_s=wall,process_cpu_s=process_cpu,rss_peak_bytes=peak,requests=results,metrics=dict(METRICS),stages={k:dict(v) for k,v in STAGES.items()},loop_lag=lag,private_stores_per_tenant=owners,shared_store_paths=len(all_paths)!=len(set(all_paths)),pool_max=client.options.pool_options.max_pool_size,logical_cpus=os.cpu_count(),cpu_affinity=list(os.sched_getaffinity(0)) if hasattr(os,'sched_getaffinity') else None,**probe.finish())
    finally:client.close()

def main():
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('mode',choices=['suite','worker']);parser.add_argument('--database');parser.add_argument('--tenants',type=int,default=1);parser.add_argument('--count',type=int,default=100000);parser.add_argument('--profile',action='store_true');parser.add_argument('--out',default='forensic-results');args=parser.parse_args()
    actual=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip();assert actual==TARGET,(actual,TARGET)
    if args.mode=='worker':print(json.dumps(asyncio.run(worker(args.database,args.tenants,args.profile)),ensure_ascii=False));return
    out=Path(args.out);out.mkdir(exist_ok=True);database=bench.DB_PREFIX+'forensic_'+uuid.uuid4().hex[:16]
    try:
        asyncio.run(bench.seed(database,args.count,1000,1024,4))
        # Same runner and fixed dataset. Fresh process per sample; no tests
        # or other benchmarks compete. Controls quantify stage-probe overhead.
        for profile,tenants in [(False,1),(False,2),(False,3),(False,4),(True,1),(True,2),(True,3),(True,4)]:
            command=[sys.executable,__file__,'worker','--database',database,'--tenants',str(tenants)]
            if profile:command+=['--profile']
            r=subprocess.run(command,cwd=ROOT,capture_output=True,text=True,encoding='utf-8',check=True)
            result=json.loads(r.stdout);result['orders_per_tenant']=args.count
            path=out/f'{"profile" if profile else "control"}-{tenants}.json';path.write_text(json.dumps(result,indent=2),encoding='utf-8')
            print(json.dumps({'sample':path.name,'wall':result['wall_s'],'cpu':result['process_cpu_s'],'peak_mib':result['rss_peak_bytes']/2**20}),flush=True)
        signatures={}
        for sample in out.glob('*.json'):
            data=json.loads(sample.read_text())
            assert data['spill_cleanup_complete'] and not data['shared_store_paths']
            for request in data['requests']:
                key=request['tenant'];value=request['financial_signature']
                assert signatures.setdefault(key,value)==value,(sample,key)
        (out/'PARITY.txt').write_text('All tenant signatures match across controls/profiles/concurrency; private stores cleaned.\n')
    finally:asyncio.run(bench.cleanup(database))
if __name__=='__main__':main()

