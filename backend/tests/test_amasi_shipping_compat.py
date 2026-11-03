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


if __name__ == "__main__":
    unittest.main()
