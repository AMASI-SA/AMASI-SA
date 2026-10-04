"""Fresh-process, isolated-Mongo financial input reader benchmark (no Production).

The common-cohort comparison isolates reader/formula parity. Full-cohort results
separately demonstrate removal of the legacy invoice/adjustment truncation caps.
"""
import asyncio
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
import uuid
from datetime import date

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "backend"), str(ROOT / "backend/tests")]
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import monitoring
from benchmark_dashboard_cart_memory import peak_rss
from dashboard_recurring_reads import load_dashboard_recurring_inputs
from dashboard_settlement_reads import iter_dashboard_settlement_rows
from dashboard_spill import DashboardSpill
from recurring_obligations_routes import INVOICES, OBLIGATIONS, compute_recurring_obligations_for_range
from settlements_routes import aggregate_settlements_by_provider

URI = "mongodb://127.0.0.1:27261"
FILES = ("backend/dashboard_recurring_reads.py", "backend/recurring_obligations_routes.py",
         "backend/dashboard_settlement_reads.py", "backend/settlements_routes.py",
         "backend/dashboard_spill.py", "backend/tests/benchmark_dashboard_financial_reads.py")


class Reads(monitoring.CommandListener):
    def __init__(self):
        self.counts, self.max_batch, self.query_us = {}, 0, 0
    def started(self, event):
        pass
    def failed(self, event):
        pass
    def succeeded(self, event):
        if event.command_name not in {"find", "getMore"}:
            return
        cursor = event.reply.get("cursor", {})
        batch = cursor.get("firstBatch", cursor.get("nextBatch", []))
        name = cursor.get("ns", "").split(".")[-1]
        self.counts[name] = self.counts.get(name, 0) + len(batch)
        self.max_batch = max(self.max_batch, len(batch))
        self.query_us += event.duration_micros


class CommonCollection:
    def __init__(self, collection, cap):
        self.collection, self.cap = collection, cap
    def find(self, query, projection):
        if self.cap:
            query = dict(query, fixture_ordinal={"$lt": self.cap})
        return self.collection.find(query, projection)


class CommonDB:
    def __init__(self, db):
        self.db = db
    def __getitem__(self, name):
        return CommonCollection(self.db[name], {INVOICES: 20000, "payment_adjustments": 50000}.get(name))
    def __getattr__(self, name):
        return self[name]


async def worker(mode, database, size):
    assert database.startswith("dashboard_financial_benchmark_")
    reads = Reads()
    client = AsyncIOMotorClient(URI, event_listeners=[reads])
    raw_db = client[database]
    db = CommonDB(raw_db) if mode == "after_common" else raw_db
    store = DashboardSpill()
    baseline_rss = peak_rss()
    started = time.perf_counter()
    try:
        begin = time.perf_counter()
        inputs = None if mode == "before" else await load_dashboard_recurring_inputs(db, "owner", store)
        recurring = await compute_recurring_obligations_for_range(
            db, "owner", date(2026, 10, 10), date(2026, 10, 10), dashboard_inputs=inputs)
        recurring_ms = (time.perf_counter() - begin) * 1000
        begin = time.perf_counter()
        settlements = await aggregate_settlements_by_provider(
            db, "owner", row_loader=None if mode == "before" else iter_dashboard_settlement_rows)
        settlements_ms = (time.perf_counter() - begin) * 1000
        payload = {"recurring": recurring, "settlements": settlements}
        result = dict(mode=mode, dataset_size=size, elapsed_ms=(time.perf_counter() - started) * 1000,
                      recurring_ms=recurring_ms, settlements_ms=settlements_ms,
                      baseline_rss_bytes=baseline_rss, peak_rss_bytes=peak_rss(),
                      mongo_documents=reads.counts, max_mongo_wire_batch=reads.max_batch,
                      mongo_command_ms=reads.query_us / 1000, totals=payload,
                      response_bytes=len(json.dumps(payload).encode()),
                      max_materialized_rows=(max(min(size, 20000), min(size, 50000)) if mode == "before" else 128),
                      historical_selection_bound=None if mode == "before" else 3,
                      recurring_metrics=None if inputs is None else inputs.metrics,
                      source_hashes={name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in FILES})
        print(json.dumps(result))
    finally:
        store.close()
        client.close()


