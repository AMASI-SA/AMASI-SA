"""Provider-free ASGI benchmark. Start an isolated mongod, never an app lifespan.

Run from the repository root with backend requirements installed:
    python scripts/benchmark_dashboard_contention.py --mongod /path/to/mongod
"""
import argparse
import asyncio
from collections import Counter
import json
import hashlib
import math
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
from uuid import uuid4

import httpx
from fastapi import FastAPI, Request
from motor.motor_asyncio import AsyncIOMotorClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


def limit_network(port):
    def audit(event, args):
        if event == "socket.connect":
            _, address = args
            if not isinstance(address, tuple) or address[:2] != ("127.0.0.1", port):
                raise RuntimeError("Only the private benchmark Mongo socket is allowed")
    sys.addaudithook(audit)


OWNER = {"id": "synthetic-owner", "role": "owner"}
def employee(name):
    return {"id": name, "created_by": OWNER["id"], "role": "viewer"}


async def seed(db, count, orders):
    from ai_store_access_contract import ROLE_ASSIGNMENTS
    from auth import ensure_user_settings
    import dashboard_v2_routes as dashboard
    import preparation_supplier_dispatch as dispatch
    from salla_shipping import CURRENT_SHIPPING

    owner = OWNER["id"]
    await ensure_user_settings(db, owner)
    for name in ("reader", "writer"):
        await db[ROLE_ASSIGNMENTS].insert_one({"owner_user_id": owner, "user_id": name,
            "role_key": "preparation_operator", "enabled": True})
    await db[dispatch.MEZAN_SUPPLIERS_V2].insert_one({"user_id": owner, "id": "supplier",
        "company_name": "Synthetic supplier", "category_ids": ["cat"], "status": "active"})
    await db[dashboard.PRODUCTS].insert_one({"user_id": owner, "salla_product_id": "p",
        "name": "Synthetic product", "sku": "P", "cost_category_id": "cat"})
    await db[dashboard.COST_PROFILES].insert_one({"user_id": owner, "salla_product_id": "p", "base_cost": 9})
    await db.unified_orders.create_index([("user_id", 1), ("order_number", 1)])
    await db.unified_orders.create_index([("user_id", 1), ("order_date", 1)])
    raw = {"id": "order-a", "reference_id": "A", "status": {"slug": "reviewed", "name": "reviewed"},
           "date": {"date": "2024-01-01T00:00:00Z"}, "items": []}
    await db.unified_orders.insert_one({"user_id": owner, "order_number": "A", "order_status": "reviewed",
           "order_date": "2024-01-01", "raw_by_source": {"salla_direct": raw}})
    await db.unified_orders.insert_one({"user_id": owner, "order_number": "3001", "order_date": "2024-01-01",
        "raw_by_source": {"salla_direct": {"id": 123, "reference_id": "3001", "status": "completed",
            "date": {"date": "2024-01-01T00:00:00Z"}, "items": []}},
        "shipping_label_url": "https://labels.invalid/test.pdf", CURRENT_SHIPPING: {"company_name": "iMile",
            "shipment_id": "shipment", "status": "created", "tracking_number": "AWB",
            "label_url": "https://labels.invalid/test.pdf"}})
    await db[dispatch.WORKFLOWS].insert_one({"user_id": owner, "order_number": "3001",
        "stage": "completed", "assembly_status": "completed"})

    files, pieces = [], []
    for i in range(-1, count + 1):
        file = f"PF-{i}"
        files.append({"user_id": owner, "file_number": file, "batch_id": file,
                      "status": "ready", "execution_status": "assigned"})
        # Reader keeps one fixed 9-piece file (one warmup dispatch). Each writer request sends one of two pieces.
        for j in range(9 if i == -1 else 2):
            pieces.append({"user_id": owner, "piece_id": f"{i}-{j}", "group_key": "product:P",
                "order_number": "A", "order_item_id": "line-A", "unit_index": j + 1,
                "batch_id": file, "file_number": file, "responsible_employee_id": "reader" if i == -1 else "writer",
                "status": "assigned", "product_id": "p", "product_name": "Synthetic product", "sku": "P"})
    await db[dispatch.REGISTRY].insert_many(files)
    await db[dispatch.PIECES].insert_many(pieces)
    today = dashboard._today_riyadh().isoformat()
    lines = [{"order_item_id": f"line-{j}", "product_id": "p", "quantity": 1,
              "sku": "P", "total": 10} for j in range(4)]
    await db.unified_orders.insert_many([{"user_id": owner, "order_number": f"dash-{i}", "order_date": today,
        "currency": "SAR", "order_status": "completed", "total_amount": 40, "products": lines}
        for i in range(orders)])


