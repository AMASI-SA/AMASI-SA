"""Paced Linux worker A/B benchmark. No network or database writes.

Measures real ASGI middleware, Mongo listener callbacks, Governor admission,
lag task and diagnostics serialization. Mongo events are synthetic: this does
not benchmark the driver/network/database or a complete production workload.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
LEGACY_BASE = "cd554446e3dbdf618521f3e122f9b58b8e4e9035"


def rss_bytes():
    """Linux /proc reports KiB; return bytes, never ambiguous MB units."""
    for line in Path("/proc/self/status").read_text().splitlines():
        if line.startswith("VmRSS:"):
            return int(line.split()[1]) * 1024
    raise RuntimeError("VmRSS unavailable")


async def child(args):
    import resource

    os.environ["OBS_METRICS_ENABLED"] = str(args.child == "enabled").lower()
    os.environ.pop("OBS_CONTROL_FILE", None)
    sys.path.insert(0, str(ROOT / "backend"))
    legacy_dir = None
    legacy_hashes = {}
    if args.child == "legacy":
        legacy_dir = tempfile.TemporaryDirectory(prefix="observability-legacy-")
        for name in ("resource_governor.py", "mongo_observability.py", "runtime_diagnostics.py"):
            source = subprocess.check_output(["git", "show", f"{args.legacy_base}:backend/{name}"], cwd=ROOT)
            (Path(legacy_dir.name) / name).write_bytes(source)
            legacy_hashes[name] = hashlib.sha256(source).hexdigest()
        sys.path.insert(0, legacy_dir.name)
    from observability_metrics import metrics
    from observability_middleware import DiagnosticsMiddleware
    from mongo_observability import mongo_metrics
    from resource_governor import governor
    from runtime_diagnostics import diagnostics, start_lag_monitor

    received = 0
    snapshots = 0
    snapshot_bytes = 0

    async def app(scope, receive, send):
        nonlocal received
        # The same legacy listener/governor costs execute in BOTH modes.
        mongo_metrics.connection_check_out_started(None)
        mongo_metrics.connection_checked_out(None)
        for name in ("find", "commitTransaction"):
            mongo_metrics.started(SimpleNamespace(command_name=name, command={name: "synthetic"}))
            mongo_metrics.succeeded(SimpleNamespace(command_name=name, duration_micros=2000))
        mongo_metrics.connection_checked_in(None)
        token, _ = await governor.acquire("dashboard", task_name="synthetic-benchmark")
        try:
            await asyncio.sleep(.002)
        finally:
            await governor.release(token)
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"{}"})
        received += 1

    wrapped = app if args.child == "legacy" else DiagnosticsMiddleware(app)

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        pass

    routes = ("/api/preparation-work-v1/assembly/pieces/{piece_id}/ready",
              "/supplier-invoices", "/shipping", "/synthetic")

    async def phase(seconds, sample_memory):
        nonlocal snapshots, snapshot_bytes
        start = time.monotonic()
        next_snapshot = start
        next_rss = start
        rss = []
        max_lateness = 0.
        count = int(seconds * args.requests_per_second)
        for index in range(count):
            deadline = start + index / args.requests_per_second
            await asyncio.sleep(max(0., deadline - time.monotonic()))
            max_lateness = max(max_lateness, time.monotonic() - deadline)
            scope = {"type": "http", "method": "POST",
                     "route": SimpleNamespace(path=routes[index % len(routes)])}
            await wrapped(scope, receive, send)
            now = time.monotonic()
            if now >= next_snapshot:
                # Same existing diagnostics collector cadence for A and B;
                # addition being measured is phase1 aggregation/serialization.
                payload = json.dumps(diagnostics(), separators=(",", ":"))
                snapshot_bytes = max(snapshot_bytes, len(payload.encode()))
                snapshots += 1
                next_snapshot = now + args.snapshot_interval
            if sample_memory and now >= next_rss:
                rss.append(rss_bytes())
                next_rss = now + .25
        await asyncio.sleep(max(0., start + seconds - time.monotonic()))
        return rss, max_lateness

    lag = start_lag_monitor()  # Existing monitor runs in both modes.
    await phase(args.warmup, False)
    received = snapshots = snapshot_bytes = 0
    before_rss = rss_bytes()
    wall_start, cpu_start = time.monotonic(), time.process_time()
    usage_start = resource.getrusage(resource.RUSAGE_SELF)
    rss, lateness = await phase(args.duration, True)
    cpu = time.process_time() - cpu_start
    wall = time.monotonic() - wall_start
    usage_end = resource.getrusage(resource.RUSAGE_SELF)
    lag.cancel()
    try:
        await lag
    except asyncio.CancelledError:
        pass
    snapshot = metrics.snapshot()
    if legacy_dir is not None:
        legacy_dir.cleanup()
    return dict(mode=args.child, pid=os.getpid(), legacy_source_hashes=legacy_hashes,
                cpu_seconds=cpu, wall_seconds=wall,
                user_seconds=usage_end.ru_utime - usage_start.ru_utime,
                system_seconds=usage_end.ru_stime - usage_start.ru_stime,
                cpu_allocated_percent=cpu / wall / args.allocated_cores * 100,
                rss_before_bytes=before_rss, rss_end_bytes=rss_bytes(),
                rss_steady_median_bytes=statistics.median(rss),
                rss_sampled_peak_bytes=max(rss),
                rss_process_highwater_bytes=usage_end.ru_maxrss * 1024,
                rss_samples=len(rss), requests=received, snapshots=snapshots,
                snapshot_max_bytes=snapshot_bytes, max_pacing_lateness_seconds=lateness,
                recorded_histograms=sorted(snapshot["histograms"]),
                active_at_end=snapshot["api"]["active"])


def summarize(samples, allocated_cores, baseline="disabled", measured="enabled"):
    pairs = []
    for repeat in sorted({s["repeat"] for s in samples}):
        pair = {s["mode"]: s for s in samples if s["repeat"] == repeat}
        off, on = pair[baseline], pair[measured]
        pairs.append(dict(repeat=repeat,
            incremental_cpu_allocated_percent=(on["cpu_seconds"] / on["wall_seconds"]
                - off["cpu_seconds"] / off["wall_seconds"]) / allocated_cores * 100,
            incremental_rss_steady_bytes=on["rss_steady_median_bytes"] - off["rss_steady_median_bytes"],
            incremental_rss_highwater_bytes=on["rss_process_highwater_bytes"] - off["rss_process_highwater_bytes"],
            incremental_rss_sampled_peak_bytes=on["rss_sampled_peak_bytes"] - off["rss_sampled_peak_bytes"]))
    cpu = max(p["incremental_cpu_allocated_percent"] for p in pairs)
    memory = max(max(p[k] for k in ("incremental_rss_steady_bytes",
        "incremental_rss_highwater_bytes", "incremental_rss_sampled_peak_bytes")) for p in pairs)
    return dict(baseline_mode=baseline, measured_mode=measured,
                pairs=pairs, worst_pair_cpu_allocated_percent=cpu,
                worst_pair_rss_bytes=memory,
                measured_budget_result="PASS_BOUNDED_WORKLOAD" if cpu <= 1 and memory <= 8 * 1024**2 else "FAIL",
                production_overhead_acceptance="LIMITATION")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--duration", type=float, default=10.)
    parser.add_argument("--warmup", type=float, default=1.)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--requests-per-second", type=float, default=100.)
    parser.add_argument("--allocated-cores", type=float, default=1.)
    parser.add_argument("--snapshot-interval", type=float, default=30.)
    parser.add_argument("--legacy-base", default=LEGACY_BASE)
    parser.add_argument("--child", choices=("enabled", "disabled", "legacy"))
    args = parser.parse_args()
    if platform.system() != "Linux":
        parser.error("Linux required for /proc RSS and resource accounting; no emulated PASS")
    if min(args.duration, args.warmup, args.repeats, args.requests_per_second,
           args.allocated_cores, args.snapshot_interval) <= 0 or args.duration * args.requests_per_second < 1:
        parser.error("positive settings and at least one measured request required")
    if args.child:
        print(json.dumps(asyncio.run(child(args))))
        return
    samples = []
    for repeat in range(args.repeats):
        for mode in (("legacy", "disabled", "enabled") if repeat % 2 == 0 else ("enabled", "disabled", "legacy")):
            cmd = [sys.executable, __file__, "--child", mode, "--legacy-base", args.legacy_base]
            for name in ("duration", "warmup", "requests_per_second", "allocated_cores", "snapshot_interval"):
                cmd += ["--" + name.replace("_", "-"), str(getattr(args, name))]
            sample = json.loads(subprocess.check_output(cmd, text=True, timeout=args.duration + args.warmup + 60))
            sample["repeat"] = repeat
            samples.append(sample)
    summary = summarize(samples, args.allocated_cores)
    disabled_summary = summarize(samples, args.allocated_cores, "legacy", "disabled")
    required = {"event_loop.lag", "api.duration.ready", "api.duration.supplier_invoice",
                "api.duration.shipping", "api.duration.other", "mongo.command.find.ok",
                "mongo.command.commitTransaction.ok", "mongo.pool.wait.ok",
                "governor.wait.kind.dashboard", "governor.wait.global.dashboard",
                "governor.hold.dashboard"}
    validity = dict(full_measurement=args.duration >= 10 and args.warmup >= 1 and args.repeats >= 5,
        pacing_maintained=all(s["max_pacing_lateness_seconds"] < .1 for s in samples),
        requests_completed=all(s["requests"] == int(args.duration * args.requests_per_second)
                               and s["active_at_end"] == 0 for s in samples),
        disabled_empty=all(not s["recorded_histograms"] for s in samples if s["mode"] == "disabled"),
        legacy_measured=all(len(s["legacy_source_hashes"]) == 3 and not s["recorded_histograms"]
                            for s in samples if s["mode"] == "legacy"),
        enabled_coverage=all(required <= set(s["recorded_histograms"])
                             for s in samples if s["mode"] == "enabled"))
    if not all(validity.values()) and summary["measured_budget_result"] != "FAIL":
        summary["measured_budget_result"] = "LIMITATION_INCOMPLETE_OR_UNPACED"
    if not all(validity.values()) and disabled_summary["measured_budget_result"] != "FAIL":
        disabled_summary["measured_budget_result"] = "LIMITATION_INCOMPLETE_OR_UNPACED"
    print(json.dumps(dict(platform=platform.platform(), python=sys.version,
        affinity_cpus=len(os.sched_getaffinity(0)), allocated_cores_denominator=args.allocated_cores,
        cgroup_cpu_max=Path("/sys/fs/cgroup/cpu.max").read_text().strip() if Path("/sys/fs/cgroup/cpu.max").exists() else None,
        settings=vars(args), samples=samples, validity=validity,
        disabled_vs_legacy=disabled_summary, **summary,
        limitations=["Synthetic ASGI workload and Mongo callbacks, not real network/database or full business server.",
            "Linux CI is not Production; scheduler/allocator noise is retained in all paired results.",
            "Disabled baseline retains existing diagnostics, Mongo listeners, lag task and Governor costs.",
            "Legacy comparison loads three exact historical modules via git show; missing Git history fails the run, never substitutes current modules.",
            "Legacy harness imports the new disabled metrics/middleware definitions but does not wrap requests; this common harness is not a full historical server startup comparison.",
            "One worker per child; collector transport, nginx and multi-worker aggregate costs are not measured.",
            "RSS highwater includes imports/warmup; steady /proc RSS is sampled every 250ms. No tracemalloc during timing.",
            "Allocated cores is an explicit normalization budget, not inferred Production capacity."]), indent=2))


if __name__ == "__main__":
    main()
