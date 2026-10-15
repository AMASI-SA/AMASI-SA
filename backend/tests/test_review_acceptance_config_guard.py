"""Real replica-set config writer/completion serialization; synthetic fixtures."""
import asyncio
import unittest

from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
import order_review_routes as review
from unittest.mock import AsyncMock, patch

import order_review_completion as completion
from review_acceptance_config_guard import AcceptanceConfigDatabase, FENCES
import test_review_completion_acceptance as acceptance_cases


class ConfigWriterFenceTests(unittest.IsolatedAsyncioTestCase):
    asyncTearDown = acceptance_cases.AcceptanceSnapshotTests.asyncTearDown
    order = acceptance_cases.AcceptanceSnapshotTests.order
    accept = acceptance_cases.AcceptanceSnapshotTests.accept
    change_config = acceptance_cases.AcceptanceSnapshotTests.change_config
    assert_changed = acceptance_cases.AcceptanceSnapshotTests.assert_changed
    assert_no_completion = acceptance_cases.AcceptanceSnapshotTests.assert_no_completion
    # Run the approval contract with the same DB adapter installed by server.
    async def asyncSetUp(self):
        await acceptance_cases.AcceptanceSnapshotTests.asyncSetUp(self)
        self.raw = self.db
        self.db = AcceptanceConfigDatabase(self.raw)
        await self.client.aclose()
        self.app = FastAPI()
        async def actor():
            return self.actor
        self.app.include_router(review.make_order_review_router(self.db, actor))
        self.client = AsyncClient(transport=ASGITransport(app=self.app, raise_app_exceptions=False), base_url="http://test")

    async def version(self, owner="owner"):
        return (await self.raw[FENCES].find_one({"_id": owner}) or {}).get("version", 0)

    async def test_config_write_after_final_snapshot_forces_conflict_and_409(self):
        original = completion.operational_owner
        injected = False
        async def owner(db, user, callback, **kw):
            nonlocal injected
            if callback.__name__ != "finalize":
                return await original(db, user, callback, **kw)
            async def raced(scoped):
                nonlocal injected
                await scoped.settings.find_one({"user_id": "owner"})
                if not injected:
                    injected = True
                    await self.change_config()
                return await callback(scoped)
            return await original(db, user, raced, **kw)
        with patch.object(completion, "operational_owner", owner):
            response, _ = await self.accept()
        self.assertTrue(injected)
        await self.assert_changed(response)
        self.assertEqual((await self.raw[completion.OPERATIONS].find_one({}))["state"], "provider_confirmed")

    async def test_writer_after_completion_fence_serializes_after_commit(self):
        original = completion.operational_owner
        entered, attempted, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        async def owner(db, user, callback, **kw):
            if callback.__name__ != "finalize":
                return await original(db, user, callback, **kw)
            async def held(scoped):
                await scoped[FENCES].update_one({"_id": user}, {"$inc": {"fence": 1}}, upsert=True)
                entered.set()
                await asyncio.wait_for(release.wait(), 5)
                return await callback(scoped)
            return await original(db, user, held, **kw)
        async def writer():
            await entered.wait()
            attempted.set()
            await self.change_config()
        with patch.object(completion, "operational_owner", owner):
            reviewing = asyncio.create_task(self.accept())
            writing = asyncio.create_task(writer())
            try:
                await asyncio.wait_for(attempted.wait(), 5)
                await asyncio.sleep(0.05)
                self.assertFalse(writing.done())
                settings = await self.raw.settings.find_one({"user_id": "owner"})
                self.assertEqual(settings["g47_inventory"]["component_lifecycle_starts_at"], "2026-09-01T00:00:00+00:00")
            finally:
                release.set()
            response, _ = await reviewing
            await asyncio.wait_for(writing, 10)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(await self.raw[completion.EVENTS].count_documents({"event_type": "order_review_completed"}), 1)

    async def test_aba_config_changes_cannot_revalidate_old_approval(self):
        async def aba(*args):
            await self.change_config()
            await self.db.settings.update_one({"user_id": "owner"}, {"$set": {
                "g47_inventory.component_lifecycle_starts_at": "2026-09-01T00:00:00+00:00"}})
            return "sent", None
        response, _ = await self.accept(sync=AsyncMock(side_effect=aba))
        await self.assert_changed(response)
        self.assertEqual(await self.version(), 2)

    async def test_noop_sync_and_unrelated_settings_do_not_advance_version(self):
        await self.db.settings.update_one({"user_id": "owner"}, {"$set": {"unrelated": True}})
        await self.db.settings.update_one({"user_id": "owner"}, {"$set": {
            "g47_inventory.component_lifecycle_starts_at": "2026-09-01T00:00:00+00:00"}})
        self.assertEqual(await self.version(), 0)
        response, _ = await self.accept()
        self.assertEqual(response.status_code, 200, response.text)

    async def test_existing_transaction_rolls_back_config_and_version_together(self):
        before = await self.version()
        async with await self.mongo.start_session() as session:
            with self.assertRaisesRegex(RuntimeError, "synthetic rollback"):
                async with session.start_transaction():
                    await self.db.mezan_product_resource_bindings_v2.insert_one({
                        "user_id": "owner", "id": "new-binding", "salla_product_id": "p",
                        "resource_id": "material", "quantity": 10}, session=session)
                    raise RuntimeError("synthetic rollback")
        self.assertEqual(await self.version(), before)
        self.assertEqual(await self.raw.mezan_product_resource_bindings_v2.count_documents({"id": "new-binding"}), 0)

    async def test_phantom_binding_insert_during_provider_rejects_completion(self):
        async def inserted(*args):
            await self.db.mezan_product_resource_bindings_v2.insert_one({
                "user_id": "owner", "id": "phantom", "salla_product_id": "p",
                "resource_id": "material", "quantity": 1})
            return "sent", None
        response, _ = await self.accept(sync=AsyncMock(side_effect=inserted))
        await self.assert_changed(response)

    async def test_other_owner_and_resource_cost_only_writes_are_not_approval_changes(self):
        async def unrelated(*args):
            await self.db.settings.update_one({"user_id": "another-owner"}, {"$set": {
                "g47_inventory.component_lifecycle_starts_at": "2026-10-01T00:00:00+00:00"}}, upsert=True)
            await self.db.mezan_cost_resources_v2.update_one({"user_id": "owner", "id": "material"}, {"$set": {"unit_cost": 5}})
            return "sent", None
        response, _ = await self.accept(sync=AsyncMock(side_effect=unrelated))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(await self.version(), 0)
        self.assertEqual(await self.version("another-owner"), 1)

    async def test_rename_into_acceptance_field_increments_version(self):
        await self.raw.settings.update_one({"user_id": "owner"}, {"$set": {"new_revision": 2}})
        await self.db.settings.update_one({"user_id": "owner"}, {"$rename": {"new_revision": "g47_inventory.revision"}})
        self.assertEqual(await self.version(), 1)

    async def test_upsert_selector_cannot_hide_semantic_insert(self):
        await self.db.mezan_products_v2.update_one(
            {"user_id": "owner", "salla_product_id": "new-product"}, {"$set": {"name": "Synthetic"}}, upsert=True)
        self.assertEqual(await self.version(), 1)

    async def test_keyword_crud_and_collection_options_remain_guarded(self):
        collection = self.db.mezan_product_operation_profiles_v2.with_options()
        inserted = await collection.insert_one(document={"user_id": "owner", "id": "new-profile", "fulfillment_type": "instant"})
        await collection.update_one(filter={"user_id": "owner", "_id": inserted.inserted_id}, update={"$set": {"fulfillment_type": "preparation"}})
        await collection.delete_one(filter={"user_id": "owner", "_id": inserted.inserted_id})
        self.assertEqual(await self.version(), 3)

    async def test_unscoped_and_cross_owner_mutations_fail_closed(self):
        for query, update in [({}, {"$set": {"g47_inventory.revision": 1}}),
                              ({"user_id": "owner"}, {"$set": {"user_id": "another-owner"}})]:
            with self.subTest(query=query):
                with self.assertRaises(HTTPException) as error:
                    await self.db.settings.update_one(query, update)
                self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(await self.version(), 0)

    async def test_semantic_deletion_during_provider_is_rejected(self):
        async def deleted(*args):
            await self.db.mezan_product_resource_bindings_v2.delete_one({"user_id": "owner", "id": "recipe-material"})
            return "sent", None
        response, _ = await self.accept(sync=AsyncMock(side_effect=deleted))
        await self.assert_changed(response)

    async def test_find_one_upsert_with_changed_selector_advances_version(self):
        collection = self.db.mezan_products_v2
        result = await collection.find_one_and_update(
            {"user_id": "owner", "sku": "old"},
            {"$set": {"sku": "new", "salla_product_id": "new-product"}}, upsert=True)
        self.assertIsNone(result)  # Preserve default BEFORE return semantics.
        self.assertEqual(await self.version(), 1)
        result = await collection.find_one_and_replace(
            {"user_id": "owner", "sku": "old-again"},
            {"user_id": "owner", "sku": "new-again", "salla_product_id": "another-product"}, upsert=True)
        self.assertIsNone(result)
        self.assertEqual(await self.version(), 2)

    async def test_positional_upsert_cannot_bypass_fence(self):
        await self.db.mezan_products_v2.update_one(
            {"user_id": "owner", "salla_product_id": "positional"}, {"$set": {"name": "Synthetic"}}, True)
        self.assertEqual(await self.version(), 1)
