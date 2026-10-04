"""Isolated Real Mongo baseline/page benchmark, fresh process per measurement.

No Production configuration is loaded. URI is fixed to the dedicated local test
server; only a newly created, uniquely named database is ever removed.
"""
import asyncio
import ctypes
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / "backend")]
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import monitoring
from dashboard_abandoned_page import read_cart_page, DETAIL_FIELDS
from dashboard_v2_routes import select_abandoned_carts_for_period
from dashboard_read_coordinator import DashboardReadCoordinator

URI = "mongodb://127.0.0.1:27261"


def peak_rss():
    if os.name != "nt":
        import resource
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
    class Counters(ctypes.Structure):
        _fields_ = [("cb", ctypes.c_ulong), ("faults", ctypes.c_ulong),
                    *[(name, ctypes.c_size_t) for name in (
                        "peak_rss", "rss", "peak_paged", "paged", "peak_nonpaged",
                        "nonpaged", "pagefile", "peak_pagefile")]]
    counters = Counters()
    counters.cb = ctypes.sizeof(counters)
    kernel = ctypes.windll.kernel32
    kernel.GetCurrentProcess.restype = ctypes.c_void_p
    fn = ctypes.windll.psapi.GetProcessMemoryInfo
    fn.argtypes = [ctypes.c_void_p, ctypes.POINTER(Counters), ctypes.c_ulong]
    if not fn(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
        raise ctypes.WinError()
    return counters.peak_rss


class Reads(monitoring.CommandListener):
    def __init__(self):
        self.documents = self.full_documents = self.query_us = self.commands = 0

    def started(self, event):
        pass

    def succeeded(self, event):
        if event.command_name not in {"find", "getMore", "aggregate"}:
            return
        self.commands += 1
        self.query_us += event.duration_micros
        cursor = event.reply.get("cursor", {})
        batch = cursor.get("firstBatch", cursor.get("nextBatch", []))
        self.documents += len(batch)
        self.full_documents += sum("items" in doc for doc in batch)

    def failed(self, event):
        pass


async def worker(mode, name, concurrency):
    reads = Reads()
    client = AsyncIOMotorClient(URI, event_listeners=[reads])
    carts = client[name].carts
    async def one():
        if mode == "before":
            rows = await carts.find({"user_id": "owner"}, {"_id": 0, **{k: 1 for k in DETAIL_FIELDS}}).to_list(100000)
            rows, abandoned, recovered = select_abandoned_carts_for_period(rows, start="2026-08-15", end="2026-08-15")
            return {"items": rows, "abandoned_count": abandoned, "recovered_count": recovered}
        rows, abandoned, recovered, pagination = await read_cart_page(
            carts, "owner", start="2026-08-15", end="2026-08-15", limit=50)
        return {"items": rows, "abandoned_count": abandoned, "recovered_count": recovered, "pagination": pagination}
    coordinator = DashboardReadCoordinator()
    rss_before = peak_rss()
    started = time.perf_counter()
    result = await asyncio.gather(*(
        coordinator.run("same-owner-period", one) if mode == "after_shared" else one()
        for _ in range(concurrency)))
    query_wall_ms = round((time.perf_counter() - started) * 1000, 2)
    serialization_started = time.perf_counter()
    sizes = [len(json.dumps(payload).encode()) for payload in result]
    serialization_ms = round((time.perf_counter() - serialization_started) * 1000, 2)
    print(json.dumps({"mode": mode, "concurrency": concurrency,
        "query_wall_ms": query_wall_ms, "mongo_command_ms": round(reads.query_us / 1000, 2),
        "mongo_commands": reads.commands, "mongo_documents": reads.documents,
        "full_item_documents": reads.full_documents, "peak_rss_bytes": peak_rss(),
        "baseline_rss_bytes": rss_before, "json_ms": serialization_ms,
        "response_bytes": sizes, "returned_items": [len(r["items"]) for r in result],
        "period_counts": [r["abandoned_count"] for r in result]}))
    client.close()


async def seed(name, count):
    client = AsyncIOMotorClient(URI)
    carts = client[name].carts
    await carts.create_index("user_id")
    for start in range(0, count, 100):
        await carts.insert_many([{"_id": str(i).zfill(8), "user_id": "owner", "cart_id": str(i),
            "cart_created_at": "2026-08-15T08:00:00Z", "cart_updated_at": "2026-08-15T10:00:00Z",
            "purchased": False, "total": 10, "items": [{"product_id": "p", "name": "x" * 1024}]}
            for i in range(start, min(start + 100, count))])
    client.close()


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("seed", "before", "after", "after_shared", "cleanup"))
    parser.add_argument("database")
    parser.add_argument("count", type=int)
    parser.add_argument("--output")
    args = parser.parse_args()
    assert args.database.startswith("dashboard_memory_benchmark_")
    if args.mode == "seed":
        asyncio.run(seed(args.database, args.count))
        print(json.dumps({"seeded": args.count, "item_bytes": 1024}))
    elif args.mode == "cleanup":
        async def cleanup():
            client = AsyncIOMotorClient(URI)
            await client.drop_database(args.database)
            client.close()
        asyncio.run(cleanup())
    elif args.output:
        # Fresh process for each before/after/concurrency sample.
        output = subprocess.check_output([sys.executable, __file__, args.mode,
                                          args.database, str(args.count)], text=True)
        data = json.loads(output)
        Path(args.output).write_text(json.dumps(data, indent=2), encoding="utf-8")
        print(output, end="")
    else:
        asyncio.run(worker(args.mode, args.database, args.count))
