"""Bounded representative queue measurements on a disposable replica set.

No sleeps simulate business time: only delivery.now advances. The Mongo queue,
leases, owner transactions, and shipping GET budget are production code. HTTP is
MockTransport, with every non-GET rejected. No imported test classes are collected.
Set SHIPPING_LOAD_REPORT to persist this run's JSON evidence.
"""
import asyncio
from collections import Counter
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
from time import perf_counter
import unittest
from unittest.mock import AsyncMock, patch
from urllib.parse import urlsplit
import uuid

import httpx
from bson.codec_options import CodecOptions
from bson.errors import InvalidBSON
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.monitoring import CommandListener

import assembly_completion_delivery as delivery
import shipping_read_budget as read_transport
from salla_integration import service

ROOT = Path(__file__).resolve().parents[2]


class QueueCommands(CommandListener):
    def __init__(self):
        self.counts = Counter()
        self.candidates = []
        self.writes = Counter()

    def started(self, event):
        self.counts[event.command_name] += 1
        command = event.command
        if event.command_name in {"insert", "update", "delete", "findAndModify"}:
            self.writes[command[event.command_name]] += 1
        if command.get("find") == delivery.WORKFLOWS and command.get("limit") == 16:
            self.candidates.append({key: deepcopy(command[key]) for key in
                ("find", "filter", "projection", "sort", "limit") if key in command})

    def succeeded(self, event):
        pass

    def failed(self, event):
        pass


