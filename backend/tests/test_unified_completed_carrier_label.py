"""Explicit label requests share completed-page checks, never outbox read gates."""
import unittest
from unittest.mock import AsyncMock, patch
import fulfillment_carrier_label as labels
from order_engine.shipping_label_service import ShippingLabelError

class UnifiedCompletedLabelTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.guard = AsyncMock(return_value={"assembly_status": "completed"})
        self.refresh = AsyncMock(return_value={"ready": True, "order_status_completed": True, "label_type": "store_courier", "print_data": {"qr_code": "synthetic"}})
        self.resume = AsyncMock(return_value={"ready": False, "order_status_completed": False})
        self.authorize = AsyncMock()
        for name, target, mock in [
            ("guard", "fulfillment_carrier_label._require_print_completed_workflow", self.guard),
            ("refresh", "order_engine.shipping_label_service.refresh_shipping_label", self.refresh),
            ("resume", "assembly_completion_delivery.resume", self.resume),
        ]:
            replacement = patch(target, mock)
            replacement.start()
            self.addCleanup(replacement.stop)

    async def run_action(self):
        return await labels.prepare_completed_carrier_label(object(), user_id="owner", order_number="synthetic", actor_id="employee", actor_name="test", authorize=self.authorize)

    async def test_existing_label_returns_directly_without_outbox_or_status_post(self):
        result = await self.run_action()
        self.assertTrue(result["ready"])
        self.assertTrue(result["assembly_completion_confirmed"])
        self.assertEqual(result["order_number"], "synthetic")
        self.refresh.assert_awaited_once()
        self.resume.assert_not_awaited()
        self.authorize.assert_awaited_once()

    async def test_not_completed_uses_durable_resume_only(self):
        self.refresh.side_effect = ShippingLabelError("order_status_not_completed", "pending")
        result = await self.run_action()
        self.assertFalse(result["ready"])
        self.resume.assert_awaited_once()
        self.assertEqual(self.authorize.await_count, 2)

    async def test_unready_products_never_call_provider(self):
        self.guard.side_effect = ShippingLabelError("assembly_completion_required", "pending")
        with self.assertRaises(ShippingLabelError):
            await self.run_action()
        self.refresh.assert_not_awaited()
        self.resume.assert_not_awaited()

    async def test_network_identity_and_shipment_errors_never_fall_back_to_issue(self):
        for code in ["salla_shipping_unavailable", "salla_order_reference_mismatch", "shipping_snapshot_changed", "assembly_canonical_status_blocked"]:
            with self.subTest(code=code):
                self.refresh.side_effect = ShippingLabelError(code, code)
                with self.assertRaises(ShippingLabelError):
                    await self.run_action()
        self.resume.assert_not_awaited()

    async def test_revoked_authority_after_read_blocks_result_and_status_effect(self):
        self.authorize.side_effect = PermissionError("revoked")
        with self.assertRaises(PermissionError):
            await self.run_action()
        self.refresh.side_effect = ShippingLabelError("order_status_not_completed", "pending")
        with self.assertRaises(PermissionError):
            await self.run_action()
        self.resume.assert_not_awaited()


# Reuse the provider-boundary fixture in both memory and a real replica set.
import pytest
from test_shipping_print_legacy_path import setup, OWNER, ORDER, dump

@pytest.mark.asyncio
async def test_existing_completed_label_uses_same_boundary_despite_old_outbox(setup):
    db, state = setup
    await db.order_review_workflows.update_one({}, {"$set": {"assembly_delivery": {
        "state": "requires_attention", "error_code": "assembly_completion_evidence_changed",
        "source_fingerprint": "old", "workflow_fingerprint": "old"}}})
    before = await dump(db)
    authority = AsyncMock()
    result = await labels.prepare_completed_carrier_label(db, user_id=OWNER, order_number=ORDER,
        actor_id="employee", actor_name="test", authorize=authority)
    assert result["ready"] and result["order_status_completed"]
    assert result["label_url"] == "https://labels.test/current.pdf"
    assert await dump(db) == before
    authority.assert_awaited_once()

@pytest.mark.asyncio
async def test_all_unready_orders_block_shared_path_before_provider(setup):
    db, state = setup
    await db.order_review_workflows.update_one({}, {"$set": {"assembly_status": "pending"}})
    with pytest.raises(ShippingLabelError) as error:
        await labels.prepare_completed_carrier_label(db, user_id=OWNER, order_number=ORDER,
            actor_id="employee", actor_name="test", authorize=AsyncMock())
    assert error.value.code == "assembly_completion_required"
    assert not state["calls"]
