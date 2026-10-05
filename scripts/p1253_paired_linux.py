"""Diagnostic only. Fixed source worktrees; loopback synthetic Mongo only."""
import argparse, asyncio, collections, hashlib, importlib, json, os, pathlib, platform, subprocess, sys, time
BASE = "bd8e1a4c377e61544b5cfe0f71f573c71abca89f"
HEAD = "15f00ff0d072cf8e00b8d642a6390d2ef71c11ac"

def git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()

def load(harness, target):
    sys.path[:0] = [str(harness / "backend/tests"), str(target / "backend"), str(target)]
    import benchmark_dashboard_summary_memory as bench
    import dashboard_summary_fixture as fixture
    # The harness and generator are frozen, while ALL application imports use target.
    bench.ROOT = fixture.REPOSITORY = target
    sys.path[:0] = [str(target / "backend"), str(target)]
    return bench

async def fingerprint(bench, database):
    from bson import json_util
    client = bench.AsyncIOMotorClient(bench.URI)
    try:
        db = client[database]; digest = hashlib.sha256(); counts = {}; indexes = {}
        for name in sorted(await db.list_collection_names()):
            digest.update(name.encode()); count = 0
            indexes[name] = await db[name].index_information()
            async for doc in db[name].find({}).sort("_id", 1).batch_size(128):
                digest.update(json_util.dumps(doc, sort_keys=True).encode()); count += 1
            counts[name] = count
        return dict(sha256=digest.hexdigest(), counts=counts, indexes=indexes)
    finally: client.close()

async def measure(bench, args):
    reads_instances = []
    class Reads(bench.Reads):
        def __init__(self):
            super().__init__(); self.batches = collections.Counter(); self.kinds = collections.Counter(); reads_instances.append(self)
        def succeeded(self, event):
            super().succeeded(event)
            if event.command_name in {"find", "getMore", "aggregate"}:
                cursor = event.reply.get("cursor", {})
                self.batches[len(cursor.get("firstBatch", cursor.get("nextBatch", [])))] += 1
                self.kinds[event.command_name] += 1
    bench.Reads = Reads
    before = await fingerprint(bench, args.database) # identical warm Mongo scan, outside timer
    attribution = None
    loop = asyncio.get_running_loop(); factory = loop.get_task_factory()
    if args.profile:
        import profile_dashboard_tenants as profile
        attribution = profile.Attribution(); attribution.install(); loop.set_task_factory(attribution.factory)
    wall, cpu, thread = time.perf_counter(), time.process_time(), time.thread_time()
    try:
        result = await bench.worker("after", args.database, args.concurrency, args.tenants)
        result.update(process_cpu_seconds=time.process_time()-cpu, event_loop_cpu_seconds=time.thread_time()-thread,
                      measured_elapsed_seconds=time.perf_counter()-wall, profile=args.profile,
                      mongo_batches=dict(reads_instances[0].batches), mongo_commands_by_kind=dict(reads_instances[0].kinds),
                      request_order=["owner" if args.tenants == 1 else "tenant-"+str(i % args.tenants) for i in range(args.concurrency)],
                      cache_hit_miss=None)
        if attribution:
            result["exclusive_sync_categories"] = dict(attribution.sync)
            result["inclusive_stages_DO_NOT_SUM"] = dict(attribution.stages)
            result["event_loop_disjoint"] = attribution.snapshot()
            result["suspension_inclusive_DO_NOT_SUM"] = dict(attribution.waits)
    finally:
        loop.set_task_factory(factory)
        if attribution: attribution.restore()
    after = await fingerprint(bench, args.database)
    assert before == after, "Dataset/index mutation during measured read"
    result["dataset"] = before
    result["event_loop_cpu_fraction"] = result["event_loop_cpu_seconds"] / result["measured_elapsed_seconds"]
    result["source"] = dict(head=git(args.target,"rev-parse","HEAD"), tree=git(args.target,"rev-parse","HEAD^{tree}"), dirty=git(args.target,"status","--porcelain","--untracked-files=no"))
    assert result["source"]["head"] in (BASE, HEAD) and not result["source"]["dirty"]
    assert all(r["ok"] for r in result["requests"]) and not result["mongo_failed_commands"]
    assert result["spill_cleanup_complete"]
    return result

