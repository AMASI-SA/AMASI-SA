"""Real local Mongo/ASGI contracts. All successful recovery data is synthetic.

The nine real evidence summaries are tested separately; their missing approval
inputs must never be manufactured by this fixture.
"""
import asyncio
from copy import deepcopy
import unittest
from unittest.mock import patch

from fastapi import HTTPException

import order_review_completion as completion
import order_review_routes as routes
import order_review_resume_worker as worker
import order_review_recovery_guard as recovery
from order_review_acceptance_snapshot import acceptance_snapshot
from order_review_business_snapshot import compare_snapshots, build_snapshot
import test_review_completion_auto_resume as fixture


class BusinessRecoveryTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.AutoResumeTests.asyncSetUp
    asyncTearDown = fixture.AutoResumeTests.asyncTearDown
    source_payload = fixture.AutoResumeTests.source_payload
    webhook = fixture.AutoResumeTests.webhook
    refresh = fixture.AutoResumeTests.refresh
    saved = fixture.AutoResumeTests.saved
    assert_completed = fixture.AutoResumeTests.assert_completed

    async def pending(self, *, blocked=True):
        # Synthetic order uses an explicitly permitted incident order number;
        # no incident content or customer data is used or invented.
        self.number = "291967952"
        payload = self.source_payload(number=self.number)
        payload["shipping"] = {"company": "Synthetic carrier", "method": "door"}
        await self.webhook(payload)
        await self.refresh(payload)
        await self.db.create_collection(completion.EVENTS)
        await self.db.command({"collMod": completion.EVENTS,
            "validator": {"event_type": {"$ne": "order_review_completed"}}, "validationLevel": "strict"})
        async def provider_boundary(*_args):
            saved = await self.saved()
            self.assertEqual(saved["state"], "syncing")
            self.assertTrue(saved["business_snapshot"]["integrity_hash"])
            self.assertTrue(saved["approved_component_source"]["source_fingerprint"])
            return "sent", None
        self.provider.side_effect = provider_boundary
        response = await self.client.post(f"/order-reviews-v1/{self.number}/complete", json={"expected_revision": 0})
        self.assertEqual(response.status_code, 500, response.text)
        op = await self.saved()
        self.assertEqual(op["state"], "provider_confirmed")
        self.assertTrue(op["business_snapshot"])
        self.assertTrue(op["approved_component_source"])
        self.assertEqual((await self.client.get("/order-reviews-v1/reviewed")).json()["items"], [])
        await self.db.command({"collMod": completion.EVENTS, "validator": {}})
        # Reproduce the historical boundary without constructing an approval:
        # a legacy raw comparator sees a different representation, while the
        # durable immutable snapshot saved before provider I/O remains intact.
        source = await self.db.unified_orders.find_one({})
        raw = deepcopy(source)
        raw["raw_by_source"]["salla_direct"]["shipping"].pop("company_name")
        self.assertNotEqual(completion.source_fingerprint(source, 1), completion.source_fingerprint(raw, 1))
        await self.db.unified_orders.update_one({}, {"$set": {"raw_by_source": raw["raw_by_source"]}})
        await self.db[completion.OPERATIONS].update_one({"_id": op["_id"]}, {"$set": {
            "state": "requires_review" if blocked else "provider_confirmed",
            "resume_block_reason": "review_completion_source_changed"}})
        self.request = {"request_id": "isolated-rehearsal", "operation_id": op["_id"],
            "user_id": "owner", "order_number": self.number,
            "approval_hash": op["business_snapshot"]["integrity_hash"]}
        self.provider.reset_mock()
        return await self.saved()

    async def assess(self, op=None):
        order = await routes.get_order(routes.MongoOrderRepository(self.db), user_id="owner", order_number=self.number)
        return recovery.assess_recovery(op or await self.saved(), request=self.request,
            source=await self.db.unified_orders.find_one({}), order=order,
            acceptance=await acceptance_snapshot(self.db, user_id="owner", order=order),
            workflow=await self.db[completion.WORKFLOWS].find_one({}),
            component=await self.db.mezan_component_order_lifecycle_v1.find_one({}))

    async def no_completion(self):
        self.assertEqual(await self.db[completion.WORKFLOWS].count_documents({}), 0)
        self.assertEqual(await self.db[completion.EVENTS].count_documents({"event_type": "order_review_completed"}), 0)
        self.provider.assert_not_awaited()

    async def test_safe_recovery_same_identity_snapshot_confirmation_and_visible_once(self):
        op = await self.pending()
        self.assertEqual((await self.assess())["decision"], "SAFE_TO_RESUME")
        self.assertEqual(await worker.run_once(self.db), 0)
        await recovery.recover_existing(self.db, request=self.request)
        await self.assert_completed(op)
        await recovery.recover_existing(self.db, request=self.request)
        await self.assert_completed(op)
        current = await self.saved()
        self.assertEqual(current["business_snapshot"], op["business_snapshot"])
        self.assertEqual(current["provider_confirmed_at"], op["provider_confirmed_at"])
        self.assertFalse(current["recovery_audit"]["provider_mutation"])
        self.provider.assert_not_awaited()

    async def test_offline_complete_evidence_with_absent_workflow_is_assessed_without_io(self):
        await self.pending()
        from test_review_completion_offline_analysis import analyzer
        order = await routes.get_order(routes.MongoOrderRepository(self.db), user_id="owner", order_number=self.number)
        evidence = {"cases": [{"operation": await self.saved(), "request": self.request,
            "source": await self.db.unified_orders.find_one({}), "order": order.model_dump(mode="json"),
            "acceptance": await acceptance_snapshot(self.db, user_id="owner", order=order),
            "workflow": None, "component": await self.db.mezan_component_order_lifecycle_v1.find_one({})}]}
        with patch("socket.socket.connect", side_effect=AssertionError("Offline analyzer attempted network")):
            result = analyzer.analyze(evidence)
        self.assertEqual(result["counts"]["safe_to_resume"], 1)
        self.assertNotIn("Synthetic", str(result))
        await self.no_completion()

    async def test_business_unknown_and_acceptance_changes_rejected(self):
        await self.pending()
        original = await self.db.unified_orders.find_one({})
        mutations = [
            ("items.0.product_id", "changed", "component_acceptance_changed"),
            ("items.0.quantity", 5, "review_completion_source_changed"),
            ("items.0.sku", "CHANGED", "review_completion_source_changed"),
            ("items.0.options", [{"name": "Size", "value": "XL"}], None),
            ("items.0.customer_options", [{"name": "Text", "value": "Changed"}], None),
            ("customer.mobile", "0511111111", None),
            ("shipping.company", "Other", "review_completion_source_changed"),
            ("payment_method", "bank", "review_completion_order_ineligible"),
            ("previously_unknown", "private-value", "unknown_source_change"),
        ]
        for path, value, code in mutations:
            with self.subTest(path=path):
                await self.db.unified_orders.replace_one({"_id": original["_id"]}, deepcopy(original))
                await self.db.unified_orders.update_one({}, {"$set": {"raw_by_source.salla_direct." + path: value}})
                self.assertEqual((await self.assess())["decision"], "REQUIRES_REVIEW")
                with self.assertRaises(HTTPException) as caught:
                    await recovery.recover_existing(self.db, request=self.request)
                self.assertEqual(caught.exception.status_code, 409)
                if code:
                    self.assertEqual(caught.exception.detail["code"], code)
                await self.no_completion()
        await self.db.unified_orders.replace_one({"_id": original["_id"]}, original)
        await self.db.order_review_acceptance_config_versions.update_one({"_id": "owner"}, {"$inc": {"version": 1}})
        with self.assertRaises(HTTPException) as caught:
            await recovery.recover_existing(self.db, request=self.request)
        self.assertEqual(caught.exception.detail["code"], "component_acceptance_changed")
        await self.no_completion()

    async def test_missing_approval_does_not_use_hash_as_evidence(self):
        await self.pending()
        await self.db[completion.OPERATIONS].update_one({}, {"$unset": {"business_snapshot": ""}})
        decision = await self.assess()
        self.assertEqual(decision["decision"], "REQUIRES_REVIEW")
        self.assertEqual(decision["evidence_status"], "INSUFFICIENT_EVIDENCE")
        with self.assertRaises(HTTPException):
            await recovery.recover_existing(self.db, request=self.request)
        await self.no_completion()

    async def test_two_recoverers_worker_and_manual_retry_no_duplicate(self):
        op = await self.pending()
        results = await asyncio.gather(
            recovery.recover_existing(self.db, request=self.request),
            recovery.recover_existing(self.db, request=self.request), worker.run_once(self.db),
            self.client.post(f"/order-reviews-v1/{self.number}/complete", json={"expected_revision": 0}),
            return_exceptions=True)
        for result in results[:2]:
            if isinstance(result, Exception):
                self.assertIsInstance(result, HTTPException)
                self.assertEqual(result.status_code, 409)
        await self.assert_completed(op)
        self.provider.assert_not_awaited()

    async def test_restart_after_commit_failure_preserves_approval_and_no_provider(self):
        op = await self.pending()
        await self.db.command({"collMod": completion.EVENTS,
            "validator": {"event_type": {"$ne": "order_review_completed"}}, "validationLevel": "strict"})
        with self.assertRaises(Exception):
            await recovery.recover_existing(self.db, request=self.request)
        await self.no_completion()
        self.assertEqual((await self.saved())["business_snapshot"], op["business_snapshot"])
        await self.db.command({"collMod": completion.EVENTS, "validator": {}})
        # A new call reloads the durable operation, no in-memory ticket retained.
        await recovery.recover_existing(self.db, request=deepcopy(self.request))
        await self.assert_completed(op)
        self.provider.assert_not_awaited()

    async def test_cancellation_revision_generation_and_scope_are_guarded(self):
        await self.pending()
        for field, value in (("cancelled", True), ("generation", 999)):
            component = await self.db.mezan_component_order_lifecycle_v1.find_one({})
            await self.db.mezan_component_order_lifecycle_v1.update_one({}, {"$set": {field: value}})
            with self.assertRaises(HTTPException):
                await recovery.recover_existing(self.db, request=self.request)
            await self.no_completion()
            await self.db.mezan_component_order_lifecycle_v1.replace_one({"_id": component["_id"]}, component)
        await self.db[completion.WORKFLOWS].insert_one({"user_id": "owner", "order_number": self.number, "revision": 2})
        with self.assertRaises(HTTPException):
            await recovery.recover_existing(self.db, request=self.request)
        with self.assertRaises(HTTPException) as caught:
            await recovery.recover_existing(self.db, request={**self.request, "order_number": "not-allowed"})
        self.assertEqual(caught.exception.detail["code"], "review_recovery_out_of_scope")

    async def test_unknown_change_keeps_diagnostics_after_rollback(self):
        await self.pending()
        await self.db.unified_orders.update_one({}, {"$set": {"raw_by_source.salla_direct.secret_key_name": "sensitive-value"}})
        with self.assertRaises(HTTPException):
            await recovery.recover_existing(self.db, request=self.request)
        evidence = (await self.saved())["source_diagnostic"]
        self.assertEqual(evidence["code"], "unknown_source_change")
        self.assertNotIn("sensitive-value", str(evidence))
        self.assertNotIn("secret_key_name", str(evidence))
        await self.no_completion()

    async def test_alias_conflict_keeps_diagnostics_after_rollback(self):
        await self.pending()
        await self.db.unified_orders.update_one({}, {"$set": {"raw_by_source.salla_direct.shipping.company_name": "Conflicting"}})
        with self.assertRaises(HTTPException):
            await recovery.recover_existing(self.db, request=self.request)
        evidence = (await self.saved())["source_diagnostic"]
        self.assertEqual(evidence["code"], "review_completion_source_changed")
        self.assertIsNone(evidence["current_canonical_hash"])
        self.assertNotIn("Conflicting", str(evidence))
        await self.no_completion()

    async def test_recovery_timeout_then_reload_same_operation(self):
        op = await self.pending()
        original = completion.operational_owner
        async def interrupt(db, owner, callback, **kwargs):
            if callback.__name__ == "finalize":
                raise asyncio.TimeoutError("isolated commit interruption")
            return await original(db, owner, callback, **kwargs)
        with patch.object(completion, "operational_owner", interrupt):
            with self.assertRaises(asyncio.TimeoutError):
                await recovery.recover_existing(self.db, request=self.request)
        await self.no_completion()
        await recovery.recover_existing(self.db, request=deepcopy(self.request))
        await self.assert_completed(op)
        self.provider.assert_not_awaited()

    async def test_provider_confirmed_recovery_failure_cannot_fall_into_provider_worker(self):
        op = await self.pending(blocked=False)
        original = completion.operational_owner
        async def interrupt(db, owner, callback, **kwargs):
            if callback.__name__ == "finalize":
                raise asyncio.TimeoutError()
            return await original(db, owner, callback, **kwargs)
        with patch.object(completion, "operational_owner", interrupt):
            with self.assertRaises(asyncio.TimeoutError):
                await recovery.recover_existing(self.db, request=self.request)
        self.assertEqual((await self.saved())["state"], "requires_review")
        self.assertEqual(await worker.run_once(self.db), 0)
        await recovery.recover_existing(self.db, request=self.request)
        await self.assert_completed(op)
        self.provider.assert_not_awaited()

    async def test_unfenced_source_writer_after_transaction_snapshot_cannot_commit_stale_approval(self):
        await self.pending()
        original_owner = completion.operational_owner
        original_acceptance = completion.acceptance_snapshot
        at_finalize = False
        injected = False
        async def owner(db, user_id, callback, **kwargs):
            nonlocal at_finalize
            at_finalize = callback.__name__ == "finalize"
            try:
                return await original_owner(db, user_id, callback, **kwargs)
            finally:
                at_finalize = False
        async def config(scoped, **kwargs):
            nonlocal injected
            if at_finalize and not injected:
                injected = True
                # Deliberately bypass owner serialization: an ingestion writer
                # commits after Mongo established the final transaction snapshot.
                await self.db.unified_orders.update_one({}, {"$set": {
                    "raw_by_source.salla_direct.items.0.quantity": 7}})
            return await original_acceptance(scoped, **kwargs)
        with patch.object(completion, "operational_owner", owner), patch.object(completion, "acceptance_snapshot", config):
            with self.assertRaises(HTTPException) as caught:
                await recovery.recover_existing(self.db, request=self.request)
        self.assertTrue(injected)
        self.assertEqual(caught.exception.status_code, 409)
        await self.no_completion()
