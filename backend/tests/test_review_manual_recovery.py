"""Explicit new approval: ASGI + real loopback Mongo, synthetic Salla GETs only.

An allowlisted number is a synthetic identifier here, never a production payload.
The historical operation deliberately has no business approval snapshot.
"""
import asyncio
from copy import deepcopy
import unittest
from unittest.mock import AsyncMock, patch

import order_engine.salla_refresh as refresh_module
import order_review_completion as completion
import order_review_manual_recovery as manual
import order_review_routes as routes
import order_review_resume_worker as worker
from order_review_acceptance_snapshot import acceptance_snapshot
import test_review_completion_auto_resume as fixture


class ManualRecoveryTests(unittest.IsolatedAsyncioTestCase):
    asyncTearDown = fixture.AutoResumeTests.asyncTearDown
    source_payload = fixture.AutoResumeTests.source_payload
    webhook = fixture.AutoResumeTests.webhook
    refresh = fixture.AutoResumeTests.refresh
    number = "291967952"

    async def asyncSetUp(self):
        await fixture.AutoResumeTests.asyncSetUp(self)
        self.payload = self.source_payload(number=self.number, status="reviewed")
        self.payload["status"]["name"] = "تم المراجعة"
        self.payload["shipping"] = {"company": "Synthetic carrier", "method": "door"}
        await self.webhook(self.payload)
        current_order = await routes.get_order(routes.MongoOrderRepository(self.db), user_id="owner", order_number=self.number)
        original_acceptance = await acceptance_snapshot(self.db, user_id="owner", order=current_order)
        self.old = {
            "_id": "review_" + "a" * 64, "user_id": "owner", "actor_id": "owner",
            "order_number": self.number, "revision": 0, "state": "requires_review",
            "resume_block_reason": "review_completion_source_changed",
            "provider_confirmed_at": "2026-09-25T12:00:00+00:00",
            "source_fingerprint": "synthetic-historical-hash-do-not-rewrite",
            "order_fingerprint": "synthetic-historical-order-hash", "workflow_fingerprint": "old",
            "items": [], "acceptance_snapshot": original_acceptance,
        }
        await self.db[completion.OPERATIONS].insert_one(deepcopy(self.old))
        self.gets = []

        async def transport(_db, _owner, method, path, **kwargs):
            self.assertEqual(method, "GET", "Manual recovery must not mutate Salla")
            self.gets.append(path)
            if path == "/orders/items":
                return {"data": deepcopy(self.payload["items"])}
            if path == "/orders/" + self.number:
                return {"data": deepcopy(self.payload)}
            if path == "/products/p":
                return {"data": {"id": "p", "name": "Synthetic product", "sku": "SYN-P",
                    "images": [{"url": "https://synthetic.invalid/a.png"}, {"url": "https://synthetic.invalid/b.png"}]}}
            self.fail("Unexpected Salla path " + path)

        async def bank(_db, _owner, value):
            return value

        for target, attribute, replacement in [
            (refresh_module, "call_salla", transport),
            (routes, "call_salla", transport),
            (refresh_module, "_enrich_order_receiving_bank", bank),
        ]:
            replacement_patch = patch.object(target, attribute, replacement)
            replacement_patch.start()
            self.patches.append(replacement_patch)

    async def prepare(self):
        response = await self.client.post(f"/order-reviews-v1/{self.number}/manual-recovery/prepare",
            json={"old_operation_id": self.old["_id"]})
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertTrue(body["session_id"])
        self.assertTrue(body["approval_hash"])
        self.assertEqual(body["old_operation_id"], self.old["_id"])
        self.assertEqual(body["order_number"], self.number)
        self.assertTrue(body["preview"])
        self.assertEqual(await self.db[completion.OPERATIONS].count_documents({}), 1)
        await self.assert_old_unchanged()
        return body

    async def confirm(self, session, **overrides):
        body = {"session_id": session["session_id"], "approval_hash": session["approval_hash"], "confirmed": True}
        body.update(overrides)
        return await self.client.post(f"/order-reviews-v1/{self.number}/manual-recovery/confirm", json=body)

    async def assert_old_unchanged(self):
        self.assertEqual(await self.db[completion.OPERATIONS].find_one({"_id": self.old["_id"]}), self.old)

    async def assert_no_completion(self):
        self.assertEqual(await self.db[completion.WORKFLOWS].count_documents({"stage": "reviewed"}), 0)
        self.assertEqual(await self.db[completion.EVENTS].count_documents({"event_type": "order_review_completed"}), 0)
        self.provider.assert_not_awaited()
        await self.assert_old_unchanged()

    async def assert_completed_once(self):
        await self.assert_old_unchanged()
        self.assertEqual(await self.db[completion.OPERATIONS].count_documents({}), 2)
        new = await self.db[completion.OPERATIONS].find_one({"_id": {"$ne": self.old["_id"]}})
        self.assertEqual(new["state"], "completed")
        self.assertTrue(new["business_snapshot"])
        audit = new["manual_review_recovery"]
        self.assertEqual(audit["old_operation_id"], self.old["_id"])
        self.assertEqual(audit["new_operation_id"], new["_id"])
        self.assertEqual(audit["actor_id"], "owner")
        self.assertEqual(audit["reason"], "manual_review_recovery")
        self.assertEqual(audit["provider_mode"], "read_only")
        self.assertTrue(audit["confirmed_at"])
        self.assertEqual(audit["approval_hash"], new["business_snapshot"]["integrity_hash"])
        self.assertEqual(audit["schema_version"], new["business_snapshot"]["schema_version"])
        self.assertEqual(audit["normalization_version"], new["business_snapshot"]["normalization_version"])
        self.assertEqual(await self.db[completion.WORKFLOWS].count_documents({"stage": "reviewed"}), 1)
        self.assertEqual(await self.db[completion.EVENTS].count_documents({"event_type": "order_review_completed"}), 1)
        self.assertEqual(len((await self.client.get("/order-reviews-v1/reviewed")).json()["items"]), 1)
        self.provider.assert_not_awaited()
        return new

    async def test_commit_failure_worker_restart_and_manual_retry_preserve_new_identity(self):
        session = await self.prepare()
        await self.db.command({"collMod": completion.EVENTS,
            "validator": {"event_type": {"$ne": "order_review_completed"}}, "validationLevel": "strict"})
        response = await self.confirm(session)
        self.assertEqual(response.status_code, 500, response.text)
        pending = await self.db[completion.OPERATIONS].find_one({"_id": {"$ne": self.old["_id"]}})
        self.assertEqual(pending["state"], "provider_confirmed")
        await self.assert_no_completion()
        await self.db.command({"collMod": completion.EVENTS, "validator": {}})
        # A fresh worker call reloads durable evidence, without in-memory approval.
        await worker.run_once(self.db)
        recovered = await self.assert_completed_once()
        self.assertEqual(recovered["_id"], pending["_id"])
        self.assertEqual(recovered["provider_confirmed_at"], pending["provider_confirmed_at"])
        self.assertEqual((await self.confirm(session)).status_code, 200)
        await self.assert_completed_once()

    async def test_explicit_new_approval_visible_once_old_operation_immutable(self):
        self.assertEqual((await self.client.get("/order-reviews-v1/reviewed")).json()["items"], [])
        session = await self.prepare()
        await self.assert_no_completion()
        self.assertIn("/orders/items", self.gets)
        response = await self.confirm(session)
        self.assertEqual(response.status_code, 200, response.text)
        first = await self.assert_completed_once()
        response = await self.confirm(session)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual((await self.assert_completed_once())["_id"], first["_id"])

    async def test_concurrent_confirm_one_new_operation_workflow_event(self):
        session = await self.prepare()
        results = await asyncio.gather(self.confirm(session), self.confirm(session))
        self.assertTrue(any(r.status_code == 200 for r in results), [r.text for r in results])
        for response in results:
            self.assertIn(response.status_code, (200, 409), response.text)
        await self.assert_completed_once()

    async def test_explicit_fresh_approval_after_failed_new_operation_does_not_reuse_it(self):
        session = await self.prepare()
        self.payload["shipping"]["company"] = "New current carrier"
        self.payload["updated_at"] = "2026-09-26T13:00:00+00:00"
        response = await self.confirm(session)
        self.assertEqual(response.status_code, 409, response.text)
        await worker.run_once(self.db)
        failed = await self.db[completion.OPERATIONS].find_one({"_id": {"$ne": self.old["_id"]}})
        self.assertEqual(failed["state"], "requires_review")
        candidates = await self.client.get("/order-reviews-v1/manual-recovery/candidates")
        self.assertEqual(candidates.status_code, 200, candidates.text)
        self.assertEqual(candidates.json()["items"], [{"order_number": self.number, "old_operation_id": self.old["_id"]}])
        response = await self.client.post(f"/order-reviews-v1/{self.number}/manual-recovery/prepare",
            json={"old_operation_id": self.old["_id"]})
        self.assertEqual(response.status_code, 200, response.text)
        fresh = response.json()
        self.assertNotEqual(fresh["session_id"], session["session_id"])
        response = await self.confirm(fresh)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(await self.db[completion.OPERATIONS].count_documents({}), 3)
        self.assertEqual(await self.db[completion.OPERATIONS].find_one({"_id": failed["_id"]}), failed)
        new = await self.db[completion.OPERATIONS].find_one({"state": "completed"})
        self.assertNotEqual(new["_id"], failed["_id"])
        self.assertEqual(await self.db[completion.WORKFLOWS].count_documents({"stage": "reviewed"}), 1)
        self.assertEqual(await self.db[completion.EVENTS].count_documents({"event_type": "order_review_completed"}), 1)
        self.assertEqual(len((await self.client.get("/order-reviews-v1/reviewed")).json()["items"]), 1)
        await self.assert_old_unchanged()
        self.provider.assert_not_awaited()

    async def test_explicit_confirmation_hash_and_session_required(self):
        session = await self.prepare()
        for overrides in ({"confirmed": False}, {"approval_hash": "tampered"}, {"session_id": "missing"}):
            with self.subTest(overrides=overrides):
                response = await self.confirm(session, **overrides)
                self.assertIn(response.status_code, (400, 404, 409, 422), response.text)
                await self.assert_no_completion()

    async def test_actor_cannot_confirm_another_actor_session(self):
        session = await self.prepare()
        self.actor = {"id": "different-owner", "role": "owner"}
        response = await self.confirm(session)
        self.assertIn(response.status_code, (403, 404, 409), response.text)
        await self.assert_no_completion()

    async def test_expired_preview_rejected(self):
        session = await self.prepare()
        await self.db[manual.SESSIONS].update_one({"_id": session["session_id"]},
            {"$set": {"expires_at": "2000-01-01T00:00:00+00:00"}})
        response = await self.confirm(session)
        self.assertEqual(response.status_code, 409, response.text)
        await self.assert_no_completion()

    async def test_component_generation_change_rejected(self):
        session = await self.prepare()
        await self.db.mezan_component_order_lifecycle_v1.update_one({"order_number": self.number},
            {"$inc": {"generation": 1}})
        response = await self.confirm(session)
        self.assertEqual(response.status_code, 409, response.text)
        await self.assert_no_completion()

    async def test_refresh_transport_failure_does_not_approve(self):
        with patch.object(refresh_module, "call_salla", AsyncMock(side_effect=TimeoutError("synthetic"))):
            response = await self.client.post(f"/order-reviews-v1/{self.number}/manual-recovery/prepare",
                json={"old_operation_id": self.old["_id"]})
        self.assertIn(response.status_code, (409, 502, 503, 504), response.text)
        self.assertEqual(await self.db[completion.OPERATIONS].count_documents({}), 1)
        await self.assert_no_completion()

    async def test_business_changes_after_preview_reject_confirmation(self):
        session = await self.prepare()
        original = await self.db.unified_orders.find_one({"order_number": self.number})
        mutations = [
            ("items.0.product_id", "other-product"), ("items.0.quantity", 7),
            ("items.0.sku", "OTHER-SKU"), ("items.0.options", [{"name": "size", "value": "XL"}]),
            ("items.0.customer_options", [{"name": "text", "value": "other"}]),
            ("shipping.company", "Another carrier"), ("customer.mobile", "0500000001"),
            ("payment_method", "bank"), ("unknown_business_field", "changed"),
        ]
        for path, value in mutations:
            with self.subTest(path=path):
                await self.db.unified_orders.replace_one({"_id": original["_id"]}, deepcopy(original))
                await self.db.unified_orders.update_one({"_id": original["_id"]},
                    {"$set": {"raw_by_source.salla_direct." + path: value}})
                response = await self.confirm(session)
                self.assertEqual(response.status_code, 409, response.text)
                await self.assert_no_completion()

    async def test_acceptance_change_stays_409(self):
        session = await self.prepare()
        await self.db.order_review_acceptance_config_versions.update_one({"_id": "owner"}, {"$inc": {"version": 1}})
        response = await self.confirm(session)
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["detail"]["code"], "component_acceptance_changed")
        await self.assert_no_completion()

    async def provider_change(self, path, value):
        session = await self.prepare()
        target = self.payload
        parts = path.split(".")
        for key in parts[:-1]:
            if isinstance(target, list):
                target = target[int(key)]
            else:
                target = target.setdefault(key, {})
        target[parts[-1]] = value
        response = await self.confirm(session)
        self.assertEqual(response.status_code, 409, response.text)
        await self.assert_no_completion()

    async def test_provider_quantity_change_rejected(self):
        await self.provider_change("items.0.quantity", 8)

    async def test_provider_product_change_rejected(self):
        await self.provider_change("items.0.product_id", "another-product")

    async def test_provider_sku_change_rejected(self):
        await self.provider_change("items.0.sku", "OTHER")

    async def test_provider_options_change_rejected(self):
        await self.provider_change("items.0.options", [{"name": "Size", "value": "XL"}])

    async def test_provider_shipping_change_rejected(self):
        await self.provider_change("shipping.company", "Different carrier")

    async def test_provider_customer_change_rejected(self):
        await self.provider_change("customer.mobile", "0500000001")

    async def test_cancellation_after_preview_rejected(self):
        session = await self.prepare()
        await self.db.unified_orders.update_one({"order_number": self.number},
            {"$set": {"raw_by_source.salla_direct.status": {"slug": "canceled"}}})
        response = await self.confirm(session)
        self.assertEqual(response.status_code, 409, response.text)
        await self.assert_no_completion()

    async def test_revision_after_preview_rejected(self):
        session = await self.prepare()
        await self.db[completion.WORKFLOWS].insert_one({"user_id": "owner", "order_number": self.number,
            "revision": 7, "stage": "pending_review"})
        response = await self.confirm(session)
        self.assertEqual(response.status_code, 409, response.text)
        await self.assert_no_completion()

    async def test_non_source_mismatch_is_not_candidate(self):
        await self.db[completion.OPERATIONS].update_one({"_id": self.old["_id"]},
            {"$set": {"resume_block_reason": "component_acceptance_changed"}})
        response = await self.client.get("/order-reviews-v1/manual-recovery/candidates")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["items"], [])
        response = await self.client.post(f"/order-reviews-v1/{self.number}/manual-recovery/prepare",
            json={"old_operation_id": self.old["_id"]})
        self.assertIn(response.status_code, (404, 409), response.text)
        self.assertEqual(self.gets, [])

    async def test_missing_recipe_evidence_with_existing_component_plan_rejected(self):
        from stock_component_consumption_service import PLANS
        self.assertTrue(await self.db[PLANS].find_one({"user_id": "owner", "order_id": self.number}))
        await self.db[completion.OPERATIONS].update_one({"_id": self.old["_id"]},
            {"$set": {"acceptance_snapshot": {}}})
        self.old["acceptance_snapshot"] = {}
        response = await self.client.post(f"/order-reviews-v1/{self.number}/manual-recovery/prepare",
            json={"old_operation_id": self.old["_id"]})
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn("component_plan_reapproval_required", response.text)
        await self.assert_no_completion()

    async def test_changed_recipe_with_existing_component_plan_rejected(self):
        from stock_component_consumption_service import PLANS
        self.assertTrue(await self.db[PLANS].find_one({"user_id": "owner", "order_id": self.number}))
        await self.db[completion.OPERATIONS].update_one({"_id": self.old["_id"]},
            {"$set": {"acceptance_snapshot.product_bindings": []}})
        self.old["acceptance_snapshot"]["product_bindings"] = []
        response = await self.client.post(f"/order-reviews-v1/{self.number}/manual-recovery/prepare",
            json={"old_operation_id": self.old["_id"]})
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn("component_plan_reapproval_required", response.text)
        await self.assert_no_completion()

    async def test_wrong_status_or_existing_reviewed_workflow_not_candidate(self):
        await self.db.unified_orders.update_one({"order_number": self.number},
            {"$set": {"raw_by_source.salla_direct.status": {"slug": "under_review", "name": "بانتظار المراجعة"}}})
        response = await self.client.get("/order-reviews-v1/manual-recovery/candidates")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["items"], [])
        await self.db.unified_orders.update_one({"order_number": self.number},
            {"$set": {"raw_by_source.salla_direct.status": deepcopy(self.payload["status"])}})
        await self.db[completion.WORKFLOWS].insert_one({"user_id": "owner", "order_number": self.number,
            "stage": "reviewed", "revision": 1})
        response = await self.client.get("/order-reviews-v1/manual-recovery/candidates")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["items"], [])

    async def test_out_of_allowlist_rejected_before_transport(self):
        response = await self.client.post("/order-reviews-v1/999999999/manual-recovery/prepare",
            json={"old_operation_id": self.old["_id"]})
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(self.gets, [])
        await self.assert_no_completion()


if __name__ == "__main__":
    unittest.main()
