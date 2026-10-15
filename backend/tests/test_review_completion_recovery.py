"""Real replica-set review recovery; synthetic provider, no production fallback."""
import asyncio
from copy import deepcopy
import unittest
from unittest.mock import AsyncMock, patch

import fulfillment_v2_routes as fulfillment
import order_review_completion as completion
import order_review_routes as review
from order_item_engine.mapper import map_order_item_identities
import test_g47_component_lifecycle_integration as fixture
import httpx
from pymongo.errors import OperationFailure
from tests.review_legacy_contract_fixture import enable_legacy_review_contract


class ReviewCompletionRecoveryTests(unittest.IsolatedAsyncioTestCase):
    # Reuse fixture utilities without inheriting its unrelated test methods.
    asyncTearDown = fixture.ComponentRouteTests.asyncTearDown
    order = fixture.ComponentRouteTests.order
    accept = fixture.ComponentRouteTests.accept
    on_hand = fixture.ComponentRouteTests.on_hand
    source_payload = fixture.ComponentRouteTests.source_payload
    webhook = fixture.ComponentRouteTests.webhook
    refresh = fixture.ComponentRouteTests.refresh

    async def asyncSetUp(self):
        await fixture.ComponentRouteTests.asyncSetUp(self)
        enable_legacy_review_contract(self)

    async def assert_once(self, number="order-1"):
        query = {"user_id": "owner", "order_number": number}
        self.assertEqual(await self.db[completion.WORKFLOWS].count_documents(query), 1)
        self.assertEqual(await self.db[completion.EVENTS].count_documents(
            {**query, "event_type": "order_review_completed"}), 1)
        self.assertEqual(await self.db[completion.OPERATIONS].count_documents(query), 1)
        operation = await self.db[completion.OPERATIONS].find_one(query)
        self.assertEqual(operation["state"], "completed")
        workflow = await self.db[completion.WORKFLOWS].find_one(query)
        self.assertEqual(workflow["review_completion_operation_id"], operation["_id"])
        self.assertEqual(workflow["revision"], 1)
        return operation

    async def assert_no_completion(self):
        self.assertEqual(await self.db[completion.WORKFLOWS].count_documents({"stage": "reviewed"}), 0)
        self.assertEqual(await self.db[completion.EVENTS].count_documents({"event_type": "order_review_completed"}), 0)

    async def reject_collection_write(self, name, validator):
        if name not in await self.db.list_collection_names():
            await self.db.create_collection(name)
        await self.db.command({"collMod": name, "validator": validator, "validationLevel": "strict"})

    async def test_provider_success_workflow_failure_retry_exactly_once(self):
        await self.reject_collection_write(completion.WORKFLOWS, {"stage": {"$ne": "reviewed"}})
        failed, provider = await self.accept()
        self.assertEqual(failed.status_code, 500, failed.text)
        provider.assert_awaited_once()
        await self.assert_no_completion()
        operation = await self.db[completion.OPERATIONS].find_one({})
        self.assertEqual(operation["state"], "provider_confirmed")
        self.assertTrue(operation["items"])
        await self.db.command({"collMod": completion.WORKFLOWS, "validator": {}})
        retry, _ = await self.accept()
        self.assertEqual(retry.status_code, 200, retry.text)
        saved = await self.assert_once()
        self.assertEqual(saved["_id"], operation["_id"])
        self.assertEqual(saved["items"], operation["items"])
        again, repeated_provider = await self.accept()
        self.assertEqual(again.status_code, 200, again.text)
        repeated_provider.assert_not_awaited()
        await self.assert_once()

    async def test_real_provider_timeout_then_local_failure_retry_posts_once(self):
        payload = self.source_payload(number="order-1")
        await self.webhook(payload)
        await self.reject_collection_write(completion.WORKFLOWS, {"stage": {"$ne": "reviewed"}})
        provider = deepcopy(payload)
        posts = 0

        async def transport(db, owner, method, path, **kwargs):
            nonlocal posts
            self.assertEqual(owner, "owner")
            if method == "POST":
                self.assertEqual(path, "/orders/order-1/status")
                operation = await self.db[completion.OPERATIONS].find_one({})
                self.assertEqual(operation["state"], "syncing")
                self.assertTrue(operation["items"])
                posts += 1
                provider["status"]["customized"] = {"name": "تم المراجعة"}
                raise httpx.ReadTimeout("provider committed; response lost")
            self.assertEqual(method, "GET")
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
            self.client._transport.raise_app_exceptions = True
            with self.assertRaises(OperationFailure):
                await self.client.post("/order-reviews-v1/order-1/complete", json={"expected_revision": 0})
            await self.assert_no_completion()
            prepared = await self.db[completion.OPERATIONS].find_one({})
            self.assertEqual(prepared["state"], "provider_confirmed")
            await self.db.command({"collMod": completion.WORKFLOWS, "validator": {}})
            for _ in range(2):
                retry = await self.client.post("/order-reviews-v1/order-1/complete", json={"expected_revision": 0})
                self.assertEqual(retry.status_code, 200, retry.text)
        self.assertEqual(posts, 1)
        completed = await self.assert_once()
        self.assertEqual(completed["_id"], prepared["_id"])

    async def test_event_failure_rolls_back_workflow_then_retries(self):
        await self.reject_collection_write(completion.EVENTS, {"event_type": {"$ne": "order_review_completed"}})
        failed, provider = await self.accept()
        self.assertEqual(failed.status_code, 500, failed.text)
        provider.assert_awaited_once()
        self.assertEqual(await self.db[completion.WORKFLOWS].count_documents({}), 0)
        await self.assert_no_completion()
        self.assertEqual((await self.db[completion.OPERATIONS].find_one({}))["state"], "provider_confirmed")
        await self.db.command({"collMod": completion.EVENTS, "validator": {}})
        retry, _ = await self.accept()
        self.assertEqual(retry.status_code, 200, retry.text)
        await self.assert_once()

    async def test_concurrent_complete_has_one_provider_owner_and_one_commit(self):
        order = self.order()
        await self.db.unified_orders.insert_one({"user_id": "owner", "order_number": order.order_number,
            "raw_by_source": {"salla_direct": {"date": order.created_at.isoformat()}}})
        entered, release = asyncio.Event(), asyncio.Event()
        async def provider_call(*args):
            durable = await self.db[completion.OPERATIONS].find_one({"order_number": "order-1"})
            self.assertEqual(durable["state"], "syncing")
            self.assertTrue(durable["items"])
            self.assertTrue(durable["lease_token"])
            await self.assert_no_completion()
            entered.set()
            await asyncio.wait_for(release.wait(), timeout=10)
            return "sent", None
        provider = AsyncMock(side_effect=provider_call)
        with patch.object(review, "get_order", AsyncMock(return_value=order)), \
             patch.object(review, "_review_item_identities", AsyncMock(return_value=map_order_item_identities(order))), \
             patch.object(review, "_sync_salla_reviewed", provider):
            first = asyncio.create_task(self.client.post("/order-reviews-v1/order-1/complete", json={"expected_revision": 0}))
            try:
                await asyncio.wait_for(entered.wait(), timeout=10)
                second = await self.client.post("/order-reviews-v1/order-1/complete", json={"expected_revision": 0})
                self.assertEqual(second.status_code, 409, second.text)
                self.assertEqual(second.json()["detail"]["code"], "review_completion_in_progress")
            finally:
                release.set()
            first_response = await first
        self.assertEqual(first_response.status_code, 200, first_response.text)
        provider.assert_awaited_once()
        await self.assert_once()

    async def test_product_change_during_provider_fails_closed(self):
        original = self.order()
        current = [original]
        async def provider_call(*args):
            current[0] = self.order(quantity=3)
            return "sent", None
        async def load(*args, **kwargs):
            return current[0]
        await self.db.unified_orders.insert_one({"user_id": "owner", "order_number": "order-1",
            "raw_by_source": {"salla_direct": {"date": original.created_at.isoformat()}}})
        with patch.object(review, "get_order", load), \
             patch.object(review, "_review_item_identities", AsyncMock(return_value=map_order_item_identities(original))), \
             patch.object(review, "_sync_salla_reviewed", AsyncMock(side_effect=provider_call)):
            failed = await self.client.post("/order-reviews-v1/order-1/complete", json={"expected_revision": 0})
        self.assertEqual(failed.status_code, 409, failed.text)
        self.assertEqual(failed.json()["detail"]["code"], "review_completion_source_changed")
        await self.assert_no_completion()
        retry, provider = await self.accept(current[0])
        self.assertEqual(retry.status_code, 409, retry.text)
        provider.assert_not_awaited()
        await self.assert_no_completion()

    async def test_workflow_revision_changed_during_provider_fails_closed(self):
        async def provider_call(*args):
            await self.db[completion.WORKFLOWS].insert_one({"user_id": "owner", "order_number": "order-1",
                "revision": 1, "stage": "pending_review", "items": [], "operational_items": []})
            return "sent", None
        failed, _ = await self.accept(sync=AsyncMock(side_effect=provider_call))
        self.assertEqual(failed.status_code, 409, failed.text)
        self.assertEqual(failed.json()["detail"]["code"], "review_revision_conflict")
        await self.assert_no_completion()

    async def test_cancellation_during_provider_cannot_complete(self):
        order = self.order()
        async def provider_call(*args):
            await fulfillment.auto_route_instant_order(self.db, user_id="owner",
                order=order.model_copy(update={"status": "canceled"}), source_updated_at=fixture.LATER)
            return "sent", None
        failed, _ = await self.accept(order, sync=AsyncMock(side_effect=provider_call))
        self.assertEqual(failed.status_code, 409, failed.text)
        await self.assert_no_completion()

    async def test_manual_salla_status_webhook_does_not_approve_review(self):
        payload = self.source_payload(number="manual-status")
        payload["status"]["customized"] = {"id": "synthetic-reviewed", "name": "تم المراجعة"}
        await self.webhook(payload)
        await self.assert_no_completion()
        self.assertEqual(await self.db[completion.OPERATIONS].count_documents({}), 0)

    async def test_status_only_webhook_during_provider_revalidates_and_completes(self):
        initial = self.source_payload(number="order-1")
        await self.webhook(initial)
        order = await review.get_order(review.MongoOrderRepository(self.db), user_id="owner", order_number="order-1")
        before = await self.db.unified_orders.find_one({"order_number": "order-1"})
        async def provider_call(*args):
            updated = self.source_payload(number="order-1", version=fixture.LATER)
            updated["status"]["customized"] = {"id": "synthetic-reviewed", "name": "تم المراجعة"}
            await self.webhook(updated, event="order.updated")
            return "sent", None
        response, provider = await self.accept(order, sync=AsyncMock(side_effect=provider_call))
        provider.assert_awaited_once()
        self.assertEqual(response.status_code, 200, response.text)
        after = await self.db.unified_orders.find_one({"order_number": "order-1"})
        self.assertGreater(after["g47_salla_snapshot"]["revision"], before["g47_salla_snapshot"]["revision"])
        await self.assert_once()

    async def test_stale_worker_cannot_finalize_or_clear_successor_lease(self):
        async def provider_call(*args):
            await self.db[completion.OPERATIONS].update_one({"order_number": "order-1"},
                {"$set": {"lease_token": "synthetic-successor", "lease_until": "2999-01-01T00:00:00+00:00"}})
            return "sent", None
        response, _ = await self.accept(sync=AsyncMock(side_effect=provider_call))
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["detail"]["code"], "review_completion_lease_lost")
        await self.assert_no_completion()
        operation = await self.db[completion.OPERATIONS].find_one({})
        self.assertEqual(operation["lease_token"], "synthetic-successor")
        self.assertEqual(operation["lease_until"], "2999-01-01T00:00:00+00:00")

    async def test_canonical_product_change_cannot_hide_behind_stale_presentation(self):
        await self.webhook(self.source_payload(number="order-1"))
        order = await review.get_order(review.MongoOrderRepository(self.db), user_id="owner", order_number="order-1")
        async def provider_call(*args):
            changed = self.source_payload(number="order-1", version=fixture.LATER)
            changed["items"][0]["quantity"] = 3
            await self.webhook(changed, event="order.updated")
            return "sent", None
        # accept deliberately serves its original DTO throughout: canonical DB
        # evidence must independently prevent committing the stale approval.
        response, provider = await self.accept(order, sync=AsyncMock(side_effect=provider_call))
        provider.assert_awaited_once()
        self.assertEqual(response.status_code, 409, response.text)
        await self.assert_no_completion()

    async def test_canonical_cancel_snapshot_cannot_hide_behind_stale_presentation(self):
        order = self.order()
        async def provider_call(*args):
            async def persist(scoped):
                await scoped.unified_orders.update_one({"user_id": "owner", "order_number": "order-1"},
                    {"$set": {"order_status_slug": "canceled"}}, upsert=True)
                return {"created": False}
            await fulfillment.persist_component_source_snapshot(self.db, user_id="owner", order_number="order-1",
                payload=self.source_payload(number="order-1", version=fixture.LATER, status="canceled"), persist=persist)
            return "sent", None
        response, provider = await self.accept(order, sync=AsyncMock(side_effect=provider_call))
        provider.assert_awaited_once()
        self.assertEqual(response.status_code, 409, response.text)
        await self.assert_no_completion()

    async def test_committed_crash_boundaries_resume_same_operation(self):
        original_owner = completion.operational_owner
        for state in ("prepared", "syncing", "provider_confirmed", "completed"):
            with self.subTest(state=state):
                number = "crash-" + state
                triggered = False
                async def stop_after_commit(db, owner, callback, **kwargs):
                    nonlocal triggered
                    result = await original_owner(db, owner, callback, **kwargs)
                    op = await self.db[completion.OPERATIONS].find_one({"order_number": number})
                    if not triggered and op and op["state"] == state:
                        triggered = True
                        raise RuntimeError("Synthetic process-stop boundary after " + state)
                    return result
                with patch.object(completion, "operational_owner", stop_after_commit):
                    failed, _ = await self.accept(self.order(number=number))
                self.assertTrue(triggered)
                self.assertEqual(failed.status_code, 500, failed.text)
                before = await self.db[completion.OPERATIONS].find_one({"order_number": number})
                self.assertEqual(before["state"], state)
                # Simulate restart after a dead worker's persisted lease expires.
                await self.db[completion.OPERATIONS].update_one({"_id": before["_id"]},
                    {"$set": {"lease_until": "2000-01-01T00:00:00+00:00"}})
                retry, _ = await self.accept(self.order(number=number))
                self.assertEqual(retry.status_code, 200, retry.text)
                after = await self.assert_once(number)
                self.assertEqual(before["_id"], after["_id"])


if __name__ == "__main__":
    unittest.main()
