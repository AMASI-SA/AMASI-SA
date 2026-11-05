"""Controlled local A/B workload; never connects to a database or provider.

Run separately for the original and candidate source roots using the same
Python environment. Transport delay applies equally to single and batch order
queries. Mongomock does not model real Mongo execution, indexes or contention.
"""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
import socket
import statistics
import sys
import time


async def run(args):
    # No external database, provider, production action, or network socket.
    def forbidden(*_args, **_kwargs):
        raise RuntimeError("This benchmark permits in-process ASGI reads only")
    socket.socket.connect = forbidden
    socket.create_connection = forbidden
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend/tests"))
    sys.path.insert(0, str(Path(args.source_root).resolve() / "backend"))
    from mongomock_motor import AsyncMongoMockClient
    from test_fulfillment_read_performance import ReadCounter, seed_queue, queue_response
    from preparation_piece_operations import _assembly_order_board

    workloads = {}
    cases = (("ready", "ready_to_ship", args.orders),
                               ("delivery", "delivering", args.orders),
                               ("assembly", "in_progress", args.orders * 2 + 5))
    if args.workload == "purchases":
        cases = (("purchases", "", args.orders),)
    for name, stage, count in cases:
        raw = AsyncMongoMockClient()[name]
        delayed_collections = ("unified_orders",)
        if name == "purchases":
            import httpx
            from fastapi import FastAPI
            import purchase_invoices_routes as purchases
            from purchase_receiving_service import SCHEMA, OPERATIONS
            await raw.users.insert_one({"id": "owner", "role": "owner"})
            for index in range(count):
                await raw.purchase_invoices.insert_one({
                    "user_id": "owner", "id": f"invoice-{index}", "schema_version": SCHEMA,
                    "approval_operation_id": f"operation-{index}", "liability_id": f"liability-{index}",
                    "state": "approved", "total": 125.0,
                })
                await raw[OPERATIONS].insert_one({"_id": f"operation-{index}", "user_id": "owner", "state": "completed"})
                await raw.liabilities.insert_one({"user_id": "owner", "id": f"liability-{index}",
                                                  "expected_amount": 125.0, "paid_amount": 25.0, "status": "partial"})
            async def actor(*_args):
                return {"id": "owner", "role": "owner"}
            purchases.get_current_user_from_db = actor
            delayed_collections = (OPERATIONS, "liabilities")
        else:
            await seed_queue(raw, stage, count)
        db = ReadCounter(raw, delay=args.latency_ms / 1000, delay_collections=delayed_collections)
        async def request():
            if name == "purchases":
                app = FastAPI()
                purchases.attach_purchase_invoice_routes(app, db)
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://local.test") as client:
                    response = await client.get("/purchase-invoices", params={"limit": 2000})
                    response.raise_for_status()
                    return response.json()
            if name == "assembly":
                return await _assembly_order_board(db, user_id="owner", state="in_progress", limit=100, offset=0)
            path = "/fulfillment-v2/ready-to-ship" if name == "ready" else "/fulfillment-v2/delivery-tracking"
            return await queue_response(db, path, params={} if name == "ready" else {"stage": "delivering"})
        expected = await request()
        expected_hash = hashlib.sha256(json.dumps(expected, sort_keys=True, default=str).encode()).hexdigest()
        durations = []
        for _ in range(args.repeats):
            db.reads.clear()
            start = time.perf_counter()
            actual = await request()
            durations.append(round((time.perf_counter() - start) * 1000, 3))
            assert actual == expected, "Read-only workload changed its response"
        workloads[name] = {
            "synthetic_orders": count,
            "response_sha256": expected_hash,
            "median_ms": round(statistics.median(durations), 3),
            "max_ms": max(durations), "samples_ms": durations,
            "order_query_count": sum(value for (collection, _), value in db.reads.items() if collection == "unified_orders"),
            "modeled_query_count": sum(value for (collection, _), value in db.reads.items() if collection in delayed_collections),
        }
    return {
        "kind": "local_synthetic_read_only",
        "simulated_order_query_rtt_ms": args.latency_ms,
        "repeats_after_warmup": args.repeats,
        "limitations": "Mongomock and in-process ASGI; excludes deployed middleware, real Mongo, device, network, and production contention.",
        "workloads": workloads,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--orders", type=int, default=100)
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--latency-ms", type=float, default=8)
    parser.add_argument("--workload", choices=("fulfillment", "purchases"), default="fulfillment")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not 1 <= args.orders <= 300 or not 1 <= args.repeats <= 20 or not 0 <= args.latency_ms <= 50:
        parser.error("orders 1..300, repeats 1..20, latency-ms 0..50 required")
    result = asyncio.run(run(args))
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
