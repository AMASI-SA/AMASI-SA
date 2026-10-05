"""Real-Mongo approval snapshot guards; provider is synthetic, never Production."""
import asyncio
from copy import deepcopy
import unittest
from unittest.mock import AsyncMock, patch

import httpx
import order_review_completion as completion
import order_review_routes as review
from order_review_acceptance_snapshot import acceptance_snapshot, fingerprint
from order_item_engine.mapper import map_order_item_identities
import test_g47_component_lifecycle_integration as fixture
import test_review_completion_recovery as recovery


class AcceptanceSnapshotTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.ComponentRouteTests.asyncSetUp
    asyncTearDown = fixture.ComponentRouteTests.asyncTearDown
    order = fixture.ComponentRouteTests.order
    accept = fixture.ComponentRouteTests.accept
    webhook = fixture.ComponentRouteTests.webhook
    source_payload = fixture.ComponentRouteTests.source_payload
    assert_no_completion = recovery.ReviewCompletionRecoveryTests.assert_no_completion
    reject_collection_write = recovery.ReviewCompletionRecoveryTests.reject_collection_write

    async def change_config(self):
        await self.db.settings.update_one({"user_id": "owner"}, {"$set": {
            "g47_inventory.component_lifecycle_starts_at": "2026-09-02T00:00:00+00:00"}})

    async def assert_changed(self, response):
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["detail"]["code"], "component_acceptance_changed")
        await self.assert_no_completion()

    async def request(self, body, provider):
        order = self.order()
        with patch.object(review, "get_order", AsyncMock(return_value=order)), \
             patch.object(review, "_review_item_identities", AsyncMock(return_value=map_order_item_identities(order))), \
             patch.object(review, "_sync_salla_reviewed", provider):
            return await self.client.post("/order-reviews-v1/order-1/complete", json=body)

    async def rejected_approval(self):
        async def changed(*args):
            await self.change_config()
            return "sent", None
        response, _ = await self.accept(sync=AsyncMock(side_effect=changed))
        await self.assert_changed(response)
        previous = await self.db[completion.OPERATIONS].find_one({})
        self.assertEqual(previous["state"], "provider_confirmed")
        self.assertTrue(previous["provider_confirmed_at"])
        body = {"expected_revision": 0, "reapprove_operation_id": previous["_id"],
                "expected_acceptance_fingerprint": response.json()["detail"]["current_acceptance_fingerprint"]}
        return previous, body

    async def test_unchanged_config_and_unrelated_settings_complete(self):
        async def unchanged(*args):
            await self.db.settings.update_one({"user_id": "owner"}, {"$set": {"unrelated_display_setting": True}})
            return "sent", None
        response, _ = await self.accept(sync=AsyncMock(side_effect=unchanged))
        self.assertEqual(response.status_code, 200, response.text)
        op = await self.db[completion.OPERATIONS].find_one({})
        self.assertEqual(op["acceptance_fingerprint"], fingerprint(op["acceptance_snapshot"]))

    async def test_config_change_retains_provider_success_without_completion(self):
        previous, _ = await self.rejected_approval()
        retry, provider = await self.accept()
        await self.assert_changed(retry)
        provider.assert_not_awaited()
        after = await self.db[completion.OPERATIONS].find_one({"_id": previous["_id"]})
        self.assertEqual(after, previous)

    async def test_config_change_before_retry_rejects_original_operation(self):
        await self.reject_collection_write(completion.WORKFLOWS, {"stage": {"$ne": "reviewed"}})
        response, _ = await self.accept()
        self.assertEqual(response.status_code, 500)
        original = await self.db[completion.OPERATIONS].find_one({})
        await self.db.command({"collMod": completion.WORKFLOWS, "validator": {}})
        await self.change_config()
        retry, provider = await self.accept()
        await self.assert_changed(retry)
        provider.assert_not_awaited()
        saved = await self.db[completion.OPERATIONS].find_one({})
        self.assertEqual(saved["acceptance_snapshot"], original["acceptance_snapshot"])
        self.assertEqual(saved["provider_confirmed_at"], original["provider_confirmed_at"])

    async def test_change_before_final_transaction_rolls_back_completion(self):
        original = completion.operational_owner
        async def inject(db, owner, callback, **kwargs):
            if callback.__name__ == "finalize":
                await self.change_config()
            return await original(db, owner, callback, **kwargs)
        with patch.object(completion, "operational_owner", inject):
            response, _ = await self.accept()
        await self.assert_changed(response)
        self.assertEqual((await self.db[completion.OPERATIONS].find_one({}))["state"], "provider_confirmed")

    async def test_explicit_reapproval_is_new_identity_and_retry_is_idempotent(self):
        previous, body = await self.rejected_approval()
        provider = AsyncMock(return_value=("sent", None))
        response = await self.request(body, provider)
        self.assertEqual(response.status_code, 200, response.text)
        new_id = response.json()["operation_id"]
        self.assertNotEqual(new_id, previous["_id"])
        old = await self.db[completion.OPERATIONS].find_one({"_id": previous["_id"]})
        self.assertEqual(old["acceptance_snapshot"], previous["acceptance_snapshot"])
        self.assertEqual(old["provider_confirmed_at"], previous["provider_confirmed_at"])
        self.assertEqual(old["state"], "provider_confirmed")
        self.assertEqual(old["superseded_by"], new_id)
        new = await self.db[completion.OPERATIONS].find_one({"_id": new_id})
        self.assertEqual(new["supersedes_operation_id"], previous["_id"])
        self.assertNotEqual(new["acceptance_snapshot"], previous["acceptance_snapshot"])
        self.assertEqual((await self.request(body, provider)).status_code, 200)
        provider.assert_awaited_once()
        self.assertEqual(await self.db[completion.OPERATIONS].count_documents({}), 2)
        self.assertEqual(await self.db[completion.WORKFLOWS].count_documents({}), 1)
        self.assertEqual(await self.db[completion.EVENTS].count_documents({"event_type": "order_review_completed"}), 1)

    async def test_reapproval_rejects_stale_config_confirmation(self):
        _, body = await self.rejected_approval()
        await self.db.settings.update_one({"user_id": "owner"}, {"$set": {"g47_inventory.revision": 2}})
        provider = AsyncMock(return_value=("sent", None))
        await self.assert_changed(await self.request(body, provider))
        provider.assert_not_awaited()
        self.assertEqual(await self.db[completion.OPERATIONS].count_documents({}), 1)

    async def test_concurrent_reapproval_creates_only_one_successor(self):
        _, body = await self.rejected_approval()
        entered, release = asyncio.Event(), asyncio.Event()
        async def held(*args):
            entered.set()
            await asyncio.wait_for(release.wait(), 10)
            return "sent", None
        provider = AsyncMock(side_effect=held)
        first = asyncio.create_task(self.request(body, provider))
        try:
            await asyncio.wait_for(entered.wait(), 10)
            second = await self.request(body, provider)
            self.assertEqual(second.status_code, 409, second.text)
        finally:
            release.set()
        self.assertEqual((await first).status_code, 200)
        provider.assert_awaited_once()
        self.assertEqual(await self.db[completion.OPERATIONS].count_documents({}), 2)
        self.assertEqual(await self.db[completion.EVENTS].count_documents({"event_type": "order_review_completed"}), 1)

    async def test_all_semantic_config_sources_are_frozen(self):
        order = self.order()
        before = await acceptance_snapshot(self.db, user_id="owner", order=order)
        mutations = [
            ("mezan_product_operation_profiles_v2", {"salla_product_id": "p", "fulfillment_type": "instant"}),
            ("mezan_product_resource_bindings_v2", {"salla_product_id": "p", "resource_id": "material", "quantity": 3}),
            ("mezan_product_option_cost_bindings_v2", {"salla_product_id": "p", "mode": "resource", "resource_id": "service", "option_id": "size", "value_id": "large", "quantity": 1}),
            ("order_review_preparation_assignment_defaults", {"product_key": "product:p", "preparation_route": "supplier_file"}),
        ]
        for collection, row in mutations:
            with self.subTest(collection=collection):
                result = await self.db[collection].insert_one({"user_id": "owner", **row})
                self.assertNotEqual(before, await acceptance_snapshot(self.db, user_id="owner", order=order))
                await self.db[collection].delete_one({"_id": result.inserted_id})
        await self.db.mezan_cost_resources_v2.update_one({"id": "material"}, {"$set": {"track_inventory": False}})
        self.assertNotEqual(before, await acceptance_snapshot(self.db, user_id="owner", order=order))

    async def test_lost_provider_response_and_changed_config_preserve_verified_success(self):
        payload = self.source_payload(number="order-1")
        await self.webhook(payload)
        provider = deepcopy(payload)
        posts = 0
        async def transport(db, owner, method, path, **kwargs):
            nonlocal posts
            if method == "POST":
                posts += 1
                provider["status"]["customized"] = {"name": "تم المراجعة"}
                await self.change_config()
                raise httpx.ReadTimeout("synthetic success then response loss")
            if path == "/products/p":
                return {"data": {"id": "p", "name": "Synthetic product", "sku": "SYN-P"}}
            if path == "/products":
                return {"data": []}
            if path == "/orders/items":
                return {"data": deepcopy(provider["items"])}
            if path == "/orders/statuses":
                return {"data": [{"id": 12, "name": "تم المراجعة"}]}
            self.assertEqual(path, "/orders/order-1")
            return {"data": deepcopy(provider)}
        with patch.object(review, "call_salla", transport):
            response = await self.client.post("/order-reviews-v1/order-1/complete", json={"expected_revision": 0})
        await self.assert_changed(response)
        op = await self.db[completion.OPERATIONS].find_one({})
        self.assertEqual(op["state"], "provider_confirmed")
        self.assertTrue(op["provider_confirmed_at"])
        self.assertEqual(posts, 1)


if __name__ == "__main__":
    unittest.main()