def make_app(db):
    import dashboard_v2_routes as dashboard
    import fulfillment_v2_routes as fulfillment
    import preparation_supplier_dispatch as dispatch
    from release_identity import release_health_payload

    async def actor(request: Request):
        name = request.headers.get("x-test-actor", "owner")
        assert name in ("owner", "reader", "writer")
        return OWNER if name == "owner" else employee(name)

    def require_owner(user):
        assert user == OWNER

    async def forbidden(*args, **kwargs):
        raise AssertionError("Legacy dashboard is outside this isolated benchmark")

    app = FastAPI()
    app.include_router(dispatch.make_preparation_supplier_dispatch_router(db, actor), prefix="/api")
    app.include_router(fulfillment.make_fulfillment_v2_router(db, actor), prefix="/api")
    app.include_router(dashboard.make_dashboard_v2_router(db, actor, forbidden, require_owner), prefix="/api")

    @app.get("/bench/health")
    async def local_health():
        return release_health_payload()

    return app


def stats(samples):
    values = sorted(samples)
    def rank(p):
        return round(values[math.ceil(len(values) * p) - 1], 2) if values else None
    return {"n": len(values), "p95_ms": rank(.95), "p99_ms": rank(.99), "max_ms": rank(1)}


async def phase(db, count, orders, loaded, interval, deadline_ms, workload):
    import dashboard_v2_routes as dashboard_v2
    import preparation_supplier_dispatch as dispatch
    await seed(db, count, orders)
    app = make_app(db)
    timings = {kind: [] for kind in ("health_control", "workspace", "supplier_create", "saved_label")}
    failures = {kind: Counter() for kind in timings}
    dashboard_times, lag = [], []
    loop = asyncio.get_running_loop()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://local") as client:
        route = "/api/dashboard-v2/product-cost-summary"
        admission_reply = await client.get(route)
        admission = {"http_status": admission_reply.status_code}
        if admission_reply.status_code == 503:
            admission["code"] = admission_reply.json()["detail"]["code"]
        else:
            assert admission_reply.status_code == 200, admission_reply.text
            assert admission_reply.json()["month_total"] == orders * 4 * 9, admission_reply.text
        if workload == "route" and admission_reply.status_code != 200:
            raise RuntimeError(f"Governed dashboard was refused: {admission}. Use --workload summary-cost-stage for a stage-only experiment.")
        today = dashboard_v2._today_riyadh().isoformat()
        rows = await db.unified_orders.find({"user_id": OWNER["id"], "order_date": today}, {"_id": 0}).to_list(orders)
        assert len(rows) == orders

        async def dashboard():
            started = loop.time()
            if workload == "summary-cost-stage":
                value = await dashboard_v2.build_mezan_v2_product_cost_cooperative(db, OWNER["id"], rows)
                assert value["total"] == orders * 4 * 9, value["total"]
            else:
                reply = await client.get(route)
                assert reply.status_code == 200, reply.text
                assert reply.json()["month_total"] == orders * 4 * 9, reply.text
            return (loop.time() - started) * 1000

        async def send_piece(actor, file_index):
            return await client.post("/api/supplier-dispatch-v1/dispatches", headers={"x-test-actor": actor},
                json={"client_request_id": f"request-{file_index:05d}", "supplier_id": "supplier",
                      "file_number": f"PF-{file_index}", "selections": [{"group_key": f"piece:{file_index}-0", "quantity": 1}]})

        saved = await send_piece("reader", -1)
        assert saved.status_code == 201, saved.text
        saved_id = saved.json()["dispatch"]["id"]

        async def probe(kind, i):
            if kind == "workspace":
                reply = await client.get("/api/supplier-dispatch-v1/workspace?grain=piece",
                                         headers={"x-test-actor": "reader"})
                body = reply.json()
                expected = (reply.status_code == 200 and body["summary"]["waiting_review_pieces"] == 8
                            and any(item["id"] == saved_id and item["status"] == "sent"
                                    for account in body["supplier_accounts"] for item in account["dispatches"]))
            elif kind == "supplier_create":
                reply = await send_piece("writer", i)
                expected = reply.status_code == 201 and reply.json().get("dispatch", {}).get("status") == "sent"
            elif kind == "saved_label":
                reply = await client.post("/api/fulfillment-v2/completed/3001/carrier-label/refresh-saved")
                expected = reply.status_code == 200 and reply.json().get("label_url") == "https://labels.invalid/test.pdf"
            else:
                reply = await client.get("/bench/health")
                expected = reply.status_code == 200 and reply.json()["ok"]
            if not expected:
                raise ValueError(f"{kind}: {reply.status_code} {reply.text[:280]}")

        for kind in timings:
            await probe(kind, count)  # distinct warmup dispatch
        await dashboard()  # equal warm-cache conditions in both phases
        start = loop.time() + .05
        end = start + count * interval
        async def scheduled(kind, i, due):
            await asyncio.sleep(max(0, due - loop.time()))
            try:
                await probe(kind, i)
            except Exception as error:
                failures[kind][str(error)[:320]] += 1
            timings[kind].append((loop.time() - due) * 1000)

        async def load():
            await asyncio.sleep(max(0, start - loop.time()))
            while loaded and loop.time() < end:
                dashboard_times.append(await dashboard())

        async def heartbeat():
            # Each planned tick counts, even when several elapse during one loop stall.
            for i in range(math.ceil(count * interval / .010)):
                due = start + i * .010
                if due > loop.time():
                    await asyncio.sleep(due - loop.time())
                lag.append(max(0, (loop.time() - due) * 1000))

        await asyncio.gather(load(), heartbeat(), *(scheduled(kind, i, start + (i + k/4) * interval)
            for i in range(count) for k, kind in enumerate(timings)))
        confirmed = await db[dispatch.DISPATCHES].count_documents({"status": "sent"})
        events = await db[dispatch.DISPATCH_EVENTS].count_documents({"event_type": "preparation_pieces_sent_to_supplier"})
        successful = count + 2 - sum(failures["supplier_create"].values())
        assert confirmed == events == successful, (confirmed, events, successful)
        return {"probes": {kind: {**stats(times), "failures": dict(failures[kind]),
                    "over_deadline": sum(t > deadline_ms for t in times)} for kind, times in timings.items()},
                "loop_lag": stats(lag), "dashboard_jobs": stats(dashboard_times), "governed_route_preflight": admission,
                "verified_sent_dispatches_and_events_including_two_warmups": confirmed}


