"""Production-path contract replay on real Mongo; synthetic orders, not incidents.

Refresh/webhook/mapper/completion/worker/catalog are real. Salla transport is fake.
"""
import asyncio
from copy import deepcopy
import unittest
from unittest.mock import patch

import order_review_completion as completion
import order_review_resume_worker as worker
from order_review_acceptance_snapshot import acceptance_snapshot, fingerprint
import order_review_routes as routes
import test_review_completion_auto_resume as fixture


class RepresentationIntegrationTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.AutoResumeTests.asyncSetUp
    asyncTearDown = fixture.AutoResumeTests.asyncTearDown
    source_payload = fixture.AutoResumeTests.source_payload
    webhook = fixture.AutoResumeTests.webhook
    refresh = fixture.AutoResumeTests.refresh
    saved = fixture.AutoResumeTests.saved
    assert_completed = fixture.AutoResumeTests.assert_completed

    async def seed(self):
        payload = self.source_payload(number="new-review")
        payload["shipping"] = {"company": "Synthetic carrier", "method": "door"}
        self.assertTrue((await self.webhook(payload))["synced"])
        return payload

    async def pending(self):
        payload = await self.seed()
        await self.db.create_collection(completion.EVENTS)
        await self.db.command({"collMod": completion.EVENTS,
            "validator": {"event_type": {"$ne": "order_review_completed"}}})
        response = await self.client.post("/order-reviews-v1/new-review/complete", json={"expected_revision": 0})
        self.assertEqual(response.status_code, 500, response.text)
        op = await self.saved()
        self.assertEqual(op["state"], "provider_confirmed")
        self.assertEqual((await self.client.get("/order-reviews-v1/reviewed")).json()["items"], [])
        await self.db.command({"collMod": completion.EVENTS, "validator": {}})
        return payload, op

    async def test_refresh_during_provider_then_webhook_preserves_approval(self):
        payload = await self.seed()
        before = await self.db.unified_orders.find_one({})
        original = None
        async def provider(*_args):
            nonlocal original
            original = await self.saved()
            self.assertTrue(original["business_snapshot"]["integrity_hash"])
            self.assertTrue((await self.refresh(payload))["ok"])
            after = await self.db.unified_orders.find_one({})
            # Actual refresh adds company_name: the deployed comparator rejects.
            self.assertNotEqual(completion.source_fingerprint(before), completion.source_fingerprint(after))
            return "sent", None
        self.provider.side_effect = provider
        self.client._transport.raise_app_exceptions = True
        response = await self.client.post("/order-reviews-v1/new-review/complete", json={"expected_revision": 0})
        self.assertEqual(response.status_code, 200, response.text)
        await self.assert_completed(original)
        self.assertEqual((await self.saved())["business_snapshot"], original["business_snapshot"])

    async def test_refresh_webhook_auto_resume_concurrent_manual_retry(self):
        payload, original = await self.pending()
        self.assertTrue((await self.refresh(payload))["ok"])
        # Real order.updated processing, including lifecycle reconciliation.
        updated = deepcopy(payload)
        updated["updated_at"] = "2030-01-03T12:00:00+00:00"
        result = await self.webhook(updated, "order.updated")
        self.assertTrue(result["synced"], result)
        self.assertTrue((await self.refresh(updated))["ok"])
        await asyncio.gather(worker.run_once(self.db), worker.run_once(self.db),
            self.client.post("/order-reviews-v1/new-review/complete", json={"expected_revision": 0}))
        await self.assert_completed(original)
        self.assertEqual(await worker.run_once(self.db), 0)
        self.assertEqual((await self.saved())["business_snapshot"], original["business_snapshot"])

    async def test_v1_business_unknown_and_conflicting_alias_rejected_after_confirmation(self):
        # Existing durable v1 approvals must not silently inherit the v2 scope.
        with patch.object(completion, "REVIEW_SCOPE_VERSION", 1):
            _, original = await self.pending()
        source = await self.db.unified_orders.find_one({})
        changes = [
            ("items.0.product_id", "other"), ("items.0.quantity", 9),
            ("items.0.sku", "other"), ("items.0.options", [{"value": "other"}]),
            ("items.0.customer_selections", ["other"]),
            ("shipping.company", "other"), ("shipping_address.city", "other"),
            ("payment_method", "bank"), ("customer", {"mobile": "changed"}),
            ("unknown_root", 1), ("shipping.unknown_nested", 1),
            ("shipping.company_name", "conflicting"),
        ]
        for path, value in changes:
            with self.subTest(path=path):
                await self.db.unified_orders.replace_one({"_id": source["_id"]}, deepcopy(source))
                await self.db.unified_orders.update_one({}, {"$set": {"raw_by_source.salla_direct." + path: value}})
                response = await self.client.post("/order-reviews-v1/new-review/complete", json={"expected_revision": 0})
                self.assertEqual(response.status_code, 409, response.text)
                self.assertEqual(await self.db[completion.WORKFLOWS].count_documents({}), 0)
                self.assertEqual(await self.db[completion.EVENTS].count_documents({}), 0)
                self.assertEqual((await self.saved())["business_snapshot"], original["business_snapshot"])

    async def test_unfenced_source_write_during_final_transaction_cannot_commit(self):
        await self.pending()
        original_owner = completion.operational_owner
        original_acceptance = completion.acceptance_snapshot
        at_finalize = injected = False
        async def owner(db, user_id, callback, **kwargs):
            nonlocal at_finalize
            at_finalize = callback.__name__ == "finalize"
            return await original_owner(db, user_id, callback, **kwargs)
        async def acceptance(scoped, **kwargs):
            nonlocal injected
            if at_finalize and not injected:
                injected = True
                await self.db.unified_orders.update_one({}, {"$set": {
                    "raw_by_source.salla_direct.items.0.quantity": 7}})
            return await original_acceptance(scoped, **kwargs)
        with patch.object(completion, "operational_owner", owner), patch.object(completion, "acceptance_snapshot", acceptance):
            response = await self.client.post("/order-reviews-v1/new-review/complete", json={"expected_revision": 0})
        self.assertTrue(injected)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(await self.db[completion.WORKFLOWS].count_documents({}), 0)
        self.assertEqual(await self.db[completion.EVENTS].count_documents({}), 0)

    async def test_legacy_operation_is_not_upgraded_or_recovered(self):
        payload, original = await self.pending()
        await self.db[completion.OPERATIONS].update_one({}, {"$unset": {"business_snapshot": "", "approval_contract_version": ""}})
        await self.refresh(payload)
        await worker.run_once(self.db)
        saved = await self.saved()
        self.assertEqual(saved["_id"], original["_id"])
        self.assertEqual(saved["state"], "requires_review")
        self.assertNotIn("business_snapshot", saved)
        self.assertEqual(await self.db[completion.WORKFLOWS].count_documents({}), 0)

    async def test_missing_new_snapshot_cannot_downgrade_to_legacy(self):
        await self.pending()
        await self.db[completion.OPERATIONS].update_one({}, {"$unset": {"business_snapshot": ""}})
        await self.db.unified_orders.update_one({}, {"$set": {"raw_by_source.salla_direct.unknown_root": 1}})
        await worker.run_once(self.db)
        saved = await self.saved()
        self.assertEqual(saved["state"], "requires_review")
        self.assertEqual(saved["resume_block_reason"], "review_completion_approval_evidence_missing")
        self.assertEqual(await self.db[completion.WORKFLOWS].count_documents({}), 0)

    async def test_enrichment_does_not_block_explicit_config_reapproval(self):
        payload, original = await self.pending()
        await self.refresh(payload)
        await self.db.settings.update_one({"user_id": "owner"}, {"$set": {
            "g47_inventory.component_lifecycle_starts_at": "2026-09-02T00:00:00+00:00"}})
        rejected = await self.client.post("/order-reviews-v1/new-review/complete", json={"expected_revision": 0})
        self.assertEqual(rejected.status_code, 409, rejected.text)
        self.assertEqual(rejected.json()["detail"]["code"], "component_acceptance_changed")
        order = await routes.get_order(routes.MongoOrderRepository(self.db), user_id="owner", order_number="new-review")
        config = await acceptance_snapshot(self.db, user_id="owner", order=order)
        response = await self.client.post("/order-reviews-v1/new-review/complete", json={
            "expected_revision": 0, "reapprove_operation_id": original["_id"],
            "expected_acceptance_fingerprint": fingerprint(config)})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotEqual(response.json()["operation_id"], original["_id"])
        previous = await self.db[completion.OPERATIONS].find_one({"_id": original["_id"]})
        self.assertEqual(previous["business_snapshot"], original["business_snapshot"])
        self.assertEqual(previous["state"], "provider_confirmed")
        self.assertEqual(await self.db[completion.OPERATIONS].count_documents({}), 2)
        self.assertEqual(await self.db[completion.WORKFLOWS].count_documents({"stage": "reviewed"}), 1)
        self.assertEqual(await self.db[completion.EVENTS].count_documents({"event_type": "order_review_completed"}), 1)

    async def test_component_generation_mutation_still_blocks_resume(self):
        await self.pending()
        await self.db.mezan_component_order_lifecycle_v1.update_one({}, {"$inc": {"generation": 1}})
        await worker.run_once(self.db)
        saved = await self.saved()
        self.assertEqual(saved["state"], "requires_review")
        self.assertEqual(saved["resume_block_reason"], "component_acceptance_changed")
        self.assertEqual(await self.db[completion.WORKFLOWS].count_documents({}), 0)
