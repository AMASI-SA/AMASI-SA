"""Legacy completion recovery: local Mongo, synthetic provider, no live IO."""
from unittest.mock import AsyncMock, patch
import unittest
import asyncio
from fastapi import HTTPException

import assembly_completion_delivery as delivery
import test_assembly_completion_delivery as fixture


class LegacyRecoveryContractTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.DeliveryTests.asyncSetUp
    asyncTearDown = fixture.DeliveryTests.asyncTearDown
    source_payload = fixture.DeliveryTests.source_payload
    webhook = fixture.DeliveryTests.webhook
    mark_piece = fixture.DeliveryTests.mark_piece
    on_hand = fixture.DeliveryTests.on_hand
    complete_review = fixture.DeliveryTests.complete_review
    workflow = fixture.DeliveryTests.workflow
    finish = fixture.DeliveryTests.finish
    async def test_legacy_missing_operation_does_not_advertise_automatic_progress(self):
        await self.finish()
        await self.db.order_review_workflows.update_one(
            {"user_id": "owner"}, {"$unset": {delivery.FIELD: ""}})
        result = await self.client.get(
            "/preparation-work-v1/assembly/search?q=local-assembly")
        self.assertEqual(result.status_code, 200, result.text)
        self.assertTrue(result.json()["requires_attention"])
        self.assertEqual(result.json()["completion_state"], "recovery_required")
        self.assertEqual(await delivery.run_once(self.db), 0)
        self.external.assert_not_awaited()

    async def test_explicit_legacy_resume_can_finish_without_repeating_ready(self):
        await self.finish()
        await self.db.order_review_workflows.update_one(
            {"user_id": "owner"}, {"$unset": {delivery.FIELD: ""}})
        stock_before = await self.on_hand()
        state = "in_progress"
        async def resolve(*args):
            return "local-assembly", {"id": "local-assembly", "reference_id": "local-assembly",
                                "status": {"slug": state}}
        async def post(*args, **kwargs):
            nonlocal state
            self.assertEqual(args[2:4], ("POST", "/orders/local-assembly/status"))
            self.assertEqual(kwargs["json"], {"slug": "completed"})
            state = "completed"
        with patch.object(delivery.shipping, "_resolve_order", resolve), \
             patch.object(delivery.shipping, "call_salla", AsyncMock(side_effect=post)) as dispatch, \
             patch.object(delivery.shipping, "refresh_shipping_label", AsyncMock(return_value={"ready": True})):
            first = await delivery.resume(self.db, user_id="owner", order_number="local-assembly", manual=True, authorize=AsyncMock())
            self.assertFalse(first["requires_attention"])
            dispatch.assert_awaited_once()
            second = await delivery.resume(self.db, user_id="owner", order_number="local-assembly", manual=True, authorize=AsyncMock())
            self.assertTrue(second["ready"])
            dispatch.assert_awaited_once()
        self.assertEqual(await self.on_hand(), stock_before)

    async def test_legacy_changed_component_input_never_dispatches(self):
        from stock_component_consumption_service import PLANS
        await self.finish()
        await self.db.order_review_workflows.update_one({}, {"$unset": {delivery.FIELD: ""}})
        await self.db[PLANS].update_one({}, {"$set": {"input_hash": "different-input"}})
        stock = await self.on_hand()
        with patch.object(delivery.shipping, "_resolve_order", AsyncMock(return_value=("local-assembly", {
                "id": "local-assembly", "reference_id": "local-assembly", "status": {"slug": "in_progress"}}))), \
             patch.object(delivery.shipping, "call_salla", AsyncMock()) as post:
            result = await delivery.resume(self.db, user_id="owner", order_number="local-assembly", manual=True, authorize=AsyncMock())
            self.assertTrue(result["requires_attention"])
            post.assert_not_awaited()
        self.assertEqual(await self.on_hand(), stock)
        self.assertTrue((await self.workflow())[delivery.FIELD]["legacy_readback_only"])

    async def test_revoked_recovery_does_not_enroll_or_dispatch(self):
        await self.finish()
        await self.db.order_review_workflows.update_one({}, {"$unset": {delivery.FIELD: ""}})
        with self.assertRaises(HTTPException):
            await delivery.resume(self.db, user_id="owner", order_number="local-assembly", manual=True,
                                  authorize=AsyncMock(side_effect=HTTPException(403)))
        self.assertNotIn(delivery.FIELD, await self.workflow())
        self.external.assert_not_awaited()

    async def test_background_does_not_promote_historical_read_only_operation(self):
        await self.finish()
        await self.db.order_review_workflows.update_one({}, {"$set": {
            f"{delivery.FIELD}.version": 1, f"{delivery.FIELD}.legacy_readback_only": True}})
        with patch.object(delivery.shipping, "_resolve_order", AsyncMock(return_value=("local-assembly", {
                "id": "local-assembly", "reference_id": "local-assembly", "status": {"slug": "in_progress"}}))), \
             patch.object(delivery.shipping, "call_salla", AsyncMock()) as post:
            await delivery.run_once(self.db)
            post.assert_not_awaited()
        self.assertTrue((await self.workflow())[delivery.FIELD]["legacy_readback_only"])

    async def test_two_explicit_recoveries_share_one_lease_and_post(self):
        await self.finish()
        await self.db.order_review_workflows.update_one({}, {"$unset": {delivery.FIELD: ""}})
        entered, release = asyncio.Event(), asyncio.Event()
        async def resolve(*args):
            entered.set()
            await release.wait()
            return "local-assembly", {"id": "local-assembly", "reference_id": "local-assembly", "status": {"slug": "in_progress"}}
        with patch.object(delivery.shipping, "_resolve_order", resolve), \
             patch.object(delivery.shipping, "call_salla", AsyncMock()) as post:
            first = asyncio.create_task(delivery.resume(self.db, user_id="owner", order_number="local-assembly", manual=True, authorize=AsyncMock()))
            await asyncio.wait_for(entered.wait(), 5)
            await delivery.resume(self.db, user_id="owner", order_number="local-assembly", manual=True, authorize=AsyncMock())
            release.set()
            await first
            post.assert_awaited_once()

    async def test_terminal_provider_status_never_promotes_or_dispatches(self):
        await self.finish()
        await self.db.order_review_workflows.update_one({}, {"$unset": {delivery.FIELD: ""}})
        with patch.object(delivery.shipping, "_resolve_order", AsyncMock(return_value=("local-assembly", {
                "id": "local-assembly", "reference_id": "local-assembly", "status": {"slug": "delivered"}}))), \
             patch.object(delivery.shipping, "call_salla", AsyncMock()) as post:
            result = await delivery.resume(self.db, user_id="owner", order_number="local-assembly", manual=True, authorize=AsyncMock())
            self.assertFalse(result["ready"])
            post.assert_not_awaited()
        self.assertTrue((await self.workflow())[delivery.FIELD]["legacy_readback_only"])

    async def test_unconsumed_unit_blocks_legacy_promotion_without_stock_write(self):
        from stock_component_consumption_service import UNITS
        await self.finish()
        await self.db.order_review_workflows.update_one({}, {"$unset": {delivery.FIELD: ""}})
        await self.db[UNITS].update_one({}, {"$set": {"state": "reserved"}})
        stock = await self.on_hand()
        with patch.object(delivery.shipping, "_resolve_order", AsyncMock(return_value=("local-assembly", {
                "id": "local-assembly", "reference_id": "local-assembly", "status": {"slug": "in_progress"}}))), \
             patch.object(delivery.shipping, "call_salla", AsyncMock()) as post:
            result = await delivery.resume(self.db, user_id="owner", order_number="local-assembly", manual=True, authorize=AsyncMock())
            self.assertTrue(result["requires_attention"])
            post.assert_not_awaited()
        self.assertEqual(await self.on_hand(), stock)

    async def test_provider_id_changed_during_get_cannot_target_old_order(self):
        await self.finish()
        await self.db.order_review_workflows.update_one({}, {"$unset": {delivery.FIELD: ""}})
        async def resolve(*args):
            await self.db.unified_orders.update_one({}, {"$set": {"raw_by_source.salla_direct.id": "replacement-id"}})
            return "local-assembly", {"id": "local-assembly", "reference_id": "local-assembly",
                                      "status": {"slug": "in_progress"}}
        with patch.object(delivery.shipping, "_resolve_order", resolve), \
             patch.object(delivery.shipping, "call_salla", AsyncMock()) as post:
            result = await delivery.resume(self.db, user_id="owner", order_number="local-assembly",
                                           manual=True, authorize=AsyncMock())
            self.assertTrue(result["requires_attention"])
            self.assertEqual(result["error_code"], "salla_order_reference_mismatch")
            post.assert_not_awaited()
        self.assertTrue((await self.workflow())[delivery.FIELD]["legacy_readback_only"])

    async def test_recovery_permission_revoked_during_provider_get_blocks_post(self):
        await self.finish()
        await self.db.order_review_workflows.update_one({}, {"$unset": {delivery.FIELD: ""}})
        with patch.object(delivery.shipping, "_resolve_order", AsyncMock(return_value=("local-assembly", {
                "id": "local-assembly", "reference_id": "local-assembly", "status": {"slug": "in_progress"}}))), \
             patch.object(delivery.shipping, "call_salla", AsyncMock()) as post:
            with self.assertRaises(HTTPException) as caught:
                await delivery.resume(self.db, user_id="owner", order_number="local-assembly", manual=True,
                                      authorize=AsyncMock(side_effect=[None, HTTPException(403)]))
            self.assertEqual(caught.exception.status_code, 403)
            post.assert_not_awaited()
        self.assertTrue((await self.workflow())[delivery.FIELD]["legacy_readback_only"])
