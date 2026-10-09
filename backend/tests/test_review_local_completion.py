"""Local-v1 HTTP contracts on a real loopback Mongo replica set, no provider IO."""
import asyncio
from copy import deepcopy
import unittest
from unittest.mock import AsyncMock, patch

import fulfillment_v2_routes as fulfillment
import order_review_completion as completion
import order_review_routes as routes
import order_review_resume_worker as worker
import reviewed_products_catalog as catalog
import test_g47_component_lifecycle_integration as fixture
from review_local_policy import LOCAL_COMPLETION_MODE


class LocalCompletionTests(unittest.IsolatedAsyncioTestCase):
    asyncTearDown = fixture.ComponentRouteTests.asyncTearDown
    source_payload = fixture.ComponentRouteTests.source_payload
    webhook = fixture.ComponentRouteTests.webhook

    async def asyncSetUp(self):
        await fixture.ComponentRouteTests.asyncSetUp(self)
        async def actor():
            return self.actor
        self.app.include_router(catalog.make_reviewed_products_catalog_router(self.db, actor))
        self.payload = self.source_payload(number="local-review")
        self.assertTrue((await self.webhook(self.payload))["synced"])
        self.external = AsyncMock(side_effect=AssertionError("Review called Salla"))
        for replacement in (
            patch.object(routes, "call_salla", self.external),
            patch.object(routes, "_sync_salla_reviewed", self.external),
            patch.object(routes, "refresh_order_from_salla", self.external),
            patch.object(routes, "schedule_salla_auto_sync", side_effect=AssertionError("Review scheduled Salla")),
            patch("salla_integration.service.call_salla", self.external),
        ):
            replacement.start()
            self.patches.append(replacement)

    async def post(self):
        return await self.client.post("/order-reviews-v1/local-review/complete", json={"expected_revision": 0})

    async def saved(self):
        return await self.db[completion.OPERATIONS].find_one({"order_number": "local-review"})

    async def assert_completed(self, response):
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["state"], "completed")
        self.assertEqual(response.json()["completion_mode"], LOCAL_COMPLETION_MODE)
        self.assertEqual(response.json()["salla_status_sync"], "not_requested")
        op = await self.saved()
        self.assertEqual(op["state"], "completed")
        self.assertEqual(op["completion_mode"], LOCAL_COMPLETION_MODE)
        self.assertEqual(op["approval_contract_version"], 2)
        for key in ("provider_confirmed_at", "provider_dispatch", "provider_delivery_version", "auto_resume_version"):
            self.assertNotIn(key, op)
        self.assertEqual(await self.db[completion.OPERATIONS].count_documents({}), 1)
        self.assertEqual(await self.db[completion.WORKFLOWS].count_documents({"stage": "reviewed"}), 1)
        self.assertEqual(await self.db[completion.EVENTS].count_documents({"event_type": "order_review_completed"}), 1)
        self.external.assert_not_awaited()
        return op

    async def assert_no_completion(self):
        self.assertIsNone(await self.saved())
        self.assertEqual(await self.db[completion.WORKFLOWS].count_documents({"stage": "reviewed"}), 0)
        self.assertEqual(await self.db[completion.EVENTS].count_documents({"event_type": "order_review_completed"}), 0)
        self.external.assert_not_awaited()

    async def test_local_200_visible_products_one_atomic_completion_external_pending(self):
        before = await self.db.unified_orders.find_one({})
        owner_before = await self.db.mz2_atomic_owners.find_one({"_id": "owner"})
        response = await self.post()
        op = await self.assert_completed(response)
        owner_after = await self.db.mz2_atomic_owners.find_one({"_id": "owner"})
        # Nested inventory/component work uses the same transaction/owner revision.
        self.assertEqual(owner_after["revision"] - owner_before["revision"], 1)
        source = await self.db.unified_orders.find_one({})
        self.assertEqual(source["raw_by_source"], before["raw_by_source"])
        self.assertEqual(source.get("order_status"), before.get("order_status"))
        self.assertEqual(source.get("order_status_slug"), before.get("order_status_slug"))
        reviewed = await self.client.get("/order-reviews-v1/reviewed")
        self.assertEqual([r["order_number"] for r in reviewed.json()["items"]], ["local-review"])
        products = await self.client.get("/reviewed-products-v1/catalog")
        self.assertEqual(products.status_code, 200, products.text)
        self.assertTrue(products.json()["products"])
        pending = await self.client.get("/order-reviews-v1")
        self.assertEqual(pending.status_code, 200, pending.text)
        self.assertEqual(pending.json()["total_count"], 0)
        searched = await self.client.get("/order-reviews-v1?search=local-review")
        self.assertEqual(searched.status_code, 200, searched.text)
        self.assertEqual(searched.json()["items"], [])
        self.external.assert_not_awaited()
        status = await self.client.get("/order-reviews-v1/local-review/completion-operation")
        self.assertEqual(status.json()["operation"]["completion_mode"], LOCAL_COMPLETION_MODE)
        self.assertEqual(status.json()["operation"]["_id"], op["_id"])

    async def test_cached_split_detail_and_incomplete_images_never_call_provider(self):
        await self.db.salla_products.update_one({"user_id": "owner", "id": "p"}, {"$set": {"images": []}})
        detail = await self.client.get("/order-reviews-v1/local-review?local_only=true")
        self.assertEqual(detail.status_code, 200, detail.text)
        self.assertTrue(detail.json()["items"])
        await self.assert_completed(await self.post())
        self.external.assert_not_awaited()

    async def test_duplicate_retry_returns_same_completed_identity(self):
        first = await self.assert_completed(await self.post())
        response = await self.post()
        again = await self.assert_completed(response)
        self.assertTrue(response.json()["already_reviewed"])
        self.assertEqual(first, again)

    async def test_concurrent_double_click_one_workflow_event(self):
        responses = await asyncio.gather(self.post(), self.post())
        self.assertTrue(any(r.status_code == 200 for r in responses))
        self.assertTrue(all(r.status_code in (200, 409) for r in responses), [r.text for r in responses])
        await self.assert_completed(next(r for r in responses if r.status_code == 200))

    async def test_business_changes_between_request_and_claim_reject(self):
        original = await self.db.unified_orders.find_one({})
        real_owner = completion.operational_owner
        changes = [
            ("items.0.product_id", "different"), ("items.0.sku", "different"),
            ("items.0.variant_id", "different"), ("items.0.options", [{"value": "different"}]),
            ("items.0.customer_selections", ["different"]), ("items.0.quantity", 9),
            ("payment_method", "bank"), ("amounts.total.amount", 999),
            ("unknown_business_fact", "different"), ("shipping.unknown_business_fact", "different"),
        ]
        for path, value in changes:
            with self.subTest(path=path):
                await self.db.unified_orders.replace_one({"_id": original["_id"]}, deepcopy(original))
                injected = False
                async def before_owner(db, owner, callback, **kwargs):
                    nonlocal injected
                    if callback.__name__ == "finish_local" and not injected:
                        injected = True
                        await self.db.unified_orders.update_one({}, {"$set": {"raw_by_source.salla_direct." + path: value}})
                    return await real_owner(db, owner, callback, **kwargs)
                with patch.object(completion, "operational_owner", before_owner):
                    response = await self.post()
                self.assertTrue(injected)
                self.assertEqual(response.status_code, 409, response.text)
                await self.assert_no_completion()

    async def test_acceptance_revision_source_revision_and_cancellation_reject(self):
        real_owner = completion.operational_owner
        cases = (
            ("order_review_acceptance_config_versions", {"_id": "owner"}, {"$inc": {"version": 1}}),
            ("unified_orders", {}, {"$inc": {"g47_salla_snapshot.revision": 1}}),
            ("unified_orders", {}, {"$set": {"raw_by_source.salla_direct.status.slug": "canceled"}}),
            (completion.WORKFLOWS, {"user_id": "owner", "order_number": "local-review"},
             {"$set": {"revision": 7, "stage": "pending_review"}}),
        )
        for collection, query, change in cases:
            with self.subTest(collection=collection, change=change):
                originals = await self.db[collection].find({}).to_list(100)
                async def before_owner(db, owner, callback, **kwargs):
                    if callback.__name__ == "finish_local":
                        await self.db[collection].update_one(query, change, upsert=True)
                    return await real_owner(db, owner, callback, **kwargs)
                with patch.object(completion, "operational_owner", before_owner):
                    response = await self.post()
                self.assertEqual(response.status_code, 409, response.text)
                await self.assert_no_completion()
                await self.db[collection].delete_many({})
                if originals:
                    await self.db[collection].insert_many(originals)

    async def test_generation_change_after_evaluate_is_fenced(self):
        real_assert = fulfillment.assert_component_acceptance
        async def change_generation(scoped, *, ticket):
            await scoped[fulfillment.COMPONENT_LIFECYCLES].update_one(
                {"user_id": "owner", "order_number": "local-review"}, {"$inc": {"generation": 1}})
            return await real_assert(scoped, ticket=ticket)
        with patch.object(fulfillment, "assert_component_acceptance", change_generation):
            response = await self.post()
        self.assertEqual(response.status_code, 409, response.text)
        await self.assert_no_completion()

    async def test_unknown_contract_fails_closed_without_legacy_dispatch(self):
        await self.db[completion.OPERATIONS].insert_one({
            "_id": "unknown-operation", "user_id": "owner", "order_number": "local-review",
            "completion_mode": "unknown", "state": "prepared", "revision": 0,
        })
        before = await self.db[completion.OPERATIONS].find_one({})
        response = await self.post()
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(await self.db[completion.OPERATIONS].find_one({}), before)
        self.external.assert_not_awaited()

    async def test_legacy_operations_parked_unchanged_across_worker_restart(self):
        for state in ("prepared", "syncing", "provider_confirmed"):
            with self.subTest(state=state):
                await self.db[completion.OPERATIONS].delete_many({})
                doc = {"_id": "legacy", "user_id": "owner", "order_number": "local-review",
                       "revision": 0, "state": state, "auto_resume_version": 1,
                       "approval_contract_version": 1, "business_snapshot": {"old": "immutable"},
                       "provider_dispatch": {"state": "outcome_unknown"}, "resume_due_at": ""}
                await self.db[completion.OPERATIONS].insert_one(deepcopy(doc))
                self.assertEqual(await worker.run_once(self.db), 0)
                task = await worker.start_worker(self.db)
                await asyncio.sleep(0)
                await worker.stop_worker(task)
                response = await self.post()
                self.assertEqual(response.status_code, 409, response.text)
                self.assertEqual(response.json()["detail"]["code"], "review_completion_legacy_operation_requires_resolution")
                self.assertEqual(await self.db[completion.OPERATIONS].find_one({}), doc)
                self.external.assert_not_awaited()

    async def test_transaction_failure_rolls_back_every_completion_effect(self):
        await self.db.create_collection(completion.EVENTS)
        await self.db.command({"collMod": completion.EVENTS,
            "validator": {"event_type": {"$ne": "order_review_completed"}}, "validationLevel": "strict"})
        before = {name: await self.db[name].find({}).to_list(100) for name in (
            "unified_orders", fulfillment.COMPONENT_LIFECYCLES, "order_component_units", "mz2_atomic_owners")}
        response = await self.post()
        self.assertEqual(response.status_code, 500, response.text)
        await self.assert_no_completion()
        for name, docs in before.items():
            self.assertEqual(await self.db[name].find({}).to_list(100), docs, name)
        await self.db.command({"collMod": completion.EVENTS, "validator": {}})
        await self.assert_completed(await self.post())

    async def test_timeout_before_commit_leaves_no_partial_completion_then_restart_safe(self):
        real_decision = fulfillment.build_order_fulfillment_decision
        entered = asyncio.Event()
        async def wait_inside_transaction(*args, **kwargs):
            result = await real_decision(*args, **kwargs)
            entered.set()
            await asyncio.Event().wait()
            return result
        with patch.object(fulfillment, "build_order_fulfillment_decision", wait_inside_transaction):
            task = asyncio.create_task(self.post())
            await asyncio.wait_for(entered.wait(), timeout=10)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        await self.assert_no_completion()
        await self.assert_completed(await self.post())
        self.assertEqual(await worker.run_once(self.db), 0)

    async def test_incomplete_address_still_reviewed_never_ready_to_ship(self):
        await self.db.unified_orders.update_one({}, {"$set": {
            "raw_by_source.salla_direct.shipping_address": {"city": "Synthetic only"}}})
        response = await self.post()
        await self.assert_completed(response)
        self.assertFalse(response.json()["fulfillment_decision"]["ready_to_ship"])
        self.assertEqual(response.json()["stage"], "reviewed")

    async def test_current_address_at_finalize_overrides_stale_readiness(self):
        real_decision = fulfillment.build_order_fulfillment_decision
        async def changed_address(scoped, **kwargs):
            decision = await real_decision(scoped, **kwargs)
            await scoped.unified_orders.update_one({}, {"$set": {
                "raw_by_source.salla_direct.shipping_address": {"city": "Synthetic only"}}})
            return {**decision, "lines": [{"resolved_type": "instant", "configured": True}],
                    "ready_to_ship": True}
        with patch.object(fulfillment, "build_order_fulfillment_decision", changed_address):
            response = await self.post()
        await self.assert_completed(response)
        self.assertFalse(response.json()["fulfillment_decision"]["ready_to_ship"])

    async def test_no_financial_or_shipping_side_effect_collections(self):
        before = {name: await self.db[name].find({}).to_list(100) for name in
                  await self.db.list_collection_names() if any(x in name for x in ("accounting", "qoyod", "shipping", "invoice"))}
        await self.assert_completed(await self.post())
        after = {name: await self.db[name].find({}).to_list(100) for name in
                 await self.db.list_collection_names() if any(x in name for x in ("accounting", "qoyod", "shipping", "invoice"))}
        self.assertEqual(before, after)
