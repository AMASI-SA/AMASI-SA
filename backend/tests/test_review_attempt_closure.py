"""Explicit closure on real Mongo transactions, with synthetic ASGI traffic only.

Absence remains UNKNOWN. A separately requested atomic revision barrier is the
only new evidence permitting a fresh approval; no original POST is replayed.
"""
import asyncio
import hashlib
import unittest
from unittest.mock import patch

from pymongo.errors import AutoReconnect

import operational_atomic
import order_review_completion as completion
import order_review_export_controls as controls
import review_completion_readback as readback
import review_session_fence as sessions
import test_review_readback_v2 as v2


class ReviewAttemptClosureTests(unittest.IsolatedAsyncioTestCase):
    asyncTearDown = v2.ReviewReadbackV2Tests.asyncTearDown
    source_payload = v2.ReviewReadbackV2Tests.source_payload
    webhook = v2.ReviewReadbackV2Tests.webhook
    saved = v2.ReviewReadbackV2Tests.saved
    display = v2.ReviewReadbackV2Tests.display
    request = v2.ReviewReadbackV2Tests.request
    submit = v2.ReviewReadbackV2Tests.submit
    reconcile = v2.ReviewReadbackV2Tests.reconcile
    state = v2.ReviewReadbackV2Tests.state
    assert_binding = v2.ReviewReadbackV2Tests.assert_binding
    assert_commit = v2.ReviewReadbackV2Tests.assert_commit
    assert_unknown = v2.ReviewReadbackV2Tests.assert_unknown
    fresh_family = v2.ReviewReadbackV2Tests.fresh_family

    async def asyncSetUp(self):
        await v2.ReviewReadbackV2Tests.asyncSetUp(self)
        from accounting_ledger_v2 import GENERAL_LEDGER_COLLECTION, GROUPS_COLLECTION
        await self.db[GENERAL_LEDGER_COLLECTION].insert_one({"id": "synthetic-ledger", "user_id": "owner", "amount": 17})
        await self.db[GROUPS_COLLECTION].insert_one({"id": "synthetic-group", "user_id": "owner", "amount": 17})
        await self.db.payment_transactions.insert_one({"id": "synthetic-payment", "user_id": "owner", "amount": 17})

    def receipt(self, detail, payload):
        return {**detail["approval_context"], "client_request_id": payload["client_request_id"],
                "approval_token_sha256": hashlib.sha256(payload["approval_token"].encode()).hexdigest()}

    async def close(self, binding, number="local-review"):
        return await self.client.post(f"/order-reviews-v1/{number}/completion-operation/close", json=binding)

    def assert_closed(self, response, binding):
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers.get("cache-control"), "no-store")
        result = response.json()
        self.assertEqual(result["state"], "not_committed")
        proof = result["operation"]
        self.assertEqual({key: proof[key] for key in readback.BINDING_FIELDS}, binding)
        self.assertIs(proof["commit_confirmed"], False)
        self.assertIs(proof["retry_post"], False)
        self.assertIs(proof["refresh_required"], True)
        self.assertEqual(proof["closure_contract_version"], 1)
        self.assertEqual(proof["state"], "not_committed")
        self.assertEqual(proof["completion_mode"], "mezan_local_v1")
        self.assertEqual(proof["event_id"], proof["operation_id"] + ":closed")
        self.assertEqual(proof["event_type"], "order_review_attempt_closed")
        self.assertGreater(proof["barrier_revision"], binding["approved_revision"])
        self.assertGreaterEqual(result["current_revision"], proof["barrier_revision"])
        self.assertTrue(proof["closed_at"])
        self.external.assert_not_awaited()
        return proof

    async def business_state(self):
        allowed = {completion.OPERATIONS, completion.EVENTS, completion.WORKFLOWS,
                   "mz2_atomic_owners", sessions.SESSIONS}
        result = {}
        for name in sorted(set(await self.db.list_collection_names()) - allowed):
            rows = await self.db[name].find({}).sort("_id", 1).to_list(None)
            if rows:
                result[name] = rows
        return result

    async def test_absence_get_stays_unknown_close_fences_late_post_and_fresh_approval_commits_once(self):
        detail = await self.display()
        payload = self.request(detail)
        binding = self.receipt(detail, payload)
        before = await self.business_state()
        self.assert_unknown(await self.reconcile(payload["client_request_id"]))
        proof = self.assert_closed(await self.close(binding), binding)
        self.assertEqual(await self.business_state(), before)
        self.assertIsNone((await self.close(binding)).json()["current_operation_id"])
        self.assertEqual(proof, self.assert_closed(await self.submit(payload), binding))
        fresh = await self.display()
        self.assertEqual(fresh["revision"], proof["barrier_revision"])
        self.assertNotEqual(fresh["approval_fingerprint"], detail["approval_fingerprint"])
        new_payload = self.request(fresh)
        completed = await self.assert_commit(await self.submit(new_payload), fresh, new_payload)
        await self.assert_commit(await self.submit(new_payload), fresh, new_payload)
        old = await self.reconcile(payload["client_request_id"])
        self.assertEqual(proof, self.assert_closed(old, binding))
        self.assertEqual(old.json()["current_operation_id"], completed["operation_id"])
        self.assertEqual(await self.db[completion.OPERATIONS].count_documents({"state": "completed"}), 1)
        self.assertEqual(await self.db[completion.EVENTS].count_documents({"event_type": "order_review_completed"}), 1)

    async def test_lost_closure_ack_get_proves_exact_durable_barrier_without_writes(self):
        detail = await self.display()
        payload = self.request(detail)
        binding = self.receipt(detail, payload)
        real_owner = operational_atomic.operational_owner
        async def lose_ack(*args, **kwargs):
            await real_owner(*args, **kwargs)
            raise AutoReconnect("synthetic acknowledgement loss")
        with patch.object(operational_atomic, "operational_owner", lose_ack):
            self.assert_unknown(await self.close(binding), 503)
        before = await self.state(all_collections=True)
        first = self.assert_closed(await self.reconcile(payload["client_request_id"]), binding)
        self.assertEqual(first, self.assert_closed(await self.reconcile(payload["client_request_id"]), binding))
        self.assertEqual(await self.state(all_collections=True), before)

    async def test_closure_abort_rolls_back_tombstone_event_revision_and_session_fence(self):
        detail = await self.display()
        payload = self.request(detail)
        binding = self.receipt(detail, payload)
        before = await self.state(all_collections=True)
        real_result = readback.operation_result
        async def abort_after_writes(scoped, operation):
            if operation and operation.get("state") == "not_committed":
                raise AutoReconnect("synthetic failure before transaction commit")
            return await real_result(scoped, operation)
        with patch.object(readback, "operation_result", abort_after_writes):
            self.assert_unknown(await self.close(binding), 503)
        self.assertEqual(await self.state(all_collections=True), before)
        self.assert_unknown(await self.reconcile(payload["client_request_id"]))
        await self.assert_commit(await self.submit(payload), detail, payload)

    async def test_completion_paused_after_route_reads_cannot_pass_closed_consent(self):
        detail = await self.display()
        payload = self.request(detail)
        binding = self.receipt(detail, payload)
        entered, release = asyncio.Event(), asyncio.Event()
        real_owner = completion.operational_owner
        async def delay(db, owner, callback, **kwargs):
            if callback.__name__ == "finish_local":
                entered.set()
                await asyncio.wait_for(release.wait(), 5)
            return await real_owner(db, owner, callback, **kwargs)
        with patch.object(completion, "operational_owner", delay):
            task = asyncio.create_task(self.submit(payload))
            try:
                await asyncio.wait_for(entered.wait(), 5)
                self.assert_closed(await self.close(binding), binding)
            finally:
                release.set()
            response = await task
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["detail"]["code"], "review_attempt_closed")
        self.assertEqual(await self.db[completion.OPERATIONS].count_documents({"state": "completed"}), 0)
        self.assertEqual(await self.db[completion.EVENTS].count_documents({"event_type": "order_review_completed"}), 0)

    async def test_revision_barrier_also_fences_pre_read_legacy_replica_without_closure_lookup(self):
        detail = await self.display()
        payload = self.request(detail)
        binding = self.receipt(detail, payload)
        legacy_detail = await self.client.get("/order-reviews-v1/local-review?local_only=true")
        legacy_payload = {"expected_revision": legacy_detail.json()["revision"],
                          "approval_token": legacy_detail.json()["approval_token"]}
        entered, release = asyncio.Event(), asyncio.Event()
        real_owner = completion.operational_owner
        async def delay(db, owner, callback, **kwargs):
            if callback.__name__ == "finish_local":
                entered.set()
                await asyncio.wait_for(release.wait(), 5)
            return await real_owner(db, owner, callback, **kwargs)
        with patch.object(completion, "operational_owner", delay):
            task = asyncio.create_task(self.submit(legacy_payload))
            try:
                await asyncio.wait_for(entered.wait(), 5)
                self.assert_closed(await self.close(binding), binding)
            finally:
                release.set()
            response = await task
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["detail"]["code"], "component_source_event_stale")
        self.assertEqual(await self.db[completion.OPERATIONS].count_documents({"state": "completed"}), 0)

    async def test_completion_wins_before_closure_returns_original_commit_not_a_barrier(self):
        detail = await self.display()
        payload = self.request(detail)
        binding = self.receipt(detail, payload)
        entered, release = asyncio.Event(), asyncio.Event()
        real_owner = operational_atomic.operational_owner
        async def delay(db, owner, callback, **kwargs):
            if callback.__name__ == "close_owned":
                entered.set()
                await asyncio.wait_for(release.wait(), 5)
            return await real_owner(db, owner, callback, **kwargs)
        with patch.object(operational_atomic, "operational_owner", delay):
            task = asyncio.create_task(self.close(binding))
            try:
                await asyncio.wait_for(entered.wait(), 5)
                proof = await self.assert_commit(await self.submit(payload), detail, payload)
            finally:
                release.set()
            response = await task
        self.assertEqual(proof, await self.assert_commit(response, detail, payload))
        self.assertEqual(await self.db[completion.EVENTS].count_documents({"event_type": "order_review_attempt_closed"}), 0)

    async def test_concurrent_close_clicks_only_one_barrier_and_event(self):
        detail = await self.display()
        payload = self.request(detail)
        binding = self.receipt(detail, payload)
        responses = await asyncio.gather(self.close(binding), self.close(binding))
        self.assertTrue(any(r.status_code == 200 for r in responses), [r.text for r in responses])
        self.assertTrue(all(r.status_code in (200, 409, 503) for r in responses), [r.text for r in responses])
        proof = self.assert_closed(await self.reconcile(payload["client_request_id"]), binding)
        self.assertEqual(proof["barrier_revision"], detail["revision"] + 1)
        self.assertEqual(await self.db[completion.OPERATIONS].count_documents({}), 1)
        self.assertEqual(await self.db[completion.EVENTS].count_documents({"event_type": "order_review_attempt_closed"}), 1)

    async def test_unknown_prepared_legacy_or_orphaned_completion_evidence_never_closed(self):
        detail = await self.display()
        payload = self.request(detail)
        binding = self.receipt(detail, payload)
        for case in ("prepared", "legacy", "completed_event", "workflow_completed", "future_revision"):
            with self.subTest(case=case):
                if case in ("prepared", "legacy"):
                    row = {"_id": "unresolved", "user_id": "owner", "order_number": "local-review", "state": "prepared"}
                    if case == "prepared":
                        row.update(actor_id="owner", completion_mode="mezan_local_v1", readback_binding=binding)
                    await self.db[completion.OPERATIONS].insert_one(row)
                elif case == "completed_event":
                    await self.db[completion.EVENTS].insert_one({"_id": "orphan", "user_id": "owner",
                        "order_number": "local-review", "event_type": "order_review_completed"})
                elif case == "workflow_completed":
                    await self.db[completion.WORKFLOWS].update_one({"user_id": "owner", "order_number": "local-review"},
                        {"$set": {"stage": "reviewed", "revision": 1}}, upsert=True)
                candidate = {**binding, "approved_revision": 999} if case == "future_revision" else binding
                before = await self.business_state()
                self.assert_unknown(await self.close(candidate))
                self.assertEqual(await self.business_state(), before)
                self.assertEqual(await self.db[completion.OPERATIONS].count_documents({"state": "not_committed"}), 0)
                await self.db[completion.OPERATIONS].delete_many({})
                await self.db[completion.EVENTS].delete_many({"_id": "orphan"})
                await self.db[completion.WORKFLOWS].delete_many({})

    async def test_wrong_tenant_actor_order_session_or_permission_cannot_close(self):
        detail = await self.display()
        binding = self.receipt(detail, self.request(detail))
        for field in ("merchant_id", "actor_id", "order_number"):
            response = await self.close({**binding, field: "other"})
            self.assertEqual(response.status_code, 409, response.text)
        self.assert_unknown(await self.close({**binding, "origin_session_ref": "a" * 43}))
        self.actor = {"id": "employee", "role": "employee", "created_by": "owner"}
        self.assertEqual((await self.close(binding)).status_code, 403)
        self.assertEqual(await self.db[completion.OPERATIONS].count_documents({}), 0)

    async def test_exact_receipt_from_prior_login_can_be_closed_without_reusing_old_consent(self):
        detail = await self.display()
        payload = self.request(detail)
        binding = self.receipt(detail, payload)
        await sessions.revoke_review_session(self.db, self.actor)
        await self.fresh_family()
        self.assert_closed(await self.close(binding), binding)
        response = await self.submit(payload)
        self.assertEqual(response.status_code, 409, response.text)
        fresh = await self.display()
        fresh_payload = self.request(fresh)
        await self.assert_commit(await self.submit(fresh_payload), fresh, fresh_payload)

    async def test_closure_binding_mismatch_malformed_body_and_incomplete_proof_fail_closed(self):
        detail = await self.display()
        payload = self.request(detail)
        binding = self.receipt(detail, payload)
        for changed in ({"contract_version": True}, {"approved_revision": "0"},
                        {"approval_token_sha256": "bad"}, {"extra": 1}, {"client_request_id": "a" * 36}):
            self.assertEqual((await self.close({**binding, **changed})).status_code, 422)
        proof = self.assert_closed(await self.close(binding), binding)
        self.assertEqual((await self.close({**binding, "approval_token_sha256": "0" * 64})).status_code, 409)
        await self.db[completion.EVENTS].delete_one({"_id": proof["event_id"]})
        self.assert_unknown(await self.reconcile(payload["client_request_id"]), 503)
        self.assert_unknown(await self.close(binding), 503)

    async def install_controls(self):
        async def actor():
            return self.actor
        self.app.include_router(controls.make_order_review_export_controls_router(self.db, actor))
        result = await self.client.get("/order-review-export-controls-v1/local-review")
        self.assertEqual(result.status_code, 200, result.text)
        return result.json()["items"][0]["order_item_id"]

    async def test_hide_unhide_direct_route_preserve_data_advance_revision_and_recover_stuck_receipt(self):
        item_id = await self.install_controls()
        await self.db[completion.WORKFLOWS].insert_one({"user_id": "owner", "order_number": "local-review",
            "stage": "pending_review", "revision": 0, "items": [{"order_item_id": item_id,
            "internal_note": "synthetic retained draft", "preparation_note": "synthetic retained note",
            "selected_image_url": "https://example.invalid/synthetic.png",
            "manual_supplier_export_excluded_spec_keys": ["old hidden"],
            "supplier_export_excluded_spec_keys": ["old hidden"]}], "operational_items": []})
        detail = await self.display()
        stale_payload = self.request(detail)
        binding = self.receipt(detail, stale_payload)
        before = await self.business_state()
        for values, expected_keys in (
            ({"manual_hidden_spec_keys": ["old hidden", "new hidden"]}, ["new hidden", "old hidden"]),
            ({"manual_hidden_spec_keys": ["old hidden"]}, ["old hidden"]),
            ({"preparation_route": "direct_assembly", "save_assignment_as_default": False}, ["old hidden"]),
            ({"preparation_route": "supplier_file", "save_assignment_as_default": False}, ["old hidden"]),
        ):
            prior = await self.display()
            response = await self.client.patch(f"/order-review-export-controls-v1/local-review/items/{item_id}", json=values)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["manual_hidden_spec_keys"], expected_keys)
            fresh = await self.display()
            self.assertEqual(fresh["revision"], prior["revision"] + 1)
            self.assertNotEqual(fresh["approval_fingerprint"], prior["approval_fingerprint"])
            workflow = await self.db[completion.WORKFLOWS].find_one({"order_number": "local-review"})
            self.assertEqual(workflow["items"][0]["internal_note"], "synthetic retained draft")
            self.assertEqual(workflow["items"][0]["preparation_note"], "synthetic retained note")
            self.assertEqual(workflow["items"][0]["selected_image_url"], "https://example.invalid/synthetic.png")
            self.assertEqual(await self.business_state(), before)
        rejected = await self.submit(stale_payload)
        self.assertEqual(rejected.status_code, 409, rejected.text)
        self.assert_unknown(await self.reconcile(stale_payload["client_request_id"]))
        self.assert_closed(await self.close(binding), binding)
        fresh = await self.display()
        fresh_payload = self.request(fresh)
        await self.assert_commit(await self.submit(fresh_payload), fresh, fresh_payload)
        await self.assert_commit(await self.submit(fresh_payload), fresh, fresh_payload)

    async def test_export_control_first_write_creates_revision_one_and_invalidates_initial_approval(self):
        item_id = await self.install_controls()
        detail = await self.display()
        result = await self.client.patch(f"/order-review-export-controls-v1/local-review/items/{item_id}",
            json={"manual_hidden_spec_keys": ["synthetic key"]})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual((await self.display())["revision"], 1)
        self.assertEqual((await self.submit(self.request(detail))).status_code, 409)

    async def test_first_hide_then_fresh_display_can_complete_without_recovery(self):
        item_id = await self.install_controls()
        self.assertIsNone(await self.db[completion.WORKFLOWS].find_one({"order_number": "local-review"}))
        response = await self.client.patch(f"/order-review-export-controls-v1/local-review/items/{item_id}",
            json={"manual_hidden_spec_keys": ["size"]})
        self.assertEqual(response.status_code, 200, response.text)
        detail = await self.display()
        payload = self.request(detail)
        await self.assert_commit(await self.submit(payload), detail, payload)
        workflow = await self.db[completion.WORKFLOWS].find_one({"order_number": "local-review"})
        self.assertEqual(workflow["items"][0]["manual_supplier_export_excluded_spec_keys"], ["size"])

    async def test_hide_real_customer_option_after_intake_can_complete_once(self):
        number = "options-review"
        payload = self.source_payload(number=number)
        payload["items"][0]["options"] = [
            {"id": "synthetic-size", "name": "المقاس", "value": "M"},
            {"id": "synthetic-name", "name": "الاسم", "value": "Synthetic"},
        ]
        self.assertTrue((await self.webhook(payload))["synced"])
        await self.install_controls()
        before = await self.display(number)
        item_id = before["items"][0]["order_item_id"]
        self.assertTrue(before["items"][0]["options"])
        response = await self.client.patch(f"/order-review-export-controls-v1/{number}/items/{item_id}",
            json={"manual_hidden_spec_keys": ["size"]})
        self.assertEqual(response.status_code, 200, response.text)
        detail = await self.display(number)
        request = self.request(detail)
        await self.assert_commit(await self.submit(request, number), detail, request)
        await self.assert_commit(await self.submit(request, number), detail, request)
        workflow = await self.db[completion.WORKFLOWS].find_one({"order_number": number})
        self.assertEqual(workflow["items"][0]["manual_supplier_export_excluded_spec_keys"], ["size"])
        self.assertEqual(await self.db[completion.EVENTS].count_documents({"event_type": "order_review_completed"}), 1)

    async def test_export_hide_during_pre_read_completion_preserves_new_choice_and_prevents_commit(self):
        item_id = await self.install_controls()
        detail = await self.display()
        payload = self.request(detail)
        real_owner = completion.operational_owner
        changed = False
        async def hide_before_claim(db, owner, callback, **kwargs):
            nonlocal changed
            if callback.__name__ == "finish_local" and not changed:
                changed = True
                response = await self.client.patch(f"/order-review-export-controls-v1/local-review/items/{item_id}",
                    json={"manual_hidden_spec_keys": ["new choice"]})
                self.assertEqual(response.status_code, 200, response.text)
            return await real_owner(db, owner, callback, **kwargs)
        with patch.object(completion, "operational_owner", hide_before_claim):
            response = await self.submit(payload)
        self.assertTrue(changed)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(await self.db[completion.OPERATIONS].count_documents({"state": "completed"}), 0)
        workflow = await self.db[completion.WORKFLOWS].find_one({"order_number": "local-review"})
        self.assertEqual(workflow["items"][0]["manual_supplier_export_excluded_spec_keys"], ["new choice"])


if __name__ == "__main__":
    unittest.main()
