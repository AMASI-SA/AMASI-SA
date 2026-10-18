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
            return "internal", {"id": "internal", "reference_id": "local-assembly",
                                "status": {"slug": state}}
        async def post(*args, **kwargs):
            nonlocal state
            self.assertEqual(args[2:4], ("POST", "/orders/internal/status"))
            self.assertEqual(kwargs["json"], {"slug": "completed"})
            state = "completed"
        with patch.object(delivery.shipping, "_resolve_order", resolve), \
             patch.object(delivery.shipping, "call_salla", AsyncMock(side_effect=post)) as dispatch, \
             patch.object(delivery.shipping, "refresh_shipping_label", AsyncMock(return_value={"ready": True})):
            first = await delivery.resume(self.db, user_id="owner", order_number="local-assembly", manual=True)
            self.assertFalse(first["requires_attention"])
            dispatch.assert_awaited_once()
            second = await delivery.resume(self.db, user_id="owner", order_number="local-assembly", manual=True)
            self.assertTrue(second["ready"])
            dispatch.assert_awaited_once()
        self.assertEqual(await self.on_hand(), stock_before)

    async def test_legacy_changed_component_input_never_dispatches(self):
        from stock_component_consumption_service import PLANS
        await self.finish()
        await self.db.order_review_workflows.update_one({}, {"$unset": {delivery.FIELD: ""}})
        await self.db[PLANS].update_one({}, {"$set": {"input_hash": "different-input"}})
        stock = await self.on_hand()
        with patch.object(delivery.shipping, "_resolve_order", AsyncMock(return_value=("internal", {
                "id": "internal", "reference_id": "local-assembly", "status": {"slug": "in_progress"}}))), \
             patch.object(delivery.shipping, "call_salla", AsyncMock()) as post:
            result = await delivery.resume(self.db, user_id="owner", order_number="local-assembly", manual=True)
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
        with patch.object(delivery.shipping, "_resolve_order", AsyncMock(return_value=("internal", {
                "id": "internal", "reference_id": "local-assembly", "status": {"slug": "in_progress"}}))), \
             patch.object(delivery.shipping, "call_salla", AsyncMock()) as post:
            await delivery.run_once(self.db)
            post.assert_not_awaited()
        self.assertTrue((await self.workflow())[delivery.FIELD]["legacy_readback_only"])
