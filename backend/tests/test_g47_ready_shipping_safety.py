"""Real replica-set regressions; all provider IO is synthetic."""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import AsyncMock, patch

import test_assembly_completion_delivery as fixture
from assembly_status_policy import canonical_status

spec = importlib.util.spec_from_file_location("outbox_counterexample", Path(__file__).parents[2] /
    "docs/operations/READY-CANONICAL-STATUS-RACE/outbox_counterexample.py")
counterexample = importlib.util.module_from_spec(spec)
spec.loader.exec_module(counterexample)


class CounterexampleTests(unittest.IsolatedAsyncioTestCase):
    async def test_status_read_then_terminal_never_posts(self):
        await self.check("status_post")

    async def test_shipment_read_then_terminal_never_posts(self):
        await self.check("awb_post")

    async def test_label_read_then_terminal_never_publishes(self):
        await self.check("publication")

    async def check(self, boundary):
        result = await counterexample.scenario(boundary)
        self.assertEqual(result["canonical_status"], "delivered")
        self.assertEqual(result["mock_posts"], [])
        self.assertFalse(result["ready"])
        self.assertEqual(result["outbox_state"], "requires_attention")


class SafetyTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.DeliveryTests.asyncSetUp
    asyncTearDown = fixture.DeliveryTests.asyncTearDown
    source_payload = fixture.DeliveryTests.source_payload
    webhook = fixture.DeliveryTests.webhook
    mark_piece = fixture.DeliveryTests.mark_piece
    on_hand = fixture.DeliveryTests.on_hand
    complete_review = fixture.DeliveryTests.complete_review
    workflow = fixture.DeliveryTests.workflow
    finish = fixture.DeliveryTests.finish

    async def test_conflicting_root_slug_rejects_ready_without_inventory_write(self):
        await self.db.unified_orders.update_one({"user_id": "owner"},
            {"$set": {"order_status_slug": "cancelled"}})
        response = await self.mark_piece(self.ids[0])
        self.assertEqual(response.status_code, 409)
        self.assertEqual(await self.on_hand(), 20)
        search = await self.client.get("/preparation-work-v1/assembly/search?q=local-assembly")
        self.assertFalse(any(p["can_mark_ready"] for p in search.json()["pieces"]))

    async def test_no_remote_precondition_stops_without_any_post_or_replay(self):
        await self.finish()
        delivery = fixture.delivery
        with patch.object(delivery.shipping, "_resolve_order", AsyncMock(return_value=("internal", {
            "id": "internal", "reference_id": "local-assembly", "status": {"slug": "in_progress"}}))), \
             patch.object(delivery.shipping, "call_salla", AsyncMock()) as post:
            first = await delivery.resume(self.db, user_id="owner", order_number="local-assembly", manual=True)
            second = await delivery.resume(self.db, user_id="owner", order_number="local-assembly", manual=True)
        self.assertFalse(first["ready"])
        self.assertFalse(second["ready"])
        self.assertEqual(first["error_code"], "completion_remote_precondition_unavailable")
        self.assertTrue(first["requires_attention"])
        post.assert_not_awaited()
        self.assertEqual(await self.on_hand(), 16)

    async def test_lost_legacy_post_response_is_readback_only(self):
        await self.finish()
        delivery = fixture.delivery
        await self.db[delivery.WORKFLOWS].update_one({"user_id": "owner"}, {"$set": {
            "assembly_delivery.status_attempted": True, "assembly_delivery.awb_attempted": True}})
        with patch.object(delivery.shipping, "_resolve_order", AsyncMock(return_value=("internal", {
            "id": "internal", "reference_id": "local-assembly", "status": {"slug": "completed"}}))), \
             patch.object(delivery.shipping, "refresh_shipping_label", AsyncMock(return_value={"ready": True})), \
             patch.object(delivery.shipping, "call_salla", AsyncMock()) as post:
            for _ in range(2):
                result = await delivery.resume(self.db, user_id="owner", order_number="local-assembly", manual=True)
                self.assertTrue(result["ready"])
        post.assert_not_awaited()
        self.assertEqual(await self.on_hand(), 16)

    async def test_terminal_writer_revokes_already_confirmed_shipping(self):
        await self.finish()
        from orders_db import upsert_order
        await self.db[fixture.delivery.WORKFLOWS].update_one({"user_id": "owner"}, {"$set": {
            "carrier_label_ready": True, "assembly_delivery.order_confirmed": True,
            "assembly_delivery.state": "confirmed", "salla_order_status": "completed"}})
        for status in ("shipped", "delivered", "cancelled"):
            await upsert_order(self.db, "owner", "local-assembly",
                {"order_status": status, "order_status_slug": status}, "salla_direct", raw={"status": {"slug": status}})
            workflow = await self.workflow()
            self.assertFalse(workflow["carrier_label_ready"])
            self.assertFalse(workflow["assembly_delivery"]["order_confirmed"])
            result = await fixture.delivery.read_status(self.db, "owner", "local-assembly")
            self.assertFalse(result["ready"])

    async def test_blocked_then_allowed_does_not_restore_old_attempt_ownership(self):
        await self.finish()
        from orders_db import upsert_order
        from copy import deepcopy
        delivery = fixture.delivery
        async def refresh(*args):
            row = await self.db.unified_orders.find_one({"order_number": "local-assembly"})
            for status in ("delivered", "in_progress"):
                raw = deepcopy(row["raw_by_source"]["salla_direct"])
                raw.update(status={"slug": status}, status_slug=status)
                await upsert_order(self.db, "owner", "local-assembly",
                    {"order_status": status, "order_status_slug": status}, "salla_direct", raw=raw)
            return {"ready": True, "label_url": "https://example.test/old.pdf"}
        with patch.object(delivery.shipping, "_resolve_order", AsyncMock(return_value=("internal", {
                "id": "internal", "reference_id": "local-assembly", "status": {"slug": "completed"}}))), \
             patch.object(delivery.shipping, "refresh_shipping_label", refresh):
            result = await delivery.resume(self.db, user_id="owner", order_number="local-assembly", manual=True)
        self.assertFalse(result["ready"])
        self.assertFalse((await self.workflow())["carrier_label_ready"])
        self.assertEqual((await self.workflow())[delivery.FIELD]["state"], "requires_attention")

    async def test_legacy_result_cannot_overwrite_terminal_invalidation(self):
        await self.finish()
        import fulfillment_carrier_label as carrier
        from orders_db import upsert_order
        async def refresh(*args):
            await upsert_order(self.db, "owner", "local-assembly",
                {"order_status": "delivered", "order_status_slug": "delivered"},
                "salla_direct", raw={"status": {"slug": "delivered"}})
            return {"ready": True, "order_status_completed": True}
        with patch.object(carrier, "refresh_shipping_label", refresh):
            with self.assertRaises(fixture.delivery.shipping.ShippingLabelError):
                await carrier.sync_completed_carrier_label(self.db, user_id="owner", order_number="local-assembly",
                    actor_id="owner", actor_name="Synthetic", action="refresh")
        self.assertFalse((await self.workflow()).get("carrier_label_ready", False))

    async def test_all_carriers_reject_terminal_provider_status(self):
        await self.finish()
        shipping = fixture.delivery.shipping
        for carrier in ("SMSA", "iMile", "مندوب المتجر"):
            for status in ("delivered", "shipped", "cancelled"):
                with self.subTest(carrier=carrier, status=status), \
                     patch.object(shipping, "_resolve_order", AsyncMock(return_value=("internal", {
                         "id": "internal", "reference_id": "local-assembly", "status": {"slug": status},
                         "shipping": {"company_name": carrier}}))), \
                     patch.object(shipping, "call_salla", AsyncMock()) as post:
                    with self.assertRaises(shipping.ShippingLabelError):
                        await shipping.refresh_shipping_label(self.db, "owner", "local-assembly")
                    post.assert_not_awaited()

    async def test_direct_refresh_rejects_blocked_then_allowed_without_outbox(self):
        await self.finish()
        from copy import deepcopy
        from orders_db import upsert_order
        shipping = fixture.delivery.shipping
        await self.db[fixture.delivery.WORKFLOWS].update_one({"user_id": "owner"}, {"$unset": {"assembly_delivery": ""}})
        changed = False
        async def resolve(*args):
            nonlocal changed
            if not changed:
                changed = True
                row = await self.db.unified_orders.find_one({"order_number": "local-assembly"})
                for status in ("delivered", "in_progress"):
                    raw = deepcopy(row["raw_by_source"]["salla_direct"])
                    raw.update(status={"slug": status}, status_slug=status)
                    await upsert_order(self.db, "owner", "local-assembly",
                        {"order_status": status, "order_status_slug": status}, "salla_direct", raw=raw)
            return "internal", {"id": "internal", "reference_id": "local-assembly", "status": {"slug": "completed"}}
        rows = [{"id": "a", "status": "created", "tracking_number": "AWB-A", "label_url": "https://example.test/a.pdf"}]
        with patch.object(shipping, "_resolve_order", resolve), \
             patch.object(shipping, "_print_shipment_rows", AsyncMock(return_value=rows)):
            with self.assertRaises(shipping.ShippingLabelError) as error:
                await shipping.refresh_shipping_label(self.db, "owner", "local-assembly")
        self.assertEqual(error.exception.code, "shipping_snapshot_changed")
        self.assertFalse((await self.workflow())["carrier_label_ready"])

    async def test_fallback_merchant_shipment_switch_during_store_read_rejects_print(self):
        await self.finish()
        from copy import deepcopy
        shipping = fixture.delivery.shipping
        # Both order observations omit carrier/shipments: only the shipment
        # endpoint proves that the initially selected merchant row is current.
        order = {"id": "internal", "reference_id": "local-assembly",
                 "status": {"slug": "completed"}}
        merchant = {"id": "a", "order_id": "internal", "status": "created",
                    "courier_name": "مندوب المتجر", "meta": {"app_id": 0}}
        external = {"id": "b", "order_id": "internal", "status": "created",
                    "courier_name": "SMSA", "tracking_number": "AWB-B",
                    "label_url": "https://example.test/b.pdf"}
        switched = False
        calls = []
        async def provider(db, owner, method, path, **kwargs):
            nonlocal switched
            self.assertEqual(method, "GET", "Printing must never issue a provider write")
            calls.append(path)
            if path == "/orders":
                return {"data": [deepcopy(order)]}
            if path == "/orders/internal":
                return {"data": deepcopy(order)}
            if path == "/shipments":
                return {"data": [deepcopy(external if switched else merchant)]}
            if path == "/store/info":
                switched = True
                return {"data": {"name": "Synthetic store"}}
            if path == "/shipments/b":
                return {"data": deepcopy(external)}
            self.fail(f"Unexpected provider GET {path}")
        with patch.object(shipping, "call_salla", provider):
            with self.assertRaises(shipping.ShippingLabelError) as error:
                await shipping.refresh_shipping_label(self.db, "owner", "local-assembly")
        self.assertEqual(error.exception.code, "shipping_snapshot_changed")
        self.assertTrue(switched)
        self.assertEqual(calls.count("/shipments"), 2)
        workflow = await self.workflow()
        self.assertFalse(workflow["carrier_label_ready"])
        self.assertIsNone(workflow["carrier_label_print_data"])
        self.assertFalse(workflow["assembly_delivery"]["order_confirmed"])
        self.assertEqual(await self.on_hand(), 16)

    async def test_direct_refresh_rechecks_non_embedded_shipment_identity(self):
        await self.finish()
        shipping = fixture.delivery.shipping
        def rows(identity):
            return [{"id": identity, "status": "created", "tracking_number": "AWB-" + identity,
                     "label_url": "https://example.test/" + identity + ".pdf"}]
        with patch.object(shipping, "_resolve_order", AsyncMock(return_value=("internal", {
                "id": "internal", "reference_id": "local-assembly", "status": {"slug": "completed"}}))), \
             patch.object(shipping, "_print_shipment_rows", AsyncMock(side_effect=[rows("a"), rows("b")])):
            with self.assertRaises(shipping.ShippingLabelError) as error:
                await shipping.refresh_shipping_label(self.db, "owner", "local-assembly")
        self.assertEqual(error.exception.code, "shipping_snapshot_changed")
        self.assertFalse((await self.workflow())["carrier_label_ready"])


