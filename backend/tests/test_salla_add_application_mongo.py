"""PR4 application acceptance against disposable local Mongo; no database mocks."""
import asyncio
import os
import unittest
from copy import deepcopy
from unittest.mock import patch
from uuid import uuid4

from fastapi import HTTPException
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.uri_parser import parse_uri
from pymongo.errors import OperationFailure

import fulfillment_lifecycle as controls
import salla_order_change_reconciliation as intake
import stock_component_consumption_service as stock
import salla_add_application as application


class SallaAddApplicationMongoTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        uri = os.environ.get("MZ2_TEST_MONGO_URI", "")
        if not uri:
            self.skipTest("MZ2_TEST_MONGO_URI required; no application database fallback")
        parsed = parse_uri(uri)
        self.assertTrue(all(host in {"localhost", "127.0.0.1", "::1"} for host, _ in parsed["nodelist"]))
        self.assertFalse(parsed["username"] or parsed["password"] or parsed["database"])
        self.client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=5000, tz_aware=True)
        self.addAsyncCleanup(self.cleanup)
        hello = await self.client.admin.command("hello")
        self.assertEqual(hello.get("setName"), "lifecyclepr3")
        self.assertTrue(hello.get("isWritablePrimary"))
        self.assertEqual((await self.client.admin.command("buildInfo"))["version"], "8.0.12")
        self.db = self.client["salla_add_pr4_" + uuid4().hex]
        flags = patch.dict(os.environ, {controls.FLAG: "true", intake.FLAG: "true",
            "ORDER_SALLA_ADD_APPLICATION_ENABLED": "true"})
        flags.start()
        self.addCleanup(flags.stop)
        self.context = {"merchant_id": "owner", "actor_id": "owner", "is_owner": True,
                        "permissions": set(), "actor_name": "Synthetic manager"}
        await self.db.mz2_atomic_owners.insert_one({"_id": "owner", "revision": 0,
            "writes_paused": False, "control_revision": 0})
        await stock.ensure_component_consumption_indexes(self.db)
        await self.db.settings.insert_one({"user_id": "owner", "g47_inventory": {
            "component_lifecycle_starts_at": "2040-01-01T00:00:00+00:00"}})
        await self.db[stock.PRODUCTS].insert_one({"user_id": "owner", "id": "p", "salla_product_id": "p", "name": "Synthetic product"})
        await self.db[stock.RESOURCES].insert_one({"user_id": "owner", "id": "r", "kind": "stock_component", "track_inventory": True})
        await self.db[stock.PRODUCT_BINDINGS].insert_one({"id": "binding", "user_id": "owner",
            "salla_product_id": "p", "resource_id": "r", "quantity": 2})
        await self.db[stock.LOCATIONS].insert_one({"user_id": "owner", "id": "location", "warehouse_id": "warehouse",
            "state": "occupied", "occupancy": {"items": [{"item_type": "stock_component", "resource_id": "r",
                "receipt_id": "lot", "quantity": 30}], "total_quantity": 30}})
        old_quantity = 3 if self._testMethodName == "test_mixed_old_piece_stages_and_reservations_are_unchanged" else 1
        await stock.reserve_component_stock(self.db, merchant_id="owner", order_id="order-1",
            lines=[{"order_line_id": "old", "product_id": "p", "quantity": old_quantity}], source_version=1,
            source_created_at="2040-01-02T00:00:00+00:00")
        await self.db[controls.WORKFLOWS].insert_one({"user_id": "owner", "order_number": "order-1",
            "stage": "in_progress", "revision": 0, "items": [{"order_item_id": "old"}]})
        await self.db.mezan_component_order_lifecycle_v1.insert_one({"user_id": "owner", "order_number": "order-1",
            "generation": 1, "state": "reserved", "accepted": True})
        await self.db.unified_orders.insert_one({"user_id": "owner", "order_number": "order-1",
            "raw_by_source": {"salla_direct": {"created_at": "2040-01-02T00:00:00+00:00"}}})
        from preparation_file_registry import MOBILE_APP_ACCESS, MOBILE_APP_ACCESS_OWNER_FIELD
        await self.db.users.insert_many([{"id": "employee-1", "name": "Synthetic worker", "email": "worker@example.test", "is_active": True},
            {"id": "owner", "name": "Synthetic owner", "email": "owner@example.test", "is_active": True}])
        await self.db[MOBILE_APP_ACCESS].insert_one({MOBILE_APP_ACCESS_OWNER_FIELD: "owner", "user_id": "employee-1",
            "enabled": True, "permissions": ["app.page.my_products"]})
        await self.db[controls.PIECES].insert_one({"user_id": "owner", "order_number": "order-1", "piece_id": "old-piece",
            "order_item_id": "old", "unit_index": 1, "generation": 0, "revision": 1, "current": True,
            "active": True, "status": "in_progress", "responsible_employee_id": "employee-1"})
        self.initial = {"version": "2040-01-02T10:00:00+00:00", "complete": True,
            "items": [{"order_item_id": "old", "product_id": "p", "quantity": old_quantity, "options": {}}]}
        await self.replay(self.initial, baseline=True)
        # Independent stops predate the provider ADD; PR1 disallows stacking a
        # new manual order stop after a source-created order barrier exists.
        if self._testMethodName in {"test_manual_hold_blocks_application_and_remains_unchanged",
                                    "test_actual_pr2_consumed_reconciliation_barrier_is_preserved"}:
            caps = await controls.capabilities(self.db, user_id="owner", order_number="order-1", context=self.context)
            self.manual_hold = await controls.create_hold(self.db, user_id="owner", order_number="order-1", context=self.context,
                payload={"scope": "order", "stop_type": "edit", "reason": "Independent manual stop", "idempotency_key": uuid4().hex,
                    "expected_revision": caps["revision"], "expected_generation": caps["generation"]})
        self.added = deepcopy(self.initial)
        self.added["version"] = "2040-01-02T11:00:00+00:00"
        self.added["items"].append({"order_item_id": "new", "product_id": "p", "quantity": 2,
            "options": {"color": "red", "engraving": "Synthetic inscription"}})
        if self._testMethodName == "test_quantity_one_and_same_product_variant_options_are_distinct":
            self.added["items"][-1].update(quantity=1, variant_id="variant-red")
        if self._testMethodName in {"test_same_change_sibling_holds_release_only_matching_item", "test_distinct_added_items_concurrent_apply_then_retry"}:
            self.added["items"].append({"order_item_id": "new-sibling", "product_id": "p", "quantity": 1, "options": {"color": "blue"}})
        if self._testMethodName == "test_employee_sees_image_variant_options_custom_fields_and_required_action":
            self.added["items"][-1].update(image_url="https://example.test/product.png", variant_id="red-variant",
                product_name="Synthetic added product", custom_fields={"message": "full customer text"})
        self.source_result = await self.replay(self.added)
        self.event = next(e for e in self.source_result["events"] if e.get("new_data", {}).get("order_item_id") == "new")

    async def cleanup(self):
        if hasattr(self, "db"):
            self.assertTrue(self.db.name.startswith("salla_add_pr4_"))
            await self.client.drop_database(self.db.name)
        if hasattr(self, "client"):
            self.client.close()

    async def replay(self, source, baseline=False):
        return await intake.replay_snapshot(self.db, user_id="owner", order_number="order-1", snapshot=deepcopy(source),
            baseline=baseline, idempotency_key=uuid4().hex)

    async def payload(self):
        caps = await controls.capabilities(self.db, user_id="owner", order_number="order-1", context=self.context)
        return {"event_id": self.event["event_id"], "employee_id": "employee-1", "reason": "Apply authoritative Salla addition",
            "idempotency_key": uuid4().hex, "expected_revision": caps["revision"], "expected_generation": caps["generation"]}

    async def apply(self, payload=None, context=None, db=None):
        return await application.apply_addition(self.db if db is None else db, user_id="owner", order_number="order-1",
            context=self.context if context is None else context, payload=await self.payload() if payload is None else payload)

    async def state(self):
        names = (stock.PLANS, stock.UNITS, stock.LOCATIONS, controls.PIECES, controls.HOLDS, controls.WORKFLOWS,
                 controls.AUDIT, controls.REQUESTS, application.BATCHES, application.REGISTRY, application.ALLOCATIONS,
                 "mezan_component_order_lifecycle_v1", "unified_orders")
        return {name: await self.db[name].find({}).sort("_id", 1).to_list(1000) for name in names}

    async def test_application_preserves_old_unit_piece_source_and_physical_stock(self):
        before = await self.state()
        source = await self.db[controls.AUDIT].find_one({"event_id": self.event["event_id"], "event_type": intake.EVENT})
        result = await self.apply()
        self.assertEqual(result["status"], "applied")
        self.assertEqual(len(result["piece_ids"]), 2)
        self.assertTrue(result["eligible_for_execution"])
        self.assertEqual(await self.db[controls.PIECES].find_one({"piece_id": "old-piece"}), before[controls.PIECES][0])
        self.assertEqual(await self.db[stock.UNITS].find_one({"order_line_id": "old"}), before[stock.UNITS][0])
        self.assertEqual(await self.db[stock.LOCATIONS].find({}).to_list(10), before[stock.LOCATIONS])
        self.assertEqual(await self.db[controls.AUDIT].find_one({"_id": source["_id"]}), source)
        self.assertEqual(await self.db[stock.UNITS].count_documents({"order_line_id": "new", "state": "reserved"}), 2)
        self.assertEqual(await self.db[controls.AUDIT].count_documents({"event_type": "salla_add_applied"}), 1)
        self.assertEqual(await self.db[controls.AUDIT].count_documents({"event_type": "salla_add_notification_outbox"}), 1)

    async def test_response_loss_retry_has_one_receipt_and_no_duplicate_units(self):
        payload = await self.payload()
        first = await self.apply(payload)
        before = await self.state()
        second = await self.apply(payload)
        self.assertEqual(first["application_id"], second["application_id"])
        self.assertTrue(second["idempotent_replay"])
        self.assertEqual(await self.state(), before)

    async def test_same_request_parallel_clients_apply_once(self):
        payload = await self.payload()
        other = AsyncIOMotorClient(os.environ["MZ2_TEST_MONGO_URI"], tz_aware=True)
        try:
            results = await asyncio.gather(self.apply(payload), self.apply(payload, db=other[self.db.name]))
            self.assertEqual(results[0]["application_id"], results[1]["application_id"])
            self.assertEqual(await self.db[controls.AUDIT].count_documents({"event_type": "salla_add_applied"}), 1)
        finally:
            other.close()

    async def test_stale_fences_do_not_mutate(self):
        payload = await self.payload()
        for override in ({"expected_revision": 999}, {"expected_generation": "stale"}):
            before = await self.state()
            with self.assertRaises(HTTPException):
                await self.apply({**payload, **override})
            self.assertEqual(await self.state(), before)

    async def test_invalid_assignment_has_no_partial_application(self):
        payload = await self.payload()
        for employee in ("missing", "foreign-worker", ""):
            before = await self.state()
            with self.assertRaises(HTTPException):
                await self.apply({**payload, "employee_id": employee})
            self.assertEqual(await self.state(), before)

    async def test_audit_validator_failure_rolls_back_every_application_effect(self):
        await self.db.command({"collMod": controls.AUDIT, "validator": {"event_type": {"$ne": "salla_add_applied"}}, "validationLevel": "strict"})
        before = await self.state()
        with self.assertRaises(OperationFailure) as denied:
            await self.apply()
        self.assertEqual(denied.exception.code, 121)
        self.assertEqual(await self.state(), before)

    async def test_shortage_rolls_back_all_new_units_and_hold_release(self):
        await self.db[stock.LOCATIONS].update_one({}, {"$set": {"occupancy.items.0.quantity": 2, "occupancy.total_quantity": 2}})
        before = await self.state()
        with self.assertRaises(HTTPException):
            await self.apply()
        self.assertEqual(await self.state(), before)

    async def test_unprivileged_operator_cannot_apply(self):
        before = await self.state()
        with self.assertRaises(HTTPException):
            await self.apply(context={"merchant_id": "owner", "actor_id": "worker", "is_owner": False, "permissions": set()})
        self.assertEqual(await self.state(), before)

    async def test_disabled_application_flag_does_not_apply(self):
        before = await self.state()
        with patch.dict(os.environ, {"ORDER_SALLA_ADD_APPLICATION_ENABLED": "false"}):
            with self.assertRaises(HTTPException):
                await self.apply()
        self.assertEqual(await self.state(), before)

    async def test_pending_read_does_not_mutate(self):
        before = await self.state()
        result = await application.pending_additions(self.db, user_id="owner", order_number="order-1", context=self.context)
        self.assertTrue(result["changes"])
        self.assertTrue(result["employees"])
        self.assertEqual(result["changes"][0]["change_type"], "add_product")
        self.assertEqual(result["changes"][0]["application_state"], "pending_application")
        self.assertEqual(await self.state(), before)

    async def test_later_delete_cannot_apply_removed_addition(self):
        removed = deepcopy(self.initial)
        removed["version"] = "2040-01-02T12:00:00+00:00"
        await self.replay(removed)
        before = await self.state()
        with self.assertRaises(HTTPException):
            await self.apply()
        self.assertEqual(await self.state(), before)

    async def test_changed_added_options_require_new_application_contract(self):
        changed = deepcopy(self.added)
        changed["version"] = "2040-01-02T12:00:00+00:00"
        changed["items"][1]["options"]["color"] = "blue"
        await self.replay(changed)
        before = await self.state()
        with self.assertRaises(HTTPException):
            await self.apply()
        self.assertEqual(await self.state(), before)

    async def test_manual_hold_blocks_application_and_remains_unchanged(self):
        held = self.manual_hold
        before = await self.db[controls.HOLDS].find_one({"id": held["hold"]["id"]})
        state = await self.state()
        with self.assertRaises(HTTPException):
            await self.apply()
        self.assertEqual(await self.state(), state)
        self.assertEqual(await self.db[controls.HOLDS].find_one({"_id": before["_id"]}), before)
        with self.assertRaises(HTTPException):
            async with controls.execution_scope(self.db, user_id="owner", targets=[{"order_number": "order-1"}], operation="worker"):
                self.fail("Independent hold bypassed")

    async def test_second_addition_hold_is_preserved(self):
        next_source = deepcopy(self.added)
        next_source["version"] = "2040-01-02T12:00:00+00:00"
        next_source["items"].append({"order_item_id": "second-new", "product_id": "p", "quantity": 1, "options": {}})
        second = await self.replay(next_source)
        hold = await self.db[controls.HOLDS].find_one({"change_id": second["change_id"], "status": "active"})
        self.assertIsNotNone(hold)
        result = await self.apply()
        self.assertTrue(result["eligible_for_execution"])
        self.assertEqual(await self.db[controls.HOLDS].find_one({"_id": hold["_id"]}), hold)
        self.assertEqual(await self.db[controls.PIECES].count_documents({"order_item_id": "second-new"}), 0)

    async def test_application_is_visible_to_actual_employee_work_projection(self):
        from preparation_piece_operations import _my_work_view
        result = await self.apply()
        work = await _my_work_view(self.db, user_id="owner", employee_id="employee-1", limit=100)
        self.assertTrue(set(result["piece_ids"]).issubset({p["piece_id"] for p in work["pieces"]}))
        self.assertTrue(work["files"])
        unrelated = await _my_work_view(self.db, user_id="owner", employee_id="other-employee", limit=100)
        self.assertFalse(unrelated["pieces"])

    async def test_new_units_are_consumable_once_without_reconsuming_old_unit(self):
        await stock.consume_component_stock(self.db, merchant_id="owner", order_id="order-1", units={"old": [1]})
        original = await self.db[stock.UNITS].find_one({"order_line_id": "old"})
        await self.apply()
        from fulfillment_v2_routes import assert_component_execution
        plan = await self.db[stock.PLANS].find_one({"order_id": "order-1"})
        await assert_component_execution(self.db, user_id="owner", order_number="order-1", plan=plan)
        await stock.consume_component_stock(self.db, merchant_id="owner", order_id="order-1", units={"new": [1, 2]})
        occupancy = await self.db[stock.LOCATIONS].find_one({})
        await stock.consume_component_stock(self.db, merchant_id="owner", order_id="order-1", units={"new": [1, 2]})
        self.assertEqual(await self.db[stock.LOCATIONS].find_one({}), occupancy)
        self.assertEqual(await self.db[stock.UNITS].find_one({"order_line_id": "old"}), original)
        self.assertEqual(occupancy["occupancy"]["total_quantity"], 24)

    async def test_inactive_employee_rejected(self):
        await self.db.users.update_one({"id": "employee-1"}, {"$set": {"is_active": False}})
        before = await self.state()
        with self.assertRaises(HTTPException):
            await self.apply()
        self.assertEqual(await self.state(), before)

    async def test_same_key_different_assignment_or_reason_conflicts(self):
        payload = await self.payload()
        await self.apply(payload)
        before = await self.state()
        with self.assertRaises(HTTPException):
            await self.apply({**payload, "reason": "Different request meaning"})
        self.assertEqual(await self.state(), before)

    async def test_outbox_failure_rolls_back_receipt_and_hold_release(self):
        await self.db.command({"collMod": controls.AUDIT, "validator": {"event_type": {"$ne": "salla_add_notification_outbox"}}, "validationLevel": "strict"})
        before = await self.state()
        with self.assertRaises(OperationFailure) as denied:
            await self.apply()
        self.assertEqual(denied.exception.code, 121)
        self.assertEqual(await self.state(), before)

    async def test_actual_preparation_file_starts_and_retains_old_piece(self):
        from preparation_piece_operations import _start_file_execution
        original = await self.db[controls.PIECES].find_one({"piece_id": "old-piece"})
        result = await self.apply()
        registry = await self.db[application.REGISTRY].find_one({"change_id": result["change_id"]})
        batch = await self.db[application.BATCHES].find_one({"id": registry["batch_id"]})
        self.assertGreater(batch["pdf_size_bytes"], 100)
        self.assertEqual(len(batch["pdf_sha256"]), 64)
        await _start_file_execution(self.db, user_id="owner", registry=registry,
            actor={"id": "employee-1", "name": "Synthetic worker"}, note="Start assigned addition",
            permissions={"app.page.my_products", "preparation.assigned.work"})
        self.assertEqual(await self.db[controls.PIECES].count_documents({"piece_id": {"$in": result["piece_ids"]}, "status": "in_progress"}), 2)
        self.assertEqual(await self.db[controls.PIECES].find_one({"piece_id": "old-piece"}), original)

    async def test_tampered_change_hold_cannot_be_released(self):
        await self.db[controls.HOLDS].update_one({"change_id": self.event["change_id"]}, {"$set": {"revision": 999}})
        before = await self.state()
        with self.assertRaises(HTTPException):
            await self.apply()
        self.assertEqual(await self.state(), before)

    async def test_locked_stage_cannot_apply(self):
        for stage in ("ready_to_ship", "completed", "delivered"):
            await self.db[controls.WORKFLOWS].update_one({}, {"$set": {"stage": stage}})
            before = await self.state()
            with self.assertRaises(HTTPException):
                await self.apply()
            self.assertEqual(await self.state(), before)

    async def test_component_generation_change_rejects_source_event(self):
        await self.db.mezan_component_order_lifecycle_v1.update_one({}, {"$inc": {"generation": 1}})
        before = await self.state()
        with self.assertRaises(HTTPException):
            await self.apply()
        self.assertEqual(await self.state(), before)

    async def test_new_key_reapplication_does_not_duplicate_materialization(self):
        first = await self.apply()
        before = await self.state()
        second = await self.apply()
        self.assertEqual(first["application_id"], second["application_id"])
        self.assertTrue(second["idempotent_replay"])
        self.assertEqual(await self.state(), before)

    async def test_replay_after_rollback_flags_disabled_returns_original_receipt(self):
        payload = await self.payload()
        first = await self.apply(payload)
        before = await self.state()
        with patch.dict(os.environ, {controls.FLAG: "false", intake.FLAG: "false", "ORDER_SALLA_ADD_APPLICATION_ENABLED": "false"}):
            second = await self.apply(payload)
        self.assertEqual(first["application_id"], second["application_id"])
        self.assertTrue(second["idempotent_replay"])
        self.assertEqual(await self.state(), before)

    async def test_order_cancellation_blocks_add(self):
        cancelled = deepcopy(self.added)
        cancelled.update(version="2040-01-02T12:00:00+00:00", cancelled=True)
        await self.replay(cancelled)
        before = await self.state()
        with self.assertRaises(HTTPException):
            await self.apply()
        self.assertEqual(await self.state(), before)

    async def test_foreign_merchant_context_rejected(self):
        before = await self.state()
        with self.assertRaises(HTTPException):
            await self.apply(context={**self.context, "merchant_id": "foreign"})
        self.assertEqual(await self.state(), before)

    async def test_existing_current_unit_identity_cannot_be_duplicated(self):
        await self.db[controls.PIECES].insert_one({"user_id": "owner", "order_number": "order-1", "piece_id": "collision",
            "order_item_id": "new", "unit_index": 1})
        before = await self.state()
        with self.assertRaises(HTTPException):
            await self.apply()
        self.assertEqual(await self.state(), before)

    async def test_actual_pr2_consumed_reconciliation_barrier_is_preserved(self):
        import fulfillment_component_reconciliation as reconciliation
        await stock.consume_component_stock(self.db, merchant_id="owner", order_id="order-1", units={"old": [1]})
        consumed = await self.db[stock.UNITS].find_one({"order_line_id": "old"})
        await self.db[stock.PRODUCT_BINDINGS].update_one({}, {"$set": {"quantity": 3}})
        targets = [{"order_item_id": "old", "unit_index": 1, "generation": 0}]
        with patch.dict(os.environ, {reconciliation.FLAG: "true"}):
            preview = await reconciliation.preview_reconciliation(self.db, user_id="owner", order_number="order-1", context=self.context, units=targets)
            await reconciliation.reconcile_components(self.db, user_id="owner", order_number="order-1", context=self.context,
                payload={"units": targets, "expected_revision": preview["expected_revision"], "expected_generation": preview["expected_generation"],
                    "expected_plan_revision": preview["expected_plan_revision"], "expected_recipe_hash": preview["expected_recipe_hash"],
                    "reason": "Consumed proof requires review", "idempotency_key": uuid4().hex})
        # Remove only the synthetic manual fixture after PR2 has generated its own
        # real barrier, so this rejection proves PR2 independently.
        await self.db[controls.HOLDS].delete_one({"id": self.manual_hold["hold"]["id"]})
        lifecycle_before = await self.db.mezan_component_order_lifecycle_v1.find_one({})
        barrier = await self.db[controls.HOLDS].find_one({"contract_version": 3})
        state = await self.state()
        with self.assertRaises(HTTPException):
            await self.apply()
        self.assertEqual(await self.state(), state)
        self.assertEqual(await self.db[stock.UNITS].find_one({"order_line_id": "old"}), consumed)
        self.assertEqual(await self.db.mezan_component_order_lifecycle_v1.find_one({}), lifecycle_before)
        self.assertEqual(await self.db[controls.HOLDS].find_one({"_id": barrier["_id"]}), barrier)

    async def test_customer_options_and_notification_recipient_are_preserved(self):
        result = await self.apply()
        pieces = await self.db[controls.PIECES].find({"piece_id": {"$in": result["piece_ids"]}}).to_list(10)
        self.assertEqual(len(pieces), 2)
        self.assertTrue(all(p["source_options_snapshot"] == self.added["items"][1]["options"] for p in pieces))
        self.assertTrue(all(p["responsible_employee_id"] == "employee-1" for p in pieces))
        outbox = await self.db[controls.AUDIT].find_one({"event_type": "salla_add_notification_outbox"})
        self.assertEqual(outbox["recipients"], ["employee-1"])
        self.assertEqual(set(outbox["piece_ids"]), set(result["piece_ids"]))

    async def test_applied_addition_disappears_from_pending_projection(self):
        await self.apply()
        result = await application.pending_additions(self.db, user_id="owner", order_number="order-1", context=self.context)
        self.assertFalse(result["changes"])

    async def test_identical_newer_source_after_application_does_not_duplicate_or_rehold(self):
        await self.apply()
        before = await self.db[controls.PIECES].find({}).sort("_id", 1).to_list(100)
        newer = deepcopy(self.added)
        newer["version"] = "2040-01-02T12:00:00+00:00"
        result = await self.replay(newer)
        self.assertEqual(result["status"], "no_change")
        self.assertEqual(await self.db[controls.PIECES].find({}).sort("_id", 1).to_list(100), before)
        self.assertEqual(await self.db[controls.HOLDS].count_documents({"status": "active"}), 0)

    async def test_commercial_payload_cannot_be_smuggled_into_application(self):
        payload = await self.payload()
        before = await self.state()
        with self.assertRaises(HTTPException) as denied:
            await self.apply({**payload, "quantity": 100, "price": "1.00"})
        self.assertEqual(denied.exception.status_code, 422)
        self.assertEqual(await self.state(), before)

    async def test_active_execution_claim_blocks_application(self):
        await self.db[controls.EXECUTIONS].insert_one({"_id": controls._identity("owner", "order-1"),
            "user_id": "owner", "order_number": "order-1", "state": "uncertain"})
        before = await self.state()
        with self.assertRaises(HTTPException) as denied:
            await self.apply()
        self.assertEqual(denied.exception.detail["code"], "fulfillment_execution_in_flight")
        self.assertEqual(await self.state(), before)

    async def test_retry_required_component_intent_denies_application(self):
        await self.db.mezan_component_order_lifecycle_v1.update_one({}, {"$set": {"retry_required": True}})
        before = await self.state()
        with self.assertRaises(HTTPException):
            await self.apply()
        self.assertEqual(await self.state(), before)

    async def test_quantity_one_and_same_product_variant_options_are_distinct(self):
        result = await self.apply()
        self.assertEqual(len(result["piece_ids"]), 1)
        old = await self.db[stock.UNITS].find_one({"order_line_id": "old"})
        new = await self.db[stock.UNITS].find_one({"order_line_id": "new"})
        self.assertEqual(new["product_id"], old["product_id"])
        self.assertEqual(new["variant_id"], "variant-red")
        self.assertNotEqual(new["selected_context"], old["selected_context"])
        self.assertEqual(await self.db[stock.UNITS].count_documents({"order_line_id": "new"}), 1)

    async def test_mixed_old_piece_stages_and_reservations_are_unchanged(self):
        await self.db[controls.PIECES].update_one({"piece_id": "old-piece"}, {"$set": {"status": "ready_for_assembly"}})
        await self.db[controls.PIECES].insert_many([
            {"user_id": "owner", "order_number": "order-1", "piece_id": "old-received", "order_item_id": "old", "unit_index": 2,
             "status": "ready_for_assembly", "preparation_receipt_status": "received", "revision": 4, "generation": 0, "current": True, "active": True},
            {"user_id": "owner", "order_number": "order-1", "piece_id": "old-assigned", "order_item_id": "old", "unit_index": 3,
             "status": "assigned", "revision": 0, "generation": 0, "current": True, "active": True}])
        await stock.consume_component_stock(self.db, merchant_id="owner", order_id="order-1", units={"old": [1, 2]})
        pieces = await self.db[controls.PIECES].find({"order_item_id": "old"}).sort("_id", 1).to_list(10)
        units = await self.db[stock.UNITS].find({"order_line_id": "old"}).sort("_id", 1).to_list(10)
        result = await self.apply()
        self.assertEqual(len(result["piece_ids"]), 2)
        self.assertEqual(await self.db[controls.PIECES].find({"order_item_id": "old"}).sort("_id", 1).to_list(10), pieces)
        self.assertEqual(await self.db[stock.UNITS].find({"order_line_id": "old"}).sort("_id", 1).to_list(10), units)
        from preparation_piece_operations import _start_file_execution
        registry = await self.db[application.REGISTRY].find_one({"change_id": result["change_id"]})
        await _start_file_execution(self.db, user_id="owner", registry=registry,
            actor={"id": "employee-1", "name": "Synthetic worker"}, note="Start only new C",
            permissions={"app.page.my_products", "preparation.assigned.work"})
        self.assertEqual(await self.db[controls.PIECES].count_documents({"piece_id": {"$in": result["piece_ids"]}, "status": "in_progress"}), 2)
        self.assertEqual(await self.db[controls.PIECES].find({"order_item_id": "old"}).sort("_id", 1).to_list(10), pieces)
        self.assertEqual(await self.db[stock.UNITS].find({"order_line_id": "old"}).sort("_id", 1).to_list(10), units)

    async def test_independent_client_delete_race_has_only_safe_outcomes(self):
        payload = await self.payload()
        removed = deepcopy(self.initial)
        removed["version"] = "2040-01-02T12:00:00+00:00"
        other = AsyncIOMotorClient(os.environ["MZ2_TEST_MONGO_URI"], tz_aware=True)
        try:
            results = await asyncio.gather(self.apply(payload), intake.replay_snapshot(other[self.db.name], user_id="owner",
                order_number="order-1", snapshot=removed, idempotency_key=uuid4().hex), return_exceptions=True)
            self.assertIsInstance(results[1], dict)
            if isinstance(results[0], HTTPException):
                self.assertEqual(await self.db[controls.PIECES].count_documents({"order_item_id": "new"}), 0)
                self.assertEqual(await self.db[stock.UNITS].count_documents({"order_line_id": "new"}), 0)
            else:
                self.assertIsInstance(results[0], dict)
                self.assertEqual(results[0]["status"], "applied")
                self.assertEqual(await self.db[controls.PIECES].count_documents({"order_item_id": "new"}), 2)
            self.assertGreater(await self.db[controls.HOLDS].count_documents({"status": "active"}), 0)
            with self.assertRaises(HTTPException):
                async with controls.execution_scope(self.db, user_id="owner", targets=[{"order_number": "order-1"}], operation="worker"):
                    self.fail("DELETE race left execution unguarded")
        finally:
            other.close()


    async def test_same_change_sibling_holds_release_only_matching_item(self):
        holds = await self.db[controls.HOLDS].find({"change_id": self.event["change_id"]}).to_list(10)
        own = {h["id"] for h in holds if h["order_item_id"] == "new"}
        sibling_before = next(h for h in holds if h["order_item_id"] == "new-sibling")
        first = await self.apply()
        self.assertEqual(set(first["released_hold_ids"]), own)
        self.assertEqual(await self.db[controls.HOLDS].find_one({"_id": sibling_before["_id"]}), sibling_before)
        sibling = next(e for e in self.source_result["events"] if e["event_id"] != self.event["event_id"])
        payload = {**await self.payload(), "event_id": sibling["event_id"]}
        second = await self.apply(payload)
        self.assertEqual(second["released_hold_ids"], [sibling_before["id"]])
        self.assertTrue(second["eligible_for_execution"])
        self.assertEqual(await self.db[controls.PIECES].count_documents({"change_id": self.event["change_id"]}), 3)
        self.assertEqual(await self.db[controls.HOLDS].count_documents({"status": "active"}), 0)

    async def test_shipping_and_invoice_evidence_block_local_application(self):
        for field in ("awb_number", "salla_shipment_id", "qoyod_invoice_id", "manual_qoyod_invoice_id"):
            with self.subTest(field=field):
                await self.db.unified_orders.update_one({"user_id": "owner", "order_number": "order-1"}, {"$set": {field: "synthetic"}})
                with self.assertRaises(HTTPException):
                    await self.apply()
                await self.db.unified_orders.update_one({"user_id": "owner", "order_number": "order-1"}, {"$unset": {field: ""}})
        await self.db.qoyod_invoices.insert_one({"user_id": "owner", "reference": "order-1"})
        with self.assertRaises(HTTPException):
            await self.apply()
        self.assertEqual(await self.db[controls.PIECES].count_documents({"order_item_id": "new"}), 0)

    async def test_component_insert_failure_rolls_back_every_local_effect(self):
        await self.db.command({"collMod": stock.UNITS, "validator": {"order_line_id": {"$ne": "new"}}, "validationLevel": "strict"})
        before = await self.state()
        with self.assertRaises(OperationFailure) as denied:
            await self.apply()
        self.assertEqual(denied.exception.code, 121)
        self.assertEqual(await self.state(), before)

    async def test_allocation_insert_failure_rolls_back_every_local_effect(self):
        if application.ALLOCATIONS not in await self.db.list_collection_names():
            await self.db.create_collection(application.ALLOCATIONS)
        await self.db.command({"collMod": application.ALLOCATIONS, "validator": {"order_item_id": {"$ne": "new"}}, "validationLevel": "strict"})
        before = await self.state()
        with self.assertRaises(OperationFailure) as denied:
            await self.apply()
        self.assertEqual(denied.exception.code, 121)
        self.assertEqual(await self.state(), before)

    async def test_assignment_insert_failure_rolls_back_every_local_effect(self):
        if application.REGISTRY not in await self.db.list_collection_names():
            await self.db.create_collection(application.REGISTRY)
        await self.db.command({"collMod": application.REGISTRY, "validator": {"responsible_employee_id": {"$ne": "employee-1"}}, "validationLevel": "strict"})
        before = await self.state()
        with self.assertRaises(OperationFailure) as denied:
            await self.apply()
        self.assertEqual(denied.exception.code, 121)
        self.assertEqual(await self.state(), before)

    async def test_every_hold_identity_field_is_required_before_any_write(self):
        hold = await self.db[controls.HOLDS].find_one({"change_id": self.event["change_id"]})
        for field, value in (("change_id", "other"), ("order_item_id", "other"), ("unit_index", 99),
                             ("generation", 1), ("revision", 999), ("authority", "other"),
                             ("scope", "order"), ("contract_version", 4)):
            with self.subTest(field=field):
                await self.db[controls.HOLDS].update_one({"_id": hold["_id"]}, {"$set": {field: value}})
                before = await self.state()
                with self.assertRaises(HTTPException):
                    await self.apply()
                self.assertEqual(await self.state(), before)
                await self.db[controls.HOLDS].replace_one({"_id": hold["_id"]}, hold)

    async def test_add_identity_is_preserved_on_new_units_and_exact_holds_released(self):
        before = await self.db[controls.HOLDS].find({"change_id": self.event["change_id"]}).to_list(10)
        result = await self.apply()
        self.assertEqual(set(result["released_hold_ids"]), {h["id"] for h in before})
        pieces = await self.db[controls.PIECES].find({"piece_id": {"$in": result["piece_ids"]}}).to_list(10)
        for piece in pieces:
            self.assertEqual(piece["change_id"], self.event["change_id"])
            self.assertEqual(piece["order_item_id"], "new")
            self.assertEqual(piece["generation"], 0)
            self.assertEqual(piece["revision"], self.event["revision"])
            self.assertEqual(piece["application_revision"], result["revision"])
            self.assertEqual(piece["status"], "assigned")
        self.assertEqual({p["unit_index"] for p in pieces}, {1, 2})
        for hold in before:
            saved = await self.db[controls.HOLDS].find_one({"_id": hold["_id"]})
            self.assertEqual(saved["status"], "released")
            for field in ("change_id", "order_item_id", "unit_index", "generation", "revision"):
                self.assertEqual(saved[field], hold[field])
        workflow = await self.db[controls.WORKFLOWS].find_one({})
        self.assertEqual(workflow["stage"], "in_progress")
        self.assertEqual(next(x for x in workflow["items"] if x["order_item_id"] == "new")["review_status"], "reviewed")

    async def test_duplicate_webhook_does_not_create_additional_holds_or_units(self):
        before = await self.db[controls.HOLDS].find({}).sort("_id", 1).to_list(100)
        repeated = await self.replay(self.added)
        self.assertEqual(await self.db[controls.HOLDS].find({}).sort("_id", 1).to_list(100), before)
        self.assertEqual(await self.db[controls.PIECES].count_documents({"order_item_id": "new"}), 0)
        await self.apply()
        self.assertEqual(await self.db[controls.PIECES].count_documents({"order_item_id": "new"}), 2)

    async def test_different_apply_keys_race_creates_one_complete_application(self):
        one, two = await self.payload(), await self.payload()
        other = AsyncIOMotorClient(os.environ["MZ2_TEST_MONGO_URI"], tz_aware=True)
        try:
            results = await asyncio.gather(self.apply(one), self.apply(two, db=other[self.db.name]))
            self.assertEqual(results[0]["application_id"], results[1]["application_id"])
            self.assertEqual(await self.db[controls.PIECES].count_documents({"order_item_id": "new"}), 2)
            self.assertEqual(await self.db[controls.AUDIT].count_documents({"event_type": "salla_add_applied"}), 1)
        finally:
            other.close()

    async def test_financial_sentinels_and_provider_source_are_unchanged(self):
        names = ("qoyod_invoices", "mezan_journal_entries", "mezan_refunds", "mezan_accounting_events")
        for name in names:
            await self.db[name].insert_one({"_id": "sentinel", "user_id": "foreign", "value": "untouched"})
        financial = {n: await self.db[n].find({}).to_list(100) for n in names}
        source = await self.db.unified_orders.find({}).to_list(100)
        result = await self.apply()
        self.assertFalse(result["salla_updated"])
        self.assertFalse(result["accounting_updated"])
        self.assertEqual(await self.db.unified_orders.find({}).to_list(100), source)
        for name in names:
            self.assertEqual(await self.db[name].find({}).to_list(100), financial[name])

    async def test_employee_sees_image_variant_options_custom_fields_and_required_action(self):
        from preparation_piece_operations import _my_work_view
        result = await self.apply()
        work = await _my_work_view(self.db, user_id="owner", employee_id="employee-1", limit=100)
        pieces = [p for p in work["pieces"] if p["piece_id"] in result["piece_ids"]]
        self.assertEqual(len(pieces), 2)
        for piece in pieces:
            self.assertEqual(piece["image_url"], "https://example.test/product.png")
            self.assertEqual(piece["product_name"], "Synthetic added product")
            self.assertEqual(piece["source_options_snapshot"], self.added["items"][-1]["options"])
            self.assertEqual(piece["source_custom_fields_snapshot"], {"message": "full customer text"})
            self.assertEqual(piece["source_label"], "\u0645\u0646\u062a\u062c \u0645\u0636\u0627\u0641 \u0625\u0644\u0649 \u0627\u0644\u0637\u0644\u0628")
        raw = await self.db[controls.PIECES].find_one({"piece_id": result["piece_ids"][0]})
        self.assertEqual(raw["variant_id"], "red-variant")
        self.assertEqual(raw["source_options_snapshot"], self.added["items"][-1]["options"])
        self.assertEqual(raw["source_custom_fields_snapshot"], {"message": "full customer text"})
        batch = await self.db[application.BATCHES].find_one({"change_id": result["change_id"]})
        self.assertEqual(batch["allocated_quantity"], 2)
        fields = {f["name"]: f["value"] for f in batch["lines"][0]["file_spec_fields"]}
        self.assertEqual(fields["message"], "full customer text")
        self.assertEqual(fields["color"], "red")
        outbox = await self.db[controls.AUDIT].find_one({"event_type": "salla_add_notification_outbox"})
        self.assertEqual(outbox["required_action"], "prepare_assigned_units")
        self.assertEqual(outbox["event_id"], self.event["event_id"])

    async def test_distinct_added_items_concurrent_apply_then_retry(self):
        sibling = next(e for e in self.source_result["events"] if e["event_id"] != self.event["event_id"])
        first = await self.payload()
        second = {**await self.payload(), "event_id": sibling["event_id"]}
        other = AsyncIOMotorClient(os.environ["MZ2_TEST_MONGO_URI"], tz_aware=True)
        try:
            results = await asyncio.gather(self.apply(first), self.apply(second, db=other[self.db.name]), return_exceptions=True)
            self.assertEqual(sum(isinstance(r, dict) for r in results), 1)
            self.assertEqual(sum(isinstance(r, HTTPException) for r in results), 1)
            failed = next(p for p, r in zip((first, second), results) if isinstance(r, HTTPException))
            fresh = {**await self.payload(), "event_id": failed["event_id"]}
            await self.apply(fresh)
            self.assertEqual(await self.db[controls.PIECES].count_documents({"change_id": self.event["change_id"]}), 3)
            self.assertEqual(await self.db[controls.AUDIT].count_documents({"event_type": "salla_add_applied"}), 2)
            self.assertEqual(await self.db[controls.HOLDS].count_documents({"status": "active"}), 0)
            self.assertEqual(await self.db[stock.UNITS].count_documents({"order_line_id": "new"}), 2)
            self.assertEqual(await self.db[stock.UNITS].count_documents({"order_line_id": "new-sibling"}), 1)
        finally:
            other.close()


if __name__ == "__main__":
    unittest.main()
