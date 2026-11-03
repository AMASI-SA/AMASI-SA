"""Acceptance tests for the Amasi order-first carrier label candidate.

Mocks only: these tests do not change Salla statuses or create shipments.
"""
from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from order_engine import amasi_shipping_compat as compat
from order_engine import shipping_label_service as legacy
from salla_integration.service import SallaError


class AmasiCompatibleShippingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.number = "298000001"
        self.order_id = "42"
        self.pending = {
            "id": 42, "reference_id": self.number,
            "status": {"slug": "in_progress"}, "shipments": [],
        }
        self.completed = {**self.pending, "status": {"slug": "completed"}}
        self.pdf = {
            "id": 99, "order_id": 42, "status": "created",
            "tracking_number": None,
            "label": {"url": "https://files.example/awb.pdf"},
        }
        mock_order = patch.object(legacy, "_resolve_order", new_callable=AsyncMock)
        mock_api = patch.object(compat, "call_salla", new_callable=AsyncMock)
        self.resolve = mock_order.start()
        self.api = mock_api.start()
        self.resolve.return_value = self.order_id, self.completed
        self.addCleanup(mock_order.stop)
        self.addCleanup(mock_api.stop)

    async def test_existing_pdf_prints_without_tracking_or_extra_calls(self):
        self.resolve.return_value = self.order_id, {
            **self.completed, "shipments": [self.pdf],
        }
        result = await compat.carrier_label(None, "owner", self.number, issue=True)
        self.assertTrue(result["ready"])
        self.assertEqual(result["label_url"], "https://files.example/awb.pdf")
        self.assertIsNone(result["tracking_number"])
        self.api.assert_not_called()

    async def test_completion_posts_once_and_uses_order_scoped_awb(self):
        self.resolve.return_value = self.order_id, self.pending

        async def reply(db, owner, method, path, **kwargs):
            if method == "POST":
                self.assertEqual(path, "/orders/42/status")
                self.assertTrue(kwargs["single_post_attempt"])
                self.assertEqual(kwargs["json"], {"slug": "completed"})
                return {"success": True}
            if path == "/orders/42":
                return {"data": self.completed}
            if path == "/orders/42/shipments":
                return {"data": [self.pdf]}
            raise AssertionError(path)

        self.api.side_effect = reply
        result = await compat.carrier_label(None, "owner", self.number, issue=True)
        self.assertTrue(result["ready"])
        self.assertTrue(result["order_status_completed"])
        self.assertEqual(sum(call.args[2] == "POST" for call in self.api.call_args_list), 1)
        self.assertFalse(any(call.args[3] == "/shipments" for call in self.api.call_args_list))

    async def test_order_scope_pdf_wins_without_modern_shipment_dependency(self):
        async def reply(db, owner, method, path, **kwargs):
            if path == "/orders/42/shipments":
                return {"data": [self.pdf]}
            raise AssertionError(path)

        self.api.side_effect = reply
        result = await compat.carrier_label(None, "owner", self.number, issue=False)
        self.assertTrue(result["ready"])
        self.assertFalse(any(call.args[3] == "/shipments" for call in self.api.call_args_list))

    async def test_404_order_scope_uses_modern_fallback(self):
        async def reply(db, owner, method, path, **kwargs):
            if path == "/orders/42/shipments":
                raise SallaError("Not Found", status_code=404)
            if path == "/shipments":
                return {"data": [self.pdf]}
            raise AssertionError(path)

        self.api.side_effect = reply
        result = await compat.carrier_label(None, "owner", self.number, issue=False)
        self.assertTrue(result["ready"])

    async def test_gateway_504_order_scope_uses_modern_fallback(self):
        async def reply(db, owner, method, path, **kwargs):
            if path == "/orders/42/shipments":
                raise SallaError("Gateway Timeout", status_code=504)
            if path == "/shipments":
                return {"data": [self.pdf]}
            raise AssertionError(path)

        self.api.side_effect = reply
        result = await compat.carrier_label(None, "owner", self.number, issue=False)
        self.assertEqual(result["shipment_id"], "99")
        self.assertTrue(result["ready"])

    async def test_transport_error_uses_independent_salla_fallback(self):
        import httpx

        async def reply(db, owner, method, path, **kwargs):
            if path == "/orders/42/shipments":
                raise httpx.ConnectError("network temporarily down")
            if path == "/shipments":
                return {"data": [self.pdf]}
            raise AssertionError(path)

        self.api.side_effect = reply
        result = await compat.carrier_label(None, "owner", self.number, issue=False)
        self.assertTrue(result["ready"])

    async def test_existing_known_salla_id_skips_keyword_search(self):
        class Collection:
            async def find_one(self, *args, **kwargs):
                return {"order_id": 42}

        class Db:
            unified_orders = Collection()

        self.resolve.side_effect = AssertionError("keyword search should not run")

        async def reply(db, owner, method, path, **kwargs):
            if path == "/orders/42":
                return {"data": {**self.completed, "shipments": [self.pdf]}}
            raise AssertionError(path)

        self.api.side_effect = reply
        result = await compat.carrier_label(Db(), "owner", self.number, issue=True)
        self.assertTrue(result["ready"])
        self.resolve.assert_not_called()

    async def test_missing_shipment_never_prints_or_posts(self):
        self.api.return_value = {"data": []}
        with patch.object(compat.asyncio, "sleep", new_callable=AsyncMock):
            result = await compat.carrier_label(None, "owner", self.number, issue=True)
        self.assertFalse(result["ready"])
        self.assertFalse(any(call.args[2] == "POST" for call in self.api.call_args_list))

    async def test_wrong_order_id_not_printed(self):
        self.resolve.return_value = self.order_id, {
            **self.completed, "shipments": [{**self.pdf, "order_id": 999}],
        }
        self.api.return_value = {"data": []}
        result = await compat.carrier_label(None, "owner", self.number, issue=False)
        self.assertFalse(result["ready"])

    async def test_cancelled_and_return_shipments_not_printed(self):
        self.resolve.return_value = self.order_id, {
            **self.completed, "shipments": [
                {**self.pdf, "status": "cancelled"},
                {**self.pdf, "id": 101, "shipment_type": "return"},
            ],
        }
        self.api.return_value = {"data": []}
        result = await compat.carrier_label(None, "owner", self.number, issue=False)
        self.assertFalse(result["ready"])

    async def test_store_courier_has_no_repeated_pdf_wait(self):
        self.api.return_value = {"data": []}
        def is_store(row):
            return ("مندوب" in str(row.get("courier_name") or "")
                    or (row.get("meta") or {}).get("app_id") == 0)
        with (patch.object(legacy, "extract_shipping", return_value={
                  "company_name": "مندوب الرياض", "company_code": 0,
              }),
              patch.object(legacy, "_is_store_courier", side_effect=is_store),
              patch.object(legacy, "_store_identity", new_callable=AsyncMock),
              patch.object(legacy, "_store_courier_print_data",
                           return_value={"courier_name": "مندوب الرياض", "qr_code": "data:image/svg+xml;base64,QR"})):
            result = await compat.carrier_label(None, "owner", self.number, issue=True)
        self.assertTrue(result["ready"])
        self.assertEqual(result["label_type"], "store_courier")
        self.assertEqual(len(self.api.call_args_list), 2)

    async def test_verified_destination_fallback_is_owner_and_order_scoped(self):
        class Collection:
            def __init__(self):
                self.query = None

            async def find_one(self, query, projection=None):
                self.query = query
                return {
                    "order_id": "42",
                    "salla_address_verified_at": "2026-11-03T16:05:00+00:00",
                    "shipping_address_raw": {
                        "city": "الرياض", "address_line": "عنوان المستلم الجديد"
                    },
                }

        class Db:
            unified_orders = Collection()

        db = Db()
        original = {"id": 42, "reference_id": self.number}
        result = await compat._with_verified_destination(
            db, "owner", self.number, "42", original,
        )
        self.assertEqual(
            result["_mezan_current_shipping_address"]["address_line"],
            "عنوان المستلم الجديد",
        )
        self.assertEqual(db.unified_orders.query, {
            "user_id": "owner", "order_number": self.number,
        })
        self.assertNotIn("_mezan_current_shipping_address", original)
        mismatched = await compat._with_verified_destination(
            db, "owner", self.number, "other-order-id", original,
        )
        self.assertNotIn("_mezan_current_shipping_address", mismatched)

    async def test_unverified_local_address_never_overrides_fresh_order(self):
        class Collection:
            async def find_one(self, query, projection=None):
                return {
                    "order_id": "42",
                    "shipping_address_raw": {
                        "city": "جدة", "address_line": "عنوان قديم"
                    },
                }

        class Db:
            unified_orders = Collection()

        order = {
            "id": 42,
            "shipping": {"address": {
                "city": "الرياض", "address_line": "عنوان المستلم الحالي"
            }},
        }
        result = await compat._with_verified_destination(
            Db(), "owner", self.number, "42", order,
        )
        self.assertNotIn("_mezan_current_shipping_address", result)


    async def test_cancelled_order_does_not_trigger_status_post(self):
        self.resolve.return_value = self.order_id, {
            **self.pending, "status": {"slug": "cancelled"},
        }
        with self.assertRaises(legacy.ShippingLabelError) as caught:
            await compat.carrier_label(None, "owner", self.number, issue=True)
        self.assertEqual(caught.exception.code, "order_cancelled")
        self.api.assert_not_called()

    async def test_uncertain_status_ack_reconciles_without_second_post(self):
        self.resolve.return_value = self.order_id, self.pending

        async def reply(db, owner, method, path, **kwargs):
            if method == "POST":
                raise SallaError("Gateway Timeout", status_code=504)
            if path == "/orders/42":
                return {"data": self.completed}
            if path == "/orders/42/shipments":
                return {"data": [self.pdf]}
            raise AssertionError(path)

        self.api.side_effect = reply
        result = await compat.carrier_label(None, "owner", self.number, issue=True)
        self.assertTrue(result["ready"])
        self.assertEqual(sum(call.args[2] == "POST" for call in self.api.call_args_list), 1)