class ReconciliationLoadTests(unittest.IsolatedAsyncioTestCase):
    results = {}
    ORDERS = 2000
    OWNERS = 100

    async def asyncSetUp(self):
        uri = os.environ.get("MZ2_TEST_MONGO_URI", "")
        if not uri:
            self.skipTest("MZ2_TEST_MONGO_URI required; no production fallback")
        self.assertIn(urlsplit(uri).hostname, {"127.0.0.1", "localhost"})
        self.monitor = QueueCommands()
        self.client = AsyncIOMotorClient(uri, event_listeners=[self.monitor], serverSelectionTimeoutMS=3000)
        hello = await self.client.admin.command("hello")
        self.assertTrue(hello.get("setName") and hello.get("isWritablePrimary"))
        self.db = self.client["shipping_load_" + uuid.uuid4().hex]
        self.clock = datetime.now(timezone.utc).replace(microsecond=0)
        self.base = self.clock
        self.calls = []
        self.active = 0
        self.max_active = 0
        self.first_get_entered = None
        self.release_first_get = None
        self.monkeypatches = []
        self.started = perf_counter()
        self.mongo_version = (await self.client.admin.command("buildInfo"))["version"]
        self.replset = hello["setName"]
        self.install(patch.object(delivery, "now", side_effect=lambda: self.clock))
        self.install(patch.object(service, "get_integration", new=AsyncMock(side_effect=lambda _db, owner: {"owner": owner})))
        self.install(patch.object(service, "_decrypt_access", side_effect=lambda integration: integration["owner"]))
        client_type = httpx.AsyncClient
        transport = httpx.MockTransport(self.provider)
        self.install(patch.object(read_transport.httpx, "AsyncClient", side_effect=lambda **kwargs:
            client_type(transport=transport, **kwargs)))
        # Prove the actual shipping read transport (including its budget) is used.
        self.install(patch.object(delivery.shipping, "call_salla", read_transport.call_salla))

    def install(self, patcher):
        self.monkeypatches.append(patcher)
        return patcher.start()

    async def asyncTearDown(self):
        for patcher in reversed(getattr(self, "monkeypatches", [])):
            patcher.stop()
        if hasattr(self, "db"):
            await self.client.drop_database(self.db.name)
            self.client.close()

    async def provider(self, request):
        self.assertEqual(request.method, "GET", "Load fixture forbids every provider write")
        owner = request.headers["Authorization"].removeprefix("Bearer ")
        self.calls.append({"method": request.method, "path": request.url.path,
                           "owner": owner, "simulated_at": self.clock.isoformat()})
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            if self.first_get_entered is not None and not self.first_get_entered.is_set():
                self.first_get_entered.set()
                await asyncio.wait_for(self.release_first_get.wait(), 5)
            path = request.url.path.removeprefix("/admin/v2")
            if path == "/orders":
                number = request.url.params["keyword"]
            elif path.startswith("/orders/internal-"):
                number = path.removeprefix("/orders/internal-")
            else:
                self.fail(f"Unexpected provider read: {path}")
            row = {"id": "internal-" + number, "reference_id": number,
                   "status": {"slug": "in_progress"}}
            return httpx.Response(200, json={"data": [row] if path == "/orders" else row})
        finally:
            self.active -= 1

    async def seed(self, *, clustered=False):
        started = perf_counter()
        workflows, sources = [], []
        for index in range(self.ORDERS):
            owner_index = index // (self.ORDERS // self.OWNERS) if clustered else index % self.OWNERS
            owner, number = f"owner-{owner_index:03}", f"load-{index:04}"
            operation = delivery.pending_operation("synthetic", "Load fixture")
            operation.update(state="requires_attention", read_attempts=0,
                status_attempted=True, awb_attempted=True, legacy_readback_only=True,
                due_at=(self.base - timedelta(hours=1) + timedelta(microseconds=index)).isoformat())
            workflows.append({"user_id": owner, "order_number": number, "revision": 1,
                "stage": "completed", "assembly_status": "completed", delivery.FIELD: operation})
            sources.append({"user_id": owner, "order_number": number,
                "order_status": "in_progress", "order_status_slug": "in_progress",
                "raw_by_source": {"salla_direct": {"id": "internal-" + number,
                    "reference_id": number, "status": {"slug": "in_progress"}}}})
        await self.db[delivery.WORKFLOWS].insert_many(workflows)
        await self.db.unified_orders.insert_many(sources)
        await self.db[delivery.WORKFLOWS].create_index([("user_id", 1), ("order_number", 1)], unique=True)
        await self.db.unified_orders.create_index([("user_id", 1), ("order_number", 1)], unique=True)
        # Same candidate index as start_worker, without starting its infinite loop.
        await self.db[delivery.WORKFLOWS].create_index([(f"{delivery.FIELD}.version", 1),
            (f"{delivery.FIELD}.state", 1), (f"{delivery.FIELD}.due_at", 1)], name="assembly_delivery_due")
        for collection in ("products", "inventory", "mezan_inventory_reservations_v2",
                           "mezan_component_consumption_units_v1", "general_ledger"):
            await self.db[collection].insert_one({"_id": "untouched", "on_hand": 100, "sentinel": collection})
        self.business_before = await self.business_hash()
        self.writes_before = self.monitor.writes.copy()
        self.seed_ms = (perf_counter() - started) * 1000

    async def business_hash(self):
        result = {}
        for collection in ("unified_orders", "products", "inventory", "mezan_inventory_reservations_v2",
                           "mezan_component_consumption_units_v1", "general_ledger"):
            rows = await self.db[collection].find({}).sort("_id", 1).to_list(None)
            result[collection] = hashlib.sha256(json.dumps(rows, sort_keys=True, default=str).encode()).hexdigest()
        return result

    async def explain_candidates(self):
        self.assertTrue(self.monitor.candidates)
        decode_note = None
        try:
            result = await self.db.command("explain", self.monitor.candidates[0], verbosity="executionStats")
        except InvalidBSON:
            # Windows Mongo 8.0.12 can return invalid UTF-8 inside explain's
            # diagnostic plan strings. Preserve numeric stats and disclose the
            # replacement; business reads always retain strict BSON decoding.
            result = await self.db.command("explain", self.monitor.candidates[0], verbosity="executionStats",
                codec_options=CodecOptions(unicode_decode_error_handler="replace"))
            decode_note = "Invalid UTF-8 in server explain diagnostic strings replaced on decoding; numeric metrics unchanged."
        stats = result["executionStats"]
        return {key: stats[key] for key in ("nReturned", "totalKeysExamined", "totalDocsExamined", "executionTimeMillis")} | {
            "winning_plan": result["queryPlanner"]["winningPlan"], "diagnostic_decode_note": decode_note,
            "measurement_state": "Actual captured candidate query explained after scenario completes"}

    async def record(self, name, details):
        self.assertEqual(await self.business_hash(), self.business_before)
        runtime_writes = self.monitor.writes - self.writes_before
        protected_writes = {name: runtime_writes[name] for name in self.business_before}
        self.assertTrue(all(count == 0 for count in protected_writes.values()), protected_writes)
        self.assertEqual(await self.db[delivery.WORKFLOWS].count_documents({}), self.ORDERS)
        self.assertEqual(await self.db[delivery.WORKFLOWS].count_documents({
            f"{delivery.FIELD}.status_attempted": True, f"{delivery.FIELD}.awb_attempted": True}), self.ORDERS)
        self.assertFalse(any(row["method"] != "GET" for row in self.calls))
        type(self).results[name] = {
            "orders": self.ORDERS, "owners": self.OWNERS, "mongo_version": self.mongo_version,
            "replica_set": self.replset, "seed_ms": round(self.seed_ms, 3),
            "elapsed_ms": round((perf_counter() - self.started) * 1000, 3),
            "provider_gets": len(self.calls), "provider_posts": 0,
            "max_concurrent_provider_calls": self.max_active,
            "candidate_queries": len(self.monitor.candidates), "candidate_limit": 16,
            "mongo_commands": dict(self.monitor.counts),
            "business_hash_before_and_after": self.business_before,
            "protected_collection_write_commands_after_seed": protected_writes,
            "runtime_write_commands_by_collection": dict(runtime_writes),
            "query_execution": await self.explain_candidates(), **details,
        }

    async def test_many_attention_orders_share_global_claim_and_cooldown(self):
        await self.seed()
        self.first_get_entered, self.release_first_get = asyncio.Event(), asyncio.Event()
        first = asyncio.create_task(delivery.run_once(self.db))
        try:
            await asyncio.wait_for(self.first_get_entered.wait(), 5)
            started = perf_counter()
            competitors = await asyncio.gather(*(delivery.run_once(self.db) for _ in range(15)))
            contention_ms = (perf_counter() - started) * 1000
            self.assertEqual(competitors, [0] * 15)
        finally:
            self.release_first_get.set()
            self.assertEqual(await asyncio.wait_for(first, 5), 1)
        query_count, read_count = len(self.monitor.candidates), len(self.calls)
        self.assertEqual(await delivery.run_once(self.db), 0)
        self.assertEqual((len(self.monitor.candidates), len(self.calls)), (query_count, read_count))
        job_times = []
        for slot in range(1, 8):
            self.clock = self.base + timedelta(seconds=slot * delivery.GLOBAL_READ_INTERVAL)
            started = perf_counter()
            self.assertEqual(await delivery.run_once(self.db), 1)
            job_times.append(round((perf_counter() - started) * 1000, 3))
        self.assertEqual(len(self.calls), 16)
        self.assertEqual(self.max_active, 1)
        self.assertEqual(await self.db[delivery.WORKFLOWS].count_documents({f"{delivery.FIELD}.read_attempts": 1}), 8)
        self.assertEqual(await self.db[delivery.WORKFLOWS].count_documents({f"{delivery.FIELD}.read_attempts": {"$gt": 1}}), 0)
        self.assertEqual(len({row["owner"] for row in self.calls}), 8)
        await self.record("interleaved_owners", {"workers_contending": 16,
            "duplicate_workers_started_jobs": sum(competitors), "contention_ms": round(contention_ms, 3),
            "successful_jobs": 8, "simulated_seconds": 105, "subsequent_job_ms": job_times,
            "provider_gets_per_job": 2, "configured_get_budget_per_job": 12,
            "immediate_cooldown_added_queries_or_reads": 0})

    async def test_backoff_and_exhaustion_remain_bounded_in_large_queue(self):
        await self.seed()
        await self.db[delivery.WORKFLOWS].update_many({"order_number": {"$ne": "load-0000"}},
            {"$set": {f"{delivery.FIELD}.due_at": "2100-01-01T00:00:00+00:00"}})
        delays = []
        for attempt in range(1, delivery.MAX_READ_ATTEMPTS + 1):
            self.assertEqual(await delivery.run_once(self.db), 1)
            row = await self.db[delivery.WORKFLOWS].find_one({"order_number": "load-0000"})
            operation = row[delivery.FIELD]
            self.assertEqual(operation["read_attempts"], attempt)
            self.assertEqual(operation["state"], "requires_attention")
            self.assertIsNone(operation["claim"])
            due = datetime.fromisoformat(operation["due_at"])
            delay = int((due - self.clock).total_seconds())
            self.assertEqual(delay, min(3600, 60 * (2 ** min(attempt - 1, 6))))
            delays.append(delay)
            calls_before = len(self.calls)
            self.assertEqual(await delivery.run_once(self.db), 0)
            self.assertEqual(len(self.calls), calls_before)
            self.clock = due
        self.assertEqual(await delivery.run_once(self.db), 0)
        self.clock += timedelta(hours=24)
        self.assertEqual(await delivery.run_once(self.db), 0)
        self.assertEqual(len(self.calls), 2 * delivery.MAX_READ_ATTEMPTS)
        await self.record("backoff_and_exhaustion", {"executed_read_attempts": delivery.MAX_READ_ATTEMPTS,
            "backoff_seconds": delays, "exhausted_order_additional_gets": 0,
            "simulated_seconds": int((self.clock - self.base).total_seconds()),
            "time_advance": "delivery.now only; no backoff sleeps"})

    async def test_clustered_owner_window_exposes_existing_fairness_limit(self):
        await self.seed(clustered=True)
        self.assertEqual(await delivery.run_once(self.db), 1)
        results = []
        for seconds in (15, 30, 45):
            self.clock = self.base + timedelta(seconds=seconds)
            results.append(await delivery.run_once(self.db))
        # This records an existing limitation, not a throughput success claim.
        # The oldest 16 are all on cooldown, hiding 99 eligible owners behind them.
        self.assertEqual(results, [0, 0, 0])
        other_due = await self.db[delivery.WORKFLOWS].distinct("user_id", {
            "user_id": {"$ne": "owner-000"}, f"{delivery.FIELD}.due_at": {"$lte": self.clock.isoformat()}})
        self.assertEqual(len(other_due), 99)
        self.clock = self.base + timedelta(seconds=60)
        self.assertEqual(await delivery.run_once(self.db), 1)
        self.assertEqual(len(self.calls), 4)
        await self.record("clustered_owner_fairness_limit", {
            "cooldown_slots_returning_zero": results, "eligible_owners_hidden_behind_window": len(other_due),
            "successful_jobs_in_first_60_simulated_seconds": 2,
            "limitation": "Oldest-16 candidate window can hide other eligible owners behind one owner's cooldown; no cross-owner fairness guarantee."})

    @classmethod
    def tearDownClass(cls):
        path = os.environ.get("SHIPPING_LOAD_REPORT")
        if path:
            output = Path(path)
            output.parent.mkdir(parents=True, exist_ok=True)
            report = {"generated_at": datetime.now(timezone.utc).isoformat(),
                "source_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
                "test_file_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                "worker_file_sha256": hashlib.sha256((ROOT / "backend/assembly_completion_delivery.py").read_bytes()).hexdigest(),
                "python": platform.python_version(), "platform": platform.platform(),
                "limitations": ["Synthetic local Mongo measurements, not production SLO evidence.",
                    "Provider always reports in_progress: document fetch and successful-label publication are not load-tested here.",
                    "Business time advances deterministically; measured timings are actual wall time.",
                    "Read-attempt exhaustion intentionally leaves manual attention; automation does not poll indefinitely."],
                "measurements": cls.results}
            output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
