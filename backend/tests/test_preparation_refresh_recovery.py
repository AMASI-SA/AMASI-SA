"""Explicit refresh contracts plus real Mongo/approval/source continuation.

The integration class requires the repository's disposable loopback replica
fixture. Provider transport and authentication are synthetic; source persistence,
approval/allocation proof, authorization reads and owner transactions are real.
"""
import asyncio
from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

import mobile_order_refresh as mobile
import order_engine.routes as orders
import order_engine.salla_refresh as source_refresh
import operational_atomic
import preparation_refresh_recovery as recovery
import preparation_piece_operations as pieces
import review_direct_preparation_sync as direct
import test_review_direct_preparation_sync as direct_fixture


NUMBER = "synthetic-refresh-recovery"
FRESH = {
    "ok": True, "found": True, "updated": True, "created": False,
    "skipped": False, "order_number": NUMBER,
    "source": "orders_v2_central_salla_refresh",
    "refreshed_at": "2026-11-15T22:00:00+00:00",
}


class ExplicitRefreshContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_mobile_refresh_returns_guarded_preparation_continuation(self):
        owner = {"id": "owner", "role": "owner", "name": "Synthetic owner"}
        db = SimpleNamespace(users=SimpleNamespace(find_one=AsyncMock(return_value=owner)))
        refreshed = deepcopy(FRESH)
        recovered = {**refreshed, "current_stage": "in_progress",
                     "preparation_status_sync": {"state": "sent", "retry_post": False}}
        with patch.object(mobile, "refresh_order_from_salla", AsyncMock(return_value=refreshed)), \
                patch.object(direct, "reconcile_direct_preparation_after_review", AsyncMock(return_value=recovered)):
            result = await mobile.refresh_mobile_order(db, owner, NUMBER, page="app.page.orders")
        self.assertEqual(result.get("current_stage"), "in_progress")
        self.assertEqual(result["preparation_status_sync"]["state"], "sent")
        self.assertTrue(result["updated"])

    async def recover(self, result, authorize, db=None):
        return await recovery.recover_preparation_after_refresh(
            db, user_id="owner", order_number=NUMBER,
            actor={"id": "employee", "name": "Synthetic employee"},
            result=result, authorize=authorize,
        )

    async def test_cached_failed_stale_or_foreign_refresh_never_starts_recovery(self):
        for change in (
            {"ok": False}, {"found": False}, {"updated": False},
            {"created": True}, {"skipped": True}, {"blocked": True},
            {"order_number": "another-order"}, {"source": "other_source"},
            {"refreshed_at": None}, {"ok": "true"},
        ):
            with self.subTest(change=change):
                refreshed = {**FRESH, **change}
                before = deepcopy(refreshed)
                authorize = AsyncMock()
                with patch.object(direct, "reconcile_direct_preparation_after_review", AsyncMock()) as reconcile:
                    self.assertEqual(await self.recover(refreshed, authorize), before)
                reconcile.assert_not_awaited()
                authorize.assert_not_awaited()
                self.assertEqual(refreshed, before)

    async def test_pending_recovery_preserves_successful_source_refresh(self):
        refreshed = {**FRESH, "address_found": True,
                     "auto_fulfillment": {"attempted": False, "promoted": False}}
        before = deepcopy(refreshed)
        pending = {"state": "pending", "reason": "preparation_routing_incomplete",
                   "retry_post": False, "reconcile": "get_only"}
        authorize = AsyncMock()
        with patch.object(direct, "reconcile_direct_preparation_after_review",
                          AsyncMock(return_value={**refreshed, "preparation_status_sync": pending})) as reconcile:
            result = await self.recover(refreshed, authorize)
        self.assertTrue(result["ok"] and result["found"] and result["updated"])
        self.assertEqual(result["preparation_status_sync"], pending)
        self.assertNotIn("current_stage", result)
        self.assertEqual(refreshed, before)
        self.assertEqual(reconcile.await_args.kwargs["actor"]["id"], "employee")
        self.assertIs(reconcile.await_args.kwargs["allow_dispatch"], False)
        self.assertIs(reconcile.await_args.kwargs["authorize"], authorize)

    async def test_recovery_read_failure_does_not_erase_applied_source_result(self):
        authorize = AsyncMock()
        with patch.object(direct, "reconcile_direct_preparation_after_review",
                          AsyncMock(side_effect=RuntimeError("private transport detail"))):
            result = await self.recover(deepcopy(FRESH), authorize)
        self.assertTrue(result["ok"] and result["updated"])
        self.assertEqual(result["preparation_status_sync"]["state"], "pending")
        self.assertEqual(result["preparation_status_sync"]["reason"], "RuntimeError")
        self.assertNotIn("private transport detail", str(result))
        self.assertNotIn("current_stage", result)

    async def test_revoked_actor_cannot_start_recovery(self):
        authorize = AsyncMock(side_effect=HTTPException(403, detail={"code": "revoked"}))
        with patch.object(direct, "reconcile_direct_preparation_after_review", AsyncMock()) as reconcile:
            with self.assertRaises(HTTPException) as denied:
                await self.recover(deepcopy(FRESH), authorize)
        self.assertEqual(denied.exception.status_code, 403)
        reconcile.assert_not_awaited()

    async def test_late_authorization_denial_is_not_converted_to_pending(self):
        for denied_at in ("transaction", "response"):
            with self.subTest(denied_at=denied_at):
                denial = HTTPException(403, detail={"code": "mobile_actor_unavailable"})
                authorize = AsyncMock(side_effect=[None, denial] if denied_at == "response" else None)
                reconcile = AsyncMock(
                    side_effect=denial if denied_at == "transaction" else None,
                    return_value={**FRESH, "current_stage": "in_progress"},
                )
                with patch.object(direct, "reconcile_direct_preparation_after_review", reconcile):
                    with self.assertRaises(HTTPException) as caught:
                        await self.recover(deepcopy(FRESH), authorize)
                self.assertEqual(caught.exception.status_code, 403)
                self.assertEqual(caught.exception.detail["code"], "mobile_actor_unavailable")

    async def test_owner_refresh_denies_missing_deleted_or_demoted_actor(self):
        from fastapi import FastAPI
        from httpx import ASGITransport, AsyncClient

        owner = {"id": "owner", "role": "owner"}
        for live in (None, {**owner, "deleted_at": "synthetic"},
                     {**owner, "role": "employee"}):
            with self.subTest(live=live):
                db = SimpleNamespace(users=SimpleNamespace(find_one=AsyncMock(return_value=live)))
                app = FastAPI()
                app.include_router(orders.make_order_engine_router(db, lambda: owner))
                with patch.object(orders, "refresh_order_from_salla", AsyncMock(return_value=deepcopy(FRESH))), \
                        patch.object(direct, "reconcile_direct_preparation_after_review", AsyncMock()) as reconcile:
                    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://synthetic") as client:
                        reply = await client.post(f"/orders-v2/{NUMBER}/refresh-from-salla")
                self.assertEqual(reply.status_code, 403, reply.text)
                reconcile.assert_not_awaited()


