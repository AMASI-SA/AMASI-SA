"""Reproducible recorder microbenchmark, never connects to any service.

CPU extrapolation is a synthetic capacity estimate, NOT an end-to-end worker
overhead PASS. RSS and allocator peaks cannot prove Linux/container overhead.
"""
import argparse
import gc
import json
import os
import platform
from pathlib import Path
import statistics
import subprocess
import sys
import time
import tracemalloc

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from observability_metrics import Metrics, NAMES


def rss_bytes():
    try:
        import psutil
        return psutil.Process().memory_info().rss
    except ImportError:
        return None


def batch(recorder, count):
    for index in range(count):
        token = recorder.begin_request()
        recorder.observe("api.duration.other", .012)
        recorder.increment("api.status.2xx")
        recorder.observe("mongo.command.find.ok", .003)
        recorder.observe("mongo.pool.wait.ok", .0001)
        recorder.observe("mongo.command.commitTransaction.ok", .002)
        recorder.observe("governor.wait.global.dashboard", .0001)
        recorder.observe("governor.hold.dashboard", .006)
        recorder.end_request(token)
        if index % 100 == 0:
            recorder.observe("event_loop.lag", .001)
        if index % 3000 == 0:
            json.dumps(recorder.snapshot())


def child(mode, count):
    enabled = mode == "enabled"
    recorder = Metrics(enabled)
    batch(recorder, 1000)
    started = time.process_time()
    batch(recorder, count)
    cpu = time.process_time() - started
    # Separate memory phase: tracing overhead must not contaminate CPU sample.
    gc.collect()
    before_rss = rss_bytes()
    tracemalloc.start()
    recorder = Metrics(enabled)
    for name in NAMES:
        recorder.observe(name, .05)
    tokens = [recorder.begin_request() for _ in range(4096)]
    batch(recorder, count)
    payload = json.dumps(recorder.snapshot())
    retained, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    after_rss = rss_bytes()
    print(json.dumps(dict(mode=mode, iterations=count, cpu_seconds=cpu,
        retained_bytes=retained, allocator_peak_bytes=peak,
        rss_before=before_rss, rss_after=after_rss,
        snapshot_bytes=len(payload), active=recorder.snapshot()["api"]["active"])))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=100000)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--requests-per-second", type=float, default=100)
    parser.add_argument("--allocated-cores", type=float, default=2)
    parser.add_argument("--child", choices=("enabled", "disabled"))
    args = parser.parse_args()
    if args.child:
        child(args.child, args.iterations)
        return
    if min(args.iterations, args.repeats, args.requests_per_second, args.allocated_cores) <= 0:
        parser.error("all numeric settings must be positive")
    samples = []
    for repeat in range(args.repeats):
        # Alternate ordering to reduce drift bias; fresh process each sample.
        for mode in (("disabled", "enabled") if repeat % 2 == 0 else ("enabled", "disabled")):
            output = subprocess.check_output([sys.executable, __file__, "--child", mode,
                "--iterations", str(args.iterations)], text=True)
            samples.append(json.loads(output))
    cpu = {m: statistics.median(s["cpu_seconds"] for s in samples if s["mode"] == m)
           for m in ("disabled", "enabled")}
    delta = max(0., cpu["enabled"] - cpu["disabled"])
    projected = delta / args.iterations * args.requests_per_second / args.allocated_cores * 100
    print(json.dumps(dict(platform=platform.platform(), python=sys.version,
        logical_cpus=os.cpu_count(), iterations=args.iterations, repeats=args.repeats,
        modeled_requests_per_second=args.requests_per_second,
        modeled_allocated_cores=args.allocated_cores, median_cpu_seconds=cpu,
        projected_incremental_cpu_percent=projected, samples=samples,
        acceptance="LIMITATION",
        limitations=["Recorder callbacks plus snapshot serialization only; no ASGI, real Mongo listener, governor, RSS reader or collector transport costs.",
            "CPU percentage extrapolates measured per-operation CPU to the stated synthetic rate and core allocation; it is not measured production CPU.",
            "Python allocation/RSS samples are not an end-to-end Linux per-worker memory comparison.",
            "No claim that the total monitoring meets <=1% CPU / <=8MiB RAM; Linux end-to-end A/B remains required."]), indent=2))


if __name__ == "__main__":
    main()
