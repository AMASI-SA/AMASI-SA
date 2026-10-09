"""Delivered is observational only; all writes use real replica-set transactions."""
import unittest
from unittest.mock import AsyncMock, patch

import preparation_piece_operations as operations
import test_g47_component_lifecycle_integration as fixture

WHEN = fixture.WHEN


class DeliveredAssemblyTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.ComponentRouteTests.asyncSetUp
    asyncTearDown = fixture.ComponentRouteTests.asyncTearDown
    source_payload = fixture.ComponentRouteTests.source_payload
    seed_physical = fixture.ComponentRouteTests.seed_physical
    mark_piece = fixture.ComponentRouteTests.mark_piece
    on_hand = fixture.ComponentRouteTests.on_hand

    async def seed(self, *, status="delivered", virtual=False, ready=False):
        self.context["permissions"].add("fulfillment.ready.read")
        await self.db.unified_orders.insert_one({
            "user_id": "owner", "order_number": "order-1", "order_date": WHEN,
            "order_status": status,
            "raw_by_source": {"salla_direct": self.source_payload(number="order-1", status=status)},
            "shipping_status": "delivered",  # Historical shipping is NOT the decision.
        })
        await self.seed_physical()
        await self.db[operations.WORKFLOWS].update_one({"order_number": "order-1"}, {"$set": {
            "ready_to_ship_source": "preparation_receipt", "revision": 0,
        }})
        if virtual:
            await self.db[operations.PIECES].delete_many({})
            await self.db[operations.WORKFLOWS].update_one({"order_number": "order-1"}, {"$set": {
                "operational_items": [{"operational_item_id": "virtual-1", "name": "Synthetic note",
                    "assembly_status": "ready" if ready else "pending", "blocks_order_completion": True}],
            }})
        elif ready:
            await self.db[operations.PIECES].update_many({}, {"$set": {"assembly_status": "ready"}})
        await operations.ensure_piece_operation_indexes(self.db)
        return "virtual-1" if virtual else "piece-1"

    async def snapshot(self):
        return {name: await self.db[name].find({}).sort("_id", 1).to_list(1000)
                for name in await self.db.list_collection_names()}

    async def reject_unchanged(self, *, virtual=False, ready=False):
        piece = await self.seed(virtual=virtual, ready=ready)
        before = await self.snapshot()
        with patch.object(operations, "_consume_piece_components", AsyncMock(side_effect=AssertionError("inventory write"))), \
             patch.object(operations, "sync_completed_carrier_label", AsyncMock(side_effect=AssertionError("shipping write"))), \
             patch.object(operations, "call_salla", AsyncMock(side_effect=AssertionError("Salla call"))):
            for _ in range(2):
                response = await self.mark_piece(piece)
                self.assertEqual(response.status_code, 409, response.text)
                self.assertEqual(response.json()["detail"]["code"], "assembly_order_delivered")
        self.assertEqual(await self.snapshot(), before)

    async def test_physical_delivered_rejected_without_writes(self):
        await self.reject_unchanged()

    async def test_virtual_delivered_rejected_without_writes(self):
        await self.reject_unchanged(virtual=True)

    async def test_ready_physical_retry_does_not_update_progress(self):
        await self.reject_unchanged(ready=True)

    async def test_ready_virtual_retry_does_not_update_progress(self):
        await self.reject_unchanged(virtual=True, ready=True)

    async def test_direct_assembly_virtual_piece_is_also_rejected(self):
        await self.seed(virtual=True)
        piece_id = operations._direct_assembly_piece_id("order-1", "line-1", 1)
        await self.db[operations.WORKFLOWS].update_one({"order_number": "order-1"}, {"$set": {
            "operational_items": [], "items": [{"order_item_id": "line-1", "product_id": "p",
                "quantity": 1, "preparation_route": "direct_assembly", "direct_assembly_piece_ids": [piece_id]}],
        }})
        before = await self.snapshot()
        response = await self.mark_piece(piece_id)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["detail"]["code"], "assembly_order_delivered")
        self.assertEqual(await self.snapshot(), before)

    async def test_authorization_still_blocks_direct_mark_ready(self):
        piece = await self.seed()
        self.context["is_owner"] = False
        self.context["permissions"] = set()
        before = await self.snapshot()
        response = await self.mark_piece(piece)
        self.assertEqual(response.status_code, 403, response.text)
        self.assertEqual(await self.snapshot(), before)

    async def test_virtual_search_is_read_only_without_erasing_ready_history(self):
        await self.seed(virtual=True, ready=True)
        before = await self.snapshot()
        response = await self.client.get("/preparation-work-v1/assembly/search", params={"q": "order-1"})
        self.assertEqual(response.status_code, 200, response.text)
        piece = response.json()["pieces"][0]
        self.assertTrue(piece["assembly_ready"])
        self.assertFalse(piece["can_mark_ready"])
        self.assertEqual(piece["assembly_blocker_code"], "assembly_order_delivered")
        self.assertEqual(await self.snapshot(), before)

    async def test_list_excludes_delivered_and_search_is_read_only(self):
        await self.seed()
        before = await self.snapshot()
        response = await self.client.get("/fulfillment-v2/ready-to-ship")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["items"], [])
        response = await self.client.get("/preparation-work-v1/assembly/search", params={"q": "order-1"})
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertTrue(result["history_only"])
        self.assertTrue(result["pieces"])
        for piece in result["pieces"]:
            self.assertFalse(piece["can_mark_ready"])
            self.assertEqual(piece["assembly_blocker_code"], "assembly_order_delivered")
        self.assertEqual(await self.snapshot(), before)

    async def test_historical_delivered_shipment_does_not_block_open_order(self):
        piece = await self.seed(status="in_progress", virtual=True)
        response = await self.client.get("/fulfillment-v2/ready-to-ship")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(response.json()["items"]), 1)
        response = await self.mark_piece(piece)
        self.assertEqual(response.status_code, 200, response.text)

    async def test_delivery_committed_before_transaction_read_blocks_execution(self):
        piece = await self.seed(status="in_progress", virtual=True)
        await self.db.unified_orders.update_one({"order_number": "order-1"}, {"$set": {
            "order_status": "delivered", "raw_by_source.salla_direct.status": {
                "slug": "delivered", "name": "delivered"},
        }})
        before = await self.snapshot()
        response = await self.mark_piece(piece)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["detail"]["code"], "assembly_order_delivered")
        self.assertEqual(await self.snapshot(), before)
