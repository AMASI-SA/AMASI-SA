"""ASGI + real Mongo replica-set failures and the production resume loop.

Only provider transport/auth are synthetic. No production database fallback.
"""
import asyncio
import os
import sys
import httpx
from copy import deepcopy
import unittest
from unittest.mock import AsyncMock, patch

import order_review_completion as completion
import order_review_routes as review
import order_review_resume_worker as worker
import reviewed_products_catalog as catalog
import test_g47_component_lifecycle_integration as fixture
from review_acceptance_config_guard import AcceptanceConfigDatabase


class AutoResumeTests(unittest.IsolatedAsyncioTestCase):
    asyncTearDown = fixture.ComponentRouteTests.asyncTearDown
    source_payload = fixture.ComponentRouteTests.source_payload
    webhook = fixture.ComponentRouteTests.webhook
    refresh = fixture.ComponentRouteTests.refresh

    async def asyncSetUp(self):
        await fixture.ComponentRouteTests.asyncSetUp(self)
        await self.db.users.insert_one(self.actor)
        async def actor():
            return self.actor
        self.app.include_router(catalog.make_reviewed_products_catalog_router(self.db, actor))
        self.provider = AsyncMock(return_value=("sent", None))
        self.provider_patch = patch.object(review, "_sync_salla_reviewed", self.provider)
        self.provider_patch.start()
        self.patches.append(self.provider_patch)

    async def pending(self):
        payload = self.source_payload(number="new-review")
        self.assertTrue((await self.webhook(payload))["synced"])
        await self.db.create_collection(completion.EVENTS)
        await self.db.command({"collMod": completion.EVENTS,
            "validator": {"event_type": {"$ne": "order_review_completed"}}, "validationLevel": "strict"})
        response = await self.client.post("/order-reviews-v1/new-review/complete", json={"expected_revision": 0})
        self.assertEqual(response.status_code, 500, response.text)
        op = await self.db[completion.OPERATIONS].find_one({})
        self.assertEqual(op["state"], "provider_confirmed")
        self.assertEqual(await self.db[completion.WORKFLOWS].count_documents({}), 0)
        self.assertEqual((await self.client.get("/order-reviews-v1/reviewed")).json()["items"], [])
        await self.db.command({"collMod": completion.EVENTS, "validator": {}})
        return op

    async def saved(self):
        return await self.db[completion.OPERATIONS].find_one({})

    async def due(self):
        await self.db[completion.OPERATIONS].update_many({}, {"$set": {"resume_due_at": ""}})

    async def assert_completed(self, original):
        saved = await self.saved()
        self.assertEqual(saved["_id"], original["_id"])
        self.assertEqual(saved["state"], "completed")
        for key in ("revision", "items", "acceptance_snapshot", "order_fingerprint", "source_fingerprint", "workflow_fingerprint"):
            self.assertEqual(saved[key], original[key], key)
        self.assertEqual(await self.db[completion.OPERATIONS].count_documents({}), 1)
        self.assertEqual(await self.db[completion.WORKFLOWS].count_documents({"stage": "reviewed"}), 1)
        self.assertEqual(await self.db[completion.EVENTS].count_documents({"event_type": "order_review_completed"}), 1)
        response = await self.client.get("/order-reviews-v1/reviewed")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(response.json()["items"]), 1)
        response = await self.client.get("/reviewed-products-v1/catalog")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["products"])

    async def assert_blocked(self, code=None):
        op = await self.saved()
        self.assertEqual(op["state"], "requires_review", op)
        if code:
            self.assertEqual(op["resume_block_reason"], code)
        self.assertEqual(await self.db[completion.WORKFLOWS].count_documents({"stage": "reviewed"}), 0)
        self.assertEqual(await self.db[completion.EVENTS].count_documents({"event_type": "order_review_completed"}), 0)
        self.assertEqual(await worker.run_once(self.db), 0)
        detail = await self.client.get("/order-reviews-v1/new-review/completion-operation")
        self.assertEqual(detail.status_code, 200, detail.text)
        self.assertEqual(detail.json()["operation"]["state"], "requires_review")

    async def test_failed_commit_resumes_same_identity_visible_once(self):
        op = await self.pending()
        await worker.run_once(self.db)
        await self.assert_completed(op)
        self.assertEqual(await worker.run_once(self.db), 0)
        await self.assert_completed(op)

    async def test_two_workers_one_completion_lease(self):
        op = await self.pending()
        await asyncio.gather(worker.run_once(self.db), worker.run_once(self.db))
        await self.assert_completed(op)
        self.assertEqual((await self.saved())["resume_attempts"], 1)

    async def test_product_change_is_blocked(self):
        await self.pending()
        await self.db.unified_orders.update_one({"order_number": "new-review"},
            {"$set": {"raw_by_source.salla_direct.items.0.quantity": 9}})
        await worker.run_once(self.db)
        await self.assert_blocked()

    async def test_component_configuration_change_is_blocked(self):
        await self.pending()
        guarded = AcceptanceConfigDatabase(self.db)
        await guarded.mezan_product_resource_bindings_v2.update_one(
            {"user_id": "owner", "id": "recipe-material"}, {"$set": {"quantity": 9}})
        await worker.run_once(self.db)
        await self.assert_blocked("component_acceptance_changed")

    async def test_acceptance_version_change_is_blocked(self):
        await self.pending()
        await self.db.order_review_acceptance_config_versions.update_one({"_id": "owner"}, {"$inc": {"version": 1}})
        await worker.run_once(self.db)
        await self.assert_blocked("component_acceptance_changed")

    async def test_cancelled_order_is_blocked(self):
        await self.pending()
        await self.db.unified_orders.update_one({"order_number": "new-review"},
            {"$set": {"raw_by_source.salla_direct.status": {"slug": "canceled"}}})
        await worker.run_once(self.db)
        await self.assert_blocked()

    async def test_revision_change_is_blocked(self):
        await self.pending()
        await self.db[completion.WORKFLOWS].insert_one({"user_id": "owner", "order_number": "new-review",
            "revision": 7, "stage": "pending_review"})
        await worker.run_once(self.db)
        await self.assert_blocked("review_completion_snapshot_changed")

    async def test_transient_failure_backoff_then_success(self):
        op = await self.pending()
        self.provider.return_value = ("failed", "temporary")
        await worker.run_once(self.db)
        self.assertEqual((await self.saved())["state"], "provider_confirmed")
        self.assertEqual(await worker.run_once(self.db), 0)
        await self.due()
        self.provider.return_value = ("sent", None)
        await worker.run_once(self.db)
        await self.assert_completed(op)

    async def test_persistent_failure_has_finite_budget(self):
        await self.pending()
        self.provider.return_value = ("failed", "temporary")
        for _ in range(worker.MAX_ATTEMPTS):
            await self.due()
            await worker.run_once(self.db)
        await self.assert_blocked("salla_review_status_sync_failed")
        self.assertEqual((await self.saved())["resume_attempts"], worker.MAX_ATTEMPTS)

    async def test_legacy_operations_are_never_selected(self):
        await self.pending()
        await self.db[completion.OPERATIONS].update_many({}, {"$unset": {"auto_resume_version": ""}})
        self.assertEqual(await worker.run_once(self.db), 0)
        self.assertEqual((await self.saved())["state"], "provider_confirmed")

    async def test_missing_evidence_is_blocked(self):
        await self.pending()
        await self.db[completion.OPERATIONS].update_many({}, {"$unset": {"source_fingerprint": ""}})
        await worker.run_once(self.db)
        await self.assert_blocked("review_resume_evidence_missing")

    async def test_revoked_actor_is_blocked(self):
        await self.pending()
        await self.db.users.update_one({"id": "owner"}, {"$set": {"disabled": True}})
        await worker.run_once(self.db)
        await self.assert_blocked("review_actor_unavailable")

    async def test_server_worker_restart_resumes_persisted_operation(self):
        op = await self.pending()
        # Exercise the same start/stop hooks used by server process startup.
        task = await worker.start_worker(self.db)
        await worker.stop_worker(task)
        task = await worker.start_worker(self.db)
        try:
            for _ in range(100):
                if (await self.saved())["state"] == "completed":
                    break
                await asyncio.sleep(.05)
            await self.assert_completed(op)
        finally:
            await worker.stop_worker(task)

    async def test_manual_retry_races_worker(self):
        op = await self.pending()
        results = await asyncio.gather(worker.run_once(self.db),
            self.client.post("/order-reviews-v1/new-review/complete", json={"expected_revision": 0}))
        self.assertIn(results[1].status_code, (200, 409))
        await self.assert_completed(op)

    async def test_prepared_and_syncing_survive_process_death(self):
        op = await self.pending()
        # These states are committed before provider I/O; retain the same
        # immutable operation and simulate an expired process lease.
        for state in ("prepared", "syncing"):
            with self.subTest(state=state):
                await self.db[completion.WORKFLOWS].delete_many({})
                await self.db[completion.EVENTS].delete_many({})
                restored = deepcopy(op)
                restored.update(state=state, lease_until="", lease_token=None)
                await self.db[completion.OPERATIONS].replace_one({"_id": op["_id"]}, restored)
                await worker.run_once(self.db)
                await self.assert_completed(op)

    async def test_worker_process_restart_uses_persisted_operation(self):
        op = await self.pending()
        # Two separate Python processes use the same disposable replica DB.
        # First process dies before its first cycle; replacement uses the real
        # startup hook and engine, with only provider transport substituted.
        script = '''
import asyncio, sys
from motor.motor_asyncio import AsyncIOMotorClient
import order_review_resume_worker as worker
import order_review_routes as review
async def main():
    client = AsyncIOMotorClient(sys.argv[1])
    db = client[sys.argv[2]]
    async def provider(*args): return "sent", None
    review._sync_salla_reviewed = provider
    if sys.argv[3] == "first":
        async def held(db): await asyncio.sleep(300)
        worker.run_once = held
    task = await worker.start_worker(db)
    print("started", flush=True)
    try:
        if sys.argv[3] == "first": await asyncio.sleep(300)
        else:
            for _ in range(200):
                op = await db.order_review_completion_operations.find_one({})
                if op["state"] == "completed": return
                await asyncio.sleep(.05)
            raise RuntimeError("worker did not complete")
    finally:
        await worker.stop_worker(task)
        client.close()
asyncio.run(main())
'''
        async def launch(mode):
            return await asyncio.create_subprocess_exec(sys.executable, "-c", script,
                os.environ["MZ2_TEST_MONGO_URI"], self.db.name, mode,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        first = await launch("first")
        try:
            self.assertEqual((await asyncio.wait_for(first.stdout.readline(), 30)).strip(), b"started")
        finally:
            first.kill()
            await first.communicate()
        second = await launch("second")
        try:
            output, error = await asyncio.wait_for(second.communicate(), 45)
            self.assertEqual(second.returncode, 0, error.decode())
        finally:
            if second.returncode is None:
                second.kill()
                await second.communicate()
        await self.assert_completed(op)

    async def test_component_authoritative_refresh_required_is_blocked(self):
        await self.pending()
        await self.db.unified_orders.update_one({"order_number": "new-review"},
            {"$set": {"g47_salla_snapshot.requires_authoritative_refresh": True}})
        await worker.run_once(self.db)
        await self.assert_blocked("component_authoritative_refresh_required")

    async def test_newer_component_source_is_not_overwritten(self):
        await self.pending()
        await self.db.mezan_component_order_lifecycle_v1.update_one(
            {"user_id": "owner", "order_number": "new-review"},
            {"$set": {"source_updated_at": "2027-01-01T00:00:00+00:00"}, "$inc": {"generation": 1}})
        await worker.run_once(self.db)
        await self.assert_blocked("component_source_event_stale")

    async def test_crashed_last_attempt_is_terminal_after_lease_expiry(self):
        await self.pending()
        await self.db[completion.OPERATIONS].update_many({}, {"$set": {
            "resume_attempts": worker.MAX_ATTEMPTS, "resume_claim_until": "", "lease_until": ""}})
        await worker.run_once(self.db)
        await self.assert_blocked("review_resume_attempts_exhausted")

    async def test_lost_provider_response_worker_reads_back_without_duplicate_post(self):
        self.provider_patch.stop()
        payload = self.source_payload(number="new-review")
        await self.webhook(payload)
        await self.db.create_collection(completion.EVENTS)
        await self.db.command({"collMod": completion.EVENTS,
            "validator": {"event_type": {"$ne": "order_review_completed"}}, "validationLevel": "strict"})
        provider = deepcopy(payload)
        posts = 0
        async def transport(db, owner, method, path, **kwargs):
            nonlocal posts
            if method == "POST":
                self.assertEqual(path, "/orders/new-review/status")
                posts += 1
                provider["status"]["customized"] = {"name": "تم المراجعة"}
                raise httpx.ReadTimeout("synthetic response loss")
            if path == "/products/p":
                return {"data": {"id": "p", "name": "Synthetic product", "sku": "SYN-P"}}
            if path == "/products": return {"data": []}
            if path == "/orders/items": return {"data": deepcopy(provider["items"])}
            if path == "/orders/statuses": return {"data": [{"id": 12, "name": "تم المراجعة"}]}
            self.assertEqual(path, "/orders/new-review")
            return {"data": deepcopy(provider)}
        with patch.object(review, "call_salla", transport):
            response = await self.client.post("/order-reviews-v1/new-review/complete", json={"expected_revision": 0})
            self.assertEqual(response.status_code, 500, response.text)
            op = await self.saved()
            self.assertEqual(op["state"], "provider_confirmed")
            await self.db.command({"collMod": completion.EVENTS, "validator": {}})
            await worker.run_once(self.db)
            await worker.run_once(self.db)
        self.assertEqual(posts, 1)
        await self.assert_completed(op)

    async def test_active_manual_lease_is_not_stolen_or_budgeted(self):
        op = await self.pending()
        entered, release = asyncio.Event(), asyncio.Event()
        async def held(*args):
            entered.set()
            await release.wait()
            return "sent", None
        self.provider.side_effect = held
        task = asyncio.create_task(self.client.post("/order-reviews-v1/new-review/complete",
                                                    json={"expected_revision": 0}))
        try:
            await asyncio.wait_for(entered.wait(), 20)
            self.assertEqual(await worker.run_once(self.db), 0)
            self.assertEqual((await self.saved())["resume_attempts"], 0)
        finally:
            release.set()
        self.assertEqual((await task).status_code, 200)
        await self.assert_completed(op)
