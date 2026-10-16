"""Two loopback-only synthetic workers; no business startup or provider access.

One predefined off/on/off run. Diagnostic timing, not Production acceptance.
Requires a disposable Mongo8.0.12 replica set at 127.0.0.1:27968.
"""
import asyncio
import contextlib
import hashlib
import json
import logging
import math
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
URI = "mongodb://127.0.0.1:27968/?replicaSet=latencyTest"
PORTS = (28968, 28969)


def worker(port):
    import uvicorn
    from fastapi import FastAPI
    from motor.motor_asyncio import AsyncIOMotorClient
    import latency_evidence as evidence
    from latency_api_timing import LatencyEvidenceMiddleware
    from latency_control import attach_latency_control
    from runtime_diagnostics import start_lag_monitor
    logging.basicConfig(level=logging.INFO)
    client = AsyncIOMotorClient(URI, serverSelectionTimeoutMS=1000)
    db = client[os.environ["LATENCY_SYNTHETIC_DB"]]
    app = FastAPI()
    evidence._identity = {"release_id": "synthetic", "source_git_sha": "synthetic"}
    app.add_middleware(LatencyEvidenceMiddleware)
    attach_latency_control(app, db, "synthetic")
    async def startup():
        app.state.lag_task = start_lag_monitor()
    async def shutdown():
        app.state.lag_task.cancel()
        client.close()
    app.router.add_event_handler("startup", startup)
    app.router.add_event_handler("shutdown", shutdown)
    @app.get("/health")
    async def health():
        return {"ok": True}
    @app.get("/api/dashboard")
    async def dashboard():
        with evidence.capture("dashboard"):
            with evidence.phase("computation_wall"):
                result = sum(n * n for n in range(1000))
            with evidence.phase("mongo_cursor_await"):
                await db.synthetic.find_one({"_id": "fixture"})
            return {"sum": result}
    @app.get("/api/snapchat-simulation")
    async def snapchat():
        with evidence.capture("snapchat"), evidence.phase("provider_http_await"):
            await asyncio.sleep(.002)  # no provider connection
        return {"simulated": True}
    @app.get("/synthetic-stall")
    async def stall():
        with evidence.capture("dashboard"):
            time.sleep(1.35)
        return {"synthetic": True}
    uvicorn.run(app, host="127.0.0.1", port=port, access_log=False, log_level="warning")


