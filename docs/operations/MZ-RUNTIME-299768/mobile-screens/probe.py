"""Three native AMASI screen reads using live role grants in a disposable local database."""
import asyncio
import json
from contextlib import nullcontext
from pathlib import Path
import socket
import subprocess
import sys
import tempfile

import httpx
from fastapi import FastAPI, Request
from motor.motor_asyncio import AsyncIOMotorClient
import benchmark_dashboard_contention as bench
from mobile_app_permissions import MOBILE_APP_ACCESS
from mobile_app_request_context import mobile_app_request_user
from order_review_routes import WORKFLOWS, make_order_review_router
from reviewed_products_catalog import make_reviewed_products_catalog_router
from preparation_piece_operations import PIECES, make_preparation_piece_operations_router
from test_mobile_review_queue_contract import order

SCREENS = {
    "pending": ("app.page.pending_review", "/api/order-reviews-v1/pages?page=1&limit=10"),
    "reviewed": ("app.page.reviewed_preparation", "/api/reviewed-products-v1/catalog?limit=2000"),
    "assembly": ("app.page.assembly_shipping", "/api/preparation-work-v1/assembly/orders?state=in_progress&limit=20&offset=0"),
}
OWNER = "queue-owner"

async def prepare(db):
    await db.users.insert_one({"id": OWNER, "role": "owner"})
    for actor, (grant, _) in SCREENS.items():
        await db.users.insert_one({"id": actor, "created_by": OWNER, "role": "employee"})
        await db[MOBILE_APP_ACCESS].insert_one({"owner_user_id": OWNER, "user_id": actor, "enabled": True,
                                                "permissions": [grant]})
    await db.users.insert_one({"id": "blocked", "created_by": OWNER, "role": "employee"})
    await db.unified_orders.insert_many([order("101"), order("202", current="reviewed"),
        order("303", current="in_progress", native="قيد التنفيذ"), order("999", owner="other-owner")])
    await db[WORKFLOWS].insert_many([
        {"user_id": OWNER, "order_number": "202", "stage": "reviewed", "revision": 2, "items": [{"order_item_id": "salla:202:item-202", "product_id": "p-1", "product_name": "Synthetic product", "quantity": 1}]},
        {"user_id": OWNER, "order_number": "303", "stage": "in_progress", "preparation_piece_count": 1},
        {"user_id": "other-owner", "order_number": "999", "stage": "reviewed"}])
    await db[PIECES].insert_one({"user_id": OWNER, "order_number": "303", "piece_id": "303-1",
        "order_item_id": "item-303", "status": "received", "assembly_status": "waiting"})

def make_app(db):
    async def actor(request: Request):
        user = await db.users.find_one({"id": request.headers["x-fixture-employee"]}, {"_id": 0})
        return await mobile_app_request_user(db, {**user, "_session_client": "amasi_mobile"},
                                             path=request.url.path, method=request.method)
    app = FastAPI()
    for make in (make_order_review_router, make_reviewed_products_catalog_router, make_preparation_piece_operations_router):
        app.include_router(make(db, actor), prefix="/api")
    return app

async def check(http, key):
    response = await http.get(SCREENS[key][1], headers={"x-fixture-employee": key})
    assert response.status_code == 200, (key, response.status_code, response.text[:300])
    body = response.json()
    if key == "pending":
        assert [r["order_number"] for r in body["items"]] == ["101"], body
        assert body["total_count"] == 1 and body["page"] == 1, body
    elif key == "reviewed":
        assert len(body["products"]) == 1 and body["products"][0]["source_order_numbers"] == ["202"], body
        assert body["products"][0]["remaining_quantity"] == 1, body["products"]
    else:
        assert body["state"] == "in_progress" and body["total"] == 1, body
        row, = body["items"]
        assert (row["order_number"], row["remaining_count"], row["total_count"], row["mezan_preparation"]) == ("303", 1, 1, True), row

async def phase(port, client, arm, i):
    db = client[f"native_counterbalanced_{i}"]
    assert await db.list_collection_names() == []
    count, orders, interval, spacing = 20, 350, .08, .16
    await bench.seed(db, 1, orders)
    await prepare(db)
    rows = await bench.cost_rows(db, orders)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=make_app(db)), base_url="http://fixture") as http:
        for key, (_, path) in SCREENS.items():
            denied = await http.get(path, headers={"x-fixture-employee": "blocked"})
            assert denied.status_code == 403, (key, denied.status_code, denied.text[:180])
            await check(http, key)
        await bench.cost_job(db, rows)
        ctx = bench.supervised_child(port, db.name, orders) if arm == "isolated" else nullcontext()
        async with ctx as child:
            loop = asyncio.get_running_loop()
            start = loop.time() + .05
            jobs = 10
            schedule = (start, jobs, spacing, start + count * interval)
            async def load():
                if child:
                    child.send(schedule)
                    return await bench.receive_child(child, "done", 75)
                return await bench.fixed_jobs(db, rows, *schedule)
            async def read(key, i, j):
                due = start + interval * (i + j / len(SCREENS))
                await asyncio.sleep(max(0, due - loop.time()))
                await check(http, key)
                return key, (loop.time() - due) * 1000
            dashboard, *requests = await asyncio.gather(load(), *(read(key, i, j)
                for i in range(count) for j, key in enumerate(SCREENS)))
            assert dashboard["validated"] == jobs, dashboard
        return {"arm": arm, "validated_dashboard_jobs": jobs, "probes": {
            key: bench.stats([ms for name, ms in requests if name == key]) for key in SCREENS}}

async def run(port, sha):
    bench.limit_network(port)
    client = AsyncIOMotorClient(f"mongodb://127.0.0.1:{port}/?directConnection=true", serverSelectionTimeoutMS=8000)
    try:
        blocks = [await phase(port, client, arm, i) for i, arm in enumerate(["shared", "isolated", "isolated", "shared"])]
        print(json.dumps({"source_sha": sha, "native_build_sha": "eea242896f3001234b30cf6d552b0e3f394af02a",
              "mongo_version": (await client.server_info())["version"], "screen_paths": SCREENS,
              "expected_fixture": {"pending_queue": ["101"], "reviewed_catalog_remaining_units_for_202": 1,
                                   "assembly_in_progress_order": "303", "ungranted_employee_status": 403},
              "blocks": blocks}, indent=2))
    finally:
        client.close()

def local_run(experiment):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix="native-mobile-") as tmp:
        with subprocess.Popen([sys.argv[1], "--dbpath", tmp, "--bind_ip", "127.0.0.1", "--port", str(port),
                "--nounixsocket", "--wiredTigerCacheSizeGB", "0.25", "--logpath", str(Path(tmp)/"mongo.log")],
                stdout=subprocess.DEVNULL) as proc:
            try:
                asyncio.run(experiment(port, sys.argv[2]))
            finally:
                proc.terminate()


if __name__ == "__main__":
    local_run(run)
