"""Real Mongo board equivalence and repeatable query/CPU/latency measurements.

Baseline is the immutable base commit's function, not a rewritten approximation.
Timing measures direct async handler work, excluding HTTP transport/auth. Each
size receives one warmup and >=10 measured runs per implementation. p50 uses
median; p95 is nearest-rank ceil(.95*N). No timing threshold is a correctness gate.
"""
import ast
import asyncio
from collections import Counter
from copy import deepcopy
import hashlib
import json
import math
import os
from pathlib import Path
import statistics
import subprocess
import threading
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

from bson.json_util import dumps
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.monitoring import CommandListener
from pymongo.uri_parser import parse_uri

import preparation_piece_operations as ops
import order_engine.service as order_service

BASE = "c203cbcb53cb0015f6baec47f9ee681445525d52"


def baseline_handler():
    source = subprocess.check_output(["git", "show", BASE + ":backend/preparation_piece_operations.py"], encoding="utf-8")
    tree = ast.parse(source)
    node = next(n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name == "_assembly_order_board")
    namespace = dict(vars(ops))
    exec(compile(ast.Module(body=[node], type_ignores=[]), BASE + ":_assembly_order_board", "exec"), namespace)
    return namespace["_assembly_order_board"]


class Commands(CommandListener):
    def __init__(self):
        self.lock = threading.Lock()
        self.reset()

    def reset(self):
        with self.lock:
            self.counts = Counter()
            self.single_order_reads = 0

    def started(self, event):
        with self.lock:
            self.counts[event.command_name] += 1
            if event.command_name == "find" and event.command.get("find") == "unified_orders":
                value = event.command.get("filter", {}).get("order_number")
                self.single_order_reads += isinstance(value, str)

    def succeeded(self, event):
        pass

    def failed(self, event):
        pass


class BoardPerformanceMongoTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        uri = os.environ.get("MZ2_TEST_MONGO_URI", "")
        if not uri:
            self.skipTest("Explicit isolated MZ2_TEST_MONGO_URI required")
        parsed = parse_uri(uri)
        self.assertTrue(all(h in {"127.0.0.1", "localhost", "::1"} for h, _ in parsed["nodelist"]))
        self.assertFalse(parsed["username"] or parsed["password"] or parsed["database"])
        self.commands = Commands()
        self.client = AsyncIOMotorClient(uri, event_listeners=[self.commands], tz_aware=True, serverSelectionTimeoutMS=5000)
        self.addAsyncCleanup(self.cleanup)
        self.assertEqual((await self.client.admin.command("buildInfo"))["version"], "8.0.12")
        self.assertEqual((await self.client.admin.command("hello"))["setName"], "performancepr1")
        self.db = self.client["board_perf_pr1_" + uuid4().hex]
        self.baseline = baseline_handler()
        await self.db.unified_orders.create_index([("user_id", 1), ("order_number", 1)], unique=True)
        await self.db[ops.WORKFLOWS].create_index([("user_id", 1), ("updated_at", 1)])
        await self.db[ops.PIECES].create_index([("user_id", 1), ("order_number", 1)])

    async def cleanup(self):
        if hasattr(self, "db"):
            self.assertTrue(self.db.name.startswith("board_perf_pr1_"))
            await self.client.drop_database(self.db.name)
        self.client.close()

    async def populate(self, count):
        for name in (ops.WORKFLOWS, ops.PIECES, "unified_orders"):
            await self.db[name].delete_many({})
        workflows, pieces, orders = [], [], []
        for i in range(count):
            number = f"{100000 + i}"
            workflow = {"user_id": "owner", "order_number": number, "stage": "in_progress",
                "updated_at": f"2040-01-{i % 28 + 1:02d}T10:00:00Z", "preparation_piece_count": 1,
                "carrier_name": "Synthetic carrier", "assembly_status": "in_progress"}
            if i % 7 == 0:
                workflow["operational_items"] = [{"operational_item_id": "virtual-" + number, "name": "Personalization", "preparation_status": "ready"}]
            else:
                pieces.append({"user_id": "owner", "order_number": number, "piece_id": "physical-" + number,
                    "status": "ready_for_assembly", "assembly_status": "ready" if i % 3 == 0 else "pending"})
            workflows.append(workflow)
            if i % 29 == 0:  # missing canonical order
                continue
            state = "completed" if i % 3 == 0 else "in_progress"
            raw = {"id": i + 1, "reference_id": number, "date": f"2040-01-{i % 28 + 1:02d}T10:00:00+03:00",
                "status": {"slug": state, "name": state}, "customer": {"full_name": "Synthetic customer"},
                "items": [{"id": "item-" + number, "name": "Synthetic product", "quantity": 1,
                    "options": [{"name": "Color", "value": "black"}]}], "amounts": {"total": {"amount": 100, "currency": "SAR"}}}
            if i % 31 == 0:
                raw["date"] = "invalid-date"  # mapper skips malformed order just as baseline
            orders.append({"user_id": "owner", "order_number": number, "order_status": state,
                "order_date": raw["date"], "raw_by_source": {"salla_direct": raw}})
        # Cross-tenant rows must never appear, even with matching order numbers.
        workflows.append({"user_id": "foreign", "order_number": "100001", "preparation_piece_count": 9, "updated_at": "2040"})
        pieces.append({"user_id": "foreign", "order_number": "100001", "piece_id": "foreign", "assembly_status": "ready"})
        orders.append({"user_id": "foreign", "order_number": "100001", "raw_by_source": {"salla_direct": {"reference_id": "100001"}}})
        await self.db[ops.WORKFLOWS].insert_many(workflows)
        await self.db[ops.PIECES].insert_many(pieces)
        await self.db.unified_orders.insert_many(orders)

    async def snapshot(self):
        result = {}
        for name in sorted(await self.db.list_collection_names()):
            digest = hashlib.sha256()
            async for row in self.db[name].find({}).sort("_id", 1):
                digest.update(dumps(row, sort_keys=True).encode())
            result[name] = digest.hexdigest()
        return result

    async def measured(self, handler, **kwargs):
        cpu, calls = 0.0, 0
        original = order_service._map_row
        def timed(*args, **kw):
            nonlocal cpu, calls
            started = time.thread_time()
            try:
                return original(*args, **kw)
            finally:
                cpu += time.thread_time() - started
                calls += 1
        self.commands.reset()
        with patch.object(order_service, "_map_row", timed):
            started = time.perf_counter()
            result = await handler(self.db, user_id="owner", **kwargs)
            elapsed = time.perf_counter() - started
        return result, {"handler_ms": elapsed * 1000, "mapping_cpu_ms": cpu * 1000, "mapping_calls": calls,
                        "commands": dict(self.commands.counts), "single_order_reads": self.commands.single_order_reads}

    async def test_exact_equivalence_filters_pagination_and_read_only(self):
        await self.populate(50)
        before = await self.snapshot()
        for state in ("in_progress", "completed"):
            for query, offset, limit in (("", 0, 20), ("", 5, 7), ("#10000", 0, 10), ("does-not-exist", 0, 20), ("", 500, 10)):
                with self.subTest(state=state, query=query, offset=offset):
                    args = dict(state=state, query=query, offset=offset, limit=limit)
                    old, _ = await self.measured(self.baseline, **args)
                    new, metrics = await self.measured(ops._assembly_order_board, **args)
                    self.assertEqual(new, old)
                    self.assertEqual(metrics["single_order_reads"], 0)
        self.assertEqual(await self.snapshot(), before)

    async def test_real_mongo_benchmark_50_500_5000(self):
        repetitions = max(10, int(os.environ.get("PERF_BOARD_REPETITIONS", "10")))
        evidence = {"baseline": BASE, "candidate_head": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
            "mongodb": "8.0.12", "replica_set": "performancepr1", "repetitions": repetitions,
            "measurement": "Direct handler duration, excluding HTTP/auth; mapping CPU thread_time; nearest-rank p95; one warmup per size/implementation",
            "sizes": []}
        for size in (50, 500, 5000):
            await self.populate(size)
            before = await self.snapshot()
            args = dict(state="in_progress", query="", offset=0, limit=50)
            expected, _ = await self.measured(self.baseline, **args)
            actual, _ = await self.measured(ops._assembly_order_board, **args)
            self.assertEqual(actual, expected)
            samples = {"baseline": [], "candidate": []}
            for _ in range(repetitions):
                for name, handler in (("baseline", self.baseline), ("candidate", ops._assembly_order_board)):
                    result, metric = await self.measured(handler, **args)
                    self.assertEqual(result, expected)
                    samples[name].append(metric)
            summarized = {"workflow_count": size, "samples": samples}
            for name, rows in samples.items():
                summary = {}
                for field in ("handler_ms", "mapping_cpu_ms", "mapping_calls"):
                    values = sorted(row[field] for row in rows)
                    summary[field] = {"p50": statistics.median(values), "p95": values[math.ceil(.95 * len(values)) - 1]}
                summary["commands_per_run"] = rows[0]["commands"]
                summary["single_order_reads"] = rows[0]["single_order_reads"]
                summarized[name] = summary
            self.assertGreaterEqual(samples["baseline"][0]["single_order_reads"], size)
            self.assertEqual(samples["candidate"][0]["single_order_reads"], 0)
            self.assertLess(samples["candidate"][0]["commands"].get("find", 0), samples["baseline"][0]["commands"].get("find", 0))
            self.assertEqual(await self.snapshot(), before)
            evidence["sizes"].append(summarized)
            print("BOARD_PERF " + json.dumps({k: v for k, v in summarized.items() if k != "samples"}), flush=True)
        target = os.environ.get("PERF_EVIDENCE_PATH")
        if target:
            Path(target).parent.mkdir(parents=True, exist_ok=True)
            Path(target).write_text(json.dumps(evidence, indent=2), encoding="utf-8")

    async def test_duplicate_identity_preserves_first_row_even_when_malformed(self):
        await self.populate(50)
        await self.db.unified_orders.drop_index("user_id_1_order_number_1")
        valid = await self.db.unified_orders.find_one({"user_id": "owner", "order_number": "100001"})
        await self.db.unified_orders.update_one({"_id": valid["_id"]}, {"$set": {"raw_by_source.salla_direct": []}})
        valid.pop("_id")
        await self.db.unified_orders.insert_one(valid)
        duplicate = await self.db.unified_orders.find_one({"user_id": "owner", "order_number": "100002"})
        duplicate.pop("_id")
        duplicate["order_status"] = "completed"
        duplicate["raw_by_source"]["salla_direct"]["status"] = {"slug": "completed", "name": "completed"}
        await self.db.unified_orders.insert_one(duplicate)
        before = await self.snapshot()
        for state in ("in_progress", "completed"):
            args = dict(state=state, limit=50, offset=0)
            self.assertEqual(await ops._assembly_order_board(self.db, user_id="owner", **args),
                             await self.baseline(self.db, user_id="owner", **args))
        self.assertEqual(await self.snapshot(), before)

    async def test_asgi_permission_and_tenant_boundary(self):
        await self.populate(50)
        context = {"merchant_id": "owner", "actor_id": "viewer", "permissions": set(), "is_owner": False}
        async def actor(db, user):
            return context
        async def user():
            return {"id": "viewer", "role": "viewer"}
        app = FastAPI()
        app.include_router(ops.make_preparation_piece_operations_router(self.db, user))
        with patch.object(ops, "_actor_context", actor):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://fixture.test") as client:
                denied = await client.get("/preparation-work-v1/assembly/orders", params={"state": "in_progress"})
                self.assertEqual(denied.status_code, 403, denied.text)
                context.update(merchant_id="foreign", actor_id="foreign", is_owner=True,
                               permissions={"fulfillment.ready.read"})
                response = await client.get("/preparation-work-v1/assembly/orders", params={"state": "in_progress"})
                self.assertEqual(response.status_code, 200, response.text)
                expected = await self.baseline(self.db, user_id="foreign", state="in_progress", limit=100, offset=0)
                self.assertEqual(response.json(), expected)
