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
            ("refresh", "fulfillment_carrier_label.read_current_carrier_label", self.refresh),
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
        self.refresh.side_effect = ShippingLabelError("carrier_order_in_progress", "pending")
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
        for code in ["order_status_not_completed", "salla_shipping_unavailable", "salla_order_reference_mismatch", "shipping_snapshot_changed", "assembly_canonical_status_blocked"]:
            with self.subTest(code=code):
                self.refresh.side_effect = ShippingLabelError(code, code)
                with self.assertRaises(ShippingLabelError):
                    await self.run_action()
        self.resume.assert_not_awaited()

    async def test_revoked_authority_after_read_blocks_result_and_status_effect(self):
        self.authorize.side_effect = PermissionError("revoked")
        with self.assertRaises(PermissionError):
            await self.run_action()
        self.refresh.side_effect = ShippingLabelError("carrier_order_in_progress", "pending")
        with self.assertRaises(PermissionError):
            await self.run_action()
        self.resume.assert_not_awaited()


# Reuse the provider-boundary fixture in both memory and a real replica set.
import pytest
from test_shipping_print_legacy_path import setup, OWNER, ORDER, dump
from order_engine import shipping_label_service as shipping
from order_engine.shipping_label_service import _persist_verified_snapshot
from salla_shipping import CURRENT_SHIPPING

@pytest.mark.asyncio
async def test_existing_completed_label_uses_same_boundary_despite_old_outbox(setup, monkeypatch):
    db, state = setup
    # The v1 provider must bind the current shipment and reject the former one.
    await db.unified_orders.update_one({}, {"$set": {CURRENT_SHIPPING: {
        "shipment_id": "200", "company_name": "SMSA", "superseded_shipment_ids": ["old"],
    }, "salla_shipment_id": "200"}})
    state["order"]["shipments"] = state["rows"]
    monkeypatch.setattr(labels.compat, "call_salla", shipping.call_salla)
    monkeypatch.setattr(shipping, "_persist_verified_snapshot", _persist_verified_snapshot)
    monkeypatch.setattr(shipping, "refresh_shipping_label", AsyncMock(side_effect=AssertionError("legacy label path")))
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


@pytest.mark.asyncio
@pytest.mark.parametrize("surface", ["completed", "assembly"])
@pytest.mark.parametrize("allowed", [True, False])
async def test_actual_explicit_routes_share_current_label_and_permission_boundary(setup, surface, allowed, monkeypatch):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    from fulfillment_v2_routes import make_fulfillment_v2_router
    from preparation_piece_operations import make_preparation_piece_operations_router
    from order_engine import amasi_shipping_compat as compat, shipping_label_service as shipping
    db, state = setup
    # Both public handlers use a versioned, read-only provider result.
    current = AsyncMock(return_value={
        "ok": True, "ready": True, "order_status_completed": True, "source": "salla",
        "shipment_id": "active-1", "label_url": "https://labels.test/current.pdf",
    })
    monkeypatch.setattr(compat, "carrier_label", current)
    monkeypatch.setattr("operational_atomic.operational_owner", shipping.operational_owner)
    await db.order_review_workflows.update_one({}, {"$set": {"assembly_delivery": {
        "state": "requires_attention", "error_code": "assembly_completion_evidence_changed"}}})
    async def user():
        return {"id": OWNER if allowed else "employee", "role": "owner" if allowed else "employee", "created_by": OWNER}
    app = FastAPI()
    factory = make_fulfillment_v2_router if surface == "completed" else make_preparation_piece_operations_router
    app.include_router(factory(db, user))
    path = f"/fulfillment-v2/completed/{ORDER}/carrier-label/v1" if surface == "completed" else f"/preparation-work-v1/assembly/orders/{ORDER}/completion/resume"
    before = await dump(db)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(path)
    assert response.status_code == (200 if allowed else 403), response.text
    if allowed:
        assert response.json()["ready"] and response.json()["assembly_completion_confirmed"]
        assert response.json()["label_url"] == "https://labels.test/current.pdf"
        assert current.await_args.kwargs == {"issue": False, "completed_only": True}
        if surface == "completed":
            assert response.json()["contract_version"] == 1 and response.json()["print_state"] == "ready"
    else:
        current.assert_not_awaited()
    assert not state["calls"]
    after = await dump(db)
    if allowed and surface == "completed":
        # v1 persists only the current label evidence; no business transition.
        assert after["order_review_workflows"][0]["carrier_shipment_id"] == "active-1"
        assert after["order_review_workflows"][0]["stage"] == before["order_review_workflows"][0]["stage"]
        assert after["order_review_workflows"][0]["assembly_delivery"] == before["order_review_workflows"][0]["assembly_delivery"]
        assert {k: v for k, v in after.items() if k != "order_review_workflows"} == {
            k: v for k, v in before.items() if k != "order_review_workflows"}
    else:
        assert after == before
