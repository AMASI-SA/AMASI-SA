"""Source-added lines reserve independently without rebuilding existing units.

Only unique databases on an explicit loopback replica set are used. Allocation,
consumption and rollback execute the real operational transaction and services.
"""
import asyncio
import os
import unittest

from pymongo.errors import OperationFailure

import stock_component_consumption_service as service
import test_stock_component_consumption as fixture


@unittest.skipUnless(os.environ.get("MZ2_TEST_MONGO_URI"), "requires isolated local Mongo replica set")
class AddedItemComponentStockTests(unittest.IsolatedAsyncioTestCase):
    asyncTearDown = fixture.ComponentMongoTests.asyncTearDown
    line = fixture.ComponentMongoTests.line
    reserve = fixture.ComponentMongoTests.reserve
    consume = fixture.ComponentMongoTests.consume
    occupancy = fixture.ComponentMongoTests.occupancy
    stock = fixture.ComponentMongoTests.stock
    assertCode = fixture.ComponentMongoTests.assertCode

    async def asyncSetUp(self):
        await fixture.ComponentMongoTests.asyncSetUp(self)
        await self.db.order_review_workflows.insert_one({
            "user_id": "owner", "order_number": "order", "stage": "in_progress", "revision": 7,
            "completion_mode": "mezan_local_v1", "review_completion_operation_id": "review-proof",
            "items": [{"order_item_id": "line", "review_status": "reviewed", "quantity": 2}],
        })
        await self.db.order_review_completion_operations.insert_one({
            "_id": "review-proof", "user_id": "owner", "order_number": "order",
            "state": "completed", "completion_mode": "mezan_local_v1",
        })
        await self.db.unified_orders.insert_one({"user_id": "owner", "order_number": "order",
            "order_status_slug": "in_progress", "raw_by_source": {"salla_direct": {
                "id": "provider-order", "reference_id": "order", "status": {"slug": "in_progress"}}}})
        self.original = self.line(2)
        await self.reserve(lines=[self.original])

    async def snapshot(self):
        return {
            "plan": await self.db[service.PLANS].find_one({"order_id": "order"}),
            "units": await self.db[service.UNITS].find({"order_id": "order"}).sort("_id", 1).to_list(None),
            "claims": await self.db[service.CLAIMS].find({}).sort("_id", 1).to_list(None),
            "workflow": await self.db.order_review_workflows.find_one({"order_number": "order"}),
            "occupancy": await self.occupancy(),
        }

    async def test_append_preserves_consumed_and_reserved_units_and_old_workflow(self):
        await self.consume(units={"line": [1]})
        before = await self.snapshot()
        added = self.line(1, order_line_id="new", variant_id="red")
        await self.reserve(lines=[self.original, added], source_version=2)
        after = await self.snapshot()
        self.assertEqual(before["units"], [u for u in after["units"] if u["order_line_id"] == "line"])
        self.assertEqual(before["workflow"], after["workflow"])
        self.assertEqual(before["occupancy"], after["occupancy"])
        self.assertEqual(before["claims"], after["claims"])
        self.assertEqual(len(after["units"]), 3)
        new = next(u for u in after["units"] if u["order_line_id"] == "new")
        self.assertEqual(new["state"], "reserved")
        self.assertEqual(new["variant_id"], "red")
        self.assertEqual(new["allocations"][0]["quantity"], "2")
        self.assertEqual(await self.db.mezan_preparation_pieces_v1.count_documents({}), 0)
        self.assertEqual(await self.db.order_review_events.count_documents({}), 0)

    async def test_duplicate_and_concurrent_append_insert_one_unit(self):
        rows = [self.original, self.line(1, order_line_id="new")]
        await asyncio.gather(self.reserve(lines=rows, source_version=2), self.reserve(lines=rows, source_version=2))
        before = await self.snapshot()
        again = await self.reserve(lines=rows, source_version=2)
        self.assertTrue(again["duplicate"])
        self.assertEqual(before, await self.snapshot())
        self.assertEqual(await self.db[service.UNITS].count_documents({"order_line_id": "new"}), 1)

    async def test_several_additions_and_later_source_keep_each_prior_unit(self):
        second = self.line(1, order_line_id="new-a", variant_id="red")
        third = self.line(1, order_line_id="new-b", variant_id="blue")
        await self.reserve(lines=[self.original, second], source_version=2)
        before = await self.snapshot()
        await self.reserve(lines=[third, second, self.original], source_version=3)
        after = await self.snapshot()
        self.assertEqual(before["units"], [u for u in after["units"] if u["order_line_id"] != "new-b"])
        self.assertEqual(len(after["units"]), 4)
        self.assertEqual(before["workflow"], after["workflow"])

    async def test_same_revision_mixed_edit_delete_and_warehouse_change_reject(self):
        before = await self.snapshot()
        new = self.line(1, order_line_id="new")
        for rows, version, extra in (
            ([self.original, new], 1, {}),
            ([self.line(1), new], 2, {}),
            ([{**self.original, "variant_id": "changed"}, new], 2, {}),
            ([{**self.original, "custom_fields": [{"id": "name", "value": "new text"}]}, new], 2, {}),
            ([new], 2, {}),
            ([self.original, new], 2, {"warehouse_ids": ["warehouse"]}),
        ):
            with self.subTest(rows=rows, version=version, extra=extra):
                await self.assertCode("plan_conflict", self.reserve(lines=rows, source_version=version, **extra))
                self.assertEqual(before, await self.snapshot())

    async def test_shortage_rolls_back_earlier_new_line(self):
        before = await self.snapshot()
        await self.assertCode("insufficient_stock", self.reserve(lines=[self.original,
            self.line(1, order_line_id="a-new"), self.line(4, order_line_id="b-new")], source_version=2))
        self.assertEqual(before, await self.snapshot())

    async def test_database_failure_rolls_back_all_new_allocations_and_plan(self):
        before = await self.snapshot()
        await self.db.command({"collMod": service.UNITS,
            "validator": {"order_line_id": {"$ne": "z-fail"}}, "validationLevel": "strict"})
        with self.assertRaises(OperationFailure):
            await self.reserve(lines=[self.original, self.line(1, order_line_id="a-new"),
                self.line(1, order_line_id="z-fail")], source_version=2)
        self.assertEqual(before, await self.snapshot())

    async def test_terminal_workflow_or_invalid_local_proof_cannot_append(self):
        rows = [self.original, self.line(1, order_line_id="new")]
        for stage in ("completed", "ready_to_ship", "pending_review"):
            await self.db.order_review_workflows.update_one({}, {"$set": {"stage": stage}})
            before = await self.snapshot()
            await self.assertCode("addition_review_context_required", self.reserve(lines=rows, source_version=2))
            self.assertEqual(before, await self.snapshot())
        await self.db.order_review_workflows.update_one({}, {"$set": {"stage": "in_progress"}})
        await self.db.order_review_completion_operations.update_one({}, {"$set": {"superseded_by": "next"}})
        await self.assertCode("addition_review_context_required", self.reserve(lines=rows, source_version=2))
        await self.db.order_review_completion_operations.update_one({}, {"$unset": {"superseded_by": ""}, "$set": {"user_id": "other"}})
        await self.assertCode("addition_review_context_required", self.reserve(lines=rows, source_version=2))

    async def test_legacy_reviewed_snapshot_allowed_but_unknown_mode_not(self):
        rows = [self.original, self.line(1, order_line_id="new")]
        await self.db.order_review_workflows.update_one({}, {"$set": {"completion_mode": "unknown"}})
        await self.assertCode("addition_review_context_required", self.reserve(lines=rows, source_version=2))
        await self.db.order_review_workflows.update_one({}, {"$unset": {"completion_mode": ""}})
        await self.reserve(lines=rows, source_version=2)
        self.assertEqual(await self.db[service.UNITS].count_documents({"order_line_id": "new"}), 1)

    async def test_stale_or_conflicting_provider_status_blocks_new_reservations(self):
        rows = [self.original, self.line(1, order_line_id="new")]
        for patch in ({"order_status_slug": "completed"}, {"order_status_slug": "delivered"},
                      {"order_status_slug": "canceled"},
                      {"g47_salla_snapshot.requires_authoritative_refresh": True}):
            await self.db.unified_orders.update_one({}, {"$set": patch})
            before = await self.snapshot()
            await self.assertCode("addition_source_status_required", self.reserve(lines=rows, source_version=2))
            self.assertEqual(before, await self.snapshot())
            await self.db.unified_orders.update_one({}, {"$set": {"order_status_slug": "in_progress"},
                "$unset": {"g47_salla_snapshot": ""}})
