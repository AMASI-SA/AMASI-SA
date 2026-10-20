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
    async def seed_gap(self):
        await self.db.settings.update_one({"user_id": "owner"}, {"$unset": {"g47_inventory": ""}})
        payload = self.source_payload(status="completed")
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
