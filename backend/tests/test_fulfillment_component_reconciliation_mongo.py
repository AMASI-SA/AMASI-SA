"""PR2 real local replica-set acceptance; no mocked transaction or database."""
import asyncio
import os
import unittest
from copy import deepcopy
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

from fastapi import HTTPException
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo.uri_parser import parse_uri

import fulfillment_lifecycle as lifecycle
import fulfillment_component_reconciliation as reconciliation
import stock_component_consumption_service as stock


class ReconciliationMongoTests(unittest.IsolatedAsyncioTestCase):
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
        self.assertEqual(hello.get("setName"), "lifecyclepr2")
        self.assertTrue(hello.get("isWritablePrimary"))
        self.assertEqual((await self.client.admin.command("buildInfo"))["version"], "8.0.12")
        self.db = self.client["component_reconciliation_pr2_" + uuid4().hex]
        flags = patch.dict(os.environ, {lifecycle.FLAG: "true", reconciliation.FLAG: "true"})
        flags.start()
        self.addCleanup(flags.stop)
        self.context = {"merchant_id": "owner", "actor_id": "owner", "is_owner": True,
                        "permissions": set(), "actor_name": "Synthetic reconciliation operator"}
        await self.db.mz2_atomic_owners.insert_one({"_id": "owner", "revision": 0,
            "writes_paused": False, "control_revision": 0})
        await stock.ensure_component_consumption_indexes(self.db)
        await self.db.settings.insert_one({"user_id": "owner", "g47_inventory": {
            "component_lifecycle_starts_at": "2040-01-01T00:00:00+00:00"}})
        await self.db[stock.PRODUCTS].insert_one({"user_id": "owner", "id": "p", "salla_product_id": "p"})
        await self.db[stock.RESOURCES].insert_one({"user_id": "owner", "id": "r", "kind": "stock_component", "track_inventory": True})
        await self.db[stock.PRODUCT_BINDINGS].insert_one({"id": "binding", "user_id": "owner",
            "salla_product_id": "p", "resource_id": "r", "quantity": 2})
        await self.db[stock.LOCATIONS].insert_one({"user_id": "owner", "id": "location", "warehouse_id": "warehouse",
            "state": "occupied", "occupancy": {"items": [{"item_type": "stock_component", "resource_id": "r",
                "receipt_id": "lot", "quantity": 30}], "total_quantity": 30}})
        await stock.reserve_component_stock(self.db, merchant_id="owner", order_id="order-1",
            lines=[{"order_line_id": "line", "product_id": "p", "quantity": 3,
                "options_normalized": {"color": "red"},
                "custom_fields": [{"field_id": "engraving", "name": "engraving", "value": "Synthetic inscription"}]}], source_version=1,
            source_created_at="2040-01-02T00:00:00+00:00")
        await self.db[lifecycle.WORKFLOWS].insert_one({"user_id": "owner", "order_number": "order-1",
            "stage": "in_progress", "revision": 0, "items": [{"order_item_id": "line"}]})
        await self.db.mezan_component_order_lifecycle_v1.insert_one({"user_id": "owner", "order_number": "order-1",
            "generation": 1, "state": "reserved", "accepted": True})
        caps = await lifecycle.capabilities(self.db, user_id="owner", order_number="order-1", context=self.context)
        self.held = await lifecycle.create_hold(self.db, user_id="owner", order_number="order-1", context=self.context,
            payload={"scope": "order", "stop_type": "edit", "reason": "Synthetic component review", "idempotency_key": uuid4().hex,
                "expected_revision": caps["revision"], "expected_generation": caps["generation"]})
        await self.db[stock.PRODUCT_BINDINGS].update_one({"id": "binding"}, {"$set": {"quantity": 3}})

    async def cleanup(self):
        if hasattr(self, "db"):
            self.assertTrue(self.db.name.startswith("component_reconciliation_pr2_"))
            await self.client.drop_database(self.db.name)
        if hasattr(self, "client"):
            self.client.close()

    async def rows(self):
        return await self.db[stock.UNITS].find({"order_id": "order-1"}).sort("unit_index", 1).to_list(20)

    async def plan(self):
        return await self.db[stock.PLANS].find_one({"order_id": "order-1"})

    async def payload(self, units=None):
        if units is None:
            units = [{"order_item_id": row["order_line_id"], "unit_index": row["unit_index"], "generation": row.get("generation", 0)}
                     for row in await self.rows()]
        preview = await reconciliation.preview_reconciliation(self.db, user_id="owner", order_number="order-1",
            context=self.context, units=units)
        caps = await lifecycle.capabilities(self.db, user_id="owner", order_number="order-1", context=self.context)
        return {"units": units, "expected_revision": caps["revision"], "expected_generation": caps["generation"],
            "expected_plan_revision": (await self.plan()).get("reconciliation_revision", 0),
            "expected_recipe_hash": preview.get("expected_recipe_hash", preview.get("recipe_hash")),
            "reason": "Reconcile reserved component recipe", "idempotency_key": uuid4().hex}

    async def reconcile(self, payload=None, db=None, context=None):
        return await reconciliation.reconcile_components(self.db if db is None else db,
            user_id="owner", order_number="order-1", context=self.context if context is None else context,
            payload=await self.payload() if payload is None else payload)

    @staticmethod
    def allocated(rows):
        return sum(Decimal(allocation["quantity"]) for row in rows for allocation in row["allocations"])

    async def test_allocation_delta_advances_generation_but_keeps_hold_and_physical_state(self):
        original = await self.rows()
        original_plan = await self.plan()
        occupancy = await self.db[stock.LOCATIONS].find_one({})
        audit_before = await self.db[lifecycle.AUDIT].count_documents({})
        result = await self.reconcile()
        changed = await self.rows()
        self.assertEqual(self.allocated(original), 6)
        self.assertEqual(self.allocated(changed), 9)
        self.assertTrue(all(row["generation"] == 1 for row in changed))
        self.assertTrue(all(row["state"] == "reserved" for row in changed))
        self.assertEqual((await self.plan())["reconciliation_revision"], 1)
        self.assertEqual((await self.plan())["lines"], original_plan["lines"])
        self.assertTrue(original[0]["selected_context"])
        self.assertTrue(any(row[0].startswith("field-context:") for row in original[0]["selected_context"]))
        self.assertEqual([row["selected_context"] for row in changed], [row["selected_context"] for row in original])
        self.assertEqual(await self.db[stock.LOCATIONS].find_one({}), occupancy)
        self.assertEqual(await self.db[lifecycle.AUDIT].count_documents({}), audit_before + 1)
        self.assertEqual((await self.db[lifecycle.HOLDS].find_one({"id": self.held["hold"]["id"]}))["status"], "active")
        component = await self.db.mezan_component_order_lifecycle_v1.find_one({"order_number": "order-1"})
        self.assertEqual(component["state"], "reconciliation_required")
        self.assertFalse(component["accepted"])
        self.assertIsInstance(result, dict)

    async def test_partial_consumption_preserves_consumed_unit_and_reconciles_reserved_units(self):
        await stock.consume_component_stock(self.db, merchant_id="owner", order_id="order-1", units={"line": [1]})
        consumed = (await self.rows())[0]
        occupancy = await self.db[stock.LOCATIONS].find_one({})
        await self.reconcile()
        after = await self.rows()
        self.assertEqual(after[0], consumed)
        self.assertEqual(self.allocated(after[1:]), 6)
        self.assertTrue(all(row["generation"] == 1 for row in after[1:]))
        self.assertEqual(await self.db[stock.LOCATIONS].find_one({}), occupancy)

    async def test_stale_fences_reject_without_allocation_changes(self):
        payload = await self.payload()
        before = await self.rows()
        for change, code in (({"expected_revision": 99}, "fulfillment_revision_conflict"),
                ({"expected_generation": "0" * 64}, "fulfillment_generation_conflict"),
                ({"expected_plan_revision": 99}, "component_reconciliation_plan_revision_conflict"),
                ({"expected_recipe_hash": "0" * 64}, "component_reconciliation_preview_conflict")):
            with self.subTest(change=change), self.assertRaises(HTTPException) as denied:
                await self.reconcile({**payload, **change})
            self.assertEqual(denied.exception.detail["code"], code)
            self.assertEqual(await self.rows(), before)

    async def test_recipe_change_after_preview_rejects_stale_hash(self):
        payload = await self.payload()
        before = await self.rows()
        await self.db[stock.PRODUCT_BINDINGS].update_one({"id": "binding"}, {"$set": {"quantity": 4}})
        with self.assertRaises(HTTPException):
            await self.reconcile(payload)
        self.assertEqual(await self.rows(), before)

    async def test_response_loss_replay_commits_one_reconciliation(self):
        payload = await self.payload()
        first = await self.reconcile(deepcopy(payload))
        rows = await self.rows()
        audit_count = await self.db[lifecycle.AUDIT].count_documents({})
        replay = await self.reconcile(deepcopy(payload))
        self.assertTrue(replay["idempotent_replay"])
        self.assertEqual(await self.rows(), rows)
        self.assertEqual(await self.db[lifecycle.AUDIT].count_documents({}), audit_count)
        self.assertEqual((await self.plan())["reconciliation_revision"], 1)
        self.assertIsInstance(first, dict)

    async def test_duplicate_identity_and_key_reuse_fail_without_second_write(self):
        payload = await self.payload()
        with self.assertRaises(HTTPException):
            await self.reconcile({**payload, "units": payload["units"] + [payload["units"][0]]})
        await self.reconcile(payload)
        after = await self.rows()
        with self.assertRaises(HTTPException):
            await self.reconcile({**payload, "reason": "Different operation with reused key"})
        self.assertEqual(await self.rows(), after)

    async def test_independent_clients_race_one_revision_winner(self):
        payload = await self.payload()
        other = AsyncIOMotorClient(os.environ["MZ2_TEST_MONGO_URI"], serverSelectionTimeoutMS=5000)
        try:
            results = await asyncio.gather(self.reconcile(payload), self.reconcile(
                {**payload, "idempotency_key": uuid4().hex}, db=other[self.db.name]), return_exceptions=True)
            self.assertEqual(sum(isinstance(result, dict) for result in results), 1, repr(results))
            self.assertEqual(sum(isinstance(result, HTTPException) for result in results), 1, repr(results))
            self.assertEqual((await self.plan())["reconciliation_revision"], 1)
        finally:
            other.close()

    async def test_audit_insert_failure_rolls_back_allocations_plan_and_request(self):
        payload = await self.payload()
        before, plan = await self.rows(), await self.plan()
        requests = await self.db[lifecycle.REQUESTS].count_documents({})
        await self.db.command({"collMod": lifecycle.AUDIT, "validator": {"_id": {"$exists": False}},
                               "validationLevel": "strict"})
        with self.assertRaises(Exception):
            await self.reconcile(payload)
        self.assertEqual(await self.rows(), before)
        self.assertEqual(await self.plan(), plan)
        self.assertEqual(await self.db[lifecycle.REQUESTS].count_documents({}), requests)
        self.assertEqual(await self.db[lifecycle.HOLDS].count_documents({"contract_version": 3}), 0)
        await self.db.command({"collMod": lifecycle.AUDIT, "validator": {}})
        await self.reconcile(payload)
        self.assertEqual((await self.plan())["reconciliation_revision"], 1)

    async def test_flags_and_permissions_fail_closed(self):
        payload = await self.payload()
        before = await self.rows()
        for flag in (lifecycle.FLAG, reconciliation.FLAG):
            for value in ("false", "TRUE", "1"):
                with self.subTest(flag=flag, value=value), patch.dict(os.environ, {flag: value}), self.assertRaises(HTTPException):
                    await self.reconcile(payload)
        with self.assertRaises(HTTPException):
            await self.reconcile(payload, context={"merchant_id": "owner", "actor_id": "employee", "is_owner": False, "permissions": set()})
        self.assertEqual(await self.rows(), before)

    async def test_order_hold_is_required_even_with_all_valid_fences(self):
        payload = await self.payload()
        await self.db[lifecycle.HOLDS].update_one({"id": self.held["hold"]["id"]}, {"$set": {"status": "released"}})
        before = await self.rows()
        with self.assertRaises(HTTPException):
            await self.reconcile(payload)
        self.assertEqual(await self.rows(), before)

    async def test_webhook_source_capture_invalidates_preview_without_changing_allocations(self):
        from fulfillment_v2_routes import persist_component_source_snapshot
        payload = await self.payload()
        before = await self.rows()
        async def persist(scoped):
            await scoped.unified_orders.update_one({"user_id": "owner", "order_number": "order-1"},
                {"$set": {"order_status_slug": "in_progress"}}, upsert=True)
            return {"synced": True}
        captured = await persist_component_source_snapshot(self.db, user_id="owner", order_number="order-1",
            payload={"id": 901, "status": {"slug": "in_progress"}, "updated_at": "2040-01-03T12:00:00+00:00"},
            persist=persist)
        self.assertTrue(captured["synced"])
        with self.assertRaises(HTTPException):
            await self.reconcile(payload)
        self.assertEqual(await self.rows(), before)

    async def test_insufficient_stock_rolls_back_all_selected_units(self):
        payload = await self.payload()
        before = await self.rows()
        await self.db[stock.LOCATIONS].update_one({}, {"$set": {"occupancy.items.0.quantity": 7, "occupancy.total_quantity": 7}})
        with self.assertRaises(HTTPException):
            await self.reconcile(payload)
        self.assertEqual(await self.rows(), before)
        self.assertEqual((await self.plan()).get("reconciliation_revision", 0), 0)

    async def test_full_cycle_preserves_prior_audit_and_accepts_next_explicit_generation(self):
        original_hold_event = await self.db[lifecycle.AUDIT].find_one({})
        first = await self.reconcile()
        first_audit = await self.db[lifecycle.AUDIT].find_one({"change_id": first["change_id"]})
        self.assertIsNotNone(first_audit)
        await self.db[stock.PRODUCT_BINDINGS].update_one({"id": "binding"}, {"$set": {"quantity": 4}})
        second = await self.reconcile()
        self.assertNotEqual(first["change_id"], second["change_id"])
        self.assertEqual(self.allocated(await self.rows()), 12)
        self.assertTrue(all(row["generation"] == 2 for row in await self.rows()))
        self.assertEqual(await self.db[lifecycle.AUDIT].find_one({"_id": original_hold_event["_id"]}), original_hold_event)
        self.assertEqual(await self.db[lifecycle.AUDIT].find_one({"_id": first_audit["_id"]}), first_audit)

    async def test_intraunit_partial_consumption_requires_review_without_guessing_balance(self):
        await self.db[stock.UNITS].update_one({"unit_index": 1}, {"$set": {"allocations.0.consumed_quantity": "1"}})
        before = (await self.rows())[0]
        occupancy = await self.db[stock.LOCATIONS].find_one({})
        result = await self.reconcile()
        self.assertEqual(result["state"], "reconciliation_required")
        self.assertIn("ambiguous_or_partial_consumption", {row["reason"] for row in result["required_actions"]})
        self.assertEqual((await self.rows())[0], before)
        self.assertEqual(await self.db[stock.LOCATIONS].find_one({}), occupancy)

    async def test_prebuilt_provenance_unchanged_and_requires_explicit_review(self):
        await self.db[stock.UNITS].update_one({"unit_index": 1}, {"$set": {"prebuilt": {
            "source_unit_id": "synthetic-provenance", "receipt_id": "synthetic-finished-lot"}}})
        before = (await self.rows())[0]
        result = await self.reconcile()
        self.assertEqual(result["state"], "reconciliation_required")
        self.assertIn("prebuilt_provenance_requires_review", {row["reason"] for row in result["required_actions"]})
        self.assertEqual((await self.rows())[0], before)

    async def test_preparation_dependencies_stay_frozen_and_require_later_reconciliation(self):
        await self.db[lifecycle.PIECES].insert_one({"user_id": "owner", "order_number": "order-1",
            "order_item_id": "line", "unit_index": 1, "piece_id": "prepared-unit-1", "status": "in_progress",
            "responsible_employee_id": "employee", "customer_options": {"color": "red"}, "generation": 0})
        await self.db[reconciliation.PREPARATION_ALLOCATIONS].insert_one({"user_id": "owner", "order_number": "order-1",
            "order_item_id": "line", "unit_index": 1, "assignment_id": "synthetic-assignment", "status": "assigned"})
        piece = await self.db[lifecycle.PIECES].find_one({})
        assignment = await self.db[reconciliation.PREPARATION_ALLOCATIONS].find_one({})
        result = await self.reconcile()
        self.assertEqual(result["state"], "reconciliation_required")
        self.assertIn("preparation_assignment_requires_review", {row["reason"] for row in result["required_actions"]})
        self.assertEqual(await self.db[lifecycle.PIECES].find_one({}), piece)
        self.assertEqual(await self.db[reconciliation.PREPARATION_ALLOCATIONS].find_one({}), assignment)
        self.assertEqual((await self.db[lifecycle.HOLDS].find_one({"id": self.held["hold"]["id"]}))["status"], "active")

    async def test_active_and_uncertain_execution_claims_block_reconciliation(self):
        payload = await self.payload()
        before = await self.rows()
        for state in ("active", "uncertain"):
            await self.db[lifecycle.EXECUTIONS].update_one({"_id": lifecycle._identity("owner", "order-1")},
                {"$set": {"user_id": "owner", "order_number": "order-1", "state": state}}, upsert=True)
            with self.subTest(state=state), self.assertRaises(HTTPException) as blocked:
                await self.reconcile(payload)
            self.assertEqual(blocked.exception.detail["code"], "fulfillment_execution_in_flight")
            self.assertEqual(await self.rows(), before)

    async def test_preview_is_read_only_and_commercial_input_is_rejected(self):
        before = await self.rows()
        plan = await self.plan()
        audit_count = await self.db[lifecycle.AUDIT].count_documents({})
        payload = await self.payload()
        self.assertEqual(await self.rows(), before)
        self.assertEqual(await self.plan(), plan)
        self.assertEqual(await self.db[lifecycle.AUDIT].count_documents({}), audit_count)
        with self.assertRaises(HTTPException):
            await self.reconcile({**payload, "quantity": 50})
        with self.assertRaises(HTTPException):
            await self.payload(units=[{"order_item_id": "new-commercial-line", "unit_index": 1, "generation": 0}])
        self.assertEqual(await self.rows(), before)

    async def test_source_replay_and_pr1_resume_cannot_remove_pr2_activation_barrier(self):
        import hashlib
        from datetime import datetime
        from fulfillment_v2_routes import reconcile_component_order_lifecycle
        from order_engine.models import OrderDTO, OrderItemDTO, OrderSourceDTO, PaymentDTO, ShippingDTO, AddressDTO
        # Align the legacy fixture identity with the actual webhook authority.
        identity = hashlib.sha256(b"owner:order-1").hexdigest()
        intent = await self.db[reconciliation.LIFECYCLES].find_one({"order_number": "order-1"})
        await self.db[reconciliation.LIFECYCLES].delete_one({"_id": intent["_id"]})
        await self.db[reconciliation.LIFECYCLES].insert_one({**intent, "_id": identity})
        stamp = "2040-01-02T12:00:00+00:00"
        await self.db.unified_orders.insert_one({"user_id": "owner", "order_number": "order-1",
            "raw_by_source": {"salla_direct": {"date": stamp}}})
        order = OrderDTO(order_id="order-1", order_number="order-1", created_at=datetime.fromisoformat(stamp),
            source=OrderSourceDTO(source_order_id="order-1"), status="under_review", payment=PaymentDTO(method="cod"),
            shipping=ShippingDTO(address=AddressDTO(city="Synthetic", street="Synthetic")),
            items=[OrderItemDTO(order_item_id="line", product_id="p", name="Synthetic product", quantity=3,
                options_normalized={"color": "red"},
                custom_fields=[{"field_id": "engraving", "name": "engraving", "value": "Synthetic inscription"}])])
        initial = await reconcile_component_order_lifecycle(self.db, user_id="owner", order=order,
            source_updated_at=stamp, decision={"lines": []}, strict=True)
        self.assertEqual(initial["state"], "reserved")
        result = await self.reconcile()
        barrier = await self.db[lifecycle.HOLDS].find_one({"id": result["activation_hold_id"]})
        self.assertEqual(barrier["contract_version"], 3)
        self.assertEqual(barrier["authority"], "order_change_pr2")
        replayed = await reconcile_component_order_lifecycle(self.db, user_id="owner", order=order,
            source_updated_at=stamp, decision={"lines": []}, strict=True)
        self.assertEqual(replayed["state"], "reserved")
        caps = await lifecycle.capabilities(self.db, user_id="owner", order_number="order-1", context=self.context)
        release = {"reason": "Release original operational hold", "idempotency_key": uuid4().hex,
                   "expected_revision": caps["revision"], "expected_generation": caps["generation"]}
        await lifecycle.resume_hold(self.db, user_id="owner", hold_id=self.held["hold"]["id"], context=self.context, payload=release)
        with self.assertRaises(HTTPException) as held:
            await lifecycle.assert_not_held(self.db, user_id="owner", order_number="order-1", order_wide=True)
        self.assertEqual(held.exception.detail["code"], "fulfillment_lifecycle_held")
        with self.assertRaises(HTTPException):
            async with lifecycle.execution_scope(self.db, user_id="owner", targets=[{"order_number": "order-1"}], operation="synthetic-worker"):
                self.fail("PR2 activation barrier was bypassed")
        caps = await lifecycle.capabilities(self.db, user_id="owner", order_number="order-1", context=self.context)
        with self.assertRaises(HTTPException):
            await lifecycle.resume_hold(self.db, user_id="owner", hold_id=barrier["id"], context=self.context,
                payload={**release, "idempotency_key": uuid4().hex, "expected_revision": caps["revision"], "expected_generation": caps["generation"]})
        await self.db[stock.PRODUCT_BINDINGS].update_one({"id": "binding"}, {"$set": {"quantity": 4}})
        repeated = await self.reconcile()
        self.assertEqual(repeated["activation_hold_id"], barrier["id"])
        self.assertEqual(await self.db[lifecycle.HOLDS].find_one({"id": barrier["id"]}), barrier)

    async def test_subset_followup_retains_unresolved_consumed_unit_issue(self):
        await stock.consume_component_stock(self.db, merchant_id="owner", order_id="order-1", units={"line": [1]})
        consumed = (await self.rows())[0]
        first = await self.reconcile()
        unresolved = [row for row in first["required_actions"] if row["unit"]["unit_index"] == 1]
        self.assertTrue(unresolved)
        await self.db[stock.PRODUCT_BINDINGS].update_one({"id": "binding"}, {"$set": {"quantity": 4}})
        selected = [{"order_item_id": "line", "unit_index": 2, "generation": 1}]
        followup = await self.reconcile(await self.payload(units=selected))
        self.assertEqual(followup["state"], "reconciliation_required")
        self.assertTrue((await self.plan())["reconciliation_required"])
        for issue in unresolved:
            self.assertIn(issue, followup["required_actions"])
            self.assertIn(issue, (await self.plan())["reconciliation_required_actions"])
        self.assertEqual((await self.rows())[0], consumed)

    async def test_duplicate_stored_identity_rejected_without_selecting_arbitrary_row(self):
        payload = await self.payload()
        await self.db[stock.UNITS].drop_index("uq_component_order_unit")
        existing = (await self.rows())[0]
        await self.db[stock.UNITS].insert_one({**existing, "_id": "different-row-same-unit-identity"})
        before = await self.rows()
        with self.assertRaises(HTTPException) as duplicate:
            await self.reconcile(payload)
        self.assertEqual(duplicate.exception.detail["code"], "component_reconciliation_duplicate_unit_identity")
        self.assertEqual(await self.rows(), before)

    async def test_incompatible_activation_barrier_rolls_back_new_allocations(self):
        payload = await self.payload()
        barrier_id = "component-reconciliation-hold-" + lifecycle._identity("owner", "order-1")
        await self.db[lifecycle.HOLDS].insert_one({"_id": barrier_id, "id": barrier_id, "user_id": "owner",
            "order_number": "order-1", "status": "active", "scope": "order", "contract_version": 99,
            "authority": "order_change_pr2"})
        before, plan = await self.rows(), await self.plan()
        with self.assertRaises(HTTPException) as denied:
            await self.reconcile(payload)
        self.assertEqual(denied.exception.detail["code"], "component_reconciliation_activation_barrier_conflict")
        self.assertEqual(await self.rows(), before)
        self.assertEqual(await self.plan(), plan)

    async def test_same_key_concurrent_clients_commit_one_audit_and_replay(self):
        payload = await self.payload()
        audit_count = await self.db[lifecycle.AUDIT].count_documents({})
        other = AsyncIOMotorClient(os.environ["MZ2_TEST_MONGO_URI"], serverSelectionTimeoutMS=5000)
        try:
            results = await asyncio.gather(self.reconcile(deepcopy(payload)),
                self.reconcile(deepcopy(payload), db=other[self.db.name]))
            self.assertEqual({result["change_id"] for result in results}, {results[0]["change_id"]})
            self.assertEqual(sum(result["idempotent_replay"] for result in results), 1)
            self.assertEqual(await self.db[lifecycle.AUDIT].count_documents({}), audit_count + 1)
            self.assertEqual((await self.plan())["reconciliation_revision"], 1)
        finally:
            other.close()

    async def test_selected_unit_preserves_unselected_and_all_downstream_sentinels(self):
        sentinel_collections = [lifecycle.PIECES, reconciliation.PREPARATION_ALLOCATIONS,
            stock.CLAIMS, "accounting_journal_groups_v2", "accounting_general_ledger_v2",
            "supplier_invoices", "order_invoices", "mezan_shipping_labels_v1"]
        for name in sentinel_collections:
            await self.db[name].insert_one({"_id": "synthetic-sentinel", "user_id": "owner",
                "order_number": "order-1", "order_item_id": "line", "unit_index": 2,
                "piece_id": "untouched-piece", "status": "assigned", "amount": "19.50",
                "options": {"color": "red"}, "generation": 0})
        snapshots = {name: await self.db[name].find({}).to_list(30) for name in sentinel_collections + [stock.LOCATIONS]}
        before = await self.rows()
        selected = [{"order_item_id": "line", "unit_index": 1, "generation": 0}]
        result = await self.reconcile(await self.payload(units=selected))
        after = await self.rows()
        self.assertEqual(after[1:], before[1:])
        self.assertEqual(after[0]["generation"], 1)
        self.assertEqual(after[0]["selected_context"], before[0]["selected_context"])
        self.assertFalse(result["salla_updated"])
        self.assertFalse(result["accounting_updated"])
        for name, snapshot in snapshots.items():
            self.assertEqual(await self.db[name].find({}).to_list(30), snapshot, name)

    async def test_real_owner_transaction_race_with_webhook_has_only_safe_outcomes(self):
        from fulfillment_v2_routes import persist_component_source_snapshot
        payload = await self.payload()
        before = await self.rows()
        other = AsyncIOMotorClient(os.environ["MZ2_TEST_MONGO_URI"], serverSelectionTimeoutMS=5000)
        async def persist(scoped):
            await scoped.unified_orders.update_one({"user_id": "owner", "order_number": "order-1"},
                {"$set": {"order_status_slug": "in_progress"}}, upsert=True)
            return {"synced": True}
        try:
            results = await asyncio.gather(self.reconcile(payload), persist_component_source_snapshot(
                other[self.db.name], user_id="owner", order_number="order-1",
                payload={"id": 901, "status": {"slug": "in_progress"}, "updated_at": "2040-01-03T12:00:00+00:00"},
                persist=persist), return_exceptions=True)
            self.assertIsInstance(results[1], dict, repr(results))
            self.assertTrue(results[1]["synced"])
            source = await self.db.unified_orders.find_one({"order_number": "order-1"})
            self.assertTrue(source["g47_salla_snapshot"]["component_pending"])
            if isinstance(results[0], HTTPException):
                self.assertIn(results[0].detail["code"], {"fulfillment_generation_conflict", "component_reconciliation_source_pending"})
                self.assertEqual(await self.rows(), before)
                self.assertEqual(await self.db[lifecycle.AUDIT].count_documents({}), 1)
            else:
                self.assertIsInstance(results[0], dict, repr(results))
                self.assertTrue(results[0]["ok"])
                self.assertEqual(await self.db[lifecycle.AUDIT].count_documents({}), 2)
                self.assertEqual(await self.db[lifecycle.HOLDS].count_documents({"contract_version": 3, "status": "active"}), 1)
            self.assertEqual((await self.db[lifecycle.HOLDS].find_one({"id": self.held["hold"]["id"]}))["status"], "active")
        finally:
            other.close()

    async def test_service_import_boundary_excludes_provider_and_accounting_writers(self):
        import ast
        import inspect
        tree = ast.parse(inspect.getsource(reconciliation))
        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.add(node.module)
        self.assertLessEqual(imports, {"copy", "datetime", "os", "fastapi", "fulfillment_lifecycle",
            "stock_component_consumption_service", "operational_atomic"})
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                self.assertNotIn(node.func.id, {"eval", "exec", "__import__", "call_salla"})
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                self.assertNotIn(node.func.attr, {"consume_component_stock", "release_component_stock",
                    "call_salla", "issue_shipping_label", "import_module"})


if __name__ == "__main__":
    unittest.main()
