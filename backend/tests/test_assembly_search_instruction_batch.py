"""Real loopback Mongo search regression tests; synthetic source/eligibility fixtures.
No provider IO, customer data, writes outside a disposable test database, or
production fallback. Query counts are collected at the Motor collection seam.
"""
import asyncio
from contextlib import ExitStack
import os
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from motor.motor_asyncio import AsyncIOMotorClient
import preparation_piece_operations as pieces
import order_tracking_notes as notes

class CountedCollection:
    def __init__(self, collection, counts):
        self.collection, self.counts = collection, counts
    def find(self, *args, **kwargs):
        self.counts["instruction_queries"] += 1
        return self.collection.find(*args, **kwargs)
    def __getattr__(self, name):
        return getattr(self.collection, name)


class CountedDB:
    def __init__(self, db):
        self.db = db
        self.counts = {"instruction_queries": 0}
    def __getitem__(self, key):
        collection = self.db[key]
        return CountedCollection(collection, self.counts) if key == notes.ORDER_TRACKING_INSTRUCTIONS else collection
    def __getattr__(self, key):
        return getattr(self.db, key)


class AssemblySearchInstructionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        uri = os.environ.get("MZ2_TEST_MONGO_URI", "")
        if not uri:
            self.skipTest("local MZ2_TEST_MONGO_URI required; no production fallback")
        self.assertTrue(uri.startswith("mongodb://127.0.0.1:"))
        self.mongo = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=5000)
        self.raw = self.mongo["assembly_search_" + uuid4().hex]
        self.db = CountedDB(self.raw)
        self.stack = ExitStack()
        self.order = SimpleNamespace(status="in_progress", status_native="in_progress", items=[], created_at=None, shipping=SimpleNamespace(company=None))
        self.stack.enter_context(patch.object(pieces, "_current_assembly_order", AsyncMock(return_value=self.order)))
        self.stack.enter_context(patch.object(pieces, "_historical_assembly_context", AsyncMock(return_value=(None, None, set()))))
        self.stack.enter_context(patch.object(pieces, "assembly_execution_allowed", return_value=True))
        await self.raw[pieces.WORKFLOWS].insert_one({"user_id": "synthetic", "order_number": "42", "stage": "ready_to_ship"})
        await self.raw[notes.ORDER_TRACKING_INSTRUCTIONS].create_index([("user_id", 1), ("order_number", 1), ("created_at", 1)])

    async def asyncTearDown(self):
        self.stack.close()
        await self.mongo.drop_database(self.raw.name)
        self.mongo.close()

    async def seed(self, count):
        await self.raw[pieces.PIECES].delete_many({})
        await self.raw[pieces.PIECES].insert_many([
            {"user_id": "synthetic", "order_number": "42", "piece_id": f"piece-{i}",
             "order_item_id": f"item-{i // 2}", "unit_index": i + 1,
             "status": pieces.PIECE_STATUS_READY_FOR_ASSEMBLY, "supplier_dispatch_status": "received"}
            for i in range(count)
        ])

    async def instruction(self, identifier, **values):
        await self.raw[notes.ORDER_TRACKING_INSTRUCTIONS].insert_one({
            "id": identifier, "user_id": "synthetic", "order_number": "42", "scope": "order",
            "status": "active", "target_stages": ["assembly_labeling"], "enforcement": "notice",
            "created_at": identifier, **values,
        })

    async def read(self, function, actor="actor"):
        self.db.counts["instruction_queries"] = 0
        started = time.perf_counter()
        result = await function(self.db, user_id="synthetic", query="42", actor_id=actor)
        elapsed = (time.perf_counter() - started) * 1000
        return result, elapsed, self.db.counts["instruction_queries"]

    async def test_identical_per_piece_item_order_and_actor_enforcement(self):
        await self.seed(10)
        await self.instruction("a-order-notice")
        await self.instruction("b-piece", scope="piece", target_ids=["piece-0"], enforcement="completion_required")
        await self.instruction("c-item", scope="item", target_id="item-1", enforcement="acknowledgement_required", acknowledged_by_ids=["actor"])
        await self.instruction("d-waiting", scope="piece", target_id="piece-5", status="waiting_customer_service_approval")
        await self.instruction("e-other-stage", target_stages=["preparation"], enforcement="completion_required")
        await self.instruction("f-inactive", status="completed", enforcement="completion_required")
        await self.instruction("g-other-owner", user_id="other", enforcement="completion_required")
        for actor in ("actor", "other-actor"):
            after, _, new_count = await self.read(pieces._assembly_search, actor)
            self.assertEqual(new_count, 1)
            self.assertEqual([row["id"] for row in after["pieces"][-1]["instructions"]], ["a-order-notice"])
            rows = {row["piece_id"]: row for row in after["pieces"]}
            self.assertFalse(rows["piece-0"]["can_mark_ready"])
            self.assertFalse(rows["piece-5"]["can_mark_ready"])
            self.assertEqual(rows["piece-2"]["can_mark_ready"], actor == "actor")
            self.assertTrue(rows["piece-8"]["can_mark_ready"])

    async def test_no_cross_request_cache_after_instruction_edit(self):
        await self.seed(1)
        first, _, _ = await self.read(pieces._assembly_search)
        self.assertTrue(first["pieces"][0]["can_mark_ready"])
        await self.instruction("new", enforcement="completion_required")
        second, _, calls = await self.read(pieces._assembly_search)
        self.assertFalse(second["pieces"][0]["can_mark_ready"])
        self.assertEqual(calls, 1)

    async def test_in_progress_gate_and_missing_actor_do_not_add_reads(self):
        await self.seed(10)
        for status in ("completed", "delivered", "pending_review"):
            self.order.status = self.order.status_native = status
            result, _, calls = await self.read(pieces._assembly_search)
            self.assertFalse(any(row["can_mark_ready"] for row in result["pieces"]))
            self.assertEqual(calls, 0)
        self.order.status = self.order.status_native = "in_progress"
        _, _, calls = await self.read(pieces._assembly_search, actor="")
        self.assertEqual(calls, 0)

    async def test_one_instruction_query_for_all_supported_piece_counts(self):
        await self.instruction("notice")
        for count in (1, 10, 50):
            await self.seed(count)
            result, _, queries = await self.read(pieces._assembly_search)
            self.assertEqual(queries, 1)
            self.assertEqual(len(result["pieces"]), count)
            self.assertTrue(all(row["can_mark_ready"] for row in result["pieces"]))
            self.assertTrue(all([item["id"] for item in row["instructions"]] == ["notice"] for row in result["pieces"]))


if __name__ == "__main__":
    unittest.main()
