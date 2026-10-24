"""Real local owner transactions; sync must preserve component provenance."""
import unittest
from copy import deepcopy
from unittest.mock import AsyncMock, patch

import fulfillment_v2_routes as fulfillment
from salla_integration import sync
from stock_component_consumption_service import PLANS, UNITS, LOCATIONS
from tests import test_g47_component_lifecycle_integration as fixture


class SyncComponentSnapshotTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.ComponentRouteTests.asyncSetUp
    asyncTearDown = fixture.ComponentRouteTests.asyncTearDown
    source_payload = fixture.ComponentRouteTests.source_payload
    webhook = fixture.ComponentRouteTests.webhook

    async def seed(self):
        await self.db.settings.update_one({"user_id": "owner"}, {"$unset": {"g47_inventory": ""}})
        payload = self.source_payload(status="completed")
        self.assertTrue((await self.webhook(payload))["synced"])
        lifecycle = await self.db[fulfillment.COMPONENT_LIFECYCLES].find_one({})
        self.assertTrue(lifecycle["accepted"])
        self.inventory = {name: await self.db[name].find({}).to_list(100)
                          for name in (PLANS, UNITS, LOCATIONS, fulfillment.WORKFLOWS)}
        return payload

    async def resync(self, payload):
        with patch.object(sync, "_fetch_salla_order_details", AsyncMock(return_value=payload)), \
             patch.object(sync, "_refresh_plan_b_status_snapshot", AsyncMock(return_value={})), \
             patch.object(sync, "_record_order_adjustment", AsyncMock(return_value=None)), \
             patch("product_costs.attach_cost_to_order_doc", AsyncMock(return_value={})):
            return await sync.resync_single_order(self.db, "owner", payload["reference_id"])

    async def assert_inventory_unchanged(self):
        for name, before in self.inventory.items():
            self.assertEqual(await self.db[name].find({}).to_list(100), before, name)

    async def test_single_resync_newer_unchanged_contract_advances_acceptance(self):
        payload = await self.seed()
        payload["updated_at"] = "2026-09-26T12:00:10+00:00"
        result = await self.resync(payload)
        self.assertTrue(result["ok"], result)
        source = await self.db.unified_orders.find_one({"user_id": "owner"})
        lifecycle = await self.db[fulfillment.COMPONENT_LIFECYCLES].find_one({})
        self.assertEqual(source["g47_salla_snapshot"]["source_updated_at"], fulfillment.component_provider_version(payload))
        self.assertEqual(lifecycle["snapshot_revision"], source["g47_salla_snapshot"]["revision"])
        self.assertFalse(source["g47_salla_snapshot"]["component_pending"])
        await self.assert_inventory_unchanged()

    async def test_single_resync_material_change_stays_pending(self):
        payload = await self.seed()
        payload["updated_at"] = "2026-09-26T12:00:10+00:00"
        payload["items"][0]["quantity"] = 3
        result = await self.resync(payload)
        self.assertTrue(result["ok"], result)
        source = await self.db.unified_orders.find_one({"user_id": "owner"})
        self.assertTrue(source["g47_salla_snapshot"]["component_pending"])
        await self.assert_inventory_unchanged()

    async def test_single_resync_stale_cannot_replace_newer_snapshot(self):
        payload = await self.seed()
        before = await self.db.unified_orders.find_one({"user_id": "owner"})
        stale = deepcopy(payload)
        stale["updated_at"] = "2026-09-26T11:59:00+00:00"
        stale["items"][0]["quantity"] = 7
        result = await self.resync(stale)
        self.assertFalse(result["ok"], result)
        self.assertTrue(result.get("skipped"), result)
        self.assertEqual(await self.db.unified_orders.find_one({"user_id": "owner"}), before)
        await self.assert_inventory_unchanged()

    async def test_batch_sync_advances_unchanged_contract_without_stock_work(self):
        payload = await self.seed()
        payload["updated_at"] = "2026-09-26T12:00:10+00:00"
        with patch.object(sync, "call_salla", AsyncMock(return_value={
            "data": [payload], "pagination": {"totalPages": 1, "currentPage": 1}})), \
             patch.object(sync, "_enrich_order_receiving_bank", AsyncMock(return_value=payload)), \
             patch.object(sync, "_refresh_plan_b_status_snapshot", AsyncMock(return_value={})):
            result = await sync.run_orders_sync(self.db, "owner")
        self.assertEqual(result["errors_count"], 0, result)
        source = await self.db.unified_orders.find_one({"user_id": "owner"})
        self.assertEqual(source["g47_salla_snapshot"]["source_updated_at"], fulfillment.component_provider_version(payload))
        self.assertFalse(source["g47_salla_snapshot"]["component_pending"])
        await self.assert_inventory_unchanged()

    async def test_single_resync_cancellation_stays_blocked_without_stock_work(self):
        payload = await self.seed()
        payload.update(updated_at="2026-09-26T12:00:10+00:00", status={"slug": "canceled"})
        result = await self.resync(payload)
        self.assertTrue(result["ok"], result)
        source = await self.db.unified_orders.find_one({"user_id": "owner"})
        self.assertTrue(source["g47_salla_snapshot"]["component_pending"])
        self.assertTrue(source["g47_salla_snapshot"]["cancelled"])
        await self.assert_inventory_unchanged()

    async def test_undated_cancellation_blocks_without_replacing_canonical_payload(self):
        payload = await self.seed()
        source = await self.db.unified_orders.find_one({"user_id": "owner"})
        payload.pop("updated_at")
        payload["status"] = {"slug": "canceled"}
        result = await self.resync(payload)
        self.assertFalse(result["ok"], result)
        current = await self.db.unified_orders.find_one({"user_id": "owner"})
        self.assertEqual(current["raw_by_source"], source["raw_by_source"])
        self.assertTrue(current["g47_salla_snapshot"]["requires_authoritative_refresh"])
        await self.assert_inventory_unchanged()


if __name__ == "__main__":
    unittest.main()

    async def test_payment_webhook_clears_balance_and_stale_event_cannot_restore_it(self):
        payload = self.source_payload(status="in_progress")
        payload["payment_method"] = "mada"
        payload["payment_actions"] = {"remaining_action": {"has_remaining_amount": True,
            "paid_amount": {"amount": 80}, "remaining_amount": {"amount": 20}}}
        self.assertTrue((await self.webhook(payload))["synced"])
        paid = deepcopy(payload)
        paid["updated_at"] = "2026-09-26T12:01:00+00:00"
        paid["payment_actions"]["remaining_action"] = {"has_remaining_amount": False,
            "paid_amount": {"amount": 100}, "remaining_amount": None}
        result = await self.webhook(paid, event="order.payment.updated")
        self.assertTrue(result["synced"], result)
        row = await self.db.unified_orders.find_one({"user_id": "owner"})
        self.assertEqual(row["remaining_amount"], 0)
        self.assertEqual(row["paid_amount"], 100)
        self.assertEqual(row["payment_collection_status"], "paid")
        stale = await self.webhook(payload, event="order.payment.updated")
        self.assertFalse(stale["synced"])
        self.assertEqual((await self.db.unified_orders.find_one({"user_id": "owner"}))["remaining_amount"], 0)
