"""PR5 acceptance against a real isolated Mongo replica set; no application DB fallback."""
import asyncio
import os
import unittest
from copy import deepcopy
from unittest.mock import patch
from uuid import uuid4

from fastapi import HTTPException
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.errors import OperationFailure

import fulfillment_lifecycle as controls
import salla_order_change_reconciliation as intake
import salla_add_application as add
import salla_edit_application as application
import stock_component_consumption_service as stock
from tests import test_salla_add_application_mongo as fixtures


class SallaEditApplicationMongoTests(unittest.IsolatedAsyncioTestCase):
    cleanup = fixtures.SallaAddApplicationMongoTests.cleanup
    replay = fixtures.SallaAddApplicationMongoTests.replay

    async def asyncSetUp(self):
        await fixtures.SallaAddApplicationMongoTests.asyncSetUp(self)
        flags = patch.dict(os.environ, {"ORDER_SALLA_EDIT_APPLICATION_ENABLED": "true"})
        flags.start()
        self.addCleanup(flags.stop)
        await self.db[add.ALLOCATIONS].create_index(
            [("user_id", 1), ("order_number", 1), ("order_item_id", 1), ("unit_index", 1)], unique=True)
        payload = await fixtures.SallaAddApplicationMongoTests.payload(self)
        await add.apply_addition(self.db, user_id="owner", order_number="order-1", context=self.context, payload=payload)
        self.before_edit_pieces = await self.db[controls.PIECES].find({"order_item_id": "new"}).sort("unit_index", 1).to_list(20)
        if self._testMethodName == "test_native_missing_generation_revision_is_fenced_as_zero":
            await self.db[stock.UNITS].update_many({"order_line_id": "new"}, {"$unset": {"generation": ""}})
            await self.db[controls.PIECES].update_many({"order_item_id": "new"}, {"$unset": {"generation": "", "revision": ""}})
        self.changed = deepcopy(self.added)
        self.changed["version"] = "2040-01-02T12:00:00+00:00"
        self.changed["items"][1]["options"]["color"] = "black"
        result = await self.replay(self.changed)
        self.event = next(e for e in result["events"] if e["change_type"] == "edit_options")

    async def payload(self):
        caps = await controls.capabilities(self.db, user_id="owner", order_number="order-1", context=self.context)
        pieces = await self.db[controls.PIECES].find({"order_item_id": "new", "current": True}).sort("unit_index", 1).to_list(20)
        return {"event_id": self.event["event_id"], "employee_id": "employee-1", "reason": "Apply authoritative Salla option change",
                "idempotency_key": uuid4().hex, "expected_revision": caps["revision"], "expected_generation": caps["generation"],
                "units": [{key: piece.get(key, 0 if key in {"generation", "revision"} else None)
                           for key in ("order_item_id", "unit_index", "generation", "revision", "change_id")} for piece in pieces]}

    async def apply(self, payload=None, db=None):
        return await application.apply_edit(self.db if db is None else db, user_id="owner", order_number="order-1",
            context=self.context, payload=await self.payload() if payload is None else payload)

    async def state(self):
        # Include every collection so partial audit/outbox/receipt/assignment writes cannot escape the assertion.
        return {name: await self.db[name].find({}).sort("_id", 1).to_list(10000)
                for name in sorted(await self.db.list_collection_names()) if not name.startswith("system.")}

    async def assert_failure_unchanged(self, payload=None):
        before = await self.state()
        with self.assertRaises(HTTPException):
            await self.apply(payload)
        self.assertEqual(await self.state(), before)

    def assert_replay_business_state_unchanged(self, after, before):
        # operational_owner serializes even receipt reads using an internal fence.
        # Its counter may advance; no business artifact may change on replay.
        after = {k: v for k, v in after.items() if k != "mz2_atomic_owners"}
        before = {k: v for k, v in before.items() if k != "mz2_atomic_owners"}
        self.assertEqual(after, before)

    async def test_happy_path_new_generation_options_and_unrelated_unit_preserved(self):
        old = await self.db[controls.PIECES].find_one({"piece_id": "old-piece"})
        unrelated = await self.db[stock.UNITS].find_one({"order_line_id": "old"})
        locations = await self.db[stock.LOCATIONS].find({}).to_list(20)
        result = await self.apply()
        self.assertEqual(result["status"], "applied")
        self.assertEqual(result["classification"], "preparation_file")
        self.assertEqual(len(result["new_piece_ids"]), 2)
        for before in self.before_edit_pieces:
            obsolete = await self.db[controls.PIECES].find_one({"piece_id": before["piece_id"]})
            self.assertEqual(obsolete["status"], "obsolete")
            self.assertFalse(obsolete["current"])
            for key in ("generation", "source_options_snapshot", "responsible_employee_id", "registry_id", "batch_id"):
                self.assertEqual(obsolete.get(key), before.get(key))
            current = await self.db[controls.PIECES].find_one({"order_item_id": "new", "unit_index": before["unit_index"], "current": True})
            self.assertEqual(current["generation"], before["generation"] + 1)
            self.assertEqual(current["change_id"], self.event["change_id"])
            self.assertEqual(current["source_options_snapshot"], self.changed["items"][1]["options"])
            component = await self.db[stock.UNITS].find_one({"order_line_id": "new", "unit_index": before["unit_index"]})
            self.assertEqual(component["generation"], current["generation"])
            self.assertTrue(component["allocations"])
        self.assertEqual(await self.db[controls.PIECES].find_one({"piece_id": "old-piece"}), old)
        self.assertEqual(await self.db[stock.UNITS].find_one({"order_line_id": "old"}), unrelated)
        self.assertEqual(await self.db[stock.LOCATIONS].find({}).to_list(20), locations)
        self.assertEqual(await self.db[controls.AUDIT].count_documents({"event_type": "salla_edit_applied"}), 1)

    async def test_pending_read_is_read_only_and_exposes_source_options(self):
        before = await self.state()
        result = await application.pending_edits(self.db, user_id="owner", order_number="order-1", context=self.context)
        self.assertTrue(result["changes"])
        self.assertIn("black", str(result["changes"]))
        self.assertIn("red", str(result["changes"]))
        self.assertEqual(await self.state(), before)

    async def test_timeout_after_commit_retry_is_same_receipt_without_duplicate_generation(self):
        payload = await self.payload()
        first = await self.apply(payload)
        before = await self.state()
        retry = await self.apply(payload)
        self.assertTrue(retry["idempotent_replay"])
        self.assertEqual(retry["new_piece_ids"], first["new_piece_ids"])
        self.assert_replay_business_state_unchanged(await self.state(), before)

    async def test_concurrent_apply_two_mongo_clients_commits_once(self):
        payload = await self.payload()
        other = AsyncIOMotorClient(os.environ["MZ2_TEST_MONGO_URI"], tz_aware=True)
        try:
            results = await asyncio.gather(self.apply(payload), self.apply(payload, db=other[self.db.name]))
            self.assertEqual(results[0]["new_piece_ids"], results[1]["new_piece_ids"])
            self.assertEqual(await self.db[controls.AUDIT].count_documents({"event_type": "salla_edit_applied"}), 1)
        finally:
            other.close()

    async def test_stale_order_revision(self):
        payload = await self.payload()
        payload["expected_revision"] += 1
        await self.assert_failure_unchanged(payload)

    async def test_stale_order_generation(self):
        payload = await self.payload()
        payload["expected_generation"] = "0" * 64
        await self.assert_failure_unchanged(payload)

    async def test_exact_piece_fences_all_required(self):
        for key, value in (("generation", 99), ("revision", 99), ("change_id", "foreign"), ("order_item_id", "old"), ("unit_index", 99)):
            with self.subTest(key=key):
                payload = await self.payload()
                payload["units"][0][key] = value
                await self.assert_failure_unchanged(payload)

    async def test_duplicate_webhook_does_not_duplicate_application(self):
        await self.replay(self.changed)
        await self.apply()
        self.assertEqual(await self.db[controls.AUDIT].count_documents({"event_type": "salla_edit_applied"}), 1)
        self.assertEqual(await self.db[controls.PIECES].count_documents({"order_item_id": "new", "current": True}), 2)

    async def test_later_delete_rejects_edit_without_mutation(self):
        later = deepcopy(self.changed)
        later["version"] = "2040-01-02T13:00:00+00:00"
        later["items"] = later["items"][:1]
        await self.replay(later)
        await self.assert_failure_unchanged()

    async def test_later_replace_rejects_edit_without_mutation(self):
        later = deepcopy(self.changed)
        later["version"] = "2040-01-02T13:00:00+00:00"
        later["items"][1]["product_id"] = "replacement-product"
        await self.replay(later)
        await self.assert_failure_unchanged()

    async def source_race(self, replacement):
        payload = await self.payload()
        later = deepcopy(self.changed)
        later["version"] = "2040-01-02T13:00:00+00:00"
        if replacement:
            later["items"][1]["product_id"] = "replacement-product"
        else:
            later["items"] = later["items"][:1]
        other = AsyncIOMotorClient(os.environ["MZ2_TEST_MONGO_URI"], tz_aware=True)
        try:
            results = await asyncio.gather(self.apply(payload), intake.replay_snapshot(other[self.db.name], user_id="owner",
                order_number="order-1", snapshot=later, idempotency_key=uuid4().hex), return_exceptions=True)
            self.assertIsInstance(results[1], dict)
            if isinstance(results[0], HTTPException):
                self.assertEqual(await self.db[controls.PIECES].count_documents({"order_item_id": "new", "generation": 1}), 0)
                self.assertEqual(await self.db[controls.PIECES].count_documents({"order_item_id": "new", "status": "obsolete"}), 0)
            else:
                self.assertIsInstance(results[0], dict)
                self.assertEqual(results[0]["status"], "applied")
                self.assertEqual(await self.db[controls.PIECES].count_documents({"order_item_id": "new", "generation": 1}), 2)
            with self.assertRaises(HTTPException):
                await controls.assert_not_held(self.db, user_id="owner", order_number="order-1", order_item_id="new")
        finally:
            other.close()

    async def test_delete_webhook_race_keeps_affected_product_guarded(self):
        await self.source_race(False)

    async def test_replace_webhook_race_keeps_affected_product_guarded(self):
        await self.source_race(True)

    async def test_older_out_of_order_webhook_cannot_restore_old_options(self):
        await self.replay(self.added)
        await self.apply()
        row = await self.db[controls.PIECES].find_one({"order_item_id": "new", "current": True})
        self.assertEqual(row["source_options_snapshot"]["color"], "black")

    async def test_two_successive_edits_advance_generation_without_losing_history(self):
        await self.apply()
        later = deepcopy(self.changed)
        later["version"] = "2040-01-02T13:00:00+00:00"
        later["items"][1]["options"]["color"] = "green"
        result = await self.replay(later)
        self.event = next(e for e in result["events"] if e["change_type"] == "edit_options")
        await self.apply()
        self.assertEqual(await self.db[controls.PIECES].count_documents({"order_item_id": "new", "generation": 2, "current": True}), 2)
        self.assertEqual(await self.db[controls.PIECES].count_documents({"order_item_id": "new", "status": "obsolete"}), 4)

    async def prepare_numeric_representation_edit(self):
        await self.apply()
        numeric = deepcopy(self.changed)
        numeric["version"] = "2040-01-02T13:00:00+00:00"
        numeric["items"][1]["options"]["size"] = 56
        result = await self.replay(numeric)
        self.event = next(e for e in result["events"] if e["change_type"] == "edit_options")
        await self.apply()
        string = deepcopy(numeric)
        string["version"] = "2040-01-02T14:00:00+00:00"
        string["items"][1]["options"]["size"] = "56"
        result = await self.replay(string)
        self.event = next(e for e in result["events"] if e["change_type"] == "edit_options")

    async def test_numeric_representation_only_does_not_reprepare(self):
        await self.prepare_numeric_representation_edit()
        pieces = await self.db[controls.PIECES].find({}).sort("_id", 1).to_list(30)
        result = await self.apply()
        self.assertEqual(result["classification"], "representation_only")
        self.assertEqual(result["status"], "applied")
        self.assertEqual(await self.db[controls.PIECES].find({}).sort("_id", 1).to_list(30), pieces)

    async def test_representation_only_with_partial_consumption_requires_reconciliation(self):
        await self.prepare_numeric_representation_edit()
        await self.consumption_decision({"consumed_quantity": "1"})

    async def test_representation_only_with_ambiguous_allocation_requires_reconciliation(self):
        await self.prepare_numeric_representation_edit()
        await self.consumption_decision({"allocations.0.state": "partially_consumed"})

    async def test_embedded_hold_evidence_without_hold_projection_cannot_be_bypassed(self):
        piece_id = self.before_edit_pieces[0]["piece_id"]
        baseline = await self.db[controls.PIECES].find_one({"piece_id": piece_id})
        for delta in ({"status": "blocked"}, {"execution_status": "blocked"}, {"active_hold_id": "manual-or-pr2"}):
            with self.subTest(evidence=delta):
                await self.db[controls.PIECES].update_one({"piece_id": piece_id}, {"$set": delta})
                self.assertEqual(await self.db[controls.HOLDS].count_documents({"id": "manual-or-pr2"}), 0)
                await self.assert_failure_unchanged()
                await self.db[controls.PIECES].replace_one({"piece_id": piece_id}, baseline)

    async def test_changed_component_recipe_reconciles_only_affected_reservations(self):
        unrelated = await self.db[stock.UNITS].find_one({"order_line_id": "old"})
        await self.db[stock.PRODUCT_BINDINGS].update_one({"id": "binding"}, {"$set": {"quantity": 3}})
        result = await self.apply()
        self.assertEqual(result["classification"], "components")
        self.assertEqual(result["status"], "applied")
        self.assertEqual(await self.db[stock.UNITS].find_one({"order_line_id": "old"}), unrelated)
        for unit in await self.db[stock.UNITS].find({"order_line_id": "new"}).to_list(10):
            self.assertEqual(sum(float(a["quantity"]) for a in unit["allocations"]), 3)

    async def test_native_missing_generation_revision_is_fenced_as_zero(self):
        payload = await self.payload()
        self.assertTrue(all(unit["generation"] == 0 and unit["revision"] == 0 for unit in payload["units"]))
        result = await self.apply(payload)
        self.assertEqual(result["status"], "applied")
        self.assertEqual(await self.db[controls.PIECES].count_documents({"order_item_id": "new", "generation": 1, "current": True}), 2)

    async def test_allocation_shortage_rolls_back_old_and_new_state(self):
        await self.db[stock.PRODUCT_BINDINGS].update_one({"id": "binding"}, {"$set": {"quantity": 100}})
        await self.assert_failure_unchanged()

    async def test_financial_sentinels_and_provider_source_unchanged(self):
        names = ("accounting_invoices", "accounting_journals", "refunds", "mz2_financial_events")
        for name in names:
            await self.db[name].insert_one({"_id": "sentinel", "immutable": True})
        source = await self.db.unified_orders.find_one({})
        await self.apply()
        self.assertEqual(await self.db.unified_orders.find_one({}), source)
        for name in names:
            self.assertEqual(await self.db[name].find({}).to_list(10), [{"_id": "sentinel", "immutable": True}])

    async def test_write_capability_denies_accounting_inventory_and_provider_writes(self):
        from operational_atomic import operational_owner
        for name in ("qoyod_invoices", "journal_entries", "warehouse_locations", "unified_orders"):
            with self.subTest(collection=name):
                before = await self.state()
                async def mutate(scoped):
                    try:
                        await scoped[name].insert_one({"user_id": "owner", "id": "forbidden"})
                    except HTTPException:
                        pass
                    await scoped[controls.AUDIT].insert_one({"_id": uuid4().hex, "user_id": "owner", "event_type": "must_rollback"})
                with self.assertRaises(HTTPException):
                    await operational_owner(self.db, "owner", mutate, profile="salla_edit")
                self.assertEqual(await self.state(), before)
                self.assertEqual(await self.db[name].count_documents({"id": "forbidden"}), 0)

    async def test_edit_while_preparing_replaces_only_affected_generation(self):
        await self.db[controls.PIECES].update_many({"order_item_id": "new"}, {"$set": {"status": "in_progress"}})
        result = await self.apply()
        self.assertEqual(result["status"], "applied")
        self.assertEqual(await self.db[controls.PIECES].count_documents({"order_item_id": "new", "generation": 0, "status": "obsolete"}), 2)

    async def test_edit_after_receive_before_assembly_replaces_only_affected_generation(self):
        await self.db[controls.PIECES].update_many({"order_item_id": "new"}, {"$set": {"status": "ready_for_assembly"}})
        result = await self.apply()
        self.assertEqual(result["status"], "applied")
        self.assertEqual(await self.db[controls.PIECES].count_documents({"order_item_id": "new", "generation": 0, "status": "obsolete"}), 2)

    async def test_cancelled_order_rejects_without_mutation(self):
        await self.db[controls.WORKFLOWS].update_one({}, {"$set": {"stage": "cancelled"}})
        await self.assert_failure_unchanged()

    async def test_missing_affected_unit_fails_closed(self):
        payload = await self.payload()
        payload["units"] = payload["units"][:1]
        await self.assert_failure_unchanged(payload)

    async def test_add_event_cannot_enter_edit_contract(self):
        payload = await self.payload()
        payload["event_id"] = self.source_result["events"][0]["event_id"]
        await self.assert_failure_unchanged(payload)

    async def consumption_decision(self, delta):
        await self.db[stock.UNITS].update_one({"order_line_id": "new", "unit_index": 1}, {"$set": delta})
        pieces = await self.db[controls.PIECES].find({}).sort("_id", 1).to_list(30)
        units = await self.db[stock.UNITS].find({}).sort("_id", 1).to_list(30)
        locations = await self.db[stock.LOCATIONS].find({}).to_list(20)
        payload = await self.payload()
        result = await self.apply(payload)
        self.assertEqual(result["status"], "reconciliation_required")
        notice = await self.db[controls.AUDIT].find_one({"event_type": application.OUTBOX, "event_id": self.event["event_id"]})
        self.assertEqual(notice["required_action"], "reconcile_consumption")
        self.assertEqual(await self.db[controls.PIECES].find({}).sort("_id", 1).to_list(30), pieces)
        self.assertEqual(await self.db[stock.UNITS].find({}).sort("_id", 1).to_list(30), units)
        self.assertEqual(await self.db[stock.LOCATIONS].find({}).to_list(20), locations)
        self.assertTrue(await self.db[controls.HOLDS].count_documents({"change_id": self.event["change_id"], "status": "active"}))
        before = await self.state()
        self.assertTrue((await self.apply(payload))["idempotent_replay"])
        self.assert_replay_business_state_unchanged(await self.state(), before)

    async def test_consumed_component_never_auto_returns_stock(self):
        await self.consumption_decision({"state": "consumed"})

    async def test_partial_component_consumption_requires_reconciliation(self):
        await self.consumption_decision({"consumed_quantity": "1"})

    async def test_per_allocation_consumption_requires_reconciliation(self):
        await self.consumption_decision({"allocations.0.state": "partially_consumed"})

    async def test_audit_failure_rolls_back_obsolete_new_generation_and_hold_release(self):
        await self.validator_failure(controls.AUDIT, {"event_type": {"$ne": "salla_edit_applied"}})

    async def test_notification_outbox_failure_rolls_back_everything(self):
        await self.validator_failure(controls.AUDIT, {"event_type": {"$ne": "salla_edit_notification_outbox"}})

    async def test_component_failure_rolls_back_everything(self):
        await self.validator_failure(stock.UNITS, {"generation": {"$ne": 1}})

    async def test_assignment_failure_rolls_back_everything(self):
        await self.validator_failure(add.REGISTRY, {"source": {"$ne": "salla_order_edit_options"}})

    async def test_allocation_write_failure_rolls_back_everything(self):
        await self.validator_failure(add.ALLOCATIONS, {"generation": {"$ne": 1}})

    async def validator_failure(self, name, validator):
        await self.db.command({"collMod": name, "validator": validator, "validationLevel": "strict"})
        before = await self.state()
        with self.assertRaises(OperationFailure) as error:
            await self.apply()
        self.assertEqual(error.exception.code, 121)
        self.assertEqual(await self.state(), before)

    async def test_invalid_employee_never_leaves_partial_new_generation(self):
        payload = await self.payload()
        payload["employee_id"] = "missing"
        await self.assert_failure_unchanged(payload)

    async def test_disabled_contract_is_fail_closed(self):
        with patch.dict(os.environ, {"ORDER_SALLA_EDIT_APPLICATION_ENABLED": "false"}):
            await self.assert_failure_unchanged()

    async def test_unprivileged_actor_rejected(self):
        self.context = {"merchant_id": "owner", "actor_id": "employee-1", "is_owner": False, "permissions": set()}
        await self.assert_failure_unchanged()

    async def test_manual_and_pr2_holds_remain_independent_and_block(self):
        for authority, version in (("order_change_pr1", 2), ("order_change_pr2", 3)):
            with self.subTest(authority=authority):
                hold = {"id": uuid4().hex, "user_id": "owner", "order_number": "order-1", "scope": "order", "status": "active",
                        "authority": authority, "contract_version": version, "target_id": "order-1"}
                await self.db[controls.HOLDS].insert_one(hold)
                await self.assert_failure_unchanged()
                await self.db[controls.HOLDS].delete_one({"id": hold["id"]})

    async def test_detection_notification_targets_employee_and_remains_unread(self):
        notice = await self.db[controls.AUDIT].find_one({"event_type": intake.OUTBOX, "event_id": self.event["event_id"]})
        self.assertEqual(notice["recipients"], ["employee-1"])
        self.assertEqual(notice["status"], "pending")
        self.assertIsNone(notice["read_at"])
        self.assertEqual(notice["change_id"], self.event["change_id"])
        self.assertTrue(notice["affected_units"])
        for piece in self.before_edit_pieces:
            with self.assertRaises(HTTPException):
                await controls.assert_not_held(self.db, user_id="owner", order_number="order-1",
                                              order_item_id="new", piece_id=piece["piece_id"])

    async def test_application_notification_bound_to_event_employee_and_new_units(self):
        result = await self.apply()
        notice = await self.db[controls.AUDIT].find_one({"event_type": "salla_edit_notification_outbox", "event_id": self.event["event_id"]})
        self.assertIsNotNone(notice)
        self.assertEqual(notice["recipients"], ["employee-1"])
        self.assertEqual(notice["change_id"], self.event["change_id"])
        self.assertEqual(notice["status"], "pending")
        self.assertEqual(notice["required_action"], "prepare_new_generation")
        self.assertTrue(result["new_piece_ids"])

    async def test_employee_queue_new_barcode_and_obsolete_receive_rejection(self):
        from preparation_piece_operations import _my_work_view, _preparation_receipt_search, _receive_preparation_piece
        from preparation_piece_barcode import BARCODE_PREFIX
        result = await self.apply()
        work = await _my_work_view(self.db, user_id="owner", employee_id="employee-1", limit=100)
        visible = [p for p in work["pieces"] if p["piece_id"] in result["new_piece_ids"]]
        self.assertEqual(len(visible), 2)
        for piece in visible:
            self.assertEqual(piece["source_options_snapshot"], self.changed["items"][1]["options"])
        other = await _my_work_view(self.db, user_id="owner", employee_id="other", limit=100)
        self.assertFalse(any(p["piece_id"] in result["new_piece_ids"] for p in other["pieces"]))
        for piece_id in result["new_piece_ids"]:
            found = await _preparation_receipt_search(self.db, user_id="owner", query=BARCODE_PREFIX + piece_id)
            self.assertEqual(found["matched_piece_id"], piece_id)
        for old in self.before_edit_pieces:
            with self.assertRaises(HTTPException):
                await _receive_preparation_piece(self.db, user_id="owner", piece_id=old["piece_id"],
                    client_request_id=uuid4().hex, actor_id="owner", actor_name="Synthetic owner")


if __name__ == "__main__":
    unittest.main()
