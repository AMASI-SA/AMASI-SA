"""Readback V2 acceptance at the real-Mongo ASGI boundary.

The existing local-completion fixture permits only its ASGI HTTP client and
fails on provider calls. Authentication is an explicit current-user fixture;
session authority, completion transactions, operations and events are real
Mongo records. No mongomock, production fallback, worker or POST replay client
is used. Fault-injection tests describe injected errors, not a live failover.
"""
import asyncio
import ast
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import time
import unittest
from unittest.mock import patch
from uuid import uuid1, uuid4

from pymongo.errors import AutoReconnect, OperationFailure

import order_review_approval as approval
import order_review_completion as completion
import review_completion_readback as readback
import review_session_fence as sessions
import test_review_local_completion as local
from review_acceptance_config_guard import (
    AcceptanceConfigDatabase, FENCES, ensure_acceptance_config_storage,
)


class ReviewReadbackV2Tests(unittest.IsolatedAsyncioTestCase):
    asyncTearDown = local.LocalCompletionTests.asyncTearDown
    source_payload = local.LocalCompletionTests.source_payload
    webhook = local.LocalCompletionTests.webhook
    saved = local.LocalCompletionTests.saved
    assert_no_completion = local.LocalCompletionTests.assert_no_completion

    async def asyncSetUp(self):
        await local.LocalCompletionTests.asyncSetUp(self)
        await ensure_acceptance_config_storage(self.db)
        await sessions.ensure_review_session_storage(self.db)
        # The router-only ASGI fixture does not execute server startup. Invoke
        # the actual required bootstrap helper, outside all business transactions.
        await readback.ensure_readback_storage(self.db)
        self.actor["_review_session"] = await sessions.create_review_session(self.db, self.actor)
        self.origin_actor = deepcopy(self.actor)
        self.sent_posts = 0
        self.route_prefix = ""

    async def display(self, order_number="local-review"):
        response = await self.client.get(f"{self.route_prefix}/order-reviews-v1/{order_number}", params={
            "local_only": "true", "readback_contract_version": 2,
        })
        self.assertEqual(response.status_code, 200, response.text)
        detail = response.json()
        context = detail["approval_context"]
        self.assertEqual(context["contract_version"], 2)
        self.assertEqual(context["merchant_id"], "owner")
        self.assertEqual(context["actor_id"], self.actor["id"])
        self.assertEqual(context["order_number"], order_number)
        self.assertEqual(context["approved_revision"], detail["revision"])
        self.assertEqual(context["approval_fingerprint"], detail["approval_fingerprint"])
        self.assertEqual(context["origin_session_ref"], self.actor["_review_session"]["origin_session_ref"])
        self.assertEqual(context["origin_session_epoch"], self.actor["_review_session"]["origin_session_epoch"])
        self.assertNotIn("client_request_id", context)
        self.assertIsInstance(detail["approval_token"], str)
        return detail

    def request(self, detail, request_id=None):
        return {
            "readback_contract_version": 2,
            "client_request_id": request_id or str(uuid4()),
            "expected_revision": detail["revision"],
            "approval_token": detail["approval_token"],
        }

    async def submit(self, payload, order_number="local-review"):
        self.sent_posts += 1
        return await self.client.post(f"{self.route_prefix}/order-reviews-v1/{order_number}/complete", json=payload)

    async def reconcile(self, request_id, order_number="local-review"):
        return await self.client.get(f"{self.route_prefix}/order-reviews-v1/{order_number}/completion-operation", params={
            "readback_contract_version": 2, "client_request_id": request_id,
        })

    async def state(self, *, all_collections=False):
        names = await self.db.list_collection_names() if all_collections else (
            completion.OPERATIONS, completion.WORKFLOWS, completion.EVENTS,
            local.fixture.PLANS, local.fixture.UNITS, local.fixture.LOCATIONS,
            local.fulfillment.COMPONENT_LIFECYCLES,
            "mezan_fulfillment_decisions_v2", "unified_orders",
        )
        # Empty namespace creation does not change data. All nonempty records,
        # including owner/session fences for read-only assertions, are compared.
        result = {}
        for name in sorted(names):
            rows = await self.db[name].find({}).sort("_id", 1).to_list(10000)
            if rows:
                result[name] = rows
        return result

    def assert_unknown(self, response, status=202, code=None):
        self.assertEqual(response.status_code, status, response.text)
        body = response.json()
        # FastAPI errors may carry the same typed UNKNOWN contract in detail.
        value = body.get("detail", body)
        self.assertEqual(value["state"], "unknown", body)
        self.assertIs(value["retry_post"], False, body)
        self.assertEqual(value["reconcile"], "get_only", body)
        self.assertNotEqual(value.get("commit_confirmed"), True, body)
        self.assertIsNone(value.get("operation"), body)
        if code:
            self.assertEqual(value["code"], code, body)
        self.assertNotIn("not_committed", str(body).lower())
        self.assertNotIn("failed", str(body).lower())

    def assert_binding(self, proof, detail, payload):
        expected = {
            "contract_version": 2,
            "client_request_id": payload["client_request_id"],
            "merchant_id": "owner",
            "actor_id": detail["approval_context"]["actor_id"],
            "origin_session_ref": detail["approval_context"]["origin_session_ref"],
            "origin_session_epoch": detail["approval_context"]["origin_session_epoch"],
            "order_number": detail["approval_context"]["order_number"],
            "approved_revision": detail["revision"],
            "approval_fingerprint": detail["approval_fingerprint"],
            "approval_token_sha256": hashlib.sha256(payload["approval_token"].encode()).hexdigest(),
        }
        for key, value in expected.items():
            self.assertEqual(proof[key], value, key)
        return expected

    async def assert_commit(self, response, detail, payload):
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        proof = body["operation"]
        binding = self.assert_binding(proof, detail, payload)
        self.assertIs(proof["commit_confirmed"], True)
        self.assertEqual(proof["state"], "completed")
        self.assertEqual(proof["completion_mode"], "mezan_local_v1")
        self.assertEqual(proof["salla_status_sync"], "not_requested")
        self.assertEqual(proof["event_type"], "order_review_completed")
        self.assertEqual(proof["committed_revision"], detail["revision"] + 1)
        self.assertTrue(proof["completed_at"])
        self.assertNotEqual(proof["operation_id"], payload["client_request_id"])
        operation = await self.db[completion.OPERATIONS].find_one({"_id": proof["operation_id"]})
        event = await self.db[completion.EVENTS].find_one({"_id": proof["event_id"]})
        self.assertIsNotNone(operation)
        self.assertIsNotNone(event)
        self.assertEqual(operation["readback_binding"], binding)
        self.assertEqual(event["readback_binding"], binding)
        self.assertEqual(event["operation_id"], operation["_id"])
        self.assertEqual(event["user_id"], "owner")
        self.assertEqual(event["actor_id"], binding["actor_id"])
        self.assertEqual(event["order_number"], binding["order_number"])
        self.assertEqual(event["event_type"], "order_review_completed")
        self.assertEqual(operation["committed_revision"], proof["committed_revision"])
        self.assertEqual(event["committed_revision"], proof["committed_revision"])
        self.assertEqual(operation["completed_at"], proof["completed_at"])
        self.assertEqual(event["occurred_at"], proof["completed_at"])
        workflow = await self.db[completion.WORKFLOWS].find_one({
            "user_id": "owner", "order_number": binding["order_number"],
        })
        self.assertEqual(body["current_revision"], workflow["revision"])
        self.assertEqual(body["current_operation_id"], workflow["review_completion_operation_id"])
        self.assertEqual(await self.db[completion.EVENTS].count_documents({
            "operation_id": operation["_id"], "event_type": "order_review_completed",
        }), 1)
        self.external.assert_not_awaited()
        return proof

    async def assert_rejected(self, payload, *, order_number="local-review", code=None, status=409):
        before = await self.state()
        response = await self.submit(payload, order_number)
        self.assertEqual(response.status_code, status, response.text)
        if code:
            self.assertEqual(response.json()["detail"]["code"], code, response.text)
        self.assertEqual(await self.state(), before)
        self.external.assert_not_awaited()
        return response

    async def fresh_family(self, actor=None):
        self.actor = deepcopy(actor if actor is not None else self.origin_actor)
        self.actor.pop("_review_session", None)
        self.actor["_review_session"] = await sessions.create_review_session(self.db, self.actor)

    async def test_success_commits_exact_binding_event_and_proof_atomically(self):
        detail = await self.display()
        payload = self.request(detail)
        await self.assert_commit(await self.submit(payload), detail, payload)
        self.assertEqual(await self.db[completion.OPERATIONS].count_documents({}), 1)
        self.assertEqual(await self.db[completion.WORKFLOWS].count_documents({"stage": "reviewed"}), 1)
        self.assertEqual(self.sent_posts, 1)

    async def test_actual_bootstrap_installs_tenant_attempt_partial_unique_index(self):
        indexes = await self.db[completion.OPERATIONS].index_information()
        candidates = [value for value in indexes.values()
                      if value.get("key") == [("user_id", 1), ("readback_binding.client_request_id", 1)]]
        self.assertEqual(len(candidates), 1, indexes)
        self.assertIs(candidates[0].get("unique"), True)
        self.assertEqual(candidates[0].get("partialFilterExpression"),
                         {"readback_binding.client_request_id": {"$type": "string"}})

    async def test_lost_ack_resolves_by_get_only_with_no_database_writes(self):
        detail = await self.display()
        payload = self.request(detail)
        await self.submit(payload)  # The client discards the HTTP reply.
        before = await self.state(all_collections=True)
        proofs = []
        for _ in range(3):
            response = await self.reconcile(payload["client_request_id"])
            self.assertEqual(response.headers.get("cache-control"), "no-store")
            proofs.append(await self.assert_commit(response, detail, payload))
        self.assertEqual(proofs[0], proofs[1])
        self.assertEqual(proofs[1], proofs[2])
        self.assertEqual(await self.state(all_collections=True), before)
        self.assertEqual(self.sent_posts, 1)

    async def test_unobserved_attempt_remains_unknown_and_can_arrive_later(self):
        detail = await self.display()
        payload = self.request(detail)
        before = await self.state(all_collections=True)
        for _ in range(2):
            response = await self.reconcile(payload["client_request_id"])
            self.assert_unknown(response)
            self.assertEqual(response.headers.get("cache-control"), "no-store")
        self.assertEqual(await self.state(all_collections=True), before)
        self.assertEqual(self.sent_posts, 0)
        # One delayed ORIGINAL delivery, not a client retry after a sent POST.
        await self.assert_commit(await self.submit(payload), detail, payload)
        self.assertEqual(self.sent_posts, 1)

    async def test_other_attempt_cannot_adopt_old_completed_operation(self):
        detail = await self.display()
        first = self.request(detail)
        proof = await self.assert_commit(await self.submit(first), detail, first)
        other = self.request(detail)
        before = await self.state(all_collections=True)
        self.assert_unknown(await self.reconcile(other["client_request_id"]))
        self.assertEqual(await self.state(all_collections=True), before)
        await self.assert_rejected(other, code="review_completion_attempt_conflict")
        again = await self.assert_commit(await self.reconcile(first["client_request_id"]), detail, first)
        self.assertEqual(again, proof)

    async def test_duplicate_same_binding_returns_same_proof_without_new_effects(self):
        detail = await self.display()
        payload = self.request(detail)
        first = await self.assert_commit(await self.submit(payload), detail, payload)
        before = await self.state()
        second = await self.assert_commit(await self.submit(payload), detail, payload)
        self.assertEqual(first, second)
        self.assertEqual(await self.state(), before)
        self.assertEqual(await self.db[completion.OPERATIONS].count_documents({}), 1)

    async def test_concurrent_same_attempt_has_only_one_atomic_completion(self):
        detail = await self.display()
        payload = self.request(detail)
        responses = await asyncio.gather(self.submit(payload), self.submit(payload))
        self.assertTrue(any(row.status_code == 200 for row in responses), [row.text for row in responses])
        self.assertTrue(all(row.status_code in (200, 409) for row in responses), [row.text for row in responses])
        await self.assert_commit(await self.reconcile(payload["client_request_id"]), detail, payload)
        self.assertEqual(await self.db[completion.OPERATIONS].count_documents({}), 1)
        self.assertEqual(await self.db[completion.EVENTS].count_documents({"event_type": "order_review_completed"}), 1)

    async def test_concurrent_completed_fast_return_must_revalidate_actual_event(self):
        detail = await self.display()
        payload = self.request(detail)
        actual = completion.operational_owner
        injected = False
        after_event_loss = None

        async def commit_other_delivery_before_claim(db, user_id, callback, **kwargs):
            nonlocal injected, after_event_loss
            if callback.__name__ == "finish_local" and not injected:
                # Both route-level exact-attempt lookups can observe absence.
                # Commit the second delivery before the first opens its owner
                # transaction, forcing the transaction's completed fast return.
                injected = True
                inner = await self.assert_commit(await self.submit(payload), detail, payload)
                await self.db[completion.EVENTS].delete_one({"_id": inner["event_id"]})
                after_event_loss = await self.state()
            return await actual(db, user_id, callback, **kwargs)

        with patch.object(completion, "operational_owner", commit_other_delivery_before_claim):
            response = await self.submit(payload)
        self.assertTrue(injected)
        self.assert_unknown(response, 503, "review_completion_evidence_incomplete")
        self.assertEqual(await self.state(), after_event_loss)
        self.assertEqual(await self.db[completion.OPERATIONS].count_documents({}), 1)

    async def test_concurrent_completed_fast_return_reports_current_pointer_separately(self):
        detail = await self.display()
        payload = self.request(detail)
        actual = completion.operational_owner
        injected = False
        inner_proof = None

        async def advance_workflow_before_claim(db, user_id, callback, **kwargs):
            nonlocal injected, inner_proof
            if callback.__name__ == "finish_local" and not injected:
                injected = True
                inner_proof = await self.assert_commit(await self.submit(payload), detail, payload)
                await self.db[completion.WORKFLOWS].update_one({"user_id": "owner", "order_number": "local-review"},
                    {"$set": {"revision": inner_proof["committed_revision"] + 10,
                              "review_completion_operation_id": "newer-operation-pointer"}})
            return await actual(db, user_id, callback, **kwargs)

        with patch.object(completion, "operational_owner", advance_workflow_before_claim):
            response = await self.submit(payload)
        self.assertTrue(injected)
        self.assertEqual(await self.assert_commit(response, detail, payload), inner_proof)
        self.assertEqual(response.json()["current_revision"], inner_proof["committed_revision"] + 10)
        self.assertEqual(response.json()["current_operation_id"], "newer-operation-pointer")

    async def test_same_request_id_wrong_revision_rejected_at_completed_fast_return(self):
        detail = await self.display()
        payload = self.request(detail)
        await self.assert_commit(await self.submit(payload), detail, payload)
        await self.assert_rejected({**payload, "expected_revision": payload["expected_revision"] + 1},
                                   code="readback_binding_conflict")

    async def test_same_request_id_different_valid_token_hash_cannot_reuse_commit(self):
        detail = await self.display()
        with patch.object(approval.time, "time", return_value=int(time.time()) + 10):
            second_display = await self.display()
        self.assertEqual(second_display["approval_fingerprint"], detail["approval_fingerprint"])
        self.assertNotEqual(second_display["approval_token"], detail["approval_token"])
        payload = self.request(detail)
        await self.assert_commit(await self.submit(payload), detail, payload)
        await self.assert_rejected({**payload, "approval_token": second_display["approval_token"]},
                                   code="readback_binding_conflict")

    async def test_same_request_id_other_order_conflicts_and_get_does_not_disclose(self):
        other_payload = self.source_payload(number="other-review")
        self.assertTrue((await self.webhook(other_payload))["synced"])
        first_detail = await self.display()
        other_detail = await self.display("other-review")
        payload = self.request(first_detail)
        await self.assert_commit(await self.submit(payload), first_detail, payload)
        before = await self.state(all_collections=True)
        self.assert_unknown(await self.reconcile(payload["client_request_id"], "other-review"))
        self.assertEqual(await self.state(all_collections=True), before)
        await self.assert_rejected(self.request(other_detail, payload["client_request_id"]),
                                   order_number="other-review", code="readback_binding_conflict")

    async def test_same_merchant_different_actor_cannot_adopt_or_read_original_attempt(self):
        detail = await self.display()
        payload = self.request(detail)
        await self.assert_commit(await self.submit(payload), detail, payload)
        await self.fresh_family({"id": "other-reviewer", "role": "viewer", "created_by": "owner",
                                 "extra_permissions": ["orders.manage"], "name": "Other synthetic reviewer"})
        before = await self.state(all_collections=True)
        self.assert_unknown(await self.reconcile(payload["client_request_id"]))
        self.assertEqual(await self.state(all_collections=True), before)
        await self.assert_rejected(payload, code="readback_binding_conflict")

    async def test_new_session_can_read_history_but_cannot_reuse_original_consent(self):
        detail = await self.display()
        payload = self.request(detail)
        original = await self.assert_commit(await self.submit(payload), detail, payload)
        revoked = await sessions.revoke_review_session(self.db, self.actor)
        self.assertIs(revoked["server_revocation_confirmed"], True)
        await self.fresh_family()
        self.assertNotEqual(self.actor["_review_session"]["origin_session_ref"], original["origin_session_ref"])
        before = await self.state(all_collections=True)
        historical = await self.assert_commit(await self.reconcile(payload["client_request_id"]), detail, payload)
        self.assertEqual(historical, original)
        self.assertEqual(await self.state(all_collections=True), before)
        await self.assert_rejected(payload, code="readback_binding_conflict")

    async def test_other_merchant_cannot_read_original_attempt(self):
        detail = await self.display()
        payload = self.request(detail)
        await self.assert_commit(await self.submit(payload), detail, payload)
        await self.fresh_family({"id": "other-merchant", "role": "owner", "name": "Other synthetic merchant"})
        before = await self.state(all_collections=True)
        self.assert_unknown(await self.reconcile(payload["client_request_id"]))
        self.assertEqual(await self.state(all_collections=True), before)

    async def test_current_review_permission_is_required_for_historical_get(self):
        detail = await self.display()
        payload = self.request(detail)
        await self.assert_commit(await self.submit(payload), detail, payload)
        self.actor.update(role="viewer", created_by="owner", extra_permissions=[], denied_permissions=[])
        before = await self.state(all_collections=True)
        response = await self.reconcile(payload["client_request_id"])
        self.assertEqual(response.status_code, 403, response.text)
        self.assertEqual(await self.state(all_collections=True), before)

    async def test_revoked_original_session_cannot_query_even_its_committed_history(self):
        detail = await self.display()
        payload = self.request(detail)
        await self.assert_commit(await self.submit(payload), detail, payload)
        await sessions.revoke_review_session(self.db, self.actor)
        before = await self.state(all_collections=True)
        response = await self.reconcile(payload["client_request_id"])
        self.assertEqual(response.status_code, 401, response.text)
        self.assertEqual(await self.state(all_collections=True), before)

    async def test_invalid_or_non_v4_request_id_cannot_enter_v2(self):
        detail = await self.display()
        payload = self.request(detail)
        for invalid in ("", "not-a-uuid", str(uuid1())):
            with self.subTest(request_id=invalid):
                await self.assert_rejected({**payload, "client_request_id": invalid}, status=422)
                response = await self.reconcile(invalid)
                self.assertEqual(response.status_code, 422, response.text)
        await self.assert_no_completion()

    async def test_v2_requires_server_session_and_cannot_silently_downgrade(self):
        detail = await self.display()
        payload = self.request(detail)
        self.actor.pop("_review_session")
        response = await self.client.get("/order-reviews-v1/local-review", params={
            "local_only": "true", "readback_contract_version": 2,
        })
        self.assertEqual(response.status_code, 409, response.text)
        self.assertNotIn("approval_token", response.json())
        await self.assert_rejected(payload, code="review_session_reauthentication_required")
        await self.assert_no_completion()

    async def test_v1_token_cannot_authorize_v2_and_v2_token_cannot_authorize_v1(self):
        v2 = await self.display()
        legacy = await self.client.get("/order-reviews-v1/local-review?local_only=true")
        self.assertEqual(legacy.status_code, 200, legacy.text)
        await self.assert_rejected({**self.request(v2), "approval_token": legacy.json()["approval_token"]})
        await self.assert_rejected({"expected_revision": v2["revision"], "approval_token": v2["approval_token"]})
        await self.assert_no_completion()

    async def test_post_cannot_supply_new_material_fields(self):
        detail = await self.display()
        payload = self.request(detail)
        for key, value in (("items", []), ("operational_items", []),
                           ("approval_fingerprint", "0" * 64), ("origin_session_ref", "client-chosen")):
            with self.subTest(field=key):
                await self.assert_rejected({**payload, key: value}, status=422)
        await self.assert_no_completion()

    async def test_missing_or_forged_approval_rejects_without_effects(self):
        detail = await self.display()
        payload = self.request(detail)
        forged = payload["approval_token"][:-1] + ("0" if payload["approval_token"][-1] != "0" else "1")
        for candidate in (None, "", forged):
            with self.subTest(case="missing" if not candidate else "forged"):
                changed = {**payload, "approval_token": candidate}
                if candidate is None:
                    changed.pop("approval_token")
                await self.assert_rejected(changed)
        await self.assert_no_completion()

    async def test_expired_display_rejects_post_but_does_not_erase_historical_get(self):
        detail = await self.display()
        payload = self.request(detail)
        proof = await self.assert_commit(await self.submit(payload), detail, payload)
        with patch.object(approval.time, "time", return_value=int(time.time()) + approval.TTL_SECONDS + 1):
            # A completed fast return must not turn an expired token into consent.
            await self.assert_rejected(payload)
            before = await self.state(all_collections=True)
            historical = await self.assert_commit(await self.reconcile(payload["client_request_id"]), detail, payload)
            self.assertEqual(historical, proof)
            self.assertEqual(await self.state(all_collections=True), before)

    async def test_expired_display_never_committed_remains_unknown(self):
        detail = await self.display()
        payload = self.request(detail)
        with patch.object(approval.time, "time", return_value=int(time.time()) + approval.TTL_SECONDS + 1):
            await self.assert_rejected(payload)
        self.assert_unknown(await self.reconcile(payload["client_request_id"]))
        await self.assert_no_completion()

    async def test_wrong_order_token_and_wrong_display_revision_do_not_commit(self):
        self.assertTrue((await self.webhook(self.source_payload(number="other-review")))["synced"])
        detail = await self.display()
        payload = self.request(detail)
        await self.assert_rejected(payload, order_number="other-review")
        await self.assert_rejected({**payload, "expected_revision": detail["revision"] + 1})
        await self.assert_no_completion()

    async def test_missing_event_is_unknown_and_does_not_trust_stored_success(self):
        detail = await self.display()
        payload = self.request(detail)
        proof = await self.assert_commit(await self.submit(payload), detail, payload)
        await self.db[completion.EVENTS].delete_one({"_id": proof["event_id"]})
        before = await self.state(all_collections=True)
        response = await self.reconcile(payload["client_request_id"])
        self.assert_unknown(response, 503, "review_completion_evidence_incomplete")
        self.assertEqual(response.headers.get("cache-control"), "no-store")
        self.assertEqual(await self.state(all_collections=True), before)

    async def test_wrong_event_binding_or_relationship_is_never_completed(self):
        detail = await self.display()
        payload = self.request(detail)
        proof = await self.assert_commit(await self.submit(payload), detail, payload)
        event = await self.db[completion.EVENTS].find_one({"_id": proof["event_id"]})
        changes = (
            ("operation_id", "different-operation"), ("event_type", "other_event"),
            ("actor_id", "other-reviewer"), ("user_id", "other-merchant"),
            ("order_number", "other-order"), ("committed_revision", proof["committed_revision"] + 1),
            ("readback_binding.client_request_id", str(uuid4())),
            ("readback_binding.approval_fingerprint", "0" * 64),
            ("readback_binding.approval_token_sha256", "1" * 64),
        )
        for field, value in changes:
            with self.subTest(field=field):
                await self.db[completion.EVENTS].replace_one({"_id": event["_id"]}, deepcopy(event))
                await self.db[completion.EVENTS].update_one({"_id": event["_id"]}, {"$set": {field: value}})
                before = await self.state(all_collections=True)
                self.assert_unknown(await self.reconcile(payload["client_request_id"]),
                                    503, "review_completion_evidence_incomplete")
                self.assertEqual(await self.state(all_collections=True), before)

    async def test_operation_and_event_revision_disagreement_is_unknown(self):
        detail = await self.display()
        payload = self.request(detail)
        proof = await self.assert_commit(await self.submit(payload), detail, payload)
        await self.db[completion.OPERATIONS].update_one({"_id": proof["operation_id"]},
            {"$set": {"committed_revision": proof["committed_revision"] + 1}})
        before = await self.state(all_collections=True)
        self.assert_unknown(await self.reconcile(payload["client_request_id"]),
                            503, "review_completion_evidence_incomplete")
        self.assertEqual(await self.state(all_collections=True), before)

    async def test_historical_committed_revision_is_separate_from_current_workflow(self):
        detail = await self.display()
        payload = self.request(detail)
        original = await self.assert_commit(await self.submit(payload), detail, payload)
        await self.db[completion.WORKFLOWS].update_one({"user_id": "owner", "order_number": "local-review"},
            {"$set": {"revision": original["committed_revision"] + 10,
                      "review_completion_operation_id": "newer-operation-pointer"}})
        before = await self.state(all_collections=True)
        response = await self.reconcile(payload["client_request_id"])
        historical = await self.assert_commit(response, detail, payload)
        self.assertEqual(historical, original)
        self.assertEqual(response.json()["current_revision"], original["committed_revision"] + 10)
        self.assertEqual(response.json()["current_operation_id"], "newer-operation-pointer")
        self.assertEqual(await self.state(all_collections=True), before)
        self.assert_unknown(await self.reconcile(str(uuid4())))

    async def test_material_product_options_quantity_changes_reject_original_consent(self):
        detail = await self.display()
        original = await self.db.unified_orders.find_one({"user_id": "owner", "order_number": "local-review"})
        for path, value in (
            ("items.0.product_id", "unapproved-product"),
            ("items.0.options", [{"id": "size", "name": "Size", "value": "60 inch"}]),
            ("items.0.quantity", 3),
        ):
            with self.subTest(path=path):
                await self.db.unified_orders.replace_one({"_id": original["_id"]}, deepcopy(original))
                await self.db.unified_orders.update_one({"_id": original["_id"]},
                    {"$set": {"raw_by_source.salla_direct." + path: value}})
                await self.assert_rejected(self.request(detail))
                await self.assert_no_completion()

    async def test_guarded_recipe_write_after_snapshot_cannot_commit_stale_consent(self):
        await self.db[FENCES].update_one({"_id": "owner"}, {"$setOnInsert": {
            "user_id": "owner", "version": 0, "fence": 0,
        }}, upsert=True)
        detail = await self.display()
        payload = self.request(detail)
        actual = completion.acceptance_snapshot
        injected = False

        async def change_after_snapshot(scoped, **kwargs):
            nonlocal injected
            result = await actual(scoped, **kwargs)
            if not injected:
                injected = True
                await AcceptanceConfigDatabase(self.db)[local.fixture.PRODUCT_BINDINGS].update_one(
                    {"user_id": "owner", "id": "recipe-material"}, {"$set": {"quantity": 3}})
            return result

        before = await self.state()
        with patch.object(completion, "acceptance_snapshot", change_after_snapshot):
            response = await self.submit(payload)
        self.assertTrue(injected)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(await self.state(), before)
        self.assertGreaterEqual((await self.db[FENCES].find_one({"_id": "owner"}))["version"], 1)
        await self.assert_no_completion()

    async def test_injected_commit112_is_typed_unknown_without_post_replay(self):
        detail = await self.display()
        payload = self.request(detail)
        actual = completion.operational_owner
        invocations = 0

        async def injected_commit_error(db, user_id, callback, **kwargs):
            nonlocal invocations
            if callback.__name__ == "finish_local":
                invocations += 1
                raise OperationFailure("synthetic commit conflict", code=112,
                                       details={"errorLabels": ["TransientTransactionError"]})
            return await actual(db, user_id, callback, **kwargs)

        before = await self.state()
        with patch.object(completion, "operational_owner", injected_commit_error):
            response = await self.submit(payload)
        self.assert_unknown(response, 503)
        self.assertEqual(invocations, 1)
        self.assertEqual(self.sent_posts, 1)
        self.assertEqual(await self.state(), before)
        self.assert_unknown(await self.reconcile(payload["client_request_id"]))
        self.assertEqual(self.sent_posts, 1)
        await self.assert_no_completion()

    async def test_actual_event_write_failure_rolls_back_all_completion_and_session_effects(self):
        detail = await self.display()
        payload = self.request(detail)
        if completion.EVENTS not in await self.db.list_collection_names():
            await self.db.create_collection(completion.EVENTS)
        await self.db.command({"collMod": completion.EVENTS,
            "validator": {"event_type": {"$ne": "order_review_completed"}}, "validationLevel": "strict"})
        before = await self.state(all_collections=True)
        response = await self.submit(payload)
        self.assert_unknown(response, 503)
        self.assertEqual(await self.state(all_collections=True), before)
        self.assert_unknown(await self.reconcile(payload["client_request_id"]))
        self.assertEqual(self.sent_posts, 1)
        await self.assert_no_completion()

    async def test_database_read_uncertainty_is_503_unknown_and_read_only(self):
        request_id = str(uuid4())
        before = await self.state(all_collections=True)
        with patch.object(readback, "read_attempt", side_effect=AutoReconnect("synthetic read failure")):
            response = await self.reconcile(request_id)
        self.assert_unknown(response, 503)
        self.assertEqual(response.headers.get("cache-control"), "no-store")
        self.assertEqual(await self.state(all_collections=True), before)
        self.assertEqual(self.sent_posts, 0)

    async def test_post_session_authority_database_uncertainty_is_typed_unknown(self):
        detail = await self.display()
        payload = self.request(detail)
        before = await self.state(all_collections=True)
        with patch.object(sessions, "review_session_context", side_effect=AutoReconnect("synthetic authority read failure")):
            response = await self.submit(payload)
        self.assert_unknown(response, 503)
        self.assertEqual(await self.state(all_collections=True), before)
        self.assertEqual(self.sent_posts, 1)
        self.assert_unknown(await self.reconcile(payload["client_request_id"]))

    async def test_post_preflight_database_uncertainty_is_typed_unknown(self):
        detail = await self.display()
        payload = self.request(detail)
        before = await self.state(all_collections=True)
        with patch.object(local.routes, "_ensure_indexes", side_effect=AutoReconnect("synthetic preflight DB failure")):
            response = await self.submit(payload)
        self.assert_unknown(response, 503)
        self.assertEqual(await self.state(all_collections=True), before)
        self.assertEqual(self.sent_posts, 1)
        self.assert_unknown(await self.reconcile(payload["client_request_id"]))

    async def test_native_employee_actual_auth_asgi_completion_and_contract_export(self):
        # Execute the actual server dependency and native merchant bridge without
        # importing the server's external-service startup. The Request annotation
        # is retained so FastAPI injects the genuine ASGI request into the function.
        import auth
        from mobile_app_permissions import MOBILE_APP_CLIENT
        from starlette.requests import Request

        source_root = Path(__file__).parents[1]
        source_names = (
            "server.py", "auth.py", "mobile_app_request_context.py", "mobile_app_permissions.py",
            "order_review_routes.py", "order_review_approval.py", "order_review_completion.py",
            "review_completion_readback.py", "review_session_fence.py", "operational_atomic.py",
            "review_acceptance_config_guard.py",
        )
        hashes = {name: hashlib.sha256((source_root / name).read_bytes()).hexdigest()
                  for name in source_names}
        function = next(node for node in ast.parse((source_root / "server.py").read_text(encoding="utf-8")).body
                        if isinstance(node, ast.AsyncFunctionDef) and node.name == "current_user")
        function.decorator_list = []
        module = ast.Module(body=[function], type_ignores=[])
        ast.fix_missing_locations(module)
        # Match server.py's actual DB adapter at both dependency and router seams.
        production_db = AcceptanceConfigDatabase(self.db)
        namespace = {"db": production_db, "get_current_user_from_db": auth.get_current_user_from_db, "Request": Request}
        exec(compile(module, str(source_root / "server.py"), "exec"), namespace)
        self.app.include_router(local.routes.make_order_review_router(production_db, namespace["current_user"]), prefix="/api")
        self.route_prefix = "/api"

        # Deliberately omit created_by: real registry fallback must resolve the
        # merchant while preserving the employee's signed session and actor id.
        employee = {"id": "native-reviewer", "role": "viewer", "name": "Synthetic reviewer",
                    "email": "reviewer@example.invalid"}
        await self.db.users.insert_many([deepcopy(employee), {
            "id": "owner", "role": "owner", "email": "owner@example.invalid",
            "_review_session": {"origin_session_ref": "FORGED-OWNER-DATA"},
        }])
        await self.db.mezan_employees_v2.insert_one({"account_user_id": employee["id"], "user_id": "owner"})
        await self.db.mezan_mobile_app_access_v1.insert_one({"owner_user_id": "owner", "user_id": employee["id"],
            "enabled": True, "permissions": ["app.page.pending_review"]})
        employee["_review_session"] = await sessions.create_review_session(self.db, employee)
        self.actor = employee
        token = auth.create_access_token(employee["id"], employee["email"], mfa_verified=True,
            review_session=employee["_review_session"], client_type=MOBILE_APP_CLIENT)
        self.client.headers["authorization"] = "Bearer " + token

        detail = await self.display()
        payload = self.request(detail, "927b9444-3bc1-4fa1-b273-abfbaf38ef2d")
        posted = await self.submit(payload)
        proof = await self.assert_commit(posted, detail, payload)
        self.assertEqual(proof["actor_id"], "native-reviewer")
        self.assertEqual(proof["merchant_id"], "owner")
        self.assertNotEqual(proof["origin_session_ref"], "FORGED-OWNER-DATA")
        before = await self.state(all_collections=True)
        read = await self.reconcile(payload["client_request_id"])
        self.assertEqual(await self.assert_commit(read, detail, payload), proof)
        self.assertEqual(await self.state(all_collections=True), before)
        self.assertEqual(self.sent_posts, 1)

        # A fresh permission check also protects historical GET; it is not a
        # public lookup by UUID, nor an authorization inherited from the owner.
        business_before = await self.state()
        await self.db.mezan_mobile_app_access_v1.update_one({"user_id": employee["id"]}, {"$set": {"enabled": False}})
        denied = await self.reconcile(payload["client_request_id"])
        self.assertEqual(denied.status_code, 403, denied.text)
        self.assertEqual(denied.json()["detail"]["code"], "mobile_app_page_permission_required")
        self.assertEqual(await self.state(), business_before)
        self.assertEqual(hashes, {name: hashlib.sha256((source_root / name).read_bytes()).hexdigest()
                                  for name in source_names}, "Source changed during contract capture")

        destination = os.environ.get("MZ2_TEST_READBACK_CONTRACT_EXPORT")
        if destination:
            # Explicit runner opt-in only. All users, credentials and order data
            # are public synthetic fixtures. Never export the access JWT.
            document = {
                "schema_version": 1, "synthetic": True,
                "display": {"status": 200, "body": detail},
                "request": {"order_number": "local-review", "body": payload},
                "post": {"status": posted.status_code, "body": posted.json()},
                "get": {"status": read.status_code, "body": read.json()},
                "provenance": {"source_root": str(source_root), "source_sha256": hashes,
                    "candidate_kind": "uncommitted working tree; not final HEAD or deployment evidence",
                    "mongo_version": (await self.db.command("buildInfo"))["version"],
                    "database_kind": "owned synthetic loopback replica-set fixture",
                    "auth_path": "actual extracted server.current_user -> native bridge -> reviewer normalization",
                    "database_adapter": "actual AcceptanceConfigDatabase as constructed by server.py",
                    "provider_requests": 0, "production_business_writes": 0,
                    "approval_token_is_public_synthetic_fixture": True},
            }
            Path(destination).write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
