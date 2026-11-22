"""Read-only source investigation; only own ephemeral loopback fixture is written."""
import asyncio
from copy import deepcopy
import json
import os
from pathlib import Path
import time

import httpx
import pytest

from demand_read_mongo_fixture import read_db, read_mongo_uri
from test_operational_workspace_demand_http import application, seed_workspace, BASE, HEADERS
import demand_read_jobs as jobs
import demand_read_store as store
from operational_balance_store import STATES


@pytest.mark.asyncio
async def test_actual_cold_read_timeline(read_db, read_mongo_uri, monkeypatch):
    db = read_db
    before = await seed_workspace(db, suppliers=200, movements=5000)
    monkeypatch.setenv("MONGO_URL", read_mongo_uri)
    monkeypatch.setenv("DB_NAME", db.name)
    monkeypatch.setenv("MEZAN_BACKGROUND_WORKER_PROCESS", "1")
    monkeypatch.setenv("PYTHON_DOTENV_DISABLED", "1")
    metrics = []
    monkeypatch.setattr(jobs, "_emit_metrics", lambda record: metrics.append(deepcopy(record)))
    records = []

    async def execute(claim):
        timeline = {"family": claim["family"]}
        started = time.perf_counter()
        async def launcher(*args, **kwargs):
            timeline["launch_enter_ms"] = (time.perf_counter()-started)*1000
            process = await asyncio.create_subprocess_exec(*args, **kwargs)
            timeline["launch_return_ms"] = (time.perf_counter()-started)*1000
            async def exited():
                result = await process.wait()
                timeline["child_exit_ms"] = (time.perf_counter()-started)*1000
                timeline["exit_code"] = result
            timeline["exit_task"] = asyncio.create_task(exited())
            return process
        work = asyncio.create_task(jobs.run_claim(db, claim, launcher=launcher))
        try:
            async with asyncio.timeout(30):
                while not work.done():
                    current = await db[store.JOBS].find_one({"_id":claim["_id"]},{"status":1})
                    if current["status"] == "ready" and "publication_observed_ms" not in timeline:
                        timeline["publication_observed_ms"] = (time.perf_counter()-started)*1000
                        held = await db[store.JOBS].find_one({"_id":jobs.RUNNER_ID})
                        timeline["runner_same_token_at_ready"] = held.get("lease_token")==claim["lease_token"]
                    await asyncio.sleep(.05)
                await work
        finally:
            if not work.done():
                work.cancel()
                await asyncio.gather(work, return_exceptions=True)
        timeline["run_claim_complete_ms"] = (time.perf_counter()-started)*1000
        if "exit_task" in timeline:
            await timeline.pop("exit_task")
        row = await db[store.JOBS].find_one({"_id":claim["_id"]},{"status":1,"error_code":1})
        assert row["status"] == "ready", (row,metrics)
        timeline["child_metrics"] = deepcopy(metrics[-1])
        timeline["startup_and_shutdown_outside_child_metric_ms"] = timeline["run_claim_complete_ms"] - metrics[-1]["wall_ms"]
        if "publication_observed_ms" in timeline:
            timeline["observed_publication_to_runner_release_ms"] = timeline["run_claim_complete_ms"]-timeline["publication_observed_ms"]
        records.append(timeline)
        return timeline

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application(db)),base_url="http://isolated") as client:
        baseline={}
        started=time.perf_counter()
        legacy_home=await client.get(BASE+"/context",params={"home_preview":True,"provider_positions":True,"ad_bank_fx":True})
        baseline["home_ms"]=(time.perf_counter()-started)*1000
        assert legacy_home.status_code==200,legacy_home.text
        started=time.perf_counter()
        legacy_report=await client.get("/api/operational-balances/financial-position",params={"view":"summary"})
        baseline["report_ms"]=(time.perf_counter()-started)*1000
        assert legacy_report.status_code==200,legacy_report.text
        started=time.perf_counter()
        home=await client.get(BASE+"/home",headers=HEADERS)
        home_initial_ms=(time.perf_counter()-started)*1000
        assert home.status_code==202,home.text
        report=await client.get(BASE+"/report",params={"view":"summary"},headers=HEADERS)
        assert report.status_code==202,report.text
        first=await jobs.claim_next(db)
        assert first["family"]=="operational_home"
        await execute(first)
        next_stamp=time.perf_counter()
        second=await jobs.claim_next(db)
        claim_latency=(time.perf_counter()-next_stamp)*1000
        assert second["family"]=="operational_report"
        ready=await client.get(BASE+"/home",headers=HEADERS)
        assert ready.status_code==200,ready.text
        assert ready.json()["summary"]["total"]=="1000000.00"
        assert ready.json()["recent_movements"]==legacy_home.json()["recent_movements"]
        await execute(second)
        started=time.perf_counter()
        report_ready=await client.get(BASE+"/report",params={"view":"summary"},headers=HEADERS)
        ready_report_ms=(time.perf_counter()-started)*1000
        assert report_ready.status_code==200,report_ready.text
        for key,value in legacy_report.json().items():
            if key!="generated_at":
                assert report_ready.json()[key]==value,key
        source=await db[STATES].find_one({"_id":"owner"})
        assert source==before
    evidence={"scope":"own isolated local replica; no production data or requests; no browser timing",
        "fixture":{"suppliers":200,"movements":5000},"baseline":baseline,
        "initial_home_http_202_ms":home_initial_ms,"queued_second_claim_ms":claim_latency,
        "ready_report_http_ms":ready_report_ms,"jobs":records,"canonical_results_equal":True,"source_unchanged":True}
    Path(os.environ["COLD_PROFILE_REPORT"]).write_text(json.dumps(evidence,indent=2)+"\n")
