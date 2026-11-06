"""Synthetic reporting/health contention and exact output comparison; no external IO."""
from __future__ import annotations

import argparse
import asyncio
from datetime import date, datetime, timedelta, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import statistics
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from fastapi import FastAPI
import httpx
from integrations_control_center import snapchat_account_hourly_refresh as current
from integrations_control_center.snapchat_freshness_impl_v6 import extract_provider_freshness


def fixture(count):
    start = datetime(2026, 11, 3, 21, tzinfo=timezone.utc)
    return {"request_status": "SUCCESS", "timeseries_stats": [{"sub_request_status": "SUCCESS",
        "timeseries_stat": {"granularity": "HOUR", "conversion_data_processed_end_time": start.isoformat(),
        "breakdown_stats": {"campaign": [{"id": f"synthetic-{i}", "timeseries": [{
            "start_time": (start + timedelta(hours=h)).isoformat(),
            "end_time": (start + timedelta(hours=h+1)).isoformat(),
            "stats": {"spend": 1_000_000, "impressions": 100, "swipes": 10,
                "conversion_purchases": 1, "conversion_purchases_value": 10_000_000},
        } for h in range(72)]} for i in range(count)]}}}]}


def digest(result):
    rows, errors, subrequests, accounts, campaigns, freshness = result
    encoded = json.dumps([rows, errors, subrequests, sorted(accounts.items()),
        sorted(campaigns.items()), freshness], sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


def sync_work(module, body):
    freshness = extract_provider_freshness(body)
    rows, errors, subrequests = module.extract_account_hour_rows(body)
    options = {"start_date": date(2026, 11, 4), "end_date": date(2026, 11, 6)}
    return rows, errors, subrequests, module.aggregate_account_hours_by_riyadh_day(rows, **options), \
        module.aggregate_campaign_hours_by_riyadh_day(rows, **options), freshness


async def cooperative_work(body):
    # Include the unchanged v6 response-capture scan, not just the newly
    # checkpointed functions, to expose residual blocking in the real path.
    freshness = extract_provider_freshness(body)
    rows, errors, subrequests = await current.extract_account_hour_rows_cooperative(body)
    options = {"start_date": date(2026, 11, 4), "end_date": date(2026, 11, 6)}
    accounts = await current.aggregate_account_hours_cooperative(rows, **options)
    campaigns = await current.aggregate_campaign_hours_cooperative(rows, **options)
    return rows, errors, subrequests, accounts, campaigns, freshness


async def measure(label, module, body, spacing):
    app = FastAPI()
    @app.get("/health-control")
    async def health():
        return {"ok": True}
    latencies = []
    start = time.monotonic() + 0.02
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://synthetic") as client:
        async def probe(index):
            planned = start + index * spacing
            await asyncio.sleep(max(0, planned-time.monotonic()))
            response = await client.get("/health-control")
            assert response.status_code == 200 and response.json() == {"ok": True}
            latencies.append((time.monotonic()-planned)*1000)

        async def reporting():
            await asyncio.sleep(max(0, start + spacing*3-time.monotonic()))
            begun = time.monotonic()
            result = sync_work(module, body) if label == "baseline" else await cooperative_work(body)
            return result, (time.monotonic()-begun)*1000

        job = asyncio.create_task(reporting())
        await asyncio.gather(*(probe(i) for i in range(100)))
        result, wall = await job
    ordered = sorted(latencies)
    return {"arm": label, "health_arrivals": len(latencies), "report_wall_ms": round(wall, 3),
        "health_planned_latency_ms": {"median": round(statistics.median(ordered), 3),
            "p95": round(ordered[94], 3), "p99": round(ordered[98], 3), "max": round(ordered[-1], 3)},
        "output_sha256": digest(result), "rows": len(result[0]),
        "spend_raw_micro_total": sum(row["sums"]["spend"] for row in result[3].values())}


async def main(args):
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    raw = subprocess.run(["git", "show", f"{args.baseline}:backend/integrations_control_center/snapchat_account_hourly_refresh.py"],
        cwd=ROOT, check=True, stdout=subprocess.PIPE).stdout
    baseline_path = output.parent / "baseline_hourly_source.py"
    if baseline_path.exists() and baseline_path.read_bytes() != raw:
        raise RuntimeError("refuse to replace a different baseline source")
    baseline_path.write_bytes(raw)
    name = "integrations_control_center._synthetic_baseline_hourly"
    spec = importlib.util.spec_from_file_location(name, baseline_path)
    baseline = importlib.util.module_from_spec(spec)
    sys.modules[name] = baseline
    spec.loader.exec_module(baseline)
    records = []
    for count in args.campaigns:
        body = fixture(count)
        pilot_start = time.monotonic()
        reference = sync_work(baseline, body)
        pilot_seconds = time.monotonic()-pilot_start
        expected = digest(reference)
        del reference
        spacing = max(0.01, pilot_seconds*1.35/96)
        for round_index in range(args.rounds):
            arms = ["baseline", "cooperative"] if round_index % 2 == 0 else ["cooperative", "baseline"]
            for arm in arms:
                record = await measure(arm, baseline, body, spacing)
                assert record["output_sha256"] == expected
                assert record["spend_raw_micro_total"] == count*72*1_000_000
                records.append({"campaigns": count, "round": round_index+1, **record})
                print(json.dumps(records[-1]), flush=True)
    output.write_text(json.dumps({"baseline_git_sha": args.baseline, "synthetic_only": True,
        "external_provider_or_database_requests": 0, "includes_v6_freshness_scan": True,
        "method": "100 fixed ASGI health arrivals per arm; latency includes scheduled-start delay; alternating arm order; exact full output SHA256 and independent spend total",
        "limitation": "Synthetic health control and reporting computation only; no live employee/network or hosted resource improvement claim",
        "records": records}, indent=2)+"\n", encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", default="b0b23a5ca62efe934d7eded08d2b7abdca9a9389")
    parser.add_argument("--campaigns", type=int, nargs="+", default=[200, 1000])
    parser.add_argument("--rounds", type=int, default=2)
    parser.add_argument("--output", required=True)
    asyncio.run(main(parser.parse_args()))