def main():
    parser = argparse.ArgumentParser(); parser.add_argument("mode", choices=["suite","worker","seed","cleanup"])
    parser.add_argument("--harness",type=pathlib.Path,required=True); parser.add_argument("--target",type=pathlib.Path)
    parser.add_argument("--baseline",type=pathlib.Path); parser.add_argument("--current",type=pathlib.Path)
    parser.add_argument("--output",type=pathlib.Path); parser.add_argument("--database"); parser.add_argument("--count",type=int,default=100000)
    parser.add_argument("--tenants",type=int,default=1); parser.add_argument("--concurrency",type=int,default=1); parser.add_argument("--profile",action="store_true")
    args=parser.parse_args()
    if args.mode != "suite":
        bench=load(args.harness,args.target)
        assert args.database.startswith(bench.DB_PREFIX)
        if args.mode == "seed": result=asyncio.run(bench.seed(args.database,args.count,min(args.count,1000),1024,args.tenants))
        elif args.mode == "cleanup": result=asyncio.run(bench.cleanup(args.database))
        else: result=asyncio.run(measure(bench,args))
        print(json.dumps(result,default=str)); return
    args.output.mkdir(parents=True,exist_ok=True)
    env={**os.environ,"PYTHONDONTWRITEBYTECODE":"1"}
    def child(mode,target,database,*extra):
        cmd=[sys.executable,__file__,mode,"--harness",str(args.harness),"--target",str(target),"--database",database,*map(str,extra)]
        done=subprocess.run(cmd,text=True,capture_output=True,env=env,timeout=1200)
        if done.returncode: raise RuntimeError(done.stderr[-8000:])
        return json.loads(done.stdout)
    report=dict(environment=dict(python=sys.version,platform=platform.platform(),cpu_count=os.cpu_count()),samples=[],
                procedure="Same job, fixed runtime and loopback Mongo8.0.12; shared seeded DB per workload; bounded pre-scan warms Mongo equally; fresh Python process each sample; baseline/current/baseline controls; separate profiled companions; no overlap.")
    try:
        for count,tenants,concurrencies in [(10000,1,[1]),(50000,1,[1]),(100000,1,[1,3,4]),(100000,3,[3,4])]:
            database="dashboard_summary_benchmark_p1253_"+str(count)+"_"+str(tenants)
            child("seed",args.current,database,"--count",count,"--tenants",tenants)
            try:
                for concurrency in concurrencies:
                    workload=dict(count=count,tenants=tenants,concurrency=concurrency,kind="same-key" if tenants==1 else "independent-plus-repeated-tenant" if concurrency>tenants else "independent")
                    pair=dict(workload=workload,runs={}); report["samples"].append(pair)
                    for name,target,profile in [("baseline",args.baseline,False),("current",args.current,False),("baseline_repeat",args.baseline,False),("baseline_profile",args.baseline,True),("current_profile",args.current,True)]:
                        result=child("worker",target,database,"--tenants",tenants,"--concurrency",concurrency,*( ["--profile"] if profile else []))
                        pair["runs"][name]=result
                        (args.output/(str(count)+"-"+str(tenants)+"-"+str(concurrency)+"-"+name+".json")).write_text(json.dumps(result,indent=2))
                        print(workload,name,result["wall_seconds"],result["rss_peak_bytes"],flush=True)
                    reference=pair["runs"]["baseline"]
                    def sig(r,key):return [(x["tenant"],x[key]) for x in r["requests"]]
                    pair["financial_parity"]=all(sig(r,"financial_signature")==sig(reference,"financial_signature") for r in pair["runs"].values())
                    pair["full_parity"]=all(sig(r,"full_signature")==sig(reference,"full_signature") for r in pair["runs"].values())
                    assert all(r["dataset"]==reference["dataset"] for r in pair["runs"].values())
                    assert pair["financial_parity"],"Financial mismatch"
                    (args.output/"report.json").write_text(json.dumps(report,indent=2))
            finally: child("cleanup",args.current,database)
    finally: (args.output/"report.json").write_text(json.dumps(report,indent=2))
if __name__ == "__main__": main()
