"""Source-event races using displayed GET approvals; isolated Mongo, no Salla IO."""
import asyncio
from copy import deepcopy
import unittest
from unittest.mock import patch

import order_review_completion as completion
import test_review_local_completion as local


class SourceEventRaceTests(unittest.IsolatedAsyncioTestCase):
    # Reuse setup/helpers without inheriting or rerunning the unrelated suite.
    asyncSetUp = local.LocalCompletionTests.asyncSetUp
    asyncTearDown = local.LocalCompletionTests.asyncTearDown
    source_payload = local.LocalCompletionTests.source_payload
    webhook = local.LocalCompletionTests.webhook
    post = local.LocalCompletionTests.post
    saved = local.LocalCompletionTests.saved
    assert_completed = local.LocalCompletionTests.assert_completed
    assert_no_completion = local.LocalCompletionTests.assert_no_completion

    async def business_state(self):
        names = (completion.OPERATIONS, completion.WORKFLOWS, completion.EVENTS,
                 local.fixture.PLANS, local.fixture.UNITS, local.fixture.LOCATIONS,
                 "mezan_fulfillment_decisions_v2")
        return {name: await self.db[name].find({}).sort("_id", 1).to_list(1000)
                for name in names}

    async def at_claim(self, mutate):
        """Inject after all route reads, before the real owner transaction."""
        real_owner = completion.operational_owner
        injected = False
        before = None

        async def before_owner(db, owner, callback, **kwargs):
            nonlocal injected, before
            if callback.__name__ == "finish_local" and not injected:
                injected = True
                await mutate()
                before = await self.business_state()
            return await real_owner(db, owner, callback, **kwargs)

        with patch.object(completion, "operational_owner", before_owner):
            response = await self.post()
        self.assertTrue(injected)
        return response, before

    async def test_revision_only_change_preserves_approved_facts(self):
        async def metadata_only():
            await self.db.unified_orders.update_one({}, {
                "$inc": {"g47_salla_snapshot.revision": 1}})

        response, _ = await self.at_claim(metadata_only)
        await self.assert_completed(response)

    async def test_same_facts_real_webhook_between_route_and_claim(self):
        original = await self.db.unified_orders.find_one({})

        async def webhook_arrives():
            payload = deepcopy(self.payload)
            payload["updated_at"] = local.fixture.LATER
            result = await self.webhook(payload, "order.updated")
            self.assertTrue(result["synced"], result)
            current = await self.db.unified_orders.find_one({})
            self.assertNotEqual(current["g47_salla_snapshot"]["revision"],
                                original["g47_salla_snapshot"]["revision"])

        response, _ = await self.at_claim(webhook_arrives)
        await self.assert_completed(response)

    async def test_material_changes_with_revision_bump_abort_all_completion_effects(self):
        original = await self.db.unified_orders.find_one({})
        changes = (
            ("items.0.product_id", "similar-product-different-id"),
            ("items.0.sku", "SIMILAR-SKU"),
            ("items.0.variant_id", "different-variant"),
            ("items.0.options", [{"id": "size", "name": "Size", "value": "60 inch"}]),
            ("items.0.customer_selections", [{"option_id": "size", "value_id": "60"}]),
            ("items.0.quantity", 3),
        )
        for path, value in changes:
            with self.subTest(path=path):
                await self.db.unified_orders.replace_one({"_id": original["_id"]}, deepcopy(original))

                async def mutate():
                    await self.db.unified_orders.update_one({}, {
                        "$inc": {"g47_salla_snapshot.revision": 1},
                        "$set": {"raw_by_source.salla_direct." + path: value}})

                response, before = await self.at_claim(mutate)
                self.assertEqual(response.status_code, 409, response.text)
                await self.assert_no_completion()
                self.assertEqual(await self.business_state(), before)

    async def test_material_real_webhook_aborts_without_completion_writes(self):
        async def webhook_arrives():
            payload = deepcopy(self.payload)
            payload["updated_at"] = local.fixture.LATER
            payload["items"][0]["quantity"] = 3
            self.assertTrue((await self.webhook(payload, "order.updated"))["synced"])

        response, before = await self.at_claim(webhook_arrives)
        self.assertEqual(response.status_code, 409, response.text)
        await self.assert_no_completion()
        self.assertEqual(await self.business_state(), before)

    async def test_concurrent_approvals_keep_one_operation_workflow_event(self):
        responses = await asyncio.gather(self.post(), self.post())
        self.assertTrue(all(r.status_code in (200, 409) for r in responses),
                        [r.text for r in responses])
        winners = [r for r in responses if r.status_code == 200]
        self.assertTrue(winners, [r.text for r in responses])
        operation = await self.assert_completed(winners[0])
        before = await self.business_state()
        again = await self.assert_completed(await self.post())
        self.assertEqual(operation, again)
        self.assertEqual(before, await self.business_state())

    async def test_existing_in_progress_approval_is_not_replaced(self):
        operation = await self.assert_completed(await self.post())
        await self.db[completion.WORKFLOWS].delete_many({})
        operation.update(state="prepared", lease_until="2099-01-01T00:00:00+00:00")
        await self.db[completion.OPERATIONS].replace_one({"_id": operation["_id"]}, operation)
        await self.db.unified_orders.update_one({}, {
            "$inc": {"g47_salla_snapshot.revision": 1}})
        before = await self.business_state()
        response = await self.post()
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["detail"]["code"], "review_completion_in_progress")
        self.assertEqual(operation, await self.saved())
        self.assertEqual(before, await self.business_state())
        self.external.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
