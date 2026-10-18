"""Owner-approved print policy. Real loopback replica set, fake provider only."""
import unittest
from copy import deepcopy
from unittest.mock import patch, AsyncMock

import assembly_print_completion as printing
import assembly_completion_delivery as delivery
import test_assembly_completion_v2_readback as fixture
from order_engine import shipping_label_service as shipping


class PrintCompletionTests(unittest.IsolatedAsyncioTestCase):
    reseal = fixture.BlockedCompletionTests.reseal
    provider_read = fixture.BlockedCompletionTests.provider_read
    snapshot = fixture.BlockedCompletionTests.snapshot

    async def asyncSetUp(self):
        await fixture.BlockedCompletionTests.asyncSetUp(self)
        self.order["items"] = [{"id": "line", "quantity": 2}]
        await self.db.unified_orders.update_one({}, {"$set": {"raw_by_source.salla_direct.items": self.order["items"]}})
        await self.db.mezan_preparation_pieces_v1.insert_many([
            {"user_id": "owner", "order_number": "synthetic-1", "piece_id": str(i),
             "assembly_status": "ready", "status": "ready_for_assembly", "order_item_id": "line", "unit_index": i + 1} for i in range(2)])
        await self.db.order_review_workflows.update_one({}, {"$set": {
            "assembly_delivery.error_code": "assembly_completion_evidence_changed",
            "assembly_delivery.workflow_fingerprint": "obsolete",
            "assembly_delivery.source_fingerprint": "obsolete"}})

    async def read(self):
        return await printing.read_print_completion(self.db, "owner", "synthetic-1")

    async def test_current_ready_completed_label_ignores_old_delivery_fingerprints_zero_writes(self):
        before = await self.snapshot()
        result = await self.read()
        self.assertTrue(result["ready"])
        self.assertFalse(result["requires_attention"])
        self.assertEqual(result["label_url"], "https://labels.test/current.pdf")
        self.assertEqual(await self.snapshot(), before)
        self.provider.assert_awaited_once()

    async def test_completion_endpoint_service_uses_same_print_proof(self):
        result = await delivery.read_status(self.db, "owner", "synthetic-1")
        self.assertTrue(result["order_status_completed"])
        self.assertTrue(result["ready"])

    async def test_any_unready_piece_blocks_before_provider(self):
        await self.db.mezan_preparation_pieces_v1.update_one({"piece_id": "0"}, {"$set": {"assembly_status": "pending"}})
        self.assertIsNone(await self.read())
        self.provider.assert_not_awaited()

    async def test_empty_or_other_owner_cannot_print(self):
        self.assertIsNone(await printing.read_print_completion(self.db, "other", "synthetic-1"))
        await self.db.mezan_preparation_pieces_v1.delete_many({"order_number": "synthetic-1"})
        self.assertIsNone(await self.read())
        self.provider.assert_not_awaited()

    async def test_all_noncompleted_salla_statuses_reject(self):
        for status in ("in_progress", "shipped", "delivered", "cancelled"):
            with self.subTest(status=status):
                self.order["status"] = {"slug": status}
                before = await self.snapshot()
                with self.assertRaises(shipping.ShippingLabelError):
                    await self.read()
                self.assertEqual(await self.snapshot(), before)

    async def test_identity_or_current_shipment_changed_rejects(self):
        original = deepcopy(self.order)
        for change in ({"reference_id": "other"}, {"id": "other"},
                       {"shipments": [{"id": "new", "courier_name": "iMile", "status": "created"}]}):
            self.order = {**original, **change}
            with self.assertRaises(shipping.ShippingLabelError):
                await self.read()

    async def test_piece_changed_during_provider_get_rejects(self):
        async def race(*args, **kwargs):
            await self.db.mezan_preparation_pieces_v1.update_one({"piece_id": "0"}, {"$set": {"assembly_status": "pending"}})
            return await self.provider_read(*args, **kwargs)
        self.provider.side_effect = race
        with self.assertRaises(shipping.ShippingLabelError):
            await self.read()

    async def test_harmless_delivery_revision_during_get_does_not_block_print(self):
        async def audit(*args, **kwargs):
            await self.db.order_review_workflows.update_one({}, {"$inc": {"revision": 1, "assembly_delivery.attempts": 1}})
            return await self.provider_read(*args, **kwargs)
        self.provider.side_effect = audit
        self.assertTrue((await self.read())["ready"])

    async def test_missing_label_confirms_status_but_does_not_invent_file(self):
        from salla_shipping import CURRENT_SHIPPING
        await self.db.unified_orders.update_one({}, {"$unset": {f"{CURRENT_SHIPPING}.label_url": ""}})
        result = await self.read()
        self.assertTrue(result["order_status_completed"])
        self.assertFalse(result["ready"])
        self.assertIsNone(result["label_url"])

    async def test_missing_unit_or_new_source_item_or_hold_blocks(self):
        originals = await self.db.mezan_preparation_pieces_v1.find({"order_number": "synthetic-1"}).to_list(None)
        for change in ({"status": "cancelled"}, {"experiment_archived_at": "now"}, {"active_hold_id": "hold"}, {"unit_index": 2}):
            with self.subTest(change=change):
                await self.db.mezan_preparation_pieces_v1.replace_one({"piece_id": "0"}, deepcopy(originals[0]))
                await self.db.mezan_preparation_pieces_v1.update_one({"piece_id": "0"}, {"$set": change})
                self.assertIsNone(await self.read())
        await self.db.mezan_preparation_pieces_v1.replace_one({"piece_id": "0"}, deepcopy(originals[0]))
        await self.db.unified_orders.update_one({}, {"$set": {"raw_by_source.salla_direct.items.0.quantity": 3}})
        self.assertIsNone(await self.read())
        self.provider.assert_not_awaited()

    async def test_fresh_negative_never_reuses_confirmed_outbox(self):
        await self.db.order_review_workflows.update_one({}, {"$set": {
            "assembly_delivery.state": "confirmed", "assembly_delivery.order_confirmed": True,
            "carrier_label_ready": True}})
        self.order["status"] = {"slug": "delivered"}
        result = await delivery.read_status(self.db, "owner", "synthetic-1")
        self.assertFalse(result["ready"])
        self.assertFalse(result["order_status_completed"])
        self.assertIsNone(result["label_url"])

    async def test_legacy_issue_and_completion_routes_return_same_existing_label_without_resume(self):
        from fastapi import FastAPI
        from httpx import ASGITransport, AsyncClient
        import preparation_piece_operations as pieces
        import fulfillment_v2_routes as fulfillment
        context = {"merchant_id": "owner", "actor_id": "employee", "is_owner": False,
                   "permissions": {"fulfillment.ready.read", "fulfillment.labels.print"},
                   "responsibilities": {"instant_ready", "shipping_labeling"}}
        async def actor():
            return {"id": "employee", "role": "employee", "created_by": "owner"}
        app = FastAPI()
        app.include_router(pieces.make_preparation_piece_operations_router(self.db, actor))
        app.include_router(fulfillment.make_fulfillment_v2_router(self.db, actor))
        with patch.object(pieces, "_actor_context", AsyncMock(return_value=context)), \
             patch.object(fulfillment, "_actor_context", AsyncMock(return_value=context)), \
             patch.object(delivery, "resume", AsyncMock(side_effect=AssertionError("must not resume delivery"))):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                before = await self.snapshot()
                for method, path in (("GET", "/preparation-work-v1/assembly/orders/synthetic-1/completion"),
                                     ("POST", "/fulfillment-v2/completed/synthetic-1/carrier-label")):
                    response = await client.request(method, path)
                    self.assertEqual(response.status_code, 200, response.text)
                    self.assertTrue(response.json()["ready"])
                    self.assertEqual(response.json()["label_url"], "https://labels.test/current.pdf")
                self.assertEqual(await self.snapshot(), before)

    async def test_legacy_issue_permission_revoked_during_provider_read_is_denied(self):
        from fastapi import FastAPI
        from httpx import ASGITransport, AsyncClient
        import fulfillment_v2_routes as fulfillment
        context = {"merchant_id": "owner", "actor_id": "employee", "is_owner": False,
                   "permissions": {"fulfillment.labels.print"}, "responsibilities": {"shipping_labeling"}}
        async def actor():
            return {"id": "employee"}
        async def revoke(*args, **kwargs):
            context["permissions"].clear()
            return await self.provider_read(*args, **kwargs)
        self.provider.side_effect = revoke
        app = FastAPI()
        app.include_router(fulfillment.make_fulfillment_v2_router(self.db, actor))
        with patch.object(fulfillment, "_actor_context", AsyncMock(side_effect=lambda *args: deepcopy(context))):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post("/fulfillment-v2/completed/synthetic-1/carrier-label")
                self.assertEqual(response.status_code, 403, response.text)

    async def test_virtual_units_and_operational_work_must_be_ready(self):
        await self.db.mezan_preparation_pieces_v1.delete_many({"order_number": "synthetic-1"})
        await self.db.order_review_workflows.update_one({}, {"$set": {"items": [{
            "order_item_id": "line", "quantity": 2, "preparation_route": "direct_assembly",
            "direct_assembly_piece_ids": ["v1", "v2"], "assembly_ready_piece_ids": ["v1", "v2"]}]}})
        self.assertTrue((await self.read())["ready"])
        await self.db.order_review_workflows.update_one({}, {"$set": {"items.0.assembly_ready_piece_ids": ["v1"]}})
        self.assertIsNone(await self.read())
        await self.db.order_review_workflows.update_one({}, {"$set": {
            "items.0.assembly_ready_piece_ids": ["v1", "v2"],
            "operational_items": [{"operational_item_id": "extra", "assembly_status": "pending"}]}})
        self.assertIsNone(await self.read())

    async def test_old_search_replaces_outbox_mask_with_fresh_label(self):
        from fastapi import FastAPI
        from httpx import ASGITransport, AsyncClient
        import preparation_piece_operations as pieces
        context = {"merchant_id": "owner", "actor_id": "employee", "is_owner": False,
                   "permissions": {"fulfillment.ready.read"}, "responsibilities": {"instant_ready"}}
        async def actor():
            return {"id": "employee", "role": "employee", "created_by": "owner"}
        app = FastAPI()
        app.include_router(pieces.make_preparation_piece_operations_router(self.db, actor))
        with patch.object(pieces, "_actor_context", AsyncMock(return_value=context)), \
             patch.object(pieces, "ensure_piece_operation_indexes", AsyncMock()), \
             patch.object(pieces, "_assembly_search", AsyncMock(return_value={
                 "order_number": "synthetic-1", "order_status": "completed", "summary": {"all_ready": True},
                 "carrier_label": {"ready": False, "label_url": None}, "order_completion_status": "pending"})):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                before = await self.snapshot()
                response = await client.get("/preparation-work-v1/assembly/search?q=synthetic-1")
                self.assertEqual(response.status_code, 200, response.text)
                self.assertTrue(response.json()["carrier_label"]["ready"])
                self.assertEqual(response.json()["order_completion_status"], "confirmed")
                self.assertEqual(await self.snapshot(), before)
