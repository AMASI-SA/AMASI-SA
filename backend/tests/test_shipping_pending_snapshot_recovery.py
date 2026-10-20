"""Real owner transactions; only provider transport is replaced."""
import unittest
from copy import deepcopy
from unittest.mock import AsyncMock, patch

from tests.test_g47_component_lifecycle_integration import ComponentRouteTests, LATER
from shipping_pending_snapshot_recovery import recover_pending_print_snapshot
from order_engine import salla_refresh, shipping_label_service as shipping
from salla_integration import auto_sync
from assembly_status_policy import source_status


class PendingPrintTests(ComponentRouteTests):
    async def seed_gap(self, *, options=None):
        await self.db.settings.update_one({"user_id": "owner"}, {"$unset": {"g47_inventory": ""}})
        payload = self.source_payload(status="completed")
        if options is not None:
            payload["items"][0]["options"] = options
        self.assertTrue((await self.webhook(payload))["synced"])
        selector = {"user_id": "owner", "order_number": payload["reference_id"]}
        await self.db.order_review_workflows.update_one(selector, {"$set": {
            "stage": "completed", "assembly_status": "completed",
            "assembly_piece_count": 2, "assembly_ready_piece_count": 2,
        }}, upsert=True)
        # The incident: accepted old snapshot, raw read advanced separately,
        # then a pending revision stranded without a lifecycle retry marker.
        await self.db.unified_orders.update_one(selector, {"$inc": {"g47_salla_snapshot.revision": 1},
            "$set": {"g47_salla_snapshot.component_pending": True,
                     "raw_by_source.salla_direct.updated_at": LATER}})
        payload["updated_at"] = LATER
        return selector, payload

    async def seed_catalog_rename(self):
        from order_engine.repository import MongoOrderRepository
        from order_engine.service import get_order
        from order_review_business_snapshot import build_snapshot

        options = [{"id": "option", "product_option_id": "catalog-option",
                    "name": "Product", "type": "radio",
                    "value": {"id": "choice", "name": "Old catalogue label",
                              "option_value": "Old catalogue label",
                              "price": {"amount": 0, "currency": "SAR"}}}]
        selector, payload = await self.seed_gap(options=options)
        source = await self.db.unified_orders.find_one(selector)
        order = await get_order(MongoOrderRepository(self.db), **selector)
        op = {"_id": "current-approval", **selector, "state": "completed",
              "completion_mode": "mezan_local_v1", "revision": 4,
              "workflow_fingerprint": "approved-workflow"}
        identity = {k: op.get(k) for k in ("_id", "user_id", "order_number", "revision", "workflow_fingerprint")}
        op["business_snapshot"] = build_snapshot(source, order, {}, identity=identity, schema_version=2)
        await self.db.order_review_completion_operations.insert_one(op)
        await self.db.order_review_workflows.update_one(selector, {"$set": {
            "completion_mode": "mezan_local_v1", "review_completion_operation_id": op["_id"]}})
        payload["items"][0]["options"][0]["value"]["name"] = "New catalogue label"
        payload["items"][0]["options"][0]["value"]["option_value"] = "New catalogue label"
        return selector, payload

    async def test_catalog_rename_retains_acceptance_without_stock_or_ready_writes(self):
        selector, payload = await self.seed_catalog_rename()
        before = await self.db.order_review_workflows.find_one(selector)
        approval = await self.db.order_review_completion_operations.find_one(selector)
        lifecycle = await self.db.mezan_component_order_lifecycle_v1.find_one(selector)
        stock = await self.db.warehouse_locations.find({}).to_list(100)
        await self.recover(payload)
        self.assertEqual(await source_status(self.db, "owner", payload["reference_id"]), "completed")
        after = await self.db.mezan_component_order_lifecycle_v1.find_one(selector)
        self.assertEqual(after["generation"], lifecycle["generation"])
        self.assertEqual(after["catalog_label_revalidation"]["previous_fingerprint"], lifecycle["source_fingerprint"])
        workflow_after = await self.db.order_review_workflows.find_one(selector)
        self.assertEqual({key: workflow_after[key] for key in before}, before)
        # Existing refresh invalidates only stale carrier proof while pending.
        self.assertEqual(set(workflow_after) - set(before), {
            "carrier_label_print_data", "carrier_label_ready", "carrier_label_url",
            "salla_order_status", "salla_order_status_verified_at"})
        self.assertEqual(await self.db.warehouse_locations.find({}).to_list(100), stock)
        self.assertEqual(await self.db.order_review_completion_operations.find_one(selector), approval)
        self.assertEqual(await self.db.mezan_component_consumption_plans_v1.count_documents({}), 0)
        await self.recover(payload)
        self.assertEqual(await self.db.mezan_component_order_lifecycle_v1.find_one(selector), after)

    async def test_rename_with_changed_quantity_remains_blocked(self):
        selector, payload = await self.seed_catalog_rename()
        payload["items"][0]["quantity"] = 3
        await self.recover(payload)
        self.assertIsNone(await source_status(self.db, "owner", payload["reference_id"]))

    async def test_second_catalog_rename_retains_same_review_lineage(self):
        selector, payload = await self.seed_catalog_rename()
        await self.recover(payload)
        first = await self.db.mezan_component_order_lifecycle_v1.find_one(selector)
        # A new authoritative snapshot arrives after the first print recovery.
        payload["updated_at"] = "2026-09-26T14:00:00+00:00"
        payload["items"][0]["options"][0]["value"].update(
            name="Another catalogue label", option_value="Another catalogue label")
        await self.refresh(payload, allow_auto_fulfillment=False,
                           allow_delivery_observation=False, allow_billing_eligibility=False)
        await self.recover(payload)
        self.assertEqual(await source_status(self.db, "owner", payload["reference_id"]), "completed")
        after = await self.db.mezan_component_order_lifecycle_v1.find_one(selector)
        self.assertEqual(after["catalog_label_revalidation"]["approved_fingerprint"],
                         first["catalog_label_revalidation"]["approved_fingerprint"])
        self.assertNotEqual(after["source_fingerprint"], first["source_fingerprint"])
        payload["updated_at"] = "2026-09-26T15:00:00+00:00"
        payload["items"][0]["options"][0]["value"].update(
            name="Old catalogue label", option_value="Old catalogue label")
        await self.refresh(payload, allow_auto_fulfillment=False,
                           allow_delivery_observation=False, allow_billing_eligibility=False)
        await self.recover(payload)
        self.assertEqual(await source_status(self.db, "owner", payload["reference_id"]), "completed")
        returned = await self.db.mezan_component_order_lifecycle_v1.find_one(selector)
        self.assertEqual(returned["source_fingerprint"],
                         first["catalog_label_revalidation"]["approved_fingerprint"])

    async def test_rename_with_changed_choice_remains_blocked(self):
        selector, payload = await self.seed_catalog_rename()
        payload["items"][0]["options"][0]["value"]["id"] = "different"
        await self.recover(payload)
        self.assertIsNone(await source_status(self.db, "owner", payload["reference_id"]))

    async def test_rename_with_superseded_review_remains_blocked(self):
        selector, payload = await self.seed_catalog_rename()
        await self.db.order_review_completion_operations.update_one(selector, {"$set": {"superseded_by": "later"}})
        await self.recover(payload)
        self.assertIsNone(await source_status(self.db, "owner", payload["reference_id"]))

    async def test_rename_does_not_change_receiving_revalidation(self):
        from fulfillment_v2_routes import revalidate_unchanged_component_snapshot
        selector, payload = await self.seed_catalog_rename()
        await self.db.unified_orders.update_one(selector, {"$set": {
            "raw_by_source.salla_direct": payload,
            "g47_salla_snapshot.source_updated_at": LATER}})
        source = await self.db.unified_orders.find_one(selector)
        result = await revalidate_unchanged_component_snapshot(
            self.db, **selector, source_revision=source["g47_salla_snapshot"]["revision"])
        self.assertFalse(result["accepted"])
        self.assertTrue((await self.db.unified_orders.find_one(selector))["g47_salla_snapshot"]["component_pending"])

    async def test_rename_with_component_plan_remains_blocked(self):
        selector, payload = await self.seed_catalog_rename()
        await self.db.mezan_component_consumption_plans_v1.insert_one({
            "user_id": "owner", "order_id": payload["reference_id"],
            "state": "accepted", "source_version": {"value": 1}})
        await self.recover(payload)
        self.assertIsNone(await source_status(self.db, "owner", payload["reference_id"]))

    async def recover(self, payload):
        async def provider(_db, _owner, method, path, **kwargs):
            self.assertEqual(method, "GET")
            if path == "/orders/items":
                return {"data": payload["items"]}
            if path == "/orders/" + payload["id"]:
                return {"data": payload}
            self.fail("Unexpected provider route " + path)
        async def bank(_db, _owner, details):
            return details
        with patch.object(salla_refresh, "call_salla", provider), \
             patch.object(salla_refresh, "_enrich_order_receiving_bank", bank), \
             patch("stock_component_consumption_service.reserve_component_stock", AsyncMock(side_effect=AssertionError("No stock"))), \
             patch("stock_component_consumption_service.release_component_stock", AsyncMock(side_effect=AssertionError("No stock"))), \
             patch("accounting_shipping_native_observer.observe_delivery", AsyncMock(side_effect=AssertionError("No accounting"))):
            await recover_pending_print_snapshot(self.db, "owner", payload["reference_id"])

    async def test_pending_unchanged_snapshot_recovers_without_ready_or_stock_changes(self):
        selector, payload = await self.seed_gap()
        before = await self.db.order_review_workflows.find_one(selector)
        await self.recover(payload)
        self.assertEqual(await source_status(self.db, "owner", payload["reference_id"]), "completed")
        source = await self.db.unified_orders.find_one(selector)
        lifecycle = await self.db.mezan_component_order_lifecycle_v1.find_one(selector)
        self.assertEqual(source["g47_salla_snapshot"]["revision"], lifecycle["snapshot_revision"])
        after = await self.db.order_review_workflows.find_one(selector)
        self.assertEqual({key: after[key] for key in before}, before)
        self.assertFalse(after.get("carrier_label_ready"))
        await shipping._assert_current_print_status(self.db, "owner", payload["reference_id"])

    async def test_pending_changed_quantity_stays_blocked(self):
        selector, payload = await self.seed_gap()
        payload["items"][0]["quantity"] = 3
        await self.recover(payload)
        self.assertIsNone(await source_status(self.db, "owner", payload["reference_id"]))
        with self.assertRaises(shipping.ShippingLabelError):
            await shipping._assert_current_print_status(self.db, "owner", payload["reference_id"])

    async def test_pending_incomplete_assembly_does_not_refresh(self):
        selector, payload = await self.seed_gap()
        await self.db.order_review_workflows.update_one(selector, {"$set": {"assembly_status": "pending"}})
        with patch.object(salla_refresh, "refresh_order_from_salla", AsyncMock()) as refresh:
            with self.assertRaises(shipping.ShippingLabelError):
                await recover_pending_print_snapshot(self.db, "owner", payload["reference_id"])
            refresh.assert_not_awaited()

    async def test_light_status_clock_does_not_replace_full_snapshot_clock(self):
        selector, payload = await self.seed_gap()
        source = await self.db.unified_orders.find_one(selector)
        old_clock = source["raw_by_source"]["salla_direct"]["updated_at"]
        light = {**deepcopy(payload), "updated_at": "2026-09-26T14:00:00+00:00",
                 "status": {"slug": "cancelled", "name": "cancelled"}}
        with patch.object(auto_sync, "call_salla", AsyncMock(return_value={"data": [light]})), \
             patch.object(auto_sync, "_refresh_plan_b_status_snapshot", AsyncMock(return_value={})):
            await auto_sync._reconcile_status_page(self.db, "owner", page=1)
        after = await self.db.unified_orders.find_one(selector)
        self.assertEqual(after["raw_by_source"]["salla_direct"]["updated_at"], old_clock)
        self.assertEqual(after["order_status_slug"], "cancelled")
        self.assertIsNone(await source_status(self.db, "owner", payload["reference_id"]))

    async def assert_late_cancellation_blocks_refresh(self, *, light):
        selector, payload = await self.seed_gap()
        cancelled_at = "2026-09-26T14:00:00+00:00"
        cancellation = {**deepcopy(payload), "updated_at": cancelled_at,
                        "status": {"slug": "cancelled", "name": "cancelled"}}

        async def provider(_db, _owner, method, path, **kwargs):
            self.assertEqual(method, "GET")
            if path == "/orders/items":
                return {"data": payload["items"]}
            self.assertEqual(path, "/orders/" + payload["id"])
            # The cancellation arrives after the initial pending/completion
            # check but before the older provider response is persisted.
            if light:
                with patch.object(auto_sync, "call_salla", AsyncMock(return_value={"data": [cancellation]})), \
                     patch.object(auto_sync, "_refresh_plan_b_status_snapshot", AsyncMock(return_value={})):
                    await auto_sync._reconcile_status_page(self.db, "owner", page=1)
            else:
                self.assertTrue((await self.webhook(cancellation, event="order.cancelled"))["synced"])
            return {"data": payload}

        async def bank(_db, _owner, details):
            return details

        with patch.object(salla_refresh, "call_salla", provider), \
             patch.object(salla_refresh, "_enrich_order_receiving_bank", bank):
            await recover_pending_print_snapshot(self.db, "owner", payload["reference_id"])
        current = await self.db.unified_orders.find_one(selector)
        self.assertIn(current["order_status_slug"], {"cancelled", "canceled"})
        self.assertIsNone(await source_status(self.db, "owner", payload["reference_id"]))
        with self.assertRaises(shipping.ShippingLabelError):
            await shipping._assert_current_print_status(self.db, "owner", payload["reference_id"])

    async def test_late_light_cancellation_blocks_older_full_response(self):
        await self.assert_late_cancellation_blocks_refresh(light=True)

    async def test_late_fenced_cancellation_blocks_older_full_response(self):
        await self.assert_late_cancellation_blocks_refresh(light=False)


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(PendingPrintTests(name) for name in PendingPrintTests.__dict__ if name.startswith("test_"))
