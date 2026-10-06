"""Real Mongo regression for EDIT racing a previously started transition."""
import unittest
from unittest.mock import patch
from fastapi import HTTPException
import fulfillment_lifecycle as lc
import preparation_piece_operations as ops
from tests import test_salla_order_change_reconciliation_mongo as fixture

class TransitionFenceTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.SallaChangeMongoTests.asyncSetUp
    cleanup = fixture.SallaChangeMongoTests.cleanup
    replay = fixture.SallaChangeMongoTests.replay
    baseline = fixture.SallaChangeMongoTests.baseline
    changed = fixture.SallaChangeMongoTests.changed

    async def receive(self, **kwargs):
        return await ops._receive_preparation_piece(self.db, user_id="owner", piece_id="piece-1",
            client_request_id="request-receive-1", actor_id="employee-1", actor_name="Employee", **kwargs)

    async def test_edit_commits_after_receive_started_before_transition_transaction_regression(self):
        await self.baseline()
        await self.db[lc.PIECES].update_one({"piece_id": "piece-1"}, {"$set": {"services": []}})
        original_owner, original_check = ops.operational_owner, ops.enforce_stage_instructions
        triggered = False
        async def edit():
            nonlocal triggered
            if not triggered:
                triggered = True
                await self.replay(self.changed(options={"color": "black"}))
        async def owner(db, user, callback, **kwargs):
            await edit()
            return await original_owner(db, user, callback, **kwargs)
        async def old_check(*args, **kwargs):
            await original_check(*args, **kwargs)
            await edit()
        before = await self.db[lc.PIECES].find_one({"piece_id": "piece-1"})
        with patch.object(ops, "operational_owner", owner), patch.object(ops, "enforce_stage_instructions", old_check):
            with self.assertRaises(HTTPException):
                await self.receive()
        self.assertTrue(triggered)
        self.assertEqual(await self.db[lc.PIECES].find_one({"piece_id": "piece-1"}), before)
        self.assertEqual(await self.db[ops.PIECE_EVENTS].count_documents({}), 0)

    async def test_receive_stale_revision_and_generation(self):
        await self.baseline()
        for kwargs in [{"expected_revision": 999}, {"expected_generation": "stale"}]:
            with self.subTest(kwargs=kwargs), self.assertRaises(HTTPException):
                await self.receive(**kwargs)

    async def test_unread_edit_notification_blocks_receive(self):
        await self.baseline()
        await self.replay(self.changed(options={"color": "black"}))
        with self.assertRaises(HTTPException):
            await self.receive()


    async def test_transaction_serializes_employee_receive_and_webhook(self):
        import asyncio
        from contextvars import Context
        import preparation_transition_fence as fence
        await self.baseline()
        entered, release = asyncio.Event(), asyncio.Event()
        original = fence.assert_transition_current
        async def paused(*args, **kwargs):
            result = await original(*args, **kwargs)
            entered.set()
            await release.wait()
            return result
        with patch.object(fence, "assert_transition_current", paused):
            receiving = asyncio.create_task(self.receive())
            await asyncio.wait_for(entered.wait(), 5)
            editing = asyncio.create_task(self.replay(self.changed(options={"color": "black"})), context=Context())
            await asyncio.sleep(.1)
            self.assertFalse(editing.done())
            release.set()
            result, change = await asyncio.wait_for(asyncio.gather(receiving, editing), 15)
        self.assertTrue(result["ok"])
        self.assertIn(change["status"], {"pending_application", "awaiting_execution_resolution", "exception_required"})
        with self.assertRaises(HTTPException):
            await self.receive()

    async def test_receive_piece_event_failure_rolls_back_piece_and_workflow(self):
        from pymongo.errors import OperationFailure
        await self.baseline()
        await self.db.create_collection(ops.PIECE_EVENTS, validator={"forbidden": {"$exists": True}})
        before_piece = await self.db[lc.PIECES].find_one({"piece_id": "piece-1"})
        before_workflow = await self.db[lc.WORKFLOWS].find_one({})
        with self.assertRaises(OperationFailure):
            await self.receive()
        self.assertEqual(before_piece, await self.db[lc.PIECES].find_one({"piece_id": "piece-1"}))
        self.assertEqual(before_workflow, await self.db[lc.WORKFLOWS].find_one({}))

    async def test_edit_event_blocks_even_if_hold_projection_missing(self):
        await self.baseline()
        await self.replay(self.changed(options={"color": "black"}))
        await self.db[lc.HOLDS].delete_many({})
        with self.assertRaises(HTTPException) as failure:
            await self.receive()
        self.assertEqual(failure.exception.detail["code"], "preparation_edit_pending")

    async def test_inactive_old_piece_states_rejected(self):
        await self.baseline()
        for field, value in [("obsolete", True), ("replaced_by", "new"), ("cancelled", True),
                             ("active", False), ("current", False)]:
            await self.db[lc.PIECES].update_one({"piece_id": "piece-1"}, {"$set": {field: value}})
            with self.subTest(field=field), self.assertRaises(HTTPException):
                await self.receive()
            await self.db[lc.PIECES].update_one({"piece_id": "piece-1"}, {"$unset": {field: ""}})

    async def test_start_preparation_rechecks_file_and_piece_inside_transaction(self):
        await self.baseline()
        registry = {"user_id": "owner", "file_number": "file-1", "batch_id": "batch-1", "status": "ready",
                    "execution_status": "assigned", "responsible_employee_id": "owner"}
        await self.db[ops.REGISTRY].insert_one(registry)
        await self.db[lc.PIECES].update_one({"piece_id": "piece-1"}, {"$set": {"batch_id": "batch-1", "status": "assigned"}})
        await self.replay(self.changed(options={"color": "black"}))
        with self.assertRaises(HTTPException):
            await ops._start_file_execution(self.db, user_id="owner", registry=registry,
                actor={"id": "owner", "role": "admin"}, note=None)
        self.assertEqual((await self.db[ops.REGISTRY].find_one({"file_number": "file-1"}))["execution_status"], "assigned")


    async def test_identity_change_between_entry_and_transaction_rejected(self):
        await self.baseline()
        original = ops.operational_owner
        async def change_identity(db, owner, callback, **kwargs):
            await self.db[lc.PIECES].update_one({"piece_id": "piece-1"}, {"$set": {"change_id": "newer-change"}})
            return await original(db, owner, callback, **kwargs)
        with patch.object(ops, "operational_owner", change_identity), self.assertRaises(HTTPException) as failure:
            await self.receive()
        self.assertEqual(failure.exception.detail["code"], "preparation_transition_identity_conflict")
        self.assertEqual((await self.db[lc.PIECES].find_one({"piece_id": "piece-1"}))["status"], "in_progress")

    async def test_start_preparation_audit_failure_rollback_and_uncertain_retry_blocked(self):
        from pymongo.errors import OperationFailure
        await self.baseline()
        registry = {"user_id": "owner", "file_number": "file-1", "batch_id": "batch-1", "status": "ready",
                    "execution_status": "assigned", "responsible_employee_id": "owner"}
        await self.db[ops.REGISTRY].insert_one(registry)
        await self.db[ops.BATCHES].insert_one({"user_id": "owner", "id": "batch-1", "lines": []})
        await self.db[lc.PIECES].update_one({"piece_id": "piece-1"}, {"$set": {"batch_id": "batch-1", "status": "assigned"}})
        await self.db.create_collection(ops.PIECE_EVENTS, validator={"forbidden": {"$exists": True}})
        with self.assertRaises(OperationFailure):
            await ops._start_file_execution(self.db, user_id="owner", registry=registry,
                actor={"id": "owner", "role": "admin"}, note=None)
        self.assertEqual((await self.db[lc.PIECES].find_one({"piece_id": "piece-1"}))["status"], "assigned")
        self.assertEqual((await self.db[ops.REGISTRY].find_one({"file_number": "file-1"}))["execution_status"], "assigned")
        await self.db.command({"collMod": ops.PIECE_EVENTS, "validator": {}})
        with self.assertRaises(HTTPException) as failure:
            await ops._start_file_execution(self.db, user_id="owner", registry=registry,
                actor={"id": "owner", "role": "admin"}, note=None)
        self.assertEqual(failure.exception.detail["code"], "fulfillment_execution_in_flight")


    async def test_piece_block_markers_fail_closed_without_hold_projection(self):
        from operational_atomic import operational_owner
        from preparation_transition_fence import assert_transition_current
        await self.baseline()
        for field, value in [("status", "blocked"), ("execution_status", "blocked"), ("active_hold_id", "missing-manual")]:
            before = await self.db[lc.PIECES].find_one({"piece_id": "piece-1"})
            await self.db[lc.PIECES].update_one({"piece_id": "piece-1"}, {"$set": {field: value}})
            async def check(scoped):
                return await assert_transition_current(scoped, user_id="owner", piece_id="piece-1")
            with self.subTest(field=field), self.assertRaises(HTTPException) as failure:
                await operational_owner(self.db, "owner", check, profile="preparation_transition")
            self.assertEqual(failure.exception.detail["code"], "preparation_piece_blocked")
            await self.db[lc.PIECES].replace_one({"piece_id": "piece-1"}, before)
