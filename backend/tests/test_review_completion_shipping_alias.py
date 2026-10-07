"""Narrow carrier alias contract; real Mongo, synthetic orders/provider only."""
import asyncio
from copy import deepcopy
import unittest
from unittest.mock import patch

from fastapi import HTTPException
import order_review_completion as completion
import order_review_routes as routes
import order_review_resume_worker as worker
import test_review_completion_auto_resume as fixture


class ShippingAliasTests(unittest.TestCase):
    def source(self, shipping):
        return {"raw_by_source": {"salla_direct": {"shipping": shipping}}}

    def test_alias_contract_and_metadata(self):
        for company in ("SMSA", {"name": "SMSA", "id": 7, "code": "s", "extra": {"x": 1}}):
            with self.subTest(company=company):
                before = self.source({"company": company})
                after = self.source({"company": company, "company_name": "SMSA"})
                original = deepcopy(after)
                self.assertEqual(completion.source_fingerprint(before), completion.source_fingerprint(after))
                self.assertEqual(after, original)
                for alias in ("iMile", "", None):
                    with self.assertRaises(HTTPException) as error:
                        completion.source_fingerprint(self.source({"company": company, "company_name": alias}))
                    self.assertEqual(error.exception.detail["code"], "review_completion_source_changed")
        self.assertEqual(completion.source_fingerprint(self.source({"company_name": "SMSA"})),
                         completion.source_fingerprint(self.source({"company": "SMSA"})))
        for value in (None, ""):
            self.assertNotEqual(completion.source_fingerprint(self.source({"company_name": value})),
                                completion.source_fingerprint(self.source({})))
        base = completion.source_fingerprint(self.source({"company": {"name": "SMSA", "id": 7}}))
        for company in ("SMSA", {"name": "SMSA", "id": 8}, {"name": "SMSA"}):
            self.assertNotEqual(base, completion.source_fingerprint(self.source({"company": company})))
        self.assertNotEqual(completion.source_fingerprint(self.source({"company": "SMSA"})),
                            completion.source_fingerprint(self.source({"company": "SMSA", "unknown": 1})))

    def test_version_contract(self):
        before = self.source({"company": "SMSA"})
        after = self.source({"company": "SMSA", "company_name": "SMSA"})
        self.assertNotEqual(completion.source_fingerprint(before, version=1),
                            completion.source_fingerprint(after, version=1))
        for version in (None, 0, 3, "2", True, 2.0):
            with self.subTest(version=version), self.assertRaises(HTTPException):
                completion.source_fingerprint(before, version=version)


class ShippingAliasIntegrationTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.AutoResumeTests.asyncSetUp
    asyncTearDown = fixture.AutoResumeTests.asyncTearDown
    source_payload = fixture.AutoResumeTests.source_payload
    webhook = fixture.AutoResumeTests.webhook
    refresh = fixture.AutoResumeTests.refresh
    saved = fixture.AutoResumeTests.saved
    assert_completed = fixture.AutoResumeTests.assert_completed

    async def seed(self):
        payload = self.source_payload(number="new-review")
        payload["shipping"] = {"company": {"name": "SMSA", "id": 7, "code": "smsa"}}
        self.assertTrue((await self.webhook(payload))["synced"])
        return payload

    async def complete(self):
        return await self.client.post("/order-reviews-v1/new-review/complete", json={"expected_revision": 0})

    async def pending(self):
        payload = await self.seed()
        await self.db.create_collection(completion.EVENTS)
        await self.db.command({"collMod": completion.EVENTS,
            "validator": {"event_type": {"$ne": "order_review_completed"}}})
        response = await self.complete()
        self.assertEqual(response.status_code, 500, response.text)
        original = await self.saved()
        self.assertEqual(original["state"], "provider_confirmed")
        self.assertEqual((await self.client.get("/order-reviews-v1/reviewed")).json()["items"], [])
        await self.db.command({"collMod": completion.EVENTS, "validator": {}})
        return payload, original

    async def test_provider_confirmation_refresh_alias_completion_visible(self):
        payload = await self.seed()
        before = await self.db.unified_orders.find_one({})
        async def provider(*args):
            self.assertTrue((await self.refresh(payload))["ok"])
            after = await self.db.unified_orders.find_one({})
            self.assertEqual(after["raw_by_source"]["salla_direct"]["shipping"]["company_name"], "SMSA")
            self.assertNotEqual(before["raw_by_source"]["salla_direct"]["shipping"],
                                after["raw_by_source"]["salla_direct"]["shipping"])
            return "sent", None
        self.provider.side_effect = provider
        response = await self.complete()
        saved = await self.saved()
        print({"http": response.status_code, "operation_state": saved["state"],
               "provider_confirmed": bool(saved.get("provider_confirmed_at")),
               "workflows": await self.db[completion.WORKFLOWS].count_documents({}),
               "completion_events": await self.db[completion.EVENTS].count_documents({"event_type": "order_review_completed"})})
        self.assertEqual(response.status_code, 200, response.text)
        original = await self.saved()
        self.assertTrue(original["provider_confirmed_at"])
        await self.assert_completed(original)
        self.assertEqual((await self.complete()).status_code, 200)
        await self.assert_completed(original)
        self.provider.assert_awaited_once()

    async def test_alias_after_confirmation_survives_process_restart(self):
        payload, original = await self.pending()
        await self.refresh(payload)
        # Reuse the real two-process kill/restart test with the already persisted
        # provider-confirmed operation, after actual refresh enrichment.
        async def already_pending():
            return original
        self.pending = already_pending
        await fixture.AutoResumeTests.test_worker_process_restart_uses_persisted_operation(self)
        self.assertEqual((await self.saved())["source_fingerprint_version"], 2)

    async def test_company_only_completes(self):
        await self.seed()
        self.assertEqual((await self.complete()).status_code, 200)
        await self.assert_completed(await self.saved())

    async def test_old_unchanged_operation_resumes_without_version_upgrade(self):
        _, original = await self.pending()
        source = await self.db.unified_orders.find_one({})
        old_hash = completion.source_fingerprint(source, version=1)
        await self.db[completion.OPERATIONS].update_one({}, {"$unset": {"source_fingerprint_version": ""},
            "$set": {"source_fingerprint": old_hash}})
        await worker.run_once(self.db)
        original["source_fingerprint"] = old_hash
        await self.assert_completed(original)
        self.assertNotIn("source_fingerprint_version", await self.saved())

    async def test_confirmed_operation_alias_worker_and_manual_race(self):
        payload, original = await self.pending()
        self.assertTrue((await self.refresh(payload))["ok"])
        await asyncio.gather(worker.run_once(self.db), worker.run_once(self.db), self.complete())
        await self.assert_completed(original)
        self.assertEqual((await self.saved())["source_fingerprint_version"], 2)
        self.assertEqual(await worker.run_once(self.db), 0)

    async def test_changed_business_and_unknown_shipping_reject(self):
        _, original = await self.pending()
        source = await self.db.unified_orders.find_one({})
        changes = [("items.0.product_id", "other"), ("items.0.quantity", 9),
            ("items.0.sku", "other"), ("items.0.options", [{"value": "other"}]),
            ("payment_method", "bank"), ("amounts.total.amount", 999),
            ("shipping.company.id", 8), ("shipping.company_name", "iMile"),
            ("shipping.company_name", ""), ("shipping.company_name", None),
            ("shipping.unknown", {"business": 1})]
        for path, value in changes:
            with self.subTest(path=path, value=value):
                await self.db.unified_orders.replace_one({"_id": source["_id"]}, deepcopy(source))
                await self.db.unified_orders.update_one({}, {"$set": {"raw_by_source.salla_direct." + path: value}})
                response = await self.complete()
                self.assertEqual(response.status_code, 409, response.text)
                self.assertEqual(await self.db[completion.WORKFLOWS].count_documents({}), 0)
                self.assertEqual(await self.db[completion.EVENTS].count_documents({}), 0)
                self.assertEqual((await self.saved())["source_fingerprint"], original["source_fingerprint"])

    async def test_dto_guard_still_rejects(self):
        await self.pending()
        get_order = routes.get_order
        async def changed(*args, **kwargs):
            order = await get_order(*args, **kwargs)
            return order.model_copy(update={"customer_notes": "changed DTO only"})
        with patch.object(routes, "get_order", changed):
            response = await self.complete()
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(await self.db[completion.EVENTS].count_documents({}), 0)

    async def test_old_operation_is_not_rehashed(self):
        payload, original = await self.pending()
        source = await self.db.unified_orders.find_one({})
        old_hash = completion.source_fingerprint(source, version=1)
        await self.db[completion.OPERATIONS].update_one({}, {"$unset": {"source_fingerprint_version": ""},
            "$set": {"source_fingerprint": old_hash}})
        await self.refresh(payload)
        await worker.run_once(self.db)
        saved = await self.saved()
        self.assertEqual(saved["state"], "requires_review")
        self.assertEqual(saved["_id"], original["_id"])
        self.assertEqual(saved["source_fingerprint"], old_hash)
        self.assertNotIn("source_fingerprint_version", saved)
        self.assertEqual(await self.db[completion.WORKFLOWS].count_documents({}), 0)

    async def test_unknown_operation_version_rejects(self):
        await self.pending()
        await self.db[completion.OPERATIONS].update_one({}, {"$set": {"source_fingerprint_version": 999}})
        response = await self.complete()
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["detail"]["code"], "review_completion_fingerprint_version_unsupported")
        self.assertEqual(await self.db[completion.EVENTS].count_documents({}), 0)