async def run(port, args):
    client = AsyncIOMotorClient(f"mongodb://127.0.0.1:{port}/?directConnection=true", serverSelectionTimeoutMS=10000)
    try:
        info = await client.server_info()
        report = {"mongo": info["version"], "python": sys.version.split()[0],
                  "samples_per_endpoint_per_phase": args.samples, "dashboard_orders": args.orders,
                  "lines_per_order": 4, "interval_per_endpoint_ms": args.interval_ms,
                  "deadline_ms": args.deadline_ms, "loop_tick_ms": 10, "workload": args.workload,
                  "dashboard_v2_routes_sha256": hashlib.sha256((ROOT / "backend/dashboard_v2_routes.py").read_bytes()).hexdigest(),
                  "phases": {}}
        for loaded in (False, True):
            db = client["synthetic_contention_" + uuid4().hex]
            report["phases"]["dashboard" if loaded else "idle"] = await phase(
                db, args.samples, args.orders, loaded, args.interval_ms / 1000, args.deadline_ms, args.workload)
        return report
    finally:
        client.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mongod", required=True)
    parser.add_argument("--workload", choices=("route", "summary-cost-stage"), default="route")
    parser.add_argument("--samples", type=int, default=100, choices=range(1, 201), metavar="1..200")
    parser.add_argument("--orders", type=int, default=350, choices=range(1, 1001), metavar="1..1000")
    parser.add_argument("--interval-ms", type=int, default=80, choices=range(25, 201), metavar="25..200")
    parser.add_argument("--deadline-ms", type=int, default=1000)
    args = parser.parse_args()
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    limit_network(port)
    with tempfile.TemporaryDirectory(prefix="mz-dashboard-") as tmp:
        with subprocess.Popen([args.mongod, "--dbpath", tmp, "--bind_ip", "127.0.0.1", "--port", str(port),
                               "--nounixsocket", "--wiredTigerCacheSizeGB", "0.25", "--logpath", str(Path(tmp)/"mongo.log")],
                              stdout=subprocess.DEVNULL) as mongo:
            try:
                result = asyncio.run(run(port, args))
            finally:
                mongo.terminate()
    print(json.dumps(result, indent=2, sort_keys=True))
    if any(probe["failures"] for p in result["phases"].values() for probe in p["probes"].values()):
        sys.exit(1)
