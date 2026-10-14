"""Synthetic provider IO with real local Mongo transactions and ASGI routes."""
import asyncio
from datetime import timedelta
import os
import unittest
from copy import deepcopy
from unittest.mock import AsyncMock, patch

from motor.motor_asyncio import AsyncIOMotorClient

import assembly_completion_delivery as delivery
import preparation_piece_operations as pieces
import fulfillment_v2_routes as fulfillment
import test_review_local_assembly as local_fixture
import test_g47_component_lifecycle_integration as component_fixture


class DeliveryTests(unittest.IsolatedAsyncioTestCase):
    asyncTearDown = component_fixture.ComponentRouteTests.asyncTearDown
    source_payload = component_fixture.ComponentRouteTests.source_payload
    webhook = component_fixture.ComponentRouteTests.webhook
    mark_piece = component_fixture.ComponentRouteTests.mark_piece
    on_hand = component_fixture.ComponentRouteTests.on_hand
    complete_review = local_fixture.LocalAssemblyTests.complete_review
    workflow = local_fixture.LocalAssemblyTests.workflow

    async def asyncSetUp(self):
        await local_fixture.LocalAssemblyTests.asyncSetUp(self)
        await self.complete_review()
        await self.db.unified_orders.update_one({"user_id": "owner", "order_number": "local-assembly"},
            {"$set": {"raw_by_source.salla_direct.status": {"slug": "in_progress", "name": "in_progress"},
                      "status": "in_progress", "status_native": "in_progress", "order_status": "in_progress"}})
        current = await pieces._current_assembly_order(self.db, user_id="owner", order_number="local-assembly")
        self.assertTrue(pieces._assembly_in_progress(current), current)

    async def finish(self):
        for piece_id in self.ids:
            response = await self.mark_piece(piece_id)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertTrue(response.json()["piece_ready_confirmed"])
        workflow = await self.workflow()
        self.assertEqual(workflow["assembly_status"], "completed")
        self.assertEqual(workflow[delivery.FIELD]["state"], "pending")
        return workflow

    async def test_atomic_ready_concurrent_and_response_loss_readback(self):
        responses = await asyncio.gather(self.mark_piece(self.ids[0]), self.mark_piece(self.ids[0]))
        self.assertTrue(all(r.status_code in {200, 409} for r in responses))
        self.assertEqual(await self.on_hand(), 18)
        self.assertNotIn(delivery.FIELD, await self.workflow())
        # Discard the ready response to model network loss; only GET follows.
        await self.mark_piece(self.ids[1])
        readback = await self.client.get("/preparation-work-v1/assembly/search?q=local-assembly")
        self.assertEqual(readback.status_code, 200, readback.text)
        self.assertTrue(all(p["assembly_ready"] for p in readback.json()["pieces"]))
        self.assertEqual(await self.on_hand(), 16)
        self.assertIn(delivery.FIELD, await self.workflow())
        self.external.assert_not_awaited()

    async def test_revoked_after_search_and_in_progress_gate(self):
        # Restore actual permission reads, retain injected authenticated principal.
        from ai_store_access_contract import ROLE_ASSIGNMENTS
        self.actor.update(id="employee", role="employee", created_by="owner")
        await self.db[ROLE_ASSIGNMENTS].insert_one({"owner_user_id": "owner", "user_id": "employee",
            "enabled": True, "extra_permissions": ["fulfillment.ready.read", "fulfillment.pack.confirm"],
            "fulfillment_responsibilities": ["instant_ready", "packing"]})
        # Fixture patches were applied after import; get the original from patcher.
        actor_original = next(p.temp_original for p in self.patches if p.attribute == "_actor_context")
        with patch.object(pieces, "_actor_context", actor_original):
            search = await self.client.get("/preparation-work-v1/assembly/search?q=local-assembly")
            self.assertEqual(search.status_code, 200, search.text)
            await self.db[ROLE_ASSIGNMENTS].update_one({"user_id": "employee"}, {"$set": {"enabled": False}})
            response = await self.mark_piece(self.ids[0])
            self.assertEqual(response.status_code, 403, response.text)
        self.assertEqual(await self.on_hand(), 20)
        await self.db.unified_orders.update_one({"user_id": "owner"}, {"$set": {
            "raw_by_source.salla_direct.status": {"slug": "under_review", "name": "under_review"},
            "status": "under_review", "status_native": "under_review", "order_status": "under_review"}})
        response = await self.mark_piece(self.ids[0])
        self.assertEqual(response.status_code, 409, response.text)

    async def test_manual_twice_accepted_status_response_lost_no_replay(self):
        await self.finish()
        state = "in_progress"
        posts = []
        async def resolve(*args):
            return "internal", {"id": "internal", "reference_id": "local-assembly", "status": {"slug": state}}
        async def post(*args, **kwargs):
            nonlocal state
            self.assertTrue(kwargs["single_post_attempt"])
            posts.append(args[3])
            state = "completed"
            raise TimeoutError("synthetic response loss")
        with patch.object(delivery.shipping, "_resolve_order", resolve), \
             patch.object(delivery.shipping, "call_salla", post), \
             patch.object(delivery.shipping, "refresh_shipping_label", AsyncMock(return_value={"ready": True, "label_url": "https://example.test/current.pdf"})):
            first = await delivery.resume(self.db, user_id="owner", order_number="local-assembly", manual=True)
            self.assertFalse(first["ready"])
            self.assertEqual(first["error_code"], "completion_remote_precondition_unavailable")
            self.assertEqual(posts, [])
            state = "completed"  # Merchant completed in Salla, outside this worker.
            second = await delivery.resume(self.db, user_id="owner", order_number="local-assembly", manual=True)
            self.assertTrue(second["ready"])
            third = await delivery.resume(self.db, user_id="owner", order_number="local-assembly", manual=True)
            self.assertTrue(third["ready"])
        self.assertEqual(posts, [])

    async def test_crashed_worker_new_client_resumes_expired_lease_and_budget(self):
        await self.finish()
        await self.db[delivery.WORKFLOWS].update_one({"user_id": "owner"}, {"$set": {
            f"{delivery.FIELD}.claim": "dead-process", f"{delivery.FIELD}.lease_until": (delivery.now() - timedelta(seconds=1)).isoformat(),
            f"{delivery.FIELD}.status_attempted": True}})
        restarted = AsyncIOMotorClient(os.environ["MZ2_TEST_MONGO_URI"])
        try:
            with patch.object(delivery.shipping, "_resolve_order", AsyncMock(return_value=("internal", {
                "id": "internal", "reference_id": "local-assembly", "status": {"slug": "completed"}}))), \
                 patch.object(delivery.shipping, "call_salla", AsyncMock(side_effect=AssertionError("POST repeated"))), \
                 patch.object(delivery.shipping, "refresh_shipping_label", AsyncMock(return_value={"ready": True})):
                self.assertEqual(await delivery.run_once(restarted[self.db.name]), 1)
            self.assertEqual((await self.workflow())[delivery.FIELD]["state"], "confirmed")
            await self.db[delivery.WORKFLOWS].update_one({"user_id": "owner"}, {"$set": {
                f"{delivery.FIELD}.state": "pending", f"{delivery.FIELD}.attempts": delivery.MAX_ATTEMPTS,
                f"{delivery.FIELD}.read_attempts": delivery.MAX_READ_ATTEMPTS, f"{delivery.FIELD}.due_at": "",
                f"{delivery.FIELD}.lease_until": "", f"{delivery.FIELD}.claim": "dead-final-attempt"}})
            await self.db[delivery.LIMITS].update_many({}, {"$set": {"available_at": ""}})
            await delivery.run_once(restarted[self.db.name])
            self.assertEqual((await self.workflow())[delivery.FIELD]["state"], "requires_attention")
        finally:
            restarted.close()

    async def test_changed_source_blocks_post_and_failed_refresh_clears_confirmation(self):
        await self.finish()
        await self.db.unified_orders.update_one({"user_id": "owner"}, {"$set": {
            "raw_by_source.salla_direct.items.0.quantity": 3}})
        with patch.object(delivery.shipping, "_resolve_order", AsyncMock(return_value=("internal", {
            "id": "internal", "reference_id": "local-assembly", "status": {"slug": "in_progress"}}))), \
             patch.object(delivery.shipping, "call_salla", AsyncMock()) as post:
            result = await delivery.resume(self.db, user_id="owner", order_number="local-assembly", manual=True)
            self.assertEqual(result["error_code"], "assembly_completion_evidence_changed")
            post.assert_not_awaited()
        await self.db[delivery.WORKFLOWS].update_one({"user_id": "owner"}, {"$set": {
            f"{delivery.FIELD}.order_confirmed": True, "carrier_label_ready": True}})
        with patch.object(delivery.shipping, "_resolve_order", AsyncMock(side_effect=TimeoutError)):
            result = await delivery.resume(self.db, user_id="owner", order_number="local-assembly", manual=True)
            self.assertEqual(result["order_completion_status"], "pending")
            self.assertFalse(result["ready"])

    async def test_first_awb_once_response_loss_then_current_readback(self):
        await self.finish()
        label_ready = False
        posts = []
        async def post(*args, **kwargs):
            nonlocal label_ready
            self.assertEqual(args[3], "/shipments")
            self.assertTrue(kwargs["single_post_attempt"])
            posts.append(args[3])
            label_ready = True
            raise TimeoutError("synthetic AWB response lost")
        async def refresh(*args):
            return {"ready": label_ready, "shipment_id": "current"}
        with patch.object(delivery.shipping, "_resolve_order", AsyncMock(return_value=("internal", {
            "id": "internal", "reference_id": "local-assembly", "status": {"slug": "completed"}}))), \
             patch.object(delivery.shipping, "refresh_shipping_label", refresh), \
             patch.object(delivery.shipping, "_print_shipment_rows", AsyncMock(return_value=[{
                 "id": "current", "status": "draft", "type": "shipment"}])), \
             patch.object(delivery.shipping, "_create_payload", return_value={"order_id": "internal"}), \
             patch.object(delivery.shipping, "call_salla", post):
            first = await delivery.resume(self.db, user_id="owner", order_number="local-assembly", manual=True)
            self.assertFalse(first["ready"])
            self.assertEqual(first["error_code"], "completion_remote_precondition_unavailable")
            self.assertEqual(posts, [])
            label_ready = True  # Label reconciled externally, GET-only here.
            for _ in range(2):
                resumed = await delivery.resume(self.db, user_id="owner", order_number="local-assembly", manual=True)
                self.assertTrue(resumed["ready"])
        self.assertEqual(posts, [])

    async def test_outbox_failure_rolls_back_last_piece_and_stock(self):
        first = await self.mark_piece(self.ids[0])
        self.assertEqual(first.status_code, 200)
        with patch.object(delivery, "pending_operation", side_effect=RuntimeError("synthetic outbox failure")):
            result = await self.mark_piece(self.ids[1])
            self.assertEqual(result.status_code, 500)
        self.assertEqual(await self.on_hand(), 18)
        workflow = await self.workflow()
        self.assertNotIn(delivery.FIELD, workflow)
        self.assertNotEqual(workflow.get("assembly_status"), "completed")

    async def test_idempotent_last_piece_preserves_outbox_fence_and_legacy_entry(self):
        self.assertEqual((await self.mark_piece(self.ids[0])).status_code, 200)
        repeated = await asyncio.gather(self.mark_piece(self.ids[-1]), self.mark_piece(self.ids[-1]))
        self.assertTrue(all(r.status_code in {200, 409} for r in repeated))
        self.assertEqual(await self.on_hand(), 16)
        workflow = await self.workflow()
        self.assertEqual(workflow["revision"], workflow[delivery.FIELD]["workflow_revision"])
        with patch.object(delivery.shipping, "_resolve_order", AsyncMock(return_value=("internal", {
            "id": "internal", "reference_id": "local-assembly", "status": {"slug": "completed"}}))), \
             patch.object(delivery.shipping, "call_salla", AsyncMock(side_effect=AssertionError("POST repeated"))), \
             patch.object(delivery.shipping, "refresh_shipping_label", AsyncMock(return_value={"ready": True})):
            # The old shipping service entry point shares the durable lease.
            result = await delivery.shipping.issue_shipping_label(self.db, "owner", "local-assembly")
            self.assertTrue(result["ready"])

    async def test_cancelled_worker_after_dispatch_recovers_from_new_client_without_replay(self):
        await self.finish()
        # A deployment may inherit an attempt dispatched by the older worker.
        # Seed that durable marker; this implementation must never dispatch it.
        await self.db[delivery.WORKFLOWS].update_one({"user_id": "owner"}, {"$set": {
            f"{delivery.FIELD}.status_attempted": True}})
        entered = asyncio.Event()
        interrupted = False
        async def resolve(*args):
            nonlocal interrupted
            if not interrupted:
                interrupted = True
                entered.set()
                await asyncio.Future()
            return "internal", {"id": "internal", "reference_id": "local-assembly", "status": {"slug": "completed"}}
        with patch.object(delivery.shipping, "_resolve_order", resolve), \
             patch.object(delivery.shipping, "call_salla", AsyncMock()) as post, \
             patch.object(delivery.shipping, "refresh_shipping_label", AsyncMock(return_value={"ready": True})):
            task = asyncio.create_task(delivery.resume(self.db, user_id="owner", order_number="local-assembly"))
            await asyncio.wait_for(entered.wait(), 10)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            persisted = (await self.workflow())[delivery.FIELD]
            self.assertTrue(persisted["status_attempted"])
            self.assertTrue(persisted["claim"])
            await self.db[delivery.WORKFLOWS].update_one({"user_id": "owner"}, {"$set": {
                f"{delivery.FIELD}.lease_until": (delivery.now() - timedelta(seconds=1)).isoformat()}})
            restarted = AsyncIOMotorClient(os.environ["MZ2_TEST_MONGO_URI"])
            try:
                self.assertEqual(await delivery.run_once(restarted[self.db.name]), 1)
            finally:
                restarted.close()
            post.assert_not_awaited()
        self.assertEqual((await self.workflow())[delivery.FIELD]["state"], "confirmed")

    async def test_metadata_revision_changes_during_delivery_do_not_strand_completion(self):
        await self.finish()
        state = "completed"
        posts = []
        async def metadata_change():
            await self.db[delivery.WORKFLOWS].update_one({"user_id": "owner"}, {
                "$inc": {"revision": 1}, "$set": {"updated_at": delivery.now().isoformat()}})
        async def resolve(*args):
            await metadata_change()
            return "internal", {"id": "internal", "reference_id": "local-assembly", "status": {"slug": state}}
        async def post(*args, **kwargs):
            nonlocal state
            posts.append(args[3])
            state = "completed"
            await metadata_change()
        async def refresh(*args):
            await metadata_change()
            return {"ready": True, "label_url": "https://example.test/current.pdf"}
        with patch.object(delivery.shipping, "_resolve_order", resolve), \
             patch.object(delivery.shipping, "call_salla", post), \
             patch.object(delivery.shipping, "refresh_shipping_label", refresh):
            for _ in range(2):
                result = await delivery.resume(self.db, user_id="owner", order_number="local-assembly", manual=True)
                self.assertTrue(result["ready"], result)
                self.assertFalse(result["requires_attention"])
        self.assertEqual(posts, [])
        self.assertEqual((await self.workflow())[delivery.FIELD]["state"], "confirmed")

    async def test_material_workflow_change_during_label_read_cannot_publish_stale_confirmation(self):
        await self.finish()
        async def refresh(*args):
            await self.db[delivery.WORKFLOWS].update_one({"user_id": "owner"}, {
                "$set": {"items.0.quantity": 99}})
            return {"ready": True, "label_url": "https://example.test/stale.pdf"}
        with patch.object(delivery.shipping, "_resolve_order", AsyncMock(return_value=("internal", {
                "id": "internal", "reference_id": "local-assembly", "status": {"slug": "completed"}}))), \
             patch.object(delivery.shipping, "refresh_shipping_label", refresh), \
             patch.object(delivery.shipping, "call_salla", AsyncMock()) as post:
            result = await delivery.resume(self.db, user_id="owner", order_number="local-assembly", manual=True)
        self.assertFalse(result["ready"])
        self.assertEqual(result["error_code"], "assembly_completion_evidence_changed")
        self.assertNotEqual((await self.workflow())[delivery.FIELD]["state"], "confirmed")
        post.assert_not_awaited()


class DeliveryFingerprintTests(unittest.TestCase):
    def test_generated_shipping_metadata_is_not_material_evidence(self):
        base = {"raw_by_source": {"salla_direct": {"id": "i", "reference_id": "r",
            "items": [{"id": "x", "quantity": 1}], "status": {"slug": "in_progress"},
            "shipping": {"company_name": "Carrier", "company_code": "123", "tracking_number": None},
            "shipping_address": {"city": "Synthetic city"}}}}
        changed = deepcopy(base)
        changed["raw_by_source"]["salla_direct"]["status"] = {"slug": "completed"}
        changed["raw_by_source"]["salla_direct"]["shipping"].update(
            tracking_number="synthetic", shipment_id="new", status="ready", label_url="https://example.test/label.pdf")
        self.assertEqual(delivery.source_fingerprint(base), delivery.source_fingerprint(changed))
        changed["raw_by_source"]["salla_direct"]["shipping"]["company_code"] = "456"
        self.assertNotEqual(delivery.source_fingerprint(base), delivery.source_fingerprint(changed))


if __name__ == "__main__":
    unittest.main()
