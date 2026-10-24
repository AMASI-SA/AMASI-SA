"""Real source intake -> explicit single-line review -> shared catalog."""
import asyncio
import os
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

    async def test_reviewed_and_started_local_orders_allow_addition_while_salla_pending(self):
        for stage, index in (("reviewed", 0), ("in_progress", 1)):
            with self.subTest(stage=stage):
                await self.db[review.WORKFLOWS].update_one({"order_number": self.number},
                    {"$set": {"stage": stage}})
                await self.db.unified_orders.update_one({"order_number": self.number}, {"$set": {
                    "order_status_slug": "under_review", "order_status": "بإنتظار المراجعة",
                    "raw_by_source.salla_direct.status": {"slug": "under_review", "name": "بإنتظار المراجعة"}}})
                state = await self.capability()
                self.assertTrue(state["items"][index]["eligible"], state)
                response = await self.approve(index, state)
                self.assertEqual(response.status_code, 200, response.text)
                workflow = await self.db[review.WORKFLOWS].find_one({"order_number": self.number})
                self.assertEqual(workflow["stage"], stage)
                self.assertEqual(workflow["items"][0], self.original_workflow["items"][0])
                self.assertEqual(await self.db.mezan_preparation_pieces_v1.find({}).sort("_id").to_list(None), self.old_pieces)
        self.external.assert_not_awaited()

    async def test_pending_internal_review_hides_additions_and_rejects_stale_click(self):
        state = await self.capability()
        await self.db[review.WORKFLOWS].update_one({"order_number": self.number},
            {"$set": {"stage": "pending_review"}})
        self.assertEqual((await self.capability())["items"], [])
        self.assertEqual((await self.approve(capability=state)).status_code, 409)

    async def test_terminal_source_shows_arabic_reason_and_blocks_approval(self):
        for status in ("completed", "shipped", "delivered", "canceled", "unknown"):
            with self.subTest(status=status):
                await self.db.unified_orders.update_one({"order_number": self.number}, {"$set": {
                    "order_status_slug": status, "order_status": status,
                    "raw_by_source.salla_direct.status": {"slug": status, "name": status}}})
                state = await self.capability()
                self.assertFalse(state["items"][0]["eligible"])
                self.assertIn("حالة الطلب الحالية", state["items"][0]["blocked_message"])
                self.assertEqual((await self.approve(capability=state)).status_code, 409)

    async def test_addition_notice_retains_first_observed_stage_after_stage_change(self):
        state = await self.capability()
        self.assertIn("قيد التنفيذ", state["items"][0]["addition_notice"])
        await self.db[review.WORKFLOWS].update_one({"order_number": self.number}, {"$set": {"stage": "reviewed"}})
        self.payload["updated_at"] = "2026-09-26T16:00:00+00:00"
        self.assertTrue((await self.webhook(self.payload, "order.updated"))["synced"])
        self.assertEqual((await self.capability())["items"][0]["addition_notice"], state["items"][0]["addition_notice"])
        await self.db.unified_orders.update_one({"order_number": self.number}, {"$unset": {"source_addition_observations": ""}})
        self.assertIn("غير مسجلة", (await self.capability())["items"][0]["addition_notice"])

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

    async def legacy_source_addition(self):
        selector = {"user_id": "owner", "order_number": self.number}
        await self.db[review.WORKFLOWS].update_one(selector, {"$unset": {
            "completion_mode": "", "review_completion_operation_id": ""}})
        await self.db[completion_fixture.completion.OPERATIONS].delete_many(selector)
        workflow = await self.db[review.WORKFLOWS].find_one(selector)
        self.assertTrue(workflow["reviewed_at"])
        self.assertEqual(workflow["items"], self.original_workflow["items"])
        # Exercise source intake again after removing local-only proof, so the
        # added line uses the legacy stock and review contracts end to end.
        self.payload["updated_at"] = "2026-09-26T15:00:00+00:00"
        self.payload["items"].append({"id": "legacy-added", "product_id": "p",
            "name": "Legacy added product", "sku": "SYN-P", "quantity": 1,
            "options": [{"name": "الاسم", "value": "legacy-added"}]})
        self.assertTrue((await self.webhook(self.payload, "order.updated"))["synced"])
        state = await self.capability()
        index = next(i for i, row in enumerate(state["items"])
                     if "legacy-added" in row["order_item_id"])
        return state, index

    async def test_legacy_addition_review_catalog_and_mobile_assignment(self):
        state, index = await self.legacy_source_addition()
        self.assertTrue(state["items"][index]["eligible"], state)
        result = await self.approve(index, state)
        self.assertEqual(result.status_code, 200, result.text)
        cards = await self.catalog_cards()
        self.assertEqual(len(cards), 1)
        self.assertIn("legacy-added", str(cards))
        assigned = await self.assign_cards(cards, "legacy-added-assignment", mobile=True)
        self.assertEqual(assigned.status_code, 200, assigned.text)
        workflow = await self.db[review.WORKFLOWS].find_one({"order_number": self.number})
        self.assertEqual(workflow["stage"], "in_progress")
        self.assertNotIn("completion_mode", workflow)
        self.assertEqual(await self.catalog_cards(), [])
        self.assertEqual(await self.db.mezan_preparation_pieces_v1.count_documents({}), 3)
        old_ids = [row["_id"] for row in self.old_pieces]
        self.assertEqual(await self.db.mezan_preparation_pieces_v1.find(
            {"_id": {"$in": old_ids}}).sort("_id").to_list(None), self.old_pieces)
        self.external.assert_not_awaited()

    async def test_legacy_addition_without_reviewed_at_is_blocked(self):
        state, index = await self.legacy_source_addition()
        await self.db[review.WORKFLOWS].update_one({"order_number": self.number},
            {"$unset": {"reviewed_at": ""}})
        state = await self.capability()
        self.assertFalse(state["items"][index]["eligible"])
        self.assertEqual(state["items"][index]["blocked_reason"],
                         "source_addition_review_proof_missing")
        result = await self.approve(index, state)
        self.assertEqual(result.status_code, 409, result.text)
        self.assertEqual(await self.catalog_cards(), [])
        self.assertEqual(await self.db[review.EVENTS].count_documents(
            {"event_type": "source_added_item_reviewed"}), 0)
        self.assertEqual(await self.db.mezan_preparation_pieces_v1.find({}).sort(
            "_id").to_list(None), self.old_pieces)

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

    async def pending_source_addition(self, *, updated_at="2026-09-26T17:00:00+00:00", quantity=1):
        # A source-only refresh persists the new line but deliberately does not
        # invoke the stock/automatic-routing callback.
        self.payload["updated_at"] = updated_at
        self.payload["items"].append({"id": "refresh-added", "product_id": "p",
            "name": "Added through refresh", "sku": "SYN-P", "quantity": quantity,
            "options": [{"name": "الاسم", "value": "refresh-added"}]})
        with patch("fulfillment_v2_routes.auto_route_instant_order", AsyncMock(return_value={})):
            self.assertTrue((await self.webhook(self.payload, "order.updated"))["synced"])
        source = await self.db.unified_orders.find_one({"order_number": self.number})
        self.assertTrue(source["g47_salla_snapshot"]["component_pending"])
        return next(i for i, row in enumerate((await self.capability())["items"])
                    if "refresh-added" in row["order_item_id"])

    async def test_pending_refresh_reconciles_only_on_explicit_review(self):
        index = await self.pending_source_addition()
        collections = await self.db.list_collection_names()
        before = {name: await self.db[name].find({}).sort("_id").to_list(None) for name in collections}
        state = await self.capability()
        self.assertTrue(state["items"][index]["eligible"], state)
        for name, rows in before.items():
            self.assertEqual(await self.db[name].find({}).sort("_id").to_list(None), rows, name)
        response = await self.approve(index, state)
        self.assertEqual(response.status_code, 200, response.text)
        source = await self.db.unified_orders.find_one({"order_number": self.number})
        self.assertFalse(source["g47_salla_snapshot"]["component_pending"])
        self.assertIn("refresh-added", str(await self.catalog_cards()))
        self.assertEqual(await self.db.mezan_preparation_pieces_v1.find({}).sort("_id").to_list(None), self.old_pieces)
        self.assertEqual(await self.db[UNITS].find({"_id": {"$in": [r["_id"] for r in self.old_units]}}).sort("_id").to_list(None), self.old_units)
        workflow = await self.db[review.WORKFLOWS].find_one({"order_number": self.number})
        self.assertEqual(workflow["stage"], "in_progress")
        self.assertEqual(workflow["items"][:-1], self.original_workflow["items"])
        self.external.assert_not_awaited()

    async def test_pending_reconciliation_and_review_roll_back_together(self):
        index = await self.pending_source_addition()
        state = await self.capability()
        collections = await self.db.list_collection_names()
        before = {name: await self.db[name].find({}).sort("_id").to_list(None) for name in collections}
        await self.db.command({"collMod": review.EVENTS,
            "validator": {"event_type": {"$ne": "source_added_item_reviewed"}}, "validationAction": "error"})
        self.assertEqual((await self.approve(index, state)).status_code, 500)
        for name, rows in before.items():
            self.assertEqual(await self.db[name].find({}).sort("_id").to_list(None), rows, name)

    async def test_pending_refresh_stock_shortage_cannot_approve_or_change_old_units(self):
        index = await self.pending_source_addition(quantity=10)
        state = await self.capability()
        collections = await self.db.list_collection_names()
        before = {name: await self.db[name].find({}).sort("_id").to_list(None) for name in collections}
        response = await self.approve(index, state)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["detail"]["code"], "component_insufficient_stock")
        for name, rows in before.items():
            self.assertEqual(await self.db[name].find({}).sort("_id").to_list(None), rows, name)

    async def test_pending_refresh_rechecks_source_before_reconciling(self):
        index = await self.pending_source_addition()
        state = await self.capability()
        await self.db.unified_orders.update_one({"order_number": self.number},
            {"$inc": {"g47_salla_snapshot.revision": 1}})
        before = await self.db[PLANS].find_one({"order_id": self.number})
        response = await self.approve(index, state)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["detail"]["code"], "source_addition_source_changed")
        self.assertEqual(await self.db[PLANS].find_one({"order_id": self.number}), before)

    async def test_pending_refresh_same_provider_timestamp_uses_canonical_revision(self):
        index = await self.pending_source_addition(updated_at=self.payload["updated_at"])
        response = await self.approve(index)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("refresh-added", str(await self.catalog_cards()))

    async def test_paid_refresh_recovers_awaiting_eligibility_without_provider_clock_change(self):
        await self.legacy_source_addition()
        self.payload.update({"updated_at": "2026-09-26T18:00:00+00:00", "payment_method": "credit_card",
            "payment_actions": {"remaining_action": {"has_remaining_amount": True, "paid_amount": 80, "remaining_amount": 20}}})
        self.assertTrue((await self.webhook(self.payload, "order.updated"))["synced"])
        lifecycle = await self.db.mezan_component_order_lifecycle_v1.find_one({"order_number": self.number})
        self.assertEqual(lifecycle["state"], "awaiting_eligibility")
        self.payload["payment_actions"]["remaining_action"] = {"has_remaining_amount": False, "paid_amount": 100, "remaining_amount": None}
        refreshed = await completion_fixture.fixture.ComponentRouteTests.refresh(
            self, self.payload, allow_auto_fulfillment=False)
        self.assertTrue(refreshed["ok"], refreshed)
        state = await self.capability()
        index = next(i for i, row in enumerate(state["items"]) if "legacy-added" in row["order_item_id"])
        self.assertTrue(state["items"][index]["requires_component_reconciliation"], state)
        response = await self.approve(index, state)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("legacy-added", str(await self.catalog_cards()))
        self.assertEqual(await self.db.mezan_preparation_pieces_v1.find({}).sort("_id").to_list(None), self.old_pieces)
        self.external.assert_not_awaited()

    async def test_same_timestamp_reconciliation_rejects_noncanonical_dto(self):
        import fulfillment_v2_routes as fulfillment
        from order_engine.repository import MongoOrderRepository
        from order_engine.service import get_order
        from fastapi import HTTPException
        await self.pending_source_addition(updated_at=self.payload["updated_at"])
        source = await self.db.unified_orders.find_one({"order_number": self.number})
        watermark = source["g47_salla_snapshot"]
        order = await get_order(MongoOrderRepository(self.db), user_id="owner", order_number=self.number)
        order.items[-1].quantity = 99
        before = await self.db[PLANS].find_one({"order_id": self.number})
        with self.assertRaises(HTTPException) as error:
            await fulfillment.reconcile_component_order_lifecycle(self.db, user_id="owner", order=order,
                source_updated_at=watermark["source_updated_at"], source_revision=watermark["revision"], strict=True)
        self.assertEqual(error.exception.detail["code"], "component_source_event_stale")
        self.assertEqual(await self.db[PLANS].find_one({"order_id": self.number}), before)

    async def test_pending_refresh_does_not_bypass_authoritative_refresh_requirement(self):
        index = await self.pending_source_addition()
        await self.db.unified_orders.update_one({"order_number": self.number},
            {"$set": {"g47_salla_snapshot.requires_authoritative_refresh": True}})
        state = await self.capability()
        self.assertFalse(state["items"][index]["eligible"])
        self.assertEqual((await self.approve(index, state)).status_code, 409)

    async def test_payload_cannot_supply_commercial_data(self):
        state = await self.capability()
        response = await self.client.post(self.base_url + "/items/" + state["items"][0]["order_item_id"] + "/review-addition",
            json={"expected_revision": state["revision"], "source_fingerprint": state["source_fingerprint"], "quantity": 999})
        self.assertEqual(response.status_code, 422)


    async def test_operational_disable_hides_capability_and_rejects_stale_post_without_writes(self):
        state = await self.capability()
        collections = await self.db.list_collection_names()
        before = {name: await self.db[name].find({}).sort("_id").to_list(None) for name in collections}
        with patch.dict(os.environ, {"SOURCE_ADDITION_REVIEW_ENABLED": "false"}):
            hidden = await self.capability()
            self.assertEqual(hidden, {**state, "items": []})
            with patch("source_added_item_review.operational_owner", AsyncMock()) as transaction:
                response = await self.approve(capability=state)
                self.assertEqual(response.status_code, 503, response.text)
                self.assertEqual(response.json()["detail"]["code"], "source_addition_review_disabled")
                transaction.assert_not_awaited()
        after = {name: await self.db[name].find({}).sort("_id").to_list(None)
                 for name in await self.db.list_collection_names()}
        self.assertEqual(before, after)
        self.external.assert_not_awaited()

    async def test_disable_preserves_approved_queue_and_reenable_idempotency(self):
        state = await self.capability()
        self.assertEqual((await self.approve(capability=state)).status_code, 200)
        cards = await self.catalog_cards()
        with patch.dict(os.environ, {"SOURCE_ADDITION_REVIEW_ENABLED": "0"}):
            self.assertEqual((await self.capability())["items"], [])
            self.assertEqual(await self.catalog_cards(), cards)
            self.assertEqual((await self.approve(capability=state)).status_code, 503)
        with patch.dict(os.environ, {"SOURCE_ADDITION_REVIEW_ENABLED": "true"}):
            retried = await self.approve(capability=state)
            self.assertEqual(retried.status_code, 200, retried.text)
            self.assertTrue(retried.json()["already_reviewed"])
            self.assertEqual(await self.db[review.EVENTS].count_documents(
                {"event_type": "source_added_item_reviewed"}), 1)
            self.assertEqual((await self.approve(1)).status_code, 200)
        self.assertEqual(await self.db.mezan_preparation_pieces_v1.find({}).sort("_id").to_list(None), self.old_pieces)

    async def test_disable_rechecked_inside_owner_transaction(self):
        state = await self.capability()
        before = await self.db[review.WORKFLOWS].find_one({"order_number": self.number})
        with patch("source_added_item_review.source_addition_review_enabled", side_effect=[True, False]):
            response = await self.approve(capability=state)
        self.assertEqual(response.status_code, 503, response.text)
        self.assertEqual(await self.db[review.WORKFLOWS].find_one({"order_number": self.number}), before)
        self.assertEqual(await self.db[review.EVENTS].count_documents(
            {"event_type": "source_added_item_reviewed"}), 0)

    async def test_switch_default_and_strict_true_values(self):
        from source_added_item_review import source_addition_review_enabled
        with patch.dict(os.environ):
            os.environ.pop("SOURCE_ADDITION_REVIEW_ENABLED", None)
            self.assertTrue(source_addition_review_enabled())
            for value in ("true", "1", "yes", "on", " TRUE "):
                os.environ["SOURCE_ADDITION_REVIEW_ENABLED"] = value
                self.assertTrue(source_addition_review_enabled(), value)
            for value in ("false", "0", "", "enabled", "tru", "2"):
                os.environ["SOURCE_ADDITION_REVIEW_ENABLED"] = value
                self.assertFalse(source_addition_review_enabled(), value)
