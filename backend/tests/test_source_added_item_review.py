"""Real source intake -> explicit single-line review -> shared catalog."""
import asyncio
from copy import deepcopy
import unittest
from unittest.mock import AsyncMock, patch

import test_review_local_completion as completion_fixture
import test_review_local_assembly as assembly_fixture
import order_review_routes as review
import reviewed_products_catalog as catalog
from stock_component_consumption_service import PLANS, UNITS


class SourceAdditionReviewTests(unittest.IsolatedAsyncioTestCase):
    source_payload = completion_fixture.LocalCompletionTests.source_payload
    webhook = completion_fixture.LocalCompletionTests.webhook
    asyncTearDown = completion_fixture.LocalCompletionTests.asyncTearDown
    saved = completion_fixture.LocalCompletionTests.saved
    post = completion_fixture.LocalCompletionTests.post
    mount_assignment = assembly_fixture.LocalAssemblyTests.mount_assignment
    catalog_cards = assembly_fixture.LocalAssemblyTests.catalog_cards
    assign_cards = assembly_fixture.LocalAssemblyTests.assign_cards

    async def asyncSetUp(self):
        # unittest skips asyncTearDown when setup fails. The borrowed fixtures
        # append patches after their initial cleanup registrations.
        self.addCleanup(lambda: [replacement.stop() for replacement in
                                reversed(getattr(self, "patches", []))])
        await completion_fixture.LocalCompletionTests.asyncSetUp(self)
        response = await self.post()
        self.assertEqual(response.status_code, 200, response.text)
        self.number = "local-review"
        self.base_url = "/order-reviews-v1/local-review"
        self.mount_assignment()
        image_boundary = patch("reviewed_preparation_batches._download_card_image",
                               AsyncMock(return_value=(None, None)))
        image_boundary.start()
        self.addCleanup(image_boundary.stop)
        assigned = await self.assign_cards(await self.catalog_cards(), "original-assignment", mobile=True)
        self.assertEqual(assigned.status_code, 200, assigned.text)
        await self.db[review.WORKFLOWS].update_one({"order_number": self.number},
            {"$set": {"stage": "in_progress"}})
        self.original_workflow = await self.db[review.WORKFLOWS].find_one({"order_number": self.number})
        self.old_units = await self.db[UNITS].find({}).sort("_id").to_list(None)
        self.old_pieces = await self.db.mezan_preparation_pieces_v1.find({}).sort("_id").to_list(None)
        self.assertEqual(len(self.old_pieces), 2)
        self.original_source = deepcopy(self.payload)
        self.payload["status"] = {"slug": "in_progress", "name": "قيد التنفيذ"}
        self.payload["updated_at"] = "2026-09-26T14:00:00+00:00"
        for key in ("added-1", "added-2"):
            self.payload["items"].append({"id": key, "product_id": "p", "name": "Added product",
                "sku": "SYN-P", "quantity": 1, "options": [{"name": "الاسم", "value": key}]})
        self.assertTrue((await self.webhook(self.payload, "order.updated"))["synced"])

    async def capability(self):
        response = await self.client.get(self.base_url + "/source-additions")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    async def approve(self, index=0, capability=None):
        state = capability or await self.capability()
        key = state["items"][index]["order_item_id"]
        return await self.client.post(self.base_url + "/items/" + key + "/review-addition",
            json={"expected_revision": state["revision"], "source_fingerprint": state["source_fingerprint"]})

    async def test_independent_approval_preserves_old_units_and_parent(self):
        state = await self.capability()
        self.assertEqual(len(state["items"]), 2)
        self.assertTrue(all(r["eligible"] for r in state["items"]), state)
        before = await catalog.load_reviewed_product_context(self.db, user_id="owner", order_number=self.number)
        self.assertFalse(any("added-" in str(p) for p in before["catalog"]["products"]))
        result = await self.approve(capability=state)
        self.assertEqual(result.status_code, 200, result.text)
        current = await self.db[review.WORKFLOWS].find_one({"order_number": self.number})
        self.assertEqual(current["stage"], "in_progress")
        self.assertEqual(current["items"][:-1], self.original_workflow["items"])
        self.assertEqual(current["items"][-1]["options"], [{"name": "الاسم", "value": "added-1"}])
        old_ids = [r["_id"] for r in self.old_units]
        self.assertEqual(await self.db[UNITS].find({"_id": {"$in": old_ids}}).sort("_id").to_list(None), self.old_units)
        after = await catalog.load_reviewed_product_context(self.db, user_id="owner", order_number=self.number)
        self.assertIn("added-1", str(after["catalog"]["products"]))
        self.assertNotIn("added-2", str(after["catalog"]["products"]))
        self.assertEqual(await self.db.mezan_preparation_pieces_v1.find({}).sort("_id").to_list(None), self.old_pieces)
        self.assertEqual(await self.db.mezan_fulfillment_batches_v2.count_documents({}), 0)
        second = await self.approve(1)
        self.assertEqual(second.status_code, 200, second.text)
        self.external.assert_not_awaited()

    async def test_reviewed_addition_uses_ordinary_mobile_file_assignment(self):
        result = await self.approve()
        self.assertEqual(result.status_code, 200, result.text)
        cards = await self.catalog_cards()
        self.assertEqual(len(cards), 1)
        assigned = await self.assign_cards(cards, "added-assignment", mobile=True)
        self.assertEqual(assigned.status_code, 200, assigned.text)
        workflow = await self.db[review.WORKFLOWS].find_one({"order_number": self.number})
        self.assertEqual(workflow["stage"], "in_progress")
        self.assertEqual(await self.catalog_cards(), [])  # second addition still awaits review
        self.assertEqual(await self.db.mezan_preparation_pieces_v1.count_documents({}), 3)
        old_ids = [r["_id"] for r in self.old_pieces]
        self.assertEqual(await self.db.mezan_preparation_pieces_v1.find({"_id": {"$in": old_ids}}).sort("_id").to_list(None), self.old_pieces)

    async def test_duplicate_and_concurrent_clicks_append_once(self):
        state = await self.capability()
        results = await asyncio.gather(self.approve(capability=state), self.approve(capability=state))
        self.assertEqual([r.status_code for r in results], [200, 200], [r.text for r in results])
        self.assertEqual(sorted(r.json()["already_reviewed"] for r in results), [False, True])
        self.assertEqual(await self.db[review.EVENTS].count_documents({"event_type": "source_added_item_reviewed"}), 1)
        self.assertEqual(len((await self.db[review.WORKFLOWS].find_one({"order_number": self.number}))["items"]), 2)

    async def test_stale_revision_and_source_reject_without_approval(self):
        state = await self.capability()
        stale = {**state, "revision": state["revision"] + 1}
        self.assertEqual((await self.approve(capability=stale)).status_code, 409)
        stale = {**state, "source_fingerprint": "0" * 64}
        self.assertEqual((await self.approve(capability=stale)).status_code, 409)
        self.assertEqual(await self.db[review.EVENTS].count_documents({"event_type": "source_added_item_reviewed"}), 0)

    async def test_old_line_cannot_be_reviewed_as_addition(self):
        state = await self.capability()
        state["items"][0]["order_item_id"] = self.original_workflow["items"][0]["order_item_id"]
        self.assertEqual((await self.approve(capability=state)).status_code, 409)

    async def test_terminal_source_and_changed_originals_are_blocked(self):
        for mutation in ("cancel", "quantity", "delete", "options"):
            with self.subTest(mutation=mutation):
                payload = deepcopy(self.payload)
                if mutation == "cancel":
                    payload["status"] = {"slug": "canceled", "name": "ملغي"}
                elif mutation == "quantity":
                    payload["items"][0]["quantity"] += 1
                elif mutation == "delete":
                    payload["items"].pop(0)
                else:
                    payload["items"][0]["options"] = [{"name": "الاسم", "value": "changed"}]
                await self.db.unified_orders.update_one({"order_number": self.number},
                    {"$set": {"raw_by_source.salla_direct": payload,
                              "order_status_slug": payload["status"]["slug"], "order_status": payload["status"]["name"]}})
                state = await self.capability()
                self.assertFalse(any(r["eligible"] for r in state["items"]), state)
                self.assertEqual((await self.approve(capability=state)).status_code, 409)

    async def test_revoked_reviewer_denied_and_cross_owner_not_found(self):
        state = await self.capability()
        await self.db.users.update_one({"id": "owner"}, {"$set": {"is_active": False}})
        self.assertEqual((await self.approve(capability=state)).status_code, 403)
        self.assertEqual((await self.client.get(self.base_url + "/source-additions")).status_code, 403)
        await self.db.users.update_one({"id": "owner"}, {"$set": {"is_active": True}})
        self.actor = {"id": "other", "role": "owner"}
        await self.db.users.insert_one(self.actor)
        self.assertEqual((await self.client.get(self.base_url + "/source-additions")).status_code, 404)

    async def test_event_failure_rolls_back_workflow(self):
        state = await self.capability()
        before = await self.db[review.WORKFLOWS].find_one({"order_number": self.number})
        await self.db.command({"collMod": review.EVENTS,
            "validator": {"event_type": {"$ne": "source_added_item_reviewed"}}, "validationAction": "error"})
        response = await self.approve(capability=state)
        self.assertEqual(response.status_code, 500)
        self.assertEqual(await self.db[review.WORKFLOWS].find_one({"order_number": self.number}), before)

    async def test_component_retry_blocks_approval(self):
        await self.db.mezan_component_order_lifecycle_v1.update_one({"order_number": self.number},
            {"$set": {"retry_required": True}})
        state = await self.capability()
        self.assertTrue(all(r["blocked_reason"] == "source_addition_components_not_current" for r in state["items"]), state)
        self.assertEqual((await self.approve(capability=state)).status_code, 409)

    async def test_payload_cannot_supply_commercial_data(self):
        state = await self.capability()
        response = await self.client.post(self.base_url + "/items/" + state["items"][0]["order_item_id"] + "/review-addition",
            json={"expected_revision": state["revision"], "source_fingerprint": state["source_fingerprint"], "quantity": 999})
        self.assertEqual(response.status_code, 422)
