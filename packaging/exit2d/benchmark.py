"""Synthetic HTTP event-loop isolation benchmark, not Production sizing.

Uses the actual report boundary and separate Linux processes. No Mongo/provider
or customer data. Compares employee HTTP latency during one CPU-heavy report.
"""
import asyncio
import json
import multiprocessing
from pathlib import Path
import socket
import statistics
import sys
import time

sys.path.insert(0, str(Path("/opt/mezan/backend") if Path("/opt/mezan/backend").is_dir()
    else Path(__file__).resolve().parents[2] / "backend"))
import httpx


def serve(port, mode, entered):
    import uvicorn
    from independent_reports import ReportBoundary

    async def application(scope, receive, send):
        if scope["type"] == "lifespan":
            while True:
                message = await receive()
                if message["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                else:
                    await send({"type": "lifespan.shutdown.complete"})
                    return
        if scope["path"] == "/api/dashboard":
            entered.set()
            deadline = time.monotonic() + 0.6
            value = 0
            while time.monotonic() < deadline:
                value = (value + 17) % 100003
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    if mode == "split":
        # Owned by this child only; transport is created before its event loop
        # starts and used exclusively in that loop.
        application = ReportBoundary(application, role="web", worker_url="http://127.0.0.1:8011",
            client=httpx.AsyncClient(trust_env=False, timeout=5))
    uvicorn.run(application, host="127.0.0.1", port=port, access_log=False, log_level="error")


async def measure(mode):
    context = multiprocessing.get_context("spawn")
    entered = context.Event()
    processes = []
    try:
        if mode == "split":
            processes.append(context.Process(target=serve, args=(8011, "reports", entered)))
        processes.append(context.Process(target=serve, args=(8010, mode, entered)))
        for process in processes:
            process.start()
        for port in ([8011, 8010] if mode == "split" else [8010]):
            for _ in range(200):
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                        break
                except OSError:
                    await asyncio.sleep(0.05)
            else:
                raise RuntimeError("benchmark child not ready")
        async with httpx.AsyncClient(trust_env=False, timeout=5) as client:
            heavy = asyncio.create_task(client.get("http://127.0.0.1:8010/api/dashboard"))
            assert await asyncio.to_thread(entered.wait, 3), "CPU report never started"
            async def fast():
                started = time.perf_counter()
                response = await client.get("http://127.0.0.1:8010/api/employee-health")
                assert response.status_code == 200
                return (time.perf_counter() - started) * 1000
            latencies = sorted(await asyncio.gather(*(fast() for _ in range(20))))
            assert (await heavy).status_code == 200
        return {"p50_ms": round(statistics.median(latencies), 3),
            "p95_ms": round(latencies[18], 3), "max_ms": round(max(latencies), 3)}
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
            process.join(5)
            if process.is_alive():
                process.kill()
                process.join(2)


async def main():
    baseline = await measure("baseline")
    split = await measure("split")
    result = {"kind": "synthetic_process_isolation_not_production_capacity",
        "concurrent_employee_requests": 20, "report_cpu_wall_seconds": 0.6,
        "same_process": baseline, "independent_reports": split}
    print(json.dumps(result, sort_keys=True), flush=True)
    assert split["p95_ms"] < baseline["p95_ms"], "no measured isolation improvement"


if __name__ == "__main__":
    asyncio.run(main())
