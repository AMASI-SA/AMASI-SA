"""Exercise the production image wrapper with real Mongo and synthetic orders."""
import importlib
import unittest
from unittest.mock import AsyncMock, patch

import order_review_routes as routes
import test_g47_component_lifecycle_integration as fixture


class ImageWrapperTests(unittest.IsolatedAsyncioTestCase):
    source_payload = fixture.ComponentRouteTests.source_payload
    webhook = fixture.ComponentRouteTests.webhook
    asyncTearDown = fixture.ComponentRouteTests.asyncTearDown

    async def asyncSetUp(self):
        await fixture.ComponentRouteTests.asyncSetUp(self)
        # Import the same composition server.py installs, without leaking its
        # module-level replacements into other route suites.
        originals = {name: getattr(routes, name) for name in
                     ("_review_item_identities", "_item_view", "_preference_map")}
        self.images = importlib.import_module("order_review_image_modes")
        for name, original in originals.items():
            setattr(routes, name, original)
            replacement = patch.object(routes, name, getattr(self.images, name))
            replacement.start()
            self.patches.append(replacement)
        self.assertTrue((await self.webhook(self.source_payload(number="wrapper-test")))["synced"])
        await self.db[self.images.MEZAN_IMAGES].insert_many([
            {"user_id": "owner", "product_key": "product:p", "id": "own", "created_at": "1"},
            {"user_id": "other", "product_key": "product:p", "id": "foreign", "created_at": "1"},
        ])

    async def snapshot(self):
        return {name: await self.db[name].find({}).sort("_id", 1).to_list(None)
                for name in await self.db.list_collection_names()}

    async def detail(self, value):
        before = await self.snapshot()
        async def provider(db, user_id, method, *args, **kwargs):
            self.assertEqual(method, "GET", "Salla writes forbidden")
            return {"data": {}}
        # Existing non-local refresh is transport-isolated; its policy is not
        # changed by this signature fix.
        refresh = AsyncMock()
        original = AsyncMock(wraps=self.images._original_review_item_identities)
        with patch.object(routes, "refresh_order_from_salla", refresh), \
             patch.object(routes, "call_salla", AsyncMock(side_effect=provider)) as transport, \
             patch.object(self.images, "_original_review_item_identities", original):
            suffix = "" if value is None else "?local_only=" + str(value).lower()
            response = await self.client.get("/order-reviews-v1/wrapper-test" + suffix)
        self.assertEqual(response.status_code, 200, response.text)
        # Non-local detail refreshes the gallery before entering its read
        # snapshot; the snapshot itself must always use local-only identities.
        # The previous last-call assertion already failed for default/false at
        # immutable 8e981747b9c65567e893ea9f25da78aa49e1b85b (2 failed, 1 passed).
        calls = original.await_args_list
        self.assertTrue(calls)
        if value:
            self.assertTrue(all(call.kwargs == {"local_only": True} for call in calls))
        else:
            self.assertEqual(calls[0].kwargs, {"local_only": False})
            self.assertGreaterEqual(len(calls), 2, "A separate snapshot-local identity read is required")
            self.assertTrue(all(call.kwargs == {"local_only": True} for call in calls[1:]))
        if value:
            refresh.assert_not_awaited()
            transport.assert_not_awaited()
        else:
            refresh.assert_awaited_once()
            self.assertGreater(transport.await_count, 0)
        items = response.json()["items"]
        self.assertTrue(items)
        self.assertIn(self.images._image_url("own"), items[0]["gallery"])
        self.assertNotIn(self.images._image_url("foreign"), items[0]["gallery"])
        self.assertEqual(before, await self.snapshot())

    async def test_local_only_true(self):
        await self.detail(True)

    async def test_local_only_false(self):
        await self.detail(False)

    async def test_ordinary_detail_default(self):
        await self.detail(None)
