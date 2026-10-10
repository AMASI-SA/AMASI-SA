"""Isolated Linux real-loopback ASGI canary; never imports server or business code.

Five independent alternating triplets retain every response/error/timeout. A paced
load measures latency/CPU; a separate closed-loop load measures capacity. These
synthetic results are not a claim about production customer traffic.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import tempfile
import threading
import time

BASE = "c266b5a283230507d524ef2ec81370a2b5de8c1d"
MODULES = ("observability_metrics.py", "observability_middleware.py", "runtime_diagnostics.py", "resource_governor.py", "mongo_observability.py")


def percentile(values, p):
    values = sorted(values)
    return values[max(0, math.ceil(len(values) * p) - 1)] if values else None


async def serve(args):
    sys.path.insert(0, args.modules)
    from observability_metrics import metrics
    from observability_middleware import DiagnosticsMiddleware
    from runtime_diagnostics import start_lag_monitor
    monitor = start_lag_monitor()
    deadline = time.monotonic() + 5
    while args.enabled and not metrics.enabled and time.monotonic() < deadline:
        await asyncio.sleep(.02)
    if bool(metrics.enabled) != args.enabled:
        raise RuntimeError("Unexpected canary activation state")
    counts = {"requests": 0, "errors": []}

    async def synthetic(scope, receive, send):
        # Deterministic computation, no DB/provider connections or business state.
        value = sum(i * i for i in range(128))
        await asyncio.sleep(0)
        body = json.dumps({"synthetic": value}, separators=(",", ":")).encode()
        await send({"type": "http.response.start", "status": 200})
        await send({"type": "http.response.body", "body": body})

    app = DiagnosticsMiddleware(synthetic)

    async def connection(reader, writer):
        try:
            await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 3)
            messages = []
            async def receive():
                return {"type": "http.request", "body": b""}
            async def send(message):
                messages.append(message)
            await app({"type": "http", "method": "GET", "path": "/synthetic"}, receive, send)
            body = messages[-1]["body"]
            writer.write(b"HTTP/1.1 200 OK\r\nConnection: close\r\nContent-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body)
            await writer.drain()
            counts["requests"] += 1
        except Exception as exc:
            counts["errors"].append(type(exc).__name__)
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(connection, "127.0.0.1", 0)
    Path(args.ready).write_text(json.dumps({"port": server.sockets[0].getsockname()[1], "pid": os.getpid(), "enabled": metrics.enabled}))
    started = time.monotonic()
    cpu = time.process_time()
    await asyncio.to_thread(sys.stdin.readline)
    server.close()
    await server.wait_closed()
    monitor.cancel()
    await asyncio.gather(monitor, return_exceptions=True)
    print(json.dumps({**counts, "wall_seconds": time.monotonic() - started, "cpu_seconds": time.process_time() - cpu, "metrics": metrics.snapshot()}))


async def client(args):
    latencies, outcomes, errors = [], [], []
    start = time.monotonic()
    stop = start + args.seconds
    expected = json.dumps({"synthetic": sum(i * i for i in range(128))}, separators=(",", ":")).encode()

    async def one():
        before = time.monotonic()
        writer = None
        try:
            async def exchange():
                nonlocal writer
                reader, writer = await asyncio.open_connection("127.0.0.1", args.port)
                writer.write(b"GET /synthetic HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n")
                await writer.drain()
                return await reader.read()
            response = await asyncio.wait_for(exchange(), 2)
            head, body = response.split(b"\r\n\r\n", 1)
            if not head.startswith(b"HTTP/1.1 200") or body != expected:
                raise AssertionError("synthetic response mismatch")
            outcomes.append("ok")
        except Exception as exc:
            outcomes.append("error")
            errors.append({"type": type(exc).__name__, "at_seconds": time.monotonic() - start})
        finally:
            latencies.append((time.monotonic() - before) * 1000)
            if writer:
                writer.close()
                await writer.wait_closed()

    if args.rate:
        pending = set()
        index = 0
        while time.monotonic() < stop:
            await asyncio.sleep(max(0, start + index / args.rate - time.monotonic()))
            task = asyncio.create_task(one())
            pending.add(task)
            task.add_done_callback(pending.discard)
            index += 1
        await asyncio.gather(*pending)
    else:
        async def worker():
            while time.monotonic() < stop:
                await one()
        await asyncio.gather(*(worker() for _ in range(8)))
    elapsed = time.monotonic() - start
    print(json.dumps({"elapsed_seconds": elapsed, "requests": len(outcomes), "successes": outcomes.count("ok"), "errors": errors, "timeouts": sum(x["type"] == "TimeoutError" for x in errors), "throughput": outcomes.count("ok") / elapsed, "p50_ms": percentile(latencies, .50), "p95_ms": percentile(latencies, .95), "p99_ms": percentile(latencies, .99), "raw_latency_ms": latencies}))


def run_one(args, modules, variant, repeat, workload, directory):
    import psutil
    ready = directory / "ready.json"
    ready.unlink(missing_ok=True)
    env = os.environ.copy()
    for key in ("OBS_METRICS_ENABLED", "OBS_CONTROL_FILE"):
        env.pop(key, None)
    enabled = variant == "dynamic_enabled"
    if enabled:
        control = directory / "control"
        control.write_text("enabled")
        control.chmod(0o600)
        env["OBS_CONTROL_FILE"] = str(control)
    command = [sys.executable, "-B", __file__, "--mode", "server", "--modules", str(modules), "--ready", str(ready)]
    if enabled:
        command.append("--enabled")
    server = subprocess.Popen(command, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    result = {"variant": variant, "repeat": repeat, "workload": workload}
    load = None
    rss_stop = threading.Event()
    rss_thread = None
    try:
        deadline = time.monotonic() + 10
        while not ready.exists():
            if server.poll() is not None or time.monotonic() > deadline:
                raise RuntimeError("canary did not become ready")
            time.sleep(.025)
        identity = json.loads(ready.read_text())
        process = psutil.Process(server.pid)
        duration = args.seconds if workload == "paced" else args.capacity_seconds
        load = subprocess.Popen([sys.executable, "-B", __file__, "--mode", "client", "--port", str(identity["port"]), "--seconds", str(duration), "--rate", str(args.rate if workload == "paced" else 0)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        rss = []
        def sample_rss():
            while not rss_stop.is_set():
                try:
                    rss.append(process.memory_info().rss)
                except psutil.NoSuchProcess:
                    return
                rss_stop.wait(.05)
        rss_thread = threading.Thread(target=sample_rss, daemon=True)
        rss_thread.start()
        # Drain stdout while the independent client runs: its raw evidence may
        # exceed a pipe buffer. Sample server RSS from this parent process.
        out, err = load.communicate(timeout=duration + 15)
        rss_stop.set()
        rss_thread.join(timeout=1)
        rss.append(process.memory_info().rss)
        if load.returncode:
            raise RuntimeError("client failed: " + err)
        result["client"] = json.loads(out)
        out, err = server.communicate("stop\n", timeout=5)
        if server.returncode:
            raise RuntimeError("server failed: " + err)
        result["server"] = json.loads(out)
        result["rss_sampled_peak_bytes"] = max(rss)
        result["cpu_percent_one_core"] = 100 * result["server"]["cpu_seconds"] / result["server"]["wall_seconds"]
        result["pass"] = not result["client"]["errors"] and not result["server"]["errors"]
    except Exception as exc:
        result.update({"pass": False, "failure": str(exc)})
    finally:
        rss_stop.set()
        if rss_thread:
            rss_thread.join(timeout=1)
        if load is not None and load.poll() is None:
            load.kill()
            load.communicate()
        if server.poll() is None:
            server.kill()
            server.communicate()
    return result


def orchestrate(args):
    if platform.system() != "Linux":
        raise SystemExit("Linux required; Windows results are not acceptance evidence")
    root = Path(__file__).resolve().parents[1]
    results = []
    with tempfile.TemporaryDirectory(prefix="obs-dynamic-") as temporary:
        directory = Path(temporary)
        directory.chmod(0o700)
        baseline = directory / "baseline"
        baseline.mkdir()
        for name in MODULES:
            data = subprocess.check_output(["git", "show", f"{BASE}:backend/{name}"], cwd=root)
            (baseline / name).write_bytes(data)
        variants = ["production_baseline", "candidate_disabled", "dynamic_enabled"]
        for repeat in range(args.repeats):
            order = variants[repeat % 3:] + variants[:repeat % 3]
            if repeat % 2:
                order = list(reversed(order))
            for variant in order:
                for workload in ("paced", "capacity"):
                    result = run_one(args, baseline if variant == "production_baseline" else root / "backend", variant, repeat, workload, directory)
                    results.append(result)
                    Path(args.output).write_text(json.dumps({"base": BASE, "head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip(), "results": results}, indent=2))
    summaries = {}
    for variant in variants:
        records = [r for r in results if r["variant"] == variant and r.get("pass")]
        paced = [r for r in records if r["workload"] == "paced"]
        capacity = [r for r in records if r["workload"] == "capacity"]
        if len(paced) != args.repeats or len(capacity) != args.repeats:
            summaries[variant] = {"complete": False}
            continue
        summaries[variant] = {"complete": True, "p95_ms": statistics.median(r["client"]["p95_ms"] for r in paced), "cpu_percent_one_core": statistics.median(r["cpu_percent_one_core"] for r in paced), "rss_bytes": max(r["rss_sampled_peak_bytes"] for r in records), "throughput": statistics.median(r["client"]["throughput"] for r in capacity)}
    accepted = all(r.get("pass") for r in results)
    paired_comparisons = []
    for repeat in range(args.repeats):
        for variant in variants[1:]:
            pairs = { (r["variant"], r["workload"]): r for r in results if r["repeat"] == repeat and r.get("pass") }
            keys = [(v, w) for v in ("production_baseline", variant) for w in ("paced", "capacity")]
            if not all(k in pairs for k in keys):
                paired_comparisons.append({"repeat": repeat, "variant": variant, "pass": False, "reason": "incomplete workload"})
                accepted = False
                continue
            b, c = pairs[("production_baseline", "paced")], pairs[(variant, "paced")]
            bc, cc = pairs[("production_baseline", "capacity")], pairs[(variant, "capacity")]
            row = {"repeat": repeat, "variant": variant, "p95_change_percent": 100 * (c["client"]["p95_ms"] / b["client"]["p95_ms"] - 1), "throughput_change_percent": 100 * (cc["client"]["throughput"] / bc["client"]["throughput"] - 1), "cpu_added_percentage_points": c["cpu_percent_one_core"] - b["cpu_percent_one_core"], "rss_added_bytes": max(c["rss_sampled_peak_bytes"], cc["rss_sampled_peak_bytes"]) - max(b["rss_sampled_peak_bytes"], bc["rss_sampled_peak_bytes"])}
            row["pass"] = row["p95_change_percent"] <= 5 and row["throughput_change_percent"] >= -5 and row["cpu_added_percentage_points"] <= 1 and row["rss_added_bytes"] <= 8 * 1024 * 1024
            paired_comparisons.append(row)
            accepted = accepted and row["pass"]
    base = summaries["production_baseline"]
    comparisons = {}
    for variant in variants[1:]:
        row = summaries[variant]
        if not row["complete"] or not base["complete"]:
            accepted = False
            continue
        comparison = {"p95_change_percent": 100 * (row["p95_ms"] / base["p95_ms"] - 1), "throughput_change_percent": 100 * (row["throughput"] / base["throughput"] - 1), "cpu_added_percentage_points": row["cpu_percent_one_core"] - base["cpu_percent_one_core"], "rss_added_bytes": row["rss_bytes"] - base["rss_bytes"]}
        comparison["pass"] = comparison["p95_change_percent"] <= 5 and comparison["throughput_change_percent"] >= -5 and comparison["cpu_added_percentage_points"] <= 1 and comparison["rss_added_bytes"] <= 8 * 1024 * 1024
        comparisons[variant] = comparison
        accepted = accepted and comparison["pass"]
    evidence = json.loads(Path(args.output).read_text())
    evidence.update({"environment": {"python": sys.version, "platform": platform.platform(), "cpu_count": os.cpu_count()}, "summaries": summaries, "comparisons": comparisons, "paired_comparisons": paired_comparisons, "accepted": accepted, "limitations": ["Synthetic loopback ASGI only; no business traffic or provider/DB connections", "RSS is sampled every50ms; shorter transient peaks may be missed", "Shared CI runner variability; every raw repetition retained"]})
    Path(args.output).write_text(json.dumps(evidence, indent=2))
    print(json.dumps({"summaries": summaries, "comparisons": comparisons, "paired_comparisons": paired_comparisons, "accepted": accepted}, indent=2))
    return 0 if accepted else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("benchmark", "server", "client"), default="benchmark")
    parser.add_argument("--modules")
    parser.add_argument("--ready")
    parser.add_argument("--enabled", action="store_true")
    parser.add_argument("--port", type=int)
    parser.add_argument("--seconds", type=float, default=10)
    parser.add_argument("--capacity-seconds", type=float, default=6)
    parser.add_argument("--rate", type=float, default=300)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--output", default="evidence/dynamic-benchmark.json")
    args = parser.parse_args()
    if args.mode == "server":
        asyncio.run(serve(args))
    elif args.mode == "client":
        asyncio.run(client(args))
    else:
        raise SystemExit(orchestrate(args))
