"""Real review/stock/allocation transactions, synthetic provider transport only."""
from copy import deepcopy
import unittest
from unittest.mock import patch
from uuid import uuid4

import operational_atomic
import order_review_completion as completion
import preparation_piece_operations as pieces
import review_direct_preparation_sync as direct
import test_review_local_assembly as assembly_fixture


class DirectPreparationSyncTests(unittest.IsolatedAsyncioTestCase):
    asyncTearDown = assembly_fixture.LocalAssemblyTests.asyncTearDown
    source_payload = assembly_fixture.LocalAssemblyTests.source_payload
    webhook = assembly_fixture.LocalAssemblyTests.webhook
    on_hand = assembly_fixture.LocalAssemblyTests.on_hand
    mount_assignment = assembly_fixture.LocalAssemblyTests.mount_assignment
    catalog_cards = assembly_fixture.LocalAssemblyTests.catalog_cards

    async def asyncSetUp(self):
        await assembly_fixture.LocalAssemblyTests.asyncSetUp(self)
        self.remote_status = "under_review"
        self.provider_calls = []
        self.uncertain = False
        self.terminal_after_reserve = False
        self.terminal_source_on_confirmation = False
        self.change_quantity_before_reserve = False
        self.foreign_provider_identity = False
        self.number = "direct-preparation-sync"
        transport = patch.object(pieces, "call_salla", side_effect=self.provider)
        transport.start(); self.patches.append(transport)

    async def provider(self, db, owner, method, path, **kwargs):
        self.assertIsNone(operational_atomic._ACTIVE.get(), "provider I/O must occur after local transaction commits")
        self.assertEqual(owner, "owner")
        self.provider_calls.append((method, path, kwargs))
        if path == "/orders/statuses":
            return {"data": [{"id": 71, "name": "قيد التنفيذ", "slug": "in_progress"}]}
        if method == "POST":
            self.assertTrue(kwargs.get("single_post_attempt"))
            marker = await self.db[direct.WORKFLOWS].find_one({"order_number": self.number})
            self.assertEqual(marker["salla_status_sync_state"], "dispatch_started")
            self.assertEqual(marker["stage"], "reviewed", "local state must not advance before provider confirmation")
            if self.uncertain:
                raise TimeoutError("synthetic uncertain status dispatch")
            self.remote_status = "in_progress"
            return {"success": True}
        self.assertEqual(method, "GET")
        self.assertEqual(path, "/orders/" + self.number)
        row = await self.db.unified_orders.find_one({"order_number": self.number})
        raw = deepcopy(row["raw_by_source"]["salla_direct"])
        if self.terminal_after_reserve:
            workflow = await self.db[direct.WORKFLOWS].find_one({"order_number": self.number})
            if workflow.get("salla_status_sync_state") == "dispatch_started":
                self.remote_status = "completed"
        raw["status"] = {"slug": self.remote_status, "name": "قيد التنفيذ" if self.remote_status == "in_progress" else self.remote_status}
        raw["status_slug"] = self.remote_status
        if self.foreign_provider_identity:
            raw["id"] = "another-order"
        if self.change_quantity_before_reserve and not self.posts():
            await self.db.unified_orders.update_one({"order_number": self.number}, {"$set": {
                "raw_by_source.salla_direct.items.0.quantity": 3}})
        if self.terminal_source_on_confirmation and self.remote_status == "in_progress" and self.posts():
            await self.db.unified_orders.update_one({"order_number": self.number}, {"$set": {
                "order_status": "cancelled", "order_status_slug": "cancelled",
                "raw_by_source.salla_direct.status": {"slug": "cancelled", "name": "cancelled"},
                "raw_by_source.salla_direct.status_slug": "cancelled"}})
        return {"data": raw}

    def posts(self):
        return [call for call in self.provider_calls if call[0] == "POST"]

    async def workflow(self):
        return await self.db[direct.WORKFLOWS].find_one({"user_id": "owner", "order_number": self.number})

    async def approve(self, *, mixed=False, direct_route=True, native_v2=False):
        if native_v2:
            import review_session_fence as sessions
            import review_completion_readback as readback
            from review_acceptance_config_guard import ensure_acceptance_config_storage
            await sessions.ensure_review_session_storage(self.db)
            await readback.ensure_readback_storage(self.db)
            await ensure_acceptance_config_storage(self.db)
            self.actor = {"id": "native-reviewer", "created_by": "owner", "role": "employee",
                "name": "Synthetic native reviewer", "email": "native@example.test", "_session_client": "amasi_mobile",
                "mobile_app_permissions": ["app.page.pending_review"]}
            await self.db.users.insert_one(deepcopy(self.actor))
            self.actor["_review_session"] = await sessions.create_review_session(self.db, self.actor)
        payload = self.source_payload(number=self.number)
        if mixed:
            payload["items"].append({**payload["items"][0], "id": "supplier-line"})
        self.assertTrue((await self.webhook(payload))["synced"])
        order = await pieces._current_assembly_order(self.db, user_id="owner", order_number=self.number)
        self.item_id = order.items[0].order_item_id
        await self.db[direct.WORKFLOWS].update_one({"user_id": "owner", "order_number": self.number}, {"$set": {
            "stage": "pending_review", "revision": 0,
            "items": [{"order_item_id": self.item_id, "preparation_route": "direct_assembly",
                "supplier_export": False, "product_id": "p", "quantity": 2}] if direct_route else [],
            "operational_items": [],
        }}, upsert=True)
        displayed = await self.client.get(f"/order-reviews-v1/{self.number}", params={
            "local_only": "true", **({"readback_contract_version": 2} if native_v2 else {})})
        self.assertEqual(displayed.status_code, 200, displayed.text)
        self.before_source = await self.db.unified_orders.find_one({"order_number": self.number})
        self.detail = displayed.json()
        request = {"expected_revision": self.detail["revision"], "approval_token": self.detail["approval_token"]}
        if native_v2:
            self.client_request_id = str(uuid4())
            request.update(readback_contract_version=2, client_request_id=self.client_request_id)
        response = await self.client.post(f"/order-reviews-v1/{self.number}/complete", json=request)
        self.assertEqual(response.status_code, 200, response.text)
        self.result = response.json()
        self.assertEqual(self.result["state"], "completed")
        self.assertEqual(self.result["salla_status_sync"], "not_requested", "immutable review receipt remains local")
        self.external.assert_not_awaited()
        return response.json()

    async def reconcile(self, *, allow_dispatch=False):
        return await direct.reconcile_direct_preparation_after_review(self.db, user_id="owner",
            order_number=self.number, actor=self.actor, result=self.result, allow_dispatch=allow_dispatch)

    async def test_all_direct_approves_then_syncs_without_supplier_file_or_financial_changes(self):
        result = await self.approve()
        self.assertEqual(result["preparation_status_sync"]["state"], "sent")
        self.assertEqual(result["current_stage"], "in_progress")
        workflow = await self.workflow()
        self.assertEqual(workflow["stage"], "in_progress")
        self.assertEqual(workflow["salla_status_sync_state"], "sent")
        self.assertEqual(len(workflow["items"][0]["direct_assembly_piece_ids"]), 2)
        self.assertEqual(len(self.posts()), 1)
        self.assertGreaterEqual(len([call for call in self.provider_calls if call[0] == "GET" and call[1] != "/orders/statuses"]), 3)
        self.assertEqual(await self.db[pieces.BATCHES].count_documents({}), 0)
        self.assertEqual(await self.db[pieces.REGISTRY].count_documents({}), 0)
        self.assertEqual(await self.db[pieces.PIECES].count_documents({}), 0)
        self.assertEqual(await self.on_hand(), 20)
        stored = await self.db.unified_orders.find_one({"order_number": self.number})
        for key in ("items", "amounts", "payment_method", "shipping_address"):
            self.assertEqual(stored["raw_by_source"]["salla_direct"].get(key), self.before_source["raw_by_source"]["salla_direct"].get(key))
        self.assertEqual(stored["order_status_slug"], "in_progress")
        actual = await pieces._current_assembly_order(self.db, user_id="owner", order_number=self.number)
        self.assertEqual(actual.status_native, "قيد التنفيذ")
        operation = await self.db[completion.OPERATIONS].find_one({"_id": workflow["review_completion_operation_id"]})
        self.assertEqual(operation["result"]["stage"], "reviewed")
        self.assertEqual(operation["result"]["salla_status_sync"], "not_requested")
        prior_revision = workflow["revision"]
        await self.reconcile()
        self.assertEqual(len(self.posts()), 1)
        self.assertEqual((await self.workflow())["revision"], prior_revision)

    async def test_already_in_progress_confirms_without_post(self):
        self.remote_status = "in_progress"
        result = await self.approve()
        self.assertEqual(result["preparation_status_sync"]["state"], "sent")
        self.assertEqual(self.posts(), [])
        self.assertEqual((await self.workflow())["stage"], "in_progress")

    async def test_native_v2_lost_ack_retains_exact_approval_revision_and_read_only_commit_proof(self):
        reply = await self.approve(native_v2=True)
        self.assertEqual(reply["preparation_status_sync"]["state"], "sent")
        self.assertEqual(reply["operation"]["actor_id"], "native-reviewer")
        self.assertEqual(reply["operation"]["merchant_id"], "owner")
        workflow = await self.workflow()
        self.assertEqual(workflow["revision"], self.detail["revision"] + 1)
        self.assertEqual(workflow["preparation_status_revision"], 1)
        self.assertEqual(workflow["in_progress_by"], "native-reviewer")
        self.assertEqual(workflow["user_id"], "owner")
        before = {name: await self.db[name].find({}).to_list(None) for name in await self.db.list_collection_names()}
        for _ in range(3):
            # Simulate a lost POST response: only exact-attempt GET is used.
            response = await self.client.get(f"/order-reviews-v1/{self.number}/completion-operation", params={
                "readback_contract_version": 2, "client_request_id": self.client_request_id})
            self.assertEqual(response.status_code, 200, response.text)
            proof = response.json()
            self.assertEqual(proof["state"], "completed")
            self.assertEqual(proof["current_revision"], proof["operation"]["committed_revision"])
            self.assertEqual(proof["current_operation_id"], proof["operation"]["operation_id"])
            self.assertEqual(proof["operation"], reply["operation"])
            self.assertEqual(proof["operation"]["salla_status_sync"], "not_requested")
            self.assertEqual(response.headers["cache-control"], "no-store")
        after = {name: await self.db[name].find({}).to_list(None) for name in await self.db.list_collection_names()}
        self.assertEqual(before, after, "exact-attempt GET is still read only after preparation status sync")
        self.assertEqual(len(self.posts()), 1)

    async def test_uncertain_post_is_never_repeated_read_only_reconciliation_confirms(self):
        self.uncertain = True
        result = await self.approve()
        self.assertEqual(result["preparation_status_sync"]["state"], "pending")
        self.assertEqual((await self.workflow())["stage"], "reviewed")
        self.assertEqual((await self.workflow())["salla_status_sync_state"], "dispatch_started")
        self.assertEqual(len(self.posts()), 1)
        pending = await self.reconcile(allow_dispatch=True)
        self.assertEqual(pending["preparation_status_sync"]["state"], "pending")
        self.assertEqual(len(self.posts()), 1)
        self.remote_status = "in_progress"
        response = await self.client.get(f"/order-reviews-v1/{self.number}/preparation-status")
        self.assertEqual(response.status_code, 200, response.text)
        confirmed = response.json()
        self.assertTrue(confirmed["provider_read_only"])
        self.assertEqual(confirmed["preparation_status_sync"]["state"], "sent")
        self.assertEqual(len(self.posts()), 1)
        self.assertEqual((await self.workflow())["stage"], "in_progress")
        self.assertEqual(await self.db[direct.EVENTS].count_documents({"event_type": "order_moved_to_in_progress"}), 1)

    async def test_status_get_cannot_dispatch_and_keeps_permission_and_tenant_boundaries(self):
        self.remote_status = "completed"
        await self.approve()
        self.remote_status = "under_review"
        response = await self.client.get(f"/order-reviews-v1/{self.number}/preparation-status")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["provider_read_only"])
        self.assertEqual(response.json()["preparation_status_sync"]["state"], "pending")
        self.assertEqual(self.posts(), [])
        self.assertNotIn("salla_status_sync_state", await self.workflow())
        source = deepcopy(self.before_source)
        source.pop("_id", None)
        source.update(user_id="other-owner", order_number="other-order")
        source["raw_by_source"]["salla_direct"].update(id="other-order", reference_id="other-order")
        await self.db.unified_orders.insert_one(source)
        before = len(self.provider_calls)
        response = await self.client.get("/order-reviews-v1/other-order/preparation-status")
        self.assertEqual(response.status_code, 404, response.text)
        self.actor["role"] = "employee"
        response = await self.client.get(f"/order-reviews-v1/{self.number}/preparation-status")
        self.assertEqual(response.status_code, 403, response.text)
        self.assertEqual(len(self.provider_calls), before)

    async def assert_terminal(self, status):
        self.remote_status = status
        result = await self.approve()
        self.assertEqual(result["preparation_status_sync"]["reason"], "salla_current_status_not_eligible")
        self.assertEqual(self.posts(), [])
        self.assertEqual((await self.workflow())["stage"], "reviewed")
        self.assertNotIn("salla_status_sync_state", await self.workflow())
        source = await self.db.unified_orders.find_one({"order_number": self.number})
        self.assertEqual(source["raw_by_source"], self.before_source["raw_by_source"])

    async def test_current_completed_is_never_reopened(self):
        await self.assert_terminal("completed")

    async def test_current_cancelled_is_never_reopened(self):
        await self.assert_terminal("cancelled")

    async def test_current_shipping_is_never_reopened(self):
        await self.assert_terminal("shipping")

    async def test_current_unknown_does_not_authorize_write(self):
        await self.assert_terminal("future-status")

    async def test_terminal_after_reservation_blocks_post_and_keeps_permanent_marker(self):
        self.terminal_after_reserve = True
        result = await self.approve()
        self.assertEqual(result["preparation_status_sync"]["state"], "pending")
        self.assertEqual(self.posts(), [])
        self.assertEqual((await self.workflow())["salla_status_sync_state"], "dispatch_started")

    async def test_foreign_provider_identity_never_grants_status_authority(self):
        self.foreign_provider_identity = True
        result = await self.approve()
        self.assertEqual(result["preparation_status_sync"]["state"], "pending")
        self.assertEqual(self.posts(), [])
        self.assertNotIn("salla_status_sync_state", await self.workflow())

    async def test_source_quantity_changed_before_reservation_prevents_post(self):
        self.change_quantity_before_reserve = True
        result = await self.approve()
        self.assertEqual(result["preparation_status_sync"]["state"], "pending")
        self.assertEqual(self.posts(), [])
        self.assertNotIn("salla_status_sync_state", await self.workflow())

    async def test_terminal_source_during_readback_is_not_overwritten_locally(self):
        self.terminal_source_on_confirmation = True
        result = await self.approve()
        self.assertEqual(result["preparation_status_sync"]["state"], "pending")
        self.assertEqual(len(self.posts()), 1)
        self.assertEqual((await self.workflow())["stage"], "reviewed")
        source = await self.db.unified_orders.find_one({"order_number": self.number})
        self.assertEqual(source["order_status"], "cancelled")
        self.assertEqual(source["raw_by_source"]["salla_direct"]["status"]["slug"], "cancelled")

    async def test_current_completed_proof_and_exact_direct_ids_cannot_be_fabricated(self):
        await self.approve()
        workflow = await self.workflow()
        operation = await self.db[completion.OPERATIONS].find_one({"_id": workflow["review_completion_operation_id"]})
        cases = [
            (completion.OPERATIONS, {"state": "syncing"}),
            (completion.OPERATIONS, {"superseded_by": "another-operation"}),
            (direct.WORKFLOWS, {"items.0.direct_assembly_piece_ids": []}),
            (direct.WORKFLOWS, {"items.0.quantity": 0}),
            (direct.WORKFLOWS, {"items.0.quantity": 1.5}),
            ("unified_orders", {"raw_by_source.salla_direct.items.0.quantity": 3}),
        ]
        for collection, patch_fields in cases:
            with self.subTest(collection=collection, patch=patch_fields):
                original = await self.db[collection].find_one({})
                await self.db[collection].update_one({"_id": original["_id"]}, {"$set": patch_fields})
                before = len(self.provider_calls)
                try:
                    result = await self.reconcile(allow_dispatch=True)
                    self.assertEqual(result["preparation_status_sync"]["state"], "pending")
                    self.assertEqual(len(self.provider_calls), before)
                finally:
                    await self.db[collection].replace_one({"_id": original["_id"]}, original)

    async def test_supplier_only_review_retains_local_zero_provider_contract(self):
        result = await self.approve(direct_route=False)
        self.assertNotIn("preparation_status_sync", result)
        self.assertEqual(self.provider_calls, [])
        self.assertEqual((await self.workflow())["stage"], "reviewed")

    async def test_mixed_partial_waits_then_last_supplier_allocation_uses_same_guarded_transition(self):
        await self.approve(mixed=True)
        self.assertEqual(self.provider_calls, [])
        workflow = await self.workflow()
        supplier = next(item for item in workflow["items"] if item["order_item_id"] != self.item_id)
        for index in (1, 2):
            await self.db[pieces.PREPARATION_UNIT_ALLOCATIONS].insert_one({"user_id": "owner",
                "order_number": self.number, "order_item_id": supplier["order_item_id"], "unit_index": index,
                "status": "committed"})
            moved, remaining = await pieces._assigned_reconcile_order_stage(self.db, user_id="owner",
                order_number=self.number, batch_id="supplier-batch", actor=self.actor)
            self.assertEqual(moved, index == 2)
            self.assertEqual(remaining, 2 - index)
            self.assertEqual(len(self.posts()), int(index == 2))
        self.assertEqual((await self.workflow())["stage"], "in_progress")
        self.assertEqual((await self.workflow())["preparation_batch_ids"], ["supplier-batch"])


if __name__ == "__main__":
    unittest.main()
