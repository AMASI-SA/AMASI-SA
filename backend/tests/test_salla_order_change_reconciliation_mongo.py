"""Trusted source replay acceptance on disposable local Mongo; no provider I/O."""
import asyncio
import os
import unittest
from copy import deepcopy
from unittest.mock import patch
from uuid import uuid4

from fastapi import HTTPException
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.uri_parser import parse_uri

import fulfillment_lifecycle as lifecycle
import salla_order_change_reconciliation as service


class SallaChangeMongoTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        uri = os.environ.get("MZ2_TEST_MONGO_URI", "")
        if not uri:
            self.skipTest("MZ2_TEST_MONGO_URI required; no application DB fallback")
        parsed = parse_uri(uri)
        self.assertTrue(all(host in {"127.0.0.1", "localhost", "::1"} for host, _ in parsed["nodelist"]))
        self.assertFalse(parsed["username"] or parsed["password"] or parsed["database"])
        self.mongo = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=5000, tz_aware=True)
        self.addAsyncCleanup(self.cleanup)
        hello = await self.mongo.admin.command("hello")
        self.assertEqual(hello.get("setName"), "lifecyclepr3")
        self.assertTrue(hello.get("isWritablePrimary"))
        self.assertEqual((await self.mongo.admin.command("buildInfo"))["version"], "8.0.12")
        self.db = self.mongo["salla_change_pr3_" + uuid4().hex]
        flags = patch.dict(os.environ, {lifecycle.FLAG: "true", "ORDER_SALLA_CHANGE_RECONCILIATION_ENABLED": "true"})
        flags.start()
        self.addCleanup(flags.stop)
        self.context = {"merchant_id": "owner", "actor_id": "owner", "is_owner": True, "permissions": set()}
        await self.db.mz2_atomic_owners.insert_one({"_id": "owner", "revision": 0, "writes_paused": False, "control_revision": 0})
        await self.db[lifecycle.WORKFLOWS].insert_one({"user_id": "owner", "order_number": "order-1",
            "stage": "in_progress", "revision": 0, "items": [{"order_item_id": "line-1"}]})
        await self.db[lifecycle.PIECES].insert_one({"user_id": "owner", "order_number": "order-1",
            "piece_id": "piece-1", "order_item_id": "line-1", "unit_index": 1,
            "generation": 0, "revision": 1, "current": True, "active": True, "status": "in_progress",
            "responsible_employee_id": "employee-1", "product_options_snapshot": {"color": "red"}})
        self.initial = {"version": "2040-01-01T10:00:00+00:00", "complete": True,
            "items": [{"order_item_id": "line-1", "product_id": "product-1", "quantity": 2,
                       "options": {"color": "red"}, "price": "10.00", "discount": "0.00", "tax": "1.50"}]}

    async def cleanup(self):
        if hasattr(self, "db"):
            self.assertTrue(self.db.name.startswith("salla_change_pr3_"))
            await self.mongo.drop_database(self.db.name)
        if hasattr(self, "mongo"):
            self.mongo.close()

    async def replay(self, snapshot=None, *, key=None, baseline=False, db=None):
        return await service.replay_snapshot(self.db if db is None else db, user_id="owner", order_number="order-1",
            snapshot=deepcopy(self.initial if snapshot is None else snapshot), idempotency_key=key or uuid4().hex,
            baseline=baseline)

    async def baseline(self):
        result = await self.replay(baseline=True)
        self.assertEqual(result["status"], "baseline_recorded")
        return result

    def changed(self, **item_changes):
        snapshot = deepcopy(self.initial)
        snapshot["version"] = "2040-01-01T11:00:00+00:00"
        snapshot["items"][0].update(item_changes)
        return snapshot

    async def pieces(self):
        return await self.db[lifecycle.PIECES].find({}).to_list(100)

    async def test_baseline_and_identical_newer_snapshot_do_not_hold_or_change_physical_work(self):
        before = await self.pieces()
        await self.baseline()
        result = await self.replay(self.changed())
        self.assertEqual(result["status"], "no_change")
        self.assertEqual(await self.db[lifecycle.HOLDS].count_documents({"status": "active"}), 0)
        self.assertEqual(await self.pieces(), before)

    async def test_add_and_option_changes_record_events_without_mutating_existing_pieces(self):
        await self.baseline()
        snapshot = self.changed(options={"color": "blue"})
        snapshot["items"].append({"order_item_id": "line-2", "product_id": "product-2", "quantity": 1, "options": {"size": "large"}})
        before = await self.pieces()
        result = await self.replay(snapshot)
        self.assertEqual(result["status"], "pending_application")
        self.assertTrue(result["events"])
        self.assertEqual(await self.pieces(), before)
        self.assertGreater(await self.db[lifecycle.HOLDS].count_documents({"status": "active", "contract_version": 4}), 0)
        self.assertGreater(await self.db[lifecycle.AUDIT].count_documents({"event_type": "salla_change_notification_outbox"}), 0)

    async def test_quantity_decrease_and_complete_removal_require_application_not_deletion(self):
        await self.baseline()
        before = await self.pieces()
        decreased = await self.replay(self.changed(quantity=1))
        self.assertEqual(decreased["status"], "pending_application")
        self.assertEqual(decreased["events"][0]["affected_units"], [])
        removed = self.changed()
        removed["version"] = "2040-01-01T12:00:00+00:00"
        removed["items"] = []
        result = await self.replay(removed)
        self.assertEqual(result["status"], "pending_application")
        self.assertEqual(await self.pieces(), before)

    async def test_explicit_replacement_retains_old_piece(self):
        await self.baseline()
        replacement = self.changed()
        replacement["items"] = [{"order_item_id": "line-new", "product_id": "product-2", "quantity": 2, "options": {"color": "red"}}]
        replacement["replacements"] = [{"old_item_id": "line-1", "new_item_id": "line-new"}]
        before = await self.pieces()
        result = await self.replay(replacement)
        self.assertEqual(result["status"], "pending_application")
        self.assertEqual(await self.pieces(), before)

    async def test_incomplete_snapshot_cannot_interpret_missing_items_as_cancellation(self):
        await self.baseline()
        partial = self.changed()
        partial.update(complete=False, items=[])
        before = await self.pieces()
        result = await self.replay(partial)
        self.assertEqual(result["status"], "awaiting_authoritative_refresh")
        self.assertEqual(await self.pieces(), before)

    async def test_older_version_is_stale_and_same_version_different_content_conflicts(self):
        await self.baseline()
        older = self.changed(options={"color": "blue"})
        older["version"] = "2040-01-01T09:00:00+00:00"
        self.assertEqual((await self.replay(older))["status"], "stale_ignored")
        conflict = self.changed(options={"color": "green"})
        conflict["version"] = self.initial["version"]
        self.assertEqual((await self.replay(conflict))["status"], "source_conflict")

    async def test_response_loss_replay_and_replay_with_flags_disabled_do_not_duplicate_events(self):
        await self.baseline()
        snapshot, key = self.changed(options={"color": "blue"}), uuid4().hex
        original = await self.replay(snapshot, key=key)
        events = await self.db[lifecycle.AUDIT].find({}).to_list(100)
        with patch.dict(os.environ, {lifecycle.FLAG: "false", "ORDER_SALLA_CHANGE_RECONCILIATION_ENABLED": "false"}):
            replay = await self.replay(snapshot, key=key)
        self.assertEqual(replay["change_id"], original["change_id"])
        self.assertEqual(await self.db[lifecycle.AUDIT].find({}).to_list(100), events)

    async def test_different_payload_cannot_reuse_idempotency_key(self):
        await self.baseline()
        key = uuid4().hex
        await self.replay(self.changed(options={"color": "blue"}), key=key)
        with self.assertRaises(HTTPException):
            await self.replay(self.changed(options={"color": "green"}), key=key)

    async def test_independent_clients_same_snapshot_same_key_emit_once(self):
        await self.baseline()
        snapshot, key = self.changed(options={"color": "blue"}), uuid4().hex
        other = AsyncIOMotorClient(os.environ["MZ2_TEST_MONGO_URI"], serverSelectionTimeoutMS=5000)
        try:
            results = await asyncio.gather(self.replay(snapshot, key=key), self.replay(snapshot, key=key, db=other[self.db.name]))
            self.assertEqual(results[0]["change_id"], results[1]["change_id"])
            self.assertEqual(await self.db[lifecycle.HOLDS].count_documents({"status": "active", "contract_version": 4}), 1)
        finally:
            other.close()

    async def test_material_source_change_blocks_all_execution_targets_even_after_flags_disabled(self):
        await self.baseline()
        await self.replay(self.changed(options={"color": "blue"}))
        targets = [{"order_number": "order-1"},
            {"order_number": "order-1", "order_item_id": "line-1", "piece_id": "piece-1"}]
        with patch.dict(os.environ, {lifecycle.FLAG: "false", "ORDER_SALLA_CHANGE_RECONCILIATION_ENABLED": "false"}):
            for operation in ("worker", "receive", "prepare", "ready", "assembly", "addressing", "shipping", "carrier_handoff"):
                for target in targets:
                    with self.subTest(operation=operation, target=target), self.assertRaises(HTTPException):
                        async with lifecycle.execution_scope(self.db, user_id="owner", targets=[target], operation=operation):
                            self.fail("Changed order executed before reconciliation")

    async def test_active_worker_source_update_waits_for_execution_resolution(self):
        await self.baseline()
        async with lifecycle.execution_scope(self.db, user_id="owner", targets=[{"order_number": "order-1"}], operation="worker"):
            result = await self.replay(self.changed(options={"color": "blue"}))
            self.assertEqual(result["status"], "awaiting_execution_resolution")
        self.assertEqual((await self.pieces())[0]["status"], "in_progress")

    async def test_audit_failure_rolls_back_hold_revision_and_idempotency_record(self):
        await self.baseline()
        revision = (await self.db[lifecycle.WORKFLOWS].find_one({}))["revision"]
        request_count = await self.db[lifecycle.REQUESTS].count_documents({})
        await self.db.command({"collMod": lifecycle.AUDIT, "validator": {"event_type": {"$ne": "salla_order_change"}}, "validationLevel": "strict"})
        snapshot, key = self.changed(options={"color": "blue"}), uuid4().hex
        with self.assertRaises(Exception):
            await self.replay(snapshot, key=key)
        self.assertEqual((await self.db[lifecycle.WORKFLOWS].find_one({}))["revision"], revision)
        self.assertEqual(await self.db[lifecycle.HOLDS].count_documents({}), 0)
        self.assertEqual(await self.db[lifecycle.REQUESTS].count_documents({}), request_count)
        await self.db.command({"collMod": lifecycle.AUDIT, "validator": {}})
        self.assertEqual((await self.replay(snapshot, key=key))["status"], "pending_application")

    async def test_outbox_failure_cannot_commit_silent_change(self):
        await self.baseline()
        events = await self.db[lifecycle.AUDIT].find({}).to_list(100)
        await self.db.command({"collMod": lifecycle.AUDIT, "validator": {"event_type": {"$ne": "salla_change_notification_outbox"}}, "validationLevel": "strict"})
        with self.assertRaises(Exception):
            await self.replay(self.changed(quantity=3))
        self.assertEqual(await self.db[lifecycle.AUDIT].find({}).to_list(100), events)
        self.assertEqual(await self.db[lifecycle.HOLDS].count_documents({}), 0)

    async def test_inventory_invoice_accounting_and_original_pieces_remain_unchanged(self):
        await self.baseline()
        names = ["mezan_component_consumption_units_v1", "mezan_component_consumption_plans_v1",
            "warehouse_locations", "accounting_journal_groups_v2", "accounting_general_ledger_v2", "supplier_invoices", "order_invoices"]
        for name in names:
            await self.db[name].insert_one({"_id": "synthetic", "user_id": "owner", "order_number": "order-1", "amount": "10.00", "state": "unchanged"})
        before = {name: await self.db[name].find({}).to_list(20) for name in names + [lifecycle.PIECES]}
        await self.replay(self.changed(quantity=3, options={"color": "blue"}))
        for name, expected in before.items():
            self.assertEqual(await self.db[name].find({}).to_list(20), expected, name)

    async def test_strict_identity_quantity_completeness_and_version_schema(self):
        cases = [("quantity_invalid", {"items": [{**self.initial["items"][0], "quantity": True}]}),
            ("quantity_invalid", {"items": [{**self.initial["items"][0], "quantity": 0}]}),
            ("duplicate_item_identity", {"items": self.initial["items"] * 2}),
            ("options_required", {"items": [{**self.initial["items"][0], "options": []}]}),
            ("completeness_required", {"complete": "true"}), ("version_invalid", {"version": "2040-01-01T10:00:00"})]
        for code, updates in cases:
            with self.subTest(code=code), self.assertRaises(HTTPException) as invalid:
                await self.replay({**deepcopy(self.initial), **updates}, baseline=True)
            self.assertEqual(invalid.exception.status_code, 422)
            self.assertEqual(invalid.exception.detail["code"], "salla_change_" + code)
        self.assertEqual(await self.db[lifecycle.HOLDS].count_documents({}), 0)

    async def test_same_product_and_sku_lines_keep_separate_option_identity(self):
        initial = deepcopy(self.initial)
        initial["items"][0]["sku"] = "SHARED"
        initial["items"].append({**initial["items"][0], "order_item_id": "line-2", "options": {"color": "green"}})
        await self.replay(initial, baseline=True)
        changed = deepcopy(initial)
        changed["version"] = "2040-01-01T11:00:00+00:00"
        changed["items"][1]["options"] = {"color": "blue"}
        result = await self.replay(changed)
        self.assertEqual(len(result["events"]), 1)
        self.assertEqual(result["events"][0]["change_type"], "edit_options")
        self.assertEqual(result["events"][0]["old_data"]["order_item_id"], "line-2")

    async def test_financial_and_order_cancellation_are_evidence_only(self):
        await self.baseline()
        changed = self.changed()
        changed.update(commercial={"total": "22.00", "shipping": "2.00"}, cancelled=True)
        result = await self.replay(changed)
        self.assertEqual({event["change_type"] for event in result["events"]}, {"financial_change", "cancel_order"})
        self.assertEqual((await self.pieces())[0]["status"], "in_progress")

    async def test_late_shipped_change_is_exception_required_and_preserves_history(self):
        await self.baseline()
        await self.db[lifecycle.WORKFLOWS].update_one({}, {"$set": {"stage": "completed", "carrier_label_barcode": "SYNTHETIC-AWB"}})
        before = await self.pieces()
        result = await self.replay(self.changed(options={"color": "blue"}))
        self.assertEqual(result["status"], "exception_required")
        self.assertEqual(await self.pieces(), before)
        self.assertEqual((await self.db[lifecycle.WORKFLOWS].find_one({}))["carrier_label_barcode"], "SYNTHETIC-AWB")

    async def test_virtual_operational_and_direct_assembly_pieces_are_affected_evidence(self):
        await self.db[lifecycle.WORKFLOWS].update_one({}, {"$set": {
            "operational_items": [{"operational_item_id": "virtual-card", "source_order_item_id": "line-1", "generation": 0, "revision": 1}],
            "items": [{"order_item_id": "line-1", "quantity": 1, "preparation_route": "direct_assembly",
                       "direct_assembly_piece_ids": ["virtual-direct"], "generation": 0, "revision": 1}]}})
        await self.baseline()
        result = await self.replay(self.changed(options={"color": "blue"}))
        ids = {row["piece_id"] for event in result["events"] for row in event["affected_units"]}
        self.assertEqual(ids, {"piece-1", "virtual-card", "virtual-direct"})
        self.assertEqual(await self.db[lifecycle.PIECES].count_documents({}), 1)

    async def test_v4_barrier_cannot_be_released_through_pr1_resume(self):
        await self.baseline()
        await self.replay(self.changed(quantity=3))
        hold = await self.db[lifecycle.HOLDS].find_one({"contract_version": 4})
        caps = await lifecycle.capabilities(self.db, user_id="owner", order_number="order-1", context=self.context)
        with self.assertRaises(HTTPException):
            await lifecycle.resume_hold(self.db, user_id="owner", hold_id=hold["id"], context=self.context,
                payload={"reason": "Attempt generic resume", "idempotency_key": uuid4().hex,
                    "expected_revision": caps["revision"], "expected_generation": caps["generation"]})
        self.assertEqual((await self.db[lifecycle.HOLDS].find_one({"id": hold["id"]}))["status"], "active")

    async def test_both_flags_exact_true_are_required_for_new_intake(self):
        for flag in (lifecycle.FLAG, "ORDER_SALLA_CHANGE_RECONCILIATION_ENABLED"):
            for value in ("false", "TRUE", "1"):
                with self.subTest(flag=flag, value=value), patch.dict(os.environ, {flag: value}), self.assertRaises(HTTPException) as denied:
                    await self.replay(baseline=True)
                self.assertEqual(denied.exception.detail["code"], "salla_change_disabled")
        self.assertEqual(await self.db[lifecycle.AUDIT].count_documents({}), 0)

    async def test_incomplete_then_complete_same_version_and_facts_have_distinct_intake(self):
        await self.baseline()
        complete = self.changed(options={"color": "blue"})
        incomplete = {**deepcopy(complete), "complete": False}
        first = await self.replay(incomplete)
        self.assertEqual(first["status"], "awaiting_authoritative_refresh")
        original_events = await self.db[lifecycle.AUDIT].find({}).to_list(100)
        second = await self.replay(complete)
        self.assertEqual(second["status"], "pending_application")
        self.assertNotEqual(second["change_id"], first["change_id"])
        for original in original_events:
            self.assertEqual(await self.db[lifecycle.AUDIT].find_one({"_id": original["_id"]}), original)
        self.assertEqual(await self.db[lifecycle.HOLDS].count_documents({"status": "active", "contract_version": 4}), 1)

    async def test_actor_reason_unit_history_and_outbox_ack_do_not_apply_change(self):
        await self.baseline()
        snapshot = self.changed(options={"color": "blue"})
        snapshot.update(actor={"actor_id": "synthetic-salla-user", "name": "Synthetic source actor"}, reason="Source-supplied correction")
        result = await self.replay(snapshot)
        event = result["events"][0]
        self.assertEqual(event["actor"], snapshot["actor"])
        self.assertEqual(event["reason"], snapshot["reason"])
        self.assertEqual(event["old_data"]["options"], {"color": "red"})
        self.assertEqual(event["new_data"]["options"], {"color": "blue"})
        self.assertEqual(event["affected_units"][0]["generation"], 0)
        outbox = await self.db[lifecycle.AUDIT].find_one({"event_type": service.OUTBOX})
        self.assertEqual(outbox["recipients"], ["employee-1"])
        self.assertEqual(outbox["status"], "pending")
        await self.db.synthetic_notification_receipts.insert_one({"event_id": outbox["event_id"],
            "read_at": "2040-01-01T12:00:00+00:00", "acknowledged_at": "2040-01-01T12:00:00+00:00"})
        with self.assertRaises(HTTPException):
            async with lifecycle.execution_scope(self.db, user_id="owner", targets=[{"order_number": "order-1"}], operation="worker-after-ack"):
                self.fail("Notification acknowledgement activated the pending change")
        self.assertEqual((await self.pieces())[0]["product_options_snapshot"], {"color": "red"})

    async def test_complete_snapshot_before_baseline_can_be_reinterpreted_with_fresh_key(self):
        snapshot, original_key = self.changed(options={"color": "blue"}), uuid4().hex
        ungrounded = await self.replay(snapshot, key=original_key)
        self.assertEqual(ungrounded["status"], "awaiting_authoritative_refresh")
        await self.baseline()
        grounded = await self.replay(snapshot)
        self.assertEqual(grounded["status"], "pending_application")
        self.assertNotEqual(grounded["change_id"], ungrounded["change_id"])
        self.assertEqual(grounded["events"][0]["change_type"], "edit_options")
        self.assertEqual(grounded["events"][0]["old_data"]["options"], {"color": "red"})
        retried = await self.replay(snapshot, key=original_key)
        self.assertEqual(retried["status"], "awaiting_authoritative_refresh")
        self.assertEqual(retried["change_id"], ungrounded["change_id"])

    async def test_reopening_cancelled_source_requires_explicit_exception(self):
        baseline = {**deepcopy(self.initial), "cancelled": True}
        await self.replay(baseline, baseline=True)
        reopened = {**self.changed(), "cancelled": False}
        result = await self.replay(reopened)
        self.assertEqual(result["status"], "exception_required")
        self.assertEqual([event["change_type"] for event in result["events"]], ["order_reopened"])
        self.assertEqual(result["events"][0]["affected_units"][0]["piece_id"], "piece-1")
        self.assertEqual((await self.pieces())[0]["status"], "in_progress")
        self.assertEqual(await self.db[lifecycle.HOLDS].count_documents({"contract_version": 4, "status": "active"}), 1)

    async def test_combined_options_and_quantity_change_preserves_both_changed_fields(self):
        await self.baseline()
        result = await self.replay(self.changed(quantity=3, options={"color": "blue"}))
        self.assertEqual(result["status"], "pending_application")
        event = result["events"][0]
        self.assertTrue({"options", "quantity"}.issubset(set(event["changed_fields"])))
        self.assertEqual(event["old_data"]["quantity"], 2)
        self.assertEqual(event["new_data"]["quantity"], 3)
        self.assertEqual(event["old_data"]["options"], {"color": "red"})
        self.assertEqual(event["new_data"]["options"], {"color": "blue"})

    async def test_independent_clients_out_of_order_concurrency_keeps_newest_source(self):
        await self.baseline()
        older, newer = self.changed(quantity=3), self.changed(quantity=4)
        newer["version"] = "2040-01-01T12:00:00+00:00"
        other = AsyncIOMotorClient(os.environ["MZ2_TEST_MONGO_URI"], serverSelectionTimeoutMS=5000)
        try:
            results = await asyncio.gather(self.replay(older), self.replay(newer, db=other[self.db.name]))
            self.assertTrue(all(r["status"] in {"pending_application", "stale_ignored"} for r in results))
            latest = await self.db[lifecycle.AUDIT].find({"event_type": service.INTAKE, "accepted": True}).sort("source_version", -1).limit(1).to_list(1)
            self.assertEqual(latest[0]["snapshot"]["items"][0]["quantity"], 4)
            self.assertEqual((await self.pieces())[0]["generation"], 0)
        finally:
            other.close()

    async def test_immutable_history_boundary_and_no_provider_or_financial_import(self):
        import ast
        from pathlib import Path
        await self.baseline()
        await self.replay(self.changed(quantity=3))
        async def tamper(scoped):
            await scoped[lifecycle.AUDIT].update_one({"user_id": "owner", "event_type": service.OUTBOX},
                                                   {"$set": {"acknowledged_at": "invalid"}})
        with self.assertRaises(HTTPException):
            await service.operational_owner(self.db, "owner", tamper)
        tree = ast.parse(Path(service.__file__).read_text())
        imports = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
        imports.update(a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names)
        self.assertEqual(imports, {"copy", "datetime", "json", "os", "fastapi", "fulfillment_lifecycle", "operational_atomic"})
        self.assertFalse(any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                             and n.func.id in {"eval", "exec", "__import__"} for n in ast.walk(tree)))


if __name__ == "__main__":
    unittest.main()
