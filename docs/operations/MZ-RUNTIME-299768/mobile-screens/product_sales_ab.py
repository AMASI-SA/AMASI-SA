"""Compare the actual product-sales GET on a Git revision and the working tree."""
import argparse
import asyncio
import hashlib
import json
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from types import ModuleType

from bson import BSON
from fastapi import APIRouter, FastAPI
import httpx
from motor.motor_asyncio import AsyncIOMotorClient

ROOT = Path(__file__).resolve().parents[4]
sys.path[:0] = [str(ROOT / "backend"), str(ROOT / "scripts")]
from benchmark_dashboard_contention import limit_network

BASE = "939d4a8f7cfce9ca0c93a99cc9d2595490f66b34"
ROUTE = "/api/product-costs/product-sales"
DAY = "2026-10-01"


class ReadFixture:
    """Expose only find, and observe whether a ready callback runs during one large order."""
    def __init__(self, raw):
        self.raw = raw
        self.finds = {}
        self.observe = False
        self.turns = []

    def __getattr__(self, collection):
        fixture = self

        class Collection:
            def find(self, query, projection):
                fixture.finds[collection] = (query, projection)

                async def stream():
                    async for row in fixture.raw[collection].find(query, projection):
                        if not fixture.observe or collection != "unified_orders" or len(row.get("products") or []) < 2000:
                            yield row
                            continue
                        marker = asyncio.Event()
                        asyncio.get_running_loop().call_soon(marker.set)
                        started = time.perf_counter()
                        yield row
                        fixture.turns.append({"callback_ran": marker.is_set(), "document_ms": elapsed_ms(started)})
                return stream()
        return Collection()


def elapsed_ms(started):
    return round((time.perf_counter() - started) * 1000, 3)


def client_for(name, source, fixture):
    module = ModuleType(f"product_sales_{name}")
    sys.modules[module.__name__] = module
    exec(compile(source, f"{name}/backend/product_costs.py", "exec"), module.__dict__)

    async def user():
        return {"id": "fixture", "role": "owner"}

    router = APIRouter(prefix="/api")
    module.attach_product_costs_routes(router, fixture, user)
    app = FastAPI()
    app.include_router(router)
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    return httpx.AsyncClient(transport=transport, base_url="http://fixture")


async def seed(db):
    assert not await db.list_collection_names()
    base = {"user_id": "fixture", "order_date": DAY}
    padding = "f" * 768
    products = [
        {"sku": " X ", "id": "x", "name": "Alpha", "quantity": 2, "price": 10, "image": "raw.jpg"},
        {"id": 42, "name": "By ID", "quantity": 1, "price": 11, "image_url": "item.jpg"},
        {"name": "By name", "quantity": 1, "price": 7}, {"name": "Missing", "quantity": 1, "price": 10},
        {"sku": "MIX", "name": "Partial", "price": 3}, {"unrelated": True},
    ]
    costs = [
        {"sku": "x", "matched_by": "sku", "line_cost": 7},
        {"product_id": "42", "matched_by": "id", "line_cost": 4},
        {"name": "By name", "matched_by": "name", "line_cost": 3},
        {"sku": "MIX", "matched_by": "sku", "line_cost": 1},
    ]
    await db.product_costs.insert_many([
        {"user_id": "fixture", "is_active": True, "sku_normalized": "X", "product_id": "x",
         "image_url": "catalog.jpg", "currency": "QAR", "product_name": padding},
        {"user_id": "unlinked", "is_active": True, "sku_normalized": "X", "image_url": "wrong"},
    ])
    for i in range(151):
        repeats = 1200 if i == 150 else 2
        cost_lines = [{**r, "unit_cost": 10, "price_metadata": padding[:100]} for r in costs] * repeats
        if i % 2 == 0:
            cost_lines = [r for r in cost_lines if r.get("sku") != "MIX"]
        if i == 0:
            cost_lines.append({"junk": 1})  # Legacy blank-key match truth-tests this object.
        elif i == 1:
            cost_lines.append({})
        await db.unified_orders.insert_one({
            **base, "order_number": str(i), "profit_status": "unset", "cost_items": cost_lines,
            "products": [{**r, "options": [{"notes": padding}]} for r in products] * repeats,
            "missing_product_cost_lines": [{"extra": padding}] * 6,
        })
    await db.unified_orders.insert_many([
        {**base, "user_id": "unlinked", "products": [{"name": "not mine", "price": 12345}]},
        {**base, "products": None, "cost_items": None}, {**base, "products": [], "cost_items": []}, base,
        {**base, "order_date": "2026-10-02", "products": products, "cost_items": costs},
        {**base, "order_date": "2026-10-03", "products": [products[0], 17]},
        {**base, "order_date": "2026-10-04", "products": products, "cost_items": [17]},
    ])


