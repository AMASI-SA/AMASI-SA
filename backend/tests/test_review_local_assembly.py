"""Local Review through assembly, using real Mongo/fulfillment/component writes."""
from copy import deepcopy
import unittest
from unittest.mock import AsyncMock, patch

import fulfillment_v2_routes as fulfillment
import order_review_completion as completion
import order_review_routes as review
import preparation_piece_operations as pieces
import test_g47_component_lifecycle_integration as fixture
from review_local_policy import LOCAL_COMPLETION_MODE
from order_engine.models import PaymentDTO
from stock_component_consumption_service import LOCATIONS, UNITS


class LocalAssemblyTests(unittest.IsolatedAsyncioTestCase):
    asyncTearDown = fixture.ComponentRouteTests.asyncTearDown
    source_payload = fixture.ComponentRouteTests.source_payload
    webhook = fixture.ComponentRouteTests.webhook
    mark_piece = fixture.ComponentRouteTests.mark_piece
    on_hand = fixture.ComponentRouteTests.on_hand

    async def asyncSetUp(self):
        await fixture.ComponentRouteTests.asyncSetUp(self)
        self.context["permissions"].add("fulfillment.ready.read")
        await self.db[LOCATIONS].insert_one({
            "id": "base-products", "user_id": "owner", "warehouse_id": "wh", "state": "occupied",
            "occupancy": {"total_quantity": 10, "items": [{
                "product_id": "mp", "sku": "SYN-P", "quantity": 10,
                "source_type": "purchase_invoice", "preparation_state": "requires_preparation",
            }]},
        })
        self.external = AsyncMock(side_effect=AssertionError("local review/assembly called Salla"))
        for replacement in (
            patch.object(review, "call_salla", self.external),
            patch.object(review, "_sync_salla_reviewed", self.external),
            patch.object(review, "refresh_order_from_salla", self.external),
            patch.object(pieces, "call_salla", self.external),
        ):
            replacement.start()
            self.patches.append(replacement)

    async def complete_review(self, *, mixed=False, operational=False):
        payload = self.source_payload(number="local-assembly")
        if mixed:
            payload["items"].append({**payload["items"][0], "id": "supplier-line"})
        self.assertTrue((await self.webhook(payload))["synced"])
        current = await pieces._current_assembly_order(self.db, user_id="owner", order_number="local-assembly")
        self.direct_line_id = current.items[0].order_item_id
        self.supplier_line_id = current.items[1].order_item_id if mixed else None
        await self.db[completion.WORKFLOWS].update_one(
            {"user_id": "owner", "order_number": "local-assembly"},
            {"$set": {
                "stage": "pending_review", "revision": 0,
                "items": [{"order_item_id": self.direct_line_id, "preparation_route": "direct_assembly",
                           "product_id": "p", "quantity": 2}],
                "operational_items": ([{"operational_item_id": "operational-piece", "name": "Synthetic note",
                                        "blocks_order_completion": True}] if operational else []),
            }}, upsert=True,
        )
        response = await self.client.post(
            "/order-reviews-v1/local-assembly/complete", json={"expected_revision": 0},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["completion_mode"], LOCAL_COMPLETION_MODE)
        self.assertEqual(response.json()["stage"], "reviewed")
        self.assertEqual(await self.db[completion.EVENTS].count_documents({"event_type": "order_review_completed"}), 1)
        workflow = await self.workflow()
        self.assertEqual(workflow["stage"], "reviewed")
        self.external.assert_not_awaited()
        self.ids = workflow["items"][0]["direct_assembly_piece_ids"]
        self.assertEqual(len(self.ids), 2)
        return workflow

    async def workflow(self):
        return await self.db[completion.WORKFLOWS].find_one({"user_id": "owner", "order_number": "local-assembly"})

    async def mark(self, piece_id, *, status=200):
        response = await self.mark_piece(piece_id)
        self.assertEqual(response.status_code, status, response.text)
        self.external.assert_not_awaited()
        return response.json()

    async def set_address(self, *, complete):
        address = {"city": "Synthetic city"}
        if complete:
            address["street"] = "Synthetic street"
        await self.db.unified_orders.update_one(
            {"user_id": "owner", "order_number": "local-assembly"},
            {"$set": {"raw_by_source.salla_direct.shipping_address": address}},
        )

    async def test_real_local_review_direct_assembly_starts_then_completes(self):
        await self.complete_review()
        board = await self.client.get("/preparation-work-v1/assembly/orders?state=in_progress")
        self.assertEqual(board.status_code, 200, board.text)
        self.assertEqual([row["order_number"] for row in board.json()["items"]], ["local-assembly"])
        search = await self.client.get("/preparation-work-v1/assembly/search?q=local-assembly")
        self.assertEqual(search.status_code, 200, search.text)
        self.assertTrue(all(row["can_mark_ready"] for row in search.json()["pieces"]))
        first = await self.mark(self.ids[0])
        self.assertFalse(first["progress"]["order_completed"])
        self.assertEqual((await self.workflow())["stage"], "in_progress")
        self.assertEqual(await self.on_hand(), 18)
        final = await self.mark(self.ids[1])
        self.assertTrue(final["progress"]["order_completed"])
        self.assertEqual((await self.workflow())["stage"], "completed")
        self.assertEqual(await self.on_hand(), 16)
        self.assertEqual(await self.db[UNITS].count_documents({"state": "consumed"}), 2)
        self.assertEqual(await self.db[pieces.SHIPPING_BATCHES].count_documents({}), 1)

    async def test_missing_address_retains_ready_work_and_retry_finishes_once(self):
        await self.complete_review()
        await self.set_address(complete=False)
        for piece_id in self.ids:
            result = await self.mark(piece_id)
            self.assertFalse(result["progress"]["order_completed"])
        self.assertEqual((await self.workflow())["stage"], "in_progress")
        self.assertEqual(await self.db[pieces.SHIPPING_BATCHES].count_documents({}), 0)
        search = await self.client.get("/preparation-work-v1/assembly/search?q=local-assembly")
        self.assertEqual(search.status_code, 200, search.text)
        self.assertTrue(all(row["assembly_ready"] and row["can_mark_ready"] for row in search.json()["pieces"]))
        await self.set_address(complete=True)
        retried = await self.mark(self.ids[-1])
        self.assertTrue(retried["idempotent"])
        self.assertTrue(retried["progress"]["order_completed"])
        await self.mark(self.ids[-1])
        self.assertEqual(await self.on_hand(), 16)
        self.assertEqual(await self.db[pieces.SHIPPING_BATCHES].count_documents({}), 1)
        self.assertEqual(await self.db[pieces.PIECE_EVENTS].count_documents({}), 2)

    async def test_mixed_order_requires_every_supplier_unit_before_completion(self):
        await self.complete_review(mixed=True)
        for piece_id in self.ids:
            result = await self.mark(piece_id)
        self.assertFalse(result["progress"]["order_completed"])
        self.assertEqual((await self.workflow())["stage"], "in_progress")
        # A partial receipt must not turn absence of the other supplier unit
        # into evidence that the entire order finished preparation.
        supplier_piece = {
            "piece_id": "supplier-1", "user_id": "owner", "order_number": "local-assembly",
            "order_item_id": self.supplier_line_id, "unit_index": 1,
            "status": pieces.PIECE_STATUS_READY_FOR_ASSEMBLY, "assembly_status": "pending",
            "supplier_dispatch_status": "received", "preparation_receipt_status": "received",
        }
        await self.db[pieces.PIECES].insert_one(dict(supplier_piece))
        await self.db[completion.WORKFLOWS].update_one({"order_number": "local-assembly"}, {"$set": {"stage": "ready_to_ship"}})
        result = await self.mark("supplier-1")
        self.assertFalse(result["progress"]["order_completed"])
        self.assertEqual(await self.db[pieces.SHIPPING_BATCHES].count_documents({}), 0)
        await self.db[pieces.PIECES].insert_one({**supplier_piece, "piece_id": "supplier-2", "unit_index": 2})
        result = await self.mark("supplier-2")
        self.assertTrue(result["progress"]["order_completed"])
        self.assertEqual(await self.on_hand(), 12)

    async def test_invalid_local_proofs_and_unknown_modes_block_both_write_paths(self):
        workflow = await self.complete_review()
        await self.db[completion.WORKFLOWS].update_one({"order_number": "local-assembly"}, {"$set": {"stage": "in_progress"}})
        await self.db[pieces.PIECES].insert_one({
            "piece_id": "physical-proof", "user_id": "owner", "order_number": "local-assembly",
            "order_item_id": self.direct_line_id, "unit_index": 1, "status": pieces.PIECE_STATUS_READY_FOR_ASSEMBLY,
            "assembly_status": "pending",
        })
        operation = await self.db[completion.OPERATIONS].find_one({"_id": workflow["review_completion_operation_id"]})
        current = await pieces._current_assembly_order(self.db, user_id="owner", order_number="local-assembly")
        current = current.model_copy(update={"status": "in_progress", "status_native": "in_progress"})
        mutations = [{"state": "provider_confirmed"}, {"user_id": "other"}, {"order_number": "other"},
                     {"completion_mode": "future"}, {"superseded_by": "new-operation"}]
        with patch.object(pieces, "_current_assembly_order", AsyncMock(return_value=current)):
            for mutation in mutations:
                with self.subTest(mutation=mutation):
                    await self.db[completion.OPERATIONS].replace_one({"_id": operation["_id"]}, {**deepcopy(operation), **mutation})
                    for piece_id in (self.ids[0], "physical-proof"):
                        await self.mark(piece_id, status=409)
            await self.db[completion.OPERATIONS].replace_one({"_id": operation["_id"]}, operation)
            for mode in ("future", ""):
                await self.db[completion.WORKFLOWS].update_one({"order_number": "local-assembly"}, {"$set": {"completion_mode": mode}})
                for piece_id in (self.ids[0], "physical-proof"):
                    result = await self.mark(piece_id, status=409)
                    self.assertEqual(result["detail"]["code"], "review_completion_mode_unknown")
        self.assertEqual(await self.on_hand(), 20)
        self.assertEqual(await self.db[pieces.PIECE_EVENTS].count_documents({}), 0)

    async def test_ready_retry_rechecks_proof_payment_cancellation_and_source_fence(self):
        workflow = await self.complete_review()
        await self.mark(self.ids[0])
        op_id = workflow["review_completion_operation_id"]
        await self.db[completion.OPERATIONS].update_one({"_id": op_id}, {"$set": {"superseded_by": "new-operation"}})
        await self.mark(self.ids[0], status=409)
        await self.db[completion.OPERATIONS].update_one({"_id": op_id}, {"$unset": {"superseded_by": ""}})
        current = await pieces._current_assembly_order(self.db, user_id="owner", order_number="local-assembly")
        for changed in (
            current.model_copy(update={"status": "cancelled", "status_native": "cancelled"}),
            current.model_copy(update={"payment": PaymentDTO(method="card", status="pending", collection_status="unpaid")}),
        ):
            with patch.object(pieces, "_current_assembly_order", AsyncMock(return_value=changed)):
                await self.mark(self.ids[0], status=409)
        await self.db.unified_orders.update_one({"order_number": "local-assembly"}, {"$set": {"g47_salla_snapshot.component_pending": True}})
        result = await self.mark(self.ids[0], status=409)
        self.assertEqual(result["detail"]["code"], "component_execution_blocked")
        self.assertEqual(await self.on_hand(), 18)
        self.assertEqual(await self.db[pieces.PIECE_EVENTS].count_documents({}), 1)

    async def test_operational_last_piece_observes_source_fence(self):
        await self.complete_review(operational=True)
        for piece_id in self.ids:
            result = await self.mark(piece_id)
            self.assertFalse(result["progress"]["order_completed"])
        await self.db.unified_orders.update_one({"order_number": "local-assembly"}, {"$set": {"g47_salla_snapshot.component_pending": True}})
        await self.mark("operational-piece", status=409)
        self.assertEqual(await self.db[pieces.SHIPPING_BATCHES].count_documents({}), 0)
        await self.db.unified_orders.update_one({"order_number": "local-assembly"}, {"$set": {"g47_salla_snapshot.component_pending": False}})
        result = await self.mark("operational-piece")
        self.assertTrue(result["progress"]["order_completed"])

    async def test_physical_reviewed_and_unreceived_pieces_remain_blocked(self):
        await self.complete_review(mixed=True)
        await self.db[pieces.PIECES].insert_one({
            "piece_id": "not-received", "user_id": "owner", "order_number": "local-assembly",
            "order_item_id": self.supplier_line_id, "unit_index": 1, "status": "assigned",
            "supplier_dispatch_status": "dispatched", "assembly_status": "pending",
        })
        await self.mark("not-received", status=409)
        await self.mark(self.ids[0])
        result = await self.mark("not-received", status=409)
        self.assertEqual(result["detail"]["code"], "assembly_piece_supplier_receipt_required")
        self.assertEqual(await self.on_hand(), 18)


if __name__ == "__main__":
    unittest.main()