async def seed(db, size):
    await db[OBLIGATIONS].insert_one(dict(id="utility", user_id="owner", expense_type="electricity",
        status="active", cycle="monthly", start_date="2026-01-01", period_amount=310,
        estimation_basis="last_3_invoices"))
    for start in range(0, size, 128):
        invoices, adjustments = [], []
        for index in range(start, min(start + 128, size)):
            actual = index == size - 1
            invoices.append(dict(user_id="owner", obligation_id="utility", fixture_ordinal=index,
                amount=31 if actual else 60, period_start="2026-10-01" if actual else "2026-09-01",
                period_end="2026-10-31" if actual else "2026-09-30", unused_note="x" * 2048))
            adjustments.append(dict(user_id="owner", fixture_ordinal=index,
                payment_method=("mada", "tabby", "tamara", "cod")[index % 4],
                adjustment_amount=1.005, original_amount=10.125, new_amount=9.12, unused_note="x" * 2048))
        await db[INVOICES].insert_many(invoices)
        await db.payment_adjustments.insert_many(adjustments)


async def main(output, sizes):
    client = AsyncIOMotorClient(URI, serverSelectionTimeoutMS=3000)
    evidence = dict(mongo_uri=URI, isolated=True, sizes=sizes, results=[], comparisons=[],
                    cap_change="Dashboard reads all invoices/adjustments; legacy defaults cap invoices at 20,000 and adjustments at 50,000.",
                    financial_formulas="Unchanged canonical calculators; common-cohort parity is checked separately from full-cohort completeness.",
                    git_head=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip())
    try:
        for size in sizes:
            database = "dashboard_financial_benchmark_" + uuid.uuid4().hex
            try:
                await seed(client[database], size)
                rows = []
                for mode in ("before", "after_common", "after_full"):
                    proc = await asyncio.create_subprocess_exec(sys.executable, __file__, "--worker", mode, database, str(size),
                        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                    stdout, stderr = await proc.communicate()
                    if proc.returncode:
                        raise RuntimeError(stderr.decode())
                    row = json.loads(stdout.decode().strip())
                    rows.append(row)
                    evidence["results"].append(row)
                assert rows[0]["totals"] == rows[1]["totals"]
                full = rows[2]
                assert full["totals"]["recurring"]["total"] == 1.0
                assert sum(bucket["count"] for bucket in full["totals"]["settlements"].values()) == size
                assert full["mongo_documents"][INVOICES] == size
                assert full["mongo_documents"]["payment_adjustments"] == size
                assert full["max_mongo_wire_batch"] <= 128
                evidence["comparisons"].append(dict(size=size, common_cohort_exact_parity=True,
                    full_cohort_complete=True, full_cohort_recurring_total=1.0,
                    legacy_recurring_total=rows[0]["totals"]["recurring"]["total"],
                    legacy_adjustments=min(size, 50000), full_adjustments=size))
                Path(output).parent.mkdir(parents=True, exist_ok=True)
                Path(output).write_text(json.dumps(evidence, indent=2), encoding="utf-8")
                print(json.dumps(evidence["comparisons"][-1]), flush=True)
            finally:
                await client.drop_database(database)
    finally:
        client.close()


if __name__ == "__main__":
    if sys.argv[1] == "--worker":
        asyncio.run(worker(sys.argv[2], sys.argv[3], int(sys.argv[4])))
    else:
        asyncio.run(main(sys.argv[1], [int(value) for value in sys.argv[2:]] or [10000, 50000, 100000]))