async def read(http, day):
    return await http.get(ROUTE, params={"from_date": day, "to_date": day})


async def run(port, baseline):
    limit_network(port)
    mongo = AsyncIOMotorClient(f"mongodb://127.0.0.1:{port}/?directConnection=true", serverSelectionTimeoutMS=8000)
    db = mongo.synthetic_product_sales
    clients = {}
    try:
        await seed(db)
        sources = {
            "baseline": subprocess.check_output(["git", "show", f"{baseline}:backend/product_costs.py"], cwd=ROOT),
            "candidate": (ROOT / "backend/product_costs.py").read_bytes(),
        }
        report = {"baseline_revision": baseline, "mongo": (await mongo.server_info())["version"], "arms": {}}
        golden = {}
        for arm, source in sources.items():
            fixture = ReadFixture(db)
            http = client_for(arm, source, fixture)
            clients[arm] = http, fixture
            for day, status in [("2026-10-02", 200), ("2026-10-03", 500), ("2026-10-04", 500), (DAY, 200)]:
                res = await read(http, day)
                assert res.status_code == status, (arm, day, res.status_code, res.text[:300])
                assert golden.setdefault(day, res.content) == res.content
                if status == 500:
                    continue
                by_name = {row["name"]: row for row in res.json()["items"]}
                assert set(by_name) == {"Alpha", "By ID", "By name", "Missing", "Partial"}
                assert by_name["Missing"]["total_profit"] is None and by_name["Alpha"]["image_url"] == "catalog.jpg"
                assert by_name["Alpha"]["currency"] == "SAR" and by_name["By ID"]["image_url"] == "item.jpg"
                if day == "2026-10-02":
                    for key, value in [("Alpha", 13), ("By ID", 7), ("By name", 4), ("Partial", 2)]:
                        assert by_name[key]["total_profit"] == value
                else:
                    assert by_name["Partial"]["total_profit"] is None
                    assert res.json()["totals"] == {"total_sales_all": 76500, "total_sales_complete": 30000,
                        "total_cost_complete": 10500, "total_profit_complete": 19500, "margin_complete_pct": 65}
            size = 0
            for collection, (query, projection) in fixture.finds.items():
                async for doc in db[collection].find(query, projection):
                    size += len(BSON.encode(doc))
            report["arms"][arm] = {
                "source_sha256": hashlib.sha256(source).hexdigest(), "projected_bson_bytes": size,
                "response_sha256": hashlib.sha256(golden[DAY]).hexdigest(),
                "large_document": fixture.turns, "http_duration_ms": [],
            }
            fixture.observe = True

        for arm in ("baseline", "candidate", "candidate", "baseline",
                    "candidate", "baseline", "baseline", "candidate"):
            http, fixture = clients[arm]
            started = time.perf_counter()
            res = await read(http, DAY)
            duration = elapsed_ms(started)
            assert res.status_code == 200 and res.content == golden[DAY]
            report["arms"][arm]["http_duration_ms"].append(duration)
        assert report["arms"]["candidate"]["projected_bson_bytes"] < report["arms"]["baseline"]["projected_bson_bytes"]
        print(json.dumps(report, indent=2))
    finally:
        for http, _ in clients.values():
            await http.aclose()
        mongo.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mongod", required=True)
    parser.add_argument("--baseline", default=BASE)
    args = parser.parse_args()
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix="sales-ab-") as tmp:
        with subprocess.Popen([args.mongod, "--dbpath", tmp, "--bind_ip", "127.0.0.1", "--port", str(port),
                               "--nounixsocket", "--wiredTigerCacheSizeGB", "0.25", "--logpath", f"{tmp}/mongo.log"],
                              stdout=subprocess.DEVNULL) as proc:
            try:
                asyncio.run(run(port, args.baseline))
            finally:
                proc.terminate()
