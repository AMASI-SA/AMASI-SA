"""Full dashboard summary benchmark against isolated Mongo, fresh workers.

Examples (large runs must be explicitly requested):
  python benchmark_dashboard_summary_memory.py matrix --counts 100 --concurrency 1
  python benchmark_dashboard_summary_memory.py matrix --counts 10000 50000 100000 --concurrency 1 2 4

Workers execute the actual V2 route including admission/coalescing decorators,
with the actual legacy function extracted without importing server/bootstrap.
Before freezes server.py and dashboard_v2_routes.py at BASE_SHA; their imported
calculator dependencies remain the checkout's unchanged implementations.
"""
from __future__ import annotations

import argparse
import asyncio
import ctypes
from datetime import date
import functools
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import types
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "backend"), str(ROOT / "backend/tests")]
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import UpdateOne, monitoring
from fastapi.encoders import jsonable_encoder
from dashboard_summary_fixture import BASE_SHA, extract_dashboard, seed_dashboard

URI = "mongodb://127.0.0.1:27261"
DB_PREFIX = "dashboard_summary_benchmark_"


def rss():
    if os.name != "nt":
        import resource
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        peak *= 1 if sys.platform == "darwin" else 1024
        try:
            current = int(Path("/proc/self/statm").read_text().split()[1]) * os.sysconf("SC_PAGE_SIZE")
        except (OSError, IndexError):
            current = peak
        return current, peak
    class Counters(ctypes.Structure):
        _fields_ = [("cb", ctypes.c_ulong), ("faults", ctypes.c_ulong),
                    *[(name, ctypes.c_size_t) for name in (
                        "peak_rss", "rss", "peak_paged", "paged", "peak_nonpaged",
                        "nonpaged", "pagefile", "peak_pagefile")]]
    counters = Counters()
    counters.cb = ctypes.sizeof(counters)
    kernel = ctypes.windll.kernel32
    kernel.GetCurrentProcess.restype = ctypes.c_void_p
    function = ctypes.windll.psapi.GetProcessMemoryInfo
    function.argtypes = [ctypes.c_void_p, ctypes.POINTER(Counters), ctypes.c_ulong]
    if not function(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
        raise ctypes.WinError()
    return counters.rss, counters.peak_rss


class Reads(monitoring.CommandListener):
    def __init__(self):
        self.documents = self.commands = self.query_us = 0
        self.collections = {}
        self.failures = []

    def started(self, event):
        pass

    def succeeded(self, event):
        if event.command_name not in {"find", "getMore", "aggregate"}:
            return
        cursor = event.reply.get("cursor", {})
        count = len(cursor.get("firstBatch", cursor.get("nextBatch", [])))
        namespace = cursor.get("ns", "unknown").split(".", 1)[-1]
        self.collections[namespace] = self.collections.get(namespace, 0) + count
        self.documents += count
        self.commands += 1
        self.query_us += event.duration_micros

    def failed(self, event):
        self.failures.append(event.command_name)


class SpillProbe:
    """Observe only directories created by this worker; no extra payload cache."""
    def __init__(self):
        self.paths = set()
        self.highwater = self.stores = 0
        self.lock = threading.Lock()
        self.stopped = threading.Event()

    def sample(self):
        with self.lock:
            total = 0
            for path in self.paths:
                try:
                    total += sum(entry.stat().st_size for entry in path.iterdir() if entry.is_file())
                except (FileNotFoundError, PermissionError):
                    pass
            self.highwater = max(self.highwater, total)

    def install(self):
        from dashboard_spill import DashboardSpill
        self.original_init, self.original_close = DashboardSpill.__init__, DashboardSpill.close
        probe = self
        @functools.wraps(self.original_init)
        def initialize(store, *args, **kwargs):
            probe.original_init(store, *args, **kwargs)
            with probe.lock:
                probe.paths.add(store.directory)
                probe.stores += 1
        @functools.wraps(self.original_close)
        def close(store, *args, **kwargs):
            probe.sample()
            return probe.original_close(store, *args, **kwargs)
        DashboardSpill.__init__, DashboardSpill.close = initialize, close
        def run():
            while not self.stopped.wait(0.05):
                self.sample()
        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()

    def finish(self):
        from dashboard_spill import DashboardSpill
        self.stopped.set()
        self.thread.join()
        self.sample()
        DashboardSpill.__init__, DashboardSpill.close = self.original_init, self.original_close
        return {"spill_peak_bytes_observed": self.highwater, "spill_stores": self.stores,
                "spill_cleanup_complete": all(not path.exists() for path in self.paths),
                "spill_sampling_seconds": 0.05}


def v2_endpoint(db, mode):
    revision = BASE_SHA if mode == "before" else None
    if revision:
        source = subprocess.run(["git", "show", f"{revision}:backend/dashboard_v2_routes.py"], cwd=ROOT,
                                capture_output=True, text=True, encoding="utf-8", check=True).stdout
    else:
        source = (ROOT / "backend/dashboard_v2_routes.py").read_text(encoding="utf-8")
    name = "_dashboard_v2_benchmark_" + uuid.uuid4().hex
    module = types.ModuleType(name)
    sys.modules[name] = module
    try:
        exec(compile(source, f"dashboard-v2:{revision or 'working'}", "exec"), module.__dict__)
        module._today_riyadh = lambda: date(2026, 9, 30)
        async def current_user():
            return {"id": "owner", "role": "owner"}
        router = module.make_dashboard_v2_router(db, current_user, extract_dashboard(db, revision=revision), lambda user: None)
        return next(route.endpoint for route in router.routes if route.path == "/dashboard-v2")
    finally:
        sys.modules.pop(name, None)


def signature(value):
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(encoded).hexdigest()


async def worker(mode, database, concurrency):
    if not database.startswith(DB_PREFIX):
        raise ValueError("benchmark database prefix required")
    reads = Reads()
    client = AsyncIOMotorClient(URI, event_listeners=[reads], serverSelectionTimeoutMS=3000)
    await client.admin.command("ping")
    db = client[database]
    endpoint = v2_endpoint(db, mode)
    probe = SpillProbe()
    probe.install()
    baseline_rss, _ = rss()
    started = time.perf_counter()
    async def one():
        request_start = time.perf_counter()
        try:
            response = await endpoint(user={"id": "owner", "role": "owner"}, from_date="2026-09-01", to_date="2026-09-30",
                                      payment_methods=None, shipping_companies=None)
            endpoint_seconds = time.perf_counter() - request_start
            encode_start = time.perf_counter()
            safe = jsonable_encoder(response)
            wire = json.dumps(safe, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
            encode_seconds = time.perf_counter() - encode_start
            # Full signatures expose intentional response-contract differences;
            # financial signatures independently compare executive calculations.
            finances = {key: safe.get(key) for key in ("totals", "monthly", "payment_breakdown", "shipping_breakdown",
                                                       "source_breakdown", "month_kpis", "currency_conversion", "net_sales_config")}
            return {"ok": True, "endpoint_seconds": endpoint_seconds, "json_seconds": encode_seconds,
                    "response_bytes": len(wire), "full_signature": signature(safe),
                    "financial_signature": signature(finances), "totals": safe.get("totals"),
                    "product_rows_returned": len((safe.get("product_cost_v2") or {}).get("product_rows") or [])}
        except Exception as exc:
            return {"ok": False, "endpoint_seconds": time.perf_counter() - request_start,
                    "error_type": type(exc).__name__, "status_code": getattr(exc, "status_code", None),
                    "error": str(getattr(exc, "detail", exc))[:500]}
    try:
        results = await asyncio.gather(*(one() for _ in range(concurrency)))
        wall = time.perf_counter() - started
        current_rss, peak = rss()
        return {"mode": mode, "database": database, "concurrency": concurrency, "wall_seconds": wall,
                "rss_baseline_bytes": baseline_rss, "rss_current_bytes": current_rss, "rss_peak_bytes": peak,
                "mongo_wire_documents": reads.documents, "mongo_wire_documents_by_collection": reads.collections,
                "mongo_read_commands": reads.commands, "mongo_query_seconds": reads.query_us / 1e6,
                "mongo_failed_commands": reads.failures, "requests": results,
                "admission": "actual decorators; default governor preserved; identical request keys",
                "sample_scope": "complete V2 summary plus JSON encoding; local seeded data; not product-details endpoint",
                **probe.finish()}
    finally:
        if not probe.stopped.is_set():
            probe.finish()
        client.close()


async def seed(database, count, products, item_bytes):
    assert database.startswith(DB_PREFIX)
    client = AsyncIOMotorClient(URI, serverSelectionTimeoutMS=3000)
    try:
        db = client[database]
        if await db.list_collection_names():
            raise ValueError("benchmark seed requires an empty uniquely named database")
        await seed_dashboard(db, count=count)
        await db.unified_orders.create_index([("user_id", 1), ("order_number", 1)], unique=True)
        await db.unified_orders.create_index([("user_id", 1), ("order_date", -1)])
        products = max(1, min(products, max(count, 1)))
        for offset in range(0, products, 128):
            await db.mezan_products_v2.insert_many([{"user_id": "owner", "id": f"p{i}", "salla_product_id": f"p{i}",
                "name": f"Product {i}", "sku": f"SKU{i}", "cost_price_from_salla": 21.5,
                "raw_salla_details": {"cost_price": 21.5, "description": "x" * item_bytes}}
                for i in range(offset, min(offset + 128, products))])
        for offset in range(0, count, 128):
            updates = [UpdateOne({"user_id": "owner", "order_number": str(i)}, {"$set": {"products": [{
                "product_id": f"p{i % products}", "name": f"Product {i % products}", "quantity": 1,
                "price": 50, "description": "x" * item_bytes}],
                "currency": "BHD" if i % 4 == 0 else "SAR",
                "raw_by_source.salla_direct.currency": "BHD" if i % 4 == 0 else "SAR",
                "raw_by_source.salla_direct.exchange_rate": {"rate": "9.97", "base_currency": "SAR", "exchange_currency": "BHD"},
            }}) for i in range(offset, min(offset + 128, count))]
            await db.unified_orders.bulk_write(updates)
        return {"orders": count, "products": products, "item_padding_bytes": item_bytes}
    finally:
        client.close()


async def cleanup(database):
    assert database.startswith(DB_PREFIX)
    client = AsyncIOMotorClient(URI)
    try:
        await client.drop_database(database)
    finally:
        client.close()


def child(*arguments):
    result = subprocess.run([sys.executable, __file__, *map(str, arguments)], cwd=ROOT,
                            capture_output=True, text=True, encoding="utf-8", check=True,
                            env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    return json.loads(result.stdout)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("matrix", "seed", "before", "after", "cleanup"))
    parser.add_argument("--database")
    parser.add_argument("--counts", nargs="+", type=int, default=[10000, 50000, 100000])
    parser.add_argument("--concurrency", nargs="+", type=int, default=[1, 2, 4])
    parser.add_argument("--products", type=int, default=1000)
    parser.add_argument("--item-bytes", type=int, default=1024)
    args = parser.parse_args()
    if any(count < 0 for count in args.counts) or any(n < 1 for n in args.concurrency):
        parser.error("counts must be nonnegative and concurrency positive")
    if args.mode == "matrix":
        samples = []
        for count in args.counts:
            database = DB_PREFIX + uuid.uuid4().hex
            try:
                fixture = child("seed", "--database", database, "--counts", count, "--products", args.products, "--item-bytes", args.item_bytes)
                for concurrency in args.concurrency:
                    before = child("before", "--database", database, "--concurrency", concurrency)
                    after = child("after", "--database", database, "--concurrency", concurrency)
                    before_signatures = {r.get("financial_signature") for r in before["requests"] if r["ok"]}
                    after_signatures = {r.get("financial_signature") for r in after["requests"] if r["ok"]}
                    all_succeeded = all(request["ok"] for sample in (before, after) for request in sample["requests"])
                    samples.append({"fixture": fixture, "before": before, "after": after,
                                    "all_requests_succeeded": all_succeeded,
                                    "financial_signatures_equal": all_succeeded and bool(before_signatures) and before_signatures == after_signatures,
                                    "full_signatures_equal": all_succeeded and {r["full_signature"] for r in before["requests"]} == {r["full_signature"] for r in after["requests"]}})
            finally:
                child("cleanup", "--database", database)
        print(json.dumps({"base_sha": BASE_SHA, "samples": samples}, ensure_ascii=False))
    else:
        if not args.database or not args.database.startswith(DB_PREFIX):
            parser.error("a dedicated benchmark database name is required")
        if args.mode == "seed":
            result = asyncio.run(seed(args.database, args.counts[0], args.products, args.item_bytes))
        elif args.mode == "cleanup":
            asyncio.run(cleanup(args.database))
            result = {"cleaned": args.database}
        else:
            result = asyncio.run(worker(args.mode, args.database, args.concurrency[0]))
        print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
