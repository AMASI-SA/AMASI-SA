"""Synthetic approval contracts on loopback Mongo. Never production recovery."""
import asyncio
from copy import deepcopy
import unittest
from unittest.mock import patch

import order_review_completion as completion
import order_review_business_snapshot as snapshots
import test_review_completion_representation_integration as fixture


class ApprovalScopeTests(unittest.TestCase):
    def build(self, raw, dto=None, version=2):
        return snapshots.build_snapshot({"raw_by_source": {"salla_direct": raw}},
            dto or {"order_id": "synthetic"}, {"config_version": 1},
            identity={"_id": "synthetic"}, schema_version=version)

    def test_delivery_projection_preserves_originals_and_financial_siblings(self):
        raw = {"shipping": {"address": {"street": "one"}, "company": "A",
            "company_name": "conflict", "label_url": "label", "cod_fee": 5},
            "shipping_address": {"formatted": "different"},
            "customer": {"id": 7, "shipping_address": {"city": "one"}}}
        dto = deepcopy(raw)
        original = deepcopy((raw, dto))
        before = self.build(raw, dto)
        for field in snapshots.DELIVERY_FIELDS:
            with self.subTest(field=field):
                changed = deepcopy(raw)
                changed["shipping"][field] = {"different": "delivery-only"}
                self.assertTrue(snapshots.compare_snapshots(before, self.build(changed, dto))["equal"])
        self.assertEqual((raw, dto), original)
        for field in ("cod_fee", "method", "unknown_business_field"):
            with self.subTest(protected=field):
                changed = deepcopy(raw)
                changed["shipping"][field] = 9
                self.assertFalse(snapshots.compare_snapshots(before, self.build(changed, dto))["equal"])

    def test_dto_address_copies_excluded_but_customer_identity_protected(self):
        raw = {"items": []}
        dto = {"customer": {"id": 7, "shipping_address": {"city": "one"}},
               "shipping": {"address": {"street": "one"}, "company": "A"}}
        before = self.build(raw, dto)
        changed = deepcopy(dto)
        changed["shipping"].update(address=None, company="B", label_url="new")
        changed["customer"]["shipping_address"] = {"city": "two"}
        self.assertTrue(snapshots.compare_snapshots(before, self.build(raw, changed))["equal"])
        changed["customer"]["id"] = 8
        self.assertFalse(snapshots.compare_snapshots(before, self.build(raw, changed))["equal"])

    def test_versions_do_not_mix_or_accept_unknown_contracts(self):
        raw = {"shipping": {"company": "A"}}
        old = self.build(raw, version=1)
        new = self.build(raw)
        self.assertTrue(snapshots.verify_snapshot(old))
        self.assertTrue(snapshots.verify_snapshot(new))
        self.assertFalse(snapshots.compare_snapshots(old, new)["equal"])
        with self.assertRaises(Exception):
            self.build(raw, version=99)
        changed = {"shipping": {"company": "B"}}
        self.assertFalse(snapshots.compare_snapshots(old, self.build(changed, version=1))["equal"])


class ApprovalScopeMongoTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.RepresentationIntegrationTests.asyncSetUp
    asyncTearDown = fixture.RepresentationIntegrationTests.asyncTearDown
    source_payload = fixture.RepresentationIntegrationTests.source_payload
    webhook = fixture.RepresentationIntegrationTests.webhook
    refresh = fixture.RepresentationIntegrationTests.refresh
    saved = fixture.RepresentationIntegrationTests.saved
    assert_completed = fixture.RepresentationIntegrationTests.assert_completed
    seed = fixture.RepresentationIntegrationTests.seed
    pending = fixture.RepresentationIntegrationTests.pending

    async def post(self):
        return await self.client.post("/order-reviews-v1/new-review/complete", json={"expected_revision": 0})

    async def test_conflicting_addresses_at_claim_complete_without_changing_source(self):
        await self.seed()
        await self.db.unified_orders.update_one({}, {"$set": {
            "raw_by_source.salla_direct.shipping.address": {"city": "one", "street": "one"},
            "raw_by_source.salla_direct.shipping_address": {"city": "two", "formatted": "two"},
            "raw_by_source.salla_direct.shipping.company_name": "conflicting carrier",
            "raw_by_source.salla_direct.shipping.label_url": "synthetic-label"}})
        before = await self.db.unified_orders.find_one({})
        response = await self.post()
        self.assertEqual(response.status_code, 200, response.text)
        op = await self.saved()
        self.assertEqual(op["approval_contract_version"], 2)
        await self.assert_completed(op)
        self.assertEqual((await self.db.unified_orders.find_one({}))["raw_by_source"], before["raw_by_source"])
        self.provider.assert_awaited_once()
        await self.post()
        await self.assert_completed(op)
        self.provider.assert_awaited_once()

    async def test_provider_enrichment_then_resume_and_concurrent_retry_exactly_once(self):
        _, original = await self.pending()
        await self.db.unified_orders.update_one({}, {"$set": {
            "raw_by_source.salla_direct.shipping.address": {"city": "changed"},
            "raw_by_source.salla_direct.shipping.company_name": "other",
            "raw_by_source.salla_direct.shipping.tracking_number": "new",
            "raw_by_source.salla_direct.shipping.label_url": "new"}})
        source = (await self.db.unified_orders.find_one({}))["raw_by_source"]
        import order_review_resume_worker as worker
        await asyncio.gather(worker.run_once(self.db), worker.run_once(self.db), self.post())
        await self.assert_completed(original)
        self.assertEqual((await self.saved())["business_snapshot"], original["business_snapshot"])
        self.assertEqual((await self.db.unified_orders.find_one({}))["raw_by_source"], source)

    async def test_missing_address_allows_reviewed_without_ready_to_ship(self):
        await self.seed()
        await self.db.unified_orders.update_one({}, {"$set": {
            "raw_by_source.salla_direct.shipping.address": {"city": "only city"},
            "raw_by_source.salla_direct.shipping_address": {"city": "only city"}}})
        response = await self.post()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["stage"], "reviewed")
        self.assertFalse(response.json()["fulfillment_decision"]["ready_to_ship"])
        self.assertIn("shipping_address_incomplete", response.json()["fulfillment_decision"]["blockers"])

    async def test_protected_changes_still_reject(self):
        _, original = await self.pending()
        source = await self.db.unified_orders.find_one({})
        for path, value in [("items.0.product_id", "other"), ("items.0.variant_id", "other"),
                ("items.0.sku", "other"), ("items.0.options", [{"value": "other"}]),
                ("items.0.quantity", 8), ("payment_method", "bank"),
                ("amounts.total.amount", 1234), ("shipping.cod_fee", 9),
                ("unknown_business_fact", 1), ("shipping.unknown_business_fact", 1)]:
            with self.subTest(path=path):
                await self.db.unified_orders.replace_one({"_id": source["_id"]}, deepcopy(source))
                await self.db.unified_orders.update_one({}, {"$set": {"raw_by_source.salla_direct." + path: value}})
                response = await self.post()
                self.assertEqual(response.status_code, 409, response.text)
                self.assertEqual(await self.db[completion.WORKFLOWS].count_documents({}), 0)
                self.assertEqual(await self.db[completion.EVENTS].count_documents({}), 0)
                self.assertEqual((await self.saved())["business_snapshot"], original["business_snapshot"])

    async def test_v1_pending_operation_retains_shipping_guard_and_snapshot(self):
        builder = completion.build_snapshot
        def old_snapshot(*args, **kwargs):
            kwargs["schema_version"] = 1
            return builder(*args, **kwargs)
        with patch.object(completion, "REVIEW_SCOPE_VERSION", 1), patch.object(completion, "build_snapshot", old_snapshot):
            _, original = await self.pending()
        await self.db.unified_orders.update_one({}, {"$set": {"raw_by_source.salla_direct.shipping.company": "other"}})
        response = await self.post()
        self.assertEqual(response.status_code, 409, response.text)
        saved = await self.saved()
        self.assertEqual(saved["business_snapshot"], original["business_snapshot"])
        self.assertEqual(saved["approval_contract_version"], 1)
        self.assertEqual(await self.db[completion.EVENTS].count_documents({}), 0)

    async def test_current_incomplete_address_overrides_stale_ready_decision(self):
        await self.seed()
        owner = completion.operational_owner
        import fulfillment_v2_routes as fulfillment
        real_decision = fulfillment.build_order_fulfillment_decision
        async def pretend_instant(*args, **kwargs):
            decision = await real_decision(*args, **kwargs)
            decision["lines"] = [{"resolved_type": "instant", "configured": True}]
            decision.update(ready_to_ship=True, route_stage="ready_to_ship")
            return decision
        async def before_transaction(db, user_id, callback, **kwargs):
            if callback.__name__ == "finalize":
                row = await self.db.unified_orders.find_one({})
                raw = row["raw_by_source"]["salla_direct"]
                raw["shipping"]["address"] = {"city": "only city"}
                raw["shipping_address"] = {"city": "only city"}
                await self.db.unified_orders.update_one({}, {"$set": {"raw_by_source.salla_direct": raw}})
            return await owner(db, user_id, callback, **kwargs)
        with patch.object(completion, "operational_owner", before_transaction), \
             patch.object(fulfillment, "build_order_fulfillment_decision", pretend_instant):
            response = await self.post()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["stage"], "reviewed")
        self.assertIn("shipping_address_incomplete", response.json()["fulfillment_decision"]["blockers"])
        await self.assert_completed(await self.saved())
