"""Diagnostic only. Fixed source worktrees; loopback synthetic Mongo only."""
import argparse, asyncio, collections, hashlib, importlib, json, os, pathlib, platform, subprocess, sys, time
from p1253_diagnostic_contract import BASELINE, CANDIDATE, request_ids, execution_steps
BASE = "bd8e1a4c377e61544b5cfe0f71f573c71abca89f"
HEAD = "15f00ff0d072cf8e00b8d642a6390d2ef71c11ac"

def git(root, *args):
    return subprocess.check_output(["git", "-c", "safe.directory="+str(root), "-C", str(root), *args], text=True).strip()

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

def source_identity(target):
    result=dict(head=git(target,"rev-parse","HEAD"),tree=git(target,"rev-parse","HEAD^{tree}"),
                dirty=git(target,"status","--porcelain","--untracked-files=no"))
    if result["head"] not in (BASE,HEAD) or result["dirty"]:
        raise ValueError("Unexpected or dirty application source")
    return result


def rss_status():
    values={}
    for line in pathlib.Path("/proc/self/status").read_text().splitlines():
        if line.startswith(("VmRSS:","VmHWM:")):
            key, value=line.split(":",1); values[key]=int(value.split()[0])*1024
    return values


def reset_rss_window():
    # Linux kernel high-water mark reset: no lifetime getrusage peak in samples.
    pathlib.Path("/proc/self/clear_refs").write_text("5\n")
    values=rss_status()
    if "VmHWM" not in values: raise RuntimeError("Kernel RSS window unavailable")
    return values


async def measure(bench,args,row):
    if row["profile"]: raise ValueError("Profiling cannot enter latency samples")
    owners=request_ids(row["workload"])
    if any(owner not in ("tenant-0","tenant-1","tenant-2","tenant-3") for owner in owners):
        raise ValueError("Invalid tenant")
    class Reads(bench.Reads):
        def __init__(self): super().__init__(); self.batches=collections.Counter(); self.kinds=collections.Counter()
        def succeeded(self,event):
            super().succeeded(event)
            if event.command_name in {"find","getMore","aggregate"}:
                cursor=event.reply.get("cursor",{})
                self.batches[len(cursor.get("firstBatch",cursor.get("nextBatch",[])))]+=1
                self.kinds[event.command_name]+=1
        def reset(self): self.__init__()
    reads=Reads()
    client=bench.AsyncIOMotorClient(bench.URI,event_listeners=[reads],serverSelectionTimeoutMS=3000)
    endpoint=bench.v2_endpoint(client[args.database],"after")
    async def one(owner):
        start=time.perf_counter()
        response=await endpoint(user={"id":owner,"role":"owner"},from_date="2026-09-01",to_date="2026-09-30",
                                payment_methods=None,shipping_companies=None)
        endpoint_seconds=time.perf_counter()-start
        encode=time.perf_counter()
        safe=bench.jsonable_encoder(response)
        wire=json.dumps(safe,ensure_ascii=False,separators=(",",":"),allow_nan=False).encode()
        return dict(tenant=owner,ok=True,endpoint_seconds=endpoint_seconds,
                    json_seconds=time.perf_counter()-encode,response_bytes=len(wire),payload=safe)
    probe=None
    try:
        before_commands=reads.commands
        if before_commands: raise AssertionError("Unexpected data reads before warmup/measurement")
        warmup=[]
        warmup_started=time.perf_counter()
        for _ in range(row["warmup_count"]):
            warmup=await asyncio.gather(*(one(owner) for owner in owners))
        warmup_metrics=dict(count=row["warmup_count"],commands=reads.commands,documents=reads.documents,
                            wall_seconds=time.perf_counter()-warmup_started)
        del warmup
        reads.reset()
        probe=bench.SpillProbe(); probe.install()
        window_start=reset_rss_window()
        wall,cpu,thread=time.perf_counter(),time.process_time(),time.thread_time()
        results=await asyncio.gather(*(one(owner) for owner in owners))
        elapsed=time.perf_counter()-wall
        process_cpu=time.process_time()-cpu; thread_cpu=time.thread_time()-thread
        window_end=rss_status()  # Before signature encoding or dataset verification.
        spill=probe.finish();probe=None
        signature_started=time.perf_counter()
        for index,r in enumerate(results):
            safe=r.pop("payload")
            finances={key:safe.get(key) for key in ("totals","monthly","payment_breakdown","shipping_breakdown",
                      "source_breakdown","month_kpis","currency_conversion","net_sales_config")}
            r.update(request_index=index,full_signature=bench.signature(safe),
                     financial_signature=bench.signature(bench.financial_values(finances)))
        signature_seconds=time.perf_counter()-signature_started
        assert spill["spill_cleanup_complete"] and not reads.failures
        return dict(source=source_identity(args.target),sample=row,requests=results,
                    wall_seconds=elapsed,signature_seconds=signature_seconds,process_cpu_seconds=process_cpu,event_loop_cpu_seconds=thread_cpu,
                    event_loop_cpu_fraction=thread_cpu/elapsed,
                    rss_baseline_bytes=window_start["VmRSS"],rss_peak_bytes=window_end["VmHWM"],
                    rss_method="Linux VmHWM reset immediately before endpoint+JSON window; process RSS, not Mongo",
                    mongo_wire_documents=reads.documents,mongo_read_commands=reads.commands,
                    mongo_query_seconds=reads.query_us/1e6,mongo_batches=dict(reads.batches),
                    mongo_commands_by_kind=dict(reads.kinds),mongo_failed_commands=reads.failures,
                    before_measurement_data_commands=before_commands,warmup=warmup_metrics,
                    cache_hit_miss=None,coalescing_count=None,profile=False,**spill)
    finally:
        if probe:probe.finish()
        client.close()


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("mode",choices=["worker","seed","fingerprint"])
    parser.add_argument("--harness",type=pathlib.Path,required=True)
    parser.add_argument("--target",type=pathlib.Path,required=True)
    parser.add_argument("--database",required=True)
    parser.add_argument("--sample",type=pathlib.Path)
    parser.add_argument("--count",type=int,default=12)
    args=parser.parse_args()
    identity=source_identity(args.target)
    if source_identity(args.harness)["head"] != HEAD: raise ValueError("Harness must be frozen candidate")
    bench=load(args.harness,args.target)
    if not args.database.startswith(bench.DB_PREFIX): raise ValueError("Synthetic database required")
    if args.mode == "seed":
        # Unchanged frozen generator. Always seed the same four independent tenants;
        # workloads choose a subset, never rename/reseed A between scaling scenarios.
        result=asyncio.run(bench.seed(args.database,args.count,min(args.count,1000),1024,4))
    elif args.mode == "fingerprint": result=asyncio.run(fingerprint(bench,args.database))
    else:
        row=json.loads(args.sample.read_text())
        if row["source"] != identity["head"]: raise ValueError("Sample/source mismatch")
        if row["warmup_count"] != (0 if row["state"]=="cold" else 1): raise ValueError("Warmup mismatch")
        execution_steps(row["state"])
        result=asyncio.run(measure(bench,args,row))
    print(json.dumps(result,default=str))

if __name__ == "__main__": main()
