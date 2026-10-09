"""Isolated synthetic A/B benchmark. No production URI, credentials or provider IO."""
import argparse
import asyncio
from collections import Counter
from copy import deepcopy
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import time
from unittest.mock import patch


def percentile(rows, fraction):
    rows = sorted(rows)
    if not rows:
        return 0
    pos = (len(rows) - 1) * fraction
    lo = int(pos)
    return rows[lo] + (rows[min(lo + 1, len(rows) - 1)] - rows[lo]) * (pos - lo)


def summary(rows):
    return {"p50": percentile(rows, .50), "p95": percentile(rows, .95),
            "p99": percentile(rows, .99), "max": max(rows, default=0)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--checkout", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--samples", type=int, default=80)
    parser.add_argument("--provider-latency-ms", type=float, default=50)
    args = parser.parse_args()
    root = Path(args.checkout).resolve()
    assert (root / "backend/order_review_completion.py").is_file()
    uri = os.environ.get("MZ2_TEST_MONGO_URI", "")
    assert uri.startswith("mongodb://127.0.0.1:"), "Explicit loopback replica set required"
    os.environ["PYTHON_DOTENV_DISABLED"] = "1"
    sys.path[:0] = [str(root / "backend"), str(root / "backend/tests")]
    import test_g47_component_lifecycle_integration as fixture
    import order_review_routes as routes
    import order_review_completion as completion
    import reviewed_products_catalog as catalog
    from motor.motor_asyncio import AsyncIOMotorClient
    from pymongo.monitoring import CommandListener

    class Commands(CommandListener):
        def __init__(self):
            self.enabled = False
            self.reset()
        def reset(self):
            self.counts = Counter()
            self.writes = Counter()
            self.starts = {}
            self.transactions = []
            self.finishing = {}
        def started(self, event):
            if not self.enabled:
                return
            self.counts[event.command_name] += 1
            if event.command_name in {"insert", "update", "delete", "findAndModify"}:
                self.writes[str(event.command.get(event.command_name))] += 1
            if event.command.get("startTransaction"):
                self.starts[str(event.command.get("lsid"))] = time.perf_counter()
            if event.command_name in {"commitTransaction", "abortTransaction"}:
                started = self.starts.pop(str(event.command.get("lsid")), None)
                if started is not None:
                    self.finishing[event.request_id] = started
        def succeeded(self, event):
            started = self.finishing.pop(event.request_id, None)
            if started is not None:
                self.transactions.append((time.perf_counter() - started) * 1000)
        def failed(self, event):
            self.finishing.pop(event.request_id, None)

    async def run():
        listener = Commands()
        rows = []
        version = None
        def client(*pos, **kw):
            return AsyncIOMotorClient(*pos, **kw, event_listeners=[listener])
        for index in range(args.samples + 3):
            case = fixture.ComponentRouteTests("runTest")
            with patch.object(fixture, "AsyncIOMotorClient", client):
                await case.asyncSetUp()
            try:
                version = (await case.db.command("buildInfo"))["version"]
                assert version == "8.0.12", version
                assert (await case.db.command("hello")).get("setName")
                payload = case.source_payload(number="benchmark-order")
                assert (await case.webhook(payload))["synced"]
                async def actor():
                    return case.actor
                case.app.include_router(catalog.make_reviewed_products_catalog_router(case.db, actor))
                calls = []
                visible = False
                async def transport(db, owner, method, path, **kwargs):
                    nonlocal visible
                    calls.append(method + " " + path)
                    await asyncio.sleep(args.provider_latency_ms / 1000)
                    if path.startswith("/products"):
                        return {"data": [] if path == "/products" else {"id": "p", "images": []}}
                    if path == "/orders/items":
                        return {"data": deepcopy(payload["items"])}
                    if path == "/orders/statuses":
                        return {"data": [{"id": 12, "name": "تمت المراجعة"}]}
                    if method == "POST":
                        assert path == "/orders/benchmark-order/status"
                        assert kwargs["json"] == {"status_id": 12}
                        visible = True
                        return {"success": True}
                    assert path == "/orders/benchmark-order", path
                    data = deepcopy(payload)
                    if visible:
                        data["status"]["customized"] = {"name": "تمت المراجعة"}
                    return {"data": data}
                lag = []
                running = True
                async def probe():
                    target = time.perf_counter() + .005
                    while running:
                        await asyncio.sleep(max(0, target - time.perf_counter()))
                        now = time.perf_counter()
                        lag.append(max(0, now - target) * 1000)
                        target = now + .005
                probe_task = asyncio.create_task(probe())
                listener.reset()
                listener.enabled = True
                start = time.perf_counter()
                cpu = time.process_time()
                with patch.object(routes, "call_salla", transport):
                    response = await case.client.post("/order-reviews-v1/benchmark-order/complete",
                                                      json={"expected_revision": 0})
                elapsed = (time.perf_counter() - start) * 1000
                cpu_ms = (time.process_time() - cpu) * 1000
                listener.enabled = False
                running = False
                await probe_task
                assert response.status_code == 200, response.text
                result = response.json()
                assert await case.db[completion.OPERATIONS].count_documents({"state": "completed"}) == 1
                assert await case.db[completion.EVENTS].count_documents({"event_type": "order_review_completed"}) == 1
                products = await case.client.get("/reviewed-products-v1/catalog")
                assert products.status_code == 200 and products.json()["products"], products.text
                visibility_ms = (time.perf_counter() - start) * 1000
                if index >= 3:
                    rows.append({"http_ms": elapsed, "cpu_ms": cpu_ms,
                        "visible_products_ms": visibility_ms, "salla_calls": len(calls),
                        "salla_post_calls": sum(c.startswith("POST ") for c in calls),
                        "db_commands": dict(listener.counts), "write_collections": dict(listener.writes),
                        "transaction_ms": list(listener.transactions), "event_loop_lag_ms": summary(lag),
                        "completion_mode": result.get("completion_mode", "legacy_provider")})
            finally:
                listener.enabled = False
                await case.asyncTearDown()
        result = {
            "head": subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip(),
            "tree": subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD^{tree}"], text=True).strip(),
            "mongo_version": version, "samples": len(rows), "warmups_excluded": 3,
            "provider_latency_ms_per_request": args.provider_latency_ms,
            "environment": "synthetic loopback replica set; simulated provider latency; not production",
            "http_ms": summary([r["http_ms"] for r in rows]),
            "cpu_ms": summary([r["cpu_ms"] for r in rows]),
            "visible_products_ms": summary([r["visible_products_ms"] for r in rows]),
            "salla_calls": summary([r["salla_calls"] for r in rows]),
            "db_commands": summary([sum(r["db_commands"].values()) for r in rows]),
            "transaction_count": summary([len(r["transaction_ms"]) for r in rows]),
            "transaction_ms": summary([t for r in rows for t in r["transaction_ms"]]),
            "event_loop_lag_p99_ms": summary([r["event_loop_lag_ms"]["p99"] for r in rows]),
            "runs": rows,
        }
        Path(args.output).write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({k:v for k,v in result.items() if k != "runs"}, indent=2))
    asyncio.run(run())


if __name__ == "__main__":
    main()
