"""Provider-free ASGI benchmark. Start an isolated mongod, never an app lifespan.

Run from the repository root with backend requirements installed:
    python scripts/benchmark_dashboard_contention.py --mongod /path/to/mongod
"""
import argparse
import asyncio
from collections import Counter
from contextlib import asynccontextmanager
import json
import hashlib
import math
import multiprocessing
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
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


def make_app(db, *, order_read=False):
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
    if order_read:
        # The product-level wrapper attaches unrelated provider and finance routers.
        from order_engine import _original_make_order_engine_router
        app.include_router(_original_make_order_engine_router(db, actor), prefix="/api")
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


async def cost_rows(db, orders):
    from dashboard_v2_routes import _today_riyadh

    rows = await db.unified_orders.find({"user_id": OWNER["id"], "order_date": _today_riyadh().isoformat()},
                                       {"_id": 0}).sort("order_number", 1).to_list(orders)
    if len(rows) != orders:
        raise ValueError("Incomplete synthetic cost fixture")
    return rows


async def cost_job(db, rows):
    from dashboard_v2_routes import build_mezan_v2_product_cost_cooperative

    value = await build_mezan_v2_product_cost_cooperative(db, OWNER["id"], rows)
    if value["total"] != len(rows) * 4 * 9:
        raise ValueError("Incomplete synthetic dashboard total")


async def fixed_jobs(db, rows, start, count, spacing, end):
    # Fixed arrivals, one sequential consumer in either process. Late jobs queue and drain.
    durations, turnaround = [], []
    completed_in_window = 0
    for i in range(count):
        due = start + i * spacing
        await asyncio.sleep(max(0, due - time.monotonic()))
        began = time.monotonic()
        await cost_job(db, rows)
        finished = time.monotonic()
        durations.append((finished - began) * 1000)
        turnaround.append((finished - due) * 1000)
        completed_in_window += finished <= end
    return {"planned": count, "validated": len(durations), "completed_in_probe_window": completed_in_window,
            "total_per_job": len(rows) * 4 * 9, "service": stats(durations), "arrival_to_finish": stats(turnaround)}


def dashboard_child(pipe, port, database, orders):
    async def serve():
        client = AsyncIOMotorClient(f"mongodb://127.0.0.1:{port}/?directConnection=true",
                                    serverSelectionTimeoutMS=5000)
        try:
            rows = await cost_rows(client[database], orders)
            await cost_job(client[database], rows)
            pipe.send(("ready", None))
            if not await asyncio.to_thread(pipe.poll, 30):
                raise TimeoutError("Parent did not start the dashboard child")
            schedule = pipe.recv()
            async with asyncio.timeout(75):
                result = await fixed_jobs(client[database], rows, *schedule)
            pipe.send(("done", result))
        finally:
            client.close()

    try:
        limit_network(port)
        asyncio.run(serve())
    except Exception as exc:
        pipe.send(("error", f"{type(exc).__name__}: {exc}"))
        raise
    finally:
        pipe.close()


async def receive_child(pipe, expected, timeout):
    if not await asyncio.to_thread(pipe.poll, timeout):
        raise TimeoutError(f"Dashboard child timed out waiting for {expected}")
    state, payload = pipe.recv()
    if state != expected:
        raise RuntimeError(f"Expected {expected} from dashboard child, got {state}: {payload}")
    return payload


@asynccontextmanager
async def supervised_child(port, database, orders):
    # Spawn a fresh interpreter before timing. No inherited Mongo client, app lifespan or web server.
    context = multiprocessing.get_context("spawn")
    parent, worker = context.Pipe()
    child = context.Process(target=dashboard_child, args=(worker, port, database, orders), daemon=True)
    child.start()
    worker.close()
    try:
        await receive_child(parent, "ready", 30)
        yield parent
        await asyncio.to_thread(child.join, 5)
        if child.exitcode != 0:
            raise RuntimeError(f"Dashboard child did not exit successfully: {child.exitcode}")
    finally:
        parent.close()
        if child.is_alive():
            child.terminate()
            child.join(2)
        if child.is_alive():
            child.kill()
            child.join(2)
        child.close()


