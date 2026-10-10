"""Single bounded isolated overhead run; no application startup or network.

Nine cells fixed in advance: baseline/off/on, on/baseline/off, off/on/baseline.
Each cell: 0.5s warmup, 3s at 300 operations/s, 1s throughput with 8 tasks.
Synthetic 2ms async wait + identical computation. Not a Production cost claim.
"""
import asyncio
import contextlib
import hashlib
import json
import logging
import os
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import tempfile
import time

import psutil

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
import latency_evidence as evidence


def percentile(values, q):
    values = sorted(values)
    return values[min(len(values)-1, int((len(values)-1)*q))]


async def cell(mode):
    process = psutil.Process()
    evidence._last.clear()
    evidence._active.clear()
    evidence._identity = {"source_git_sha": "synthetic", "release_id": "synthetic"}
    evidence._enabled = mode == "on"
    null = contextlib.nullcontext
    results = []
    errors = 0

    async def operation():
        with evidence.capture("dashboard") if mode != "baseline" else null():
            with evidence.phase("computation_wall") if mode != "baseline" else null():
                value = sum(n*n for n in range(1000))
            with evidence.phase("mongo_cursor_await") if mode != "baseline" else null():
                await asyncio.sleep(.002)
            assert value == 332833500

    async def load(seconds, record=False):
        nonlocal errors
        start = time.perf_counter()
        async def one():
            nonlocal errors
            now = time.perf_counter()
            try:
                await operation()
            except Exception:
                errors += 1
            if record:
                results.append((time.perf_counter()-now)*1000)
        tasks = []
        for i in range(int(seconds*300)):
            await asyncio.sleep(max(0, start+i/300-time.perf_counter()))
            tasks.append(asyncio.create_task(one()))
        await asyncio.gather(*tasks)

    await load(.5)
    evidence._last.clear()  # include an actual sampled record in the measured window
    rss_start = process.memory_info().rss
    cpu_start, wall_start = time.process_time(), time.perf_counter()
    await load(3, True)
    wall = time.perf_counter()-wall_start
    cpu = (time.process_time()-cpu_start)/wall*100
    rss_end = process.memory_info().rss
    end = time.perf_counter()+1
    async def worker():
        count = 0
        while time.perf_counter() < end:
            await operation()
            count += 1
        return count
    t = time.perf_counter()
    throughput = sum(await asyncio.gather(*(worker() for _ in range(8))))/(time.perf_counter()-t)
    return {"mode": mode, "p95_ms": percentile(results, .95),
            "p99_ms": percentile(results, .99), "cpu_one_core_pct": cpu,
            "rss_start": rss_start, "rss_end": rss_end,
            "throughput": throughput, "errors": errors, "raw_ms": results}


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--cell":
        with tempfile.TemporaryFile(mode="w+") as output:
            handler = logging.StreamHandler(output)
            evidence.log.addHandler(handler)
            evidence.log.setLevel(logging.INFO)
            row = asyncio.run(cell(sys.argv[2]))
            output.seek(0)
            row["log_bytes"] = len(output.read().encode())
            print(json.dumps(row))
        return
    rows = []
    orders = [("baseline", "off", "on"), ("on", "baseline", "off"), ("off", "on", "baseline")]
    for repeat, order in enumerate(orders):
        for mode in order:
            result = subprocess.run([sys.executable, "-B", __file__, "--cell", mode],
                                    capture_output=True, text=True, check=True, timeout=25)
            rows.append({"repeat": repeat+1, **json.loads(result.stdout)})
    comparisons = []
    for repeat in (1, 2, 3):
        cells = {r["mode"]: r for r in rows if r["repeat"] == repeat}
        for before, after in (("baseline", "off"), ("baseline", "on"), ("off", "on")):
            a, b = cells[before], cells[after]
            comparisons.append({"repeat": repeat, "comparison": after+"-"+before,
                "p95_delta_ms": b["p95_ms"]-a["p95_ms"],
                "p99_delta_ms": b["p99_ms"]-a["p99_ms"],
                "cpu_delta_pp": b["cpu_one_core_pct"]-a["cpu_one_core_pct"],
                "rss_delta_mib": (b["rss_end"]-a["rss_end"])/1024**2,
                "throughput_delta_pct": (b["throughput"]/a["throughput"]-1)*100})
    output = {"source": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
              "python": sys.version, "platform": platform.platform(),
              "runtime_sha256": hashlib.sha256((ROOT/"backend/latency_evidence.py").read_bytes()).hexdigest(),
              "rows": rows, "paired": comparisons,
              "limits": "diagnostic only; all repetitions retained; not a Production acceptance gate"}
    Path(sys.argv[1]).write_text(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
