"""Native item projection through real G47/owner transactions.

Run only against the explicitly supplied local replica set. Provider transport
and attribution are the existing fixture doubles; source/version persistence,
the refresh entry point, and inventory transaction machinery remain real.
"""
import unittest
from copy import deepcopy

import fulfillment_v2_routes as fulfillment
import preparation_piece_operations as pieces
from order_cost_authority import SNAPSHOTS, CORRECTIONS
from stock_component_consumption_service import PLANS, UNITS, LOCATIONS
from tests import test_g47_component_lifecycle_integration as fixture


class SallaProductsProjectionIntegrationTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.ComponentRouteTests.asyncSetUp
    asyncTearDown = fixture.ComponentRouteTests.asyncTearDown
    source_payload = fixture.ComponentRouteTests.source_payload
    webhook = fixture.ComponentRouteTests.webhook
    refresh = fixture.ComponentRouteTests.refresh

    @staticmethod
    def native_line(identity, quantity=1, choice="yes"):
        return {
            "id": identity, "product_id": "p", "sku": "SYN-P",
            "name": "Synthetic product", "quantity": quantity,
            "options": [{"product_option_id": "embroidery", "name": "Embroidery",
                         "value": {"id": choice, "name": choice}}],
        }

    async def source(self):
        return await self.db.unified_orders.find_one({
            "user_id": "owner", "order_number": "intake-order",
        })

    async def seed(self, lines):
        # The legacy cohort is already accepted; refreshing a projection must
        # not recreate preparation, reserve stock, or change frozen costs.
        await self.db.settings.update_one({"user_id": "owner"}, {"$unset": {"g47_inventory": ""}})
        payload = self.source_payload(status="completed")
        payload["items"] = lines
        result = await self.webhook(payload)
        self.assertTrue(result["synced"], result)
        lifecycle = await self.db[fulfillment.COMPONENT_LIFECYCLES].find_one({})
        self.assertTrue(lifecycle["accepted"])
        for collection in (SNAPSHOTS, CORRECTIONS):
            await self.db[collection].insert_one({
                "user_id": "owner", "order_number": "intake-order",
                "order_item_id": "line-b", "quantity": 1,
                "synthetic_immutable_marker": "preserve-historical-evidence",
            })
        await self.db[pieces.PIECES].insert_one({
            "id": "synthetic-existing-piece", "piece_id": "synthetic-existing-piece",
            "user_id": "owner", "order_number": "intake-order",
            "order_item_id": "line-b", "unit_index": 1,
            "status": pieces.PIECE_STATUS_READY_FOR_ASSEMBLY,
            "assembly_status": "pending",
        })
        self.protected = {name: await self.db[name].find({}).sort("_id", 1).to_list(100)
                          for name in (PLANS, UNITS, LOCATIONS, fulfillment.WORKFLOWS,
                                       pieces.PIECES, pieces.PIECE_EVENTS, SNAPSHOTS, CORRECTIONS)}
        return payload

    async def read_refresh(self, payload):
        return await self.refresh(
            payload, allow_auto_fulfillment=False,
            allow_delivery_observation=False, allow_billing_eligibility=False,
        )

    async def assert_protected_unchanged(self):
        for name, before in self.protected.items():
            self.assertEqual(await self.db[name].find({}).sort("_id", 1).to_list(100), before, name)

    def assert_matching_projection(self, source, expected):
        self.assertEqual([(row["order_item_id"], row["quantity"]) for row in source["products"]], expected)
        self.assertEqual([(row["id"], row["quantity"])
                          for row in source["raw_by_source"]["salla_direct"]["items"]], expected)

    async def test_three_to_one_stale_and_replay_keep_one_current_projection(self):
        original = await self.seed([self.native_line("line-a"), self.native_line("line-b"), self.native_line("line-c")])
        current = deepcopy(original)
        current.update(updated_at=fixture.LATER, items=[self.native_line("line-b")])
        result = await self.read_refresh(current)
        self.assertTrue(result["ok"], result)
        self.assert_matching_projection(await self.source(), [("line-b", 1)])

        before_stale = await self.source()
        result = await self.webhook(original, event="order.updated")
        self.assertFalse(result["synced"], result)
        self.assertEqual(result["reason"], "stale_salla_snapshot")
        self.assertEqual(await self.source(), before_stale)

        result = await self.read_refresh(current)
        self.assertTrue(result["ok"], result)
        replay = await self.source()
        self.assert_matching_projection(replay, [("line-b", 1)])
        self.assertEqual(replay["products"], before_stale["products"])
        self.assertEqual(replay["g47_salla_snapshot"]["source_updated_at"], fulfillment.component_provider_version(current))
        self.assertTrue(replay["g47_salla_snapshot"]["component_pending"])
        await self.assert_protected_unchanged()

    async def test_quantity_reduction_keeps_choices_bound_to_native_line_ids(self):
        original = await self.seed([self.native_line("line-a", 3), self.native_line("line-b", choice="no")])
        newer = deepcopy(original)
        newer["updated_at"] = fixture.LATER
        newer["items"][0].update(quantity=1, options=[])
        result = await self.read_refresh(newer)
        self.assertTrue(result["ok"], result)
        current = await self.source()
        self.assert_matching_projection(current, [("line-a", 1), ("line-b", 1)])
        self.assertEqual([line["options"][0]["value"] for line in current["products"]], ["yes", "no"])
        self.assertEqual([line["options"][0]["value"]["id"]
                          for line in current["raw_by_source"]["salla_direct"]["items"]], ["yes", "no"])

        # A replacement native line sharing the SKU must not inherit the
        # removed line's customization merely because the SKU is equal.
        replacement = deepcopy(newer)
        replacement["updated_at"] = "2026-09-26T14:00:00+00:00"
        replacement["items"][0]["id"] = "line-replacement"
        result = await self.read_refresh(replacement)
        self.assertTrue(result["ok"], result)
        current = await self.source()
        self.assert_matching_projection(current, [("line-replacement", 1), ("line-b", 1)])
        self.assertEqual(current["products"][0]["options"], [])
        self.assertEqual(current["products"][1]["options"][0]["value"], "no")
        native = current["raw_by_source"]["salla_direct"]["items"]
        self.assertEqual(native[0]["options"], [])
        self.assertEqual(native[1]["options"][0]["value"]["id"], "no")
        await self.assert_protected_unchanged()

    async def test_complete_api_empty_clears_last_items_in_both_views(self):
        original = await self.seed([self.native_line("line-b")])
        empty = deepcopy(original)
        empty.update(updated_at=fixture.LATER, items=[])
        result = await self.read_refresh(empty)
        self.assertTrue(result["ok"], result)
        self.assert_matching_projection(await self.source(), [])
        await self.assert_protected_unchanged()

    async def test_cancellation_guard_survives_projection_update_and_replay(self):
        original = await self.seed([self.native_line("line-a"), self.native_line("line-b")])
        undated_cancel = deepcopy(original)
        undated_cancel.pop("updated_at")
        undated_cancel["status"] = {"slug": "canceled"}
        undated_cancel["items"] = [self.native_line("line-b")]
        before = await self.source()
        result = await self.read_refresh(undated_cancel)
        self.assertFalse(result["ok"], result)
        self.assertTrue(result["blocked"], result)
        blocked = await self.source()
        self.assertEqual(blocked["products"], before["products"])
        self.assertEqual(blocked["raw_by_source"], before["raw_by_source"])
        self.assertTrue(blocked["g47_salla_snapshot"]["requires_authoritative_refresh"])

        confirmed_cancel = deepcopy(undated_cancel)
        confirmed_cancel["updated_at"] = fixture.LATER
        result = await self.read_refresh(confirmed_cancel)
        self.assertTrue(result["ok"], result)
        cancelled = await self.source()
        self.assert_matching_projection(cancelled, [("line-b", 1)])
        self.assertTrue(cancelled["g47_salla_snapshot"]["cancelled"])
        self.assertTrue(cancelled["g47_salla_snapshot"]["component_pending"])

        same_version_active = deepcopy(confirmed_cancel)
        same_version_active["status"] = {"slug": "completed"}
        same_version_active["items"] = original["items"]
        result = await self.read_refresh(same_version_active)
        self.assertTrue(result["skipped"], result)
        self.assertEqual(result["reason"], "stale_salla_snapshot")
        self.assertEqual(await self.source(), cancelled)
        await self.assert_protected_unchanged()
