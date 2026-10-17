"""Legacy completion reads: real local Mongo, synthetic provider only."""
import asyncio
from copy import deepcopy
import os
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from motor.motor_asyncio import AsyncIOMotorClient

import assembly_completion_legacy_readback as legacy
from accounting_write_control import AccountingDatabase
from order_engine import shipping_label_service as shipping
from salla_shipping import CURRENT_SHIPPING


class LegacyCompletionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        uri = os.environ.get("MZ2_TEST_MONGO_URI", "")
        if not uri:
            self.skipTest("MZ2_TEST_MONGO_URI required; no production fallback")
        self.assertTrue(uri.startswith("mongodb://127.0.0.1:"))
        self.mongo = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=5000)
        self.db = self.mongo["legacy_readback_" + uuid4().hex]
        self.assertTrue((await self.db.command("hello")).get("setName"))
        self.addCleanup(self.mongo.close)
        async def cleanup():
            self.assertTrue(self.db.name.startswith("legacy_readback_"))
            await self.mongo.drop_database(self.db.name)
        self.addAsyncCleanup(cleanup)
        self.order = {"id": "123", "reference_id": "3001", "status": {"slug": "completed"},
                      "date": "2026-10-01T10:00:00Z", "items": [],
                      "shipping": {"company": "iMile"}}
        await self.db.unified_orders.insert_one({"user_id": "owner", "order_number": "3001",
            "order_date": "2026-10-01T10:00:00Z",
            "order_status": "completed", "raw_by_source": {"salla_direct": deepcopy(self.order)},
            CURRENT_SHIPPING: {"company_name": "iMile", "shipment_id": "shipment-a", "status": "created",
                "tracking_number": "SYNTHETIC-AWB", "label_url": "https://labels.test/current.pdf"}})
        await self.db.order_review_workflows.insert_one({"user_id": "owner", "order_number": "3001",
            "stage": "completed", "assembly_status": "completed", "revision": 4,
            "assembly_ready_piece_count": 2, "assembly_total_piece_count": 2, "items": []})
        self.provider = AsyncMock(side_effect=self.provider_read)
        for replacement in (patch.object(shipping, "call_salla", self.provider),
                patch.object(shipping, "_resolve_order", AsyncMock(side_effect=AssertionError("no keyword lookup"))),
                patch.object(shipping, "_refresh_provider_shipping_label", AsyncMock(side_effect=AssertionError("no eager courier PDF"))),
                patch.object(shipping, "_best_effort_resync", AsyncMock(side_effect=AssertionError("no resync")))):
            replacement.start()
            self.addCleanup(replacement.stop)

    async def provider_read(self, db, owner, method, path, **kwargs):
        self.assertIs(db.current() if isinstance(db, AccountingDatabase) else db, self.db)
        self.assertEqual((owner, method, path), ("owner", "GET", "/orders/123"))
        return {"data": deepcopy(self.order)}

    async def snapshot(self):
        return {name: await self.db[name].find({}).to_list(None)
                for name in await self.db.list_collection_names()}

    async def read(self):
        return await legacy.read_legacy_completion(self.db, "owner", "3001")

    async def test_completed_saved_label_is_immediate_and_writes_nothing(self):
        before = await self.snapshot()
        result = await self.read()
        self.assertTrue(result["assembly_completion_confirmed"])
        self.assertEqual(result["order_completion_status"], "confirmed")
        self.assertTrue(result["order_status_completed"])
        self.assertFalse(result["requires_attention"])
        self.assertTrue(result["ready"])
        self.assertEqual(result["label_status"], "available")
        self.assertEqual(result["label_url"], "https://labels.test/current.pdf")
        self.assertEqual(self.provider.await_count, 1)
        self.assertEqual(await self.snapshot(), before)

    async def test_every_settled_read_gets_new_provider_proof_not_cached_authority(self):
        self.assertTrue((await self.read())["ready"])
        self.order["status"] = {"slug": "delivered"}
        with self.assertRaises(shipping.ShippingLabelError) as error:
            await self.read()
        self.assertEqual(error.exception.code, "order_status_not_completed")
        self.assertEqual(self.provider.await_count, 2)

    async def test_outbox_presence_including_empty_or_null_never_takes_fast_path(self):
        for value in (None, {}, {"state": "pending"}, {"state": "requires_attention"}):
            await self.db.order_review_workflows.update_one({}, {"$set": {"assembly_delivery": value}})
            self.assertIsNone(await self.read())
        self.provider.assert_not_awaited()

    async def test_durable_assembly_completion_is_required_even_with_cached_label(self):
        await self.db.order_review_workflows.update_one({}, {"$set": {"assembly_status": "in_progress"}})
        with self.assertRaises(shipping.ShippingLabelError) as error:
            await self.read()
        self.assertEqual(error.exception.code, "assembly_completion_required")
        self.provider.assert_not_awaited()

    async def test_other_owner_cannot_lookup_or_read_this_order(self):
        with self.assertRaises(shipping.ShippingLabelError):
            await legacy.read_legacy_completion(self.db, "other-owner", "3001")
        self.provider.assert_not_awaited()

    async def test_missing_cached_internal_id_never_uses_keyword_fallback(self):
        await self.db.unified_orders.update_one({}, {"$unset": {"raw_by_source.salla_direct.id": ""}})
        with self.assertRaises(shipping.ShippingLabelError) as error:
            await self.read()
        self.assertEqual(error.exception.code, "salla_order_id_missing")
        self.provider.assert_not_awaited()

    async def test_exact_provider_identity_and_completed_status_are_required(self):
        original = deepcopy(self.order)
        for changed in ({"id": "456"}, {"reference_id": "OTHER"}, {"reference_id": None},
                {"status": {"slug": "in_progress"}}, {"status": {"slug": "shipped"}},
                {"status": None}, {"status_slug": "cancelled"}):
            with self.subTest(changed=changed):
                self.order = {**original, **changed}
                with self.assertRaises(shipping.ShippingLabelError):
                    await self.read()

    async def test_pending_saved_label_keeps_completion_but_never_fetches_provider_label(self):
        await self.db.unified_orders.update_one({}, {"$set": {f"{CURRENT_SHIPPING}.label_url": None}})
        result = await self.read()
        self.assertTrue(result["order_status_completed"])
        self.assertEqual(result["label_status"], "pending")
        self.assertFalse(result["ready"])
        self.assertIsNone(result["label_url"])
        self.assertEqual(self.provider.await_count, 1)

    async def test_courier_get_confirms_completion_without_building_pdf(self):
        await self.db.unified_orders.update_one({}, {"$set": {
            f"{CURRENT_SHIPPING}.company_name": "مندوب المتجر", f"{CURRENT_SHIPPING}.company_code": "0"}})
        self.order["shipping"] = {"company": "مندوب المتجر", "company_code": "0"}
        before = await self.snapshot()
        result = await self.read()
        self.assertTrue(result["order_status_completed"])
        self.assertFalse(result["ready"])
        self.assertEqual(result["label_type"], "store_courier")
        self.assertIsNone(result["print_data"])
        self.assertEqual(await self.snapshot(), before)

    async def test_external_reader_itself_refuses_courier_fallback(self):
        await self.db.unified_orders.update_one({}, {"$set": {
            f"{CURRENT_SHIPPING}.company_name": "مندوب المتجر", f"{CURRENT_SHIPPING}.company_code": "0"}})
        with self.assertRaises(shipping.ShippingLabelError) as error:
            await shipping.read_saved_external_shipping_label(self.db, "owner", "3001")
        self.assertEqual(error.exception.code, "store_courier_explicit_print_required")
        self.provider.assert_not_awaited()

    async def test_fresh_provider_carrier_or_shipment_contradiction_rejects_cached_pdf(self):
        for changed in ({"shipping": {"company": "SMSA"}},
                {"shipments": [{"id": "replacement", "courier_name": "iMile", "status": "created"}]},
                {"shipping": {"company": "مندوب المتجر", "company_code": "0"}}):
            with self.subTest(changed=changed):
                self.order = {"id": "123", "reference_id": "3001", "status": {"slug": "completed"}, **changed}
                with self.assertRaises(shipping.ShippingLabelError) as error:
                    await self.read()
                self.assertEqual(error.exception.code, "shipping_snapshot_changed")

    async def test_light_provider_order_uses_saved_label_without_shipments_lookup(self):
        self.order.pop("shipping")
        self.assertTrue((await self.read())["ready"])
        self.assertEqual(self.provider.await_count, 1)

    async def test_local_canonical_conflict_or_component_pending_prevents_provider_read(self):
        for change in ({"order_status": "delivered"}, {"g47_salla_snapshot.component_pending": True}):
            with self.subTest(change=change):
                await self.db.unified_orders.update_one({}, {"$set": change})
                with self.assertRaises(shipping.ShippingLabelError) as error:
                    await self.read()
                self.assertEqual(error.exception.code, "assembly_canonical_status_blocked")
                await self.db.unified_orders.update_one({}, {"$set": {"order_status": "completed"}, "$unset": {"g47_salla_snapshot": ""}})
        self.provider.assert_not_awaited()

    async def test_changes_during_provider_read_fence_publication(self):
        source = await self.db.unified_orders.find_one({})
        workflow = await self.db.order_review_workflows.find_one({})
        for collection, change in (
            ("order_review_workflows", {"assembly_delivery": {"state": "pending"}}),
            ("order_review_workflows", {"assembly_status": "in_progress"}),
            ("order_review_workflows", {"shipping_status_epoch": 1}),
            ("order_review_workflows", {"store_delivery_assignment_id": "replacement"}),
            ("order_review_workflows", {"items": [{"new": "material"}]}),
            ("unified_orders", {f"{CURRENT_SHIPPING}.shipment_id": "replacement"}),
            ("unified_orders", {"raw_by_source.salla_direct.items": [{"new": "material"}]}),
            ("unified_orders", {"order_status": "delivered"}),
        ):
            with self.subTest(collection=collection, change=change):
                await self.db.unified_orders.replace_one({}, deepcopy(source))
                await self.db.order_review_workflows.replace_one({}, deepcopy(workflow))
                async def racing(*args, **kwargs):
                    await self.db[collection].update_one({}, {"$set": change})
                    return await self.provider_read(*args, **kwargs)
                self.provider.side_effect = racing
                with self.assertRaises(shipping.ShippingLabelError):
                    await self.read()

    async def test_late_cancellation_receipt_blocks_before_projection_worker(self):
        async def racing(*args, **kwargs):
            await self.db.salla_shipping_label_inbox.insert_one({"user_id": "owner", "order_number": "3001",
                "state": "pending", "observation": {"shipment_id": "shipment-a", "status": "cancelled"}})
            return await self.provider_read(*args, **kwargs)
        self.provider.side_effect = racing
        with self.assertRaises(shipping.ShippingLabelError) as error:
            await self.read()
        self.assertEqual(error.exception.code, "shipping_snapshot_changed")
        row = await self.db.unified_orders.find_one({})
        self.assertEqual(row[CURRENT_SHIPPING]["status"], "created")
        result = await self.read()
        self.assertFalse(result["ready"])
        self.assertTrue(result["order_status_completed"])

    async def test_concurrent_same_fence_shares_one_provider_get_and_returns_independent_data(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def wait(*args, **kwargs):
            entered.set()
            await release.wait()
            return await self.provider_read(*args, **kwargs)
        self.provider.side_effect = wait
        first = asyncio.create_task(self.read())
        await entered.wait()
        second = asyncio.create_task(self.read())
        # A separate actual read snapshot gets a chance to join the shared IO.
        await asyncio.sleep(0.05)
        self.assertEqual(self.provider.await_count, 1)
        release.set()
        a, b = await asyncio.gather(first, second)
        self.assertIsNot(a, b)
        a["ready"] = False
        self.assertTrue(b["ready"])
        self.assertEqual(self.provider.await_count, 1)

    async def test_cancelled_reader_does_not_cancel_shared_provider_read(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def wait(*args, **kwargs):
            entered.set()
            await release.wait()
            return await self.provider_read(*args, **kwargs)
        self.provider.side_effect = wait
        first = asyncio.create_task(self.read())
        await entered.wait()
        second = asyncio.create_task(self.read())
        await asyncio.sleep(0.05)
        first.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await first
        release.set()
        self.assertTrue((await second)["ready"])
        self.assertEqual(self.provider.await_count, 1)

    async def test_changed_fence_never_joins_old_completion_authority(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def wait(*args, **kwargs):
            entered.set()
            await release.wait()
            return await self.provider_read(*args, **kwargs)
        self.provider.side_effect = wait
        first = asyncio.create_task(self.read())
        await entered.wait()
        await self.db.order_review_workflows.update_one({}, {"$set": {"store_delivery_assignment_id": "new"}})
        second = asyncio.create_task(self.read())
        await asyncio.sleep(0.05)
        self.assertEqual(self.provider.await_count, 2)
        release.set()
        a, b = await asyncio.gather(first, second, return_exceptions=True)
        self.assertIsInstance(a, shipping.ShippingLabelError)
        self.assertTrue(b["ready"])

    async def test_last_cancelled_waiter_cancels_and_joins_provider_without_orphan(self):
        entered, exited = asyncio.Event(), asyncio.Event()
        async def wait(*args, **kwargs):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                exited.set()
        self.provider.side_effect = wait
        first = asyncio.create_task(self.read())
        await entered.wait()
        second = asyncio.create_task(self.read())
        await asyncio.sleep(0.05)
        first.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await first
        self.assertFalse(exited.is_set())
        second.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await second
        self.assertTrue(exited.is_set())
        self.assertFalse(legacy._flights)
        self.provider.side_effect = self.provider_read
        self.assertTrue((await self.read())["ready"])
        self.assertEqual(self.provider.await_count, 2)

    async def test_different_request_wrappers_share_only_underlying_database_fence(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def wait(*args, **kwargs):
            entered.set()
            await release.wait()
            return await self.provider_read(*args, **kwargs)
        self.provider.side_effect = wait
        a, b = AccountingDatabase(self.db), AccountingDatabase(self.db)
        first = asyncio.create_task(legacy.read_legacy_completion(a, "owner", "3001"))
        await entered.wait()
        second = asyncio.create_task(legacy.read_legacy_completion(b, "owner", "3001"))
        await asyncio.sleep(0.05)
        self.assertEqual(self.provider.await_count, 1)
        release.set()
        results = await asyncio.gather(first, second)
        self.assertTrue(all(result["ready"] for result in results))
        self.assertFalse(legacy._flights)

    async def test_each_shared_result_waiter_rechecks_current_state_before_its_own_return(self):
        entered, release = asyncio.Event(), asyncio.Event()
        a, b = AccountingDatabase(self.db), AccountingDatabase(self.db)
        async def wait(*args, **kwargs):
            entered.set()
            await release.wait()
            return await self.provider_read(*args, **kwargs)
        self.provider.side_effect = wait
        original = legacy._snapshot
        b_final_read = False
        async def changed_before_b_final(db, owner, number, *, expected=None):
            nonlocal b_final_read
            if db is b and expected is not None:
                b_final_read = True
                await self.db.order_review_workflows.update_one({}, {"$set": {"store_delivery_assignment_id": "new"}})
            return await original(db, owner, number, expected=expected)
        with patch.object(legacy, "_snapshot", changed_before_b_final):
            first = asyncio.create_task(legacy.read_legacy_completion(a, "owner", "3001"))
            await entered.wait()
            second = asyncio.create_task(legacy.read_legacy_completion(b, "owner", "3001"))
            await asyncio.sleep(0.05)
            release.set()
            results = await asyncio.gather(first, second, return_exceptions=True)
        self.assertTrue(b_final_read)
        self.assertIsInstance(results[1], shipping.ShippingLabelError)
        self.assertEqual(results[1].code, "shipping_snapshot_changed")
        self.assertEqual(self.provider.await_count, 1)
        self.assertFalse(legacy._flights)

    async def test_timeout_has_clear_error_and_no_persisted_confirmation(self):
        async def wait(*args, **kwargs):
            await asyncio.Event().wait()
        self.provider.side_effect = wait
        before = await self.snapshot()
        with patch.object(legacy, "READBACK_TIMEOUT_SECONDS", 0.08):
            with self.assertRaises(shipping.ShippingLabelError) as error:
                await self.read()
            self.assertEqual(error.exception.code, "completion_readback_timeout")
            self.assertFalse(legacy._flights)
        self.assertEqual(await self.snapshot(), before)

    async def test_provider_failure_is_sanitized_and_does_not_create_outbox(self):
        self.provider.side_effect = shipping.SallaError("private provider details", status_code=503)
        before = await self.snapshot()
        with self.assertRaises(shipping.ShippingLabelError) as error:
            await self.read()
        self.assertEqual(error.exception.code, "completion_readback_unavailable")
        self.assertNotIn("private", str(error.exception))
        self.assertEqual(await self.snapshot(), before)