async def phase(db, count, orders, loaded, interval, deadline_ms, workload, *, arm=None, port=None, spacing=.16):
    import preparation_supplier_dispatch as dispatch
    await seed(db, count, orders)
    app = make_app(db, order_read=bool(arm))
    timings = {kind: [] for kind in ("health_control", "workspace", "supplier_create", "saved_label")}
    if arm:
        timings["order_read"] = []
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
        rows = await cost_rows(db, orders)

        async def dashboard():
            started = loop.time()
            if workload == "summary-cost-stage":
                await cost_job(db, rows)
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
            elif kind == "order_read":
                reply = await client.get("/api/orders-v2/A")
                expected = reply.status_code == 200 and reply.json()["order_number"] == "A"
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
        async def scheduled(kind, i, due):
            await asyncio.sleep(max(0, due - loop.time()))
            try:
                await probe(kind, i)
            except Exception as error:
                failures[kind][str(error)[:320]] += 1
            timings[kind].append((loop.time() - due) * 1000)

        async def load(start, end, worker):
            nonlocal fixed_result
            if arm in ("shared", "isolated"):
                jobs = math.ceil((count * interval) / spacing)
                schedule = (start, jobs, spacing, end)
                if worker is None:
                    fixed_result = await fixed_jobs(db, rows, *schedule)
                else:
                    worker.send(schedule)
                    fixed_result = await receive_child(worker, "done", 75)
                if fixed_result["validated"] != jobs:
                    raise RuntimeError("Dashboard arrival count mismatch")
                return
            await asyncio.sleep(max(0, start - time.monotonic()))
            while loaded and time.monotonic() < end:
                dashboard_times.append(await dashboard())

        async def heartbeat(start):
            # Each planned tick counts, even when several elapse during one loop stall.
            for i in range(math.ceil(count * interval / .010)):
                due = start + i * .010
                if due > loop.time():
                    await asyncio.sleep(due - loop.time())
                lag.append(max(0, (loop.time() - due) * 1000))

        fixed_result = None
        async def measure(worker=None):
            start = time.monotonic() + .15
            end = start + count * interval
            async with asyncio.timeout(count * interval + 30):
                await asyncio.gather(load(start, end, worker), heartbeat(start),
                    *(scheduled(kind, i, start + (i + k/len(timings)) * interval)
                      for i in range(count) for k, kind in enumerate(timings)))

        if arm == "isolated":
            async with supervised_child(port, db.name, orders) as worker:
                await measure(worker)
        else:
            await dashboard()  # each arm warms once on the process that will compute its workload
            await measure()
        confirmed = await db[dispatch.DISPATCHES].count_documents({"status": "sent"})
        events = await db[dispatch.DISPATCH_EVENTS].count_documents({"event_type": "preparation_pieces_sent_to_supplier"})
        successful = count + 2 - sum(failures["supplier_create"].values())
        assert confirmed == events == successful, (confirmed, events, successful)
        return {"probes": {kind: {**stats(times), "failures": dict(failures[kind]),
                    "over_deadline": sum(t > deadline_ms for t in times)} for kind, times in timings.items()},
                "loop_lag": stats(lag), "governed_route_preflight": admission,
                **({"fixed_dashboard_jobs": fixed_result} if arm else {"dashboard_jobs": stats(dashboard_times)}),
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
        if args.compare_isolation:
            report["dashboard_interval_ms"] = args.dashboard_interval_ms
            report["phase_order"] = ["1_idle", "1_shared", "1_isolated", "2_isolated", "2_shared", "2_idle"]
        for label in report.get("phase_order", ("idle", "dashboard")):
            db = client["synthetic_contention_" + uuid4().hex]
            arm = label.split("_")[1] if args.compare_isolation else None
            async with asyncio.timeout(110):
                report["phases"][label] = await phase(
                    db, args.samples, args.orders, label == "dashboard", args.interval_ms / 1000, args.deadline_ms,
                    args.workload, arm=arm, port=port, spacing=args.dashboard_interval_ms / 1000)
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
    parser.add_argument("--compare-isolation", action="store_true", help="Run two counterbalanced local process comparisons")
    parser.add_argument("--dashboard-interval-ms", type=int, default=160, choices=range(50, 1001), metavar="50..1000")
    args = parser.parse_args()
    if args.compare_isolation and args.workload != "summary-cost-stage":
        parser.error("--compare-isolation requires --workload summary-cost-stage")
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
                try:
                    mongo.wait(8)
                except subprocess.TimeoutExpired:
                    mongo.kill()
                    mongo.wait(3)
    print(json.dumps(result, indent=2, sort_keys=True))
    if any(probe["failures"] for p in result["phases"].values() for probe in p["probes"].values()):
        sys.exit(1)