class MobileShippingV1Tests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        import httpx
        from fastapi import FastAPI
        from mongomock_motor import AsyncMongoMockClient
        from fulfillment_v2_routes import make_fulfillment_v2_router

        self.db = AsyncMongoMockClient().mezan
        self.key = {"user_id": "merchant", "order_number": "12345"}
        self.workflows = self.db.order_review_workflows
        await self.workflows.insert_one({**self.key, "assembly_status": "completed"})
        self.actor = {"id": "merchant", "role": "owner"}

        async def current_user():
            return self.actor

        app = FastAPI()
        app.include_router(make_fulfillment_v2_router(self.db, current_user))
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
        self.addAsyncCleanup(self.client.aclose)
        self.path = "/fulfillment-v2/completed/12345/carrier-label/v1"
        self.pdf = {"ok": True, "ready": True, "source": "salla", "shipment_id": "42",
                    "label_url": "https://carrier.example/official.pdf", "tracking_number": None,
                    "order_status_completed": True}

    async def test_issue_and_refresh_expose_versioned_print_states(self):
        with patch.object(compat, "carrier_label", new_callable=AsyncMock) as engine:
            engine.return_value = self.pdf
            response = await self.client.post(self.path)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), {**self.pdf, "order_number": "12345",
                             "assembly_completion_confirmed": True, "contract_version": 1, "print_state": "ready"})
            engine.assert_awaited_once_with(self.db, "merchant", "12345", issue=True)
            engine.return_value = {**self.pdf, "ready": False, "label_url": None}
            response = await self.client.post(self.path + "/refresh")
            self.assertEqual(response.json()["print_state"], "pending")
            engine.assert_awaited_with(self.db, "merchant", "12345", issue=False)

    async def test_incomplete_and_foreign_orders_fail_before_carrier_access(self):
        with patch.object(compat, "carrier_label", new_callable=AsyncMock) as engine:
            for actor, status in [("other", "completed"), ("merchant", "in_progress")]:
                self.actor.update(id=actor)
                await self.workflows.update_one(self.key, {"$set": {"assembly_status": status}})
                response = await self.client.post(self.path + "/refresh")
                self.assertEqual(response.status_code, 409)
                detail = response.json()["detail"]
                self.assertEqual((detail["code"], detail["contract_version"], detail["print_state"]),
                                 ("assembly_completion_required", 1, "failed"))
            engine.assert_not_awaited()

    async def test_employee_requires_print_permission_and_responsibility(self):
        self.actor = {"id": "employee", "created_by": "merchant"}
        with patch("fulfillment_v2_routes.find_role_assignment", new_callable=AsyncMock, return_value=None), \
             patch.object(compat, "carrier_label", new_callable=AsyncMock) as engine:
            response = await self.client.post(self.path)
            self.assertEqual(response.status_code, 403)
            self.assertEqual(response.json()["detail"]["print_state"], "failed")
            engine.assert_not_awaited()

    async def test_authorized_employee_gets_own_merchants_label_and_revocation_blocks_a_write(self):
        from ai_store_access_contract import ROLE_ASSIGNMENTS, ROLE_ASSIGNMENT_OWNER_FIELD

        self.actor = {"id": "employee", "created_by": "merchant"}
        role = {ROLE_ASSIGNMENT_OWNER_FIELD: "merchant", "user_id": "employee"}
        permissions = self.db[ROLE_ASSIGNMENTS]
        await permissions.insert_one({**role, "extra_permissions": ["fulfillment.labels.print"],
                                      "fulfillment_responsibilities": ["shipping_labeling"]})

        async def revoke(db, owner, number, *, issue):
            await permissions.update_one(role, {"$set": {"enabled": False}})
            return {**self.pdf, "shipment_id": "unexpected"}

        with patch.object(compat, "carrier_label", new_callable=AsyncMock) as engine:
            engine.return_value = self.pdf
            first = await self.client.post(self.path + "/refresh")
            self.assertEqual(first.status_code, 200)
            engine.assert_awaited_once_with(self.db, "merchant", "12345", issue=False)
            engine.side_effect = revoke
            blocked = await self.client.post(self.path + "/refresh")
            self.assertEqual(blocked.status_code, 403)
            self.assertEqual(blocked.json()["detail"]["print_state"], "failed")
            self.assertEqual((await self.workflows.find_one(self.key))["carrier_shipment_id"], "42")

    async def test_slow_web_refresh_cannot_overwrite_newer_mobile_refresh(self):
        import asyncio

        started, resume = asyncio.Event(), asyncio.Event()
        async def provider(db, owner, number, *, issue):
            self.assertFalse(issue)
            if not started.is_set():
                started.set()
                await resume.wait()
                return {**self.pdf, "shipment_id": "41", "label_url": "https://carrier.example/old.pdf"}
            return self.pdf

        with patch.object(compat, "carrier_label", side_effect=provider):
            slow = asyncio.create_task(self.client.post(self.path.replace("/v1", "/amasi") + "/refresh"))
            try:
                await asyncio.wait_for(started.wait(), 2)
                fast = await self.client.post(self.path + "/refresh")
            finally:
                resume.set()
                old = await slow
        self.assertEqual(fast.status_code, 200)
        self.assertEqual(old.status_code, 409)
        self.assertEqual(old.json()["detail"]["code"], "shipping_snapshot_changed")
        saved = await self.workflows.find_one(self.key)
        self.assertEqual((saved["carrier_shipment_id"], saved["carrier_label_url"]),
                         ("42", self.pdf["label_url"]))


if __name__ == "__main__":
    unittest.main()
