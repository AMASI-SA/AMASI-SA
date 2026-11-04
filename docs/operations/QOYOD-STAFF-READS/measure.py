"""Measure an employee queue read overlapping a zero-dispatch Qoyod discovery pass."""
import argparse
import asyncio
import json
import math
from pathlib import Path
import socket
import statistics
import subprocess
import sys
import tempfile

import httpx
from motor.motor_asyncio import AsyncIOMotorClient

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--source", type=Path, required=True, help="Mezan checkout to exercise")
parser.add_argument("--mongod", required=True, help="local mongod executable")
args = parser.parse_args()
source = args.source.resolve()
sys.path[:0] = [str(source / path) for path in (
    "backend", "backend/tests", "scripts", "docs/operations/MZ-RUNTIME-299768/mobile-screens",
)]
import probe as mobile
from benchmark_dashboard_contention import limit_network
from integrations.qoyod_manual import auto_send


def p95(values):
    return sorted(values)[math.ceil(.95 * len(values)) - 1]


async def measure(port):
    limit_network(port)
    client = AsyncIOMotorClient(f"mongodb://127.0.0.1:{port}/?directConnection=true",
                                serverSelectionTimeoutMS=10000)
    try:
        db = client["staff_contention_fixture"]
        await mobile.prepare(db)
        total, held = 2000, 142
        refs = [str(600000000 + i) for i in range(total)]
        order = dict(user_id="fixture", order_date="2026-08-08", order_status="completed",
                     order_status_slug="completed", order_status_native="تم التنفيذ",
                     payment_method="mada", payment_status="paid", payment_collection_status="paid",
                     paid_amount=100, remaining_amount=0, total_amount=100, currency="SAR")
        await db.unified_orders.insert_many([dict(order, order_number=n) for n in refs])
        await db.qoyod_invoices.insert_many([dict(user_id="main", reference=n,
                qoyod_official_reference=n, reference_provenance="qoyod.reference",
                qoyod_invoice_id=f"fixture-{n}") for n in refs[held:]])
        await db.qoyod_manual_auto_quarantines.insert_many([dict(user_id="main", order_number=n,
                status="open", code="fixture_manual_review") for n in refs[:held]])
        await db.unified_orders.create_index([("user_id", 1), ("order_date", 1)])
        for key in ("qoyod_official_reference", "reference"):
            await db.qoyod_invoices.create_index([("user_id", 1), (key, 1)])

        loop = asyncio.get_running_loop()

        async def scan():
            start = loop.time()
            # The installed selector includes the real hold/retry logic, without invoking send.
            candidates, status = await auto_send._load_candidate_rows(db,
                settings={"invoice_trigger_statuses": ["completed", "in_delivery", "delivered"]},
                orders_user_id="fixture", batch_limit=5)
            assert not candidates and status["open_quarantined_candidate_count"] == held
            assert status["authoritative_backlog_count"] == held and status["runnable_candidate_count"] == 0
            return (loop.time() - start) * 1000

        staff_ms, scan_ms = [], []
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=mobile.make_app(db)),
                                     base_url="http://fixture") as http:
            await mobile.check(http, "pending")
            await scan()

            async def read_at(due):
                await asyncio.sleep(max(0, due - loop.time()))
                await mobile.check(http, "pending")
                return (loop.time() - due) * 1000

            for _ in range(8):
                start = loop.time()
                elapsed, *staff = await asyncio.gather(scan(), *(read_at(start + i * .008)
                                                       for i in range(1, 17)))
                scan_ms.append(elapsed)
                staff_ms.extend(staff)
                await asyncio.sleep(.05)
        assert await db.qoyod_invoices.count_documents({}) == total - held
        assert await db.qoyod_manual_auto_quarantines.count_documents({"status": "open"}) == held
        return {"staff_success": len(staff_ms), "staff_p95_ms": round(p95(staff_ms), 2),
                "scan_median_ms": round(statistics.median(scan_ms), 2),
                "staff_ms": [round(v, 3) for v in staff_ms], "scan_ms": [round(v, 3) for v in scan_ms]}
    finally:
        client.close()


if __name__ == "__main__":
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    with tempfile.TemporaryDirectory() as data:
        with subprocess.Popen([args.mongod, "--dbpath", data, "--bind_ip", "127.0.0.1", "--port", str(port),
                               "--nounixsocket", "--wiredTigerCacheSizeGB", "0.25", "--logpath", f"{data}/mongo.log"],
                              stdout=subprocess.DEVNULL) as server:
            try:
                print(json.dumps(asyncio.run(measure(port))))
            finally:
                server.terminate()
