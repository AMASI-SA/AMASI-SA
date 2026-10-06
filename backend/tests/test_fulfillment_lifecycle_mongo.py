"""Lifecycle overlay acceptance against an isolated, real local replica set.

Run with unittest to avoid the repository's application-env pytest conftest.
No service, transaction, database, or execution-claim test doubles are used.
"""
import asyncio
import os
import unittest
from copy import deepcopy
from uuid import uuid4
from unittest.mock import patch

from fastapi import HTTPException
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.uri_parser import parse_uri

import fulfillment_lifecycle as lifecycle


class LifecycleMongoTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        uri = os.environ.get("MZ2_TEST_MONGO_URI", "")
        if not uri:
            self.skipTest("MZ2_TEST_MONGO_URI required; no application database fallback")
        parsed = parse_uri(uri)
        self.assertTrue(all(host in {"127.0.0.1", "localhost", "::1"}
                            for host, _ in parsed["nodelist"]))
        self.assertFalse(parsed["username"] or parsed["password"] or parsed["database"])
        self.mongo = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=5000, tz_aware=True)
        self.addAsyncCleanup(self.cleanup_database)
        hello = await self.mongo.admin.command("hello")
        self.assertTrue(hello.get("setName"))
        self.assertTrue(hello.get("isWritablePrimary"))
        self.db = self.mongo["lifecycle_pr1_" + uuid4().hex]
        self.flag = patch.dict(os.environ, {"ORDER_FULFILLMENT_LIFECYCLE_CONTROLS_ENABLED": "true"})
        self.flag.start()
        self.addCleanup(self.flag.stop)
        self.context = {"merchant_id": "owner", "actor_id": "owner", "is_owner": True,
                        "permissions": set(), "actor_name": "Synthetic operator"}
        await self.db.mz2_atomic_owners.insert_one({
            "_id": "owner", "revision": 0, "writes_paused": False, "control_revision": 0,
        })
        await self.db[lifecycle.WORKFLOWS].insert_one({
            "user_id": "owner", "order_number": "order-1", "stage": "in_progress", "revision": 0,
            "items": [{"order_item_id": "item-1"}, {"order_item_id": "item-2"}],
        })
        await self.db[lifecycle.PIECES].insert_many([
            {"user_id": "owner", "order_number": "order-1", "piece_id": "piece-1", "id": "piece-1",
             "order_item_id": "item-1", "status": "in_progress", "assembly_status": "pending"},
            {"user_id": "owner", "order_number": "order-1", "piece_id": "piece-2", "id": "piece-2",
             "order_item_id": "item-2", "status": "ready_for_assembly", "assembly_status": "pending"},
        ])
        await self.db[lifecycle.PIECES].update_many({}, {"$set": {
            "generation": 2, "revision": 1, "current": True, "active": True, "unit_index": 1}})

    async def cleanup_database(self):
        if hasattr(self, "db"):
            self.assertTrue(self.db.name.startswith("lifecycle_pr1_"))
            await self.mongo.drop_database(self.db.name)
        if hasattr(self, "mongo"):
            self.mongo.close()

    async def caps(self, context=None):
        return await lifecycle.capabilities(self.db, user_id="owner", order_number="order-1",
                                            context=self.context if context is None else context)

    async def payload(self, **changes):
        caps = await self.caps()
        result = {"scope": "order", "stop_type": "note", "reason": "Synthetic hold reason",
                  "idempotency_key": uuid4().hex, "expected_revision": caps["revision"],
                  "expected_generation": caps["generation"]}
        result.update(changes)
        return result

    async def hold(self, payload=None, *, context=None, db=None):
        return await lifecycle.create_hold(self.db if db is None else db, user_id="owner",
            order_number="order-1", context=self.context if context is None else context,
            payload=payload if payload is not None else await self.payload())

    async def resume(self, hold_id, payload=None):
        body = await self.payload(reason="Synthetic resume reason") if payload is None else payload
        return await lifecycle.resume_hold(self.db, user_id="owner", hold_id=hold_id,
                                            context=self.context, payload=body)

    async def pieces(self):
        return await self.db[lifecycle.PIECES].find({}, {"_id": 0}).sort("piece_id", 1).to_list(20)

    async def assert_denied(self, call, status=409):
        with self.assertRaises(HTTPException) as caught:
            await call
        self.assertEqual(caught.exception.status_code, status, str(caught.exception.detail))

    async def test_order_hold_and_resume_preserve_mixed_piece_history(self):
        before = await self.pieces()
        initial = await self.caps()
        result = await self.hold()
        self.assertTrue(result["ok"])
        self.assertGreater(result["revision"], initial["revision"])
        self.assertEqual(result["hold"]["scope"], "order")
        self.assertEqual(result["hold"]["status"], "active")
        self.assertEqual(await self.pieces(), before)
        self.assertEqual(len((await self.caps())["active_holds"]), 1)
        resumed = await self.resume(result["hold"]["id"])
        self.assertNotEqual(resumed["hold"]["status"], "active")
        self.assertEqual((await self.caps())["active_holds"], [])
        self.assertEqual(await self.pieces(), before)
        self.assertEqual(await self.db[lifecycle.AUDIT].count_documents({}), 2)

    async def test_item_and_piece_scopes_keep_other_pieces_unchanged(self):
        before = await self.pieces()
        for scope, target in [("item", "item-1"), ("piece", "piece-2")]:
            with self.subTest(scope=scope):
                result = await self.hold(await self.payload(scope=scope, target_id=target))
                self.assertEqual(result["hold"]["scope"], scope)
                self.assertEqual(result["hold"]["target_id"], target)
                self.assertEqual(await self.pieces(), before)
                await self.resume(result["hold"]["id"])

    async def test_resume_never_restores_stale_piece_status(self):
        result = await self.hold(await self.payload(scope="piece", target_id="piece-1"))
        await self.db[lifecycle.PIECES].update_one({"piece_id": "piece-1"},
                                                  {"$set": {"status": "cancelled"}})
        await self.resume(result["hold"]["id"])
        self.assertEqual((await self.db[lifecycle.PIECES].find_one({"piece_id": "piece-1"}))["status"], "cancelled")

    async def test_stale_revision_and_generation_are_rejected_without_writes(self):
        body = await self.payload()
        await self.assert_denied(self.hold({**body, "expected_revision": body["expected_revision"] + 7}))
        await self.assert_denied(self.hold({**body, "expected_generation": "0" * 64}))
        self.assertEqual(await self.db[lifecycle.HOLDS].count_documents({}), 0)
        self.assertEqual(await self.db[lifecycle.AUDIT].count_documents({}), 0)

    async def test_lost_response_retry_replays_exact_operation_once(self):
        body = await self.payload()
        original = await self.hold(deepcopy(body))  # Transport discards this successful response.
        replay = await self.hold(deepcopy(body))
        self.assertTrue(replay["idempotent_replay"])
        self.assertEqual(replay["hold"]["id"], original["hold"]["id"])
        self.assertEqual(replay["revision"], original["revision"])
        self.assertEqual(await self.db[lifecycle.HOLDS].count_documents({}), 1)
        self.assertEqual(await self.db[lifecycle.AUDIT].count_documents({}), 1)

    async def test_idempotency_key_cannot_change_payload(self):
        body = await self.payload()
        await self.hold(body)
        await self.assert_denied(self.hold({**body, "reason": "Different operation"}))
        self.assertEqual(await self.db[lifecycle.AUDIT].count_documents({}), 1)

    async def test_resume_retry_does_not_duplicate_audit_or_restore_status(self):
        held = await self.hold()
        body = await self.payload(reason="Resume after verification")
        original = await self.resume(held["hold"]["id"], body)
        replay = await self.resume(held["hold"]["id"], deepcopy(body))
        self.assertTrue(replay["idempotent_replay"])
        self.assertEqual(replay["revision"], original["revision"])
        self.assertEqual(await self.db[lifecycle.AUDIT].count_documents({}), 2)

    async def test_two_independent_clients_cannot_commit_same_revision(self):
        second = AsyncIOMotorClient(os.environ["MZ2_TEST_MONGO_URI"], serverSelectionTimeoutMS=5000)
        try:
            first_body = await self.payload(scope="piece", target_id="piece-1")
            second_body = {**first_body, "target_id": "piece-2", "idempotency_key": uuid4().hex}
            results = await asyncio.gather(self.hold(first_body), self.hold(second_body, db=second[self.db.name]),
                                            return_exceptions=True)
            self.assertEqual(sum(isinstance(value, dict) for value in results), 1, repr(results))
            errors = [value for value in results if isinstance(value, Exception)]
            self.assertEqual(len(errors), 1)
            self.assertIsInstance(errors[0], HTTPException)
            self.assertEqual(errors[0].status_code, 409)
            self.assertEqual(await self.db[lifecycle.HOLDS].count_documents({}), 1)
            self.assertEqual(await self.db[lifecycle.AUDIT].count_documents({}), 1)
        finally:
            second.close()

    async def test_audit_failure_rolls_back_hold_request_and_revision(self):
        before = await self.caps()
        await self.db.create_collection(lifecycle.AUDIT)
        await self.db.command({"collMod": lifecycle.AUDIT,
            "validator": {"_id": {"$exists": False}}, "validationLevel": "strict", "validationAction": "error"})
        body = await self.payload()
        with self.assertRaises(Exception):
            await self.hold(body)
        self.assertEqual(await self.db[lifecycle.HOLDS].count_documents({}), 0)
        self.assertEqual(await self.db[lifecycle.REQUESTS].count_documents({}), 0)
        self.assertEqual((await self.caps())["revision"], before["revision"])
        await self.db.command({"collMod": lifecycle.AUDIT, "validator": {}})
        self.assertTrue((await self.hold(body))["ok"])

    async def test_unprivileged_employee_cannot_hold(self):
        employee = {"merchant_id": "owner", "actor_id": "employee", "is_owner": False, "permissions": set()}
        await self.assert_denied(self.hold(await self.payload(), context=employee), status=403)
        self.assertEqual(await self.db[lifecycle.HOLDS].count_documents({}), 0)

    async def test_employee_self_stop_requires_current_assignment_and_cannot_stop_order(self):
        employee = {"merchant_id": "owner", "actor_id": "employee", "is_owner": False,
                    "permissions": {lifecycle.SELF_STOP}}
        body = await self.payload(scope="piece", target_id="piece-1", stop_type="employee")
        await self.assert_denied(self.hold(body, context=employee), status=403)
        await self.db[lifecycle.PIECES].update_one({"piece_id": "piece-1"},
            {"$set": {"responsible_employee_id": "employee"}})
        held = await self.hold(body, context=employee)
        self.assertEqual(held["hold"]["created_by"], "employee")
        await self.assert_denied(self.hold(await self.payload(stop_type="employee"), context=employee), status=403)
        other = {**employee, "actor_id": "other-employee"}
        with self.assertRaises(HTTPException) as caught:
            await lifecycle.resume_hold(self.db, user_id="owner", hold_id=held["hold"]["id"],
                context=other, payload=await self.payload(reason="Unrelated employee tries resume"))
        self.assertEqual(caught.exception.status_code, 403)

    async def test_cross_merchant_actor_cannot_read_or_mutate(self):
        intruder = {**self.context, "merchant_id": "another-owner"}
        await self.assert_denied(self.caps(context=intruder), status=403)
        await self.assert_denied(self.hold(await self.payload(), context=intruder), status=403)
        self.assertEqual(await self.db[lifecycle.HOLDS].count_documents({}), 0)

    async def test_completed_stage_cannot_hold(self):
        await self.db[lifecycle.WORKFLOWS].update_one({}, {"$set": {"stage": "completed"}})
        await self.assert_denied(self.hold())
        self.assertEqual(await self.db[lifecycle.HOLDS].count_documents({}), 0)

    async def test_execution_claim_excludes_hold_until_worker_exits(self):
        target = {"order_number": "order-1", "order_item_id": "item-1", "piece_id": "piece-1"}
        async with lifecycle.execution_scope(self.db, user_id="owner", targets=[target], operation="synthetic-worker"):
            await self.assert_denied(self.hold())
            self.assertGreater(await self.db[lifecycle.EXECUTIONS].count_documents({}), 0)
        self.assertTrue((await self.hold())["ok"])

    async def test_active_hold_excludes_worker(self):
        await self.hold()
        target = {"order_number": "order-1", "order_item_id": "item-1", "piece_id": "piece-1"}
        with self.assertRaises(HTTPException) as caught:
            async with lifecycle.execution_scope(self.db, user_id="owner", targets=[target], operation="synthetic-worker"):
                self.fail("Held order entered execution")
        self.assertEqual(caught.exception.status_code, 409)

    async def test_item_hold_blocks_its_worker_but_allows_sibling_worker(self):
        await self.hold(await self.payload(scope="item", target_id="item-1"))
        held_target = {"order_number": "order-1", "order_item_id": "item-1", "piece_id": "piece-1"}
        other_target = {"order_number": "order-1", "order_item_id": "item-2", "piece_id": "piece-2"}
        with self.assertRaises(HTTPException):
            async with lifecycle.execution_scope(self.db, user_id="owner", targets=[held_target], operation="synthetic-worker"):
                self.fail("Held item entered execution")
        async with lifecycle.execution_scope(self.db, user_id="owner", targets=[other_target], operation="synthetic-worker"):
            self.assertEqual(await self.db[lifecycle.HOLDS].count_documents({"status": "active"}), 1)

    async def test_disabling_creation_flag_does_not_remove_existing_hold_fence(self):
        await self.hold()
        with patch.dict(os.environ, {"ORDER_FULFILLMENT_LIFECYCLE_CONTROLS_ENABLED": "false"}):
            with self.assertRaises(HTTPException):
                async with lifecycle.execution_scope(self.db, user_id="owner",
                    targets=[{"order_number": "order-1"}], operation="synthetic-worker"):
                    self.fail("Existing hold lost its execution fence after flag disable")

    async def test_flag_is_exact_and_disabled_mutation_fails_closed(self):
        body = await self.payload()
        for value in ("false", "TRUE", "1", ""):
            with self.subTest(value=value), patch.dict(os.environ, {"ORDER_FULFILLMENT_LIFECYCLE_CONTROLS_ENABLED": value}):
                self.assertFalse(lifecycle.enabled())
                with self.assertRaises(HTTPException):
                    await self.hold(body)
        self.assertEqual(await self.db[lifecycle.HOLDS].count_documents({}), 0)

    async def test_commercial_actions_denied_in_every_policy_stage(self):
        for stage in (*lifecycle.STAGES, "pending_review", "cancelled", "unknown"):
            with self.subTest(stage=stage):
                await self.db[lifecycle.WORKFLOWS].update_one({}, {"$set": {"stage": stage}})
                capabilities = await self.caps()
                self.assertFalse(capabilities["commercial_mutations_enabled"])
                for action in ("cancel_product", "edit_product", "add_product", "replace_product"):
                    self.assertFalse(capabilities["actions"][action]["allowed"])
                self.assertEqual(capabilities["actions"]["hold_order"]["allowed"], stage in lifecycle.HOLD_STAGES)

    async def test_noncurrent_cancelled_replaced_and_obsolete_pieces_cannot_execute(self):
        invalid_states = [{"status": "cancelled"}, {"status": "replaced"}, {"status": "obsolete"},
                          {"current": False}, {"active": False}]
        original = await self.db[lifecycle.PIECES].find_one({"piece_id": "piece-1"})
        for change in invalid_states:
            with self.subTest(change=change):
                await self.db[lifecycle.PIECES].replace_one({"piece_id": "piece-1"}, {**original, **change})
                await self.assert_denied(lifecycle.assert_piece_current(self.db,
                    user_id="owner", piece_id="piece-1"))
                with self.assertRaises(HTTPException):
                    async with lifecycle.execution_scope(self.db, user_id="owner",
                        targets=[{"order_number": "order-1", "order_item_id": "item-1", "piece_id": "piece-1"}],
                        operation="synthetic-current-piece-check"):
                        self.fail("Invalid piece entered execution")

    async def test_piece_revision_and_generation_fences(self):
        row = await self.db[lifecycle.PIECES].find_one({"piece_id": "piece-1"})
        generation = lifecycle.piece_generation(row)
        self.assertEqual(len(generation), 64)
        await lifecycle.assert_piece_current(self.db, user_id="owner", piece_id="piece-1",
            expected_revision=1, expected_generation=generation)
        for fences in ({"expected_revision": 0}, {"expected_generation": "0" * 64}):
            with self.subTest(fences=fences):
                await self.assert_denied(lifecycle.assert_piece_current(self.db,
                    user_id="owner", piece_id="piece-1", **fences))
                with self.assertRaises(HTTPException):
                    async with lifecycle.execution_scope(self.db, user_id="owner",
                        targets=[{"order_number": "order-1", "order_item_id": "item-1", "piece_id": "piece-1", **fences}],
                        operation="synthetic-stale-piece-check"):
                        self.fail("Stale piece entered execution")

    async def test_order_hold_blocks_piece_materialized_after_hold(self):
        held = await self.hold()
        await self.db[lifecycle.PIECES].insert_one({"user_id": "owner", "order_number": "order-1",
            "piece_id": "future-piece", "order_item_id": "future-item", "status": "in_progress",
            "generation": 2, "revision": 1, "current": True, "active": True, "unit_index": 1})
        self.assertNotIn("future-piece", held["hold"]["piece_ids"])
        with self.assertRaises(HTTPException):
            async with lifecycle.execution_scope(self.db, user_id="owner",
                targets=[{"order_number": "order-1", "order_item_id": "future-item", "piece_id": "future-piece"}],
                operation="synthetic-future-piece"):
                self.fail("Order hold failed to cover future piece")

    async def test_webhook_source_update_preserves_hold_and_invalidates_stale_resume(self):
        from fulfillment_v2_routes import persist_component_source_snapshot
        held = await self.hold()
        stale_resume = await self.payload(reason="Resume with earlier source snapshot")
        payload = {"id": 901, "reference_id": "order-1", "status": {"slug": "in_progress"},
                   "updated_at": "2026-10-06T10:00:00+00:00"}

        async def persist(scoped):
            await scoped.unified_orders.update_one({"user_id": "owner", "order_number": "order-1"},
                {"$set": {"order_status_slug": "in_progress", "raw_by_source": {"salla_direct": payload}}}, upsert=True)
            return {"synced": True}

        result = await persist_component_source_snapshot(self.db, user_id="owner", order_number="order-1",
            payload=payload, persist=persist)
        self.assertTrue(result["synced"])
        self.assertEqual((await self.caps())["active_holds"][0]["id"], held["hold"]["id"])
        self.assertNotEqual((await self.caps())["generation"], stale_resume["expected_generation"])
        await self.assert_denied(self.resume(held["hold"]["id"], stale_resume))
        self.assertTrue((await self.resume(held["hold"]["id"]))["ok"])

    async def test_concurrent_resume_and_new_hold_commit_only_one_revision(self):
        held = await self.hold(await self.payload(scope="piece", target_id="piece-1"))
        resume_payload = await self.payload(reason="Concurrent resume")
        new_payload = {**resume_payload, "scope": "piece", "target_id": "piece-2", "idempotency_key": uuid4().hex}
        results = await asyncio.gather(self.resume(held["hold"]["id"], resume_payload),
            self.hold(new_payload), return_exceptions=True)
        self.assertEqual(sum(isinstance(result, dict) for result in results), 1, repr(results))
        failures = [result for result in results if isinstance(result, Exception)]
        self.assertEqual(len(failures), 1)
        self.assertIsInstance(failures[0], HTTPException)
        self.assertEqual(failures[0].status_code, 409)
        self.assertEqual(await self.db[lifecycle.AUDIT].count_documents({}), 2)

    async def test_resume_audit_failure_preserves_active_hold_and_revision(self):
        held = await self.hold()
        body = await self.payload(reason="Resume after audit repair")
        await self.db.command({"collMod": lifecycle.AUDIT,
            "validator": {"event_type": {"$ne": "fulfillment_hold_resumed"}}, "validationLevel": "strict"})
        with self.assertRaises(Exception):
            await self.resume(held["hold"]["id"], body)
        self.assertEqual((await self.caps())["revision"], body["expected_revision"])
        self.assertEqual((await self.caps())["active_holds"][0]["id"], held["hold"]["id"])
        self.assertEqual(await self.db[lifecycle.REQUESTS].count_documents({}), 1)
        await self.db.command({"collMod": lifecycle.AUDIT, "validator": {}})
        self.assertTrue((await self.resume(held["hold"]["id"], body))["ok"])

    async def test_hold_resume_only_write_control_collections_and_immutable_audit(self):
        before_pieces = await self.pieces()
        held = await self.hold()
        first = await self.db[lifecycle.AUDIT].find_one({})
        self.assertEqual(first["actor_id"], "owner")
        self.assertEqual(first["reason"], "Synthetic hold reason")
        self.assertIsNotNone(first["occurred_at"].tzinfo)
        self.assertIn("before", first)
        self.assertIn("after", first)
        await self.resume(held["hold"]["id"])
        self.assertEqual(await self.db[lifecycle.AUDIT].find_one({"_id": first["_id"]}), first)
        self.assertEqual(await self.pieces(), before_pieces)
        allowed = {lifecycle.WORKFLOWS, lifecycle.PIECES, lifecycle.HOLDS, lifecycle.AUDIT,
                   lifecycle.REQUESTS, lifecycle.CONTROL_OWNERS, "mz2_atomic_owners"}
        nonempty = {name for name in await self.db.list_collection_names()
                    if await self.db[name].count_documents({})}
        self.assertLessEqual(nonempty, allowed)


if __name__ == "__main__":
    unittest.main()
