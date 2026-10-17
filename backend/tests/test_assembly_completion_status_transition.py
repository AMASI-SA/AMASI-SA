"""Completed status transitions, with real local transactions and synthetic IO."""
import unittest
from copy import deepcopy
from unittest.mock import AsyncMock, patch

import assembly_completion_delivery as delivery
import fulfillment_v2_routes as fulfillment
import operational_atomic
import preparation_piece_operations as pieces
from assembly_status_policy import invalidate_shipping_if_blocked
from assembly_status_policy import canonical_status
from salla_integration import auto_sync
import test_assembly_completion_delivery as fixture


class StatusTransitionTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.DeliveryTests.asyncSetUp
    asyncTearDown = fixture.DeliveryTests.asyncTearDown
    source_payload = fixture.DeliveryTests.source_payload
    webhook = fixture.DeliveryTests.webhook
    mark_piece = fixture.DeliveryTests.mark_piece
    on_hand = fixture.DeliveryTests.on_hand
    complete_review = fixture.DeliveryTests.complete_review
    workflow = fixture.DeliveryTests.workflow
    finish = fixture.DeliveryTests.finish

    async def reconcile_completed(self):
        payload = self.source_payload(number="local-assembly", status="completed")
        with patch.object(auto_sync, "call_salla", AsyncMock(return_value={"data": [payload]})), \
             patch.object(auto_sync, "_refresh_plan_b_status_snapshot", AsyncMock()):
            return await auto_sync._reconcile_status_page(self.db, "owner", page=1)

    def completed(self):
        return "internal", {"id": "internal", "reference_id": "local-assembly", "status": {"slug": "completed"}}

    async def test_reconciler_updates_all_status_aliases_before_invalidating_completion(self):
        original = await self.finish()
        await self.reconcile_completed()
        source = await self.db.unified_orders.find_one({"user_id": "owner", "order_number": "local-assembly"})
        self.assertEqual(canonical_status(source), "completed")
        current = await self.workflow()
        self.assertEqual(current[delivery.FIELD]["state"], "pending")
        self.assertEqual(current.get("shipping_status_epoch"), original.get("shipping_status_epoch"))

    async def test_reconciler_repairs_old_stale_raw_alias_even_when_top_level_completed(self):
        await self.finish()
        await self.db.unified_orders.update_one({"user_id": "owner"}, {"$set": {
            "order_status": "completed", "order_status_slug": "completed",
            "raw_by_source.salla_direct.status": {"slug": "completed", "name": "completed"},
        }})
        await self.reconcile_completed()
        source = await self.db.unified_orders.find_one({"user_id": "owner"})
        self.assertEqual(canonical_status(source), "completed")

    async def test_completed_during_fresh_get_requires_immediate_readback_not_status_post(self):
        await self.finish()
        reads = []
        async def resolve(*args):
            reads.append("GET")
            if len(reads) == 1:
                await self.db.unified_orders.update_one({"user_id": "owner"}, {"$set": {
                    "order_status": "completed", "order_status_slug": "completed",
                    "raw_by_source.salla_direct.status": {"slug": "completed", "name": "completed"},
                    "raw_by_source.salla_direct.status_slug": "completed",
                }})
                return "internal", {"id": "internal", "reference_id": "local-assembly", "status": {"slug": "in_progress"}}
            return self.completed()
        with patch.object(delivery.shipping, "_resolve_order", resolve), \
             patch.object(delivery.shipping, "call_salla", AsyncMock(side_effect=AssertionError("No status POST after local completed"))) as post, \
             patch.object(delivery.shipping, "refresh_shipping_label", AsyncMock(return_value={
                 "ready": True, "label_type": "store_courier", "print_data": {"courier_name": "Synthetic courier"}})):
            result = await delivery.resume(self.db, user_id="owner", order_number="local-assembly", manual=True)
        self.assertEqual(reads, ["GET", "GET"])
        self.assertTrue(result["order_status_completed"], result)
        self.assertTrue(result["ready"], result)
        self.assertFalse(result["requires_attention"], result)
        post.assert_not_awaited()
        self.assertEqual(await self.on_hand(), 16)

    async def test_actual_completed_webhook_retains_material_evidence_for_courier_readback(self):
        original = await self.finish()
        source = await self.db.unified_orders.find_one({"user_id": "owner"})
        payload = deepcopy(source["raw_by_source"]["salla_direct"])
        payload.update(status={"slug": "completed", "name": "completed"}, status_slug="completed",
                       updated_at="2026-10-18T00:00:00+00:00")
        await self.webhook(payload, event="order.updated")
        latest = await self.db.unified_orders.find_one({"user_id": "owner"})
        self.assertEqual(canonical_status(latest), "completed")
        self.assertEqual(delivery.source_fingerprint(latest), original[delivery.FIELD]["source_fingerprint"])
        with patch.object(delivery.shipping, "_resolve_order", AsyncMock(return_value=self.completed())), \
             patch.object(delivery.shipping, "call_salla", AsyncMock(side_effect=AssertionError("No POST"))) as post, \
             patch.object(delivery.shipping, "refresh_shipping_label", AsyncMock(return_value={
                 "ready": True, "label_type": "store_courier", "print_data": {"courier_name": "Synthetic courier"}})):
            result = await delivery.resume(self.db, user_id="owner", order_number="local-assembly", manual=True)
        self.assertTrue(result["ready"], result)
        post.assert_not_awaited()

    async def test_component_reconcile_window_defers_then_recovers_without_post(self):
        await self.finish()
        original = fulfillment.persist_component_source_snapshot
        observations = []
        async def persist_then_read(*args, **kwargs):
            persisted = await original(*args, **kwargs)
            intermediate = await delivery.resume(self.db, user_id="owner", order_number="local-assembly", manual=True)
            observations.append(intermediate)
            return persisted
        source = await self.db.unified_orders.find_one({"user_id": "owner"})
        payload = deepcopy(source["raw_by_source"]["salla_direct"])
        payload.update(status={"slug": "completed", "name": "completed"}, status_slug="completed",
                       updated_at="2026-10-18T00:00:00+00:00")
        with patch.object(fulfillment, "persist_component_source_snapshot", persist_then_read), \
             patch.object(delivery.shipping, "_resolve_order", AsyncMock(return_value=self.completed())) as resolve, \
             patch.object(delivery.shipping, "call_salla", AsyncMock(side_effect=AssertionError("No POST"))) as post, \
             patch.object(delivery.shipping, "refresh_shipping_label", AsyncMock(return_value={
                 "ready": True, "label_type": "store_courier", "print_data": {"courier_name": "Synthetic courier"}})) as label:
            await self.webhook(payload, event="order.updated")
            self.assertEqual(len(observations), 1)
            self.assertFalse(observations[0]["ready"])
            self.assertFalse(observations[0]["requires_attention"], observations[0])
            resolve.assert_not_awaited()
            label.assert_not_awaited()
            result = await delivery.resume(self.db, user_id="owner", order_number="local-assembly", manual=True)
        self.assertTrue(result["ready"], result)
        self.assertFalse(result["requires_attention"])
        post.assert_not_awaited()
        self.assertEqual(await self.on_hand(), 16)

    async def test_alias_repair_cannot_overwrite_new_raw_webhook_with_unchanged_top_status(self):
        await self.finish()
        original = operational_atomic.operational_owner
        entered = False
        async def newer_before_owner(db, owner, callback, **kwargs):
            nonlocal entered
            if not entered:
                entered = True
                await self.db.unified_orders.update_one({"user_id": "owner"}, {"$set": {
                    "raw_by_source.salla_direct.status": {"slug": "delivered"},
                    "raw_by_source.salla_direct.status_slug": "delivered",
                    "raw_by_source.salla_direct.updated_at": "2026-10-18T01:00:00+00:00",
                }})
            return await original(db, owner, callback, **kwargs)
        with patch.object(operational_atomic, "operational_owner", newer_before_owner):
            await self.reconcile_completed()
        source = await self.db.unified_orders.find_one({"user_id": "owner"})
        self.assertEqual(source["raw_by_source"]["salla_direct"]["status_slug"], "delivered")

    async def test_transient_source_invalidates_all_cached_labels_without_reviving_material_failure(self):
        await self.finish()
        await self.db.unified_orders.update_one({"user_id": "owner"}, {"$set": {"g47_salla_snapshot.component_pending": True}})
        for outbox in (None, {"version": 2, "state": "requires_attention", "error_code": "assembly_completion_evidence_changed"}):
            await self.db.order_review_workflows.update_one({"user_id": "owner"}, {"$set": {
                "carrier_label_ready": True, "carrier_label_url": "https://labels.test/old.pdf",
                "carrier_label_print_data": {"old": True},
            }, "$unset": {delivery.FIELD: ""}})
            if outbox is not None:
                await self.db.order_review_workflows.update_one({"user_id": "owner"}, {"$set": {delivery.FIELD: outbox}})
            await invalidate_shipping_if_blocked(self.db, "owner", "local-assembly")
            current = await self.workflow()
            self.assertFalse(current["carrier_label_ready"])
            self.assertIsNone(current["carrier_label_url"])
            self.assertIsNone(current["carrier_label_print_data"])
            if outbox is None:
                self.assertNotIn(delivery.FIELD, current)
            else:
                self.assertEqual(current[delivery.FIELD], outbox)

    async def test_completion_get_rechecks_permission_after_provider_wait(self):
        await self.finish()
        context = deepcopy(self.context)
        context.update(is_owner=False, permissions={"fulfillment.ready.read"}, responsibilities={"instant_ready"})
        async def revoke(*args, **kwargs):
            context["permissions"].clear()
            context["responsibilities"].clear()
            return {"ready": True}
        with patch.object(pieces, "_actor_context", AsyncMock(side_effect=lambda *args: deepcopy(context))), \
             patch.object(delivery, "read_status", revoke):
            response = await self.client.get("/preparation-work-v1/assembly/orders/local-assembly/completion")
        self.assertEqual(response.status_code, 403, response.text)

    async def test_light_reconcile_never_reopens_terminal_order_for_alias_repair(self):
        await self.finish()
        for status in ("delivered", "cancelled"):
            await self.db.unified_orders.update_one({"user_id": "owner"}, {"$set": {
                "order_status": status, "order_status_slug": status,
                "raw_by_source.salla_direct.status": {"slug": status},
                "raw_by_source.salla_direct.status_slug": status,
            }})
            before = await self.db.unified_orders.find_one({"user_id": "owner"})
            await self.reconcile_completed()
            self.assertEqual(await self.db.unified_orders.find_one({"user_id": "owner"}), before)
