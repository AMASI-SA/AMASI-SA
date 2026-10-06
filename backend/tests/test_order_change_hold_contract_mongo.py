"""PR3.1 acceptance: real source replay and PR1 execution on local Mongo8."""
import asyncio
from copy import deepcopy
import unittest
from uuid import uuid4

from fastapi import HTTPException
import fulfillment_lifecycle as lifecycle
import salla_order_change_reconciliation as service
from order_change_hold_contract import ADD_KIND, IDENTITY_FIELDS, add_identity_matches
from tests import test_salla_order_change_reconciliation_mongo as fixture


class HoldContractTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.SallaChangeMongoTests.asyncSetUp
    cleanup = fixture.SallaChangeMongoTests.cleanup
    replay = fixture.SallaChangeMongoTests.replay
    baseline = fixture.SallaChangeMongoTests.baseline
    changed = fixture.SallaChangeMongoTests.changed
    pieces = fixture.SallaChangeMongoTests.pieces

    def added(self, quantity=1, item="new-line", version="2040-01-01T11:00:00+00:00"):
        s = self.changed()
        s["version"] = version
        s["items"].append({"order_item_id": item, "product_id": "product-new", "quantity": quantity,
                           "variant_id": "variant-new", "options": {"engraving": "Customer choice"}})
        return s

    async def state(self):
        names = [lifecycle.HOLDS, lifecycle.WORKFLOWS, lifecycle.AUDIT, lifecycle.REQUESTS,
                 lifecycle.PIECES, "mezan_component_consumption_units_v1", lifecycle.CONTROL_OWNERS]
        return {n: await self.db[n].find({}).sort("_id", 1).to_list(1000) for n in names}

    async def test_add_quantity_identity_no_order_hold_no_units_and_old_piece_executable(self):
        await self.baseline()
        old = await self.pieces()
        result = await self.replay(self.added(3))
        holds = await self.db[lifecycle.HOLDS].find({}).sort("unit_index", 1).to_list(10)
        self.assertEqual(len(holds), 3)
        for index, hold in enumerate(holds, 1):
            self.assertEqual((hold["scope"], hold["target_id"], hold["hold_kind"]), ("item", "new-line", ADD_KIND))
            self.assertEqual({k: hold[k] for k in IDENTITY_FIELDS}, dict(change_id=result["change_id"],
                order_item_id="new-line", unit_index=index, generation=0, revision=result["revision"]))
            self.assertEqual(hold["source_generation"], result["generation"])
        self.assertEqual(await self.db[lifecycle.HOLDS].count_documents({"scope": "order"}), 0)
        self.assertEqual(await self.pieces(), old)
        self.assertEqual(await self.db.mezan_component_consumption_units_v1.count_documents({}), 0)
        async with lifecycle.execution_scope(self.db, user_id="owner", targets=[{
                "order_number": "order-1", "order_item_id": "line-1", "piece_id": "piece-1"}], operation="old-piece"):
            pass
        for target in [{"order_number": "order-1", "order_item_id": "new-line"}, {"order_number": "order-1"}]:
            with self.assertRaises(HTTPException):
                async with lifecycle.execution_scope(self.db, user_id="owner", targets=[target], operation="new-or-shipping"):
                    self.fail("held target executed")

    async def test_manual_and_pr2_holds_survive_and_still_block_old_and_new_items(self):
        await self.baseline()
        workflow, generation = await lifecycle._snapshot(self.db, "owner", "order-1")
        await lifecycle.create_hold(self.db, user_id="owner", order_number="order-1", context=self.context,
            payload={"scope": "order", "stop_type": "note", "reason": "Manual hold", "idempotency_key": uuid4().hex,
                     "expected_revision": workflow["revision"], "expected_generation": generation})
        await self.db[lifecycle.HOLDS].insert_one({"_id": "pr2", "id": "pr2", "user_id": "owner",
            "order_number": "order-1", "target_id": "order-1", "scope": "order", "status": "active",
            "contract_version": 3, "authority": "order_change_pr2"})
        before = await self.db[lifecycle.HOLDS].find({}).to_list(10)
        await self.replay(self.added())
        for row in before:
            self.assertEqual(await self.db[lifecycle.HOLDS].find_one({"_id": row["_id"]}), row)
        for item in ("line-1", "new-line"):
            with self.assertRaises(HTTPException):
                await lifecycle.assert_not_held(self.db, user_id="owner", order_number="order-1", order_item_id=item)

    async def test_add_and_delete_keep_independent_stops(self):
        await self.baseline()
        snapshot = self.added()
        snapshot["items"] = snapshot["items"][1:]
        await self.replay(snapshot)
        rows = await self.db[lifecycle.HOLDS].find({}).to_list(10)
        self.assertEqual({r["target_id"] for r in rows}, {"line-1", "new-line"})
        self.assertEqual({r["hold_kind"] for r in rows}, {ADD_KIND, "SOURCE_ITEM_CHANGE_HOLD"})
        for item in ("line-1", "new-line"):
            with self.assertRaises(HTTPException):
                await lifecycle.assert_not_held(self.db, user_id="owner", order_number="order-1", order_item_id=item)

    async def test_concurrent_adds_same_snapshot_emit_each_unit_once(self):
        await self.baseline()
        snapshot = self.added(2)
        snapshot["items"].append({**snapshot["items"][-1], "order_item_id": "another-new-line", "options": {"engraving": "Different"}})
        first, second = await asyncio.gather(self.replay(snapshot), self.replay(snapshot))
        self.assertEqual(first["change_id"], second["change_id"])
        self.assertEqual(await self.db[lifecycle.HOLDS].count_documents({"hold_kind": ADD_KIND}), 4)
        self.assertEqual(await self.db[lifecycle.AUDIT].count_documents({"event_type": service.EVENT}), 2)

    async def test_duplicate_transport_and_response_loss_reuse_original_result(self):
        await self.baseline()
        key, snapshot = uuid4().hex, self.added(2)
        result = await self.replay(snapshot, key=key)
        before = await self.state()
        retry = await self.replay(snapshot, key=key)
        self.assertEqual(retry, {**result, "idempotent_replay": True})
        self.assertEqual(await self.state(), before)

    async def test_stale_revision_and_generation_reject_without_writes(self):
        await self.baseline()
        workflow, generation = await lifecycle._snapshot(self.db, "owner", "order-1")
        before = await self.state()
        for revision, gen in [(workflow["revision"] + 1, generation), (workflow["revision"], "0" * 64)]:
            with self.subTest(revision=revision, gen=gen), self.assertRaises(HTTPException):
                await service.replay_snapshot(self.db, user_id="owner", order_number="order-1", snapshot=self.added(),
                    idempotency_key=uuid4().hex, expected_revision=revision, expected_generation=gen)
            self.assertEqual(await self.state(), before)

    async def test_incomplete_payload_keeps_conservative_order_barrier(self):
        await self.baseline()
        await self.replay({**self.added(), "complete": False})
        self.assertEqual(await self.db[lifecycle.HOLDS].count_documents({"scope": "order", "status": "active"}), 1)
        self.assertEqual(await self.db[lifecycle.HOLDS].count_documents({"hold_kind": ADD_KIND}), 0)

    async def test_add_reusing_existing_piece_identity_stays_order_held(self):
        await self.baseline()
        await self.db[lifecycle.PIECES].insert_one({"_id": "archived", "user_id": "owner", "order_number": "order-1",
            "piece_id": "old-new", "order_item_id": "new-line", "unit_index": 1, "generation": 4, "status": "cancelled"})
        await self.replay(self.added())
        self.assertEqual(await self.db[lifecycle.HOLDS].count_documents({"scope": "order"}), 1)
        self.assertEqual(await self.db[lifecycle.HOLDS].count_documents({"hold_kind": ADD_KIND}), 0)

    async def test_audit_and_notification_failure_roll_back_all_add_holds(self):
        await self.baseline()
        for event in (service.EVENT, service.OUTBOX, service.INTAKE):
            before = await self.state()
            await self.db.command({"collMod": lifecycle.AUDIT, "validator": {"event_type": {"$ne": event}}, "validationLevel": "strict"})
            with self.assertRaises(Exception):
                await self.replay(self.added(3))
            self.assertEqual(await self.state(), before)
            await self.db.command({"collMod": lifecycle.AUDIT, "validator": {}})

    async def test_no_generic_resume_and_exact_identity_predicate(self):
        await self.baseline()
        result = await self.replay(self.added())
        hold = await self.db[lifecycle.HOLDS].find_one({"hold_kind": ADD_KIND})
        identity = {k: hold[k] for k in IDENTITY_FIELDS}
        self.assertTrue(add_identity_matches(hold, identity))
        for key in IDENTITY_FIELDS:
            wrong = {**identity, key: identity[key] + 1 if isinstance(identity[key], int) else identity[key] + "-wrong"}
            self.assertFalse(add_identity_matches(hold, wrong))
        self.assertFalse(add_identity_matches({**hold, "authority": "order_change_pr2"}, identity))
        self.assertFalse(add_identity_matches({**hold, "scope": "order"}, identity))
        self.assertFalse(add_identity_matches({**hold, "contract_version": 2}, identity))
        with self.assertRaises(HTTPException):
            await lifecycle.resume_hold(self.db, user_id="owner", hold_id=hold["id"], context=self.context,
                payload={"reason": "Do not resume", "idempotency_key": uuid4().hex,
                         "expected_revision": result["revision"], "expected_generation": result["generation"]})
        self.assertEqual((await self.db[lifecycle.HOLDS].find_one({"_id": hold["_id"]}))["status"], "active")

    async def test_old_global_hold_is_never_migrated_or_removed(self):
        await self.baseline()
        await self.replay({**self.changed(), "complete": False})
        old = await self.db[lifecycle.HOLDS].find_one({"scope": "order"})
        await self.replay(self.added())
        self.assertEqual(await self.db[lifecycle.HOLDS].find_one({"_id": old["_id"]}), old)

    async def test_safe_edit_and_replace_use_item_targets_only(self):
        await self.baseline()
        await self.replay(self.changed(options={"color": "blue"}))
        self.assertEqual(await self.db[lifecycle.HOLDS].count_documents({"scope": "order"}), 0)
        snapshot = self.added(version="2040-01-01T12:00:00+00:00")
        snapshot["items"] = snapshot["items"][1:]
        snapshot["replacements"] = [{"old_item_id": "line-1", "new_item_id": "new-line"}]
        await self.replay(snapshot)
        self.assertEqual(await self.db[lifecycle.HOLDS].count_documents({"scope": "order"}), 0)
        self.assertEqual(await self.db[lifecycle.HOLDS].count_documents({"hold_kind": ADD_KIND}), 0)
        self.assertEqual({r["target_id"] for r in await self.db[lifecycle.HOLDS].find({}).to_list(10)}, {"line-1", "new-line"})

    async def test_historical_item_hold_identity_is_not_new_generation_zero(self):
        await self.baseline()
        await self.db[lifecycle.HOLDS].insert_one({"_id": "historical", "id": "historical", "user_id": "owner",
            "order_number": "order-1", "target_id": "new-line", "scope": "item", "status": "released", "contract_version": 2})
        await self.replay(self.added())
        self.assertEqual(await self.db[lifecycle.HOLDS].count_documents({"hold_kind": ADD_KIND}), 0)
        self.assertEqual(await self.db[lifecycle.HOLDS].count_documents({"scope": "order", "status": "active"}), 1)

    async def test_oversized_add_projects_conservative_barrier_not_unscannable_holds(self):
        await self.baseline()
        source = self.added(1000)
        source["items"].extend({**source["items"][-1], "order_item_id": f"bulk-{i}"} for i in range(9))
        await self.replay(source)
        self.assertEqual(await self.db[lifecycle.HOLDS].count_documents({}), 1)
        self.assertEqual(await self.db[lifecycle.HOLDS].count_documents({"scope": "order"}), 1)

    async def test_cancelled_order_and_active_execution_keep_order_barriers(self):
        await self.baseline()
        async with lifecycle.execution_scope(self.db, user_id="owner", targets=[{"order_number": "order-1"}], operation="running"):
            result = await self.replay(self.added())
            self.assertEqual(result["status"], "awaiting_execution_resolution")
        self.assertEqual(await self.db[lifecycle.HOLDS].count_documents({"hold_kind": ADD_KIND}), 0)
        source = self.added(item="second-new", version="2040-01-01T12:00:00+00:00")
        source["cancelled"] = True
        await self.replay(source)
        self.assertEqual(await self.db[lifecycle.HOLDS].count_documents({"scope": "order", "status": "active"}), 1)

    async def test_add_preserves_components_assignments_and_financial_sentinels(self):
        await self.baseline()
        names = ["mezan_component_consumption_units_v1", "mezan_component_consumption_plans_v1",
                 "mezan_preparation_unit_allocations_v2", "warehouse_locations", "unified_orders",
                 "accounting_journal_groups_v2", "accounting_general_ledger_v2", "order_invoices"]
        for name in names:
            await self.db[name].insert_one({"_id": "untouched", "user_id": "owner", "order_number": "order-1", "proof": "same"})
        before = {name: await self.db[name].find({}).to_list(10) for name in names + [lifecycle.PIECES]}
        await self.replay(self.added(2))
        for name, rows in before.items():
            self.assertEqual(await self.db[name].find({}).to_list(10), rows)

    async def test_add_to_already_cancelled_source_cannot_narrow_order_barrier(self):
        await self.replay({**self.initial, "cancelled": True}, baseline=True)
        result = await self.replay({**self.added(), "cancelled": True})
        self.assertEqual(result["status"], "exception_required")
        self.assertEqual(await self.db[lifecycle.HOLDS].count_documents({"scope": "order"}), 1)
        self.assertEqual(await self.db[lifecycle.HOLDS].count_documents({"hold_kind": ADD_KIND}), 0)

    async def test_mixed_add_edit_overflow_uses_one_conservative_barrier(self):
        await self.baseline()
        # Historical stops count toward the bounded identity scan too.
        await self.db[lifecycle.HOLDS].insert_many([{"_id": f"prior-{i}", "user_id": "owner",
            "order_number": "order-1", "scope": "piece", "target_id": f"prior-{i}", "status": "released"}
            for i in range(9997)])
        source = self.added(2)
        source["items"][0]["options"] = {"color": "blue"}
        result = await self.replay(source)
        self.assertEqual(result["status"], "exception_required")
        self.assertEqual(await self.db[lifecycle.HOLDS].count_documents({"status": "active"}), 1)
        self.assertEqual(await self.db[lifecycle.HOLDS].count_documents({"scope": "order"}), 1)

    async def _single_order_stop(self, version, authority):
        await self.baseline()
        hold = {"_id": "independent", "id": "independent", "user_id": "owner",
                "order_number": "order-1", "target_id": "order-1", "scope": "order", "status": "active",
                "contract_version": version, "authority": authority}
        await self.db[lifecycle.HOLDS].insert_one(deepcopy(hold))
        await self.replay(self.added())
        with self.assertRaises(HTTPException) as exc:
            await lifecycle.assert_not_held(self.db, user_id="owner", order_number="order-1", order_item_id="unrelated-old-line")
        self.assertEqual(exc.exception.detail["hold_id"], "independent")
        self.assertEqual(await self.db[lifecycle.HOLDS].find_one({"_id": "independent"}), hold)
        self.assertEqual(await self.db[lifecycle.HOLDS].count_documents({"hold_kind": ADD_KIND}), 1)

    async def test_add_with_manual_hold_independently_preserved(self):
        await self._single_order_stop(2, "manual")

    async def test_add_with_pr2_hold_independently_preserved(self):
        await self._single_order_stop(3, "order_change_pr2")

    async def test_independent_clients_two_add_versions_never_duplicate_units(self):
        await self.baseline()
        first = self.added()
        second = self.added(version="2040-01-01T12:00:00+00:00")
        second["items"].append({**second["items"][-1], "order_item_id": "other-line"})
        client = fixture.AsyncIOMotorClient(fixture.os.environ["MZ2_TEST_MONGO_URI"], serverSelectionTimeoutMS=5000)
        try:
            await asyncio.gather(self.replay(first), self.replay(second, db=client[self.db.name]))
            holds = await self.db[lifecycle.HOLDS].find({"hold_kind": ADD_KIND}).to_list(10)
            self.assertEqual(len(holds), 2)
            self.assertEqual({h["order_item_id"] for h in holds}, {"new-line", "other-line"})
            self.assertEqual(await self.db[lifecycle.PIECES].count_documents({}), 1)
        finally:
            client.close()
