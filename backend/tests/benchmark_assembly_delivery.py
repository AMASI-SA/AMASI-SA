"""Local-only actual route/transaction timings, synthetic 20-second provider.

Run from backend with PYTHONPATH=backend:backend/tests, local replica-set
MZ2_TEST_MONGO_URI and an output JSON path. Does not start the server.
"""
import asyncio
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch

from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport
import preparation_piece_operations as current
from test_assembly_completion_delivery import DeliveryTests


async def measure(module):
    fixture = DeliveryTests("test_atomic_ready_concurrent_and_response_loss_readback")
    await fixture.asyncSetUp()
    app = FastAPI()
    async def actor():
        return fixture.actor
    app.include_router(module.make_preparation_piece_operations_router(fixture.db, actor))
    stages = {"progress_seconds": 0, "decision_seconds": 0, "progress_calls": 0,
              "decision_calls": 0, "provider_seconds": 0, "provider_calls": 0}
    progress, decision = module._assembly_progress, module.build_order_fulfillment_decision
    async def time_stage(name, fn, *args, **kwargs):
        start = time.perf_counter()
        try:
            return await fn(*args, **kwargs)
        finally:
            stages[name + "_seconds"] += time.perf_counter() - start
            stages[name + "_calls"] += 1
    async def progress_timed(*a, **kw):
        return await time_stage("progress", progress, *a, **kw)
    async def decision_timed(*a, **kw):
        return await time_stage("decision", decision, *a, **kw)
    async def slow_provider(*a, **kw):
        start = time.perf_counter()
        await asyncio.sleep(20)
        stages["provider_seconds"] += time.perf_counter() - start
        stages["provider_calls"] += 1
        return {"ready": True, "order_status_completed": True}
    try:
        with patch.object(module, "_actor_context", return_value=fixture.context), \
             patch.object(module, "_assembly_progress", progress_timed), \
             patch.object(module, "build_order_fulfillment_decision", decision_timed), \
             patch.object(module, "sync_completed_carrier_label", slow_provider):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                durations = []
                for piece in fixture.ids:
                    started = time.perf_counter()
                    response = await client.post(f"/preparation-work-v1/assembly/pieces/{piece}/ready",
                        json={"client_request_id": "synthetic-benchmark-" + piece})
                    durations.append(time.perf_counter() - started)
                    assert response.status_code == 200, response.text
                assert await fixture.on_hand() == 16
        return {"ordinary_ready_seconds": durations[0], "last_ready_seconds": durations[1], **stages}
    finally:
        await fixture.asyncTearDown()


async def main():
    root = Path(__file__).resolve().parents[2]
    source = subprocess.check_output(["git", "show", "d55b1b86:backend/preparation_piece_operations.py"], cwd=root)
    with tempfile.TemporaryDirectory(prefix="mezan-ready-baseline-") as temp:
        file = Path(temp) / "baseline_ready.py"
        file.write_bytes(source)
        spec = importlib.util.spec_from_file_location("baseline_ready", file)
        baseline = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = baseline
        spec.loader.exec_module(baseline)
        results = {"environment": "local Mongo replica set; synthetic products; provider awaited sleep20; no production calls",
                   "baseline_source": "d55b1b86", "before": await measure(baseline), "after": await measure(current)}
    Path(sys.argv[1]).write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
