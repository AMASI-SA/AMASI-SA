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

    async def test_unknown_conflicting_cancelled_and_refunded_orders_never_dispatch(self):
        for status, slug in [("refunded", None), ("ملغى", None), ("waiting_payment", None),
                             ("in_progress", "cancelled"), (None, None)]:
            self.resolve.return_value = self.order_id, {**self.pending, "status": status, "status_slug": slug}
            for strict in (False, True):
                with self.assertRaises(legacy.ShippingLabelError):
                    await compat.carrier_label(None, "owner", self.number, issue=True, completed_only=strict)
        self.api.assert_not_called()

    async def test_native_filters_superseded_id_before_reading_official_pdf(self):
        from mongomock_motor import AsyncMongoMockClient
        from salla_shipping import CURRENT_SHIPPING

        db = AsyncMongoMockClient().mezan
        metadata = {"shipment_id": "10", "superseded_shipment_ids": ["999"],
                    "company_name": "iMile", "company_code": "imile"}
        await db.unified_orders.insert_one({"user_id": "owner", "order_number": self.number,
                                             CURRENT_SHIPPING: metadata})
        old = {**self.pdf, "id": 999, "label": {"url": "https://files.example/archived.pdf"}}
        current = {**self.pdf, "id": 10, "courier_name": "iMile", "courier_id": "imile"}
        self.api.return_value = {"data": []}

        async def local_owner(database, owner, callback):
            return await callback(database)
        with patch.object(legacy, "operational_owner", local_owner):
            for rows, expected in (([old, current], True), ([old], False)):
                self.resolve.return_value = self.order_id, {**self.completed, "shipments": rows}
                out = await compat.carrier_label(db, "owner", self.number, issue=False, completed_only=True)
                self.assertEqual(out["ready"], expected)
                self.assertEqual(out["label_url"], current["label"]["url"] if expected else None)
                self.assertIsNone(out["tracking_number"])
            internal = {**current, "courier_name": "مندوب المتجر", "meta": {"app_id": "0"}}
            self.resolve.return_value = self.order_id, {**self.completed, "shipments": [internal]}
            with self.assertRaises(legacy.ShippingLabelError) as mismatch:
                await compat.carrier_label(db, "owner", self.number, issue=False, completed_only=True)
            self.assertEqual(mismatch.exception.code, "shipping_snapshot_changed")

    async def test_superseded_external_label_is_not_reused_after_store_courier_switch(self):
        from mongomock_motor import AsyncMongoMockClient
        from salla_shipping import CURRENT_SHIPPING

        db = AsyncMongoMockClient().mezan
        key = {"user_id": "owner", "order_number": self.number}
        await db.unified_orders.insert_one({**key, CURRENT_SHIPPING: {
            "company_name": "مندوب المتجر", "company_code": "0", "superseded_shipment_ids": ["99"],
        }})
        await db.order_review_workflows.insert_one({**key, "store_courier_assignee_name": "موصل الرياض"})
        self.api.return_value = {"data": []}
        async def local_owner(database, owner, callback):
            return await callback(database)
        with patch.object(legacy, "operational_owner", local_owner):
            for shipping, allowed in [({"company_name": "iMile", "company_code": "imile"}, False),
                                      ({"company_name": "مندوب المتجر", "company_code": "0"}, True)]:
                self.resolve.return_value = self.order_id, {**self.completed, "shipping": shipping,
                                                            "shipments": [self.pdf]}
                if not allowed:
                    with self.assertRaises(legacy.ShippingLabelError):
                        await compat.carrier_label(db, "owner", self.number, issue=False, completed_only=True)
                    continue
                result = await compat.carrier_label(db, "owner", self.number, issue=False, completed_only=True)
                self.assertEqual(result["label_type"], "store_courier")
                self.assertEqual(result["print_data"]["assigned_courier_name"], "موصل الرياض")
                self.assertIsNone(result["label_url"])
                self.assertIsNone(result["shipment_id"])

    async def test_store_courier_current_order_aliases_and_city_guard(self):
        from mongomock_motor import AsyncMongoMockClient
        from salla_shipping import CURRENT_SHIPPING

        db = AsyncMongoMockClient().mezan
        key = {"user_id": "owner", "order_number": self.number}
        city = "مندوب الرياض"
        await db.unified_orders.insert_one({**key, CURRENT_SHIPPING: {
            "company_name": city, "company_code": "0", "superseded_shipment_ids": ["99"],
        }})
        await db.order_review_workflows.insert_one({**key, "store_courier_assignee_name": "موصل الاختبار"})
        self.api.return_value = {"data": []}
        async def local_owner(database, owner, callback):
            return await callback(database)

        cases = [
            ({"shipping_company_name": city, "courier_id": 0}, True),
            ({"courier_name": city, "courier_code": "0"}, True),
            ({"delivery_method": city}, True),
            ({"shipping_company_name": city, "shipments": [self.pdf]}, True),
            ({"shipping": {"company_name": "مندوب جدة", "company_code": "0"}}, False),
            ({"shipping_company_name": city, "courier_code": "different"}, False),
            ({"shipping_company_code": "0"}, False),
            ({"shipping_company_name": "iMile", "shipping_company_code": "0"}, False),
            ({}, False),
            ({"shipping": {"shipment": {"id": 99, "courier_name": city}}}, False),
            ({"shipments": [{**self.pdf, "courier_name": "مندوب جدة", "meta": {"app_id": 0}}]}, False),
        ]
        with patch.object(legacy, "operational_owner", local_owner):
            for fields, allowed in cases:
                with self.subTest(fields=fields):
                    self.resolve.return_value = self.order_id, {**self.completed, **fields}
                    if not allowed:
                        with self.assertRaises(legacy.ShippingLabelError) as error:
                            await compat.carrier_label(db, "owner", self.number, issue=False, completed_only=True)
                        self.assertEqual(error.exception.code, "shipping_snapshot_changed")
                        continue
                    result = await compat.carrier_label(db, "owner", self.number, issue=False, completed_only=True)
                    self.assertTrue(result["ready"] and result["order_status_completed"])
                    self.assertEqual(result["label_type"], "store_courier")
                    self.assertEqual(result["print_data"]["courier_name"], city)
                    self.assertEqual(result["print_data"]["assigned_courier_name"], "موصل الاختبار")
                    self.assertEqual(result["print_data"]["order_number"], self.number)
                    self.assertTrue(result["print_data"]["qr_code"])
                    self.assertIsNone(result["shipment_id"])
                    self.assertIsNone(result["label_url"])
        self.assertTrue(all(call.args[2] == "GET" for call in self.api.call_args_list))

    async def test_native_named_order_courier_needs_no_shipment_or_driver_assignment(self):
        for name in ["مندوب جدة", "mandoob riyadh", "مندوب المتجر"]:
            with self.subTest(name=name), patch.object(legacy, "_store_identity", new_callable=AsyncMock, return_value={}):
                old = {**self.pdf, "courier_name": "مندوب آخر", "meta": {"app_id": 0}}
                self.resolve.return_value = self.order_id, {
                    **self.completed, "shipping_company_name": name, "shipments": [old],
                }
                label = await compat.carrier_label(None, "owner", self.number, issue=False, completed_only=True)
                self.assertTrue(label["ready"])
                self.assertEqual(label["label_type"], "store_courier")
                self.assertEqual(label["print_data"]["courier_name"], name)
                self.assertIsNone(label["shipment_id"])
                self.assertIsNone(label["label_url"])
        self.api.assert_not_awaited()

    async def test_authoritative_order_city_wins_over_legacy_carrier_display(self):
        from mongomock_motor import AsyncMongoMockClient
        from salla_shipping import CURRENT_SHIPPING

        db = AsyncMongoMockClient().mezan
        key = {"user_id": "owner", "order_number": self.number}
        await db.unified_orders.insert_one({**key, "shipping_company": "مندوب الرياض", CURRENT_SHIPPING: {
            "company_name": "shipping", "source_path": "delivery_method",
        }})
        await db.order_review_workflows.insert_one({**key, "store_courier_assignee_name": "مندوب سابق"})
        self.resolve.return_value = self.order_id, {**self.completed, "shipping_company_name": "مندوب جدة"}
        with patch.object(legacy, "_store_identity", new_callable=AsyncMock, return_value={}):
            label = await compat.carrier_label(db, "owner", self.number, issue=False, completed_only=True)
        self.assertEqual(label["print_data"]["courier_name"], "مندوب جدة")
        self.assertFalse(label["print_data"].get("assigned_courier_name"))
        self.assertIsNone(label["label_url"])
        self.api.assert_not_awaited()

    async def test_native_external_order_identity_does_not_generate_merchant_qr(self):
        for name in ["SMSA", "Unlisted Express"]:
            with self.subTest(name=name):
                self.resolve.return_value = self.order_id, {**self.completed, "shipping_company_name": name}
                self.api.return_value = {"data": []}
                label = await compat.carrier_label(None, "owner", self.number, issue=False, completed_only=True)
                self.assertFalse(label["ready"])
                self.assertNotIn("print_data", label)

    async def test_native_global_list_requires_positive_order_identity_without_local_facts(self):
        for parent, allowed in [(None, False), (314, False), (42, True)]:
            async def reply(db, owner, method, path, **kwargs):
                row = {**self.pdf, "order_id": parent}
                return {"data": [row] if path == "/shipments" else []}
            self.api.side_effect = reply
            result = await compat.carrier_label(None, "owner", self.number, issue=False, completed_only=True)
            self.assertEqual(result["ready"], allowed)
            self.assertEqual(result["label_url"], self.pdf["label"]["url"] if allowed else None)

    async def test_terminal_compat_read_preserves_status_and_strict_native_refuses(self):
        for status in ("shipped", "delivering", "delivered"):
            self.resolve.return_value = self.order_id, {**self.pending, "status": status, "shipments": [self.pdf]}
            web = await compat.carrier_label(None, "owner", self.number, issue=False)
            self.assertTrue(web["ready"])
            self.assertEqual(web["observed_order_status"], status)
            with self.assertRaises(legacy.ShippingLabelError):
                await compat.carrier_label(None, "owner", self.number, issue=False, completed_only=True)
        self.api.assert_not_called()

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

    async def test_fresh_courier_destination_without_reading_history(self):
        database = AsyncMock()
        database.unified_orders.find_one.side_effect = AssertionError("fresh delivery address needs no local read")
        buyer = {"name": "المشتري", "shipping_address": {"address_line": "عنوان المشتري"}}
        recipient = {"name": "المستلم", "phone": "0500000000"}
        current = {"id": 51, "ship_to": {"address_line": "عنوان الشحنة الحالية"},
                   "ship_from": {"address_line": "عنوان المرسل"}}
        for source, expected in [
            ({"recipient": {**recipient, "address": {"address_line": "عنوان المستلم الجديد"}}}, "عنوان المستلم الجديد"),
            ({"shipping": {"address": {"address_line": "عنوان التوصيل الجديد"}}}, "عنوان التوصيل الجديد"),
        ]:
            with self.subTest(source=source):
                order = {"id": 42, "reference_id": self.number, "receiver": recipient, "customer": buyer,
                         "shipments": [{"ship_to": {"address_line": "عنوان بوليصة قديمة"}}], **source}
                selected = await compat._with_verified_destination(database, "owner", self.number, "42", order, current)
                label = legacy._store_courier_print_data(self.number, {**selected, "shipments": []}, {}, {})
                self.assertEqual(label["address"]["address_line"], expected)
                self.assertEqual(label["recipient_name"], "المستلم")
        database.unified_orders.find_one.assert_not_awaited()

    async def test_sparse_order_never_mixes_verified_destination_with_old_shipment(self):
        db = AsyncMock()
        live = {"recipient": {"name": "أمل", "address": {"city": "الرياض"}}}
        shipment = {"id": 51, "ship_to": {"name": "أمل", "city": "الرياض", "street": "شارع قديم"}}
        saved = {"order_id": "42", "salla_address_verified_at": "2026-11-03",
                 "shipping_address_raw": {"name": "أمل", "city": "الرياض", "street": "الشارع الجديد"}}
        for current, record, row, expected in [
            (live, saved, shipment, "الشارع الجديد"), ({}, saved, shipment, "الشارع الجديد"),
            (live, {}, shipment, "شارع قديم"), ({}, {}, shipment, "شارع قديم"),
            (live, {**saved, "shipping_address_raw": {"name": "ليلى", "city": "الرياض", "street": "غير صالح"}},
             None, None),
            (live, {**saved, "shipping_address_raw": {"name": "أمل", "city": "جدة", "street": "غير صالح"}},
             None, None),
        ]:
            with self.subTest(current=current, record=record):
                db.unified_orders.find_one.return_value = record
                selected = await compat._with_verified_destination(db, "owner", self.number, "42", current, row)
                label = legacy._store_courier_print_data(self.number, selected, {}, {})
                self.assertEqual(label["address"].get("street"), expected)

    async def test_independent_recipient_never_inherits_saved_buyer_or_sender_destination(self):
        database = AsyncMock()
        order = {"id": 42, "recipient": {"name": "المستلم", "phone": "0500000000"},
                 "customer": {"name": "المشتري", "shipping_address": {"address_line": "عنوان المشتري"}}}
        for source in ("webhook.customer.address", "shipping.ship_from"):
            with self.subTest(source=source):
                database.unified_orders.find_one.return_value = {
                    "order_id": "42", "shipping_address_raw": {"address_line": "عنوان غير صالح للمستلم"},
                    "salla_address_verified_at": "2026-11-03", "shipping_address_source_path": source}
                document = await compat._with_verified_destination(database, "owner", self.number, "42", order)
                label = legacy._store_courier_print_data(self.number, document, {}, {})
                self.assertEqual(label["recipient_name"], "المستلم")
                self.assertEqual(label["address"], {})

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
        async def local_owner(db, owner, work):
            self.assertIs(db, self.db)
            self.assertEqual(owner, "merchant")
            return await work(db)
        self.local_owner = local_owner
        transaction = patch("operational_atomic.operational_owner", local_owner)
        transaction.start()
        self.addCleanup(transaction.stop)
        self.key = {"user_id": "merchant", "order_number": "12345"}
        self.workflows = self.db.order_review_workflows
        await self.workflows.insert_one({**self.key, "stage": "completed", "assembly_status": "completed"})
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

    async def test_city_courier_from_single_current_order_matched_provider_child(self):
        from salla_shipping import CURRENT_SHIPPING

        city = "مندوب الرياض"
        await self.db.unified_orders.insert_one({**self.key, "order_id": "42",
                                                 CURRENT_SHIPPING: {"company_name": city}})
        await self.workflows.update_one(self.key, {"$set": {"store_courier_assignee_name": "موصل الاختبار"}})
        order = {"id": 42, "reference_id": self.key["order_number"], "status": {"slug": "completed"},
                 "delivery_method": "shipping"}
        current = {"id": 51, "order_id": 42, "courier_id": 88, "courier_name": city,
                   "meta": {"app_id": 0}, "type": "shipment", "status": "created"}
        provider_rows = [current]
        pagination = {}
        async def reply(db, owner, method, path, **kwargs):
            self.assertEqual((owner, method), ("merchant", "GET"))
            if path == "/orders/42":
                return {"data": order}
            if path == "/shipments":
                self.assertEqual(kwargs["params"]["order_id"], "42")
                return {"data": provider_rows, "pagination": pagination}
            return {"data": []}

        cases = [
            ([current], True), ([{**current, "order_id": 43}], False),
            ([{**current, "type": "return"}], False), ([{**current, "status": "cancelled"}], False),
            ([{**current, "status": "delivered"}], False),
            ([{**current, "courier_name": "مندوب جدة"}], False),
            ([current, {**current, "id": 52}], False),
        ]
        with patch.object(compat, "call_salla", side_effect=reply), patch.object(legacy, "operational_owner", self.local_owner):
            for provider_rows, allowed in cases:
                with self.subTest(rows=provider_rows):
                    response = await self.client.post(self.path + "/refresh")
                    self.assertEqual(response.status_code, 200 if allowed else 409)
                    if allowed:
                        result = response.json()
                        self.assertEqual(result["print_state"], "ready")
                        self.assertEqual(result["label_type"], "store_courier")
                        self.assertEqual(result["print_data"]["courier_name"], city)
                        self.assertEqual(result["print_data"]["assigned_courier_name"], "موصل الاختبار")
                        self.assertTrue(result["assembly_completion_confirmed"] and result["order_status_completed"])
                        self.assertIsNone(result["label_url"])
                    else:
                        self.assertEqual(response.json()["detail"]["code"], "shipping_snapshot_changed")
            provider_rows = [current]
            for pagination in ({"totalPages": 2}, {"total_pages": 3}, {"links": {"next": "next-page"}}):
                response = await self.client.post(self.path + "/refresh")
                self.assertEqual(response.status_code, 409)
            pagination = {}
            for metadata, allowed in [
                ({"superseded_shipment_ids": ["23"]}, True),
                ({"source_path": "delivery_method", "company_name": "shipping"}, True),
                ({"source_path": "shipping_company", "company_name": "iMile"}, False),
            ]:
                with self.subTest(metadata=metadata):
                    await self.db.unified_orders.update_one(self.key, {"$set": {
                        CURRENT_SHIPPING: metadata, "shipping_company": city,
                    }})
                    response = await self.client.post(self.path + "/refresh")
                    self.assertEqual(response.status_code, 200 if allowed else 409)
                    if allowed:
                        self.assertEqual(response.json()["print_data"]["assigned_courier_name"], "موصل الاختبار")
                    else:
                        self.assertEqual(response.json()["detail"]["code"], "shipping_snapshot_changed")
            for changed in [{"shipment_id": "999"}, {"superseded_shipment_ids": ["51"]}]:
                await self.db.unified_orders.update_one(self.key, {"$set": {
                    CURRENT_SHIPPING: {"company_name": city, **changed},
                }})
                response = await self.client.post(self.path + "/refresh")
                self.assertEqual(response.status_code, 409)
                self.assertEqual(response.json()["detail"]["code"], "shipping_snapshot_changed")

    async def seed_current_mezan_courier(self):
        from salla_shipping import CURRENT_SHIPPING
        number = self.key["order_number"]
        raw = {"id": 42, "reference_id": number, "date": "2026-01-01", "status": {"slug": "completed"},
               "delivery_method": "shipping", "receiver": {"name": "مستلم الاختبار"}}
        await self.db.unified_orders.insert_one({
            **self.key, "order_id": "local-assembly-42", "order_date": "2026-01-01", "order_status": "completed",
            "raw_by_source": {"salla_direct": raw}, "shipping_company": "مندوب الرياض",
            "salla_address_verified_at": "2026-01-01", "shipping_address_source_path": "shipping.address",
            "shipping_address_raw": {"city": "الرياض", "street": "الشارع الحالي"},
            CURRENT_SHIPPING: {"company_name": "SMSA", "shipment_id": "999", "label_url": "https://old.example/awb.pdf"},
        })
        await self.workflows.update_one(self.key, {"$set": {"salla_order_status_verified_at": "2026-01-01",
            "shipping_status_epoch": 14, "carrier_name": "ناقل قديم"}})
        return number

    async def test_native_prints_current_mezan_courier_without_provider_or_previous_shipment(self):
        number = await self.seed_current_mezan_courier()
        with patch.object(compat, "call_salla", side_effect=AssertionError("unexpected carrier provider read")), \
             patch.object(legacy, "call_salla", side_effect=AssertionError("unexpected store info / provider read")):
            for path, city, street in [(self.path, "مندوب الرياض", "الشارع الحالي"),
                                       (self.path + "/refresh", "مندوب جدة", "عنوان المندوب الحالي")]:
                await self.db.unified_orders.update_one(self.key, {"$set": {"shipping_company": city,
                    "shipping_address_raw.street": street}})
                response = await self.client.post(path)
                self.assertEqual(response.status_code, 200, response.text)
                label = response.json()
                self.assertEqual((label["label_type"], label["print_state"]), ("store_courier", "ready"))
                self.assertEqual(label["courier_name"], city)
                self.assertIsNone(label["shipment_id"])
                self.assertIsNone(label["label_url"])
                self.assertEqual(label["print_data"]["barcode_value"], number)
                self.assertTrue(label["print_data"]["qr_code"].startswith("data:image/svg+xml;base64,"))
                self.assertEqual(label["print_data"]["address"]["street"], street)
                self.assertEqual(label["print_data"]["customer_name"], "مستلم الاختبار")
        saved = await self.workflows.find_one(self.key)
        self.assertEqual(saved["salla_order_status_verified_at"], "2026-01-01")
        self.assertTrue(saved["carrier_label_ready"])
        self.assertFalse(saved.get("carrier_label_print_confirmed"))

    async def test_local_courier_final_read_accepts_new_city_epoch_and_tracking_but_not_external(self):
        await self.seed_current_mezan_courier()
        for name, local in [("مندوب جدة", True), ("SMSA", False)]:
            await self.db.unified_orders.update_one(self.key, {"$set": {"shipping_company": "مندوب الرياض"}})
            async def switch(db, owner, callback):
                await db.unified_orders.update_one(self.key, {"$set": {
                    "shipping_company": name, "tracking_number": "new-awb", "salla_shipment_id": "old",
                    "shipping_address_raw.street": "عنوان حالي",
                }})
                await self.workflows.update_one(self.key, {"$inc": {"shipping_status_epoch": 1}})
                return await self.local_owner(db, owner, callback)
            with patch("operational_atomic.operational_owner", side_effect=switch), \
                 patch.object(compat, "carrier_label", new_callable=AsyncMock, return_value=self.pdf) as external:
                response = await self.client.post(self.path + "/refresh")
                if local:
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.json()["print_data"]["courier_name"], name)
                    self.assertEqual(response.json()["print_data"]["address"]["street"], "عنوان حالي")
                    external.assert_not_awaited()
                else:
                    external.assert_awaited_once()
                    self.assertNotEqual(response.json().get("label_type"), "store_courier")

    async def test_local_document_uses_current_recipient_address_and_merchant_brand(self):
        await self.seed_current_mezan_courier()
        await self.db.salla_integrations.insert_one({"user_id": "merchant", "store_name": "متجر الاختبار"})
        await self.db.unified_orders.update_one(self.key, {"$set": {
            "raw_by_source.salla_direct.updated_at": "2026-02-01T00:00:00Z",
            "raw_by_source.salla_direct.receiver.address": {"street": "عنوان المستلم الجديد"},
            "raw_by_source.salla_direct.customer.shipping_address": {"street": "عنوان المشتري"},
            "salla_address_provider_updated_at": "2026-01-01T00:00:00Z",
        }})
        response = await self.client.post(self.path + "/refresh")
        self.assertEqual(response.status_code, 200)
        data = response.json()["print_data"]
        self.assertEqual(data["store_name"], "متجر الاختبار")
        self.assertEqual(data["address"]["street"], "عنوان المستلم الجديد")
        await self.db.unified_orders.update_one(self.key, {"$set": {
            "salla_address_provider_updated_at": "2026-03-01T00:00:00Z",
            "shipping_address_raw.name": "شخص آخر",
        }})
        response = await self.client.post(self.path + "/refresh")
        self.assertEqual(response.json()["print_data"]["address"]["street"], "عنوان المستلم الجديد")

    async def test_local_merchant_requires_correct_order_and_completed_assembly_and_status(self):
        await self.seed_current_mezan_courier()
        with patch.object(compat, "carrier_label", new_callable=AsyncMock, return_value=self.pdf) as engine:
            await self.db.unified_orders.update_one(self.key, {"$set": {"raw_by_source.salla_direct.id": ""}})
            rejected = await self.client.post(self.path)
            self.assertEqual(rejected.status_code, 409)
            engine.assert_not_awaited()
            await self.db.unified_orders.update_one(self.key, {"$set": {
                "raw_by_source.salla_direct.id": 42, "order_status": "cancelled", "raw_by_source.salla_direct.status.slug": "cancelled",
            }})
            response = await self.client.post(self.path + "/refresh")
            self.assertEqual(response.status_code, 409)
            engine.assert_not_awaited()
            await self.workflows.update_one(self.key, {"$set": {"assembly_status": "pending"}})
            self.assertEqual((await self.client.post(self.path)).status_code, 409)
            await self.workflows.update_one(self.key, {"$set": {"assembly_status": "completed", "stage": "delivering"}})
            self.assertEqual((await self.client.post(self.path)).status_code, 409)

    async def test_handoff_rejects_issue_and_only_fresh_in_progress_uses_existing_outbox(self):
        from assembly_completion_delivery import pending_operation
        journal = pending_operation("merchant", "test")
        await self.workflows.update_one(self.key, {"$set": {
            "stage": "delivering", "assembly_delivery": journal,
        }})
        with patch.object(compat, "carrier_label", new_callable=AsyncMock) as engine, \
             patch("assembly_completion_delivery._deliver", new_callable=AsyncMock) as dispatch:
            response = await self.client.post(self.path)
            self.assertEqual(response.status_code, 409)
            engine.assert_not_awaited()
            await self.workflows.update_one(self.key, {"$set": {"stage": "completed"}})
            engine.side_effect = legacy.ShippingLabelError("order_status_not_completed", "blocked")
            self.assertEqual((await self.client.post(self.path)).status_code, 409)
            dispatch.assert_not_awaited()
            engine.side_effect = legacy.ShippingLabelError("carrier_order_in_progress", "in progress")
            async def ambiguous(db, operation):
                await operation["_authorize_first_issue"]()
                await self.workflows.update_one(self.key, {"$set": {"assembly_delivery.status_attempted": True}})
                raise TimeoutError("ambiguous")
            dispatch.side_effect = ambiguous
            for _ in range(2):
                response = await self.client.post(self.path)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["print_state"], "pending")
                # Make even the second tap due, eliminating scheduler delay as a guard.
                await self.workflows.update_one(self.key, {"$set": {"assembly_delivery.due_at": ""}})
            dispatch.assert_awaited_once()
            self.assertTrue(all(call.kwargs == {"issue": False, "completed_only": True}
                                for call in engine.await_args_list))

    async def test_issue_and_refresh_expose_versioned_print_states(self):
        with patch.object(compat, "carrier_label", new_callable=AsyncMock) as engine:
            engine.return_value = self.pdf
            response = await self.client.post(self.path)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), {**self.pdf, "order_number": "12345",
                             "assembly_completion_confirmed": True, "contract_version": 1, "print_state": "ready"})
            engine.assert_awaited_once_with(self.db, "merchant", "12345", issue=False, completed_only=True)
            engine.return_value = {**self.pdf, "ready": False, "label_url": None}
            response = await self.client.post(self.path + "/refresh")
            self.assertEqual(response.json()["print_state"], "pending")
            engine.assert_awaited_with(self.db, "merchant", "12345", issue=False, completed_only=True)

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

        async def revoke(db, owner, number, *, issue, completed_only=False):
            await permissions.update_one(role, {"$set": {"enabled": False}})
            return {**self.pdf, "shipment_id": "unexpected"}

        with patch.object(compat, "carrier_label", new_callable=AsyncMock) as engine:
            engine.return_value = self.pdf
            first = await self.client.post(self.path + "/refresh")
            self.assertEqual(first.status_code, 200)
            engine.assert_awaited_once_with(self.db, "merchant", "12345", issue=False, completed_only=True)
            engine.side_effect = revoke
            blocked = await self.client.post(self.path + "/refresh")
            self.assertEqual(blocked.status_code, 403)
            self.assertEqual(blocked.json()["detail"]["print_state"], "failed")
            self.assertEqual((await self.workflows.find_one(self.key))["carrier_shipment_id"], "42")

    async def test_native_bridge_rechecks_assembly_grant_before_and_after_provider(self):
        from mobile_app_permissions import MOBILE_APP_ACCESS, MOBILE_APP_ACCESS_OWNER_FIELD
        from mobile_app_request_context import mobile_app_request_user

        employee = {"id": "packer", "role": "viewer", "created_by": "merchant",
                    "_session_client": "amasi_mobile"}
        await self.db.users.insert_many([dict(self.actor), dict(employee)])
        grant = self.db[MOBILE_APP_ACCESS]
        key = {MOBILE_APP_ACCESS_OWNER_FIELD: "merchant", "user_id": employee["id"]}
        handoff = {"permissions": ["app.page.carrier_handoff"]}
        await grant.insert_one({**key, **handoff})
        # Even handoff-only users are bridged to the owner's ID on /completed.
        async def bridge():
            return await mobile_app_request_user(
                self.db, employee, path="/api" + self.path, method="POST",
            )
        self.actor = await bridge()
        self.assertEqual(self.actor["id"], "merchant")
        self.assertEqual(self.actor["_mobile_actor_id"], "packer")
        with patch.object(compat, "carrier_label", new_callable=AsyncMock) as engine:
            engine.return_value = self.pdf
            for path in (self.path, self.path + "/refresh"):
                self.assertEqual((await self.client.post(path)).status_code, 403)
            engine.assert_not_awaited()
            await grant.update_one(key, {"$set": {"permissions": ["app.page.assembly_shipping"]}})
            self.actor = await bridge()
            self.assertEqual((await self.client.post(self.path + "/refresh")).status_code, 200)
            engine.assert_awaited_once_with(self.db, "merchant", "12345", issue=False, completed_only=True)

            async def revoke(*args, **kwargs):
                await grant.update_one(key, {"$set": handoff})
                return {**self.pdf, "shipment_id": "unexpected"}

            engine.side_effect = revoke
            self.assertEqual((await self.client.post(self.path + "/refresh")).status_code, 403)
            self.assertEqual((await self.workflows.find_one(self.key))["carrier_shipment_id"], "42")

    async def test_current_shipment_changed_during_lookup_suppresses_old_label(self):
        from salla_shipping import CURRENT_SHIPPING
        await self.db.unified_orders.insert_one({**self.key, CURRENT_SHIPPING: {"shipment_id": "42"}})
        async def replacement(*args, **kwargs):
            await self.db.unified_orders.update_one(self.key, {"$set": {
                CURRENT_SHIPPING: {"shipment_id": "10", "superseded_shipment_ids": ["42"]},
            }})
            return self.pdf
        with patch.object(compat, "carrier_label", side_effect=replacement):
            response = await self.client.post(self.path + "/refresh")
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["detail"]["code"], "shipping_snapshot_changed")
        self.assertNotIn("carrier_label_url", await self.workflows.find_one(self.key))

    async def test_slow_web_refresh_cannot_overwrite_newer_mobile_refresh(self):
        import asyncio

        started, resume = asyncio.Event(), asyncio.Event()
        async def provider(db, owner, number, *, issue, completed_only=False):
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