async def parent(output):
    import httpx
    import psutil
    from pymongo import MongoClient
    output.mkdir(parents=True, exist_ok=True)
    report = {"kind": "synthetic_diagnostic_not_production_acceptance", "rows": [], "failures": [],
              "plan": "off/on/off; 2s warmup + 10s at100rps +3s throughput; one run; then isolated stall/restart",
              "head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
              "runtime_hashes": {name: hashlib.sha256((ROOT / "backend" / name).read_bytes()).hexdigest()
                                 for name in ("latency_control.py", "latency_evidence.py", "latency_api_timing.py")}}
    mongo = MongoClient(URI, serverSelectionTimeoutMS=1500)
    assert mongo.server_info()["version"] == "8.0.12"
    assert mongo.admin.command("hello")["setName"] == "latencyTest"
    database = "latency_control_rehearsal_" + uuid.uuid4().hex
    mongo[database].synthetic.insert_one({"_id": "fixture", "synthetic": True})
    token = uuid.uuid4().hex + uuid.uuid4().hex
    env = {k: v for k, v in os.environ.items() if k in ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "HOME")}
    env.update(PYTHONDONTWRITEBYTECODE="1", PYTHON_DOTENV_DISABLED="1",
               LATENCY_EVIDENCE_ENABLED="1", LATENCY_EVIDENCE_CONTROL_ENABLED="1",
               LATENCY_EVIDENCE_CONTROL_TOKEN=token, LATENCY_EVIDENCE_EXPECTED_WORKERS="2",
               LATENCY_SYNTHETIC_DB=database)
    children, logs = [], []
    def launch(port):
        log = (output / f"worker-{port}-{len(logs)}.log").open("w", encoding="utf8")
        logs.append(log)
        process = subprocess.Popen([sys.executable, "-B", __file__, "--worker", str(port)],
                                   env=env, stdout=log, stderr=log,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        children.append(process)
        return process
    for port in PORTS:
        launch(port)
    prefix = f"http://127.0.0.1:{PORTS[0]}/api/internal/latency-evidence"
    headers = {"X-Mezan-Latency-Control-Token": token}
    async with httpx.AsyncClient(timeout=3, trust_env=False) as client:
        async def status():
            r = await client.get(prefix, headers=headers)
            r.raise_for_status()
            return r.json()
        async def wait_for(predicate, timeout=20):
            end = time.monotonic() + timeout
            while time.monotonic() < end:
                try:
                    value = await status()
                    if predicate(value):
                        return value
                except httpx.TransportError:
                    pass
                await asyncio.sleep(.25)
            raise AssertionError("worker confirmation timeout")
        async def load(seconds, paced=True):
            samples, errors = [], []
            start = time.perf_counter()
            async def one(i):
                url = f"http://127.0.0.1:{PORTS[i % 2]}" + (
                    "/api/dashboard" if i % 3 else "/api/snapchat-simulation")
                t = time.perf_counter()
                try:
                    response = await client.get(url)
                    response.raise_for_status()
                    expected = {"sum": 332833500} if i % 3 else {"simulated": True}
                    assert response.json() == expected
                except Exception as exc:
                    errors.append(type(exc).__name__)
                samples.append((time.perf_counter() - t) * 1000)
            if paced:
                tasks = []
                for i in range(int(seconds * 100)):
                    await asyncio.sleep(max(0, start + i / 100 - time.perf_counter()))
                    tasks.append(asyncio.create_task(one(i)))
                await asyncio.gather(*tasks)
            else:
                async def lane(n):
                    while time.perf_counter() - start < seconds:
                        await one(n)
                        n += 1
                await asyncio.gather(*(lane(n) for n in range(4)))
            return samples, errors, len(samples) / (time.perf_counter() - start)
        try:
            state = await wait_for(lambda s: len(s["workers"]) == 2)
            session = str(uuid.uuid4())
            for mode in ("off_before", "on", "off_after"):
                if mode != "off_before":
                    state = await status()
                    t = time.perf_counter()
                    r = await client.post(prefix + ("/start" if mode == "on" else "/stop"), headers=headers,
                        json={"release_id": "synthetic", "session_id": session,
                              "expected_revision": state["revision"], "duration_seconds": 900})
                    r.raise_for_status()
                    await wait_for(lambda s: s["state"] == ("enabled_confirmed" if mode == "on" else "disabled_confirmed"))
                    report[mode + "_confirmation_ms"] = (time.perf_counter() - t) * 1000
                await load(2)
                processes = [psutil.Process(p.pid) for p in children if p.poll() is None]
                cpu0 = [p.cpu_times().user + p.cpu_times().system for p in processes]
                rss0 = [p.memory_info().rss for p in processes]
                start = time.perf_counter()
                raw, errors, achieved = await load(10)
                elapsed = time.perf_counter() - start
                cpu = [(p.cpu_times().user + p.cpu_times().system - c) / elapsed * 100
                       for p, c in zip(processes, cpu0)]
                ordered = sorted(raw)
                burst, burst_errors, throughput = await load(3, paced=False)
                report["rows"].append({"mode": mode, "raw_ms": raw,
                    "p95_ms": ordered[math.ceil(.95 * len(ordered)) - 1],
                    "p99_ms": ordered[math.ceil(.99 * len(ordered)) - 1],
                    "cpu_one_core_pct_per_worker": cpu, "rss_start_per_worker": rss0,
                    "rss_end_per_worker": [p.memory_info().rss for p in processes],
                    "paced_rps": achieved, "throughput_rps": throughput,
                    "errors": errors + burst_errors, "state": await status()})
                assert not errors + burst_errors
            # Separate functional stress; never included in timing rows.
            state = await status()
            session = str(uuid.uuid4())
            r = await client.post(prefix + "/start", headers=headers, json={
                "release_id": "synthetic", "session_id": session,
                "expected_revision": state["revision"], "duration_seconds": 30})
            r.raise_for_status()
            await wait_for(lambda s: s["state"] == "enabled_confirmed")
            r = await client.get(f"http://127.0.0.1:{PORTS[0]}/synthetic-stall")
            assert r.status_code == 200
            assert (await client.get(f"http://127.0.0.1:{PORTS[0]}/health")).json() == {"ok": True}
            children[1].terminate()
            children[1].wait(timeout=10)
            launch(PORTS[1])
            state = await wait_for(lambda s: len(s["workers"]) == 3)
            new_workers = set(state["workers"]) - set(state["targets"])
            assert len(new_workers) == 1 and not state["workers"][new_workers.pop()]["enabled"]
            report["replacement_worker_did_not_join_session"] = True
            await wait_for(lambda s: not s["desired_enabled"], timeout=35)
            report["expiry_state"] = await status()
            assert report["expiry_state"]["state"] == "unconfirmed"  # dead target is not acknowledged
        except BaseException as exc:
            report["failures"].append(type(exc).__name__ + ": " + str(exc)[:160])
            raise
        finally:
            for process in children:
                if process.poll() is None:
                    process.terminate()
                    process.wait(timeout=10)
            for log in logs:
                log.close()
            report["tokens_in_logs"] = any(token in p.read_text() for p in output.glob("worker-*.log"))
            events = []
            for path in output.glob("worker-*.log"):
                for line in path.read_text().splitlines():
                    if "latency_evidence {" in line:
                        events.append(json.loads(line.split("latency_evidence ", 1)[1]))
            report["event_counts"] = {kind: sum(e["event"] == kind for e in events)
                                      for kind in ("stage_sample", "api_sample", "event_loop_lag")}
            report["recorded_workers"] = sorted({e["worker_instance"] for e in events})
            if report["tokens_in_logs"] or not all(report["event_counts"].values()):
                report["failures"].append("privacy_or_missing_recording_evidence")
            mongo.drop_database(database)
            mongo.close()
            (output / "result.json").write_text(json.dumps(report, indent=2), encoding="utf8")
    assert not report["failures"], "See retained result.json failures"


if __name__ == "__main__":
    if sys.argv[1] == "--worker":
        worker(int(sys.argv[2]))
    else:
        asyncio.run(parent(Path(sys.argv[1])))
