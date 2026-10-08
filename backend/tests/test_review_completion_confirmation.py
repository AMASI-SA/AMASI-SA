"""Durable dispatch with delayed/lost Salla replies; real loopback Mongo only."""
import asyncio
from copy import deepcopy
from unittest.mock import patch
import unittest

import httpx
import order_review_completion as completion
import order_review_routes as routes
import order_review_resume_worker as worker
import test_review_completion_auto_resume as fixture


class ConfirmationTests(unittest.IsolatedAsyncioTestCase):
    asyncTearDown = fixture.AutoResumeTests.asyncTearDown
    source_payload = fixture.AutoResumeTests.source_payload
    webhook = fixture.AutoResumeTests.webhook
    saved = fixture.AutoResumeTests.saved
    due = fixture.AutoResumeTests.due
    assert_completed = fixture.AutoResumeTests.assert_completed

    async def asyncSetUp(self):
        await fixture.AutoResumeTests.asyncSetUp(self)
        self.provider_patch.stop()  # Real sync helper; replace only external transport.
        self.client._transport.raise_app_exceptions = True
        self.payload = self.source_payload(number="new-review")
        await self.webhook(self.payload)
        self.posts = 0
        self.visible = False
        self.post_error = None
        self.read_error = None
        self.after_post = None
        self.transport_patch = patch.object(routes, "call_salla", self.transport)
        self.transport_patch.start()
        self.patches.append(self.transport_patch)

    async def transport(self, db, owner, method, path, **kwargs):
        self.assertEqual(owner, "owner")
        if path == "/products/p":
            return {"data": {"id": "p", "name": "Synthetic product", "sku": "SYN-P", "options": []}}
        if path == "/products":
            return {"data": []}
        if path.startswith("/products/"):
            return {"data": {"id": path.rsplit("/", 1)[-1], "options": []}}
        if method == "POST":
            self.assertEqual(path, "/orders/new-review/status")
            self.assertEqual(kwargs["json"], {"status_id": 12})
            self.posts += 1
            op = await self.saved()
            if op.get("provider_delivery_version"):
                self.assertEqual(op["provider_dispatch"]["state"], "dispatch_started")
            if self.after_post:
                await self.after_post()
            if self.post_error:
                raise self.post_error
            return {"success": True}
        if self.posts and self.read_error:
            raise self.read_error
        if path == "/orders/statuses":
            return {"data": [{"id": 12, "name": "تمت المراجعة"}]}
        if path == "/orders/items":
            return {"data": deepcopy(self.payload["items"])}
        self.assertEqual(path, "/orders/new-review")
        data = deepcopy(self.payload)
        if self.visible:
            data["status"]["customized"] = {"name": "تمت المراجعة"}
        return {"data": data}

    async def post(self):
        return await self.client.post("/order-reviews-v1/new-review/complete", json={"expected_revision": 0})

    async def test_delayed_visibility_returns_202_then_worker_completes_without_repost(self):
        response = await self.post()
        self.assertEqual(response.status_code, 202, response.text)
        self.assertTrue(response.json()["confirmation_pending"])
        original = await self.saved()
        self.assertEqual(original["provider_dispatch"]["state"], "response_received")
        self.assertEqual(original["state"], "syncing")
        self.assertEqual(await self.db[completion.EVENTS].count_documents({}), 0)
        for _ in range(2):
            await self.due()
            await worker.run_once(self.db)
            self.assertEqual(await worker.run_once(self.db), 0)  # backoff applied
        self.assertEqual(self.posts, 1)
        self.visible = True
        await self.due()
        await asyncio.gather(worker.run_once(self.db), worker.run_once(self.db), self.post())
        await self.assert_completed(original)
        self.assertEqual(self.posts, 1)

    async def test_lease_lost_after_post_preserves_marker_without_stale_receipt(self):
        async def steal():
            await self.db[completion.OPERATIONS].update_one({}, {"$set": {
                "lease_token": "different-owner", "lease_until": "2099-01-01T00:00:00+00:00"}})
        self.after_post = steal
        response = await self.post()
        self.assertEqual(response.status_code, 202, response.text)
        original = await self.saved()
        self.assertEqual(original["provider_dispatch"]["state"], "dispatch_started")
        self.assertEqual(original["lease_token"], "different-owner")
        self.after_post = None
        await self.db[completion.OPERATIONS].update_one({}, {"$set": {"lease_until": "", "lease_token": None}})
        self.assertEqual((await self.post()).status_code, 202)
        self.visible = True
        await self.due()
        await worker.run_once(self.db)
        await self.assert_completed(original)
        self.assertEqual(self.posts, 1)

    async def test_lease_lost_before_post_is_409_with_zero_posts(self):
        transport = self.transport
        async def steal_before(db, owner, method, path, **kwargs):
            result = await transport(db, owner, method, path, **kwargs)
            if path == "/orders/statuses":
                await self.db[completion.OPERATIONS].update_one({}, {"$set": {"lease_token": "other"}})
            return result
        with patch.object(routes, "call_salla", steal_before):
            response = await self.post()
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["detail"]["code"], "review_completion_lease_lost")
        self.assertEqual(self.posts, 0)
        self.assertNotIn("provider_dispatch", await self.saved())

    async def test_process_cancel_after_marker_is_confirmation_only_after_restart(self):
        self.post_error = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await self.post()
        original = await self.saved()
        self.assertEqual(original["provider_dispatch"]["state"], "dispatch_started")
        self.post_error = None
        self.assertEqual((await self.post()).status_code, 202)
        self.visible = True
        await worker.run_once(self.db)
        await self.assert_completed(original)
        self.assertEqual(self.posts, 1)

    async def test_crash_before_network_does_not_guess_post_was_unsent(self):
        transport = self.transport
        async def crash_before(db, owner, method, path, **kwargs):
            if method == "POST":
                raise asyncio.CancelledError()
            return await transport(db, owner, method, path, **kwargs)
        with patch.object(routes, "call_salla", crash_before):
            with self.assertRaises(asyncio.CancelledError):
                await self.post()
        self.assertEqual(self.posts, 0)
        self.assertEqual((await self.post()).status_code, 202)
        self.assertEqual(self.posts, 0)

    async def test_confirmation_wait_budget_is_bounded_and_keeps_dispatch_evidence(self):
        self.assertEqual((await self.post()).status_code, 202)
        original = await self.saved()
        for _ in range(worker.MAX_ATTEMPTS):
            await self.due()
            await worker.run_once(self.db)
        saved = await self.saved()
        self.assertEqual(saved["state"], "requires_review")
        self.assertEqual(saved["provider_dispatch"], original["provider_dispatch"])
        self.assertEqual(saved["resume_block_reason"], "review_provider_confirmation_pending")
        self.assertEqual(await worker.run_once(self.db), 0)
        self.assertEqual((await self.post()).status_code, 409)
        self.assertEqual(self.posts, 1)

    async def test_confirmed_provider_finalize_rollback_resumes_once(self):
        self.visible = False
        async def visible_after():
            self.visible = True
        self.after_post = visible_after
        await self.db.create_collection(completion.EVENTS)
        await self.db.command({"collMod": completion.EVENTS, "validator": {"event_type": {"$ne": "order_review_completed"}}})
        self.client._transport.raise_app_exceptions = False
        self.assertEqual((await self.post()).status_code, 500)
        original = await self.saved()
        self.assertEqual(original["state"], "provider_confirmed")
        self.assertEqual(await self.db[completion.WORKFLOWS].count_documents({}), 0)
        await self.db.command({"collMod": completion.EVENTS, "validator": {}})
        await worker.run_once(self.db)
        await self.assert_completed(original)
        self.assertEqual(self.posts, 1)

    async def test_unknown_outcome_does_not_allow_changed_business_facts(self):
        self.assertEqual((await self.post()).status_code, 202)
        source = await self.db.unified_orders.find_one({})
        for path, value in [("items.0.product_id", "other"), ("items.0.sku", "other"),
                            ("items.0.variant_id", "other"), ("items.0.options", [{"value": "other"}]),
                            ("items.0.quantity", 9), ("payment_method", "bank"),
                            ("amounts.total.amount", 999), ("unknown_business_field", True),
                            ("status.slug", "canceled")]:
            with self.subTest(path=path):
                await self.db.unified_orders.replace_one({"_id": source["_id"]}, deepcopy(source))
                await self.db.unified_orders.update_one({}, {"$set": {"raw_by_source.salla_direct." + path: value}})
                self.assertEqual((await self.post()).status_code, 409)
                self.assertEqual(self.posts, 1)
                self.assertEqual(await self.db[completion.EVENTS].count_documents({}), 0)

    async def test_provider_business_change_is_not_hidden_by_pending(self):
        self.assertEqual((await self.post()).status_code, 202)
        self.payload["items"][0]["quantity"] = 9
        response = await self.post()
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["detail"]["reason"], "salla_review_source_changed")
        self.assertEqual(self.posts, 1)

    async def test_explicit_reapproval_preserves_dispatch_lineage(self):
        self.assertEqual((await self.post()).status_code, 202)
        original = await self.saved()
        await self.db.order_review_acceptance_config_versions.update_one({"_id": "owner"}, {"$inc": {"version": 1}})
        from order_review_acceptance_snapshot import acceptance_snapshot, fingerprint
        order = await routes.get_order(routes.MongoOrderRepository(self.db), user_id="owner", order_number="new-review")
        config = await acceptance_snapshot(self.db, user_id="owner", order=order)
        response = await self.client.post("/order-reviews-v1/new-review/complete", json={
            "expected_revision": 0, "reapprove_operation_id": original["_id"],
            "expected_acceptance_fingerprint": fingerprint(config)})
        self.assertEqual(response.status_code, 202, response.text)
        successor = await self.db[completion.OPERATIONS].find_one({"_id": response.json()["operation_id"]})
        self.assertEqual(successor["provider_dispatch"]["origin_operation_id"], original["_id"])
        self.assertEqual(self.posts, 1)

    async def test_legacy_provider_contract_is_not_silently_upgraded(self):
        # A historical operation has no proof that a missing marker means unsent.
        self.assertEqual((await self.post()).status_code, 202)
        await self.db[completion.OPERATIONS].update_one({}, {"$unset": {"provider_delivery_version": "", "provider_dispatch": ""}})
        self.visible = True
        self.assertEqual((await self.post()).status_code, 200)
        saved = await self.saved()
        self.assertNotIn("provider_delivery_version", saved)
        self.assertNotIn("provider_dispatch", saved)
        self.assertEqual(self.posts, 1)

    async def test_lost_reply_and_failed_readback_never_redispatch(self):
        self.post_error = httpx.ReadTimeout("synthetic secret must not be logged")
        self.read_error = httpx.ConnectError("synthetic unreachable")
        response = await self.post()
        self.assertEqual(response.status_code, 202, response.text)
        original = await self.saved()
        self.assertEqual(original["provider_dispatch"]["state"], "outcome_unknown")
        self.read_error = None
        self.assertEqual((await self.post()).status_code, 202)
        self.visible = True
        await self.due()
        await worker.run_once(self.db)
        await self.assert_completed(original)
        self.assertEqual(self.posts, 1)
