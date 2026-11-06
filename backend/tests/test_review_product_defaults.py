"""Saved product/option settings through the real HTTP and Mongo boundaries."""
import base64
import asyncio
import os
import unittest
from unittest.mock import AsyncMock, patch

import order_review_routes as review
import order_review_export_controls as controls
import test_g47_component_lifecycle_integration as fixture


class ProductDefaultsTests(unittest.IsolatedAsyncioTestCase):
    asyncTearDown = fixture.ComponentRouteTests.asyncTearDown
    source_payload = fixture.ComponentRouteTests.source_payload
    webhook = fixture.ComponentRouteTests.webhook

    async def asyncSetUp(self):
        signing = patch.dict(os.environ, {"JWT_SECRET": "synthetic-defaults-signing-only"})
        signing.start()
        self.addCleanup(signing.stop)
        await fixture.ComponentRouteTests.asyncSetUp(self)
        async def actor():
            return self.actor
        self.app.include_router(controls.make_order_review_export_controls_router(self.db, actor))
        external = patch.object(review, "call_salla", AsyncMock(side_effect=AssertionError("unexpected provider IO")))
        external.start()
        self.addCleanup(external.stop)

    async def seed(self, number, *, personal="FIRST", gift="yes", product="p"):
        payload = self.source_payload(number=number)
        payload["items"][0].update(product_id=product, options=[
            {"name": "gift box", "value": gift}, {"name": "الاسم", "value": personal},
        ])
        self.assertTrue((await self.webhook(payload))["synced"])
        response = await self.client.get(f"/order-reviews-v1/{number}?local_only=true")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    async def test_hidden_field_is_saved_for_product_and_unhide_revokes_future_default(self):
        first = await self.seed("defaults-1")
        line = first["items"][0]["order_item_id"]
        path = f"/order-review-export-controls-v1/defaults-1/items/{line}"
        response = await self.client.patch(path, json={"manual_hidden_spec_keys": ["الاسم"]})
        self.assertEqual(response.status_code, 200, response.text)
        await self.seed("defaults-2", personal="SECOND")
        second = await self.client.get("/order-review-export-controls-v1/defaults-2")
        self.assertEqual(second.json()["items"][0]["manual_hidden_spec_keys"], ["الاسم"])
        response = await self.client.patch(path, json={"manual_hidden_spec_keys": []})
        self.assertEqual(response.status_code, 200, response.text)
        await self.seed("defaults-3")
        third = await self.client.get("/order-review-export-controls-v1/defaults-3")
        self.assertEqual(third.json()["items"][0]["manual_hidden_spec_keys"], [])

    async def test_operational_binding_photo_and_fresh_personal_values_repeat_once_until_unlinked(self):
        first = await self.seed("operational-1")
        response = await self.client.post("/order-reviews-v1/operational-1/operational-items", json={
            "expected_revision": first["revision"], "source_order_item_id": first["items"][0]["order_item_id"],
            "name": "Synthetic box", "linked_spec_keys": ["gift box", "الاسم"],
            "operational_image": {"filename": "test.png", "content_type": "image/png",
                "data_base64": base64.b64encode(b"\x89PNG\r\n\x1a\nsynthetic").decode()},
        })
        self.assertEqual(response.status_code, 200, response.text)
        photo = response.json()["operational_items"][0]["image_url"]
        from order_review_product_defaults import DEFAULTS
        settings = await self.db[DEFAULTS].find_one({"user_id": "owner"})
        self.assertNotIn("FIRST", str(settings))
        second = await self.seed("operational-2", personal="SECOND")
        self.assertEqual(len(second["operational_items"]), 1)
        card = second["operational_items"][0]
        self.assertEqual(card["image_url"], photo)
        self.assertEqual({row["key"]: row["value"] for row in card["linked_specs"]}["الاسم"], "SECOND")
        repeat = await self.client.get("/order-reviews-v1/operational-2?local_only=true")
        self.assertEqual(len(repeat.json()["operational_items"]), 1)
        self.assertEqual(repeat.json()["revision"], second["revision"])
        self.assertEqual(await self.db["order_review_operational_images"].count_documents({}), 1)
        unrelated = await self.seed("operational-other", gift="no")
        self.assertEqual(unrelated["operational_items"], [])
        different = await self.seed("operational-product", product="another-product")
        self.assertEqual(different["operational_items"], [])
        unlink = await self.client.delete(
            f"/order-reviews-v1/operational-2/operational-items/{card['operational_item_id']}",
            params={"expected_revision": second["revision"]},
        )
        self.assertEqual(unlink.status_code, 200, unlink.text)
        future = await self.seed("operational-3", personal="THIRD")
        self.assertEqual(future["operational_items"], [])

    async def test_route_default_visible_in_future_order_and_can_be_revoked(self):
        first = await self.seed("route-1")
        line = first["items"][0]["order_item_id"]
        path = f"/order-review-export-controls-v1/route-1/items/{line}"
        self.assertEqual((await self.client.patch(path, json={"preparation_route": "direct_assembly"})).status_code, 200)
        await self.seed("route-2")
        next_control = (await self.client.get("/order-review-export-controls-v1/route-2")).json()["items"][0]
        self.assertTrue(next_control["direct_assembly"])
        self.assertEqual(next_control["route_source"], "default")
        self.assertEqual((await self.client.patch(path, json={"preparation_route": "supplier_file"})).status_code, 200)
        await self.seed("route-3")
        next_control = (await self.client.get("/order-review-export-controls-v1/route-3")).json()["items"][0]
        self.assertFalse(next_control["direct_assembly"])

    async def test_defaults_do_not_mutate_approved_order_or_copy_into_other_tenant(self):
        from order_review_product_defaults import DEFAULTS
        await self.seed("approved-1")
        await self.db[review.WORKFLOWS].insert_one({"user_id": "owner", "order_number": "approved-1",
            "stage": "reviewed", "revision": 4, "items": [], "operational_items": []})
        await self.db[DEFAULTS].insert_one({"user_id": "owner", "product_key": "product:p",
            "manual_hidden_spec_keys": ["الاسم"]})
        before = await self.db[review.WORKFLOWS].find_one({"order_number": "approved-1"})
        result = await self.client.get("/order-reviews-v1/approved-1?local_only=true")
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(await self.db[review.WORKFLOWS].find_one({"order_number": "approved-1"}), before)
        await self.db[DEFAULTS].delete_many({"user_id": "owner"})
        await self.db[DEFAULTS].insert_one({"user_id": "another-owner", "product_key": "product:p",
            "manual_hidden_spec_keys": ["الاسم"]})
        await self.seed("tenant-scope")
        control = (await self.client.get("/order-review-export-controls-v1/tenant-scope")).json()["items"][0]
        self.assertEqual(control["manual_hidden_spec_keys"], [])

    async def test_parallel_open_materializes_once_and_approval_retains_customer_options(self):
        first = await self.seed("template-source")
        created = await self.client.post("/order-reviews-v1/template-source/operational-items", json={
            "expected_revision": first["revision"], "source_order_item_id": first["items"][0]["order_item_id"],
            "name": "Synthetic box", "linked_spec_keys": ["gift box", "الاسم"],
        })
        self.assertEqual(created.status_code, 200, created.text)
        payload = self.source_payload(number="parallel-default")
        payload["items"][0]["options"] = [{"name": "gift box", "value": "yes"}, {"name": "الاسم", "value": "CURRENT"}]
        self.assertTrue((await self.webhook(payload))["synced"])
        responses = await asyncio.gather(*[self.client.get("/order-reviews-v1/parallel-default?local_only=true") for _ in range(2)])
        for response in responses:
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(len(response.json()["operational_items"]), 1)
        self.assertEqual(responses[0].json()["revision"], responses[1].json()["revision"])
        detail = responses[0].json()
        result = await self.client.post("/order-reviews-v1/parallel-default/complete", json={
            "expected_revision": detail["revision"], "approval_token": detail["approval_token"]})
        self.assertEqual(result.status_code, 200, result.text)
        workflow = await self.db[review.WORKFLOWS].find_one({"order_number": "parallel-default"})
        self.assertEqual({o["name"]: o["value"] for o in workflow["items"][0]["options"]}, {"gift box": "yes", "الاسم": "CURRENT"})
        self.assertEqual({o["name"]: o["value"] for o in workflow["operational_items"][0]["linked_specs"]}, {"gift box": "yes", "الاسم": "CURRENT"})
        self.assertFalse(workflow["operational_items"][0]["financial_item"])
