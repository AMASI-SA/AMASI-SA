"""One predeclared synthetic session. No business imports or external services.

Yappi CPU and wall runs are separate; none runs estimate profiler perturbation,
not acceptance. Exact runtime blobs are git-show extracted and hash checked.
No monkeypatches to metrics, locks, Watcher, Mongo hooks or Governor.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import threading
import time

from obs1316_attribution import BASE, TARGET, MODULES, client

VARIANTS = ("old_static", "new_static", "new_dynamic")
CLOCKS = ("none", "cpu", "wall")
ORDERS = ((0, 1, 2), (1, 2, 0), (2, 0, 1))
SECONDS = 12
RATE = 300


def thread_cpu():
    # POSIX per-thread CPU clock excludes sleep and blocking wait. Workers are
    # alive until after the final reading; no clock lookup on exited threads.
    rows = []
    for thread in threading.enumerate():
        if thread.ident is not None:
            try:
                clock = time.pthread_getcpuclockid(thread.ident)
                rows.append(dict(name=thread.name, ident=thread.ident,
                                 native_id=thread.native_id,
                                 cpu_seconds=time.clock_gettime(clock)))
            except (OSError, ValueError) as exc:
                rows.append(dict(name=thread.name, error=type(exc).__name__))
    return rows


def profile_rows(yappi):
    rows = []
    for thread in yappi.get_thread_stats():
        funcs = []
        for f in yappi.get_func_stats(ctx_id=thread.id):
            funcs.append(dict(name=f.name, module=f.module, line=f.lineno,
                              calls=f.ncall, actual_calls=f.nactualcall,
                              inclusive_seconds=f.ttot, exclusive_seconds=f.tsub,
                              builtin=f.builtin, index=f.index,
                              children=[dict(index=c.index, name=c.name, module=c.module, calls=c.ncall,
                                             inclusive_seconds=c.ttot,
                                             exclusive_seconds=c.tsub)
                                        for c in f.children]))
        rows.append(dict(name=thread.name, context_id=thread.id, ident=thread.tid,
                         total_seconds=thread.ttot, schedules=thread.sched_count,
                         functions=funcs))
    return rows


async def serve(args):
    import yappi
    sys.path.insert(0, args.modules)
    import observability_metrics as registry
    from observability_middleware import DiagnosticsMiddleware
    from runtime_diagnostics import start_lag_monitor
    metrics = registry.metrics
    # Start before creating Watcher: both request and Watcher threads covered.
    # CPU mode uses thread CPU clocks, not process CPU for each function.
    if args.clock != "none":
        yappi.set_clock_type(args.clock)
        yappi.start(builtins=True, profile_threads=True)
    profile_start = time.monotonic()
    monitor = start_lag_monitor()
    deadline = time.monotonic() + 5
    while not metrics.enabled and time.monotonic() < deadline:
        await asyncio.sleep(.02)
    if not metrics.enabled:
        raise RuntimeError("Activation did not complete")
    # Fixed one-second warmup of monitor; no business startup.
    await asyncio.sleep(1)
    counts = {"requests": 0, "errors": [], "disabled_observations": 0}
    samples = []
    pending = set()

    async def synthetic(scope, receive, send):
        value = sum(i*i for i in range(128))
        await asyncio.sleep(0)
        body = json.dumps({"synthetic": value}, separators=(",", ":")).encode()
        await send({"type": "http.response.start", "status": 200})
        await send({"type": "http.response.body", "body": body})

    app = DiagnosticsMiddleware(synthetic)

    async def connection(reader, writer):
        task = asyncio.current_task()
        pending.add(task)
        try:
            await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 3)
            messages = []
            async def receive():
                return {"type": "http.request", "body": b""}
            async def send(message):
                messages.append(message)
            if not metrics.enabled:
                counts["disabled_observations"] += 1
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
            pending.discard(task)

    server = await asyncio.start_server(connection, "127.0.0.1", 0)
    window_start = time.monotonic()
    cpu_start = time.process_time()
    threads_before = thread_cpu()
    Path(args.ready).write_text(json.dumps({"port": server.sockets[0].getsockname()[1]}))
    # Diagnostic stop-file polling is identical across all three cases.
    # Snapshot audit only once/second; every request also checks enabled state.
    audit_at = 0.
    while not Path(args.stop).exists():
        await asyncio.sleep(.1)
        if not metrics.enabled:
            counts["disabled_observations"] += 1
        if time.monotonic() >= audit_at:
            snap = metrics.snapshot()
            control = getattr(registry, "_control", None)
            samples.append(dict(at=time.monotonic()-window_start,
                                enabled=snap["enabled"],
                                requests=counts["requests"],
                                recorded=snap["counters"].get("api.status.2xx", 0),
                                latched=control.latched if control else None))
            audit_at = time.monotonic()+1
        if time.monotonic()-window_start > 30:
            raise TimeoutError("Bounded profile session exceeded deadline")
    server.close()
    await server.wait_closed()
    if pending:
        await asyncio.gather(*pending)
    snap = metrics.snapshot()
    control = getattr(registry, "_control", None)
    control_state = dict(present=control is not None,
                         latched=control.latched if control else None,
                         alive=control.thread.is_alive() if control else False)
    threads_after = thread_cpu()
    duration = time.monotonic()-window_start
    cpu_seconds = time.process_time()-cpu_start
    yappi.stop()
    profiled_seconds = time.monotonic()-profile_start
    profiles = profile_rows(yappi) if args.clock != "none" else []
    invariant = (snap["enabled"] and counts["disabled_observations"] == 0
                 and snap["counters"].get("api.status.2xx", 0) == counts["requests"]
                 and snap["histograms"].get("api.duration.other", {}).get("count", 0) == counts["requests"]
                 and snap["api"]["active"] == 0
                 and not control_state["latched"])
    monitor.cancel()
    await asyncio.gather(monitor, return_exceptions=True)
    print(json.dumps(dict(**counts, instrumentation_verified=bool(invariant),
                         window_seconds=duration, cpu_seconds=cpu_seconds,
                         profile_window_seconds=profiled_seconds,
                         threads_before=threads_before, threads_after=threads_after,
                         profiles=profiles, samples=samples, metrics=snap,
                         control=control_state)))


def run_cell(modules, variant, clock, repeat, directory):
    ready, stop = directory/"ready.json", directory/"stop"
    ready.unlink(missing_ok=True)
    stop.unlink(missing_ok=True)
    env = os.environ.copy()
    for key in ("OBS_CONTROL_FILE", "OBS_METRICS_ENABLED"):
        env.pop(key, None)
    if variant == "new_dynamic":
        control = directory/"control"
        control.write_text("enabled")
        control.chmod(0o600)
        env["OBS_CONTROL_FILE"] = str(control)
    else:
        env["OBS_METRICS_ENABLED"] = "true"
    args = [sys.executable, "-B", __file__, "--mode", "server", "--clock", clock,
            "--modules", str(modules), "--ready", str(ready), "--stop", str(stop)]
    server = subprocess.Popen(args, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    result = dict(variant=variant, clock=clock, repeat=repeat)
    try:
        deadline = time.monotonic()+10
        while not ready.exists():
            if server.poll() is not None or time.monotonic() > deadline:
                raise RuntimeError("Profile child not ready")
            time.sleep(.025)
        port = json.loads(ready.read_text())["port"]
        load = subprocess.run([sys.executable, "-B", __file__, "--mode", "client",
                               "--port", str(port), "--seconds", str(SECONDS),
                               "--rate", str(RATE)], capture_output=True, text=True, timeout=25)
        result["client_exit"] = load.returncode
        result["client_stderr"] = load.stderr
        if load.returncode == 0:
            result["client"] = json.loads(load.stdout)
        stop.write_text("stop")
        out, err = server.communicate(timeout=10)
        result.update(server_exit=server.returncode, server_stderr=err)
        if server.returncode == 0:
            result["server"] = json.loads(out)
        else:
            result["server_stdout"] = out
        result["valid"] = (load.returncode == 0 and server.returncode == 0
                           and result["server"]["instrumentation_verified"]
                           and not result["server"]["errors"]
                           and not result["client"]["errors"])
    except Exception as exc:
        result.update(valid=False, failure=repr(exc))
        if server.poll() is not None:
            out, err = server.communicate()
            result.update(failed_child_stdout=out, failed_child_stderr=err)
    finally:
        if server.poll() is None:
            server.kill()
            out, err = server.communicate()
            result.update(terminated_child_stdout=out, terminated_child_stderr=err)
    return result


def main(args):
    if platform.system() != "Linux":
        raise SystemExit("Linux only; no local substitute measurements")
    root = Path(__file__).resolve().parents[2]
    evidence = dict(source=TARGET, base=BASE,
                    diagnostic_head=subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
                    settings=dict(seconds=SECONDS, rps=RATE, repeats=3, variants=VARIANTS,
                                  clocks=CLOCKS, orders=ORDERS,
                                  purpose="profiling only, not acceptance"),
                    environment=dict(python=sys.version, platform=platform.platform(), cpus=os.cpu_count()),
                    runtime_hashes={}, tool_hashes={}, results=[])
    for name in ("obs1316_profile.py", "obs1316_attribution.py"):
        evidence["tool_hashes"][name] = hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
    def save():
        Path(args.output).write_text(json.dumps(evidence, indent=2))
    with tempfile.TemporaryDirectory(prefix="obs-profile-") as temp:
        directory = Path(temp)
        directory.chmod(0o700)
        dirs = {}
        for label, ref in (("old", BASE), ("new", TARGET)):
            dirs[label] = directory/label
            dirs[label].mkdir()
            evidence["runtime_hashes"][label] = {}
            for module in MODULES:
                blob = subprocess.check_output(["git", "show", f"{ref}:backend/{module}"], cwd=root)
                path = dirs[label]/module
                path.write_bytes(blob)
                assert path.read_bytes() == blob
                evidence["runtime_hashes"][label][module] = hashlib.sha256(blob).hexdigest()
        save()
        # Latin rotation of variants and profiler order, declared before run.
        for repeat, order in enumerate(ORDERS):
            for index in order:
                for clock_index in ORDERS[(repeat+index) % 3]:
                    result = run_cell(dirs["old" if index == 0 else "new"],
                                      VARIANTS[index], CLOCKS[clock_index], repeat, directory)
                    evidence["results"].append(result)
                    save()
                    print(json.dumps({k:v for k,v in result.items() if k not in ("client", "server")}), flush=True)
        # Ensure runtime files used by children remained byte-identical.
        evidence["runtime_hashes_after"] = {label:{m:hashlib.sha256((p/m).read_bytes()).hexdigest()
                                                   for m in MODULES} for label,p in dirs.items()}
    evidence["complete"] = len(evidence["results"]) == 27
    evidence["all_cells_valid"] = all(r["valid"] for r in evidence["results"])
    save()
    return 0 if evidence["complete"] and evidence["all_cells_valid"] else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("session", "server", "client"), default="session")
    parser.add_argument("--clock", choices=CLOCKS, default="none")
    parser.add_argument("--modules")
    parser.add_argument("--ready")
    parser.add_argument("--stop")
    parser.add_argument("--port", type=int)
    parser.add_argument("--seconds", type=float, default=SECONDS)
    parser.add_argument("--rate", type=float, default=RATE)
    parser.add_argument("--output", default="evidence/profile.json")
    args = parser.parse_args()
    if args.mode == "server":
        asyncio.run(serve(args))
    elif args.mode == "client":
        asyncio.run(client(args))
    else:
        raise SystemExit(main(args))
