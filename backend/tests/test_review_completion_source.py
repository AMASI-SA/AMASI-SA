"""Semantic source regression, plus real Mongo/ASGI refresh-webhook-resume replay."""
import asyncio
from copy import deepcopy
import unittest
from unittest.mock import patch

from fastapi import HTTPException

import order_review_completion as completion
import order_review_resume_worker as worker
import test_review_completion_auto_resume as resume
import test_g47_component_lifecycle_integration as fixture


class SourceFingerprintTests(unittest.TestCase):
    def snapshot(self):
        return {"raw_by_source": {"salla_direct": {
            "id": 123, "reference_id": 456,
            "shipping": {"company": "Synthetic carrier", "shipping_method": "door",
                         "address": {"city": "City", "street": "Street"}},
            "customer": {"name": "Synthetic buyer", "phone": "0500000000"},
            "items": [{"id": 11, "product": {"id": 22}, "sku": "SKU-1", "quantity": 1,
                       "options": [{"id": 33, "value": "blue"}],
                       "customer_options": [{"name": "engraving", "value": "AB"}]}],
            "payment_method": "cod", "amounts": {"total": 100},
        }}}

    def test_refresh_alias_reproduces_old_failure_and_new_equivalence(self):
        before = self.snapshot()
        after = deepcopy(before)
        shipping = after["raw_by_source"]["salla_direct"]["shipping"]
        shipping["company_name"] = shipping["company"]
        self.assertNotEqual(completion.source_fingerprint(before, 1), completion.source_fingerprint(after, 1))
        self.assertEqual(completion.source_fingerprint(before), completion.source_fingerprint(after))
        shipping.pop("company")
        self.assertEqual(completion.source_fingerprint(before), completion.source_fingerprint(after))

    def test_other_proven_aliases_and_json_number_serialization(self):
        before = self.snapshot()
        after = deepcopy(before)
        raw = after["raw_by_source"]["salla_direct"]
        raw["shipping"]["method"] = raw["shipping"].pop("shipping_method")
        raw["shipping_address"] = raw["shipping"].pop("address")
        raw["customer"]["full_name"] = raw["customer"].pop("name")
        raw["customer"]["mobile"] = raw["customer"].pop("phone")
        raw["items"][0].update(product_id=22, quantity=1.0)
        raw["status"] = {"slug": "under_review", "customized": {"name": "تم المراجعة"}}
        raw["updated_at"] = "2030-01-01"
        self.assertEqual(completion.source_fingerprint(before), completion.source_fingerprint(after))
        self.assertEqual(before, self.snapshot(), "canonicalization must not mutate source")

    def test_commercial_changes_remain_distinct(self):
        changes = [
            ("items", 0, "quantity", 2), ("items", 0, "sku", "SKU-2"),
            ("items", 0, "product", {"id": 23}),
            ("items", 0, "variant_id", "other"),
            ("items", 0, "options", [{"id": 33, "value": "red"}]),
            ("items", 0, "customer_options", [{"name": "engraving", "value": "CD"}]),
            ("shipping", "company", "Different carrier"),
            ("shipping", "address", {"city": "Elsewhere", "street": "Street"}),
            ("shipping", "recipient", {"name": "Another recipient"}),
            ("customer", "phone", "0511111111"), ("payment_method", "bank"),
            ("remaining_amount", 50), ("id", 999), ("reference_id", 999),
            ("items", 0, "new_business_field", "must remain guarded"),
        ]
        before = self.snapshot()
        for path in changes:
            with self.subTest(path=path):
                after = deepcopy(before)
                target = after["raw_by_source"]["salla_direct"]
                for key in path[:-2]:
                    target = target[key]
                target[path[-2]] = path[-1]
                self.assertNotEqual(completion.source_fingerprint(before), completion.source_fingerprint(after))

    def test_conflicting_aliases_are_not_accepted_by_precedence(self):
        for section, key, value in (("shipping", "company_name", "Other"),
                                    ("customer", "full_name", "Other buyer")):
            with self.subTest(section=section):
                snapshot = self.snapshot()
                snapshot["raw_by_source"]["salla_direct"][section][key] = value
                with self.assertRaises(HTTPException) as raised:
                    completion.source_fingerprint(snapshot)
                self.assertEqual(raised.exception.status_code, 409)
                self.assertEqual(raised.exception.detail["code"], "review_completion_source_changed")

    def test_unknown_fingerprint_version_fails_closed(self):
        with self.assertRaises(HTTPException):
            completion.source_fingerprint(self.snapshot(), 999)


class CanonicalResumeTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = resume.AutoResumeTests.asyncSetUp
    asyncTearDown = resume.AutoResumeTests.asyncTearDown
    source_payload = resume.AutoResumeTests.source_payload
    webhook = resume.AutoResumeTests.webhook
    refresh = resume.AutoResumeTests.refresh
    saved = resume.AutoResumeTests.saved
    assert_completed = resume.AutoResumeTests.assert_completed
    assert_blocked = resume.AutoResumeTests.assert_blocked

    async def pending(self):
        payload = self.source_payload(number="new-review")
        payload["shipping"] = {"company": "Synthetic carrier", "method": "door"}
        self.assertTrue((await self.webhook(payload))["synced"])
        self.assertTrue((await self.refresh(payload))["ok"])
        raw = (await self.db.unified_orders.find_one({}))["raw_by_source"]["salla_direct"]
        self.assertEqual(raw["shipping"]["company_name"], "Synthetic carrier")
        await self.db.create_collection(completion.EVENTS)
        await self.db.command({"collMod": completion.EVENTS,
            "validator": {"event_type": {"$ne": "order_review_completed"}}, "validationLevel": "strict"})
        response = await self.client.post("/order-reviews-v1/new-review/complete", json={"expected_revision": 0})
        self.assertEqual(response.status_code, 500, response.text)
        op = await self.saved()
        self.assertEqual(op["state"], "provider_confirmed")
        self.assertEqual(op["fingerprint_version"], 2)
        self.assertEqual(await self.db[completion.WORKFLOWS].count_documents({}), 0)
        self.assertEqual((await self.client.get("/order-reviews-v1/reviewed")).json()["items"], [])
        await self.db.command({"collMod": completion.EVENTS, "validator": {}})
        payload["updated_at"] = fixture.LATER
        payload["status"]["customized"] = {"name": "تم المراجعة"}
        self.assertTrue((await self.webhook(payload, event="order.updated"))["synced"])
        return op

    async def test_refresh_confirmation_webhook_restart_resume_visible_once(self):
        op = await self.pending()
        task = await worker.start_worker(self.db)
        try:
            for _ in range(100):
                if (await self.saved())["state"] == "completed":
                    break
                await asyncio.sleep(.05)
            await self.assert_completed(op)
        finally:
            await worker.stop_worker(task)
        self.assertEqual(await worker.run_once(self.db), 0)
        await self.assert_completed(op)

    async def test_two_workers_plus_manual_retry_same_operation(self):
        op = await self.pending()
        results = await asyncio.gather(worker.run_once(self.db), worker.run_once(self.db),
            self.client.post("/order-reviews-v1/new-review/complete", json={"expected_revision": 0}))
        self.assertIn(results[-1].status_code, (200, 409))
        await self.assert_completed(op)

    async def test_real_quantity_change_after_confirmation_blocks_resume(self):
        await self.block_change("items.0.quantity", 3)

    async def block_change(self, path, value):
        await self.pending()
        await self.db.unified_orders.update_one({}, {"$set": {"raw_by_source.salla_direct." + path: value}})
        await worker.run_once(self.db)
        await self.assert_blocked()

    async def test_real_product_change_blocks_resume(self):
        await self.block_change("items.0.product_id", "another-product")

    async def test_real_sku_change_blocks_resume(self):
        await self.block_change("items.0.sku", "ANOTHER-SKU")

    async def test_real_option_change_blocks_resume(self):
        await self.block_change("items.0.options", [{"name": "Size", "value": "XL"}])

    async def test_real_customer_selection_blocks_resume(self):
        await self.block_change("items.0.customer_options", [{"name": "Gift text", "value": "Changed"}])

    async def test_real_shipping_change_blocks_resume(self):
        await self.block_change("shipping.company", "Different carrier")

    async def test_old_source_hash_reproduces_requires_review_at_same_boundary(self):
        original = completion.source_fingerprint
        with patch.object(completion, "source_fingerprint", side_effect=lambda snapshot, *args: original(snapshot, 1)):
            await self.pending()
            await worker.run_once(self.db)
            await self.assert_blocked("review_completion_source_changed")

    async def test_acceptance_change_after_confirmation_keeps_specific_conflict(self):
        await self.pending()
        await self.db.order_review_acceptance_config_versions.update_one({"_id": "owner"}, {"$inc": {"version": 1}})
        await worker.run_once(self.db)
        await self.assert_blocked("component_acceptance_changed")

    async def test_unversioned_operation_keeps_original_hash_contract(self):
        op = await self.pending()
        import order_review_routes as review
        source = await self.db.unified_orders.find_one({})
        order = await review.get_order(review.MongoOrderRepository(self.db), user_id="owner", order_number="new-review")
        await self.db[completion.OPERATIONS].update_one({"_id": op["_id"]}, {
            "$unset": {"fingerprint_version": ""}, "$set": {
                "source_fingerprint": completion.source_fingerprint(source, 1),
                "order_fingerprint": completion.order_fingerprint(order, 1)}})
        old = await self.saved()
        await worker.run_once(self.db)
        await self.assert_completed(old)
        self.assertNotIn("fingerprint_version", await self.saved())