class PreparationRefreshRecoveryIntegrationTests(unittest.IsolatedAsyncioTestCase):
    """Exercise actual refresh routes and owner transactions; never live data."""
    asyncTearDown = direct_fixture.DirectPreparationSyncTests.asyncTearDown
    source_payload = direct_fixture.DirectPreparationSyncTests.source_payload
    webhook = direct_fixture.DirectPreparationSyncTests.webhook
    on_hand = direct_fixture.DirectPreparationSyncTests.on_hand
    mount_assignment = direct_fixture.DirectPreparationSyncTests.mount_assignment
    catalog_cards = direct_fixture.DirectPreparationSyncTests.catalog_cards
    approve = direct_fixture.DirectPreparationSyncTests.approve
    workflow = direct_fixture.DirectPreparationSyncTests.workflow
    posts = direct_fixture.DirectPreparationSyncTests.posts

    async def asyncSetUp(self):
        await direct_fixture.DirectPreparationSyncTests.asyncSetUp(self)
        self.number = "explicit-preparation-refresh"
        self.source_status = "in_progress"
        self.source_calls = []
        self.readback_hook = None
        self.after_source = None
        canonical_refresh = source_refresh.refresh_order_from_salla

        async def refresh_and_capture(*args, **kwargs):
            result = await canonical_refresh(*args, **kwargs)
            self.after_source = await self.snapshot()
            return result

        async def bank(_db, _owner, document):
            return document

        for replacement in (
            patch.object(source_refresh, "call_salla", side_effect=self.source_provider),
            patch.object(source_refresh, "_enrich_order_receiving_bank", side_effect=bank),
            patch.object(mobile, "refresh_order_from_salla", side_effect=refresh_and_capture),
            patch.object(orders, "refresh_order_from_salla", side_effect=refresh_and_capture),
        ):
            replacement.start()
            self.patches.append(replacement)
            self.addCleanup(replacement.stop)
        self.app.include_router(orders.make_order_engine_router(self.db, lambda: self.actor))

    async def provider(self, db, owner, method, path, **kwargs):
        self.assertEqual(method, "GET", "explicit recovery must never dispatch a provider status")
        if self.readback_hook is not None and path == "/orders/" + self.number:
            await self.readback_hook()
        return await direct_fixture.DirectPreparationSyncTests.provider(self, db, owner, method, path, **kwargs)

    async def source_provider(self, db, owner, method, path, **kwargs):
        self.assertIsNone(operational_atomic._ACTIVE.get(), "source provider reads belong outside transactions")
        self.assertEqual((owner, method), ("owner", "GET"))
        self.source_calls.append((method, path))
        current = await self.db.unified_orders.find_one({"user_id": owner, "order_number": self.number})
        raw = deepcopy(current["raw_by_source"]["salla_direct"])
        if path == "/orders/items":
            self.assertEqual(kwargs.get("params"), {"order_id": self.number})
            return {"data": raw["items"]}
        self.assertEqual(path, "/orders/" + self.number)
        raw["status"] = {"slug": self.source_status,
                         "name": "قيد التنفيذ" if self.source_status == "in_progress" else self.source_status}
        raw["status_slug"] = self.source_status
        return {"data": raw}

    async def prepare(self, *, full=True, direct_route=True):
        await self.approve(mixed=True, direct_route=direct_route)
        workflow = await self.workflow()
        if direct_route:
            supplier = next(row for row in workflow["items"] if row["order_item_id"] != self.item_id)
            await self.db[pieces.PREPARATION_UNIT_ALLOCATIONS].insert_one({
                "user_id": "owner", "order_number": self.number,
                "order_item_id": supplier["order_item_id"], "unit_index": 1, "status": "committed",
            })
            moved, remaining = await pieces._assigned_reconcile_order_stage(
                self.db, user_id="owner", order_number=self.number, batch_id="first-batch", actor=self.actor)
            self.assertFalse(moved)
            self.assertEqual(remaining, 1)
            if full:
                # Model a crash/failure after the last allocation commits and
                # before the existing post-batch continuation can finish.
                await self.db[pieces.PREPARATION_UNIT_ALLOCATIONS].insert_one({
                    "user_id": "owner", "order_number": self.number,
                    "order_item_id": supplier["order_item_id"], "unit_index": 2, "status": "committed",
                })
            await self.db[pieces.BATCHES].insert_one({"user_id": "owner", "id": "second-batch",
                "status": "ready", "reconciliation_required": [self.number]})
            await self.db[pieces.PIECES].insert_many([
                {"user_id": "owner", "order_number": self.number, "piece_id": "synthetic-received",
                 "order_item_id": supplier["order_item_id"], "unit_index": 1,
                 "status": "received", "execution_status": "received_from_supplier", "services": []},
                {"user_id": "owner", "order_number": self.number, "piece_id": "synthetic-unstarted",
                 "order_item_id": supplier["order_item_id"], "unit_index": 2,
                 "status": "assigned", "execution_status": "not_started", "services": [{"status": "pending"}]},
            ])
            self.assertEqual((await self.workflow())["preparation_progress"]["allocated_quantity"], 1)
        await self.db.general_ledger.insert_one({"user_id": "owner", "id": "frozen-ledger", "amount": 100})
        self.remote_status = "in_progress"
        self.provider_calls.clear()
        self.source_calls.clear()

    async def select_actor(self, surface):
        if surface == "web":
            self.actor = {"id": "owner", "role": "owner", "name": "Synthetic owner"}
            return "owner", f"/orders-v2/{self.number}/refresh-from-salla"
        page = "app.page.orders" if surface == "orders" else "app.page.assembly_shipping"
        await self.db.users.update_one({"id": "employee"}, {"$set": {
            "id": "employee", "role": "employee", "created_by": "owner", "name": "Synthetic employee",
        }}, upsert=True)
        await self.db.mezan_mobile_app_access_v1.update_one(
            {"owner_user_id": "owner", "user_id": "employee"}, {"$set": {
                "enabled": True, "permissions": [page],
            }}, upsert=True)
        self.actor = {"id": "owner", "role": "owner", "name": "Merchant principal",
            "_session_client": "amasi_mobile", "_mobile_owner_id": "owner",
            "_mobile_actor_id": "employee", "_mobile_actor_name": "Synthetic employee"}
        prefix = "/orders-v2" if surface == "orders" else "/preparation-work-v1/assembly/orders"
        return "employee", f"{prefix}/{self.number}/refresh-from-salla"

    async def snapshot(self):
        selector = {"user_id": "owner", "order_number": self.number}
        allowed = {direct.WORKFLOWS, direct.EVENTS, "unified_orders", "mz2_atomic_owners"}
        protected = {
            name: await self.db[name].find({}).to_list(None)
            for name in await self.db.list_collection_names() if name not in allowed
        }
        return {"source": await self.db.unified_orders.find_one(selector),
                "workflow": await self.workflow(), "protected": protected,
                "events": await self.db[direct.EVENTS].find(selector).to_list(None)}

    async def assert_recovery_scope(self):
        after = await self.snapshot()
        self.assertEqual(after["protected"], self.after_source["protected"],
                         "recovery must not write allocations, receipts, pieces, stock, finance or approval receipts")
        before_source = deepcopy(self.after_source["source"])
        after_source = deepcopy(after["source"])
        for row in (before_source, after_source):
            for key in ("order_status", "order_status_slug", "preparation_status_verified_at"):
                row.pop(key, None)
            raw = (row.get("raw_by_source") or {}).get("salla_direct") or {}
            raw.pop("status", None)
            raw.pop("status_slug", None)
        self.assertEqual(after_source, before_source, "recovery may confirm status aliases, not replace source facts")
        for key in ("revision", "review_completion_operation_id", "items", "operational_items"):
            self.assertEqual(after["workflow"].get(key), self.after_source["workflow"].get(key))
        self.assertEqual(self.posts(), [])

    async def assert_surface_recovers(self, surface):
        await self.prepare()
        actor_id, path = await self.select_actor(surface)
        response = await self.client.post(path)
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertTrue(result["ok"] and result["found"] and result["updated"])
        self.assertEqual(result["preparation_status_sync"]["state"], "sent")
        self.assertEqual(result["current_stage"], "in_progress")
        workflow = await self.workflow()
        self.assertEqual(workflow["stage"], "in_progress")
        self.assertEqual(workflow["preparation_progress"]["allocated_quantity"], 2)
        self.assertEqual(workflow["preparation_progress"]["remaining_quantity"], 0)
        self.assertEqual(workflow["in_progress_by"], actor_id)
        event = await self.db[direct.EVENTS].find_one({"event_type": "order_moved_to_in_progress"})
        self.assertEqual(event["actor_id"], actor_id)
        self.assertEqual(len(self.source_calls), 2)
        self.assertEqual([call[:2] for call in self.provider_calls], [("GET", "/orders/" + self.number)])
        await self.assert_recovery_scope()
        return path

    async def test_mobile_orders_refresh_recovers_committed_allocations(self):
        await self.assert_surface_recovers("orders")

    async def test_mobile_assembly_refresh_recovers_committed_allocations(self):
        await self.assert_surface_recovers("assembly")

    async def test_owner_web_refresh_recovers_committed_allocations(self):
        await self.assert_surface_recovers("web")

    async def test_partial_allocation_returns_source_success_and_pending(self):
        await self.prepare(full=False)
        _, path = await self.select_actor("orders")
        response = await self.client.post(path)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["ok"] and response.json()["updated"])
        self.assertEqual(response.json()["preparation_status_sync"]["state"], "pending")
        self.assertNotIn("current_stage", response.json())
        self.assertEqual((await self.workflow())["stage"], "reviewed")
        self.assertEqual(self.provider_calls, [])
        await self.assert_recovery_scope()

    async def test_ineligible_workflows_make_no_extra_provider_read(self):
        await self.prepare()
        original = await self.workflow()
        _, path = await self.select_actor("orders")
        for change in ({"stage": "completed"}, {"stage": "ready_to_ship"},
                       {"stage": "cancelled"}, {"completion_mode": "unknown"},
                       {"review_completion_operation_id": "missing"}, {"items": []}):
            with self.subTest(change=change):
                await self.db[direct.WORKFLOWS].replace_one({"_id": original["_id"]}, {**original, **change})
                response = await self.client.post(path)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertTrue(response.json()["updated"])
                self.assertEqual(self.provider_calls, [])
                self.assertNotIn("current_stage", response.json())
                await self.assert_recovery_scope()

    async def test_provider_identity_and_terminal_observations_never_promote(self):
        await self.prepare()
        _, path = await self.select_actor("orders")
        for provider_state, foreign in (("completed", False), ("cancelled", False),
                                        ("shipping", False), ("future-status", False),
                                        ("in_progress", True), ("under_review", False)):
            with self.subTest(provider_state=provider_state, foreign=foreign):
                self.remote_status = provider_state
                self.foreign_provider_identity = foreign
                response = await self.client.post(path)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertTrue(response.json()["updated"])
                self.assertEqual(response.json()["preparation_status_sync"]["state"], "pending")
                self.assertNotIn("current_stage", response.json())
                workflow = await self.workflow()
                self.assertEqual(workflow["stage"], "reviewed")
                self.assertNotIn("salla_status_sync_state", workflow)
                await self.assert_recovery_scope()

        async def unavailable_readback():
            raise TimeoutError("private provider diagnostic")

        self.readback_hook = unavailable_readback
        response = await self.client.post(path)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["updated"])
        self.assertEqual(response.json()["preparation_status_sync"]["reason"], "TimeoutError")
        self.assertNotIn("private provider diagnostic", response.text)
        self.assertEqual((await self.workflow())["stage"], "reviewed")
        await self.assert_recovery_scope()

    async def test_revoked_actor_during_readback_prevents_local_commit(self):
        await self.prepare()
        for surface in ("orders", "web"):
            with self.subTest(surface=surface):
                _, path = await self.select_actor(surface)
                entered, release = asyncio.Event(), asyncio.Event()

                async def wait_for_revocation():
                    entered.set()
                    await asyncio.wait_for(release.wait(), 10)

                self.readback_hook = wait_for_revocation
                request = asyncio.create_task(self.client.post(path))
                try:
                    await asyncio.wait_for(entered.wait(), 10)
                    if surface == "orders":
                        await self.db.mezan_mobile_app_access_v1.update_one(
                            {"owner_user_id": "owner", "user_id": "employee"}, {"$set": {"enabled": False}})
                    else:
                        await self.db.users.update_one({"id": "owner"}, {"$set": {"role": "employee"}})
                finally:
                    release.set()
                response = await asyncio.wait_for(request, 10)
                self.assertEqual(response.status_code, 403, response.text)
                workflow = await self.workflow()
                self.assertEqual(workflow["stage"], "reviewed")
                self.assertNotIn("preparation_status_revision", workflow)
                self.assertEqual(await self.db[direct.EVENTS].count_documents({"event_type": "order_moved_to_in_progress"}), 0)
                self.assertEqual(self.posts(), [])

    async def test_newer_source_during_readback_is_not_overwritten(self):
        await self.prepare()
        _, path = await self.select_actor("orders")

        async def newer_source():
            await self.db.unified_orders.update_one({"user_id": "owner", "order_number": self.number}, {"$set": {
                "order_status": "cancelled", "order_status_slug": "cancelled",
                "raw_by_source.salla_direct.status": {"slug": "cancelled", "name": "cancelled"},
                "raw_by_source.salla_direct.status_slug": "cancelled",
            }})

        self.readback_hook = newer_source
        response = await self.client.post(path)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["preparation_status_sync"]["state"], "pending")
        self.assertEqual((await self.workflow())["stage"], "reviewed")
        source = await self.db.unified_orders.find_one({"user_id": "owner", "order_number": self.number})
        self.assertEqual(source["order_status_slug"], "cancelled")
        self.assertEqual(source["raw_by_source"]["salla_direct"]["status"]["slug"], "cancelled")
        self.assertEqual(self.posts(), [])

    async def test_repeated_explicit_refresh_preserves_approval_and_single_event(self):
        path = await self.assert_surface_recovers("orders")
        operation = await self.db[direct.OPERATIONS].find_one({"order_number": self.number})
        response = await self.client.post(path)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["preparation_status_sync"]["state"], "sent")
        self.assertEqual(await self.db[direct.OPERATIONS].find_one({"order_number": self.number}), operation)
        self.assertEqual(await self.db[direct.EVENTS].count_documents({"event_type": "order_moved_to_in_progress"}), 1)
        await self.assert_recovery_scope()

    async def test_completed_source_refresh_stays_successful_with_recovery_pending(self):
        await self.prepare()
        self.source_status = "completed"
        _, path = await self.select_actor("orders")
        response = await self.client.post(path)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["ok"] and response.json()["updated"])
        self.assertEqual(response.json()["preparation_status_sync"]["state"], "pending")
        self.assertNotIn("current_stage", response.json())
        self.assertEqual((await self.workflow())["stage"], "reviewed")
        self.assertEqual(self.provider_calls, [])
        source = await self.db.unified_orders.find_one({"user_id": "owner", "order_number": self.number})
        self.assertEqual(source["raw_by_source"]["salla_direct"]["status"]["slug"], "completed")
        await self.assert_recovery_scope()


if __name__ == "__main__":
    unittest.main()