class StatusPolicyTests(unittest.TestCase):
    def test_missing_conflicting_custom_or_stale_evidence_fails_closed(self):
        for source in ({}, {"order_status": "in_progress"},
                       {"order_status_slug": "delivered", "raw_by_source": {"salla_direct": {"status": {"slug": "in_progress"}}}},
                       {"raw_by_source": {"salla_direct": {"status": {"name": "in_progress"}, "status_slug": "delivered"}}},
                       {"raw_by_source": {"salla_direct": {"status": {"slug": "in_progress", "customized": {"name": "cancelled"}}}}},
                       {"raw_by_source": {"salla_direct": {"status": {"slug": "in_progress"}}}, "g47_salla_snapshot": {"requires_authoritative_refresh": True}}):
            self.assertIsNone(canonical_status(source))


class ProviderReadbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_new_completed_read_does_not_authorize_old_carrier(self):
        shipping = fixture.delivery.shipping
        initial = {"id": "internal", "reference_id": "order", "status": {"slug": "completed"},
                   "shipping": {"company_name": "SMSA"}}
        latest = {**initial, "shipping": {"company_name": "iMile"}}
        with patch.object(shipping, "_resolve_order", AsyncMock(return_value=("internal", latest))):
            with self.assertRaises(shipping.ShippingLabelError):
                await shipping._recheck_provider_completed(None, "owner", "order", "internal", initial)
